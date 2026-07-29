#!/usr/bin/env python3
"""Registry for PUZ scoring runs, so two runs can actually be compared.

Why this exists
---------------
`puzzle_run.py` writes a self-contained results dir per invocation. That is
fine for one run and useless for ten: the dirs are named by whatever the
caller passed to --out, nothing records WHICH suite or WHICH contract a score
was measured against, and answering "did opus improve between Tuesday and
Friday" means opening summary.json by hand and hoping the two runs were
comparable in the first place.

They frequently are NOT comparable, and that is the real point of this file.
A pass_1 is only meaningful against a stated (suite sha256, contract_sha,
k, strictness) tuple. Comparing across a suite edit is comparing two different
tests -- so `compare` REFUSES to rank rows whose suite or contract differ, and
says why, rather than printing a tidy table that quietly misleads. This is the
same rule the rest of the repo follows for floors: a number without the thing
it was measured against is not a number.

Storage
-------
  runs/puz/INDEX.jsonl              append-only, one row per scoring run
  runs/puz/<run_id>/                the puzzle_run out-dir itself

`run_id` is `<UTC>__<suite>__<model>__k<k>`, which sorts chronologically and
is readable at a glance. Nothing here mutates a results dir; the index is
derived data and can be rebuilt from the dirs at any time (`reindex`).

Subcommands
-----------
  record  DIR [...]   append a row per puzzle_run out-dir
  reindex ROOT        rebuild INDEX.jsonl from every run dir under ROOT
  compare [--model M] [--suite S]   table across runs, newest last
  replies DIR [--all] read the model's actual answers back out, because a
                      score is not evidence that the replies made sense

Stdlib only, Python 3.9 (repo rule: no pip, no match, no X | Y).
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "runs" / "puz"
INDEX_NAME = "INDEX.jsonl"

#: Fields that must match before two rows may be ranked against each other.
#: suite_sha256 covers the positions; contract_sha covers how they were
#: rendered; k and strictness change what pass_1 even means.
COMPARABILITY_KEYS = ("suite_sha256", "contract_sha", "k", "strictness")


def _get(d, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _est(d, *path):
    blk = _get(d, *path)
    return blk.get("est") if isinstance(blk, dict) else None


def suite_identity(summary, positions_file=None):
    """-> (suite_name, sha256). Hashes the positions file the run actually
    read, NOT a name anyone typed: a suite edited in place keeps its name and
    would otherwise silently compare against its own past self."""
    pf = positions_file or summary.get("positions_file")
    if not pf:
        return None, None
    p = Path(pf)
    if not p.is_absolute():
        p = REPO_ROOT / p
    name = p.stem
    try:
        import hashlib
        return name, hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        # The suite moved or was deleted. Say so instead of inventing a hash:
        # an unknown sha must never compare equal to another unknown sha.
        return name, None


def row_from_run(run_dir):
    """-> an index row for one puzzle_run out-dir, or None if it has no
    summary.json (a crashed or still-running invocation)."""
    run_dir = Path(run_dir)
    sp = run_dir / "summary.json"
    if not sp.is_file():
        return None
    try:
        s = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    suite_name, suite_sha = suite_identity(s)
    counters = s.get("counters") or {}
    n_valid = counters.get("valid_samples")
    n_att = counters.get("attempted_samples")
    b = s.get("baselines") or {}

    return {
        "schema": "puz-index.v1",
        "run_dir": str(run_dir.relative_to(REPO_ROOT))
                   if str(run_dir).startswith(str(REPO_ROOT)) else str(run_dir),
        "generated_utc": s.get("generated_utc"),
        "suite": suite_name,
        "suite_sha256": suite_sha,
        "contract": s.get("contract"),
        "contract_sha": s.get("contract_sha"),
        "backend": s.get("backend"),
        "model": s.get("model"),
        "offline_policy": s.get("offline_policy"),
        "k": s.get("k"),
        "strictness": s.get("strictness"),
        "split": s.get("split"),
        "n_positions": s.get("n_positions"),
        "n_samples": s.get("n_samples"),
        "valid_samples": n_valid,
        "attempted_samples": n_att,
        "transport_errors": counters.get("transport_errors"),
        # A run where nothing came back is NOT a score of zero. Recording it as
        # complete would put an empty denominator next to real measurements.
        "complete": bool(n_valid) and n_valid == n_att,
        "pass_1": _est(s, "metrics", "pass_1"),
        "pass_1_ci95": _get(s, "metrics", "pass_1", "ci95"),
        "pass_k": _est(s, "metrics", "pass_k"),
        "score_incl": _est(s, "metrics", "puz_score_incl"),
        "interface_failure_rate": _est(s, "metrics", "interface_failure_rate"),
        "margin_over_max_constant_index":
            _get(s, "metrics", "pass_1", "margin_over_max_constant_index"),
        "floor_max_constant_index": _est(b, "max_constant_index", "pass_1"),
        "floor_random": _est(b, "random_choice", "pass_1"),
        "ceiling_oracle": _est(b, "oracle_ceiling", "pass_1"),
        "engine_sha": s.get("engine_sha"),
        "overlay_sha": s.get("overlay_sha"),
    }


def load_index(root):
    p = Path(root) / INDEX_NAME
    rows = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def write_index(root, rows):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(str(root / INDEX_NAME), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def append_rows(root, new_rows):
    """Append, replacing any existing row for the same run_dir so re-recording
    a rerun updates it instead of leaving two rows that disagree."""
    rows = [r for r in load_index(root)
            if r.get("run_dir") not in set(n.get("run_dir") for n in new_rows)]
    rows.extend(new_rows)
    rows.sort(key=lambda r: (r.get("generated_utc") or "", r.get("model") or ""))
    write_index(root, rows)
    return rows


def comparability_groups(rows):
    """-> dict signature -> [rows]. Rows only belong in one table together if
    every COMPARABILITY_KEY matches."""
    groups = {}
    for r in rows:
        sig = tuple(r.get(k) for k in COMPARABILITY_KEYS)
        groups.setdefault(sig, []).append(r)
    return groups


def _fmt(v, nd=3):
    if v is None:
        return "-"
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return str(v)


def format_table(rows):
    """Rows already known comparable. Newest last, so a rerun reads as a diff."""
    out = []
    hdr = ("%-20s %-34s %-4s %6s %6s %8s %8s %7s  %s"
           % ("utc", "model", "k", "pass_1", "pass_k", "vs_floor", "score10",
              "iface", "n"))
    out.append(hdr)
    out.append("-" * len(hdr))
    for r in rows:
        note = ""
        if not r.get("complete"):
            note = "  INCOMPLETE(%s/%s valid, %s transport errors)" % (
                r.get("valid_samples"), r.get("attempted_samples"),
                r.get("transport_errors"))
        out.append("%-20s %-34s %-4s %6s %6s %8s %8s %7s  %s%s" % (
            (r.get("generated_utc") or "?")[:20],
            (r.get("model") or r.get("offline_policy") or "?")[:34],
            _fmt(r.get("k"), 0), _fmt(r.get("pass_1")), _fmt(r.get("pass_k")),
            _fmt(r.get("margin_over_max_constant_index")),
            _fmt(r.get("score_incl"), 2),
            _fmt(r.get("interface_failure_rate")),
            _fmt(r.get("n_positions"), 0), note))
    return "\n".join(out)


def cmd_record(args):
    rows = []
    for d in args.dirs:
        r = row_from_run(d)
        if r is None:
            print("skip %s: no readable summary.json" % d, file=sys.stderr)
            continue
        rows.append(r)
    if not rows:
        print("nothing recorded", file=sys.stderr)
        return 3
    append_rows(args.root, rows)
    for r in rows:
        print("recorded %s  %s  pass_1=%s%s"
              % (r["run_dir"], r.get("model") or r.get("offline_policy"),
                 _fmt(r.get("pass_1")),
                 "" if r.get("complete") else "  (INCOMPLETE)"))
    return 0


def cmd_reindex(args):
    root = Path(args.root)
    found = []
    for sp in sorted(root.glob("**/summary.json")):
        r = row_from_run(sp.parent)
        if r:
            found.append(r)
    write_index(root, sorted(found, key=lambda r: (r.get("generated_utc") or "",
                                                   r.get("model") or "")))
    print("reindexed %d runs -> %s" % (len(found), root / INDEX_NAME))
    return 0


def cmd_compare(args):
    rows = load_index(args.root)
    if args.model:
        rows = [r for r in rows if args.model in (r.get("model") or "")
                or args.model in (r.get("offline_policy") or "")]
    if args.suite:
        rows = [r for r in rows if r.get("suite") == args.suite]
    if not rows:
        print("no runs recorded (try: puz_index.py record <dir>)",
              file=sys.stderr)
        return 3
    groups = comparability_groups(rows)
    ordered = sorted(groups.items(),
                     key=lambda kv: max((r.get("generated_utc") or "")
                                        for r in kv[1]))
    for i, (sig, grp) in enumerate(ordered):
        grp.sort(key=lambda r: (r.get("generated_utc") or ""))
        if len(ordered) > 1:
            print()
            print("=== comparable group %d/%d ===" % (i + 1, len(ordered)))
            print("    " + "  ".join(
                "%s=%s" % (k, (str(v)[:16] if v is not None else "UNKNOWN"))
                for k, v in zip(COMPARABILITY_KEYS, sig)))
        print(format_table(grp))
    if len(ordered) > 1:
        print()
        print("REFUSING to rank across the %d groups above: they differ in %s."
              % (len(ordered), ", ".join(COMPARABILITY_KEYS)))
        print("Scores measured against different suites or contracts are "
              "different tests, not better and worse results.")
    return 0


def position_solve_rates(run_dirs):
    """-> {model: {position_id: mean solved over k}} plus the position order.

    Read from results.jsonl, not summary.json: the per-position detail is the
    only thing that can tell a suite's FREE positions from its discriminating
    ones, and that distinction is what decides whether a leaderboard means
    anything.
    """
    per, order = {}, []
    seen = set()
    for d in run_dirs:
        rp = Path(d) / "results.jsonl"
        if not rp.is_file():
            continue
        model = None
        acc = {}
        for line in rp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            pid = r.get("position_id")
            if not pid:
                continue
            model = model or r.get("model")
            acc.setdefault(pid, []).append(1.0 if r.get("solved") else 0.0)
            if pid not in seen:
                seen.add(pid)
                order.append(pid)
        if model:
            per[model] = dict((k, sum(v) / float(len(v)))
                              for k, v in acc.items() if v)
    return per, order


def discrimination(per, order):
    """Split the suite into FREE / DEAD / DISCRIMINATING positions.

    free  -- every model solves it every time. Contributes a constant to every
             score, so it raises the headline and ranks nobody.
    dead  -- no model ever solves it. Also ranks nobody, but unlike `free` it
             does not inflate the headline.
    disc  -- the positions that actually order the leaderboard.

    Why this matters: 15 of puz-v1's 25 positions are `lethal-now`, and the
    first three models scored 1.000 on every one. That puts a floor of 0.60
    under any model that can count damage -- far above the 0.28 constant-index
    floor reported beside the headline -- and leaves the ranking resting on 9
    positions whose CI is wider than the gap between any two frontier models.
    A leaderboard that hides that is the 88% lesson with new numbers.
    """
    models = sorted(per)
    free, dead, disc = [], [], []
    for pid in order:
        vals = [per[m].get(pid) for m in models]
        if any(v is None for v in vals):
            continue
        if all(v >= 1.0 for v in vals):
            free.append(pid)
        elif all(v <= 0.0 for v in vals):
            dead.append(pid)
        else:
            disc.append(pid)
    return {"models": models, "free": free, "dead": dead, "discriminating": disc}


def cmd_leaderboard(args):
    dirs = args.dirs
    per, order = position_solve_rates(dirs)
    if not per:
        print("no results.jsonl found in the given dirs", file=sys.stderr)
        return 3
    d = discrimination(per, order)
    models, disc, free, dead = (d["models"], d["discriminating"],
                                d["free"], d["dead"])
    n_all = len(free) + len(dead) + len(disc)
    print("suite: %d scored positions across %d models" % (n_all, len(models)))
    print("  free          %3d  (every model solves -- ranks nobody, but adds "
          "%.2f to every headline)" % (len(free),
                                       len(free) / float(n_all) if n_all else 0))
    print("  dead          %3d  (no model solves -- ranks nobody)" % len(dead))
    print("  DISCRIMINATING%3d  (the positions the ranking actually rests on)"
          % len(disc))
    if n_all:
        print("  => a model that solves only the free positions already scores "
              "%.3f" % (len(free) / float(n_all)))
    if disc:
        import math
        n = len(disc)
        print()
        print("  %-30s %8s %8s   %s" % ("model", "overall", "disc", "95% CI on disc"))
        rows = []
        for m in models:
            allv = [per[m][p] for p in (free + dead + disc) if p in per[m]]
            dv = [per[m][p] for p in disc]
            est = sum(dv) / float(n)
            se = math.sqrt(max(est * (1 - est), 0.0) / n)
            rows.append((est, m, sum(allv) / float(len(allv)) if allv else 0.0,
                         se))
        for est, m, ov, se in sorted(rows, reverse=True):
            print("  %-30s %8.3f %8.3f   +/- %.3f" % (m, ov, est, 1.96 * se))
        worst = min(r[0] for r in rows)
        best = max(r[0] for r in rows)
        se_max = max(r[3] for r in rows)
        if (best - worst) < 1.96 * se_max * (2 ** 0.5):
            print()
            print("  ORDERING NOT SIGNIFICANT: the best-to-worst spread (%.3f) "
                  "is inside the\n  noise of a single %d-position estimate "
                  "(+/-%.3f). Report the scores, not the rank."
                  % (best - worst, n, 1.96 * se_max))
    return 0


def cmd_replies(args):
    """Print the model's actual answers. A score says a run happened; only the
    replies say whether it made sense."""
    p = Path(args.dir) / "results.jsonl"
    if not p.is_file():
        print("no results.jsonl in %s" % args.dir, file=sys.stderr)
        return 3
    shown = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not r.get("position_id"):
            continue
        if not args.all and not (r.get("error") or r.get("interface_error")
                                 or r.get("solved") is False):
            continue
        print("=" * 72)
        print("position %s  k=%s  cat=%s  n_options=%s"
              % (r.get("position_id"), r.get("k"), r.get("category"),
                 r.get("n_options")))
        print("  menu_number=%s option_index=%s score=%s solved=%s outcome=%s"
              % (r.get("menu_number"), r.get("option_index"), r.get("score"),
                 r.get("solved"), r.get("outcome")))
        if r.get("error"):
            print("  ERROR: %s" % str(r["error"])[:300])
        if r.get("interface_error"):
            print("  INTERFACE_ERROR: %s" % r["interface_error"])
        print("-" * 72)
        print(r.get("reply") or "(no reply)")
        shown += 1
        if args.limit and shown >= args.limit:
            break
    if not shown:
        print("(nothing matched; pass --all to see every reply)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(DEFAULT_ROOT),
                    help="index root (default runs/puz)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("record", help="append index rows for run dirs")
    p.add_argument("dirs", nargs="+")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("reindex", help="rebuild INDEX.jsonl from run dirs")
    p.set_defaults(fn=cmd_reindex)

    p = sub.add_parser("compare", help="table across recorded runs")
    p.add_argument("--model", default=None)
    p.add_argument("--suite", default=None)
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("leaderboard",
                       help="split the suite into free/dead/discriminating "
                            "positions and rank on the part that ranks")
    p.add_argument("dirs", nargs="+")
    p.set_defaults(fn=cmd_leaderboard)

    p = sub.add_parser("replies", help="read the model's answers back out")
    p.add_argument("dir")
    p.add_argument("--all", action="store_true",
                   help="every reply, not just failures/misses")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(fn=cmd_replies)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
