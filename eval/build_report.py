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
(at least one arm has no usable data or fewer games than its seeds name, or
the holdout audit did not run, so no verdict is earned).

The gates are read from aggregate.py's JSON, not its exit code, which names
only the first gate that failed (AGGREGATE_GATE_EXITS); each of those exits is
a judged arm, never an error.
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
import aggregate  # noqa: E402  its exit codes (the summary itself comes from its --json run)
from run_suite import parse_seed_range  # noqa: E402

PY = sys.executable or "python3"
# aggregate.py's exits that still carry a judged summary: clean, or the first gate that failed. Anything else (1, a
# usage or read error) means no judged summary.
AGGREGATE_GATE_EXITS = (0, aggregate.EXIT_FALLBACK_GATE, aggregate.EXIT_MODEL_IDENTITY_GATE,
                        aggregate.EXIT_HARNESS_DECIDED_GATE)


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
        if agg is None or agg_rc not in AGGREGATE_GATE_EXITS:
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
        exps = agg.get("experiments") or {}
        n_exp = exps.get("n")
        # Student = identity A. Its tool/cost accounting, per aggregate.py.
        acct = agg.get("tool_accounting") or {}
        student_acct = None
        for seat, x in (acct.get("seats") or {}).items():
            if x.get("identity") == "a":
                student_acct = {
                    "seat": seat,
                    "model_rounds": x["model_rounds"]["total"],
                    "rounds_without_reply": x["model_rounds"]["without_reply"],
                    "tool_requests": x["tools"]["requests"],
                    "tool_ok": x["tools"]["ok"],
                    "tool_errors": x["tools"]["errors"],
                    "tool_denied": x["tools"]["denied"],
                    "elapsed_ms_sum": x["elapsed_ms"]["sum"],
                    "reported_prompt_tokens": x["tokens"]["reported_prompt"],
                    "reported_completion_tokens": x["tokens"]["reported_completion"],
                    "rounds_uncosted": x["tokens"]["rounds_uncosted"],
                    "accounting_complete": x["billing"]["complete"],
                    "incomplete_reasons": x["billing"]["incomplete_reasons"],
                }
                break

        entry["result"] = {
            "n_experiments": n_exp,
            "experiment_ids": exps.get("order"),
            "memory_tools_games": acct.get("memory_tools_games"),
            "student_tool_accounting": student_acct,
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
        fg = agg.get("fallback_gate") or {}
        fb_ok, hd_ok = not fg.get("failed"), not fg.get("harness_failed")
        g["fallback_rate"] = {
            "limit": mfr,
            "pass": fb_ok,
            "detail": "within limit" if fb_ok else "FAILED FALLBACK GATE",
        }
        g["harness_decided"] = {
            "pass": hd_ok,
            "detail": ("within limit" if hd_ok
                       else "FAILED HARNESS-DECIDED GATE: the harness made %s of the llm choices"
                            % pct(fg.get("harness_decided_rate"))),
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
        # One arm dir must hold exactly one experiment condition; otherwise the
        # numbers above are refused by aggregate.py and nothing here is citable.
        g["single_experiment"] = {
            "pass": (None if n_exp is None else n_exp == 1),
            "detail": ("aggregate.py predates experiment grouping" if n_exp is None
                       else "one condition" if n_exp == 1
                       else f"{n_exp} CONDITIONS in one dir: "
                            + ", ".join(
                                f"{eid}[{(exps.get('by_id') or {}).get(eid, {}).get('label')}]"
                                for eid in exps.get("order", []))),
        }
        # Resolved model identity: a cited arm whose serving endpoint never named
        # what answered cannot be attributed to a checkpoint (aggregate.py exit 3).
        # Legacy dirs predating the fields report pass=None and are not judged.
        # `failed` is aggregate.py's per-game verdict rolled up over every
        # experiment in the dir, so it is checked FIRST: a legacy or baseline
        # experiment beside a cited-unresolved one never excuses it.
        ident = agg.get("model_identity") or {}
        if ident:
            failed_exps = ident.get("failed_experiments") or []
            g["model_identity"] = {
                "pass": (False if ident.get("failed")
                         else None if ident.get("legacy") or ident.get("vacuous")
                         else True),
                "detail": ("UNATTRIBUTABLE: endpoint never named %s%s"
                           % (", ".join(ident.get("unresolved_fields", [])),
                              (" (experiment(s) %s)" % ", ".join(failed_exps))
                              if failed_exps else "")
                           if ident.get("failed")
                           else "no llm arm — nothing to attribute" if ident.get("vacuous")
                           else "pre-provenance dir — which model played is unrecoverable"
                           if ident.get("legacy")
                           else "attributable: " + ", ".join(
                               "%s=%s" % (k, v[0] if len(v) == 1 else v)
                               for k, v in sorted(ident.get("served", {}).items()))),
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
        # An arm that played fewer games than its seeds name (a budget stop, a crash loop) earns no verdict. A file
        # with no game line is not a played game.
        played = (entry["result"]["games_total"] or 0) - ((agg.get("games") or {}).get("no_game_line") or 0)
        if entry["expected_games"] and played < entry["expected_games"] and entry["status"] == "pass":
            entry["status"] = "short"
            entry["error"] = "%d of %d games" % (played, entry["expected_games"])
        arms.append(entry)

    statuses = [a["status"] for a in arms]
    # check_holdouts.py: exit 0 clean, 1 a violation; anything else (or no JSON) is an audit that did not run.
    holdout_ran = holdouts is not None and ho_rc in (0, 1)
    if any(s in ("missing", "error", "short") for s in statuses) or not holdout_ran:
        verdict = "INCOMPLETE"
        rc = 3
    elif any(s == "fail" for s in statuses) or holdouts.get("violations"):
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
        "holdout_audit_error": None if holdout_ran else (ho_err or "check_holdouts.py exit %s" % ho_rc),
        "arms": arms,
    }

    (out / "suite.json").write_text(json.dumps(suite, indent=2))
    (out / "report.json").write_text(json.dumps(report, indent=2))
    (out / "report.md").write_text(render_md(report, mfr))

    print(f"verdict: {verdict}")
    if report["holdout_audit_error"]:
        print(f"  holdout audit did not run: {report['holdout_audit_error']}")
    for a in arms:
        line = f"  [{a['status']:9s}] {a['arm']}"
        r = a.get("result")
        if r and r.get("student_win_rate") is not None:
            lo, hi = r["student_wilson95"]
            line += (f"  win {pct(r['student_win_rate'])} "
                     f"CI[{pct(lo)}, {pct(hi)}] "
                     f"({r['games_decisive']}/{a['expected_games']} games)")
            if a.get("error"):
                line += f"  ({a['error']})"
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
    L.append(f"- gates: fallback_rate <= {mfr}, harness-decided rate, model identity, defect invariants "
             f"(defect_analyzer exit 0), cited discipline, unmixed provenance, single experiment per arm dir; "
             f"an arm must also play the games its seeds name")
    if report.get("holdout_audit_error"):
        L.append(f"- holdout audit did not run: {report['holdout_audit_error']}")
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
        L.append(f"- status: **{a['status']}**" + (f" ({a['error']})" if a.get("error") else ""))
        L.append(f"- games: {r['games_decisive']} decisive of "
                 f"{r['games_total']} (expected {a['expected_games']})")
        L.append(f"- provenance: discipline={p.get('discipline')} "
                 f"engine={str(p.get('engine_sha', '?'))[:12]} "
                 f"overlay={p.get('overlay_sha')} deck={p.get('deck_sha')}"
                 + (" **MIXED-PROVENANCE**" if r["mixed_provenance"] else ""))
        if r.get("n_experiments") is not None:
            L.append(f"- experiments in dir: {r['n_experiments']} "
                     f"({', '.join(r.get('experiment_ids') or [])})"
                     + (" **MULTIPLE CONDITIONS — numbers refused**"
                        if r["n_experiments"] != 1 else ""))
        ta = r.get("student_tool_accounting")
        if ta:
            L.append(f"- student tool accounting (seat `{ta['seat']}`): "
                     f"model rounds {ta['model_rounds']} "
                     f"(no reply {ta['rounds_without_reply']}), tool requests "
                     f"{ta['tool_requests']} (ok {ta['tool_ok']}, errors "
                     f"{ta['tool_errors']}, denied {ta['tool_denied']}), reported "
                     f"tokens prompt={ta['reported_prompt_tokens']} "
                     f"completion={ta['reported_completion_tokens']}, "
                     f"{ta['rounds_uncosted']} round/attempt(s) uncosted; "
                     f"accounting {'complete' if ta['accounting_complete'] else 'INCOMPLETE'}"
                     + (f" ({'; '.join(ta['incomplete_reasons'])})"
                        if ta["incomplete_reasons"] else "")
                     + f"; memory-tool games: {r.get('memory_tools_games')}")
            L.append("  Token counts are the backend's last-attempt reports, not billing.")
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
    L.append("Citable only if verdict is PASS. INCOMPLETE means an arm has no "
             "usable data or fewer games than its seeds name, or the holdout "
             "audit did not run; FAIL means a gate broke. Numbers from "
             "this report may not be quoted alongside arms added after the "
             "fact — rerun the whole suite per checkpoint.")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
