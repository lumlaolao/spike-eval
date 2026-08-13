#!/usr/bin/env python3
"""Fold executed floor runs into the PZL suite manifest.

Writes the `floors` and `scoreability` blocks from actual run artifacts, never from
estimates. The manifest shipped `floors: {status: "NOT MEASURED"}` deliberately rather
than guessing; this is the only thing allowed to replace that, and only from
runs/pzl/<arm>/results.jsonl produced by eval/run_pzl.py.

Two suites of numbers come out of it:

  floors       -- solve rate per trivial arm, over UNTAINTED, BOUNDED, loadable rows.
                  These are the "a score without its floor is not a score" baselines.
  scoreability -- which rows cannot be scored at the pinned engine at all, and why.
                  9 of 300: 3 crash Forge's own attachment handling on load, 6 have a
                  goal trigger that can never fire. Recorded per-id so a consumer can
                  exclude exactly those rows instead of re-deriving them.

The suite .jsonl is NOT rewritten -- it is the frozen corpus and its sha256 is its
identity. Scoreability is a property of the ENGINE at a pin, not of the corpus, so it
belongs in the manifest beside the pin.

    python3 eval/pzl_floors.py --runs runs/pzl --suite eval/suites/pzl-v1
"""
import argparse
import collections
import json
import os

ARMS = [("floor", "floor"), ("random", "random"), ("ai", "ai-log")]


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def arm_stats(rows):
    """-> dict of the one number that may be quoted, plus everything excluded from it."""
    scoreable = [r for r in rows
                 if r.get("status") in ("solved", "failed") and not r.get("tainted_by_deckout")]
    solved = [r for r in scoreable if r.get("solved")]
    tainted = [r for r in rows if r.get("tainted_by_deckout")]
    return collections.OrderedDict([
        ("n_attempted", len(rows)),
        ("n_scoreable", len(scoreable)),
        ("n_solved", len(solved)),
        ("solve_rate", round(len(solved) / float(len(scoreable)), 4) if scoreable else None),
        ("n_excluded_tainted", len(tainted)),
        ("n_free_wins_excluded", len([r for r in tainted if r.get("solved")])),
        ("n_excluded_unscoreable", len([r for r in rows if r.get("status") == "unscoreable"])),
        ("n_invalid", len([r for r in rows if r.get("status") == "invalid"])),
    ])


def scoreability(all_rows_by_arm):
    """Rows unscoreable in EVERY arm, with the reason. Arm-independent by construction:
    a crash on load and an unreachable turn limit are properties of the file + engine."""
    ids = None
    for rows in all_rows_by_arm.values():
        s = {r.get("id") for r in rows if r.get("status") == "unscoreable"}
        ids = s if ids is None else (ids & s)
    out = []
    any_rows = list(all_rows_by_arm.values())[0]
    by_id = {r.get("id"): r for r in any_rows}
    for pid in sorted(ids or []):
        r = by_id.get(pid, {})
        if not r.get("goal_enforcement_installed", True):
            reason = ("engine crash on load: Forge's own GameState attachment handling NPEs "
                      "(handleCardAttachments / AttachEffect.resolve) at this pin")
        elif not r.get("goal_bounded", True):
            reason = ("goal trigger can never fire: the .pzl [state] starts past the turn limit "
                      "and Trigger compares TurnCount with strict equality, so the solver has "
                      "unlimited turns")
        else:
            reason = "unscoreable for an unrecorded reason -- investigate"
        out.append(collections.OrderedDict([("id", pid), ("reason", reason)]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs/pzl")
    ap.add_argument("--suite", default="eval/suites/pzl-v1")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    per_arm, rows_by_arm = collections.OrderedDict(), collections.OrderedDict()
    for dirname, label in ARMS:
        path = os.path.join(args.runs, dirname, "results.jsonl")
        if not os.path.isfile(path):
            raise SystemExit("missing %s -- run eval/run_pzl.py --arm %s first" % (path, label))
        rows = load(path)
        rows_by_arm[label] = rows
        per_arm[label] = arm_stats(rows)

    unscoreable = scoreability(rows_by_arm)

    print("%-10s %10s %8s %11s %9s" % ("arm", "scoreable", "solved", "solve_rate", "excluded"))
    for label, s in per_arm.items():
        print("%-10s %10d %8d %11s %9d"
              % (label, s["n_scoreable"], s["n_solved"],
                 "%.4f" % s["solve_rate"] if s["solve_rate"] is not None else "n/a",
                 s["n_excluded_tainted"] + s["n_excluded_unscoreable"] + s["n_invalid"]))
    print("\nunscoreable at this pin: %d rows" % len(unscoreable))
    for u in unscoreable:
        print("  %-12s %s" % (u["id"], u["reason"][:70]))

    man_path = args.suite + ".json"
    with open(man_path, encoding="utf-8") as fh:
        man = json.load(fh)

    best = max(s["solve_rate"] or 0.0 for s in per_arm.values())
    man["floors"] = collections.OrderedDict([
        ("status", "MEASURED"),
        ("method", "executed"),
        ("quote_floor_as", round(best, 4)),
        ("note",
         "EXECUTED baselines, not analytic: there is no fixed option menu to enumerate on "
         "this axis, so every number here comes from actually playing all 300 positions with "
         "that arm. Quote the HIGHEST of them (%.4f, the Forge-AI arm) as the floor a model "
         "must beat -- it is the strongest agent here that does no LLM reasoning. Rates are "
         "over UNTAINTED, BOUNDED, loadable rows only; see `excluded` counts and the "
         "`scoreability` block for what was removed and why." % best),
        ("arms", per_arm),
    ])
    man["scoreability"] = collections.OrderedDict([
        ("n_unscoreable_at_pin", len(unscoreable)),
        ("note",
         "These rows cannot produce a verdict at FORGE_COMMIT and are excluded from every "
         "rate above. They are a property of the ENGINE at this pin, not of the corpus, which "
         "is why they live here and not in the frozen .jsonl (whose sha256 is its identity). "
         "Recorded per-id so a consumer excludes exactly these rather than re-deriving them."),
        ("rows", unscoreable),
    ])

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("\nupdated %s" % man_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
