#!/usr/bin/env python3
"""Score PUZ: one solver's attempts on one split of one frozen puzzle suite (eval/PUZZLES.md).

    python3 eval/puzzle_score.py --suite puzzles/suites/<suite>/suite.json --split open RESULTS...

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

Refuses (exit 2) to: pool the open and held-out splits, mix suite versions or puzzle versions, mix
solvers, score a puzzle with fewer than k attempts, or score an unscoreable row. Stdlib only.
"""

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_format as pf  # noqa: E402
from aggregate import wilson_ci  # noqa: E402


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
    if status == "invalid":
        return False, "capped"
    if status not in ("solved", "failed"):
        raise Refused("row for %s has unknown status %r" % (row.get("id"), status))
    if status == "solved" and row.get("tainted_by_deckout"):
        return False, "tainted"
    if status == "solved" and row.get("acceptance") in ("rejected", "error"):
        # An authored puzzle's [acceptance] section was rejected (or was never readable): the
        # goal alone is not the question asked. Same rule the baseline reader applies.
        return False, "acceptance"
    return status == "solved", None


# ------------------------------------------------------------------ loading

def load_rows(paths):
    rows = []
    for p in paths:
        p = Path(p)
        files = sorted(p.rglob("puzzle-results.jsonl")) if p.is_dir() else [p]
        for f in files:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    raise Refused("%s:%d is not JSON" % (f, n))
                if d.get("schema") == "pzl-result-v1":
                    rows.append(d)
    return rows


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

    if solver:
        rows = [r for r in rows if solver_of(r) == solver]
    solvers = {solver_of(r) for r in rows}
    if len(solvers) > 1:
        raise Refused("results mix solvers %s; pass --solver to pick one" % sorted(solvers))

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
            "id": pid, "tier": rec.get("tier"), "category": rec.get("category"),
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
        "floors": {"random": 0.0, "pass": floor_margin("pass"), "first": floor_margin("first")},
    }
    out["pass@1"] = {
        "value": stat_of("pass@1")(rows), "ci95": bootstrap_ci(rows, stat_of("pass@1"), n_boot, seed),
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
    tiers = {}
    for tier in sorted({r["tier"] for r in rows}, key=lambda t: (t is None, t)):
        sub = [r for r in rows if r["tier"] == tier]
        tiers[str(tier)] = _solved_block(sub, k, n_boot, seed)
    return {"all": _solved_block(rows, k, n_boot, seed), "by_tier": tiers}


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
    return {
        "suite": manifest.get("suite"), "suite_version": manifest.get("version"),
        "suite_sha256": manifest.get("suite_sha256"), "split": split, "solver": solvers[0] if solvers else None,
        # A subset (--ids) is a pilot: it proves the chain, and is never the split's PUZ score.
        "pilot": bool(manifest.get("pilot")) or ids is not None,
        "aggregate": aggregate(puzzles, k, n_boot, seed),
        "puzzles": sorted(puzzles, key=lambda r: r["id"]),
    }


def _fmt(x):
    return "-" if x is None else ("%.3f" % x if isinstance(x, float) else str(x))


def _ci(c):
    return "[%s, %s]" % (_fmt(c[0]), _fmt(c[1])) if c else "[-]"


def render(rep):
    a = rep["aggregate"]
    lines = ["PUZ  suite=%s v%s (%s)  split=%s  solver=%s  puzzles=%d  k=%d%s"
             % (rep["suite"], rep["suite_version"], rep["suite_sha256"][:12], rep["split"], rep["solver"],
                a["n_puzzles"], a["k"], "  PILOT" if rep["pilot"] else "")]
    h = a["headline"]
    lines.append("  headline %-28s %s  95%% CI %s   floors: random 0.000, PASS %s, first %s"
                 % (h["name"], _fmt(h["value"]), _ci(h["ci95"]), _fmt(h["floors"]["pass"]), _fmt(h["floors"]["first"])))
    for key in ("pass@1", "pass^2", "pass^k"):
        if key in a:
            b = a[key]
            extra = ("  (if independent %s)" % _fmt(b["if_independent"])) if "if_independent" in b else ""
            if b.get("ci95_wilson"):
                extra += "  (Wilson %s)" % _ci(b["ci95_wilson"])
            lines.append("  %-37s %s  95%% CI %s   floors: random %s, PASS %s, first %s%s"
                         % (key, _fmt(b["value"]), _ci(b["ci95"]), _fmt(b["floors"]["random"]),
                            _fmt(b["floors"]["pass"]), _fmt(b["floors"]["first"]), extra))
    if "normalized" in a:
        lines.append("  normalized (random=0, Forge AI=1)      %s   (Forge AI rate %s)"
                     % (_fmt(a["normalized"]["value"]), _fmt(a["normalized"]["forge_ai_rate"])))
    s = a["solved"]
    for name, b in [("all", s["all"])] + [("tier " + t, b) for t, b in s["by_tier"].items()]:
        lines.append("  solved %-30s %d/%d  95%% CI %s Wilson %s   expected floors: random %.2f, PASS %.2f, first %.2f"
                     % (name, b["solved"], b["of"], _ci(b["ci95"]), _ci(b["ci95_wilson"]), b["floors"]["random"],
                        b["floors"]["pass"], b["floors"]["first"]))
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
    ap.add_argument("results", nargs="+")
    a = ap.parse_args(argv)
    if a.k < 1:
        ap.error("--k must be >= 1")
    try:
        manifest = pf.load_suite(a.suite)
        problems = pf.validate_suite(manifest)
        if problems:
            raise Refused("suite does not validate: " + "; ".join(problems[:5]))
        ids = set(a.ids.split(",")) if a.ids else None
        rep = score(manifest, load_rows(a.results), a.split, a.k, a.solver, a.bootstrap, a.seed, ids)
    except Refused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    print(render(rep))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
