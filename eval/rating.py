#!/usr/bin/env python3
"""Bradley-Terry rating of every player in one or more run directories, with the Forge AI pinned.

Fits logit P(A beats B) = r_A - r_B + h*s + m(d_A, d_B) over every DECISIVE game, where s = +1 when
identity A sat in seat 1 (swap false; aggregate.py's first-player convention) and -1 otherwise, so h is
the logit of the first player's win chance between equals, and m is the deck matchup: how much deck d_A
favours its pilot against deck d_B, antisymmetric (m(d, d') = -m(d', d)) and 0 in a mirror. Players are identified by controller and model:
"llm:openrouter:anthropic/claude-haiku-5.5" (smart/fast when they differ), "ai", "random", "floor".
The winner field is identity-keyed (aggregate.py), so a seat swap never flips attribution.

Scale and anchors (eval/suites/panels-v1.json `anchors`): the Forge AI ("ai") is fixed at 1500 on
the Elo scale (400/ln 10 Elo per logit). Every other rating has a weak Gaussian prior N(1500, 400^2)
and h has N(0, 1) in logits, so a player that never wins (floor, random) gets a finite MAP rating
instead of minus infinity. The prior is printed with every report.

Uncertainty: a cluster bootstrap. A cluster is one seed of one deck pair between one pair of players
(both seat orders travel together); clusters are resampled within each pair of players, the model is
refitted, and the 2.5-97.5 percentiles give each rating's CI and each player's RANK CI.

Checks:
  sanity          floor <= random < ai and floor < ai must hold in the point fit and in every
                  replicate, or the run is invalid. floor and random tie when both win nothing
                  against the same opponents, so that pair is checked as "floor not above random".
  goodness of fit observed vs predicted wins per pair of players, a z residual per pair (flagged
                  above 2) and a chi-square over pairs.
  intransitivity  directed 3-cycles among pairs whose head-to-head Wilson CI excludes 50%.

Conditions: games pool only when everything outside the players is the same. The condition is
aggregate.py's experiment key minus the fields that name who played (arm fields, served identity,
per-seat model settings) and the deck matchup; those must agree across every game, and each llm
player's own settings (tools, tiers, budgets, served model) must agree across its games. Anything
else is refused unless --allow-mixed-conditions is passed. Games of different benchmark versions
(eval/benchmark_version.py) never pool.

Decks: a game's decks are its run directory's batch-manifest deck_a/deck_b files (identity-keyed, like
the winner). Each pair of different decks that appears gets one matchup term with the weak prior
N(0, 1) in logits; the AI-vs-AI control games of a cross panel are what pin it down. A game whose decks
are unknown (no manifest) or the same (a mirror) has no matchup term, so mirror-only data fit exactly as
before the term existed. The matchup terms are reported with their CIs. Draws and unfinished games are
left out (a rating is over decisive games).

Usage:
  python3 eval/rating.py DIR [DIR...] [--json] [--resamples 1000] [--bootstrap-seed N]
                         [--allow-mixed-conditions]
"""

import argparse
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aggregate  # noqa: E402
import benchmark_version  # noqa: E402

ELO_PER_LOGIT = 400.0 / math.log(10.0)
DEFAULT_RESAMPLES = 1000
DEFAULT_BOOTSTRAP_SEED = 20261015
ANCHOR = "ai"
MATCHUP_PRIOR_SD = 1.0       # logits: the weak prior on each deck-matchup term
SANITY_TIE_TOL = 1e-6        # logits: two players with identical data fit to the same rating
GOF_FLAG_Z = 2.0

# aggregate.CONDITION_FIELDS partitioned by what they describe. Every condition field must be in
# exactly one group (test_rating checks), so a field added to aggregate.py forces a decision here.
SERVED_BY_IDENT = {ident: tuple(f for f in aggregate.SERVED_FIELDS if f.endswith("_" + ident))
                   for ident in ("a", "b")}
# A setting of the llm seat (its model, tools, tiers, time); part of that player's identity.
PLAYER_FIELDS = (
    "smart_budget", "fast_budget", "claude_deadline_ms", "game_cap_ms", "openrouter_base_url",
    "checkpoint_sha", "model_cfg_sha", "claude_effort_smart", "claude_effort_fast",
    "memory_tools", "memory_tools_version", "native_tools", "native_tools_seats",
    "native_tools_version", "native_tools_downgraded", "rules_tool", "rules_tool_version",
    "rules_effective", "lookahead", "lookahead_version", "one_tier", "no_coaching", "one_chat",
    "one_chat_version", "turn_chat", "turn_chat_version",
    # How the llm seat answers (the answer call or text): a floor or random arm beside it records false.
    "answer_tool", "answer_tool_seats", "answer_tool_version",
)
PLAYER_FIELDS_BY_IDENT = {ident: SERVED_BY_IDENT[ident] + ("temperature_" + ident, "decisions_bridge_version_" + ident,
                                                       "claude_cli_bridge_version_" + ident,
                                                       "claude_cli_thinking_tokens_" + ident)
                          for ident in ("a", "b")}
# Which decks met.
MATCHUP_FIELDS = ("deck_sha", "plan_a", "plan_b")
# Everything else: the environment every pooled game must share.
ENV_FIELDS = tuple(f for f in aggregate.CONDITION_FIELDS
                   if f not in aggregate.CONDITION_ARM_FIELDS and f not in PLAYER_FIELDS
                   and f not in MATCHUP_FIELDS
                   and f not in PLAYER_FIELDS_BY_IDENT["a"] + PLAYER_FIELDS_BY_IDENT["b"])


class MixedConditionError(Exception):
    """The games do not share one condition."""


def player(game, ident):
    """Player label of identity `ident` ('a' or 'b')."""
    ctrl = game.get("controller_%s" % ident) or "unknown"
    if ctrl != "llm":
        return ctrl
    smart = game.get("smart_%s" % ident) or "?"
    fast = game.get("fast_%s" % ident)
    label = "llm:%s" % smart if not fast or fast == smart else "llm:%s/%s" % (smart, fast)
    # The loopback fake model answers by policy; the game line does not say which, its manifest does
    # (tag_fake_policy). Empty and garbage replies are two players, not one.
    if ident == "a" and game.get("_fake_policy"):
        label += "[%s]" % game["_fake_policy"]
    return label


def tag_fake_policy(games, run_dir):
    """Copy a sanity arm's fake_policy from <run_dir>/batch-manifest.json onto its games."""
    try:
        with open(Path(run_dir) / "batch-manifest.json", encoding="utf-8") as f:
            policy = (json.load(f).get("condition") or {}).get("fake_policy")
    except (OSError, ValueError):
        return games
    if policy:
        for g in games:
            g["_fake_policy"] = policy
    return games


def env_condition(game):
    cond = aggregate.game_condition(game)
    return {f: cond[f] for f in ENV_FIELDS}


def _loopback(url):
    return isinstance(url, str) and re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url) is not None


def player_condition(game, ident):
    cond = aggregate.game_condition(game)
    out = {f: cond[f] for f in PLAYER_FIELDS}
    for f in PLAYER_FIELDS_BY_IDENT[ident]:
        out[f[:-2]] = cond[f]           # served_smart_a -> served_smart: comparable across seats
    # A loopback endpoint gets a fresh port on every launch (sanity_arms.start_fake_server).
    if _loopback(out.get("openrouter_base_url")):
        out["openrouter_base_url"] = "loopback"
    return out


def check_conditions(games):
    """Problems (strings) that make these games one pooled measurement no longer; [] when clean."""
    problems = []
    envs = defaultdict(int)
    for g in games:
        envs[aggregate._canon(env_condition(g))] += 1
    if len(envs) > 1:
        decoded = [json.loads(k) for k in envs]
        differing = sorted(f for f in ENV_FIELDS if len({aggregate._canon(d[f]) for d in decoded}) > 1)
        problems.append("games come from %d environment conditions (differing: %s)"
                        % (len(envs), ", ".join("%s=%s" % (f, sorted({str(d[f]) for d in decoded}))
                                                for f in differing)))
    per_player = defaultdict(set)
    for g in games:
        for ident in ("a", "b"):
            if g.get("controller_%s" % ident) == "llm":
                per_player[player(g, ident)].add(aggregate._canon(player_condition(g, ident)))
    for p, conds in sorted(per_player.items()):
        if len(conds) > 1:
            decoded = [json.loads(c) for c in conds]
            differing = sorted(k for k in decoded[0] if len({aggregate._canon(d[k]) for d in decoded}) > 1)
            problems.append("player %s played under %d settings (differing: %s)"
                            % (p, len(conds), ", ".join(differing)))
    return problems


def manifest_decks(run_dir):
    """(deck of identity A, deck of identity B) named by the run directory's batch manifest, as deck file
    stems ("burn" for eval/decks/burn.txt), or (None, None) when there is no manifest."""
    try:
        cond = json.loads((Path(run_dir) / "batch-manifest.json").read_text()).get("condition") or {}
    except (OSError, ValueError):
        return None, None
    stem = lambda p: Path(p).stem if p else None  # noqa: E731
    return stem(cond.get("deck_a")), stem(cond.get("deck_b"))


def load_games(dirs):
    """Kept (deduplicated) game lines of every experiment in each directory, tagged with `_dir` and the
    manifest's decks (`_deck_a`, `_deck_b`)."""
    out = []
    for d in dirs:
        da, db = manifest_decks(d)
        for e in aggregate.parse_dir_full(d)["experiments"]:
            for g in tag_fake_policy(e["games"], d):
                g["_dir"] = str(d)
                g["_deck_a"], g["_deck_b"] = da, db
                out.append(g)
    return out


def matchup_of(da, db):
    """(pair, sign) for decks da (identity A) and db, where pair is the sorted deck pair and sign is +1
    when da is its first deck; None for a mirror or an unknown deck."""
    if not da or not db or da == db:
        return None
    pair = tuple(sorted((da, db)))
    return pair, (1 if da == pair[0] else -1)


def decisive_records(games):
    """[(player_a, player_b, s, y, cluster, matchup)] for decisive games. s = +1 when A was first, y = 1
    when A won. cluster = (stratum, dir, deck_sha, seed): both seat orders of a seed share it. matchup is
    matchup_of(A's deck, B's deck)."""
    recs = []
    for g in games:
        w = g.get("winner")
        if w not in ("A", "B"):
            continue
        pa, pb = player(g, "a"), player(g, "b")
        s = -1 if g.get("swap") else 1
        stratum = tuple(sorted((pa, pb)))
        seed = g.get("seed")
        cluster = (stratum, g.get("_dir"), g.get("deck_sha"), seed if seed is not None else id(g))
        recs.append((pa, pb, s, 1 if w == "A" else 0, cluster, matchup_of(g.get("_deck_a"), g.get("_deck_b"))))
    return recs


# ------------------------------------------------------------------- fit

def _sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _solve(a, b):
    """Solve a x = b by Gaussian elimination with partial pivoting (a is small and positive definite)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-300:
            raise ArithmeticError("singular system")
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def cells_of(recs):
    """{(player_a, player_b, s, matchup): [wins_a, games]}: the sufficient statistics of a fit."""
    cells = defaultdict(lambda: [0, 0])
    for pa, pb, s, y, _, m in recs:
        c = cells[(pa, pb, s, m)]
        c[0] += y
        c[1] += 1
    return cells


def matchups_in(cells):
    return sorted({m[0] for (_pa, _pb, _s, m) in cells if m is not None})


def fit(cells, players, prior_sd, h_prior_sd, anchor=ANCHOR, start=None, max_iter=100,
        matchup_prior_sd=MATCHUP_PRIOR_SD):
    """MAP fit by Newton's method. Returns ({player: rating in logits, anchor 0}, h, {deck pair: m}).

    The log posterior is strictly concave (the Gaussian priors make it so), so Newton with step
    halving converges from any start."""
    free = [p for p in players if p != anchor]
    pairs = matchups_in(cells)
    idx = {p: i for i, p in enumerate(free)}
    hi = len(free)                        # h
    midx = {pair: hi + 1 + j for j, pair in enumerate(pairs)}
    n = hi + 1 + len(pairs)
    theta = [0.0] * n
    if start:
        r0, h0, m0 = start
        theta = [r0.get(p, 0.0) for p in free] + [h0] + [m0.get(pair, 0.0) for pair in pairs]
    prec = [1.0 / (prior_sd * prior_sd)] * len(free) + [1.0 / (h_prior_sd * h_prior_sd)] \
        + [1.0 / (matchup_prior_sd * matchup_prior_sd)] * len(pairs)

    def design(pa, pb, s, m):
        xs = []
        if pa in idx:
            xs.append((idx[pa], 1.0))
        if pb in idx:
            xs.append((idx[pb], -1.0))
        xs.append((hi, float(s)))
        if m is not None:
            xs.append((midx[m[0]], float(m[1])))
        return xs

    def logpost(t):
        lp = -0.5 * sum(q * x * x for q, x in zip(prec, t))
        for key, (w, k) in cells.items():
            eta = sum(t[i] * xi for i, xi in design(*key))
            # log sigma(eta) and log(1 - sigma(eta)), stably
            lp += w * -_log1pexp(-eta) + (k - w) * -_log1pexp(eta)
        return lp

    cur = logpost(theta)
    for _ in range(max_iter):
        grad = [-q * x for q, x in zip(prec, theta)]
        hess = [[0.0] * n for _ in range(n)]
        for i in range(n):
            hess[i][i] = prec[i]
        for key, (w, k) in cells.items():
            xs = design(*key)
            eta = sum(theta[i] * xi for i, xi in xs)
            p = _sigmoid(eta)
            resid = w - k * p
            v = k * p * (1.0 - p)
            for i, xi in xs:
                grad[i] += resid * xi
                for j, xj in xs:
                    hess[i][j] += v * xi * xj
        step = _solve(hess, grad)
        t = 1.0
        while True:
            cand = [a + t * b for a, b in zip(theta, step)]
            new = logpost(cand)
            if new >= cur - 1e-12 or t < 1e-8:
                break
            t *= 0.5
        theta, cur = cand, new
        if max(abs(t * x) for x in step) < 1e-10:
            break
    ratings = {p: theta[idx[p]] for p in free}
    ratings[anchor] = 0.0
    return ratings, theta[hi], {pair: theta[midx[pair]] for pair in pairs}


def matchup_value(matchups, m):
    """The fitted matchup term for a record's matchup (pair, sign), 0 for a mirror or unknown deck."""
    return 0.0 if m is None else m[1] * matchups.get(m[0], 0.0)


def _log1pexp(x):
    return x + math.log1p(math.exp(-x)) if x > 0 else math.log1p(math.exp(x))


def to_elo(r, anchor_elo):
    return anchor_elo + ELO_PER_LOGIT * r


def ranks(ratings):
    """Competition ranks, 1 = highest; exact ties share a rank."""
    out = {}
    for p, r in ratings.items():
        out[p] = 1 + sum(1 for q, s in ratings.items() if s > r + SANITY_TIE_TOL)
    return out


# --------------------------------------------------------------- checks

def sanity_violations(ratings):
    """Broken anchor orderings among floor/random/ai present in `ratings`."""
    v = []
    f, r, a = ratings.get("floor"), ratings.get("random"), ratings.get(ANCHOR)
    if f is not None and r is not None and f > r + SANITY_TIE_TOL:
        v.append("floor above random")
    if r is not None and a is not None and not r < a:
        v.append("random not below ai")
    if f is not None and a is not None and not f < a:
        v.append("floor not below ai")
    return v


def goodness_of_fit(recs, ratings, h, matchups=None):
    """Observed vs predicted wins per unordered pair of distinct players."""
    pairs = defaultdict(lambda: {"games": 0, "obs": 0.0, "exp": 0.0, "var": 0.0})
    for pa, pb, s, y, _, m in recs:
        if pa == pb:
            continue
        p = _sigmoid(ratings[pa] - ratings[pb] + h * s + matchup_value(matchups or {}, m))
        lo, hi = sorted((pa, pb))
        q = pairs[(lo, hi)]
        q["games"] += 1
        # from the point of view of `lo`
        if pa == lo:
            q["obs"] += y
            q["exp"] += p
        else:
            q["obs"] += 1 - y
            q["exp"] += 1 - p
        q["var"] += p * (1 - p)
    rows, chi2 = [], 0.0
    for (lo, hi), q in sorted(pairs.items()):
        z = (q["obs"] - q["exp"]) / math.sqrt(q["var"]) if q["var"] > 0 else 0.0
        chi2 += z * z
        rows.append({"pair": [lo, hi], "games": q["games"], "observed_wins": q["obs"],
                     "predicted_wins": round(q["exp"], 3),
                     "observed_rate": q["obs"] / q["games"], "predicted_rate": q["exp"] / q["games"],
                     "z": round(z, 3), "flagged": abs(z) > GOF_FLAG_Z})
    n_players = len(ratings)
    df = max(1, len(rows) - (n_players - 1))
    return {"pairs": rows, "chi2": chi2, "df": df, "p_value": chi2_sf(chi2, df),
            "n_flagged": sum(1 for r in rows if r["flagged"]),
            "note": "df = pairs - free ratings, floored at 1; with as many free ratings as pairs the "
                    "fit is exact and the chi-square says nothing"}


def chi2_sf(x, k):
    """P(X > x) for X ~ chi-square(k): the regularized upper incomplete gamma Q(k/2, x/2)."""
    if x <= 0:
        return 1.0
    a, z = k / 2.0, x / 2.0
    if z < a + 1:
        term = total = 1.0 / a
        n = a
        for _ in range(500):
            n += 1
            term *= z / n
            total += term
            if term < total * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(-z + a * math.log(z) - math.lgamma(a)))
    # Lentz continued fraction for Q
    b = z + 1 - a
    c = 1.0 / 1e-300
    d = 1.0 / b
    hcf = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        hcf *= delta
        if abs(delta - 1) < 1e-15:
            break
    return min(1.0, math.exp(-z + a * math.log(z) - math.lgamma(a)) * hcf)


def intransitivity(recs):
    """Directed 3-cycles among head-to-head results whose Wilson 95% CI excludes 50%."""
    h2h = defaultdict(lambda: [0, 0])
    for pa, pb, _s, y, *_ in recs:
        if pa == pb:
            continue
        lo, hi = sorted((pa, pb))
        q = h2h[(lo, hi)]
        q[1] += 1
        q[0] += y if pa == lo else 1 - y
    beats = defaultdict(set)
    edges = []
    for (lo, hi), (w, n) in sorted(h2h.items()):
        l, u = aggregate.wilson_ci(w, n)
        if l > 0.5:
            beats[lo].add(hi)
            edges.append([lo, hi])
        elif u < 0.5:
            beats[hi].add(lo)
            edges.append([hi, lo])
    cycles = set()
    for a in beats:
        for b in beats[a]:
            for c in beats.get(b, ()):
                if a in beats.get(c, ()):
                    cyc = (a, b, c)
                    k = cyc.index(min(cyc))
                    cycles.add(cyc[k:] + cyc[:k])
    return {"decided_edges": edges, "three_cycles": [list(c) for c in sorted(cycles)]}


# ------------------------------------------------------------ bootstrap

def bootstrap(recs, players, prior_sd, h_prior_sd, point, resamples, seed):
    """Cluster bootstrap within each pair of players. Returns per-replicate (ratings, h, matchups) list."""
    by_stratum = defaultdict(lambda: defaultdict(list))
    for rec in recs:
        by_stratum[rec[4][0]][rec[4]].append(rec)
    strata = [list(clusters.values()) for _, clusters in sorted(by_stratum.items(), key=lambda kv: kv[0])]
    rng = random.Random(seed)
    reps = []
    for _ in range(resamples):
        cells = defaultdict(lambda: [0, 0])
        for clusters in strata:
            k = len(clusters)
            for _ in range(k):
                for pa, pb, s, y, _c, m in clusters[rng.randrange(k)]:
                    c = cells[(pa, pb, s, m)]
                    c[0] += y
                    c[1] += 1
        reps.append(fit(cells, players, prior_sd, h_prior_sd, start=point))
    return reps


def matchup_report(recs, matchups, reps):
    """{"d1 vs d2": {...}}: how much the first deck favours its pilot against the second, in logits and as
    a win chance between equal players, with its bootstrap CI and the decisive games behind it."""
    games = defaultdict(int)
    for *_x, m in recs:
        if m is not None:
            games[m[0]] += 1
    out = {}
    for pair, v in sorted(matchups.items()):
        row = {"logit": v, "first_deck_win_prob_between_equals": _sigmoid(v), "decisive_games": games[pair]}
        if reps:
            vals = [x[2].get(pair, 0.0) for x in reps]
            row["ci95_logit"] = [_pct(vals, 0.025), _pct(vals, 0.975)]
        out["%s vs %s" % pair] = row
    return out


def _pct(vals, q):
    vs = sorted(vals)
    return vs[min(len(vs) - 1, max(0, int(q * len(vs))))]


# --------------------------------------------------------------- report

def anchors_from_panels(path=None):
    a = benchmark_version.load_panels(path).get("anchors") or {}
    return {"anchor_elo": float(a.get("forge_ai_elo", 1500)),
            "prior_sd_elo": float(a.get("rating_prior_sd_elo", 400)),
            "h_prior_sd": float(a.get("first_player_prior_sd_logit", 1.0))}


def rate(dirs, resamples=DEFAULT_RESAMPLES, bootstrap_seed=DEFAULT_BOOTSTRAP_SEED,
         allow_mixed=False, panels_path=None, games=None):
    """The full rating report as a dict. Raises MixedConditionError (or benchmark_version.VersionError)
    when the games are not one measurement and allow_mixed is false."""
    games = load_games(dirs) if games is None else games
    problems = check_conditions(games)
    version, version_problem = None, None
    try:
        version = benchmark_version.stamp(panels_path=panels_path, games=games)
    except benchmark_version.VersionError as e:
        version_problem = str(e)
    if (problems or version_problem) and not allow_mixed:
        raise MixedConditionError("; ".join(problems + ([version_problem] if version_problem else [])))
    anchors = anchors_from_panels(panels_path)
    recs = decisive_records(games)
    players = sorted({r[0] for r in recs} | {r[1] for r in recs})
    if ANCHOR not in players:
        raise MixedConditionError("no decisive game involves the Forge AI (%r): nothing pins the scale"
                                  % ANCHOR)
    prior_sd = anchors["prior_sd_elo"] / ELO_PER_LOGIT
    cells = cells_of(recs)
    ratings, h, matchups = fit(cells, players, prior_sd, anchors["h_prior_sd"])
    reps = bootstrap(recs, players, prior_sd, anchors["h_prior_sd"], (ratings, h, matchups), resamples,
                     bootstrap_seed) if resamples > 0 else []
    point_ranks = ranks(ratings)
    rep_ranks = [ranks(r) for r, *_ in reps]
    games_per = defaultdict(lambda: [0, 0])
    for pa, pb, _s, y, *_ in recs:
        games_per[pa][1] += 1
        games_per[pb][1] += 1
        games_per[pa][0] += y
        games_per[pb][0] += 1 - y
    out_players = {}
    for p in sorted(players, key=lambda q: -ratings[q]):
        row = {"elo": to_elo(ratings[p], anchors["anchor_elo"]), "logit": ratings[p],
               "rank": point_ranks[p], "wins": games_per[p][0], "decisive_games": games_per[p][1],
               "pinned": p == ANCHOR}
        if reps:
            el = [to_elo(r[p], anchors["anchor_elo"]) for r, *_ in reps]
            rk = [x[p] for x in rep_ranks]
            row["elo_ci95"] = [_pct(el, 0.025), _pct(el, 0.975)]
            row["rank_ci95"] = [_pct(rk, 0.025), _pct(rk, 0.975)]
        out_players[p] = row
    point_sanity = sanity_violations(ratings)
    rep_sanity = defaultdict(int)
    for r, *_ in reps:
        for v in sanity_violations(r):
            rep_sanity[v] += 1
    strict = sum(1 for r, *_ in reps if "floor" in r and "random" in r and r["floor"] < r["random"] - SANITY_TIE_TOL)
    present = [p for p in ("floor", "random", ANCHOR) if p in players]
    return {
        "dirs": [str(d) for d in dirs],
        "benchmark_version": version,
        "benchmark_version_problem": version_problem,
        "condition_problems": problems,
        "mixed_allowed": bool(allow_mixed and (problems or version_problem)),
        "n_games": len([g for g in games if not g.get("_no_game_line")]),
        "n_decisive": len(recs),
        "model": "logit P(A beats B) = r_A - r_B + h*s + m(d_A, d_B); s = +1 when A sat in seat 1",
        "prior": {"anchor": ANCHOR, "anchor_elo": anchors["anchor_elo"],
                  "rating_prior": "N(%g, %g^2) Elo" % (anchors["anchor_elo"], anchors["prior_sd_elo"]),
                  "first_player_prior": "N(0, %g^2) logits" % anchors["h_prior_sd"],
                  "matchup_prior": "N(0, %g^2) logits per pair of different decks" % MATCHUP_PRIOR_SD,
                  "elo_per_logit": ELO_PER_LOGIT},
        "first_player": {"h_logit": h, "h_elo": h * ELO_PER_LOGIT, "win_prob_between_equals": _sigmoid(h),
                         "ci95_win_prob": ([_sigmoid(_pct([x[1] for x in reps], 0.025)),
                                            _sigmoid(_pct([x[1] for x in reps], 0.975))] if reps else None)},
        "matchups": matchup_report(recs, matchups, reps),
        "players": out_players,
        "bootstrap": {"resamples": len(reps), "seed": bootstrap_seed,
                      "unit": "seed cluster (both seat orders) within each pair of players"},
        "sanity": {"anchors_present": present,
                   "point_violations": point_sanity,
                   "replicate_violations": dict(rep_sanity),
                   "replicates_floor_strictly_below_random": strict,
                   "valid": not point_sanity and not rep_sanity,
                   "rule": "floor <= random < ai and floor < ai in the point fit and every replicate"},
        "goodness_of_fit": goodness_of_fit(recs, ratings, h, matchups),
        "intransitivity": intransitivity(recs),
    }


def print_human(rep):
    v = rep["benchmark_version"]
    print("=== Bradley-Terry rating (%s) ===" % ", ".join(rep["dirs"]))
    print("benchmark version: %s" % (v["id"] if v else "NONE (%s)" % rep["benchmark_version_problem"]))
    if rep["mixed_allowed"]:
        print("!!! MIXED CONDITIONS ALLOWED BY FLAG: %s" % "; ".join(
            rep["condition_problems"] + ([rep["benchmark_version_problem"]] if rep["benchmark_version_problem"] else [])))
    pr = rep["prior"]
    print("%s; %s pinned at %g; prior %s, first player %s" % (rep["model"], pr["anchor"], pr["anchor_elo"],
                                                               pr["rating_prior"], pr["first_player_prior"]))
    print("games %d, decisive %d; bootstrap %d resamples (%s)" % (rep["n_games"], rep["n_decisive"],
                                                                 rep["bootstrap"]["resamples"], rep["bootstrap"]["unit"]))
    print("%-58s %7s %15s %6s %9s %9s" % ("player", "elo", "elo 95% CI", "rank", "rank CI", "W/decisive"))
    for p, r in rep["players"].items():
        ci = "[%.0f, %.0f]" % tuple(r["elo_ci95"]) if "elo_ci95" in r else "-"
        rci = "%d-%d" % tuple(r["rank_ci95"]) if "rank_ci95" in r else "-"
        print("%-58s %7.0f %15s %6d %9s %4d/%d%s" % (p, r["elo"], ci, r["rank"], rci, r["wins"], r["decisive_games"],
                                                    "  (pinned)" if r["pinned"] else ""))
    fp = rep["first_player"]
    print("first player: h = %+.3f logits (%+.0f Elo), P(first wins | equal) = %.3f%s"
          % (fp["h_logit"], fp["h_elo"], fp["win_prob_between_equals"],
             "  95%% CI [%.3f, %.3f]" % tuple(fp["ci95_win_prob"]) if fp["ci95_win_prob"] else ""))
    for pair, m in rep["matchups"].items():
        print("matchup %s: m = %+.3f logits, P(first deck's pilot wins | equal players) = %.3f%s, %d decisive games"
              % (pair, m["logit"], m["first_deck_win_prob_between_equals"],
                 "  95%% CI [%+.3f, %+.3f] logits" % tuple(m["ci95_logit"]) if "ci95_logit" in m else "",
                 m["decisive_games"]))
    s = rep["sanity"]
    print("sanity (%s): %s  point=%s replicates=%s" % (s["rule"], "VALID" if s["valid"] else "INVALID",
                                                      s["point_violations"] or "ok", s["replicate_violations"] or "ok"))
    g = rep["goodness_of_fit"]
    print("goodness of fit: chi2 = %.2f on %d df (p = %.3f), %d pair(s) with |z| > %g"
          % (g["chi2"], g["df"], g["p_value"], g["n_flagged"], GOF_FLAG_Z))
    for row in g["pairs"]:
        print("  %s vs %s: %d games, observed %.0f, predicted %.1f, z %+.2f%s"
              % (row["pair"][0], row["pair"][1], row["games"], row["observed_wins"], row["predicted_wins"],
                 row["z"], "  FLAGGED" if row["flagged"] else ""))
    it = rep["intransitivity"]
    print("intransitivity: %d decided head-to-head edge(s), %d directed 3-cycle(s)%s"
          % (len(it["decided_edges"]), len(it["three_cycles"]),
             (": " + "; ".join(" > ".join(c + [c[0]]) for c in it["three_cycles"])) if it["three_cycles"] else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", help="run directories (game-*.jsonl)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    ap.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    ap.add_argument("--panels", help="panels file holding the anchors (default eval/suites/panels-v1.json)")
    ap.add_argument("--allow-mixed-conditions", action="store_true",
                    help="pool games from different conditions or benchmark versions anyway (printed loudly)")
    a = ap.parse_args(argv)
    for d in a.dirs:
        if not Path(d).is_dir():
            raise SystemExit("not a directory: %s" % d)
    try:
        rep = rate(a.dirs, a.resamples, a.bootstrap_seed, a.allow_mixed_conditions, a.panels)
    except (MixedConditionError, benchmark_version.VersionError) as e:
        print("rating: REFUSING: %s" % e, file=sys.stderr)
        print("        (pass --allow-mixed-conditions to pool anyway; the report says so)", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print_human(rep)
    return 0 if rep["sanity"]["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
