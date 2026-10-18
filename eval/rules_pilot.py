#!/usr/bin/env python3
"""The rules-puzzle pilot (eval/BENCHMARK-V1.md, "turn the 996 rules questions into puzzles").

One directory of authored pairs, `eval/puzzles/rules-pilot/`:
  <id>.pzl            the Forge puzzle (a [state] board + an [acceptance] section)
  <id>.source.json     the question it tests: id, rule, area, the judge's answer

A gate run drives each puzzle through the engine, never through text:
  prove    the search prover (CONTROLLER_A=search CONTROLLER_B=search) against the acceptance
           state, recording opponent replies beaten, the acceptance verdict and (after a proof)
           the uniqueness hunt
  probe    one misreading prefix per probe, which must NOT reach the acceptance state
  baselines the trivial arms (random, pass, first) measured under the acceptance state

    python3 eval/rules_pilot.py build  [--out eval/puzzles/rules-pilot/manifest.json]
    python3 eval/rules_pilot.py probe  <id> --line "Seat:bound:value ..."  [--manifest M]
    python3 eval/rules_pilot.py gate   [--ids a,b] [--budget 1500] [--rollouts 200] [-j 4] --write M

The manifest is built with eval/puzzle_format.py's shapes (schema puzzle.v1, additive fields:
source.kind=authored, source.question_id/rule/areas, proof, probes, uniqueness) and is validated
by `puzzle_format.py check`. Nothing is frozen here (BENCHMARK-V1.md: the pilot decides scaling
first).
"""

import argparse
import datetime
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_format as pf  # noqa: E402
import puzzle_baselines as pb  # noqa: E402

REPO = pf.REPO
PILOT_DIR = REPO / "eval/puzzles/rules-pilot"
DEFAULT_MANIFEST = PILOT_DIR / "manifest.json"


# ------------------------------------------------------------------ source notes

def load_source(path):
    """The per-puzzle question note; unknown keys are the author's and are carried through."""
    src = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("question_id", "question", "answer", "rule", "areas"):
        if not src.get(key):
            raise SystemExit("%s: missing %r" % (path, key))
    return src


def source_for(pzl_path):
    """The per-puzzle note, <id>.source.json beside the .pzl."""
    return load_source(pzl_path.parent / (pzl_path.stem + ".source.json"))


# ------------------------------------------------------------------ drivers

def run_search(build, pzl_path, env_extra, out_dir):
    """One LlmSpikePuzzle invocation with CONTROLLER_A=search; returns its result rows."""
    env = {"PUZZLE": str(Path(pzl_path).resolve()), "CONTROLLER_A": "search", "CONTROLLER_B": "search",
           "PZL_SEEDS": "1", "GAME_CAP_MS": "120000"}
    env.update(env_extra or {})
    if (Path(out_dir) / "puzzle-results.jsonl").exists():
        (Path(out_dir) / "puzzle-results.jsonl").unlink()
    build.run(env, out_dir, timeout_s=3600)
    return pb.read_results(out_dir)


def prove_one(build, pzl_path, budget, out_root):
    rows = run_search(build, pzl_path, {"PZL_SEARCH_BUDGET": str(budget)}, Path(out_root) / "prove")
    return rows[-1] if rows else {}


def probe_one(build, pzl_path, line, out_root):
    safe = "".join(ch if ch.isalnum() else "-" for ch in line)[:60]
    rows = run_search(build, pzl_path, {"PZL_PROBE": line}, Path(out_root) / ("probe-" + safe))
    return rows[-1] if rows else {}


def baselines_one(build, pzl_path, rollouts, out_root):
    out = Path(out_root) / "baselines"
    env = {"PUZZLE": str(Path(pzl_path).resolve()), "CONTROLLER_B": "ai", "GAME_CAP_MS": "120000"}
    base = {}
    arms = {"random": ("random", "%d..%d" % (1, rollouts)),
            "pass": ("pass", "1..4"), "first": ("first", "1..4")}
    for arm, (ctl, seeds) in arms.items():
        env2 = dict(env, CONTROLLER_A=ctl, PZL_SEEDS=seeds)
        arm_dir = out / arm
        if (arm_dir / "puzzle-results.jsonl").exists():
            (arm_dir / "puzzle-results.jsonl").unlink()
        build.run(env2, arm_dir)
        rows = pb.read_results(arm_dir)
        n = len(rows)
        solved = sum(1 for r in rows if pb.accepted_row(r))
        base[arm] = {"solved": solved, "n": n, "rate": (solved / n) if n else None, "seeds": seeds}
    return base


# ------------------------------------------------------------------ commands

def cmd_build(a):
    records = []
    for pzl in sorted(PILOT_DIR.glob("*.pzl")):
        src = source_for(pzl)
        text = pzl.read_text(encoding="utf-8")
        goal, turns, start, phase = pf.pzl_goal(text)
        pid = "rules-" + src.get("puzzle_id", pzl.stem)
        rec = {
            "schema": pf.PUZZLE_SCHEMA,
            "id": pid,
            "split": pf.split_for(pid),
            "status": src.get("status", "candidate"),
            "goal": {"engine_goal": goal, "turns": turns},
            "source": {"kind": "authored", "question_id": src["question_id"], "question": src["question"],
                       "judge_answer": src["answer"], "rule": src["rule"], "areas": src["areas"]},
            "pzl": str(pzl.relative_to(REPO)),
            "pzl_sha256": pf.sha256_file(pzl),
        }
        if pf.split_for(pid) != rec["split"]:
            print("NOTE: %s split recompute differs" % pid)
        records.append(rec)
    m = {"schema": pf.SUITE_SCHEMA, "suite": "rules-pilot",
         "version": "1", "engine_sha": (REPO / "FORGE_COMMIT").read_text().strip() if (REPO / "FORGE_COMMIT").is_file() else "",
         "canary": pf.CANARY, "frozen": False, "puzzles": records}
    out = Path(a.out) if a.out else DEFAULT_MANIFEST
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
    print("built %d record(s) -> %s" % (len(records), out))
    problems = pf.validate_suite(m, check_files=True)
    for p in problems:
        print("PROBLEM", p)
    return 1 if problems else 0


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_probe(a):
    build = pb.Build(a.build, a.forge)
    m = load_manifest(a.manifest or DEFAULT_MANIFEST)
    rec = next((r for r in m["puzzles"] if r["id"] == a.id), None)
    if rec is None:
        raise SystemExit("no puzzle %s in %s" % (a.id, a.manifest or DEFAULT_MANIFEST))
    pzl = pf.pzl_path(rec)
    row = probe_one(build, pzl, a.line, REPO / "runs/puzzles/rules-pilot")
    got = {"probe_line": a.line, "status": row.get("status"),
           "acceptance": row.get("acceptance"), "solved": row.get("solved"),
           "actions": row.get("actions"), "log_rows": 1}
    rec.setdefault("probes", [])
    if rec["probes"] and rec["probes"][-1]["probe_line"] == a.line:
        rec["probes"][-1] = got
    else:
        rec["probes"].append(got)
    save_manifest(m, a.manifest or DEFAULT_MANIFEST)
    print(json.dumps(got, indent=2))
    return 0


def cmd_gate(a):
    build = pb.Build(a.build, a.forge)
    m = load_manifest(a.manifest or DEFAULT_MANIFEST)
    ids = set(a.ids.split(",")) if a.ids else None
    if m.get("frozen"):
        raise SystemExit("manifest frozen; the pilot does not freeze a suite (BENCHMARK-V1.md)")
    out_root = REPO / "runs/puzzles/rules-pilot"
    stamp = datetime.date.today().isoformat()
    engine = (REPO / "FORGE_COMMIT").read_text().strip() if (REPO / "FORGE_COMMIT").is_file() else "unknown"

    def work(rec):
        if ids is None or rec["id"] in ids:
            pzl = pf.pzl_path(rec)
            row = prove_one(build, pzl, a.budget, out_root)
            rec["proof"] = pb.proof_from_row(row, "search", engine, stamp)
            if row:
                rec["proof"]["acceptance"] = row.get("acceptance")
                rec["proof"]["acceptance_conditions"] = row.get("acceptance_conditions")
                for k in ("unique", "unique_replays", "unique_budget", "second_line"):
                    if row.get(k) not in (None, ""):
                        rec["proof"][k] = row[k]
        return rec

    from concurrent.futures import ThreadPoolExecutor, as_completed
    done = {}

    def save():
        mm = dict(m, puzzles=[done.get(x["id"], x) for x in m["puzzles"]])
        save_manifest(mm, a.manifest or DEFAULT_MANIFEST)
        return mm

    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        for fut in as_completed([ex.submit(work, r) for r in m["puzzles"]]):
            r = fut.result()
            done[r["id"]] = r
            save()
    m = save()

    # Baselines: gate 4. Random must stay under 5% on the acceptance state (this pilot's bound;
    # the frozen-suite bound is 20%).
    def workb(rec):
        if ids is None or rec["id"] in ids:
            pzl = pf.pzl_path(rec)
            rec["baselines"] = baselines_one(build, pzl, a.rollouts, out_root)
        return rec

    done = {}
    with ThreadPoolExecutor(max(1, a.jobs)) as ex2:
        for fut in as_completed([ex2.submit(workb, r) for r in m["puzzles"]]):
            r = fut.result()
            done[r["id"]] = r
            save()
    m = save()

    for r in m["puzzles"]:
        if ids is not None and r["id"] not in ids:
            continue
        p = r.get("proof") or {}
        b = r.get("baselines") or {}
        rb = (b.get("random") or {}).get("rate")
        print("%-42s proof=%-9s unique=%s opp_replies=%s random=%.3f pass=%s probe=%s" % (
            r["id"], p.get("status"), p.get("unique"), p.get("opponent_branches_beaten"),
            rb if rb is not None else -1,
            (b.get("pass") or {}).get("rate"), ",".join(x["acceptance"] or "err" for x in r.get("probes", []))))
    return 0


def save_manifest(m, path):
    Path(path).write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", default=None, help="isolated build dir (default: newest acceptance stamp)")
    ap.add_argument("--forge", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default=None)
    pr = sub.add_parser("probe")
    pr.add_argument("id")
    pr.add_argument("--line", required=True)
    pr.add_argument("--manifest", default=None)
    g = sub.add_parser("gate")
    g.add_argument("--ids", default=None)
    g.add_argument("--budget", type=int, default=1500)
    g.add_argument("--rollouts", type=int, default=200)
    g.add_argument("-j", "--jobs", type=int, default=4)
    g.add_argument("--manifest", default=None)
    g.add_argument("--write", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        return cmd_build(a)
    if a.cmd == "probe":
        return cmd_probe(a)
    return cmd_gate(a)


if __name__ == "__main__":
    sys.exit(main())
