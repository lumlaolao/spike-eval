#!/usr/bin/env python3
"""Self-test for defect_analyzer.py: synthetic (jsonl, log) fixtures.

Run: python3 eval/test_defect_analyzer.py   (or via pytest)
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import defect_analyzer as da  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def game_line(seed=1, swap=False, winner="A"):
    return {"t": "game", "seed": seed, "swap": swap,
            "deck_a": "Identity-A", "deck_b": "Identity-B",
            "controller_a": "llm", "controller_b": "llm",
            "smart_a": "openrouter:x/model-a", "smart_b": "openrouter:x/model-b",
            "winner": winner, "turns": 9, "win_condition": "life",
            "duration_ms": 60000}


def dec(seat="model-a", dtype="play", tier="smart", n_options=4, choice=1,
        latency=5000, fallback=None, prompt="P", raw_reply="reasoning...\nCHOICE: 1",
        turn=3, **extra):
    d = {"t": "decision", "seat": seat, "turn": turn, "phase": "MAIN1",
         "type": dtype, "n_options": n_options, "choice": choice,
         "tier": tier, "latency_ms": latency, "fallback": fallback,
         "options": ["a", "b", "c", "d"][:max(0, n_options)],
         "prompt": prompt, "raw_reply": raw_reply}
    d.update(extra)
    return d


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ----------------------------------------------------------------- tests

def test_parse_log_text():
    text = "\n".join([
        "[model-a] [ROUTER] mulligan: 2/7 lands -> asking x/model-a...",
        "[OPENROUTER model-a/smart] replied in 4000 ms",
        "[model-a] [ROUTER] mulligan: KEEP",
        "[model-b] [ROUTER] play(react): 2 castable -> asking x/model-b...",
        "[OPENROUTER model-b/fast] non-answer -> treating as bridge-down",
        "[OPENROUTER model-b/fast] non-answer -> treating as bridge-down",
        "[model-b] [ROUTER] play: PASS",
        "[model-a] [AUTO-LEGAL] play(bridge-down): land Forest",
    ])
    ev = da.parse_log_text(text)
    check("ask count model-a", ev["asks"]["model-a"] == 1)
    check("ask count model-b", ev["asks"]["model-b"] == 1)
    check("non_answer count model-b", ev["non_answers"]["model-b"] == 2)
    check("pass count model-b", ev["passes"]["model-b"] == 1)
    check("auto_legal count model-a", ev["auto_legal"]["model-a"] == 1)


def test_normalize_fallback_matches_analyze_corpus_convention():
    n = da.normalize_fallback
    check("norm strips ->payload", n("mulligan(no-model)->KEEP") == "mulligan(no-model)")
    check("norm strips x-count", n("attackers(no-model)->all-in x3") == "attackers(no-model)")


def test_clean_game_no_defects():
    """A fully clean paired game: every log ask has a matching jsonl row,
    no tier=ai, no forced-pick mislabel, mulligan row present."""
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        write_lines(dpath / "game-1-1000.jsonl", [
            game_line(seed=1, swap=False),
            dec(seat="model-a", dtype="mulligan", tier="smart", n_options=2, choice=0,
                raw_reply="fine hand\nKEEP"),
            dec(seat="model-a", dtype="play", tier="smart", n_options=3, choice=1),
            dec(seat="model-a", dtype="play", tier="auto", n_options=1, choice=0,
                latency=0, prompt=None, raw_reply=None, fallback=None),
        ])
        write_text(dpath / "game-1-0.log", "\n".join([
            "[model-a] [ROUTER] mulligan: 2/7 lands -> asking x/model-a...",
            "[OPENROUTER model-a/smart] replied in 4000 ms",
            "[model-a] [ROUTER] mulligan: KEEP",
            "[model-a] [ROUTER] play(main): 1 castable -> asking x/model-a...",
            "[OPENROUTER model-a/smart] replied in 5000 ms",
            "[model-a] [ROUTER] play: casts Foo",
        ]))
        report = da.build_report([dpath])
        c = report["combined"]
        check("no fallback lines", c["fallback_by_kind"] == {})
        check("ai_tier_count is 0", c["ai_tier_count"] == 0)
        check("no forced-pick mislabel (n=1 IS tier=auto)",
              c["forced_pick_mislabels"]["count"] == 0)
        check("mulligan row present for model-a",
              c["mulligan_by_seat"].get("model-a", 0) >= 1)
        check("no silent drops", c["silent_drop_total"] == 0)
        check("hard invariants pass", report["hard_invariants"]["pass"] is True)


def test_silent_drop_detected():
    """C5 signature: two 'play(react) -> asking' log lines for a seat that
    never produces a matching jsonl row (bridge-down->PASS branch that
    skips evalDec()). Must show up as a gap and fail the hard invariant."""
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        write_lines(dpath / "game-2-2000.jsonl", [
            game_line(seed=2, swap=False),
            # only an unrelated attackers decision for seat-x actually lands
            dec(seat="seat-x", dtype="attackers", tier="smart", n_options=2, choice=1),
        ])
        write_text(dpath / "game-2-0.log", "\n".join([
            "[seat-x] [ROUTER] attackers: 2 can attack -> asking x/model...",
            "[OPENROUTER seat-x/smart] replied in 3000 ms",
            "[seat-x] [ROUTER] play(react): 2 castable -> asking x/model...",
            "[OPENROUTER seat-x/fast] non-answer -> treating as bridge-down",
            "[OPENROUTER seat-x/fast] non-answer -> treating as bridge-down",
            # no PASS line, no AUTO-LEGAL line, no further jsonl row: the bug.
            "[seat-x] [ROUTER] play(react): 2 castable -> asking x/model...",
            "[OPENROUTER seat-x/fast] non-answer -> treating as bridge-down",
            "[OPENROUTER seat-x/fast] non-answer -> treating as bridge-down",
        ]))
        report = da.build_report([dpath])
        c = report["combined"]
        sd = c["silent_drop_by_seat"]["seat-x"]
        check("log_asks counted (attackers + 2x play)", sd["log_asks"] == 3)
        check("jsonl_model_consulted counted (only attackers row)",
              sd["jsonl_model_consulted"] == 1)
        check("gap is exactly 2 (the two vanished play(react) windows)",
              sd["gap"] == 2)
        check("seat flagged in silent_drop_seats", "seat-x" in c["silent_drop_seats"])
        check("silent_drop_total > 0", c["silent_drop_total"] == 2)
        check("hard invariants FAIL", report["hard_invariants"]["pass"] is False)
        check("hard invariants report the right count",
              report["hard_invariants"]["silent_drop_total"] == 2)


def test_ai_tier_forced_pick_and_missing_mulligan():
    """C1 (tier=ai), C6 (forced single-legal mislabeled tier=smart), and C7
    (a seat with zero mulligan rows despite other decisions existing) all
    in one synthetic file, no log needed for these (jsonl-only metrics)."""
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        write_lines(dpath / "game-3-3000.jsonl", [
            game_line(seed=3, swap=False),
            # C1: sacrifice routed to Forge AI, never asked the model.
            dec(seat="model-a", dtype="sacrifice", tier="ai", n_options=-1,
                choice=0, latency=0, prompt=None, raw_reply=None,
                fallback="sacrifice(ai)->Krenko, Mob Boss"),
            # C6: forced single-legal pick mislabeled tier=smart.
            dec(seat="model-a", dtype="target", tier="smart", n_options=1,
                choice=0, latency=0, prompt=None, raw_reply=None),
            # model-a has NO mulligan row at all (heuristic auto-keep, C7),
            # while model-b does.
            dec(seat="model-b", dtype="mulligan", tier="smart", n_options=2,
                choice=0, raw_reply="KEEP"),
            # parse-fail: model-b answered but never emitted CHOICE/PICKS.
            dec(seat="model-b", dtype="play", tier="smart", n_options=3,
                choice=1, raw_reply="I will just cast the spell"),
        ])
        report = da.build_report([dpath])
        c = report["combined"]
        check("ai_tier_count == 1", c["ai_tier_count"] == 1)
        check("forced_pick_mislabels count == 1",
              c["forced_pick_mislabels"]["count"] == 1)
        check("model-a missing from mulligan coverage",
              "model-a" in c["mulligan_missing_seats"])
        check("model-b has a mulligan row",
              c["mulligan_by_seat"].get("model-b", 0) == 1)
        check("model-b counted for a no-CHOICE parse-fail",
              c["parse_fail_by_model"].get("model-b", 0) == 1)
        check("hard invariants FAIL (ai_tier_count>0)",
              report["hard_invariants"]["pass"] is False)


def test_malformed_lines_and_no_log_are_tolerated():
    """A directory with a jsonl that has no matching log (crashed/manual
    run) must still be scored on jsonl-only metrics, and malformed lines
    must be skipped rather than crashing."""
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        write_lines(dpath / "game-4-4000.jsonl", [
            "{not valid json",
            game_line(seed=4, swap=False),
            dec(seat="model-a", dtype="play", tier="smart"),
        ])
        report = da.build_report([dpath])
        s = report["dirs"][str(dpath)]
        check("malformed line skipped and counted", s["n_skipped_lines"] == 1)
        check("unpaired file noted (no matching .log)",
              str(dpath / "game-4-4000.jsonl") in s["unpaired_files"])
        check("decision still counted despite missing log", s["n_decisions"] == 1)


def main():
    test_parse_log_text()
    test_normalize_fallback_matches_analyze_corpus_convention()
    test_clean_game_no_defects()
    test_silent_drop_detected()
    test_ai_tier_forced_pick_and_missing_mulligan()
    test_malformed_lines_and_no_log_are_tolerated()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        sys.exit(1)
    print("all tests passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
