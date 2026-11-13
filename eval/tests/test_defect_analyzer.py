#!/usr/bin/env python3
"""Self-test for defect_analyzer.py: synthetic (jsonl, log) fixtures.

Run: python3 eval/tests/test_defect_analyzer.py   (or via pytest)
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
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


def test_a_block_set_that_stood_illegal_is_counted():
    """Issue #311: a seat's fallback that found no legal block set under a requirement, and an arm's 64 illegal draws,
    leave an illegal declaration standing; the log says so and the report counts it. A set the fallback took is not."""
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        write_lines(dpath / "game-1-1000.jsonl", [game_line(seed=1, swap=False),
                                                  dec(seat="model-a", dtype="mulligan", n_options=2, choice=0)])
        write_text(dpath / "game-1-0.log", "\n".join([
            "[model-a] [ROUTER] mulligan: 2/7 lands -> asking x/model-a...",
            "[model-a] [BLOCK-INVALID] no blocks is not legal (Bears must block): took the legal set Bears->Unicorn",
            "[model-a] [BLOCK-INVALID] no blocks is not legal (Bears must block) and no legal set was found: none",
            "[model-b] [BLOCK-INVALID] arm: no legal block set in 64 draws (Bears must block)",
        ]))
        c = da.build_report([dpath])["combined"]
        check("illegal block sets that stood are counted", c.get("illegal_blocks_total") == 2, str(c.get("illegal_blocks_total")))
        check("per seat", c.get("illegal_blocks_by_seat") == {"model-a": 1, "model-b": 1}, str(c.get("illegal_blocks_by_seat")))
        text = da.render_text(da.build_report([dpath]))
        check("and rendered", "illegal block sets that stood: 2" in text)


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


def test_crashed_game_fails_the_invariants():
    """runs/luna-v8/g2: the game thread died on a StackOverflowError and the run read as a clean draw."""
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        crashed = game_line(seed=2, swap=False, winner="invalid")
        crashed["game_error"] = "java.lang.StackOverflowError"
        write_lines(dpath / "game-2-1000.jsonl", [crashed,
                                                  dec(seat="model-a", dtype="mulligan", n_options=2, choice=0)])
        write_text(dpath / "game-2-0.log", "[model-a] [ROUTER] mulligan: 2/7 lands -> asking x/model-a...\n"
                   "[OPENROUTER model-a/smart] replied in 4000 ms\n"
                   "[HEADLESS] game thread threw: java.lang.StackOverflowError")
        report = da.build_report([dpath])
        check("a crashed game is counted", report["hard_invariants"]["game_crash_count"] == 1)
        check("and fails the invariants", report["hard_invariants"]["pass"] is False)


def test_a_crash_only_its_log_shows_fails_the_invariants():
    """Round-6 review of #374: the crash check reads the paired log, which it never received before. A game whose
    thread died after its line was written (no game_error on it) is a crash all the same."""
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        write_lines(dpath / "game-4-1000.jsonl", [game_line(seed=4, swap=False),
                                                  dec(seat="model-a", dtype="mulligan", n_options=2, choice=0)])
        write_text(dpath / "game-4-0.log", "[model-a] [ROUTER] mulligan: 2/7 lands -> asking x/model-a...\n"
                   "[OPENROUTER model-a/smart] replied in 4000 ms\n"
                   "[HEADLESS] game thread threw: java.lang.IllegalStateException: boom")
        report = da.build_report([dpath])
        check("a crash only the log shows is counted", report["hard_invariants"]["game_crash_count"] == 1,
              str(report["hard_invariants"]))
        check("and fails the invariants", report["hard_invariants"]["pass"] is False)


def test_a_crash_is_listed_under_the_one_file_that_owns_its_log():
    """Issue #385: every log of the directory belongs to exactly one game file: the file its "[EVAL] jsonl" line
    names when that file exists here, else the file pair_dir paired it with. A crash is listed under its owner, once,
    whether or not the owner has a game line."""
    crash = "[HEADLESS] game thread threw: java.lang.StackOverflowError"
    names = lambda f: "[EVAL] jsonl %s\n" % f

    def game(d, name, swap=False, error=None):
        line = game_line(seed=7, swap=swap)
        if error:
            line["game_error"] = error
        write_lines(d / name, [line])

    def empty(d, name):  # a game that crashed before its line
        write_text(d / name, "")

    def case_no_eval_line(d):
        game(d, "game-7-1000.jsonl"), game(d, "game-7-2000.jsonl")
        write_text(d / "game-7-0.log", crash)

    def case_names_an_absent_file(d):
        game(d, "game-7-1000.jsonl")
        write_text(d / "game-7-0.log", names("game-7-9999.jsonl") + crash)

    def case_names_the_second_game_file(d):
        game(d, "game-7-1000.jsonl"), game(d, "game-7-2000.jsonl")
        write_text(d / "game-7-0.log", names("game-7-2000.jsonl") + crash)

    def case_the_first_file_has_no_game_line(d):
        empty(d, "game-7-1000.jsonl"), game(d, "game-7-2000.jsonl")
        write_text(d / "game-7-0.log", names("game-7-2000.jsonl") + crash)

    def case_the_second_file_has_no_game_line(d):
        game(d, "game-7-1000.jsonl"), empty(d, "game-7-2000.jsonl")
        write_text(d / "game-7-0.log", names("game-7-2000.jsonl") + crash)

    def case_the_other_swap_log_names_the_file(d):
        game(d, "game-7-1000.jsonl")
        write_text(d / "game-7-0.log", "clean")
        write_text(d / "game-7-1.log", names("game-7-1000.jsonl") + crash)

    def case_a_game_error_and_a_crash_in_the_sibling(d):
        game(d, "game-7-1000.jsonl", error="x"), game(d, "game-7-2000.jsonl")
        write_text(d / "game-7-0.log", names("game-7-2000.jsonl") + crash)

    table = [
        ("a log with no EVAL line is the first file's, once", case_no_eval_line, ["game-7-1000.jsonl"]),
        ("a log naming an absent file stays with its pair", case_names_an_absent_file, ["game-7-1000.jsonl"]),
        ("a log naming the second game file", case_names_the_second_game_file, ["game-7-2000.jsonl"]),
        ("the first file has no game line", case_the_first_file_has_no_game_line, ["game-7-2000.jsonl"]),
        ("the second file has no game line", case_the_second_file_has_no_game_line, ["game-7-2000.jsonl"]),
        ("a log of the other swap, unpaired, names the file", case_the_other_swap_log_names_the_file,
         ["game-7-1000.jsonl"]),
        ("a game_error file and a crash in its sibling", case_a_game_error_and_a_crash_in_the_sibling,
         ["game-7-1000.jsonl", "game-7-2000.jsonl"]),
    ]
    for name, setup, want in table:
        with tempfile.TemporaryDirectory() as d:
            setup(Path(d))
            c = da.build_report([Path(d)])["combined"]
            got = sorted(Path(f).name for f in c["game_crash_files"])
            check("crash owner: " + name, got == want and c["game_crash_count"] == len(want), "%s != %s" % (got, want))


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


def test_has_choice_tag_accepts_non_digit_reply_shapes():
    """Regression for iter-1's 14/14 false-positive parse_fail_by_model count:
    the tag-presence check must not require a digit immediately after
    CHOICE:/PICKS:/BLOCKS: — declareBlockers's real reply format is card NAME
    pairs (never digits), and a legitimate empty PICKS: (decideMany's "decline
    everything", min==0) has no digits either. Both were successfully parsed
    by the harness (fallback=None) in the real iter-1 corpus; a genuinely
    tag-less reply must still be flagged."""
    # BLOCKS: name->name pairs (declareBlockers's actual wire format) -> tag present.
    check("BLOCKS name-pair reply recognized",
          da.has_choice_tag(dec(dtype="blockers",
              raw_reply="Trading is good here.\n\nBLOCKS: Cat Warrior Token->Guide of Souls")))
    check("BLOCKS: NONE recognized", da.has_choice_tag(dec(dtype="blockers", raw_reply="BLOCKS: NONE")))
    # PICKS: with zero picks (legitimate decline, e.g. attackers/surveil) -> tag present.
    check("empty PICKS: (decline) recognized",
          da.has_choice_tag(dec(dtype="attackers", raw_reply="Holding back is correct here.\n\nPICKS:")))
    check("PICKS: with a placeholder and no digits recognized",
          da.has_choice_tag(dec(dtype="surveil", raw_reply="Bin this one.\n\nPICKS: _")))
    # A genuinely tag-less reply (never had CHOICE:/PICKS:/BLOCKS: at all) must
    # still be caught — this is the real no-CHOICE parse-fail case.
    check("truly tag-less reply still flagged",
          not da.has_choice_tag(dec(dtype="play", raw_reply="I will just cast the spell")))


def test_blocks_and_empty_picks_not_counted_as_parse_fail():
    """End-to-end: a full report over these reply shapes must NOT count them
    in parse_fail_by_model (they're real, successfully-parsed decisions —
    fallback=None, tier=smart — not model formatting failures)."""
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        write_lines(dpath / "game-5-5000.jsonl", [
            game_line(seed=5, swap=False),
            dec(seat="model-a", dtype="blockers", tier="smart", n_options=2, choice=1,
                raw_reply="Good trade.\n\nBLOCKS: Cat Warrior Token->Guide of Souls"),
            dec(seat="model-a", dtype="attackers", tier="smart", n_options=1, choice=0,
                raw_reply="Holding back is correct here.\n\nPICKS:"),
        ])
        report = da.build_report([dpath])
        c = report["combined"]
        check("no parse-fail counted for model-a", c["parse_fail_by_model"].get("model-a", 0) == 0)


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


def test_shadow_choice_field_invisible_to_merge_gate():
    """D4: the additive shadow_choice field on the student's decision row must be
    invisible to the merge gate. It adds no rows (a field, never a sibling row),
    so ai_tier_count stays 0 and the log-vs-jsonl silent-drop reconciliation is
    untouched — this is the schema constraint that dictated field-not-row."""
    # model_consulted keys on prompt ONLY — shadow_choice never flips it.
    check("prompt-less row with shadow_choice is NOT consulted",
          da.model_consulted({"prompt": None, "shadow_choice": 2}) is False)
    check("consulted row unchanged by shadow_choice",
          da.model_consulted({"prompt": "P", "shadow_choice": 2}) is True)
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        # The clean-game fixture, but every model row ALSO carries shadow_choice.
        write_lines(dpath / "game-1-1000.jsonl", [
            game_line(seed=1, swap=False),
            dec(seat="model-a", dtype="mulligan", tier="smart", n_options=2, choice=0,
                raw_reply="fine hand\nKEEP", shadow_choice=1),
            dec(seat="model-a", dtype="play", tier="smart", n_options=3, choice=1,
                shadow_choice=0),
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
        check("shadow run: ai_tier_count still 0", c["ai_tier_count"] == 0)
        check("shadow run: silent_drop_total still 0", c["silent_drop_total"] == 0)
        check("shadow run: hard invariants still pass",
              report["hard_invariants"]["pass"] is True)


def test_one_candidate_in_a_subset_decision_is_not_a_forced_pick():
    """A subset decision over one candidate has two outcomes (attack or not, keep or not). Flagging
    those as forced picks is what led the harness to take them from the model."""
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        write_lines(dpath / "game-9-9000.jsonl", [
            game_line(seed=9, swap=False),
            dec(seat="model-a", dtype="mulligan", tier="smart", n_options=2, choice=0, raw_reply="KEEP"),
            dec(seat="model-a", dtype="attackers", tier="smart", n_options=1, choice=0, raw_reply="PICKS: none"),
            dec(seat="model-a", dtype="blockers", tier="smart", n_options=1, choice=1, raw_reply="BLOCKS: none"),
            dec(seat="model-a", dtype="surveil", tier="smart", n_options=1, choice=1, raw_reply="PICKS: 1"),
            dec(seat="model-a", dtype="scry", tier="smart", n_options=1, choice=0, raw_reply="PICKS: none"),
            # A pick-exactly-one menu with one option asked of the model IS still a mislabel.
            dec(seat="model-a", dtype="target", tier="smart", n_options=1, choice=0, raw_reply="CHOICE: 1"),
        ])
        c = da.build_report([dpath])["combined"]
        check("subset decisions with one candidate are not forced-pick mislabels",
              [e["type"] for e in c["forced_pick_mislabels"]["examples"]] == ["target"])


def test_decisions_lost_to_a_dead_bridge_fail_the_invariants():
    """Issue #369: a 402 game printed overall=PASS while its own fallback table listed play(bridge-down) 260.
    The game line's count fails the invariants; a line from before it is judged by the transport failures in its
    paired log, never by (bridge-down) rows (a model's non-answer writes those too); a count of 0 passes."""
    def run(line, rows, log=None):
        with tempfile.TemporaryDirectory() as d:
            write_lines(Path(d) / "game-3-1000.jsonl", rows + [line])
            if log is not None:
                write_text(Path(d) / "game-3-0.log", log)
            return da.build_report([Path(d)])
    rows = [dec(seat="model-a", dtype="mulligan", n_options=2, choice=0, raw_reply="KEEP"),
            dec(seat="model-a", tier="auto", prompt=None, raw_reply=None, fallback="play(bridge-down)->PASS")]
    log_402 = "\n".join(["[OPENROUTER model-a/smart] HTTP 402: credits",
                         "[model-a] [BRIDGE model-a/smart] no reply (attempt 2/3) -> restarting session",
                         "[OPENROUTER model-a/smart] HTTP 402: credits",
                         "[model-a] [BRIDGE model-a/smart] no reply (attempt 3/3) -> restarting session",
                         "[OPENROUTER model-a/smart] HTTP 402: credits"])
    log_empty = log_402.replace("HTTP 402: credits", "non-answer -> treating as bridge-down")
    dead = dict(game_line(seed=3), bridge_down_decisions_a=21, bridge_down_decisions_b=0)
    r = run(dead, rows[:1])
    check("the game line's count is the invariant", r["hard_invariants"].get("bridge_down_total") == 21,
          str(r["hard_invariants"]))
    check("and fails it", r["hard_invariants"]["pass"] is False)
    check("the text says so", "bridge_down_total=21" in da.render_text(r))
    r = run(game_line(seed=3), rows, log_402)
    check("an old line is judged by its log's transport failures",
          r["hard_invariants"].get("bridge_down_total") == 1, str(r["hard_invariants"]))
    check("and fails", r["hard_invariants"]["pass"] is False)
    r = run(game_line(seed=3), rows, log_empty)
    check("an old line whose model answered with nothing passes", r["hard_invariants"].get("bridge_down_ok") is True,
          str(r["hard_invariants"]))
    r = run(dict(game_line(seed=3), bridge_down_decisions_a=0, bridge_down_decisions_b=0), rows, log_402)
    check("a line whose count is 0 passes", r["hard_invariants"]["pass"] is True, str(r["hard_invariants"]))
    # Round-5 review of #374: a log naming a third game file is neither game's; the analyzer must agree with aggregate.
    with tempfile.TemporaryDirectory() as d:
        write_lines(Path(d) / "game-7-1000.jsonl", [game_line(seed=7, swap=False)])
        write_lines(Path(d) / "game-7-2000.jsonl", [game_line(seed=7, swap=True)])
        write_text(Path(d) / "game-7-0.log", "[EVAL] jsonl game-7-3000.jsonl\n" + log_402)
        r = da.build_report([Path(d)])
        check("a log that names another game file is not judged against these games",
              r["hard_invariants"].get("bridge_down_total") == 0, str(r["hard_invariants"]))


def test_a_puzzle_attempt_a_dead_bridge_played_fails_the_invariants():
    """Issue #378: a puzzle directory has no game line; its result rows carry the count (bridge_down_decisions)."""
    with tempfile.TemporaryDirectory() as d:
        write_lines(Path(d) / "game-1-1000.jsonl", [dec(seat="Solver", dtype="mulligan", n_options=2, choice=0,
                                                        raw_reply="KEEP")])
        write_lines(Path(d) / "puzzle-results.jsonl", [{"id": "PP20", "seed": 1, "status": "failed",
                                                         "bridge_down_decisions": 11},
                                                        {"id": "PP20", "seed": 2, "status": "failed",
                                                         "bridge_down_decisions": 0}])
        r = da.build_report([Path(d)])
        check("the puzzle row's count is the invariant", r["hard_invariants"].get("bridge_down_total") == 11,
              str(r["hard_invariants"]))
        check("and fails it", r["hard_invariants"]["pass"] is False)
        check("the text names the attempt", "PP20 seed 1" in da.render_text(r), da.render_text(r))


def test_a_game_whose_log_failed_to_write_fails_the_invariants():
    """Issue #427: the game line (or a puzzle row) says a row of its log failed to write; that run may be cut."""
    with tempfile.TemporaryDirectory() as d:
        write_lines(Path(d) / "game-1-1000.jsonl", [game_line(seed=1), ])
        write_lines(Path(d) / "game-2-1000.jsonl", [dict(game_line(seed=2), eval_log_write_failed=True)])
        write_lines(Path(d) / "puzzle-results.jsonl", [{"id": "PP20", "seed": 1, "status": "failed",
                                                         "eval_log_write_failed": True}])
        r = da.build_report([Path(d)])
        hi = r["hard_invariants"]
        check("the game and the puzzle row are counted", hi.get("eval_log_write_failed_total") == 2, str(hi))
        check("and fail the run", hi["pass"] is False)
        check("the text says so", "eval_log_write_failed_total=2" in da.render_text(r))
    with tempfile.TemporaryDirectory() as d:
        write_lines(Path(d) / "game-1-1000.jsonl", [dict(game_line(seed=1), eval_log_write_failed=False)])
        hi = da.build_report([Path(d)])["hard_invariants"]
        check("a whole log passes", hi.get("eval_log_write_failed_total") == 0 and hi["pass"] is True, str(hi))


def test_the_standards_name_every_hard_invariant():
    """docs/STANDARDS.md section 4 lists the run oracle's hard invariants; a new one must be named there."""
    with tempfile.TemporaryDirectory() as d:
        write_lines(Path(d) / "game-1-1000.jsonl", [game_line(seed=1)])
        hi = da.build_report([Path(d)])["hard_invariants"]
    names = sorted(k for k in hi if k not in ("pass",) and not k.endswith("_ok"))
    text = " ".join((evalpath.REPO / "docs" / "STANDARDS.md").read_text(encoding="utf-8").split())
    check("STANDARDS names every hard invariant", all("`%s=0`" % n in text for n in names), str(names))


def main():
    test_the_standards_name_every_hard_invariant()
    test_decisions_lost_to_a_dead_bridge_fail_the_invariants()
    test_a_puzzle_attempt_a_dead_bridge_played_fails_the_invariants()
    test_a_game_whose_log_failed_to_write_fails_the_invariants()
    test_one_candidate_in_a_subset_decision_is_not_a_forced_pick()
    test_parse_log_text()
    test_normalize_fallback_matches_analyze_corpus_convention()
    test_clean_game_no_defects()
    test_silent_drop_detected()
    test_ai_tier_forced_pick_and_missing_mulligan()
    test_has_choice_tag_accepts_non_digit_reply_shapes()
    test_blocks_and_empty_picks_not_counted_as_parse_fail()
    test_malformed_lines_and_no_log_are_tolerated()
    test_shadow_choice_field_invisible_to_merge_gate()
    test_crashed_game_fails_the_invariants()
    test_a_crash_only_its_log_shows_fails_the_invariants()
    test_a_crash_is_listed_under_the_one_file_that_owns_its_log()
    test_a_block_set_that_stood_illegal_is_counted()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        sys.exit(1)
    print("all tests passed")
    sys.exit(0)


class DefectAnalyzerSuite(unittest.TestCase):
    """The checks above predate unittest in this directory and were only run by hand; this makes the
    acceptance pytests stage (every eval/tests/test_*.py) run them."""

    def test_every_check(self):
        del FAILURES[:]
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
        self.assertEqual(FAILURES, [])


if __name__ == "__main__":
    main()
