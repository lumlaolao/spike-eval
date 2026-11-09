#!/usr/bin/env python3
"""Pair the zero-model arms' games on original boards with the same seeds on their twin boards.

Reads two `puzzle_baselines.py run --baselines-only --out-dir` folders (one per side, `<dir>/<puzzle>/<arm>/`
holding `puzzle-results.jsonl` and one `game-<seed>-*.jsonl` per game), the twin manifest (`puzzle_twin.py make`) and
the two `puzzle_baselines.py prove` logs, and writes one JSON line per (puzzle, arm) and one per proof, with ids,
hashes and counts only: no board text, no card name, no twin name.

Per game it compares the result row (solved, turns, win condition, loss reason, solver actions, draws, status) and the
decision trace (turn, phase, type, option count, choice, and each option's text up to its description, with the
original's card names mapped to the twin names). Per arm it also compares the `[ROUTER]` lines of `jvm.log` the
same way. A difference is reported as the seeds that differ and the first differing decision's index, never its text.

It does not play a game, so it cannot say a twin is faithful for an arm it was not given, and a trace that matches
under the name map says nothing about the model's reading of a name. Stdlib only.

  python3 compare_twins.py --orig <dir> --twin <dir> --manifest <twin-manifest.json> \
      --prove-orig <log> --prove-twin <log> --out paired-rows.jsonl
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

ARMS = ("random", "pass", "first", "floor", "ai")
KEY = ("solved", "turns", "win_condition", "solver_loss_reason", "solver_actions", "draws", "status")


def games(folder):
    out = {}
    for f in glob.glob(os.path.join(folder, "game-*.jsonl")):
        seed = int(re.match(r"game-(\d+)-", os.path.basename(f)).group(1))
        with open(f, encoding="utf-8") as fh:
            out[seed] = [json.loads(line) for line in fh if line.strip()]
    return out


def results(folder):
    with open(os.path.join(folder, "puzzle-results.jsonl"), encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return {r["seed"]: r for r in rows}


def router(folder):
    with open(os.path.join(folder, "jvm.log"), encoding="utf-8", errors="replace") as fh:
        return [line.rstrip() for line in fh if "[ROUTER]" in line]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("orig", "twin", "manifest", "prove-orig", "prove-twin", "out"):
        ap.add_argument("--" + name, required=True)
    a = ap.parse_args(argv)
    manifest = json.load(open(a.manifest, encoding="utf-8"))
    cardmap = {c["original"]: c["twin"] for c in manifest["cards"]}
    pattern = re.compile("|".join(re.escape(k) for k in sorted(cardmap, key=len, reverse=True)))

    def mapped(text):
        return pattern.sub(lambda m: cardmap[m.group(0)], text)

    def head(text):  # an option up to its description: the seat cuts a long description by length
        return text.split(" [")[0]

    def trace(game, twin_names):
        out = []
        for e in game:
            if e.get("t") == "decision":
                opts = tuple(head(mapped(o) if twin_names else o) for o in (e.get("options") or []))
                out.append((e["turn"], e["phase"], e["type"], e["n_options"], e["choice"], opts))
        return out

    rows = []
    for p in manifest["puzzles"]:
        for arm in ARMS:
            do, dt = os.path.join(a.orig, p["id"], arm), os.path.join(a.twin, p["twin_id"], arm)
            ro, rt, go, gt = results(do), results(dt), games(do), games(dt)
            row_diff, trace_diff, first = [], [], None
            for seed in sorted(set(ro) | set(rt)):
                x, y = ro.get(seed), rt.get(seed)
                if x is None or y is None or [x.get(k) for k in KEY] != [y.get(k) for k in KEY]:
                    row_diff.append(seed)
                ta, tb = trace(go.get(seed, []), True), trace(gt.get(seed, []), False)
                if ta != tb:
                    trace_diff.append(seed)
                    if first is None:
                        first = next((i for i, (u, v) in enumerate(zip(ta, tb)) if u != v), min(len(ta), len(tb)))

            def digest(rs):
                return hashlib.sha256(json.dumps([[s] + [rs[s].get(k) for k in KEY] for s in sorted(rs)]).encode()
                                      ).hexdigest()

            rows.append({
                "kind": "arm", "id": p["id"], "twin_id": p["twin_id"], "arm": arm, "games": len(ro),
                "twin_games": len(rt), "solved": sum(1 for r in ro.values() if r["solved"]),
                "twin_solved": sum(1 for r in rt.values() if r["solved"]),
                "decisions": sum(len(trace(g, False)) for g in go.values()),
                "twin_decisions": sum(len(trace(g, False)) for g in gt.values()),
                "results_sha256": digest(ro), "twin_results_sha256": digest(rt),
                "result_diff_seeds": row_diff, "trace_diff_seeds": trace_diff, "first_diff_decision": first,
                "router_lines": len(router(do)), "twin_router_lines": len(router(dt)),
                "router_identical": [head(mapped(x)) for x in router(do)] == [head(x) for x in router(dt)]})

    def proofs(path):
        out = {}
        for line in open(path, encoding="utf-8"):
            f = line.split()
            if len(f) > 3 and f[2].startswith("rollouts="):
                out[f[0]] = [f[1]] + [x.split("=", 1)[1] for x in f[2:6]]
        return out

    po, pt = proofs(a.prove_orig), proofs(a.prove_twin)
    for p in manifest["puzzles"]:
        rows.append({"kind": "proof", "id": p["id"], "twin_id": p["twin_id"],
                     "status_rollouts_points_beaten_actions": po.get(p["id"]),
                     "twin": pt.get(p["twin_id"]), "identical": po.get(p["id"]) == pt.get(p["twin_id"])})
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    arm_rows = [r for r in rows if r["kind"] == "arm"]
    bad = [r for r in arm_rows if r["result_diff_seeds"] or r["trace_diff_seeds"] or not r["router_identical"]
           or r["solved"] != r["twin_solved"]]
    print("%d puzzle-arms, %d games per side, %d with a difference; proofs identical %d of %d" % (
        len(arm_rows), sum(r["games"] for r in arm_rows), len(bad),
        sum(1 for r in rows if r["kind"] == "proof" and r["identical"]), len(po)))
    for r in bad:
        print(r["id"], r["arm"], r["solved"], r["twin_solved"], r["result_diff_seeds"][:5], r["trace_diff_seeds"][:5])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
