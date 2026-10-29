#!/usr/bin/env python3
"""Self-test for process_metrics.py: synthetic JSONL fixtures + math checks.

Run: python3 eval/tests/test_process_metrics.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import process_metrics as pm  # noqa: E402

SCRIPT = str(evalpath.EVAL / "process_metrics.py")
FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def approx(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


# ------------------------------------------------------------- fixtures

def game_line(seed=1, swap=False, winner="A", deck_a="UR Delver", deck_b="Burn"):
    return {"t": "game", "seed": seed, "swap": swap,
            "deck_a": deck_a, "deck_b": deck_b,
            "controller_a": "llm", "controller_b": "ai",
            "smart_a": "student", "fast_a": "student",
            "smart_b": None, "fast_b": None,
            "winner": winner, "turns": 6, "win_condition": "life",
            "duration_ms": 60000}


def dec(seat="UR Delver", dtype="play", choice=0, opts=None, prompt="P",
        tier="smart", turn=1, phase="MAIN1", raw="CHOICE: 1", **extra):
    opts = opts if opts is not None else ["CAST X {R} [x]", "PASS (do nothing)"]
    d = {"t": "decision", "seat": seat, "turn": turn, "phase": phase,
         "type": dtype, "n_options": len(opts), "choice": choice, "tier": tier,
         "latency_ms": 100, "fallback": None, "options": opts, "prompt": prompt,
         "raw_reply": raw, "completion_tokens": None, "finish_reason": "stop"}
    d.update(extra)
    return d


def write_game(d, name, game, decisions):
    with open(Path(d) / name, "w") as f:
        for ln in decisions:
            f.write(json.dumps(ln) + "\n")
        if game is not None:
            f.write(json.dumps(game) + "\n")


# ----------------------------------------------------- state / choice keys

def test_chosen_option_text():
    c = pm.chosen_option_text
    check("chosen indexes options", c(dec(choice=1, opts=["X", "Y", "Z"])) == "Y")
    check("chosen collapses object ids",
          c(dec(choice=0, opts=["Mountain (28)"])) == "Mountain (#)")
    check("chosen -1 is PASS sentinel", c(dec(choice=-1, opts=["X"])) == "\x00PASS")
    check("chosen out-of-range -> None", c(dec(choice=9, opts=["X", "Y"])) is None)
    check("chosen bad choice -> None", c(dec(choice="x", opts=["X"])) is None)


def test_state_key_normalizes_ids():
    d1 = dec(prompt="board: Mountain (28)", opts=["CAST A (28)", "PASS"])
    d2 = dec(prompt="board: Mountain (84)", opts=["CAST A (84)", "PASS"])
    k1 = pm.state_key(d1, 1, False, "UR Delver")
    k2 = pm.state_key(d2, 1, False, "UR Delver")
    check("volatile ids collapse to one state hash", k1 == k2, "%s vs %s" % (k1, k2))
    check("state key None when prompt null",
          pm.state_key(dec(prompt=None), 1, False, "UR Delver") is None)
    # seed/seat namespacing: same text, different seed -> different key
    check("seed namespacing separates states",
          pm.state_key(d1, 1, False, "UR Delver")
          != pm.state_key(d1, 2, False, "UR Delver"))
    check("swap namespacing separates states",
          pm.state_key(d1, 1, False, "UR Delver")
          != pm.state_key(d1, 1, True, "UR Delver"))


def test_turn_bucket():
    check("mulligan turn 0 bucket", pm.turn_bucket(0) == "0-mull")
    check("bucket 1-3", pm.turn_bucket(3) == "1-3")
    check("bucket 4-6", pm.turn_bucket(6) == "4-6")
    check("bucket 7+", pm.turn_bucket(9) == "7+")
    check("bad turn bucket", pm.turn_bucket(None) == "unknown")


# --------------------------------------------------- metric 1: decision match

def test_decision_match_identical_is_ceiling():
    """Teacher-vs-itself: every eligible state matches with the same choice ->
    100% match rate and 100% coverage of eligible decisions (the sanity ceiling
    the design doc asks for)."""
    with tempfile.TemporaryDirectory() as d:
        decs = [dec(prompt="S1", opts=["CAST A", "PASS"], choice=0, turn=1),
                dec(prompt="S2", opts=["CAST B", "PASS"], choice=1, turn=2),
                dec(prompt="S3", opts=["CAST C", "PASS"], choice=0, turn=5,
                    tier="fast"),
                # a forced auto row (prompt null): counts in total, not eligible
                dec(prompt=None, tier="auto", choice=0, dtype="mulligan",
                    opts=["KEEP", "MULLIGAN"], turn=0)]
        write_game(d, "game-1-1000.jsonl", game_line(seed=1), decs)
        entries, seats, players, _, _ = pm.load_dir(d)
        idx = pm.build_teacher_index(entries, seats)
        m = pm.decision_match(entries, seats, idx)
        check("identical: match_rate 100%", approx(m["match_rate"], 1.0), str(m))
        check("identical: coverage of eligible 100%",
              approx(m["coverage_vs_eligible"], 1.0), str(m))
        check("identical: total counts auto row", m["total_decisions"] == 4, str(m))
        check("identical: eligible excludes null-prompt row",
              m["eligible_decisions"] == 3, str(m))
        check("identical: matched == eligible", m["matched_states"] == 3, str(m))
        check("identical: fast tier bucketed",
              m["by_tier"]["fast"]["matched"] == 1, str(m["by_tier"]))


def test_decision_match_divergence_and_mismatch():
    """A novel student state stays eligible-but-unmatched (coverage < 1); a
    matched state answered differently lowers match rate below 1."""
    with tempfile.TemporaryDirectory() as td:
        teach = Path(td) / "teacher"
        stud = Path(td) / "student"
        teach.mkdir(); stud.mkdir()
        write_game(teach, "game-1-1000.jsonl", game_line(seed=1), [
            dec(prompt="S1", opts=["CAST A", "PASS"], choice=0, turn=1),
            dec(prompt="S2", opts=["CAST B", "PASS"], choice=0, turn=2)])
        write_game(stud, "game-1-1000.jsonl", game_line(seed=1), [
            dec(prompt="S1", opts=["CAST A", "PASS"], choice=0, turn=1),   # match
            dec(prompt="S2", opts=["CAST B", "PASS"], choice=1, turn=2),   # mismatch
            dec(prompt="S9", opts=["CAST Z", "PASS"], choice=0, turn=3)])  # novel
        t_entries, t_seats, _, _, _ = pm.load_dir(teach)
        s_entries, s_seats, _, _, _ = pm.load_dir(stud)
        idx = pm.build_teacher_index(t_entries, t_seats)
        m = pm.decision_match(s_entries, s_seats, idx)
        check("eligible=3", m["eligible_decisions"] == 3, str(m))
        check("matched=2 (S1,S2; S9 novel)", m["matched_states"] == 2, str(m))
        check("hits=1 (only S1)", m["hits"] == 1, str(m))
        check("match_rate=50%", approx(m["match_rate"], 0.5), str(m))
        check("coverage of eligible = 2/3", approx(m["coverage_vs_eligible"], 2 / 3), str(m))


def test_decision_match_pairs_by_seed():
    """Same state text on a different seed must NOT join (states are namespaced
    by seed/swap/seat, not blindly by content across games)."""
    with tempfile.TemporaryDirectory() as td:
        teach = Path(td) / "t"; stud = Path(td) / "s"
        teach.mkdir(); stud.mkdir()
        write_game(teach, "game-1-1000.jsonl", game_line(seed=1),
                   [dec(prompt="S1", opts=["CAST A", "PASS"], choice=0)])
        write_game(stud, "game-2-1000.jsonl", game_line(seed=2),
                   [dec(prompt="S1", opts=["CAST A", "PASS"], choice=0)])
        t_entries, t_seats, _, _, _ = pm.load_dir(teach)
        s_entries, s_seats, _, _, _ = pm.load_dir(stud)
        idx = pm.build_teacher_index(t_entries, t_seats)
        m = pm.decision_match(s_entries, s_seats, idx)
        check("cross-seed does not match", m["matched_states"] == 0, str(m))


# --------------------------------------------- metric 2: quality counters

def test_quality_mulligan_and_durdle():
    with tempfile.TemporaryDirectory() as d:
        decs = [
            # mulligans: 3 auto (keep,keep,mull) + 1 smart keep -> keep 3/4
            dec(dtype="mulligan", tier="auto", choice=0, opts=["KEEP", "MULLIGAN"],
                prompt=None, turn=0),
            dec(dtype="mulligan", tier="auto", choice=0, opts=["KEEP", "MULLIGAN"],
                prompt=None, turn=0),
            dec(dtype="mulligan", tier="auto", choice=1, opts=["KEEP", "MULLIGAN"],
                prompt=None, turn=0),
            dec(dtype="mulligan", tier="smart", choice=0, opts=["KEEP", "MULLIGAN"],
                prompt="mp", turn=0),
            # durdle: main-phase play with a CAST option, chose land -> durdle
            dec(dtype="play", phase="MAIN1", choice=1,
                opts=["CAST Bolt {R} [x]", "PLAY LAND Island", "PASS (do nothing)"]),
            # main-phase play with CAST, chose the cast -> not durdle
            dec(dtype="play", phase="MAIN1", choice=0,
                opts=["CAST Bolt {R} [x]", "PASS (do nothing)"]),
            # main-phase play with NO castable spell -> not counted at all
            dec(dtype="play", phase="MAIN1", choice=0,
                opts=["PLAY LAND Island", "PASS (do nothing)"]),
        ]
        write_game(d, "game-1-1000.jsonl", game_line(), decs)
        entries, seats, _, _, _ = pm.load_dir(d)
        q = pm.quality_counters(entries, seats)
        check("mulligan n=4", q["mulligan"]["n"] == 4, str(q["mulligan"]))
        check("mulligan keep-rate 0.75", approx(q["mulligan"]["keep_rate"], 0.75),
              str(q["mulligan"]))
        check("mulligan model-tier subset=1", q["mulligan"]["n_model_tier"] == 1,
              str(q["mulligan"]))
        check("durdle den=2 (only castable mains)",
              q["durdle"]["castable_main_phases"] == 2, str(q["durdle"]))
        check("durdle num=1", q["durdle"]["land_or_pass"] == 1, str(q["durdle"]))
        check("durdle rate 0.5", approx(q["durdle"]["durdle_rate"], 0.5),
              str(q["durdle"]))


def test_quality_fetch_and_format():
    fetch = ("CAST Scalding Tarn [{T}, Pay 1 life, Sacrifice CARDNAME: Search "
             "your library for a Island or Mountain card, put it onto the "
             "battlefield, then shuffle.]")
    with tempfile.TemporaryDirectory() as d:
        decs = [
            # fetch available, cracked
            dec(dtype="play", choice=0, opts=[fetch, "PASS (do nothing)"],
                raw="PLAN: fix\nCHOICE: 1"),
            # fetch available, held (chose pass)
            dec(dtype="play", choice=1, opts=[fetch, "PASS (do nothing)"],
                raw="CHOICE: 2"),
            # a reply missing the CHOICE line -> format not ok
            dec(dtype="play", choice=0, opts=["CAST X {R} [x]", "PASS"],
                raw="I think we should attack"),
        ]
        write_game(d, "game-1-1000.jsonl", game_line(), decs)
        entries, seats, _, _, _ = pm.load_dir(d)
        q = pm.quality_counters(entries, seats)
        check("fetch available=2", q["fetch"]["fetch_available"] == 2, str(q["fetch"]))
        check("fetch cracked=1", q["fetch"]["cracked"] == 1, str(q["fetch"]))
        check("fetch crack-rate 0.5", approx(q["fetch"]["crack_rate"], 0.5),
              str(q["fetch"]))
        check("format-ok 2/3", approx(q["reply"]["format_ok_rate"], 2 / 3),
              str(q["reply"]))
        check("plan-line 1/3", approx(q["reply"]["plan_line_rate"], 1 / 3),
              str(q["reply"]))


# --------------------------------------------- metric 3: plan adherence

def test_your_board_parsing():
    prompt = ("== Turn 3 MAIN1 ==\nYOU [UR Delver] life 20 | poison 0/10\n"
              "  hand(2): Daze, Bolt\n  board: Dragon's Rage Channeler(3/3)\n"
              "OPP [Burn] life 20\n  board: Monastery Swiftspear(1/2)\n"
              "stack: (empty)")
    yb = pm.your_board(prompt)
    check("your_board reads YOU board only", "Dragon's Rage Channeler" in yb, yb)
    check("your_board excludes OPP board", "Monastery Swiftspear" not in yb, yb)


def test_plan_adherence():
    board_threat = ("YOU [UR Delver] life 20\n  hand(1): Daze\n"
                    "  board: Dragon's Rage Channeler(3/3)\nOPP [Burn] life 20\n"
                    "  board: (none)\nstack: (empty)")
    board_none = ("YOU [UR Delver] life 20\n  board: (none)\n"
                  "OPP [Burn] life 20\n  board: Murktide Regent(4/4)\nstack: (empty)")
    with tempfile.TemporaryDirectory() as td:
        # game 1: deploys a threat turn 2, uses protection with threat on board,
        # a burn to the face and a burn to a creature
        g1 = [
            dec(dtype="play", turn=2, choice=0,
                opts=["CAST Delver of Secrets {U} [x]", "PASS"]),
            dec(dtype="play", turn=4, choice=0,
                opts=["CAST Daze {U} [x]", "PASS"], prompt=board_threat),   # protect used
            dec(dtype="play", turn=5, choice=1,
                opts=["CAST Daze {U} [x]", "PASS"], prompt=board_threat),   # opp, not used
            dec(dtype="target", turn=4, choice=1,
                opts=["UR Delver", "Burn", "Monastery Swiftspear (1/2)[T]"]),  # face
            dec(dtype="target", turn=6, choice=2,
                opts=["UR Delver", "Burn", "Monastery Swiftspear (1/2)[T]"]),  # creature
        ]
        write_game(td, "game-1-1000.jsonl", game_line(seed=1), g1)
        # game 2: never deploys a threat; a protection option exists but no
        # threat on our board -> not an opportunity
        g2 = [dec(dtype="play", turn=3, choice=1,
                  opts=["CAST Daze {U} [x]", "PASS"], prompt=board_none)]
        write_game(td, "game-2-1000.jsonl", game_line(seed=2), g2)
        entries, seats, players, _, _ = pm.load_dir(td)
        p = pm.plan_adherence(entries, seats, players)
        ft = p["first_threat_turn"]
        check("first threat turn min=2", ft["min"] == 2, str(ft))
        check("games with threat=1", ft["games_with_threat"] == 1, str(ft))
        check("games without threat=1", ft["games_without_threat"] == 1, str(ft))
        pu = p["protection_usage"]
        check("protection opportunities=2 (threat on board only)",
              pu["opportunities"] == 2, str(pu))
        check("protection used=1", pu["used"] == 1, str(pu))
        check("protection rate 0.5", approx(pu["usage_rate"], 0.5), str(pu))
        bs = p["burn_target_split"]
        check("burn face=1", bs["face"] == 1, str(bs))
        check("burn creature=1", bs["creature"] == 1, str(bs))
        check("burn face-rate 0.5", approx(bs["face_rate"], 0.5), str(bs))


# ------------------------------------------------- loader / hygiene / CLI

def test_incomplete_games_skipped():
    """A file with no game-summary line (a live-batch in-flight file) is skipped
    entirely; only complete games load."""
    with tempfile.TemporaryDirectory() as d:
        write_game(d, "game-1-1000.jsonl", game_line(seed=1), [dec()])
        write_game(d, "game-2-1000.jsonl", None, [dec(seed=2)])  # no game line
        entries, seats, _, _, n = pm.load_dir(d)
        check("only complete game loaded", n == 1, str(n))
        check("llm seat detected", seats == {"UR Delver"}, str(seats))


def test_cli_smoke_and_json():
    """CLI runs teacher-vs-itself, exits 0, prints the report and matches ~100%
    in JSON."""
    with tempfile.TemporaryDirectory() as d:
        write_game(d, "game-1-1000.jsonl", game_line(seed=1), [
            dec(prompt="S1", opts=["CAST A", "PASS"], choice=0),
            dec(prompt="S2", opts=["CAST B", "PASS"], choice=1, turn=2)])
        jf = str(Path(d) / "out.json")
        r = subprocess.run([sys.executable, SCRIPT, d, "--teacher", d,
                            "--json", jf], capture_output=True, text=True)
        check("cli exits 0", r.returncode == 0, r.stderr)
        check("cli prints match section", "TEACHER DECISION-MATCH" in r.stdout,
              r.stdout[:200])
        blob = json.loads(Path(jf).read_text())
        check("cli json match_rate 100%",
              approx(blob["decision_match"]["match_rate"], 1.0),
              str(blob["decision_match"]))


# --------------------------------------------------------------------- run

def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print()
    if FAILURES:
        print("FAILED %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
