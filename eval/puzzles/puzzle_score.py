#!/usr/bin/env python3
"""Score PUZ: one solver's attempts on one split of one frozen puzzle suite (docs/eval/PUZZLES.md).

    python3 eval/puzzles/puzzle_score.py --suite puzzles/suites/<suite>/suite.json --split open RESULTS...

RESULTS are puzzle-results.jsonl files (or directories holding them) written by LlmSpikePuzzle,
k attempts per puzzle (default 4, distinct seeds). Per puzzle, with c solves in k attempts:

  pass@1   = c/k                      (the headline ingredient)
  pass^j   = C(c,j)/C(k,j), j=2 and k (every one of j attempts solves: reliability)
  solved   = c >= k/2
  margin   = pass@1 - random_rate     (random_rate = the puzzle's uniform-random solve rate)

Headline PUZ = mean over puzzles of margin, so a puzzle a coin flip solves earns almost nothing and
doing nothing scores about zero. Every aggregate is printed with its floor (the same number for the
random, PASS and first-option policies) and a 95% bootstrap CI over puzzles. The solved counts (and pass@1 at
k=1, where it is the solved share) also carry a Wilson interval: a bootstrap over puzzles reports 0 of 21 as
[0, 0], a certainty it does not have, where Wilson gives 0 to 3.2.

Paired mode (--paired-with B, and --against C D): two runs on the same puzzles of the same suite, split and k,
compared puzzle by puzzle. It prints the mean per-puzzle pass@1 difference B - A (or the difference of differences
(B - A) - (D - C)), its percentile bootstrap interval over puzzles (ids sorted, --paired-bootstrap resamples,
--paired-seed) and its clustered normal interval. What it cannot say: it resamples puzzles only (the attempt
noise is inside each per-puzzle rate), it does not correct for several comparisons, and result rows written
before harness_epoch was stamped cannot be told apart by epoch.

Refuses (exit 2) to: pool the open and held-out splits, mix suite versions or puzzle versions, mix solvers, game caps,
call deadlines or token caps, score a puzzle with fewer than k attempts, or score an unscoreable row. Stdlib only.
"""

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import puzzle_format as pf  # noqa: E402
from aggregate import wilson_ci  # noqa: E402


# Puzzle-row fields only some solvers carry, each a condition two attempts must share (key, what it is called).
BRIDGE_CONDITIONS = (("decisions_bridge_version", "decisions bridge versions"),
                     ("claude_cli_bridge_version", "claude-cli bridge versions"),
                     ("claude_cli_effort", "claude-cli efforts"),
                     ("claude_cli_thinking_tokens", "claude-cli thinking budgets"),
                     # The routing filters a puzzle run sent (puzzle_solve.py --max-price-at-list / --quantizations):
                     # what the model ran at (pr-review of #327).
                     ("openrouter_max_price", "routing filters (price ceilings)"),
                     ("openrouter_quantizations", "routing filters (precision lists)"),
                     # A development lane (owner, 2026-10-24: claude-cli:) is labelled so and never pooled with a score.
                     ("dev_lane", "development lanes"),
                     # From each puzzle's manifest record (load_rows): puzzle_solve.py --call-timeout-minutes, every
                     # tier's per-call deadline, and --max-tokens, the thinking budget (None: each model's own).
                     ("call_timeout_ms", "call deadlines"),
                     ("max_tokens", "token caps"),
                     # PROPOSE_ACTIONS (harness-epoch-74): the seat proposed its plays instead of picking from the menu.
                     ("propose_actions", "play interfaces (true: proposed plays, no menu)"))

# A condition read from a puzzle's manifest when the directory has none (a baseline arm): not known, not "unset".
NO_MANIFEST = "n/a"


# The game cap every puzzle run played at before puzzle_solve.py --game-cap-minutes; a manifest without a record says so.
LEGACY_GAME_CAP_MS = 1800000


class Refused(Exception):
    """A request the scorer will not answer, because the answer would not mean what it says."""


# ------------------------------------------------------------------ per-puzzle math

def pass_hat(c, n, j):
    """C(c,j)/C(n,j): chance that j attempts drawn without replacement from n all succeed."""
    if j > n or j < 0:
        raise ValueError("pass^%d needs at least %d attempts, have %d" % (j, j, n))
    return math.comb(c, j) / math.comb(n, j)


def p_solved_by_policy(rate, k):
    """Chance a policy with per-attempt solve rate `rate` gets c >= k/2 in k attempts (the
    'puzzles solved' floor for that policy)."""
    need = math.ceil(k / 2)
    return sum(math.comb(k, c) * rate ** c * (1 - rate) ** (k - c) for c in range(need, k + 1))


def attempt_success(row):
    """(success, note). A capped game counts as a failure (it cannot vanish); a solve decided by an
    empty-library deck-out is not evidence and counts as a failure; an unscoreable row is refused."""
    status = row.get("status")
    if status == "unscoreable":
        raise Refused("unscoreable row for %s (seed %s): the goal was not enforced, so this is a harness "
                      "defect, not a result" % (row.get("id"), row.get("seed")))
    if row.get("routing_refusals"):
        raise Refused("row for %s (seed %s): %s decisions lost under the routing filter (no endpoint it allows answered "
                      "after every retry), so they were not the model's; replay it with --resume"
                      % (row.get("id"), row.get("seed"), row["routing_refusals"]))
    if status == "invalid":
        return False, "capped"
    if status not in ("solved", "failed"):
        raise Refused("row for %s has unknown status %r" % (row.get("id"), status))
    if status == "solved" and row.get("tainted_by_deckout"):
        return False, "tainted"
    return status == "solved", None


# ------------------------------------------------------------------ loading

def load_rows(paths):
    rows = []
    for p in paths:
        p = Path(p)
        files = sorted(p.rglob("puzzle-results.jsonl")) if p.is_dir() else [p]
        for f in files:
            record = record_of(f.parent)
            cap = None if record is None else record.get("game_cap_ms") or LEGACY_GAME_CAP_MS
            call, tokens = ((NO_MANIFEST, NO_MANIFEST) if record is None
                            else (record.get("call_timeout_ms"), record.get("max_tokens")))
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    raise Refused("%s:%d is not JSON" % (f, n))
                if d.get("schema") == "pzl-result-v1":
                    d.setdefault("game_cap_ms", cap)
                    d.setdefault("call_timeout_ms", call)
                    d.setdefault("max_tokens", tokens)
                    rows.append(d)
    return rows


def record_of(puzzle_dir):
    """The record puzzle_solve wrote beside a puzzle directory's rows ({} for a manifest from before it); None with
    no manifest (a baseline arm, which sets its own conditions). An unreadable manifest is refused."""
    f = puzzle_dir / "batch-manifest.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("record") or {}
    except OSError:
        return None
    except ValueError:
        raise Refused("%s is not JSON: the conditions it was played under are unknown" % f)


def group_attempts(manifest, rows, split, k, solver=None, ids=None):
    """{puzzle_id: [rows]} for the active puzzles of `split` (only `ids` when given: a pilot), after every
    refusal check."""
    if split not in pf.SPLITS:
        raise Refused("--split must be one of %s; open and held-out puzzles are never pooled" % (pf.SPLITS,))
    suite_sha = manifest.get("suite_sha256")
    if not manifest.get("frozen") or not suite_sha:
        raise Refused("suite %s is not frozen; only a frozen suite has a version to score against"
                      % manifest.get("suite"))
    if suite_sha != pf.suite_hash(manifest):
        raise Refused("suite %s was edited after freezing (suite_sha256 mismatch)" % manifest.get("suite"))

    by_sha, by_id = {}, {}
    for r in manifest["puzzles"]:
        by_sha[r["pzl_sha256"]] = r
        by_id[r["id"]] = r

    stamped = {r.get("suite_sha256") for r in rows if r.get("suite_sha256")}
    if len(stamped) > 1:
        raise Refused("results come from %d different suite versions %s; score each separately"
                      % (len(stamped), sorted(stamped)))
    if stamped and stamped != {suite_sha}:
        raise Refused("results were run against suite version %s, not %s" % (stamped.pop(), suite_sha))

    epochs = {r.get("harness_epoch") for r in rows}
    if len(epochs) > 1:
        raise Refused("results come from harness epochs %s (None: a row from before the stamp); what the seat saw "
                      "differs, so score each separately (every row read counts, before --solver and --ids)"
                      % sorted(epochs, key=str))
    if solver:
        rows = [r for r in rows if solver_of(r) == solver]
    solvers = {solver_of(r) for r in rows}
    if len(solvers) > 1:
        raise Refused("results mix solvers %s; pass --solver to pick one" % sorted(solvers))
    # Each BRIDGE_CONDITIONS key is a condition every attempt must share (where each comes from: the tuple).
    for key, what in BRIDGE_CONDITIONS:
        seen = {r.get(key) for r in rows}
        if len(seen) > 1:
            raise Refused("results come from %s %s; what the solver was asked or how it ran differs, so score each "
                          "separately" % (what, sorted(seen, key=str)))

    # The game cap is a condition (owner raised it 30 to 90 minutes, 2026-10-26): even a game that ended under the
    # lower one is not pooled, since a resume that redrew only the cut-off games favours fast games.
    caps = {r.get("game_cap_ms") for r in rows} - {None}
    if len(caps) > 1:
        raise Refused("results come from game caps %s ms; replay the lower one (puzzle_solve.py --resume "
                      "--game-cap-minutes) or score each separately" % sorted(caps))

    out = defaultdict(list)
    for r in rows:
        rec = by_sha.get(r.get("pzl_sha256"))
        if rec is None:
            if r.get("id") in by_id:
                raise Refused("a result for %s was run on a different version of that puzzle "
                              "(pzl_sha256 %s)" % (r.get("id"), str(r.get("pzl_sha256"))[:12]))
            raise Refused("a result for %r matches no puzzle of suite %s" % (r.get("id"), manifest.get("suite")))
        if rec["split"] != split:
            raise Refused("results include %s puzzle %s; score the %s split from its own results "
                          "(open and held-out are never pooled)" % (rec["split"], rec["id"], rec["split"]))
        if rec["status"] != "active":
            continue
        out[rec["id"]].append(r)

    wanted = [r["id"] for r in manifest["puzzles"] if r["split"] == split and r["status"] == "active"
              and (ids is None or r["id"] in ids)]
    if ids is not None:
        unknown = sorted(set(ids) - set(wanted))
        if unknown:
            raise Refused("--ids names no active %s puzzle: %s" % (split, ", ".join(unknown)))
        out = {pid: att for pid, att in out.items() if pid in ids}
    if not wanted:
        raise Refused("suite %s has no active %s puzzles" % (manifest.get("suite"), split))
    missing = [pid for pid in wanted if len(out.get(pid, [])) < k]
    if missing:
        raise Refused("%d puzzle(s) have fewer than k=%d attempts: %s"
                      % (len(missing), k, ", ".join("%s(%d)" % (m, len(out.get(m, []))) for m in missing[:8])))
    for pid in wanted:
        att = sorted(out[pid], key=lambda r: (r.get("seed", 0), r.get("rollout", 0)))
        seeds = [r.get("seed") for r in att[:k]]
        if len(set(seeds)) < len(seeds):
            raise Refused("%s: attempts repeat a seed %s; k attempts must be k distinct seeds" % (pid, seeds))
        out[pid] = att[:k]
    return {pid: out[pid] for pid in wanted}


def solver_of(row):
    arm = row.get("solver_arm") or "?"
    return arm + ("/" + row["solver_model"] if row.get("solver_model") else "")


# ------------------------------------------------------------------ scoring

def per_puzzle(manifest, attempts, k):
    recs = {r["id"]: r for r in manifest["puzzles"]}
    rows = []
    for pid, att in attempts.items():
        rec = recs[pid]
        results = [attempt_success(r) for r in att]
        c = sum(1 for ok, _ in results if ok)
        base = rec.get("baselines", {})
        rate = {arm: base[arm]["rate"] for arm in base}
        row = {
            "id": pid, "tier": rec.get("tier"), "tier_v2": rec.get("tier_v2"), "category": rec.get("category"),
            "k": k, "solves": c, "pass@1": c / k,
            "pass^2": pass_hat(c, k, 2) if k >= 2 else None,
            "pass^k": pass_hat(c, k, k),
            "solved": 2 * c >= k,
            "random_rate": rate["random"], "pass_rate": rate["pass"], "first_rate": rate["first"],
            "ai_rate": rate.get("ai"),
            "margin": c / k - rate["random"],
            "notes": sorted({note for _, note in results if note}),
        }
        rows.append(row)
    return rows


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def bootstrap_ci(rows, stat, n_boot=2000, seed=0):
    """Percentile 95% CI of stat(rows) over puzzles resampled with replacement."""
    if not rows:
        return None
    rng = random.Random(seed)
    n = len(rows)
    vals = sorted(stat([rows[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot))
    lo = vals[int(0.025 * (n_boot - 1))]
    hi = vals[int(math.ceil(0.975 * (n_boot - 1)))]
    return [lo, hi]


def aggregate(rows, k, n_boot=2000, seed=0):
    def stat_of(key):
        return lambda rs: _mean([r[key] for r in rs])

    def floor_margin(arm):
        return _mean([r[arm + "_rate"] - r["random_rate"] for r in rows])

    out = {"n_puzzles": len(rows), "k": k}
    out["headline"] = {
        "name": "mean(pass@1 - random_rate)",
        "value": stat_of("margin")(rows),
        "ci95": bootstrap_ci(rows, stat_of("margin"), n_boot, seed),
        "ci95_clustered": clustered_ci([r["margin"] for r in rows]),
        "floors": {"random": 0.0, "pass": floor_margin("pass"), "first": floor_margin("first")},
    }
    out["pass@1"] = {
        "value": stat_of("pass@1")(rows), "ci95": bootstrap_ci(rows, stat_of("pass@1"), n_boot, seed),
        "ci95_clustered": clustered_ci([r["pass@1"] for r in rows]),
        # The smallest pass@1 difference between two independent solvers this many puzzles detects at 80% power and
        # alpha 0.05 (Miller 2024: 2.8 standard errors of the difference); a paired comparison does better.
        "mde80": mde80([r["pass@1"] for r in rows]),
        # At k=1 each puzzle is one Bernoulli trial: the count interval applies (at k>1 attempts cluster by puzzle).
        "ci95_wilson": list(wilson_ci(sum(r["solves"] for r in rows), len(rows))) if k == 1 and rows else None,
        "floors": {arm: _mean([r[arm + "_rate"] for r in rows]) for arm in ("random", "pass", "first")},
    }
    for key in ("pass^2", "pass^k"):
        if all(r[key] is not None for r in rows):
            j = 2 if key == "pass^2" else k
            out[key] = {
                "value": stat_of(key)(rows), "ci95": bootstrap_ci(rows, stat_of(key), n_boot, seed),
                # Under independent attempts pass^j would be pass@1^j; the shortfall is a finding.
                "if_independent": _mean([r["pass@1"] ** j for r in rows]),
                "floors": {arm: _mean([r[arm + "_rate"] ** j for r in rows]) for arm in ("random", "pass", "first")},
            }
    ai = [r["ai_rate"] for r in rows if r["ai_rate"] is not None]
    if len(ai) == len(rows):
        mr, ma, mp = out["pass@1"]["floors"]["random"], _mean(ai), out["pass@1"]["value"]
        out["normalized"] = {
            "name": "(pass@1 - random) / (forge_ai - random), not capped at 1",
            "forge_ai_rate": ma,
            "value": (mp - mr) / (ma - mr) if ma - mr > 1e-12 else None,
        }
    out["solved"] = solved_counts(rows, k, n_boot, seed)
    return out


def solved_counts(rows, k, n_boot=2000, seed=0):
    def by(key):
        out = {}
        for tier in sorted({r.get(key) for r in rows}, key=lambda t: (t is None, t)):
            out[str(tier)] = _solved_block([r for r in rows if r.get(key) == tier], k, n_boot, seed)
        return out
    out = {"all": _solved_block(rows, k, n_boot, seed), "by_tier": by("tier")}
    if any(r.get("tier_v2") is not None for r in rows):
        out["by_tier_v2"] = by("tier_v2")
    return out


def _solved_block(sub, k, n_boot, seed):
    count = lambda rs: sum(1 for r in rs if r["solved"])  # noqa: E731
    return {
        "solved": count(sub), "of": len(sub),
        "ci95": bootstrap_ci(sub, count, n_boot, seed),
        "ci95_wilson": [len(sub) * x for x in wilson_ci(count(sub), len(sub))],
        # Expected count a trivial policy would 'solve' (c >= k/2) given its per-attempt rate.
        "floors": {arm: sum(p_solved_by_policy(r[arm + "_rate"], k) for r in sub)
                   for arm in ("random", "pass", "first")},
    }


def score(manifest, rows, split, k=4, solver=None, n_boot=2000, seed=0, ids=None):
    attempts = group_attempts(manifest, rows, split, k, solver, ids)
    puzzles = per_puzzle(manifest, attempts, k)
    solvers = sorted({solver_of(r) for att in attempts.values() for r in att})
    flat = [r for att in attempts.values() for r in att]
    epochs = {r.get("harness_epoch") for r in flat}
    # Each BRIDGE_CONDITIONS key: the solver's value, None for a solver without it (group_attempts refuses a mix).
    conditions = {}
    for key, _ in BRIDGE_CONDITIONS:
        seen = {r.get(key) for r in flat}
        conditions[key] = seen.pop() if len(seen) == 1 else None
    return dict(conditions, **{
        # The interface scored; None for rows from before the stamp (group_attempts refuses a mix).
        "harness_epoch": epochs.pop() if len(epochs) == 1 else None,
        # The rows' one game cap (group_attempts refuses a mix); None when no row has one.
        "game_cap_ms": next((r["game_cap_ms"] for r in flat if r.get("game_cap_ms")), None),
        "suite": manifest.get("suite"), "suite_version": manifest.get("version"),
        "suite_sha256": manifest.get("suite_sha256"), "split": split, "solver": solvers[0] if solvers else None,
        # A subset (--ids) is a pilot: it proves the chain, and is never the split's PUZ score.
        "pilot": bool(manifest.get("pilot")) or ids is not None,
        "aggregate": aggregate(puzzles, k, n_boot, seed),
        "budget": budget(flat),
        "integrity": integrity(flat),
        "puzzles": sorted(puzzles, key=lambda r: r["id"]),
    })


def _mean_se(values):
    """The mean over puzzles and its standard error (attempts clustered within each puzzle, Miller 2024: the standard
    error of the per-puzzle means). None for fewer than two puzzles."""
    n = len(values)
    if n < 2:
        return None
    m = sum(values) / n
    return m, (sum((v - m) ** 2 for v in values) / (n - 1)) ** 0.5 / n ** 0.5


def clustered_ci(values):
    """The normal-approximation 95% interval of a mean over puzzles, attempts clustered within each puzzle."""
    ms = _mean_se(values)
    return None if ms is None else [ms[0] - 1.96 * ms[1], ms[0] + 1.96 * ms[1]]


def mde80(values):
    """2.8 standard errors of a difference between two independent runs of this size: the smallest difference in mean
    it detects at 80% power, alpha 0.05. None for fewer than two puzzles, or when every puzzle has the same rate: no
    spread is no estimate of it, not a detectable difference of 0 (pr-review of #282)."""
    ms = _mean_se(values)
    return None if ms is None or ms[1] == 0 else 2.8 * 2 ** 0.5 * ms[1]


BUDGET_FIELDS = ("model_calls", "prompt_tokens", "completion_tokens", "model_ms", "lookups")


def budget(rows):
    """What the attempts spent (BENCHMARK-V2 section 3), from the rows that carry it: per field the total and the
    median per attempt. `recorded` counts those rows; rows from before the stamp carry none."""
    have = [r for r in rows if all(isinstance(r.get(f), (int, float)) for f in BUDGET_FIELDS)]
    out = {"attempts": len(rows), "recorded": len(have)}
    if have:
        mid = len(have) // 2
        for f in BUDGET_FIELDS:
            vals = sorted(r[f] for r in have)
            out[f] = {"total": sum(vals), "median": vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2}
    return out


def integrity(rows):
    """Attempts that are not clean evidence, counted beside the score at weight 0: capped (the wall clock ended the
    game) and solves decided by an empty library."""
    return {"attempts": len(rows), "capped": sum(1 for r in rows if r.get("status") == "invalid"),
            "tainted": sum(1 for r in rows if r.get("tainted_by_deckout"))}


def paired(rep_a, rep_b, base=None, n_boot=10000, seed=7):
    """B minus A, puzzle by puzzle: the mean per-puzzle pass@1 difference, its bootstrap interval over puzzles (ids
    sorted, so the resample does not depend on the order results were read) and its clustered interval. With
    base=(C, D), the difference of differences (B - A) - (D - C). Refuses runs of other puzzles, suites or k."""
    reps = [rep_a, rep_b] + list(base or ())
    first = reps[0]
    for r in reps[1:]:
        for key in ("suite_sha256", "split"):
            if r.get(key) != first.get(key):
                raise Refused("paired runs differ in %s (%s vs %s)" % (key, first.get(key), r.get(key)))
        if r["aggregate"]["k"] != first["aggregate"]["k"]:
            raise Refused("paired runs differ in k")
    # A cap and a deadline are conditions, compared across every run that recorded one; a run with none (a baseline
    # arm, no manifest) is not known to differ.
    for key, unknown in (("game_cap_ms", None), ("call_timeout_ms", NO_MANIFEST)):
        known = sorted({r.get(key) for r in reps} - {unknown}, key=str)
        if len(known) > 1:
            raise Refused("paired runs differ in %s (%s)" % (key, " vs ".join(map(str, known))))
    rates = [{p["id"]: p["pass@1"] for p in r["puzzles"]} for r in reps]
    if any(set(x) != set(rates[0]) for x in rates):
        raise Refused("paired runs cover different puzzles")
    ids = sorted(rates[0])
    diffs = [rates[1][p] - rates[0][p] for p in ids]
    if base:
        diffs = [d - (rates[3][p] - rates[2][p]) for d, p in zip(diffs, ids)]
    epochs = [r.get("harness_epoch") for r in reps]
    tokens = [r.get("max_tokens") for r in reps]
    return {"n_puzzles": len(ids), "mean": _mean(diffs), "ci95": bootstrap_ci(diffs, _mean, n_boot, seed),
            "ci95_clustered": clustered_ci(diffs), "solvers": [r.get("solver") for r in reps],
            "difference_of_differences": bool(base),
            # A difference across interfaces is a measurement of the change, and is labelled as one.
            "epochs": epochs, "cross_epoch": len(set(epochs)) > 1,
            # Each model at its own maximum (frontier5) is a fair pair: labelled, not refused.
            "max_tokens": tokens, "cross_max_tokens": len(set(tokens) - {NO_MANIFEST}) > 1}


def render_paired(c, n_boot, seed):
    """The paired line, with what makes it a comparison across conditions."""
    return ("  paired %-30s %s  95%% CI %s  clustered %s  (%d puzzles, ids sorted, %d resamples, seed %d)%s%s"
            % ("(B - A) - (D - C)" if c["difference_of_differences"] else "B - A", signed(c["mean"]), _ci(c["ci95"]),
               _ci(c["ci95_clustered"]), c["n_puzzles"], n_boot, seed,
               "  CROSS-EPOCH %s" % " vs ".join(e or "unstamped" for e in c["epochs"]) if c["cross_epoch"] else "",
               "  CROSS-MAX-TOKENS %s" % " vs ".join("default" if t is None else str(t) for t in c["max_tokens"])
               if c["cross_max_tokens"] else ""))


def signed(x):
    """A difference to three places with its sign; one that rounds to zero prints +0.000, never -0.000."""
    x = round(x, 3)
    return "%+.3f" % (0.0 if x == 0 else x)


def _fmt(x):
    return "-" if x is None else ("%.3f" % x if isinstance(x, float) else str(x))


def _ci(c):
    return "[%s, %s]" % (_fmt(c[0]), _fmt(c[1])) if c else "[-]"


def render(rep):
    a = rep["aggregate"]
    dev = rep.get("dev_lane")
    lines = ["DEVELOPMENT LANE (%s): local development on the owner's subscription, not a benchmark score" % dev] if dev else []
    cap = rep.get("game_cap_ms")
    lines += [("DEV" if dev else "PUZ") + "  suite=%s v%s (%s)  split=%s  solver=%s  epoch=%s  puzzles=%d  k=%d%s%s"
             % (rep["suite"], rep["suite_version"], rep["suite_sha256"][:12], rep["split"], rep["solver"],
                rep.get("harness_epoch") or "unstamped", a["n_puzzles"], a["k"],
                "  cap=%gmin" % (cap / 60000) if cap else "", "  PILOT" if rep["pilot"] else "")
              + "".join("  %s=%s" % (k, rep[k]) for k in ("call_timeout_ms", "max_tokens")
                        if rep.get(k) not in (None, NO_MANIFEST))]
    h = a["headline"]
    lines.append("  " + ("solve rate" if dev else "headline") + " %-28s %s  95%% CI %s (clustered %s)   floors: random 0.000, PASS %s, first %s"
                 % (h["name"], _fmt(h["value"]), _ci(h["ci95"]), _ci(h.get("ci95_clustered")), _fmt(h["floors"]["pass"]),
                    _fmt(h["floors"]["first"])))
    for key in ("pass@1", "pass^2", "pass^k"):
        if key in a:
            b = a[key]
            extra = ("  (if independent %s)" % _fmt(b["if_independent"])) if "if_independent" in b else ""
            if b.get("ci95_wilson"):
                extra += "  (Wilson %s)" % _ci(b["ci95_wilson"])
            lines.append("  %-37s %s  95%% CI %s   floors: random %s, PASS %s, first %s%s"
                         % (key, _fmt(b["value"]), _ci(b["ci95"]), _fmt(b["floors"]["random"]),
                            _fmt(b["floors"]["pass"]), _fmt(b["floors"]["first"]), extra))
    if a["pass@1"].get("ci95_clustered"):
        lines.append("  pass@1 clustered 95%% CI %s; smallest difference two runs of this size detect: %s"
                     % (_ci(a["pass@1"]["ci95_clustered"]),
                        "not estimable (no spread)" if a["pass@1"]["mde80"] is None else _fmt(a["pass@1"]["mde80"])))
    if "normalized" in a:
        lines.append("  normalized (random=0, Forge AI=1)      %s   (Forge AI rate %s)"
                     % (_fmt(a["normalized"]["value"]), _fmt(a["normalized"]["forge_ai_rate"])))
    s = a["solved"]
    for name, b in [("all", s["all"])] + [("tier " + t, b) for t, b in s["by_tier"].items()] + [
            ("tier v2 " + t, b) for t, b in s.get("by_tier_v2", {}).items()]:
        lines.append("  solved %-30s %d/%d  95%% CI %s Wilson %s   expected floors: random %.2f, PASS %.2f, first %.2f"
                     % (name, b["solved"], b["of"], _ci(b["ci95"]), _ci(b["ci95_wilson"]), b["floors"]["random"],
                        b["floors"]["pass"], b["floors"]["first"]))
    b = rep.get("budget") or {}
    if b.get("recorded"):
        lines.append("  budget per attempt (median; total): " + ", ".join(
            "%s %s; %s" % (f, _fmt(b[f]["median"]), _fmt(b[f]["total"])) for f in BUDGET_FIELDS)
            + "  (%d of %d attempts recorded)" % (b["recorded"], b["attempts"]))
    elif b:
        lines.append("  budget: not recorded (rows from before harness-epoch-69)")
    i = rep.get("integrity")
    if i:
        lines.append("  integrity (weight 0): %d of %d attempts capped, %d decided by a deck-out"
                     % (i["capped"], i["attempts"], i["tainted"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", required=True, help="frozen suite manifest (suite.json)")
    ap.add_argument("--split", required=True, choices=pf.SPLITS)
    ap.add_argument("--k", type=int, default=4, help="attempts per puzzle (default 4)")
    ap.add_argument("--solver", default=None, help="score only rows of this solver (arm or arm/model)")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0, help="bootstrap RNG seed")
    ap.add_argument("--json", default=None, help="write the full report here")
    ap.add_argument("--ids", default=None,
                    help="score only these puzzle ids (comma-separated): a PILOT that proves the chain, not a score")
    ap.add_argument("--paired-with", nargs="+", default=None, metavar="RESULTS",
                    help="also score these results (same suite, split, k) and print B minus A, puzzle by puzzle")
    ap.add_argument("--against", nargs=2, default=None, metavar=("C", "D"),
                    help="with --paired-with: the difference of differences (B - A) - (D - C); C and D are dirs")
    ap.add_argument("--paired-bootstrap", type=int, default=10000, help="resamples for the paired interval")
    ap.add_argument("--paired-seed", type=int, default=7, help="RNG seed for the paired interval")
    ap.add_argument("results", nargs="+")
    a = ap.parse_args(argv)
    if a.k < 1:
        ap.error("--k must be >= 1")
    if a.against and not a.paired_with:
        ap.error("--against needs --paired-with")
    if a.paired_bootstrap < 1:
        ap.error("--paired-bootstrap must be >= 1")
    try:
        manifest = pf.load_suite(a.suite)
        problems = pf.validate_suite(manifest)
        if problems:
            raise Refused("suite does not validate: " + "; ".join(problems[:5]))
        ids = set(a.ids.split(",")) if a.ids else None
        def scored(paths):  # every run of one command scored alike, the paired ones too
            return score(manifest, load_rows(paths), a.split, a.k, a.solver, a.bootstrap, a.seed, ids)
        rep = scored(a.results)
    except Refused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    print(render(rep))
    if a.paired_with:
        try:
            base = (scored([a.against[0]]), scored([a.against[1]])) if a.against else None
            rep["paired"] = paired(rep, scored(a.paired_with), base, n_boot=a.paired_bootstrap, seed=a.paired_seed)
        except Refused as e:
            print("REFUSED: %s" % e, file=sys.stderr)
            return 2
        print(render_paired(rep["paired"], a.paired_bootstrap, a.paired_seed))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
