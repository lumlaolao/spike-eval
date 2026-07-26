#!/usr/bin/env python3
"""Self-test for make_sft_data.py: synthetic mini-jsonl fixtures + logic checks.

Run: python3 eval/test_make_sft_data.py
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_sft_data as m  # noqa: E402

FAILURES = []


@contextlib.contextmanager
def stage0_pin_aligned(pb):
    """Point STAGE0's STATIC_REFERENCE pin at the working tree for one block.

    Stage-1 fixtures tag synthetic games "STAGE0" to exercise split/dedupe/write
    plumbing — they are not reconstructing real STAGE0 corpora. system() refuses
    when the tree has drifted from what STAGE0 runs saw (it has, since task #8
    grew STATIC_REFERENCE 7492 -> 8154), so these tests state explicitly that
    they accept whatever text the tree currently holds. Production paths keep
    the strict guard.
    """
    saved = m.BUILDS["STAGE0"]["static_reference_len"]
    m.BUILDS["STAGE0"]["static_reference_len"] = len(pb.static_reference)
    try:
        yield
    finally:
        m.BUILDS["STAGE0"]["static_reference_len"] = saved


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ------------------------------------------------------------- fixtures

def game_line(seed, swap, winner, deck_a="UR Delver", deck_b="Burn",
              ctrl_a="llm", ctrl_b="ai", plan_a="tempo plan",
              smart_a="deepseek", fast_a="deepseek"):
    return {"t": "game", "seed": seed, "swap": swap,
            "deck_a": deck_a, "deck_b": deck_b,
            "controller_a": ctrl_a, "controller_b": ctrl_b,
            "smart_a": smart_a, "smart_b": "opus",
            "fast_a": fast_a, "fast_b": "haiku",
            "winner": winner, "turns": 9, "win_condition": "life",
            "duration_ms": 1000, "plan_a": plan_a, "plan_b": "burn plan",
            "engine_sha": "eng", "overlay_sha": "ov", "deck_sha": "dk"}


def decision(seat, tier="smart", dtype="play", prompt="P", reply="CHOICE: 1",
             choice=0, fallback=None, finish_reason="stop"):
    return {"t": "decision", "seat": seat, "turn": 3, "phase": "MAIN1",
            "type": dtype, "n_options": 2, "choice": choice, "tier": tier,
            "latency_ms": 10, "fallback": fallback, "options": ["a", "b"],
            "prompt": prompt, "raw_reply": reply, "finish_reason": finish_reason}


def write_game(d, name, game, decisions):
    with open(Path(d) / name, "w", encoding="utf-8") as f:
        for dd in decisions:
            f.write(json.dumps(dd) + "\n")
        if game is not None:
            f.write(json.dumps(game) + "\n")


# --------------------------------------------------- preamble reconstruction

def test_preamble_reconstruction():
    """The real Java source must reconstruct to the recon's verified lengths."""
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    plan_a = ("Izzet Delver tempo: land a cheap threat early (Delver of Secrets, "
              "Dragon's Rage Channeler, Ragavan, Murktide Regent), then protect it "
              "and race while denying their mana. Counter their key spells with Daze, "
              "Force of Will, Spell Pierce; burn their blockers and face with "
              "Lightning Bolt/Chain Lightning; Wasteland their duals. You are the "
              "beatdown when ahead on board — count clock vs their outs before trading.")
    check("plan_a is the expected 407 chars", len(plan_a) == 407,
          "got %d" % len(plan_a))
    base = pb.base("UR Delver", plan_a, terse=False)
    check("base(verbose) == 1762 chars", len(base) == 1762, "got %d" % len(base))
    # The working tree drifted past STAGE0's STATIC_REFERENCE (task #8 added the
    # OPP CAST / OPP LANDS lines), so reconstructing STAGE0 smart-tier messages
    # from it must REFUSE rather than append text those runs never saw.
    stage0_len = m.BUILDS["STAGE0"]["static_reference_len"]
    if len(pb.static_reference) != stage0_len:
        try:
            pb.system("UR Delver", plan_a, "smart", "STAGE0")
            check("STAGE0 smart refuses on STATIC_REFERENCE drift", False,
                  "no SystemExit raised")
        except SystemExit as e:
            check("STAGE0 smart refuses on STATIC_REFERENCE drift",
                  "refusing to reconstruct" in str(e), str(e))
    # Exercise the append mechanism itself against whatever the tree now holds.
    with stage0_pin_aligned(pb):
        smart = pb.system("UR Delver", plan_a, "smart", "STAGE0")
    check("STAGE0 smart system == base + blank line + STATIC_REFERENCE",
          len(smart) == len(base) + 2 + len(pb.static_reference),
          "got %d" % len(smart))
    check("STAGE0 smart contains STATIC_REFERENCE",
          "SNAPSHOT LEGEND" in smart and "LEGACY GLOSSARY" in smart)
    fix_smart = pb.system("UR Delver", plan_a, "smart", "FIX")
    check("FIX smart == base (no STATIC_REFERENCE)", fix_smart == base)
    fast = pb.system("UR Delver", plan_a, "fast", "STAGE0")
    check("fast never carries STATIC_REFERENCE", "SNAPSHOT LEGEND" not in fast)
    check("fast uses terse FORMAT line",
          "NO reasoning, no other text" in fast)
    check("smart uses verbose FORMAT line",
          "two short sentences of reasoning" in smart)
    check("plan is interpolated verbatim", "GAME PLAN: " + plan_a in base)
    check("seat is interpolated verbatim", 'piloting "UR Delver"' in base)
    # plan-free arm
    noplan = pb.base("UR Delver", "", terse=False)
    check("empty plan omits the GAME PLAN block", "GAME PLAN:" not in noplan)


# --------------------------------------------------- complete-game filter

def test_complete_game_filter():
    with tempfile.TemporaryDirectory() as d:
        # complete: has a game line
        write_game(d, "game-1-1000.jsonl", game_line(1, False, "A"),
                   [decision("UR Delver")])
        # in-flight: decisions but NO game line -> must be skipped entirely
        write_game(d, "game-2-1000.jsonl", None,
                   [decision("UR Delver"), decision("UR Delver")])
        games, warnings = m.load_complete_games(d)
        seeds = sorted(g["game"]["seed"] for g in games)
        check("only complete games are loaded", seeds == [1],
              "got seeds %s" % seeds)
        check("in-flight file left no game record", len(games) == 1)


def test_rerun_dedup_keeps_newest():
    with tempfile.TemporaryDirectory() as d:
        write_game(d, "game-5-1000.jsonl", game_line(5, False, "A"),
                   [decision("UR Delver", reply="OLD")])
        write_game(d, "game-5-2000.jsonl", game_line(5, False, "A"),
                   [decision("UR Delver", reply="NEW")])
        games, warnings = m.load_complete_games(d)
        check("rerun (seed,swap) deduped to one", len(games) == 1,
              "got %d" % len(games))
        check("newest file kept", games[0]["file"] == "game-5-2000.jsonl",
              games[0]["file"])


# --------------------------------------------------- winning-seat filter

def test_winning_seat_filter():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        # WIN for LLM (identity A won): its rows should be emitted; the Forge-AI
        # 'Burn' seat logs no smart/fast rows, but add a stray one to prove the
        # seat filter drops non-LLM seats.
        write_game(d, "game-1-1000.jsonl", game_line(1, False, "A"),
                   [decision("UR Delver", prompt="win-state", reply="R1"),
                    decision("Burn", prompt="opp-state", reply="R2"),
                    decision("UR Delver", tier="fast", prompt="f", reply="CHOICE: 2")])
        # LOSS for LLM (identity B won): none of its rows should be emitted.
        write_game(d, "game-2-1000.jsonl", game_line(2, False, "B"),
                   [decision("UR Delver", prompt="lost-state", reply="R3")])
        with stage0_pin_aligned(pb):
            examples, warnings, hygiene = m.build_examples([(d, "STAGE0")], pb)
        prompts = sorted(e["messages"][1]["content"] for e in examples)
        check("only winning-seat LLM rows kept", prompts == ["f", "win-state"],
              "got %s" % prompts)
        check("losing game contributes nothing",
              all("lost" not in p for p in prompts))
        check("opponent (non-LLM) seat dropped",
              all("opp" not in p for p in prompts))
        tiers = sorted(e["meta"]["tier"] for e in examples)
        check("both smart and fast tiers captured", tiers == ["fast", "smart"])
        # model provenance follows the tier column
        by_tier = {e["meta"]["tier"]: e["meta"]["model"] for e in examples}
        check("smart model = smart_a", by_tier["smart"] == "deepseek")
        check("fast model = fast_a", by_tier["fast"] == "deepseek")


def test_hygiene_drops():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        write_game(d, "game-1-1000.jsonl", game_line(1, False, "A"), [
            decision("UR Delver", prompt="good", reply="ok"),
            decision("UR Delver", prompt="empty", reply="   "),      # no reply
            decision("UR Delver", prompt="fb", reply="x", fallback="cli-timeout"),
            decision("UR Delver", prompt="trunc", reply="x", finish_reason="length"),
            decision("UR Delver", tier="auto", prompt="auto", reply="x"),  # not model tier
        ])
        with stage0_pin_aligned(pb):
            examples, warnings, hygiene = m.build_examples([(d, "STAGE0")], pb)
        kept = sorted(e["messages"][1]["content"] for e in examples)
        check("only the clean model-tier row survives", kept == ["good"],
              "got %s" % kept)
        check("no-reply counted", hygiene["no_reply"] == 1)
        check("fallback counted", hygiene["fallback"] == 1)
        check("truncated counted", hygiene["truncated"] == 1)


# --------------------------------------------------- dedup

def test_dedup_normalizes_object_ids():
    check("object id collapsed in key",
          m.normalize_state("Burn played Mountain (28)")
          == "Burn played Mountain (#)")
    check("second object id also collapsed",
          m.normalize_state("Swiftspear (4) deals 1")
          == "Swiftspear (#) deals 1")
    # semantic parentheticals must NOT be touched
    for keep in ["hand(8):", "Swiftspear(1/2)", "(need 1 more U)",
                 "(0 untapped sources)", "(no nonland permanents)"]:
        check("keeps semantic %r" % keep, m.normalize_state(keep) == keep)


def test_dedup_pairs():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        # two rows: identical state up to a volatile object id, identical reply
        # -> deduped to one. A third with a different reply survives.
        write_game(d, "game-1-1000.jsonl", game_line(1, False, "A"), [
            decision("UR Delver", tier="fast", prompt="board X (28)", reply="CHOICE: 1"),
            decision("UR Delver", tier="fast", prompt="board X (99)", reply="CHOICE: 1"),
            decision("UR Delver", tier="fast", prompt="board X (99)", reply="CHOICE: 2"),
        ])
        with stage0_pin_aligned(pb):
            examples, _, _ = m.build_examples([(d, "STAGE0")], pb)
        check("3 rows built pre-dedup", len(examples) == 3)
        kept, dropped = m.dedupe(examples)
        check("1 near-duplicate dropped", dropped == 1, "got %d" % dropped)
        check("2 survive dedup", len(kept) == 2, "got %d" % len(kept))


# --------------------------------------------------- split by game

def test_split_by_game():
    # 10 game keys -> val_frac 0.1 -> exactly 1 valid; deterministic.
    keys = [m.game_key("src", s) for s in range(100, 110)]
    a = m.assign_splits(keys, 0.1, 20260722)
    b = m.assign_splits(keys, 0.1, 20260722)
    check("split is deterministic", a == b)
    n_val = sum(1 for v in a.values() if v == "valid")
    check("10 games * 0.1 -> 1 valid", n_val == 1, "got %d" % n_val)
    # different seed can move the boundary but stays a valid partition
    c = m.assign_splits(keys, 0.1, 999)
    check("all keys assigned", set(c) == set(keys))
    check("every value is train or valid",
          all(v in ("train", "valid") for v in c.values()))


def test_split_keeps_both_swaps_together():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        # both swap variants of seed 1 are LLM wins -> same game key -> never
        # separated across the train/valid boundary.
        write_game(d, "game-1-1000.jsonl", game_line(1, False, "A"),
                   [decision("UR Delver", prompt="a")])
        write_game(d, "game-1-2000.jsonl", game_line(1, True, "A"),
                   [decision("UR Delver", prompt="b")])
        with stage0_pin_aligned(pb):
            examples, _, _ = m.build_examples([(d, "STAGE0")], pb)
        gkeys = {e["_gkey"] for e in examples}
        check("both swaps share one game key", len(gkeys) == 1,
              "got %s" % gkeys)


# --------------------------------------------------- end-to-end write

def test_end_to_end_write():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "src"
        src.mkdir()
        for seed in range(1, 11):
            write_game(src, "game-%d-1000.jsonl" % seed,
                       game_line(seed, False, "A"),
                       [decision("UR Delver", prompt="s%d" % seed,
                                 reply="CHOICE: %d" % seed)])
        with stage0_pin_aligned(pb):
            examples, warnings, hygiene = m.build_examples([(str(src), "STAGE0")], pb)
        examples, dropped = m.dedupe(examples)
        gkeys = {e["_gkey"] for e in examples}
        splits = m.assign_splits(gkeys, 0.1, 20260722)
        out = Path(d) / "out"
        train, valid, counts = m.write_outputs(
            out, examples, splits, ["eval/make_sft_data.py"],
            [(str(src), "STAGE0")], hygiene, dropped, warnings)
        check("train + valid == 10 examples", len(train) + len(valid) == 10)
        check("~10% held out", len(valid) == 1, "got %d" % len(valid))
        # data files parse as MLX chat format
        tl = (out / "train.jsonl").read_text().strip().splitlines()
        rec = json.loads(tl[0])
        roles = [msg["role"] for msg in rec["messages"]]
        check("chat format roles", roles == ["system", "user", "assistant"])
        # meta.jsonl aligns by line within each split block
        meta = [json.loads(x) for x in
                (out / "meta.jsonl").read_text().strip().splitlines()]
        check("meta has one row per example", len(meta) == 10)
        train_meta = [r for r in meta if r["split"] == "train"]
        check("meta train rows align to train.jsonl line count",
              len(train_meta) == len(train))
        check("meta line numbers are 1-based sequential",
              [r["line"] for r in train_meta] == list(range(1, len(train) + 1)))
        check("provenance carries build + tier + decision_index",
              all({"build", "tier", "decision_index", "seed", "source_file"}
                  <= set(r) for r in meta))
        check("README written", (out / "README.md").exists())
        readme = (out / "README.md").read_text()
        check("README records counts", "| train |" in readme)


# =========================================================================
# STAGE-2 (stage2-local-v0) multi-source builder tests
# =========================================================================

def ai_game(seed, swap, winner, deck_a="Elves", deck_b="Goblins"):
    return {"t": "game", "seed": seed, "swap": swap,
            "deck_a": deck_a, "deck_b": deck_b,
            "controller_a": "ai-log", "controller_b": "ai-log",
            "smart_a": "opus", "smart_b": "opus",
            "fast_a": "haiku", "fast_b": "haiku",
            "winner": winner, "turns": 10, "win_condition": "life",
            "duration_ms": 100, "plan_a": "elf plan", "plan_b": "gob plan",
            "engine_sha": "e", "overlay_sha": "o", "deck_sha": "d"}


def ai_dec(seat, dtype, choice, n_options, options, prompt="P"):
    return {"t": "decision", "seat": seat, "turn": 3, "phase": "MAIN1",
            "type": dtype, "n_options": n_options, "choice": choice,
            "tier": "ai-teacher", "latency_ms": 0, "fallback": None,
            "options": options, "prompt": prompt, "raw_reply": None,
            "finish_reason": None}


def _tgt(dtype, choice, n_options, options=None):
    return m.canonical_target({"type": dtype, "choice": choice,
                               "n_options": n_options,
                               "options": options or []})


# --------------------------------------------------- canonical targets

def test_canonical_target_single_choice():
    check("play choice 0 -> CHOICE: 1",
          _tgt("play", 0, 4, ["a", "b", "c", "PASS (do nothing)"]) == ("CHOICE: 1", "choice"))
    check("play PASS (choice -1) -> CHOICE: n_options",
          _tgt("play", -1, 3, ["CAST x", "CAST y", "PASS (do nothing)"]) == ("CHOICE: 3", "pass"))
    check("zone-change choice 1 -> CHOICE: 2",
          _tgt("zone-change", 1, 3)[0] == "CHOICE: 2")
    check("decideOne decline (choice -1, non-play) -> CHOICE: n_options+1",
          _tgt("zone-change", -1, 2) == ("CHOICE: 3", "decline"))
    check("targets choice 0 -> CHOICE: 1", _tgt("targets", 0, 2)[0] == "CHOICE: 1")


def test_canonical_target_mulligan():
    check("mulligan KEEP",
          _tgt("mulligan", 0, 2, ["KEEP", "MULLIGAN"]) == ("KEEP", "mulligan-word"))
    check("mulligan MULLIGAN",
          _tgt("mulligan", 1, 2, ["KEEP", "MULLIGAN"])[0] == "MULLIGAN")
    check("mulligan by convention when options absent",
          _tgt("mulligan", 0, 2)[0] == "KEEP")


def test_canonical_target_counts_and_blocks():
    check("attackers none -> PICKS: NONE", _tgt("attackers", 0, 3) == ("PICKS: NONE", "count-none"))
    check("attackers partial -> first-k",
          _tgt("attackers", 2, 3) == ("PICKS: 1,2", "count-first-k"))
    check("attackers all -> count-all",
          _tgt("attackers", 3, 3) == ("PICKS: 1,2,3", "count-all"))
    check("discard count 1 -> PICKS: 1", _tgt("discard", 1, 2) == ("PICKS: 1", "count-first-k"))
    check("modes count 1 of 1 -> count-all", _tgt("modes", 1, 1) == ("PICKS: 1", "count-all"))
    check("blockers none -> BLOCKS: NONE", _tgt("blockers", 0, 2) == ("BLOCKS: NONE", "blocks-none"))
    check("blockers k>0 underdetermined -> dropped",
          _tgt("blockers", 2, 3) == (None, "blocks-underdetermined"))
    check("unknown type dropped", _tgt("mystery-type", 1, 2)[0] is None)
    check("out-of-range count dropped", _tgt("attackers", 9, 3)[0] is None)


def test_canonical_target_fallback_oob():
    # engine-fallback play rows stamp n_options=-1, choice=-1: the PASS branch
    # must NOT emit "CHOICE: -1" (harness regex \d+ would parse it as CHOICE: 1
    # and pick option 0). It must drop instead.
    check("play PASS with n_options=-1 -> dropped (pass-oob)",
          _tgt("play", -1, -1) == (None, "pass-oob"))
    check("decideOne decline with n_options=-1 -> dropped (decline-oob)",
          _tgt("zone-change", -1, -1) == (None, "decline-oob"))
    check("play PASS with n_options=0 -> dropped (pass-oob)",
          _tgt("play", -1, 0) == (None, "pass-oob"))
    # a well-formed decline at n_options=0 is still valid (menu #1)
    check("decideOne decline with n_options=0 -> CHOICE: 1",
          _tgt("zone-change", -1, 0) == ("CHOICE: 1", "decline"))


# --------------------------------------------------- win-filter edge cases

def test_ai_teacher_win_filter_basic():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        # winner A (Elves): keep Elves rows, drop the losing Goblins seat's rows
        write_game(d, "game-1-1000.jsonl", ai_game(1, False, "A"), [
            ai_dec("Elves", "play", 0, 3, ["CAST a", "CAST b", "PASS (do nothing)"], prompt="elf-win"),
            ai_dec("Goblins", "play", 1, 2, ["CAST g", "PASS (do nothing)"], prompt="gob-lose"),
        ])
        # a draw -> nothing at all
        write_game(d, "game-2-1000.jsonl", ai_game(2, False, "draw"),
                   [ai_dec("Elves", "play", 0, 2, ["CAST a", "PASS (do nothing)"], prompt="drawn")])
        # incomplete (no game line) -> skipped
        write_game(d, "game-3-1000.jsonl", None,
                   [ai_dec("Elves", "play", 0, 2, ["CAST a", "PASS (do nothing)"], prompt="inflight")])
        rows, warnings, hyg = m.build_ai_teacher_examples(d, pb)
        prompts = sorted(e["messages"][1]["content"] for e in rows)
        check("only winning-seat rows kept", prompts == ["elf-win"], "got %s" % prompts)
        targets = [e["messages"][2]["content"] for e in rows]
        check("target synthesized (CHOICE: 1)", targets == ["CHOICE: 1"])
        check("draw contributes nothing", all("drawn" not in p for p in prompts))
        check("in-flight contributes nothing", all("inflight" not in p for p in prompts))
        check("system is terse framing for the winning seat",
              'piloting "Elves"' in rows[0]["messages"][0]["content"]
              and "SNAPSHOT LEGEND" not in rows[0]["messages"][0]["content"])
        check("provenance carries source/deck/type/synth",
              rows[0]["meta"]["source"] == "ai-teacher"
              and rows[0]["meta"]["deck"] == "Elves"
              and rows[0]["meta"]["type"] == "play"
              and rows[0]["meta"]["target_synth"] == "choice")


def test_ai_teacher_swap_seats():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        # swap=True, winner B (identity B = Goblins). winner is identity-keyed,
        # so the WINNING seat is still deck_b == Goblins regardless of swap.
        write_game(d, "game-7-1000.jsonl", ai_game(7, True, "B"), [
            ai_dec("Elves", "play", 0, 2, ["CAST a", "PASS (do nothing)"], prompt="elf"),
            ai_dec("Goblins", "play", 1, 3, ["CAST g", "CAST h", "PASS (do nothing)"], prompt="gob"),
        ])
        rows, _, _ = m.build_ai_teacher_examples(d, pb)
        prompts = sorted(e["messages"][1]["content"] for e in rows)
        check("swap: winning identity B seat (Goblins) kept", prompts == ["gob"])
        check("swap flag carried", rows[0]["meta"]["swap"] is True)


def test_ai_teacher_ai_filter_modes():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        # winner A (Elves). Losing Goblins seat has one common-type row (play)
        # and one rare-type row (modes).
        write_game(d, "game-1-1000.jsonl", ai_game(1, False, "A"), [
            ai_dec("Elves", "play", 0, 2, ["CAST a", "PASS (do nothing)"], prompt="elf-play"),
            ai_dec("Goblins", "play", 0, 2, ["CAST g", "PASS (do nothing)"], prompt="gob-play"),
            ai_dec("Goblins", "modes", 1, 2, ["Mode X", "Mode Y"], prompt="gob-modes"),
        ])
        rows, _, hyg = m.build_ai_teacher_examples(d, pb, ai_filter="win")
        check("win: loser rows dropped",
              sorted(e["messages"][1]["content"] for e in rows) == ["elf-play"])
        rows, _, hyg = m.build_ai_teacher_examples(d, pb, ai_filter="all")
        check("all: both seats kept",
              sorted(e["messages"][1]["content"] for e in rows)
              == ["elf-play", "gob-modes", "gob-play"])
        gob = [e for e in rows if e["meta"]["seat"] == "Goblins"]
        check("all: loser rows carry seat_won=False + own identity/system",
              all(e["meta"]["seat_won"] is False
                  and e["meta"]["llm_identity"] == "B"
                  and 'piloting "Goblins"' in e["messages"][0]["content"]
                  for e in gob),
              "gob meta: %s" % [e["meta"] for e in gob])
        check("all: winner rows carry seat_won=True",
              all(e["meta"]["seat_won"] is True for e in rows
                  if e["meta"]["seat"] == "Elves"))
        check("all: loser rows cell-keyed to their own deck",
              all(e["_cell"] == ("Goblins", e["meta"]["type"]) for e in gob))
        rows, _, hyg = m.build_ai_teacher_examples(d, pb, ai_filter="win+rare")
        check("win+rare: loser rare type kept, loser common type dropped",
              sorted(e["messages"][1]["content"] for e in rows)
              == ["elf-play", "gob-modes"])
        check("win+rare: hygiene counts the dropped loser common row",
              hyg["skip_loser_common_type"] == 1
              and hyg["emit_from_losing_seat"] == 1, dict(hyg))


def test_ai_teacher_pass_and_count_targets():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        write_game(d, "game-1-1000.jsonl", ai_game(1, False, "A"), [
            ai_dec("Elves", "play", -1, 3, ["CAST a", "CAST b", "PASS (do nothing)"], prompt="pass"),
            ai_dec("Elves", "attackers", 2, 2, ["Elf (2/2)", "Elf (1/1)"], prompt="atk"),
            ai_dec("Elves", "blockers", 0, 2, ["A (1/1)", "B (2/2)"], prompt="noblock"),
            ai_dec("Elves", "blockers", 1, 2, ["A (1/1)", "B (2/2)"], prompt="block-drop"),
            ai_dec("Elves", "mulligan", 1, 2, ["KEEP", "MULLIGAN"], prompt="mull"),
        ])
        rows, _, hyg = m.build_ai_teacher_examples(d, pb)
        by_prompt = {e["messages"][1]["content"]: e["messages"][2]["content"] for e in rows}
        check("PASS -> CHOICE: 3 (n_options)", by_prompt.get("pass") == "CHOICE: 3")
        check("attackers all -> PICKS: 1,2", by_prompt.get("atk") == "PICKS: 1,2")
        check("no-block -> BLOCKS: NONE", by_prompt.get("noblock") == "BLOCKS: NONE")
        check("mulligan word", by_prompt.get("mull") == "MULLIGAN")
        check("underdetermined block dropped (not emitted)", "block-drop" not in by_prompt)
        check("drop counted in hygiene", hyg.get("drop_blocks-underdetermined") == 1)


def test_ai_teacher_drops_fallback_rows():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        good = ai_dec("Elves", "play", 0, 2, ["CAST a", "PASS (do nothing)"],
                      prompt="clean")
        # engine-fallback row as it appears in the real corpora: the AI's pick
        # wasn't in the menu -> fallback set, n_options=-1, choice=-1.
        fb = ai_dec("Elves", "play", -1, -1,
                    ["CAST x", "PASS (do nothing)"], prompt="fallback")
        fb["fallback"] = "ai-choice-not-in-menu"
        write_game(d, "game-1-1000.jsonl", ai_game(1, False, "A"), [good, fb])
        rows, _, hyg = m.build_ai_teacher_examples(d, pb)
        prompts = sorted(e["messages"][1]["content"] for e in rows)
        check("engine-fallback row dropped, clean row kept",
              prompts == ["clean"], "got %s" % prompts)
        check("no 'CHOICE: -1' target ever emitted",
              all(e["messages"][2]["content"] != "CHOICE: -1" for e in rows))
        check("fallback drop counted in hygiene", hyg.get("drop_fallback") == 1)


def test_dedupe_keyed():
    def ai_row(key, rid):
        return {"_key": key, "_rid": rid, "_source": "ai-teacher"}
    def pre_row(rid):
        return {"_rid": rid, "_source": "prebuilt"}  # no _key
    by_source = {
        "ai-teacher:X": [ai_row(("Elves", "S", "CHOICE: 3"), "a1"),
                         ai_row(("Elves", "S", "CHOICE: 3"), "a2"),   # dup of a1
                         ai_row(("Elves", "T", "CHOICE: 1"), "a3")],
        "ai-teacher:Y": [ai_row(("Elves", "S", "CHOICE: 3"), "b1")],  # cross-source dup
        "prebuilt:P": [pre_row("p1"), pre_row("p1")],  # identical, but kept (no _key)
    }
    out, dropped = m.dedupe_keyed(by_source)
    check("cross-source keyed dups removed (a2 + b1)", dropped == 2, "got %d" % dropped)
    xr = [r["_rid"] for r in out["ai-teacher:X"]]
    check("first occurrence kept within source", xr == ["a1", "a3"], "got %s" % xr)
    check("cross-source dup emptied", out["ai-teacher:Y"] == [])
    check("prebuilt rows never deduped", len(out["prebuilt:P"]) == 2)
    o2, d2 = m.dedupe_keyed(by_source)
    check("dedupe_keyed deterministic", d2 == dropped
          and [r["_rid"] for r in o2["ai-teacher:X"]] == xr)


def test_ai_teacher_exact_only_drops_partial():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        write_game(d, "game-1-1000.jsonl", ai_game(1, False, "A"), [
            ai_dec("Elves", "attackers", 1, 3, ["a", "b", "c"], prompt="partial"),   # first-k
            ai_dec("Elves", "attackers", 3, 3, ["a", "b", "c"], prompt="all"),       # count-all
            ai_dec("Elves", "attackers", 0, 3, ["a", "b", "c"], prompt="none"),      # count-none
        ])
        rows, _, _ = m.build_ai_teacher_examples(d, pb, count_style="exact-only")
        kept = sorted(e["messages"][1]["content"] for e in rows)
        check("exact-only drops partial first-k, keeps k in {0,n}",
              kept == ["all", "none"], "got %s" % kept)


def test_ai_teacher_reads_tree():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        for mu in ("elves__vs__goblins", "ant__vs__dnt"):
            sub = Path(d) / mu
            sub.mkdir()
            write_game(sub, "game-1-1000.jsonl", ai_game(1, False, "A"),
                       [ai_dec("Elves", "play", 0, 2, ["CAST a", "PASS (do nothing)"], prompt=mu)])
        rows, _, _ = m.build_ai_teacher_examples(d, pb)
        prompts = sorted(e["messages"][1]["content"] for e in rows)
        check("tree walk finds both matchup subdirs",
              prompts == ["ant__vs__dnt", "elves__vs__goblins"], "got %s" % prompts)
        gkeys = {e["_gkey"] for e in rows}
        check("same seed in different subdirs -> distinct game keys", len(gkeys) == 2)


# --------------------------------------------------- prebuilt passthrough

def test_prebuilt_passthrough():
    def row(u, a):
        return json.dumps({"messages": [
            {"role": "system", "content": "S"},
            {"role": "user", "content": u},
            {"role": "assistant", "content": a}]})
    with tempfile.TemporaryDirectory() as d:
        slice_dir = Path(d) / "slice"
        slice_dir.mkdir()
        (slice_dir / "train.jsonl").write_text(
            row("t1", "CHOICE: 1") + "\n" + row("t2", "PICKS: 1,2") + "\n"
            + row("bad", "reason <think>hidden</think> CHOICE: 1") + "\n")
        (slice_dir / "valid.jsonl").write_text(row("v1", "KEEP") + "\n")
        rows, warnings, hyg = m.build_prebuilt_examples(str(slice_dir))
        users = sorted(e["messages"][1]["content"] for e in rows)
        check("prebuilt kept verbatim, <think> row dropped",
              users == ["t1", "t2", "v1"], "got %s" % users)
        check("<think> drop counted", hyg.get("drop_think_block") == 1)
        splits = {e["meta"]["split"] for e in rows}
        check("original split preserved (train+valid)", splits == {"train", "valid"})
        v = [e for e in rows if e["meta"]["split"] == "valid"]
        check("valid row from valid.jsonl", v and v[0]["messages"][1]["content"] == "v1")
        check("prebuilt rows carry no game key (keep own split)",
              all(e["_gkey"] is None for e in rows))
        check("provenance source=prebuilt", all(e["meta"]["source"] == "prebuilt" for e in rows))


def test_prebuilt_single_file_all_train():
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d) / "puzzles.jsonl"
        fp.write_text(json.dumps({"messages": [
            {"role": "user", "content": "puz"},
            {"role": "assistant", "content": "{\"line\": []}"}]}) + "\n")
        rows, _, _ = m.build_prebuilt_examples(str(fp))
        check("single file -> all train", rows and rows[0]["_split"] == "train")
        check("prebuilt_kind labeled from filename",
              rows[0]["meta"]["prebuilt_kind"] == "puzzles")


# --------------------------------------------------- allocation / stratify

def test_allocate_weighted():
    a = m.allocate_weighted(10, {"x": (1.0, 100), "y": (1.0, 100)})
    check("even split", a == {"x": 5, "y": 5}, "got %s" % a)
    b = m.allocate_weighted(10, {"x": (1.0, 3), "y": (1.0, 100)})
    check("clamp to availability + redistribute", b == {"x": 3, "y": 7}, "got %s" % b)
    c = m.allocate_weighted(1000, {"x": (1.0, 3), "y": (1.0, 4)})
    check("budget exceeds total -> take all", c == {"x": 3, "y": 4}, "got %s" % c)
    w = m.allocate_weighted(12, {"x": (3.0, 100), "y": (1.0, 100)})
    check("weight bias 3:1 sums to budget", sum(w.values()) == 12 and w["x"] > w["y"],
          "got %s" % w)
    check("allocation deterministic",
          m.allocate_weighted(7, {"a": (1.0, 5), "b": (2.0, 5), "c": (1.0, 5)})
          == m.allocate_weighted(7, {"a": (1.0, 5), "b": (2.0, 5), "c": (1.0, 5)}))


def _cellrows(cell, n, tag):
    return [{"_cell": cell, "_rid": "%s-%d" % (tag, i),
             "meta": {"deck": cell[0], "type": cell[1]}} for i in range(n)]


def test_stratified_sample_balances_and_oversamples():
    rows = (_cellrows(("Elves", "play"), 50, "ep")
            + _cellrows(("Goblins", "play"), 50, "gp")
            + _cellrows(("Elves", "modes"), 2, "em")
            + _cellrows(("Goblins", "blockers"), 1, "gb"))
    out = m.stratified_sample(rows, 20, seed=99)
    by_cell = {}
    for r in out:
        by_cell[r["_cell"]] = by_cell.get(r["_cell"], 0) + 1
    check("stratified total == cap", len(out) == 20, "got %d" % len(out))
    check("rare 'modes' cell kept in full (oversampled)", by_cell.get(("Elves", "modes")) == 2)
    check("rare 'blockers' cell kept in full", by_cell.get(("Goblins", "blockers")) == 1)
    ep, gp = by_cell.get(("Elves", "play"), 0), by_cell.get(("Goblins", "play"), 0)
    check("play rows balanced across the two decks (within 1)", abs(ep - gp) <= 1,
          "got Elves=%d Goblins=%d" % (ep, gp))
    check("no single (deck,play) cell dominates", ep < 12 and gp < 12)
    check("stratified sampling deterministic",
          [r["_rid"] for r in m.stratified_sample(rows, 20, seed=99)]
          == [r["_rid"] for r in out])


def test_apply_mix_reserves_curated_caps_ai_teacher():
    ai = _cellrows(("Elves", "play"), 40, "ep") + _cellrows(("Goblins", "play"), 40, "gp")
    for r in ai:
        r["_rid"] = "ai-" + r["_rid"]
    pre = [{"_cell": ("puzzle", "prebuilt"), "_rid": "pz-%d" % i,
            "meta": {"deck": "puzzle", "type": "prebuilt"}} for i in range(10)]
    by_source = {"ai-teacher:X": ai, "prebuilt:Y": pre}
    kept, report = m.apply_mix(by_source, {"ai-teacher:X": 1.0, "prebuilt:Y": 1.0},
                               {}, 30, sample_seed=5)
    check("curated prebuilt kept in FULL under cap", len(kept["prebuilt:Y"]) == 10)
    check("ai-teacher fills remaining budget (30-10)", len(kept["ai-teacher:X"]) == 20)
    check("global total == cap", len(kept["ai-teacher:X"]) + len(kept["prebuilt:Y"]) == 30)
    # per-source cap
    kept2, _ = m.apply_mix(by_source, {"ai-teacher:X": 1.0, "prebuilt:Y": 1.0},
                           {"ai-teacher:X": 12}, None, sample_seed=5)
    check("per-source cap applied (stratified)", len(kept2["ai-teacher:X"]) == 12)
    check("uncapped source untouched w/o global cap", len(kept2["prebuilt:Y"]) == 10)


# --------------------------------------------------- stage-2 end to end

def test_stage2_end_to_end():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        ai_root = Path(d) / "selfplay"
        (ai_root / "e_vs_g").mkdir(parents=True)
        for seed in range(1, 9):
            write_game(ai_root / "e_vs_g", "game-%d-1000.jsonl" % seed,
                       ai_game(seed, False, "A"), [
                           ai_dec("Elves", "play", 0, 2, ["CAST a", "PASS (do nothing)"], prompt="p%d" % seed),
                           ai_dec("Elves", "attackers", 1, 2, ["x", "y"], prompt="a%d" % seed),
                       ])
        pre = Path(d) / "puzzles.jsonl"
        pre.write_text("\n".join(json.dumps({"messages": [
            {"role": "user", "content": "puz%d" % i},
            {"role": "assistant", "content": "{\"line\": []}"}]}) for i in range(6)) + "\n")

        specs = [{"type": "ai-teacher", "path": str(ai_root), "weight": 1.0},
                 {"type": "prebuilt", "path": str(pre), "weight": 1.0}]
        out = Path(d) / "out"

        class A:  # minimal args stand-in
            source = specs
            out_dir = str(out)
            cap = 12
            cap_per_source = None
            sample_seed = 20260723
            val_frac = 0.25
            split_seed = 20260722
            count_style = "first-k"
            ai_filter = "win"
            java = str(m.DEFAULT_JAVA)
        rc = m.run_stage2(A(), pb)
        check("run_stage2 returns 0", rc == 0)
        train = (out / "train.jsonl").read_text().strip().splitlines()
        valid = (out / "valid.jsonl").read_text().strip().splitlines()
        total = len(train) + len(valid)
        check("global cap respected", total == 12, "got %d" % total)
        # every emitted assistant is a canonical terse line or puzzle JSON; no <think>
        alltext = (out / "train.jsonl").read_text() + (out / "valid.jsonl").read_text()
        check("no <think> anywhere in targets", "<think>" not in alltext)
        # prebuilt curated kept in full (6), ai-teacher fills the rest (6)
        meta = [json.loads(x) for x in (out / "meta.jsonl").read_text().strip().splitlines()]
        srcs = {}
        for r in meta:
            srcs[r["source"]] = srcs.get(r["source"], 0) + 1
        check("curated prebuilt fully retained under cap", srcs.get("prebuilt") == 6)
        check("ai-teacher fills remaining budget", srcs.get("ai-teacher") == 6)
        check("meta aligns line-count with data",
              len([r for r in meta if r["split"] == "train"]) == len(train))
        check("README + counts table written",
              (out / "README.md").exists()
              and "Counts by (source, deck, type)" in (out / "README.md").read_text())
        # prebuilt keeps its own (train) split; ai-teacher splits by game
        pre_splits = {r["split"] for r in meta if r["source"] == "prebuilt"}
        check("prebuilt kept its original split (train)", pre_splits == {"train"})


def test_stage2_empty_source_hard_fails():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out"

        class A:  # a prebuilt source pointing at a non-existent path -> 0 rows
            source = [{"type": "prebuilt",
                       "path": str(Path(d) / "does-not-exist"), "weight": 1.0}]
            out_dir = str(out)
            cap = None
            cap_per_source = None
            sample_seed = 1
            val_frac = 0.1
            split_seed = 20260722
            count_style = "first-k"
            ai_filter = "win"
            java = str(m.DEFAULT_JAVA)
        raised = False
        try:
            m.run_stage2(A(), pb)
        except SystemExit as e:
            raised = True
            check("0-row source error names the offending source",
                  "0 rows" in str(e) and "does-not-exist" in str(e))
        check("a declared source contributing 0 rows hard-fails", raised)


def test_stage2_dedup_across_games():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        ai_root = Path(d) / "sp"
        (ai_root / "mu").mkdir(parents=True)
        # 6 DIFFERENT games, each with the SAME normalized PASS decision -> the
        # canonical target collapses to one row after dedup (identical seat +
        # state + target across games).
        for seed in range(1, 7):
            write_game(ai_root / "mu", "game-%d-1000.jsonl" % seed,
                       ai_game(seed, False, "A"),
                       [ai_dec("Elves", "play", -1, 2,
                               ["CAST a", "PASS (do nothing)"], prompt="same state")])
        specs = [{"type": "ai-teacher", "path": str(ai_root), "weight": 1.0}]
        out = Path(d) / "out"

        class A:
            source = specs
            out_dir = str(out)
            cap = None
            cap_per_source = None
            sample_seed = 7
            val_frac = 0.5
            split_seed = 20260722
            count_style = "first-k"
            ai_filter = "win"
            java = str(m.DEFAULT_JAVA)
        m.run_stage2(A(), pb)
        train = (out / "train.jsonl").read_text().strip().splitlines()
        valid = (out / "valid.jsonl").read_text().strip().splitlines()
        check("identical (state,target) rows from 6 games deduped to 1",
              len(train) + len(valid) == 1,
              "got %d" % (len(train) + len(valid)))


def test_stage2_determinism():
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        ai_root = Path(d) / "sp"
        (ai_root / "mu").mkdir(parents=True)
        for seed in range(1, 12):
            write_game(ai_root / "mu", "game-%d-1000.jsonl" % seed,
                       ai_game(seed, False, "A"),
                       [ai_dec("Elves", "play", 0, 2, ["CAST a", "PASS (do nothing)"], prompt="p%d" % seed)])
        specs = [{"type": "ai-teacher", "path": str(ai_root), "weight": 1.0}]

        def build(tag):
            out = Path(d) / tag

            class A:
                source = specs
                out_dir = str(out)
                cap = 5
                cap_per_source = None
                sample_seed = 42
                val_frac = 0.2
                split_seed = 20260722
                count_style = "first-k"
                ai_filter = "win"
                java = str(m.DEFAULT_JAVA)
            m.run_stage2(A(), pb)
            return (out / "train.jsonl").read_text(), (out / "valid.jsonl").read_text()
        a = build("r1")
        b = build("r2")
        check("stage-2 build is fully deterministic", a == b)


# ----------------------------------------------------------------- runner

# --------------------------------------------------- D4 DAgger (dagger source)

def dagger_dec(seat, dtype, choice, shadow_choice, n_options, options, prompt="P"):
    """A STUDENT decision row (tier smart, real prompt) carrying the additive
    shadow_choice DAgger label (the Forge-AI's pick over the same menu)."""
    d = ai_dec(seat, dtype, choice, n_options, options, prompt)
    d["tier"] = "smart"                 # student tier, NOT ai-teacher
    d["shadow_choice"] = shadow_choice
    return d


def test_dagger_builder_per_family():
    """One dagger example per shadow family, target synthesized byte-exact from
    the SHADOW label (not the student's choice), with the blockers k>0 drop."""
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        write_game(d, "game-1-1000.jsonl", ai_game(1, False, "A"), [
            dagger_dec("Elves", "play", 2, 0, 3,
                       ["CAST a", "CAST b", "PASS (do nothing)"], prompt="p-index"),
            dagger_dec("Elves", "play", 0, -1, 3,
                       ["CAST a", "CAST b", "PASS (do nothing)"], prompt="p-pass"),
            dagger_dec("Elves", "attackers", 1, 2, 3, ["x", "y", "z"], prompt="a-count"),
            dagger_dec("Elves", "attackers", 0, 3, 3, ["x", "y", "z"], prompt="a-all"),
            dagger_dec("Elves", "blockers", 0, 0, 2, ["b1", "b2"], prompt="b-none"),
            dagger_dec("Elves", "blockers", 1, 2, 3, ["b1", "b2", "b3"], prompt="b-kpos"),
            dagger_dec("Elves", "mulligan", 0, 0, 2, ["KEEP", "MULLIGAN"], prompt="m-keep"),
            dagger_dec("Elves", "mulligan", 0, 1, 2, ["KEEP", "MULLIGAN"], prompt="m-mull"),
        ])
        rows, warnings, hyg = m.build_dagger_examples(d, pb)
        by_prompt = {e["messages"][1]["content"]: e["messages"][2]["content"]
                     for e in rows}
        check("play index (shadow 0) -> CHOICE: 1", by_prompt.get("p-index") == "CHOICE: 1")
        check("play PASS (shadow -1) -> CHOICE: 3", by_prompt.get("p-pass") == "CHOICE: 3")
        check("attackers count (shadow 2/3) -> PICKS: 1,2", by_prompt.get("a-count") == "PICKS: 1,2")
        check("attackers all (shadow 3/3) -> PICKS: 1,2,3", by_prompt.get("a-all") == "PICKS: 1,2,3")
        check("blockers k=0 -> BLOCKS: NONE", by_prompt.get("b-none") == "BLOCKS: NONE")
        check("blockers k>0 DROPPED (pairing not logged)", "b-kpos" not in by_prompt)
        check("blockers k>0 drop counted in hygiene",
              hyg["drop_blocks-underdetermined"] == 1)
        check("mulligan keep (shadow 0) -> KEEP", by_prompt.get("m-keep") == "KEEP")
        check("mulligan mull (shadow 1) -> MULLIGAN", by_prompt.get("m-mull") == "MULLIGAN")
        r0 = next(e for e in rows if e["messages"][1]["content"] == "p-index")
        check("provenance source=dagger tier=ai-shadow",
              r0["meta"]["source"] == "dagger" and r0["meta"]["tier"] == "ai-shadow")
        check("meta choice = the shadow label", r0["meta"]["choice"] == 0)
        check("meta records the student's own choice", r0["meta"]["student_choice"] == 2)
        check("dagger system is terse framing (no STATIC_REFERENCE)",
              'piloting "Elves"' in r0["messages"][0]["content"]
              and "SNAPSHOT LEGEND" not in r0["messages"][0]["content"])


def test_dagger_no_win_filter():
    """DAgger keeps EVERY state: losing-seat rows and draw games included."""
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        # winner A (Elves) — a shadow row on the LOSING seat (Goblins) is kept.
        write_game(d, "game-1-1000.jsonl", ai_game(1, False, "A"), [
            dagger_dec("Elves", "play", 0, 0, 2,
                       ["CAST a", "PASS (do nothing)"], prompt="winner-seat"),
            dagger_dec("Goblins", "play", 0, 0, 2,
                       ["CAST g", "PASS (do nothing)"], prompt="loser-seat"),
        ])
        # a DRAW game still contributes (no draw skip, unlike ai-teacher).
        write_game(d, "game-2-1000.jsonl", ai_game(2, False, "draw"), [
            dagger_dec("Elves", "play", 0, 0, 2,
                       ["CAST a", "PASS (do nothing)"], prompt="draw-seat"),
        ])
        rows, _, _ = m.build_dagger_examples(d, pb)
        prompts = sorted(e["messages"][1]["content"] for e in rows)
        check("losing-seat AND winning-seat AND draw rows all kept",
              prompts == ["draw-seat", "loser-seat", "winner-seat"],
              "got %s" % prompts)
        # Contrast: the win-filtered ai-teacher builder would drop the loser and
        # the draw — and here, drop everything (these rows are tier=smart).
        ai_rows, _, _ = m.build_ai_teacher_examples(d, pb)
        check("ai-teacher builder emits nothing from student(smart) rows",
              ai_rows == [])


def test_dagger_selects_by_shadow_choice_and_prompt():
    """Only rows with a non-null shadow_choice AND a real prompt survive."""
    pb = m.PreambleBuilder(str(m.DEFAULT_JAVA))
    with tempfile.TemporaryDirectory() as d:
        write_game(d, "game-1-1000.jsonl", ai_game(1, False, "A"), [
            dagger_dec("Elves", "play", 0, 0, 2,
                       ["CAST a", "PASS (do nothing)"], prompt="has-shadow"),
            # no shadow_choice key at all -> silently skipped (not a dagger row)
            ai_dec("Elves", "play", 0, 2, ["CAST a", "PASS (do nothing)"],
                   prompt="no-shadow"),
            # shadow present but prompt empty (floor/auto student row) -> dropped
            dagger_dec("Elves", "play", 0, 0, 2,
                       ["CAST a", "PASS (do nothing)"], prompt=""),
        ])
        rows, warnings, hyg = m.build_dagger_examples(d, pb)
        prompts = [e["messages"][1]["content"] for e in rows]
        check("only the shadow-labeled, prompted row survives",
              prompts == ["has-shadow"], "got %s" % prompts)
        check("prompt-less shadow row counted as drop_no_prompt",
              hyg["drop_no_prompt"] == 1)


def test_parse_typed_source_dagger():
    s = m.parse_typed_source("dagger:runs/d4:2.0")
    check("dagger type parsed", s["type"] == "dagger")
    check("dagger path parsed", s["path"] == "runs/d4")
    check("dagger weight parsed", s["weight"] == 2.0)
    s2 = m.parse_typed_source("dagger:runs/d4")
    check("dagger default weight 1.0",
          s2["weight"] == 1.0 and s2["path"] == "runs/d4")
    check("dagger source_name is dagger:PATH", m.source_name(s) == "dagger:runs/d4")


def test_apply_mix_dagger_shares_reserved_bucket():
    """dagger + ai-teacher share the stratified reserved budget; curated
    (prebuilt/win) sources stay kept-in-full."""
    ai = _cellrows(("Elves", "play"), 20, "ep")
    for r in ai:
        r["_rid"] = "ai-" + r["_rid"]
    dg = _cellrows(("Goblins", "play"), 20, "gp")
    for r in dg:
        r["_rid"] = "dg-" + r["_rid"]
    pre = [{"_cell": ("puzzle", "prebuilt"), "_rid": "pz-%d" % i,
            "meta": {"deck": "puzzle", "type": "prebuilt"}} for i in range(10)]
    by_source = {"ai-teacher:X": ai, "dagger:Y": dg, "prebuilt:Z": pre}
    weights = {"ai-teacher:X": 1.0, "dagger:Y": 1.0, "prebuilt:Z": 1.0}
    kept, report = m.apply_mix(by_source, weights, {}, 30, sample_seed=5)
    check("curated prebuilt kept in FULL under cap", len(kept["prebuilt:Z"]) == 10)
    synth = len(kept["ai-teacher:X"]) + len(kept["dagger:Y"])
    check("ai-teacher+dagger fill the reserved budget (30-10=20)", synth == 20)
    check("dagger takes a stratified 1:1 share (10 each)",
          len(kept["dagger:Y"]) == 10 and len(kept["ai-teacher:X"]) == 10)
    check("global total == cap", synth + len(kept["prebuilt:Z"]) == 30)
    kept2, _ = m.apply_mix(by_source, weights, {"dagger:Y": 8}, None, sample_seed=5)
    check("dagger per-source cap applied (stratified)", len(kept2["dagger:Y"]) == 8)


# ------------------------------------------------- held-out deck guard
# datasets/stage2-r2-teacher-v0 acquired 1148 painter / sneak-and-show rows
# (2.3%) from runs/r2-breadth-teacher because nothing checked decks at BUILD
# time, silently turning the suite's generalization arm into an
# in-distribution one. These pin the three ways out: refuse, drop, allow.

def _heldout_rows(slug, n, source="ai-teacher:X"):
    return [{"messages": [], "meta": {"deck": slug, "seat": slug}} for _ in range(n)]


def test_heldout_registry_reads_only_heldout_decks():
    slugs, note = m.load_heldout_deck_slugs()
    check("registry loads without a note", note is None, str(note))
    check("held-out decks found", bool(slugs), str(slugs))
    check("in-corpus decks are NOT treated as held-out",
          "burn" not in slugs and "ur-delver" not in slugs, str(slugs))


def test_heldout_registry_missing_is_reported_not_silent():
    """A registry we cannot read must say so — an empty set would otherwise
    read as 'no held-out decks', the exact silent pass this guards."""
    with tempfile.TemporaryDirectory() as d:
        slugs, note = m.load_heldout_deck_slugs(Path(d) / "absent.json")
        check("missing registry -> empty slugs", slugs == set())
        check("missing registry -> loud note", note is not None and "NOT checked" in note)


def test_heldout_guard_refuses_by_default():
    by_source = {"ai-teacher:X": _heldout_rows("painter", 3)
                 + [{"messages": [], "meta": {"deck": "burn"}}]}
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            m.enforce_heldout_decks(by_source, {"painter"}, drop=False, allow=False)
            check("refuses on a held-out deck row", False, "did not exit")
        except SystemExit as e:
            check("refuses on a held-out deck row -> exit 2", e.code == 2, str(e.code))


def test_heldout_guard_drop_removes_only_those_rows():
    by_source = {"ai-teacher:X": _heldout_rows("painter", 3)
                 + [{"messages": [], "meta": {"deck": "burn"}}] * 5}
    with contextlib.redirect_stderr(io.StringIO()):
        dropped, override = m.enforce_heldout_decks(
            by_source, {"painter"}, drop=True, allow=False)
    check("drop reports the count", dropped == 3, str(dropped))
    check("drop is not an override", override is False)
    check("only held-out rows removed", len(by_source["ai-teacher:X"]) == 5)
    check("no held-out row survives",
          all(r["meta"]["deck"] != "painter" for r in by_source["ai-teacher:X"]))


def test_heldout_guard_allow_keeps_rows_and_flags_override():
    by_source = {"ai-teacher:X": _heldout_rows("painter", 3)}
    with contextlib.redirect_stderr(io.StringIO()):
        dropped, override = m.enforce_heldout_decks(
            by_source, {"painter"}, drop=False, allow=True)
    check("allow drops nothing", dropped == 0 and len(by_source["ai-teacher:X"]) == 3)
    check("allow flags the override", override is True)


def test_heldout_guard_clean_corpus_is_untouched():
    by_source = {"ai-teacher:X": [{"messages": [], "meta": {"deck": "burn"}}] * 4}
    dropped, override = m.enforce_heldout_decks(
        by_source, {"painter"}, drop=False, allow=False)
    check("clean corpus does not refuse", dropped == 0 and override is False)
    check("clean corpus keeps every row", len(by_source["ai-teacher:X"]) == 4)


def test_heldout_guard_reads_the_nested_meta_deck():
    """The bug this shipped with: the slug lives in row['meta']['deck'], not at
    the top level, so a top-level-only read found nothing and passed a
    contaminated corpus."""
    check("nested meta.deck is found",
          m._row_deck_slug({"meta": {"deck": "painter"}}) == "painter")
    check("nested meta.seat is a fallback",
          m._row_deck_slug({"meta": {"seat": "painter"}}) == "painter")
    check("top-level still works",
          m._row_deck_slug({"deck": "painter"}) == "painter")
    check("a row with no deck is unjudgeable, not clean",
          m._row_deck_slug({"meta": {}}) is None)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
