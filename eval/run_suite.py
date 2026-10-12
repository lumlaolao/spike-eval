#!/usr/bin/env python3
"""Validate an eval suite (eval/suites/*.json) and print its execution plan.

A suite is the frozen artifact a checkpoint gets pointed at: declared seed
bank, mirror decks, opponent ladder, gates. This script checks the suite
against eval/holdouts.json and the run_batch.py CLI contract, then prints
the exact commands that run it. It does not launch anything (orchestration
is a separate workstream).

Usage:
    python3 eval/run_suite.py eval/suites/stage3-v1.json \
        --student openrouter:default_model [--teacher opus] [--json]

Exit 0 = suite valid, 2 = validation errors.
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVAL = Path(__file__).resolve().parent
REGISTRY = EVAL / "holdouts.json"

sys.path.insert(0, str(EVAL))
import seed_registry  # noqa: E402  the seed-side half of contamination control

ROLES = {"sanity", "in-distribution", "generalization", "calibration"}
CONTROLLERS = {"floor", "random", "ai", "llm"}


def parse_seed_range(spec):
    """'9000..9199' -> (9000, 9199). Returns None on malformed input."""
    try:
        a, b = spec.split("..")
        a, b = int(a), int(b)
        return (a, b) if a <= b else None
    except (ValueError, AttributeError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suite", help="path to a suite JSON file")
    ap.add_argument("--student", default="openrouter:default_model",
                    help="model spec substituted for ${STUDENT} (identity A). "
                         "Default assumes a local mlx server; which checkpoint "
                         "that is MUST come from the game-line provenance "
                         "(provenance workstream item 1).")
    ap.add_argument("--teacher", default=None,
                    help="model spec substituted for ${TEACHER} (teacher-mirror arm)")
    ap.add_argument("--checkpoint", default=None,
                    help="weights identity passed through to run_batch.py "
                         "--checkpoint (adapter path or opaque tag). Without it "
                         "every arm is stamped checkpoint_sha=unknown and the "
                         "report cannot say which checkpoint earned the number.")
    ap.add_argument("--context-profile", default=None, metavar="NAME",
                    help="passed through to run_batch.py --context-profile for "
                         "every arm (one condition per suite run). Unset = the "
                         "JVM baseline profile.")
    ap.add_argument("--memory-tools", action="store_true",
                    help="passed through to run_batch.py --memory-tools for every "
                         "arm. Agent-plus-tools is a separate condition; its arms "
                         "are written to runs/<suite>-<arm>-memtools.")
    ap.add_argument("--json", action="store_true", help="machine-readable plan")
    args = ap.parse_args()

    errors = []
    warnings = []

    try:
        suite = json.loads(Path(args.suite).read_text())
    except Exception as e:
        print(f"error: cannot load suite {args.suite}: {e}", file=sys.stderr)
        return 2
    registry = json.loads(REGISTRY.read_text())
    decks = registry["decks"]

    name = suite.get("suite", "<unnamed>")

    if suite.get("discipline") != "cited":
        errors.append(f"suite discipline must be 'cited' (got "
                      f"{suite.get('discipline')!r}); exploratory runs do not "
                      f"belong in a frozen suite")

    bank = parse_seed_range(suite.get("seed_policy", {}).get("bank", ""))
    if not bank:
        errors.append("seed_policy.bank missing or malformed (want 'A..B')")

    # The suite's own bank declaration is not authority — eval/seed_registry.json is
    # (that is what this suite's seed_policy note asked for). Without this, run_suite
    # can report VALID for a suite whose seeds seed_registry.py refuses, and the two
    # artifacts disagree about the same file.
    try:
        seeds_reg = seed_registry.load_registry()
    except seed_registry.RegistryError as e:
        seeds_reg = None
        errors.append(f"seed registry unusable ({e}); cannot verify the suite's "
                      f"seeds are held out")
    if seeds_reg and bank:
        for lo, hi in [bank]:
            not_eval = sorted(s for s in range(lo, hi + 1)
                              if seeds_reg.policy_for(s) != "eval-only")
            if not_eval:
                errors.append(
                    f"seed_policy.bank {lo}..{hi} includes {len(not_eval)} seed(s) "
                    f"that are not eval-only in seed_registry.json "
                    f"(e.g. {not_eval[0]})")

    gates = suite.get("gates", {})
    mfr = gates.get("max_fallback_rate")
    if not isinstance(mfr, (int, float)) or not 0 <= mfr <= 1:
        errors.append("gates.max_fallback_rate must be a number in [0, 1]")
    if gates.get("min_autonomy_pct") is not None:
        warnings.append("gates.min_autonomy_pct is set but the autonomy field "
                        "does not exist on game lines yet (provenance "
                        "workstream item 3) — the gate is unenforceable today")

    subst = {"${STUDENT}": args.student, "${TEACHER}": args.teacher or "${TEACHER}"}

    def sub(v):
        return subst.get(v, v)

    used_seeds = set()
    plan = []
    for arm in suite.get("arms", []):
        aname = arm.get("name", "<unnamed>")
        if not arm.get("enabled", False):
            plan.append({"arm": aname, "enabled": False})
            continue

        role = arm.get("role")
        if role not in ROLES:
            errors.append(f"arm {aname}: role must be one of {sorted(ROLES)}")

        deck = arm.get("deck")
        if deck not in decks:
            errors.append(f"arm {aname}: deck {deck!r} not in holdouts.json")
            deck_file = None
        else:
            deck_file = decks[deck]["file"]
            if not (REPO / deck_file).is_file():
                errors.append(f"arm {aname}: deck file missing: {deck_file}")
            status = decks[deck]["status"]
            if role == "generalization" and status != "held-out":
                errors.append(f"arm {aname}: role=generalization requires a "
                              f"held-out deck, but {deck!r} is {status!r}")
            if role == "in-distribution" and status != "in-corpus":
                warnings.append(f"arm {aname}: role=in-distribution but deck "
                                f"{deck!r} is {status!r}")

        ctrl_b = arm.get("controller_b")
        if ctrl_b not in CONTROLLERS:
            errors.append(f"arm {aname}: controller_b must be one of "
                          f"{sorted(CONTROLLERS)} (got {ctrl_b!r}); "
                          f"controller_a is always llm (the student)")
        if ctrl_b == "llm" and not (arm.get("smart_b") and arm.get("fast_b")):
            errors.append(f"arm {aname}: controller_b=llm needs smart_b/fast_b")
        if ctrl_b == "llm" and "${TEACHER}" in (arm.get("smart_b", "") +
                                                arm.get("fast_b", "")) \
                and not args.teacher:
            errors.append(f"arm {aname}: uses ${{TEACHER}} but --teacher was "
                          f"not given")

        rng = parse_seed_range(arm.get("seeds", ""))
        if not rng:
            errors.append(f"arm {aname}: seeds missing or malformed "
                          f"(want 'A..B')")
        else:
            if bank and not (bank[0] <= rng[0] and rng[1] <= bank[1]):
                errors.append(f"arm {aname}: seeds {rng[0]}..{rng[1]} outside "
                              f"the suite bank {bank[0]}..{bank[1]}")
            overlap = used_seeds.intersection(range(rng[0], rng[1] + 1))
            if overlap:
                errors.append(f"arm {aname}: seed range overlaps another arm "
                              f"(e.g. {min(overlap)})")
            used_seeds.update(range(rng[0], rng[1] + 1))
            if seeds_reg:
                arm_seeds = list(range(rng[0], rng[1] + 1))
                burned = seeds_reg.burned_in(arm_seeds)
                if burned:
                    errors.append(
                        f"arm {aname}: {len(burned)} seed(s) are BURNED — already "
                        f"harvested or played by a corpus-generating run before the "
                        f"band was declared eval-only "
                        f"({seed_registry._fmt_seed_list(burned)}). Shift the range.")

        if errors and plan and plan[-1].get("arm") == aname:
            continue
        n_seeds = (rng[1] - rng[0] + 1) if rng else 0
        entry = {
            "arm": aname,
            "enabled": True,
            "role": role,
            "deck": deck,
            "seeds": arm.get("seeds"),
            "games": 2 * n_seeds,
            "run": None,
            "aggregate": None,
        }
        if deck_file and ctrl_b:
            out_dir = f"runs/{name}-{aname}"
            if args.context_profile:
                out_dir += "-ctx-" + args.context_profile
            if args.memory_tools:
                out_dir += "-memtools"
            cmd = ["python3", "eval/run_batch.py",
                   "--seeds", arm["seeds"], "--pairs",
                   "--deck", deck_file,
                   "--controller-a", "llm",
                   "--controller-b", ctrl_b,
                   "--smart-a", sub(suite["student"]["smart"]),
                   "--fast-a", sub(suite["student"]["fast"]),
                   "--out", out_dir]
            if args.checkpoint:
                cmd += ["--checkpoint", args.checkpoint]
            if args.context_profile:
                cmd += ["--context-profile", args.context_profile]
            if args.memory_tools:
                cmd += ["--memory-tools"]
            if ctrl_b == "llm":
                cmd += ["--smart-b", sub(arm["smart_b"]),
                        "--fast-b", sub(arm["fast_b"])]
            entry["run"] = cmd
            entry["aggregate"] = ["python3", "eval/aggregate.py", out_dir,
                                  "--max-fallback-rate", str(mfr)]
            entry["defects"] = ["python3", "eval/defect_analyzer.py", out_dir]
        plan.append(entry)

    if args.json:
        print(json.dumps({"suite": name, "valid": not errors,
                          "errors": errors, "warnings": warnings,
                          "plan": plan}, indent=2))
    else:
        print(f"suite: {name} (discipline: {suite.get('discipline')})")
        print(f"student (identity A): {args.student}")
        ckpt = args.checkpoint or "UNSET — arms will stamp checkpoint_sha=unknown"
        print(f"checkpoint: {ckpt}")
        print(f"gates: max_fallback_rate<={mfr}, defect_analyzer exit 0"
              + ("" if gates.get("min_autonomy_pct") is None
                 else f", autonomy>={gates['min_autonomy_pct']}%"))
        print()
        for e in plan:
            if not e.get("enabled"):
                print(f"[disabled] {e['arm']}")
                continue
            print(f"[{e['role']}] {e['arm']}: deck={e['deck']} "
                  f"seeds={e['seeds']} -> {e['games']} games")
            if e["run"]:
                print("  run:       " + " ".join(e["run"]))
                print("  aggregate: " + " ".join(e["aggregate"]))
                print("  defects:   " + " ".join(e["defects"]))
        total = sum(e.get("games", 0) for e in plan if e.get("enabled"))
        print()
        print(f"total: {total} games across "
              f"{sum(1 for e in plan if e.get('enabled'))} arms (sequential, "
              f"jobs=1 per cited discipline)")
        for w in warnings:
            print(f"WARNING: {w}")
        for e_ in errors:
            print(f"ERROR: {e_}")
        print()
        print("RESULT:", "VALID" if not errors else "INVALID")

    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
