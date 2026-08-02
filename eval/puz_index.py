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
#: rendered; k and strictness change what pass_1 even means. max_tokens is a
#: measurement knob, not a model property: kimi-k3 scored 0.600 at 4k tokens
#: (33% interface failures) and 0.720 at 16k — the same model, two different
#: measurements, and they indexed as the same thing until this key existed.
#: RESOLUTION for legacy rows: a MISSING max_tokens is the explicit sentinel
#: 'unrecorded' (None) — it refuses to rank against a RECORDED value but
#: ranks fine against other unrecorded rows, so pre-existing artifact sets
#: (e.g. runs/puz/tournament-8) stay internally rankable while a 4k and a 16k
#: run can never again silently share a table.
COMPARABILITY_KEYS = ("suite_sha256", "contract_sha", "k", "strictness",
                      "max_tokens")


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
        "max_tokens": s.get("max_tokens"),
        "latency_ms_p50": _get(s, "solve_dynamics", "latency_ms_p50"),
        "pass_1_within_cap": _get(s, "solve_dynamics", "pass_1_within_cap"),
        "time_cap_s": _get(s, "solve_dynamics", "time_cap_s"),
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


def position_solve_rates(run_dirs, pick_latest=False):
    """-> ({model: {position_id: mean solved over k}}, position order).

    Read from results.jsonl, not summary.json: the per-position detail is the
    only thing that can tell a suite's FREE positions from its discriminating
    ones, and that distinction is what decides whether a leaderboard means
    anything.

    A DUPLICATE model name across run_dirs is an ERROR, not an overwrite:
    the silent `per[model] = ...` this replaces scored kimi-k3 from a
    SUPERSEDED 4k-token run (0.600, 33% iface) instead of the 16k rerun
    (0.720, 7%) when the caller globbed both — two contradictory published
    numbers from one command. pick_latest=True is the explicit opt-in: keep
    the run with the newest generated_utc (falling back to run-dir mtime) and
    SAY which one won.
    """
    per, order = {}, []
    model_src = {}   # model -> (run_dir, sort_key) that produced its rows
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
            if r.get("t") == "summary":
                continue
            pid = r.get("position_id")
            if not pid:
                continue
            model = model or r.get("model")
            acc.setdefault(pid, []).append(1.0 if r.get("solved") else 0.0)
            if pid not in seen:
                seen.add(pid)
                order.append(pid)
        if not model:
            continue
        sort_key = _run_sort_key(d)
        if model in per:
            prev_dir, prev_key = model_src[model]
            if not pick_latest:
                raise DuplicateModelError(
                    "model %r appears in BOTH %s and %s — refusing to pick "
                    "silently (the overwrite this replaces published a "
                    "superseded score). Re-run with --pick-latest to keep "
                    "the newest run, or pass only the run_dirs you mean."
                    % (model, prev_dir, d))
            if sort_key <= prev_key:
                print("duplicate model %r: keeping %s (newer), ignoring %s"
                      % (model, prev_dir, d), file=sys.stderr)
                continue
            print("duplicate model %r: keeping %s (newer), ignoring %s"
                  % (model, d, prev_dir), file=sys.stderr)
        per[model] = dict((k, sum(v) / float(len(v)))
                          for k, v in acc.items() if v)
        model_src[model] = (str(d), sort_key)
    return per, order


class DuplicateModelError(RuntimeError):
    pass


def _run_sort_key(run_dir):
    """generated_utc from summary.json when readable, else dir mtime — a
    total order for --pick-latest that survives missing summaries."""
    try:
        s = json.loads((Path(run_dir) / "summary.json")
                       .read_text(encoding="utf-8"))
        utc = s.get("generated_utc")
        if utc:
            return (1, str(utc))
    except (OSError, ValueError):
        pass
    try:
        return (0, "%020.6f" % Path(run_dir).stat().st_mtime)
    except OSError:
        return (0, "")


def exact_mcnemar_p(b, c):
    """Two-sided exact McNemar / sign test: b positions where A beat B, c
    where B beat A (ties dropped). p = P(X <= min(b,c) or X >= max(b,c)) for
    X ~ Binomial(b+c, 0.5). Stdlib only — sum of binomial tail terms."""
    n = b + c
    if n == 0:
        return 1.0
    from math import comb
    lo = min(b, c)
    tail = sum(comb(n, i) for i in range(0, lo + 1))
    p = 2.0 * tail / (2.0 ** n)
    return min(1.0, p)


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
    try:
        per, order = position_solve_rates(
            dirs, pick_latest=getattr(args, "pick_latest", False))
    except DuplicateModelError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 4
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
        # Solve dynamics (2026-08-02 directive): HOW each model earned its
        # score — first-try-within-cap and wall-clock percentiles, from each
        # run's summary.json when present.
        dyn_rows = []
        for d in dirs:
            try:
                sm = json.loads((Path(d) / "summary.json").read_text())
            except (OSError, ValueError):
                continue
            sd = sm.get("solve_dynamics") or {}
            if sd.get("n_first_try_samples"):
                dyn_rows.append((sm.get("model") or "?", sd))
        if dyn_rows:
            print()
            print("  %-30s %10s %8s %8s %9s" % (
                "solve dynamics", "pass1<=%.0fs" % (dyn_rows[0][1].get("time_cap_s") or 60),
                "p50(s)", "p90(s)", "attempts"))
            for m, sd in sorted(dyn_rows, key=lambda t: -(t[1].get("pass_1_within_cap") or 0)):
                print("  %-30s %10s %8s %8s %9s" % (
                    m[:30],
                    ("%.3f" % sd["pass_1_within_cap"]) if sd.get("pass_1_within_cap") is not None else "-",
                    ("%.1f" % (sd["latency_ms_p50"] / 1000.0)) if sd.get("latency_ms_p50") else "-",
                    ("%.1f" % (sd["latency_ms_p90"] / 1000.0)) if sd.get("latency_ms_p90") else "-",
                    ("%.2f" % sd["attempts_mean"]) if sd.get("attempts_mean") else "-"))
        # PAIRED test, because every model answered the IDENTICAL positions:
        # the old independent-samples check (1.96*se_max*sqrt(2)) threw away
        # the pairing and could neither confirm nor deny an ordering. Exact
        # McNemar / sign test per pair: b = positions where A beat B, c = the
        # reverse, ties dropped, two-sided exact binomial.
        import itertools
        pair_stats = []
        for ma, mb in itertools.combinations(models, 2):
            shared = [p for p in order if p in per[ma] and p in per[mb]]
            b = sum(1 for p in shared if per[ma][p] > per[mb][p])
            c = sum(1 for p in shared if per[ma][p] < per[mb][p])
            pair_stats.append((exact_mcnemar_p(b, c), ma, mb, b, c))
        if pair_stats:
            n_sig = sum(1 for p, _a, _b, _bb, _cc in pair_stats if p < 0.05)
            best_pair = min(pair_stats)
            print()
            if n_sig == 0:
                print("  ORDERING NOT SIGNIFICANT: exact McNemar over shared "
                      "positions finds 0 of %d\n  model pairs significant at "
                      "p<0.05 (best pair %s vs %s: b=%d c=%d p=%.3f).\n"
                      "  The cause is suite DIFFICULTY, not sample size — "
                      "most positions are solved by\n  every model, so more "
                      "positions from the same distribution will not "
                      "separate\n  these models. Report the scores, never the "
                      "rank."
                      % (len(pair_stats), best_pair[1], best_pair[2],
                         best_pair[3], best_pair[4], best_pair[0]))
            else:
                print("  paired significance (exact McNemar, shared "
                      "positions): %d of %d pairs\n  significant at p<0.05. "
                      "Rank claims are supported ONLY for those pairs:"
                      % (n_sig, len(pair_stats)))
                for p, ma, mb, b, c in sorted(pair_stats):
                    if p < 0.05:
                        print("    %s > %s  (b=%d c=%d p=%.4f)"
                              % ((ma if b > c else mb),
                                 (mb if b > c else ma), b, c, p))
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

    p_lb = sub.add_parser("leaderboard",
                          help="split the suite into free/dead/discriminating "
                               "positions and rank on the part that ranks")
    p_lb.add_argument("dirs", nargs="+")
    p_lb.add_argument("--pick-latest", action="store_true",
                      help="when one model appears in several run_dirs, keep "
                           "the newest run (by generated_utc) and print which "
                           "won — without this, duplicates are an ERROR")
    p_lb.set_defaults(fn=cmd_leaderboard)

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
