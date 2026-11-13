#!/usr/bin/env python3
"""Score an STR-AI panel run: per-cell win rates, the equal-weight pool, the cross-panel margin and
STR_norm, each next to its anchors.

A panel (eval/suites/panels-v1.json) is a fixed set of cells. In the MIRROR panel (the headline) the
pilot under test, identity A, plays deck d against the Forge AI on deck d; in the CROSS panel it
plays deck d against the Forge AI on deck d+5 (mod 16), and every cell has an AI-vs-AI control on
the same deck pair. run_batch.py --panel writes one directory per cell under a root:

  ROOT/<cell id>/                    the pilot's games
  ROOT/<cell id>__control/           AI-vs-AI control (cross panel)
  ROOT/<cell id>__sanity-<arm>/      floor, random, empty, garbage (run_batch.py --sanity-arms)

What it reports (eval/research/2026-10-15-scoring-design.md sections 1.2, 1.6, 1.7):

  cell       wins / decisive games with a Wilson 95% CI, the strict score (wins + half the draws
             over every started game, an unfinished game scoring 0), and the seat-1 win share.
  pooled     the mean over cells, every cell weighted equally whatever its game count, with the
             stratified CI (Var = sum p_m(1-p_m)/n_m / M^2, p_m Jeffreys-smoothed so a 0/n cell does
             not claim zero variance) and a cluster bootstrap that resamples seeds (both seat orders
             together) within each cell. Plus the worst and best cell and the number of cells whose
             Wilson CI lies wholly below 50%.
  margin     cross panel only: WR(pilot on d vs AI on d') - WR(AI on d vs AI on d') per cell,
             pooled the same way; it removes the matchup's own asymmetry.
  STR_norm   (WR - WR_random) / (WR_AImirror - WR_random), uncapped: 0 = random, 1 = plays like the
             Forge AI, above 1 = better. WR_random is the pooled random sanity arm on the same cells;
             WR_AImirror is 0.5 in the mirror panel (by symmetry) and the pooled control in the cross
             panel. Without a random arm STR_norm is not computed.

The headline score is decisive-only (--score decisive, the aggregate.py convention); --score strict
uses the strict score instead. Both are always printed.

Validity: every cell must exist and hold one experiment whose identity B is the Forge AI and whose
game lines carry the cell's pinned deck_sha; an llm pilot must have played under the panels file's
`pilot_condition` (refused otherwise, whatever the flags); the pilot must be the same player with the same
settings in every cell (rating.check_conditions); aggregate.py's fallback and harness-decided gates
must pass per cell; the sanity arms (sanity_arms.py) must not beat the floor in any cell; and every
game read must belong to one benchmark version (benchmark_version.py), which the report carries. The verdict
(`status`) is NOT VALID for any of these; else INCOMPLETE when a cell is missing, a played seed has one seat order, or
the played cells hold different seeds (the pool is then not seed-paired); else NO FLOOR when the random sanity arm is
not in every cell (no baseline for STR_norm); else VALID. A cell with a
game whose pilot lost a decision to a dead model bridge is refused (rating.bridge_down_problems): a panel seed is
replayed, never dropped.

What it cannot do: it does not re-run crashed seeds (a crash is an invalid game in the strict score
and absent from the decisive one), and it does not decide how a wall-clock cap is charged.

Usage:
  python3 eval/panel_score.py ROOT --panel mirror|cross [--panel-file F] [--score decisive|strict]
                              [--resamples 2000] [--json] [--allow-mixed-conditions]
"""

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aggregate  # noqa: E402
import benchmark_version  # noqa: E402
import cost_report  # noqa: E402  which specs run on the Claude CLI (a development lane, never scored)
import rating  # noqa: E402
import sanity_arms  # noqa: E402

DEFAULT_PANEL_FILE = Path(__file__).resolve().parent / "suites" / "panels-v1.json"
DEFAULT_RESAMPLES = 2000
DEFAULT_BOOTSTRAP_SEED = 20261015
CONTROL_SUFFIX = "__control"
Z = 1.96


class PanelError(Exception):
    """The panel file or a panel run directory is not what the panel says it is."""


# ------------------------------------------------------------ panel file

def parse_seed_spec(spec):
    """'7800..7849' or '7800,7801' -> sorted list of ints."""
    spec = str(spec).strip()
    if ".." in spec:
        lo, hi = spec.split("..", 1)
        return list(range(int(lo), int(hi) + 1))
    return sorted(int(s) for s in spec.split(",") if s.strip())


def load_panel(panel_file, name):
    """(panel dict, {deck name: pool entry}) for panel `name`."""
    try:
        doc = json.loads(Path(panel_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PanelError("unreadable panel file %s: %s" % (panel_file, e))
    panels = doc.get("panels") or {}
    if name not in panels:
        raise PanelError("panel %r not in %s (has: %s)" % (name, panel_file, ", ".join(sorted(panels))))
    pool = {d["name"]: d for d in doc.get("deck_pool") or []}
    panel = panels[name]
    for c in panel.get("cells") or []:
        for side in ("deck_a", "deck_b"):
            if c[side] not in pool:
                raise PanelError("cell %s names deck %r, which is not in the deck pool" % (c["id"], c[side]))
    return panel, pool, doc


def pilot_condition_problems(games, doc):
    """How the llm pilot's games depart from the panels file's pinned pilot condition (review E2): one line per
    field and value seen, [] when every game matches. A field a game does not carry is a departure."""
    want = (doc.get("pilot_condition") or {}).get("fields") or {}
    seen = {}
    for g in games:
        if g.get("controller_a") != "llm":
            continue
        for f, v in want.items():
            if g.get(f, "(absent)") != v:
                seen.setdefault((f, json.dumps(g.get(f, "(absent)"))), 0)
                seen[(f, json.dumps(g.get(f, "(absent)")))] += 1
    return ["the pilot played %d game(s) with %s=%s; the panel's pilot condition is %s"
            % (n, f, v, json.dumps(want[f])) for (f, v), n in sorted(seen.items())]


def pair_deck_sha(sha_a, sha_b):
    """The deck_sha run_batch.py stamps on a panel cell: one sha for a mirror, 'a+b' for a cross cell."""
    return sha_a if sha_a == sha_b else "%s+%s" % (sha_a, sha_b)


def cell_deck_sha(cell, pool):
    return pair_deck_sha(pool[cell["deck_a"]]["sha12"], pool[cell["deck_b"]]["sha12"])


# ------------------------------------------------------------- one cell

def read_block(run_dir, expected_deck_sha=None, expected_b=None, expected_a=None):
    """One block directory as a dict, or None when it does not exist. PanelError when it is not one
    experiment, or identity B / the deck pair is not what the panel says."""
    p = Path(run_dir)
    if not p.is_dir():
        return None
    parsed = aggregate.parse_dir_full(p)
    exps = parsed["experiments"]
    if not exps and not parsed["crashed"]:
        return None  # a cell the budget stopped before its first game: a manifest and no game file is not played
    if len(exps) != 1:
        raise PanelError("%s holds %d experiment conditions; a panel cell is one" % (p, len(exps)))
    exp = exps[0]
    games = rating.tag_fake_policy(exp["games"], p)
    problems = []
    for g in games:
        # Owner, 2026-10-24: a development lane (a claude-cli: seat) is never part of a benchmark score.
        if (g.get("dev_lane") or g.get("claude_cli_bridge_version_a") or g.get("claude_cli_bridge_version_b")
                or any(g.get("controller_" + i) == "llm" and cost_report.is_cli_lane(g.get(t + "_" + i))
                       for i in ("a", "b") for t in ("smart", "fast"))):
            problems.append("a development lane (%s) played here; it is never part of a benchmark score"
                            % (g.get("dev_lane") or "claude-cli"))
        if g.get("ai_profile_a", "") or g.get("ai_profile_b", ""):
            problems.append("a Forge AI profile (ai_profile_a=%r, ai_profile_b=%r) played here; the benchmark's "
                            "opponent is the compiled defaults" % (g.get("ai_profile_a", ""), g.get("ai_profile_b", "")))
        if expected_b and g.get("controller_b") != expected_b:
            problems.append("identity B is %r, not %r" % (g.get("controller_b"), expected_b))
        if expected_a and g.get("controller_a") != expected_a:
            problems.append("identity A is %r, not %r" % (g.get("controller_a"), expected_a))
        if expected_deck_sha and g.get("deck_sha") != expected_deck_sha:
            problems.append("deck_sha %r, the cell pins %r" % (g.get("deck_sha"), expected_deck_sha))
    problems += rating.bridge_down_problems(games)
    if problems:
        raise PanelError("%s: %s" % (p, "; ".join(sorted(set(problems)))))
    decisions = aggregate._experiment_decisions(exp, parsed["decisions"])
    by_seed, orders = {}, {}
    for g in games + parsed["crashed"]:
        by_seed.setdefault(g.get("seed"), []).append(g.get("winner"))
        # A crashed game has no game line and so no swap: it fills one seat order of its seed, whichever it was.
        orders.setdefault(g.get("seed"), []).append(None if g.get("_no_game_line") else bool(g.get("swap", False)))
    one_order = sorted(s for s, os_ in orders.items() if s is not None
                       and len({o for o in os_ if o is not None}) + sum(1 for o in os_ if o is None) < 2)
    wins = sum(1 for g in games if g.get("winner") == "A")
    decisive = sum(1 for g in games if g.get("winner") in ("A", "B"))
    draws = sum(1 for g in games if g.get("winner") == "draw")
    started = len(games) + len(parsed["crashed"])
    summ = aggregate.summarize_games(games)
    lo, hi = aggregate.wilson_ci(wins, decisive)
    return {
        "dir": str(p),
        "pilot": rating.player(games[0], "a") if games else None,
        "games": games,
        "wins": wins, "decisive": decisive, "draws": draws, "started": started,
        "crashed": len(parsed["crashed"]),
        "win_rate": wins / decisive if decisive else None,
        "wilson95": [lo, hi],
        "strict_score": (wins + 0.5 * draws) / started if started else None,
        "seat1_share": (summ.get("seat1_wins", 0) / decisive) if decisive else None,
        "seeds": sorted(s for s in by_seed if s is not None),
        "one_seat_order": one_order,
        "clusters": [w for _, w in sorted(by_seed.items(), key=lambda kv: str(kv[0]))],
        "fallback_gate": aggregate.summarize_fallback_gate(games, decisions, aggregate.DEFAULT_MAX_FALLBACK_RATE),
    }


def cell_point(block, score):
    """(p, n) of a block under `score` ('decisive' or 'strict')."""
    if score == "strict":
        return block["strict_score"], block["started"]
    return block["win_rate"], block["decisive"]


def _cluster_score(winners_lists, score):
    w = n = 0.0
    for ws in winners_lists:
        for x in ws:
            if score == "strict":
                n += 1
                w += 1.0 if x == "A" else 0.5 if x == "draw" else 0.0
            elif x in ("A", "B"):
                n += 1
                w += 1.0 if x == "A" else 0.0
    return (w / n) if n else None


def resample_point(block, score, rng):
    cl = block["clusters"]
    if not cl:
        return None
    return _cluster_score([cl[rng.randrange(len(cl))] for _ in cl], score)


# --------------------------------------------------------------- pooling

def stratified(points):
    """Equal-weight mean of (p, n) cells and its normal CI from the stratified variance."""
    pts = [(p, n) for p, n in points if p is not None and n]
    if not pts:
        return {"score": None, "ci95": None, "se": None, "n_cells": 0}
    m = len(pts)
    mean = sum(p for p, _ in pts) / m
    var = 0.0
    for p, n in pts:
        pt = (p * n + 0.5) / (n + 1.0)           # Jeffreys-smoothed: a 0/n cell is not certain
        var += pt * (1 - pt) / n
    se = math.sqrt(var) / m
    return {"score": mean, "se": se, "ci95": [max(0.0, mean - Z * se), min(1.0, mean + Z * se)], "n_cells": m}


def _pct(vals, q):
    vs = sorted(vals)
    return vs[min(len(vs) - 1, max(0, int(q * len(vs))))]


def bootstrap(cells, score, resamples, seed, fn):
    """Percentile CI of fn(replicate) where a replicate maps each (cell, role) to a resampled point.
    `cells` is {cell id: {role: block}}; fn returns a number or None (skipped)."""
    rng = random.Random(seed)
    vals = []
    for _ in range(resamples):
        rep = {cid: {role: resample_point(b, score, rng) for role, b in roles.items() if b is not None}
               for cid, roles in sorted(cells.items())}
        v = fn(rep)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return [_pct(vals, 0.025), _pct(vals, 0.975)]


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def str_norm(wr, wr_random, wr_ai):
    """(WR - WR_random) / (WR_AImirror - WR_random), uncapped; None when undefined."""
    if wr is None or wr_random is None or wr_ai is None or wr_ai - wr_random <= 0:
        return None
    return (wr - wr_random) / (wr_ai - wr_random)


# ---------------------------------------------------------------- report

def score_panel(root, panel_name, panel_file=DEFAULT_PANEL_FILE, score="decisive",
                resamples=DEFAULT_RESAMPLES, bootstrap_seed=DEFAULT_BOOTSTRAP_SEED, allow_mixed=False):
    panel, pool, doc = load_panel(panel_file, panel_name)
    anchors = doc.get("anchors") or {}
    root = Path(root)
    opponent = panel.get("opponent", "ai")
    is_cross = bool(panel.get("control"))
    random_margin = float(anchors.get("random_max_margin_over_floor", sanity_arms.DEFAULT_RANDOM_MARGIN))
    panel_seeds = set(parse_seed_spec(panel["seeds"])) if panel.get("seeds") else None
    control_seeds = set(parse_seed_spec(panel["control"]["seeds"])) if is_cross else None
    pairs = bool(panel.get("pairs", True))

    cells, out_cells, missing, problems, all_games = {}, {}, [], [], []
    incomplete, played_seeds = [], {}
    pilot_games = []
    for c in panel["cells"]:
        cid = c["id"]
        dsha = cell_deck_sha(c, pool)
        block = read_block(root / cid, dsha, expected_b=opponent)
        roles = {"model": block}
        if block is None:
            missing.append(cid)
        else:
            pilot_games += block["games"]
            all_games += block["games"]
            stray = sorted(set(block["seeds"]) - panel_seeds) if panel_seeds else []
            if stray:
                problems.append("%s played seeds outside the panel's list: %s" % (cid, stray[:8]))
            played_seeds[cid] = tuple(block["seeds"])
            if pairs and block["one_seat_order"]:
                incomplete.append("%s played %d seed(s) in one seat order only: %s"
                                  % (cid, len(block["one_seat_order"]), block["one_seat_order"][:8]))
        if is_cross:
            ctl = read_block(Path(str(root / cid) + CONTROL_SUFFIX), dsha, expected_b="ai", expected_a="ai")
            roles["control"] = ctl
            if ctl is None:
                missing.append(cid + CONTROL_SUFFIX)
            else:
                all_games += ctl["games"]
                stray = sorted(set(ctl["seeds"]) - control_seeds)
                if stray:
                    problems.append("%s control played seeds outside the control list: %s" % (cid, stray[:8]))
                if pairs and ctl["one_seat_order"]:
                    incomplete.append("%s control played %d seed(s) in one seat order only: %s"
                                      % (cid, len(ctl["one_seat_order"]), ctl["one_seat_order"][:8]))
        arm_blocks = {}
        for arm in sanity_arms.ARMS:
            b = read_block(sanity_arms.arm_dir(root / cid, arm), dsha, expected_b=opponent)
            arm_blocks[arm] = b
            if b is not None:
                all_games += b["games"]
        roles["random"] = arm_blocks["random"]
        cells[cid] = roles
        sanity = None
        if any(b is not None for b in arm_blocks.values()):
            sanity = sanity_arms.check(root / cid, random_margin, arms={
                a: (None if b is None else {"games": {(g["seed"], bool(g.get("swap", False))): g.get("winner")
                                                      for g in b["games"] if g.get("seed") is not None},
                                            "crashed": b["crashed"]})
                for a, b in arm_blocks.items()})
        row = {"deck_a": c["deck_a"], "deck_b": c["deck_b"], "deck_sha": dsha, "sanity": sanity}
        if block is not None:
            p, n = cell_point(block, score)
            row.update({k: block[k] for k in ("dir", "pilot", "wins", "decisive", "draws", "started", "crashed",
                                               "win_rate", "wilson95", "strict_score", "seat1_share")})
            row["score"], row["n"] = p, n
            fg = block["fallback_gate"]
            row["fallback_gate"] = {"failed": fg["failed"], "harness_failed": fg["harness_failed"],
                                    "fallback_rate": fg["fallback_rate"],
                                    "harness_decided_rate": fg["harness_decided_rate"]}
        if is_cross and roles.get("control") is not None:
            ctl = roles["control"]
            cp, cn = cell_point(ctl, score)
            row["control"] = {"wins": ctl["wins"], "decisive": ctl["decisive"], "started": ctl["started"],
                              "win_rate": ctl["win_rate"], "strict_score": ctl["strict_score"], "score": cp, "n": cn}
            if block is not None and row["score"] is not None and cp is not None:
                row["margin"] = row["score"] - cp
        if arm_blocks["random"] is not None:
            row["random_score"] = cell_point(arm_blocks["random"], score)[0]
        out_cells[cid] = row

    pilots = sorted({r["pilot"] for r in out_cells.values() if r.get("pilot")})
    if len(pilots) > 1:
        problems.append("cells were played by different pilots: %s" % ", ".join(pilots))
    cond = rating.check_conditions(all_games)
    # The pilot's own condition is part of the benchmark: no --allow-mixed-conditions lets another be scored.
    off = pilot_condition_problems(pilot_games, doc)
    if off:
        raise PanelError("; ".join(off))
    version, version_problem = None, None
    try:
        version = benchmark_version.stamp(panels_path=panel_file, games=all_games)
    except benchmark_version.VersionError as e:
        version_problem = str(e)
    mixing = cond + ([version_problem] if version_problem else [])
    if mixing and not allow_mixed:
        raise PanelError("; ".join(mixing))

    scored = {cid: r for cid, r in out_cells.items() if r.get("score") is not None}
    pooled = stratified([(r["score"], r["n"]) for r in scored.values()])
    if scored:
        worst = min(scored, key=lambda k: scored[k]["score"])
        best = max(scored, key=lambda k: scored[k]["score"])
        pooled.update({"worst_cell": [worst, scored[worst]["score"]], "best_cell": [best, scored[best]["score"]],
                       "n_cells_ci_below_half": sum(1 for r in scored.values() if r["wilson95"][1] < 0.5)})
    pooled["ci95_bootstrap"] = bootstrap(cells, score, resamples, bootstrap_seed,
                                         lambda rep: _mean([x.get("model") for x in rep.values()]))
    pooled["strict_pooled"] = _mean([r.get("strict_score") for r in scored.values()])
    pooled["decisive_pooled"] = _mean([r.get("win_rate") for r in scored.values()])

    margin = None
    if is_cross:
        ms = {cid: r for cid, r in out_cells.items() if r.get("margin") is not None}
        if ms:
            m = len(ms)
            var = 0.0
            for r in ms.values():
                for p, n in ((r["score"], r["n"]), (r["control"]["score"], r["control"]["n"])):
                    pt = (p * n + 0.5) / (n + 1.0)
                    var += pt * (1 - pt) / n
            se = math.sqrt(var) / m
            mean = sum(r["margin"] for r in ms.values()) / m
            worst = min(ms, key=lambda k: ms[k]["margin"])
            best = max(ms, key=lambda k: ms[k]["margin"])
            margin = {"score": mean, "se": se, "ci95": [mean - Z * se, mean + Z * se], "n_cells": m,
                      "worst_cell": [worst, ms[worst]["margin"]], "best_cell": [best, ms[best]["margin"]],
                      "ci95_bootstrap": bootstrap(
                          cells, score, resamples, bootstrap_seed + 1,
                          lambda rep: _mean([x["model"] - x["control"] for x in rep.values()
                                             if x.get("model") is not None and x.get("control") is not None]))}

    # Anchors and STR_norm.
    randoms = [r.get("random_score") for r in out_cells.values()]
    wr_random = _mean(randoms) if all(x is not None for x in randoms) and randoms else None
    if is_cross:
        wr_ai = _mean([r["control"]["score"] for r in out_cells.values() if r.get("control")])
        wr_ai_source = "pooled AI-vs-AI control"
    else:
        wr_ai = float(anchors.get("ai_mirror_win_rate", 0.5))
        wr_ai_source = "Forge-AI mirror, 50% by symmetry (panels file anchors)"

    def norm_rep(rep):
        wr = _mean([x.get("model") for x in rep.values()])
        wrr = _mean([x.get("random") for x in rep.values()])
        wra = _mean([x.get("control") for x in rep.values()]) if is_cross else wr_ai
        return str_norm(wr, wrr, wra)
    anchors_out = {
        "wr_random": wr_random,
        "wr_random_note": None if wr_random is not None else "no random sanity arm in every cell: STR_norm not computed",
        "wr_ai_mirror": wr_ai, "wr_ai_mirror_source": wr_ai_source,
        "str_norm": str_norm(pooled["score"], wr_random, wr_ai),
        "str_norm_ci95_bootstrap": bootstrap(cells, score, resamples, bootstrap_seed + 2, norm_rep)
        if wr_random is not None else None,
        "formula": "(WR - WR_random) / (WR_AImirror - WR_random), uncapped",
    }

    sanity_rows = {cid: r["sanity"] for cid, r in out_cells.items() if r["sanity"] is not None}
    sanity_violations = ["%s: %s" % (cid, v) for cid, s in sanity_rows.items() for v in s["violations"]]
    gate_failures = ["%s: %s" % (cid, what) for cid, r in out_cells.items() if r.get("fallback_gate")
                     for what, bad in (("fallback gate", r["fallback_gate"]["failed"]),
                                       ("harness-decided gate", r["fallback_gate"]["harness_failed"])) if bad]
    # Seed-paired cells (the panels file's promise): every played cell holds the same seeds, each in both seat
    # orders. A budget stop mid-panel leaves cells of different lengths; their equal-weight pool is not the panel's.
    if len(set(played_seeds.values())) > 1:
        sizes = sorted({len(v) for v in played_seeds.values()})
        incomplete.append("cells played different seed sets (%s seeds per cell): the pool is not seed-paired"
                          % "/".join(str(n) for n in sizes))
    per_cell = len(panel_seeds) * (2 if pairs else 1) if panel_seeds else None
    played = sum(r.get("started", 0) for r in out_cells.values())
    complete = not missing and not incomplete
    # A score without its floor is not a score (CLAUDE.md): the random arm in every cell is the floor of STR_norm.
    floor_present = wr_random is not None
    if problems or sanity_violations or gate_failures or mixing:
        status = "NOT VALID"
    elif not complete:
        status = "INCOMPLETE"
    elif not floor_present:
        status = "NO FLOOR"
    else:
        status = "VALID"
    return {
        "panel": panel_name,
        "panel_file": str(panel_file),
        "root": str(root),
        "benchmark_version": version,
        "benchmark_version_problem": version_problem,
        "condition_problems": cond,
        "mixed_allowed": bool(allow_mixed and mixing),
        "score_mode": score,
        "pilot": pilots[0] if len(pilots) == 1 else pilots,
        "cells": out_cells,
        "pooled": pooled,
        "margin": margin,
        "anchors": anchors_out,
        "sanity": {"cells_checked": len(sanity_rows), "violations": sanity_violations,
                   "cells_without_arms": sorted(set(out_cells) - set(sanity_rows))},
        "gate_failures": gate_failures,
        "problems": problems,
        "missing": missing,
        "incomplete": incomplete,
        "games_played": played,
        "games_expected": per_cell * len(out_cells) if per_cell else None,
        "seeds_per_cell": {"played": len(next(iter(played_seeds.values()))) if len(set(played_seeds.values())) == 1
                           else None, "published": len(panel_seeds) if panel_seeds else None},
        "floor_present": floor_present,
        "complete": complete,
        "status": status,
        "valid": status == "VALID",
        "bootstrap": {"resamples": resamples, "seed": bootstrap_seed,
                      "unit": "seed (both seat orders) within each cell and role"},
    }


def _pc(x):
    return "-" if x is None else "%.1f%%" % (100 * x)


def _ci(ci):
    return "-" if not ci else "[%.1f, %.1f]" % (100 * ci[0], 100 * ci[1])


def print_human(r):
    v = r["benchmark_version"]
    print("=== panel %s (%s), pilot %s ===" % (r["panel"], r["root"], r["pilot"]))
    print("benchmark version: %s" % (v["id"] if v else "NONE (%s)" % r["benchmark_version_problem"]))
    if r["mixed_allowed"]:
        print("!!! MIXED CONDITIONS ALLOWED BY FLAG: %s" % "; ".join(
            r["condition_problems"] + ([r["benchmark_version_problem"]] if r["benchmark_version_problem"] else [])))
    print("score: %s (decisive = wins/decisive; strict = wins + draws/2 over every started game)" % r["score_mode"])
    print("%-44s %9s %8s %16s %8s %8s %8s" % ("cell", "W/n", "score", "Wilson95", "strict", "seat1", "margin"))
    for cid, c in r["cells"].items():
        if c.get("score") is None and "wins" not in c:
            print("%-44s MISSING" % cid)
            continue
        print("%-44s %4d/%-4d %8s %16s %8s %8s %8s%s" % (
            cid, c["wins"], c["decisive"], _pc(c["score"]), _ci(c["wilson95"]), _pc(c["strict_score"]),
            _pc(c["seat1_share"]), "-" if c.get("margin") is None else "%+.1f" % (100 * c["margin"]),
            "" if not c["sanity"] else "  sanity %s" % ("ok" if c["sanity"]["ok"] else "VIOLATED")))
    p = r["pooled"]
    print("pooled (equal weight over %d cells): %s  stratified 95%% CI %s  bootstrap %s" % (
        p["n_cells"], _pc(p["score"]), _ci(p["ci95"]), _ci(p["ci95_bootstrap"])))
    print("        decisive-only %s   strict %s" % (_pc(p["decisive_pooled"]), _pc(p["strict_pooled"])))
    if p.get("worst_cell"):
        print("spread: worst %s %s, best %s %s, %d cell(s) with the Wilson CI wholly below 50%%" % (
            p["worst_cell"][0], _pc(p["worst_cell"][1]), p["best_cell"][0], _pc(p["best_cell"][1]),
            p["n_cells_ci_below_half"]))
    m = r["margin"]
    if m:
        print("margin over AI-vs-AI control: %+.1f pts  stratified 95%% CI [%+.1f, %+.1f]  bootstrap %s; worst %s %+.1f, "
              "best %s %+.1f" % (100 * m["score"], 100 * m["ci95"][0], 100 * m["ci95"][1], _ci(m["ci95_bootstrap"]),
                                 m["worst_cell"][0], 100 * m["worst_cell"][1], m["best_cell"][0], 100 * m["best_cell"][1]))
    a = r["anchors"]
    print("anchors: WR_random %s, WR_AImirror %s (%s)" % (_pc(a["wr_random"]), _pc(a["wr_ai_mirror"]),
                                                          a["wr_ai_mirror_source"]))
    if a["str_norm"] is not None:
        ci = a["str_norm_ci95_bootstrap"]
        print("STR_norm = %.3f%s  %s" % (a["str_norm"], "  bootstrap 95%% CI [%.3f, %.3f]" % tuple(ci) if ci else "",
                                         a["formula"]))
    else:
        print("STR_norm: not computed (%s)" % (a["wr_random_note"] or "anchors undefined"))
    s = r["sanity"]
    print("sanity arms: %d cell(s) checked, %d violation(s)%s" % (
        s["cells_checked"], len(s["violations"]),
        "; no arms in: %s" % ", ".join(s["cells_without_arms"]) if s["cells_without_arms"] else ""))
    for x in s["violations"] + r["gate_failures"] + r["problems"]:
        print("  - " + x)
    if r["missing"]:
        print("missing: %s" % ", ".join(r["missing"]))
    for x in r["incomplete"]:
        print("  - incomplete: " + x)
    sp = r["seeds_per_cell"]
    played = "%d of %s games" % (r["games_played"], r["games_expected"] or "?")
    if sp["played"] is not None:
        played += "; %d of %s published seeds in every played cell" % (sp["played"], sp["published"] or "?")
    print("verdict: %s (%s)" % ({"VALID": "VALID", "INCOMPLETE": "INCOMPLETE: a cell is missing, or the cells are not "
                                 "seed-paired in both seat orders",
                                 "NO FLOOR": "NO FLOOR: no random sanity arm in every cell, so no baseline; not "
                                 "a headline score",
                                 "NOT VALID": "NOT VALID (see above)"}[r["status"]], played))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="panel run root (run_batch.py --panel --out ROOT)")
    ap.add_argument("--panel", required=True, choices=["mirror", "cross"])
    ap.add_argument("--panel-file", default=str(DEFAULT_PANEL_FILE))
    ap.add_argument("--score", choices=["decisive", "strict"], default="decisive")
    ap.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    ap.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-mixed-conditions", action="store_true")
    a = ap.parse_args(argv)
    try:
        r = score_panel(a.root, a.panel, a.panel_file, a.score, a.resamples, a.bootstrap_seed,
                        a.allow_mixed_conditions)
    except PanelError as e:
        print("panel_score: REFUSING: %s" % e, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print_human(r)
    return 0 if r["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
