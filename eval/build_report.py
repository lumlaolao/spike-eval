#!/usr/bin/env python3
"""Build a frozen eval report for a suite.

Consumes the per-arm output dirs produced by the run_batch.py commands that
run_suite.py prints, and emits a self-contained report folder:

    <out>/suite.json        copy of the suite config (as validated)
    <out>/report.json       machine-readable results + gate verdicts
    <out>/report.md         human-readable summary
    <out>/defects-<arm>.json/.txt   defect_analyzer evidence per arm

Statistics are NOT recomputed here: aggregate.py --json is the single source
for win rates/CIs, defect_analyzer.py for harness invariants,
check_holdouts.py for contamination. This script orchestrates and judges.

Usage:
    python3 eval/build_report.py eval/suites/stage3-v1.json \
        --student openrouter:default_model \
        [--arm-dir forge-ai-burn=runs/other-dir] [--out DIR] [--json]

Arm dirs default to runs/<suite>-<arm> (what run_suite.py prints).
Exit 0 = PASS, 1 = FAIL (a gate failed), 2 = usage error, 3 = INCOMPLETE
(at least one arm has no usable data, so no verdict is earned).
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))
from run_suite import parse_seed_range  # noqa: E402

PY = sys.executable or "python3"


def run_json(cmd):
    """Run cmd, parse stdout as JSON. Returns (data, returncode, stderr)."""
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    try:
        return json.loads(p.stdout), p.returncode, p.stderr.strip()
    except json.JSONDecodeError:
        return None, p.returncode, (p.stderr.strip() or
                                    f"non-JSON stdout: {p.stdout[:200]}")


def pct(x):
    return f"{100 * x:.1f}%" if isinstance(x, (int, float)) else "n/a"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suite", help="path to a suite JSON file")
    ap.add_argument("--student", default="openrouter:default_model",
                    help="student model spec (recorded in the report header)")
    ap.add_argument("--arm-dir", action="append", default=[],
                    metavar="ARM=PATH",
                    help="override an arm's output dir (repeatable)")
    ap.add_argument("--out", default=None,
                    help="report dir (default runs/reports/<suite>-<UTCts>)")
    ap.add_argument("--json", action="store_true",
                    help="print report.json to stdout as well")
    args = ap.parse_args()

    try:
        suite = json.loads(Path(args.suite).read_text())
    except Exception as e:
        print(f"error: cannot load suite {args.suite}: {e}", file=sys.stderr)
        return 2
    name = suite.get("suite", "<unnamed>")
    gates = suite.get("gates", {})
    mfr = gates.get("max_fallback_rate", 0.10)

    overrides = {}
    for kv in args.arm_dir:
        if "=" not in kv:
            print(f"error: --arm-dir wants ARM=PATH, got {kv!r}",
                  file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        overrides[k] = v

    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else (
        REPO / "runs" / "reports" / f"{name}-{ts}")
    out.mkdir(parents=True, exist_ok=True)

    # holdout audit (suite-level evidence; exit 1 there = contamination)
    holdouts, ho_rc, ho_err = run_json([PY, "eval/check_holdouts.py", "--json"])

    arms = []
    for arm in suite.get("arms", []):
        aname = arm.get("name", "<unnamed>")
        if not arm.get("enabled", False):
            continue
        entry = {"arm": aname, "role": arm.get("role"),
                 "deck": arm.get("deck"), "seeds": arm.get("seeds"),
                 "note": arm.get("note"), "status": "missing",
                 "gates": {}, "result": None}
        rng = parse_seed_range(arm.get("seeds", ""))
        entry["expected_games"] = 2 * (rng[1] - rng[0] + 1) if rng else None

        run_dir = Path(overrides.get(aname, f"runs/{name}-{aname}"))
        entry["dir"] = str(run_dir)
        if not (REPO / run_dir).is_dir():
            arms.append(entry)
            continue

        agg, agg_rc, agg_err = run_json(
            [PY, "eval/aggregate.py", str(run_dir), "--json",
             "--max-fallback-rate", str(mfr)])
        if agg is None or agg_rc not in (0, 2):
            entry["status"] = "error"
            entry["error"] = f"aggregate.py rc={agg_rc}: {agg_err}"
            arms.append(entry)
            continue

        def_rc = None
        if gates.get("defect_invariants", True):
            dp = subprocess.run(
                [PY, "eval/defect_analyzer.py", str(run_dir),
                 "--json", str(out / f"defects-{aname}.json"),
                 "--summary", str(out / f"defects-{aname}.txt")],
                capture_output=True, text=True, cwd=REPO)
            def_rc = dp.returncode
            if def_rc == 2:
                entry["status"] = "error"
                entry["error"] = ("defect_analyzer.py rc=2: "
                                  + dp.stderr.strip()[:200])
                arms.append(entry)
                continue

        paired = agg.get("paired") or {}
        focal = paired.get("focal_arm")
        focal_stats = (agg.get("arms") or {}).get(focal) or {}
        prov = agg.get("provenance") or {}
        prov_fields = prov.get("fields") or {}
        disciplines = prov_fields.get("discipline", [])

        entry["result"] = {
            "games_total": agg.get("games", {}).get("total"),
            "games_decisive": agg.get("games", {}).get("decisive"),
            "student_arm": focal,
            "student_win_rate": focal_stats.get("win_rate"),
            "student_wilson95": focal_stats.get("wilson95"),
            "paired_delta": paired.get("delta"),
            "paired_delta_ci95": paired.get("delta_ci95"),
            "n_pairs_used": paired.get("n_pairs_used"),
            "seat1_win_rate": (agg.get("games", {}).get("seat1_wins"),
                               agg.get("games", {}).get("decisive")),
            "provenance": {k: v for k, v in prov_fields.items()
                           if k in ("discipline", "engine_sha", "overlay_sha",
                                    "deck_sha", "mulligan_heuristic",
                                    "skip_heuristics")},
            "mixed_provenance": prov.get("mixed"),
        }

        g = entry["gates"]
        g["fallback_rate"] = {
            "limit": mfr,
            "pass": agg_rc == 0,
            "detail": ("within limit" if agg_rc == 0
                       else "FAILED FALLBACK GATE (aggregate.py exit 2)"),
        }
        if def_rc is not None:
            g["defect_invariants"] = {
                "pass": def_rc == 0,
                "detail": ("clean" if def_rc == 0
                           else f"defect_analyzer exit {def_rc} "
                                f"(see defects-{aname}.txt)"),
            }
        g["cited_discipline"] = {
            "pass": disciplines == ["cited"],
            "detail": f"discipline={disciplines}",
        }
        g["provenance_unmixed"] = {
            "pass": prov.get("mixed") is False,
            "detail": ("single provenance" if prov.get("mixed") is False
                       else f"MIXED: {prov.get('mixed_fields')}"),
        }
        # Resolved model identity: a cited arm whose serving endpoint never named
        # what answered cannot be attributed to a checkpoint (aggregate.py exit 3).
        # Legacy dirs predating the fields report pass=None and are not judged.
        ident = agg.get("model_identity") or {}
        if ident:
            g["model_identity"] = {
                "pass": (None if ident.get("legacy") or ident.get("vacuous")
                         else not ident.get("failed")),
                "detail": ("no llm arm — nothing to attribute" if ident.get("vacuous")
                           else "pre-provenance dir — which model played is unrecoverable"
                           if ident.get("legacy")
                           else "attributable: " + ", ".join(
                               "%s=%s" % (k, v[0] if len(v) == 1 else v)
                               for k, v in sorted(ident.get("served", {}).items()))
                           if not ident.get("failed")
                           else "UNATTRIBUTABLE: endpoint never named %s"
                                % ", ".join(ident.get("unresolved_fields", []))),
            }

        if gates.get("min_autonomy_pct") is not None:
            g["autonomy"] = {
                "pass": None,
                "detail": "UNENFORCEABLE: autonomy field not on game lines "
                          "yet (provenance workstream item 3)",
            }

        # pass=None means "not judged" (unenforceable or not applicable), which must
        # not read as failure — `all()` would treat None as falsy and fail the arm on
        # a gate whose own detail says it cannot be evaluated.
        entry["status"] = ("pass" if all(x["pass"] is not False for x in g.values())
                           else "fail")
        arms.append(entry)

    statuses = [a["status"] for a in arms]
    if any(s in ("missing", "error") for s in statuses):
        verdict = "INCOMPLETE"
        rc = 3
    elif any(s == "fail" for s in statuses):
        verdict = "FAIL"
        rc = 1
    elif holdouts is not None and holdouts.get("violations"):
        verdict = "FAIL"
        rc = 1
    else:
        verdict = "PASS"
        rc = 0

    report = {
        "suite": name,
        "suite_version": suite.get("version"),
        "generated_utc": ts,
        "student": args.student,
        "verdict": verdict,
        "holdout_violations": (holdouts or {}).get("violations"),
        "holdout_audit_error": ho_err if holdouts is None else None,
        "arms": arms,
    }

    (out / "suite.json").write_text(json.dumps(suite, indent=2))
    (out / "report.json").write_text(json.dumps(report, indent=2))
    (out / "report.md").write_text(render_md(report, mfr))

    print(f"verdict: {verdict}")
    for a in arms:
        line = f"  [{a['status']:9s}] {a['arm']}"
        r = a.get("result")
        if r and r.get("student_win_rate") is not None:
            lo, hi = r["student_wilson95"]
            line += (f"  win {pct(r['student_win_rate'])} "
                     f"CI[{pct(lo)}, {pct(hi)}] "
                     f"({r['games_decisive']}/{a['expected_games']} games)")
        elif a["status"] == "missing":
            line += f"  (no dir: {a['dir']})"
        elif a.get("error"):
            line += f"  ({a['error']})"
        print(line)
    print(f"report: {out}")
    if args.json:
        print(json.dumps(report, indent=2))
    return rc


def render_md(report, mfr):
    L = []
    L.append(f"# Eval report: {report['suite']} — {report['verdict']}")
    L.append("")
    L.append(f"- generated (UTC): {report['generated_utc']}")
    L.append(f"- student (identity A): `{report['student']}`")
    L.append(f"- gates: fallback_rate <= {mfr}, defect invariants, "
             f"cited discipline, unmixed provenance")
    L.append("")
    L.append("| arm | role | deck | win rate | Wilson 95% CI | paired Δ CI | "
             "games | gates |")
    L.append("|---|---|---|---|---|---|---|---|")
    for a in report["arms"]:
        r = a.get("result")
        if r and r.get("student_win_rate") is not None:
            lo, hi = r["student_wilson95"]
            d = r.get("paired_delta_ci95")
            dci = (f"[{pct(d[0])}, {pct(d[1])}]"
                   if isinstance(d, list) else "n/a")
            gstr = " ".join("✓" if v["pass"] else ("✗" if v["pass"] is False
                                                   else "?")
                            for v in a["gates"].values())
            L.append(f"| {a['arm']} | {a['role']} | {a['deck']} "
                     f"| {pct(r['student_win_rate'])} "
                     f"| [{pct(lo)}, {pct(hi)}] | {dci} "
                     f"| {r['games_decisive']}/{a['expected_games']} "
                     f"| {gstr} |")
        else:
            L.append(f"| {a['arm']} | {a['role']} | {a['deck']} "
                     f"| — | — | — | 0/{a['expected_games']} "
                     f"| {a['status']} |")
    L.append("")
    for a in report["arms"]:
        r = a.get("result")
        L.append(f"## {a['arm']} ({a['role']}, deck {a['deck']})")
        if a.get("note"):
            L.append(f"_{a['note']}_")
        if not r:
            L.append(f"status: **{a['status']}** ({a.get('error') or a['dir']})")
            L.append("")
            continue
        p = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v)
             for k, v in r["provenance"].items()}
        L.append(f"- games: {r['games_decisive']} decisive of "
                 f"{r['games_total']} (expected {a['expected_games']})")
        L.append(f"- provenance: discipline={p.get('discipline')} "
                 f"engine={str(p.get('engine_sha', '?'))[:12]} "
                 f"overlay={p.get('overlay_sha')} deck={p.get('deck_sha')}"
                 + (" **MIXED-PROVENANCE**" if r["mixed_provenance"] else ""))
        for gname, gv in a["gates"].items():
            mark = ("PASS" if gv["pass"] else
                    ("FAIL" if gv["pass"] is False else "N/A"))
            L.append(f"- gate {gname}: **{mark}** — {gv['detail']}")
        L.append("")
    if report.get("holdout_violations"):
        L.append("## HOLDOUT VIOLATIONS")
        for slug, ds in report["holdout_violations"]:
            L.append(f"- held-out deck `{slug}` found in dataset `{ds}`")
        L.append("")
    L.append("Citable only if verdict is PASS. INCOMPLETE means at least one "
             "arm has no usable data; FAIL means a gate broke. Numbers from "
             "this report may not be quoted alongside arms added after the "
             "fact — rerun the whole suite per checkpoint.")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
