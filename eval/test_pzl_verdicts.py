#!/usr/bin/env python3
"""Tests for eval/pzl_verdicts.py — folding reviewer verdicts into the suite.

The thing this module must never do is make coverage look BETTER than it is.
Every check below is aimed at that: engine plumbing must be dropped from both
sides of the comparison, load-bearing counts must never exceed raw counts, and
an unjudged row must stay visibly unjudged rather than defaulting to "good".

Run: python3 eval/test_pzl_verdicts.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pzl_verdicts as V   # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FAILURES = []


def check(name, cond, detail=""):
    print("[%s] %s%s" % ("ok" if cond else "FAIL", name,
                         (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def test_is_mechanic_drops_only_plumbing():
    check("keyword_always_counts", V.is_mechanic("kw:Flying"))
    check("obscure_keyword_counts", V.is_mechanic("kw:Teamwork"))
    check("real_effect_counts", V.is_mechanic("eff:Surveil"))
    check("proliferate_counts", V.is_mechanic("eff:Proliferate"))
    # Plumbing: every deck does these every turn; "covering" them is vacuous.
    for junk in ("eff:Cleanup", "eff:Effect", "eff:Mana", "eff:Draw",
                 "eff:ChangeZone"):
        check("drops_%s" % junk, not V.is_mechanic(junk))
    # A keyword must NEVER be dropped even if its name collides conceptually.
    check("kw_draw_not_dropped", V.is_mechanic("kw:Draw"))


def _suite(tmp, rows):
    base = os.path.join(tmp, "s")
    with open(base + ".jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump({"name": "s", "sha256": "stale", "mechanic_coverage": {}}, fh)
    return base


def _verdicts(tmp, rows, name="result1.json"):
    d = os.path.join(tmp, "v")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    return d


def test_load_bearing_never_exceeds_raw_and_plumbing_is_dropped():
    rows = [{"id": "a", "tagged_mechanics":
             ["kw:Flying", "eff:Surveil", "eff:Cleanup", "eff:Mana"]},
            {"id": "b", "tagged_mechanics": ["kw:Flying", "eff:Cleanup"]}]
    # Reviewer says Flying mattered on 'a' but nothing mattered on 'b'. It
    # also (wrongly) lists plumbing as load-bearing — that must be filtered.
    verd = [{"id": "a", "load_bearing": ["kw:Flying", "eff:Cleanup"],
             "quality": "good"},
            {"id": "b", "load_bearing": [], "quality": "weak",
             "note": "vanilla board"}]
    with tempfile.TemporaryDirectory() as tmp:
        base = _suite(tmp, rows)
        V.main(["--verdicts", _verdicts(tmp, verd), "--suite", base,
                "--quota", "1"])
        out = [json.loads(l) for l in open(base + ".jsonl", encoding="utf-8")]
        man = json.load(open(base + ".json", encoding="utf-8"))

    by = {r["id"]: r for r in out}
    check("plumbing_stripped_from_load_bearing",
          by["a"]["load_bearing_mechanics"] == ["kw:Flying"],
          str(by["a"]["load_bearing_mechanics"]))
    check("empty_load_bearing_preserved",
          by["b"]["load_bearing_mechanics"] == [])
    check("quality_recorded", by["a"]["review_quality"] == "good"
          and by["b"]["review_quality"] == "weak")
    check("note_recorded", by["b"].get("review_note") == "vanilla board")
    rev = man["mechanic_coverage"]["review"]
    check("counts_load_bearing_only", rev["n_load_bearing_mechanics"] == 1,
          str(rev))
    check("plumbing_count_reported", rev["n_dropped_as_engine_plumbing"] >= 2)
    check("sha_refreshed", man["sha256"] != "stale")


def test_unjudged_rows_stay_visible():
    """A row nobody reviewed must not silently inherit a passing verdict."""
    rows = [{"id": "a", "tagged_mechanics": ["kw:Flying"]},
            {"id": "zzz", "tagged_mechanics": ["kw:Ward"]}]
    verd = [{"id": "a", "load_bearing": ["kw:Flying"], "quality": "good"}]
    with tempfile.TemporaryDirectory() as tmp:
        base = _suite(tmp, rows)
        V.main(["--verdicts", _verdicts(tmp, verd), "--suite", base])
        out = {r["id"]: r for r in
               (json.loads(l) for l in open(base + ".jsonl", encoding="utf-8"))}
        man = json.load(open(base + ".json", encoding="utf-8"))
    check("unjudged_marked", out["zzz"]["review_quality"] == "unjudged")
    check("unjudged_has_no_load_bearing",
          out["zzz"]["load_bearing_mechanics"] == [])
    check("n_judged_excludes_unjudged",
          man["mechanic_coverage"]["review"]["n_judged"] == 1)


def test_multiple_verdict_files_merge():
    rows = [{"id": "a", "tagged_mechanics": ["kw:Flying"]},
            {"id": "b", "tagged_mechanics": ["kw:Ward"]}]
    with tempfile.TemporaryDirectory() as tmp:
        base = _suite(tmp, rows)
        d = _verdicts(tmp, [{"id": "a", "load_bearing": ["kw:Flying"],
                             "quality": "good"}], "result1.json")
        _verdicts(tmp, [{"id": "b", "load_bearing": ["kw:Ward"],
                         "quality": "good"}], "result2.json")
        V.main(["--verdicts", d, "--suite", base, "--quota", "1"])
        man = json.load(open(base + ".json", encoding="utf-8"))
    rev = man["mechanic_coverage"]["review"]
    check("both_files_read", rev["verdict_files"] == 2)
    check("both_rows_judged", rev["n_judged"] == 2)
    check("merged_mechanic_count", rev["n_load_bearing_mechanics"] == 2)


def test_only_finished_batches_are_read():
    """Reviewers write scratch files beside their final output. A
    `result*.json` glob swept in result4_part1.json / result_0.json and
    reported 199 of 300 judged from partial work — inflating coverage with
    half-finished review. Only `result<N>.json` counts."""
    rows = [{"id": "a", "tagged_mechanics": ["kw:Flying"]},
            {"id": "b", "tagged_mechanics": ["kw:Ward"]}]
    with tempfile.TemporaryDirectory() as tmp:
        base = _suite(tmp, rows)
        d = _verdicts(tmp, [{"id": "a", "load_bearing": ["kw:Flying"],
                             "quality": "good"}], "result1.json")
        for junk in ("result4_part1.json", "result_0.json", "results.json",
                     "result.json"):
            with open(os.path.join(d, junk), "w", encoding="utf-8") as fh:
                json.dump([{"id": "b", "load_bearing": ["kw:Ward"],
                            "quality": "good"}], fh)
        V.main(["--verdicts", d, "--suite", base, "--quota", "1"])
        man = json.load(open(base + ".json", encoding="utf-8"))
        out = {r["id"]: r for r in
               (json.loads(l) for l in open(base + ".jsonl", encoding="utf-8"))}
    rev = man["mechanic_coverage"]["review"]
    check("partials_excluded", rev["verdict_files"] == 1, str(rev))
    check("partial_row_stays_unjudged",
          out["b"]["review_quality"] == "unjudged")
    check("partial_load_bearing_not_credited",
          rev["n_load_bearing_mechanics"] == 1)


def test_strictness_flags_divergent_reviewers():
    """Two reviewers on identical inputs, one keeping everything and one
    pruning everything, must not be silently averaged."""
    tagged = {"a": ["kw:Flying", "kw:Ward", "eff:Surveil"]}
    lenient = [{"id": "a", "load_bearing": ["kw:Flying", "kw:Ward",
                                            "eff:Surveil"], "quality": "good"}]
    strictr = [{"id": "a", "load_bearing": [], "quality": "good"}]
    lo, n1 = V.strictness(lenient, tagged)
    hi, n2 = V.strictness(strictr, tagged)
    check("lenient_scores_zero", abs(lo) < 1e-9, str(lo))
    check("strict_scores_one", abs(hi - 1.0) < 1e-9, str(hi))
    check("strictness_counts_rows", n1 == 1 and n2 == 1)
    # Plumbing must not enter the denominator, or a reviewer looks stricter
    # than it is purely for ignoring bookkeeping tags.
    tagged_junk = {"a": ["kw:Flying", "eff:Cleanup", "eff:Mana"]}
    frac, _ = V.strictness(
        [{"id": "a", "load_bearing": ["kw:Flying"], "quality": "good"}],
        tagged_junk)
    check("plumbing_out_of_denominator", abs(frac) < 1e-9, str(frac))


def test_dry_run_writes_nothing():
    rows = [{"id": "a", "tagged_mechanics": ["kw:Flying"]}]
    verd = [{"id": "a", "load_bearing": ["kw:Flying"], "quality": "good"}]
    with tempfile.TemporaryDirectory() as tmp:
        base = _suite(tmp, rows)
        before = open(base + ".jsonl", encoding="utf-8").read()
        V.main(["--verdicts", _verdicts(tmp, verd), "--suite", base,
                "--dry-run"])
        after = open(base + ".jsonl", encoding="utf-8").read()
        man = json.load(open(base + ".json", encoding="utf-8"))
    check("dry_run_leaves_jsonl", before == after)
    check("dry_run_leaves_manifest", man["sha256"] == "stale")


def main():
    test_is_mechanic_drops_only_plumbing()
    test_load_bearing_never_exceeds_raw_and_plumbing_is_dropped()
    test_unjudged_rows_stay_visible()
    test_multiple_verdict_files_merge()
    test_only_finished_batches_are_read()
    test_strictness_flags_divergent_reviewers()
    test_dry_run_writes_nothing()
    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all pzl_verdicts checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
