#!/usr/bin/env python3
"""Self-test for make_sft_data.py: synthetic mini-jsonl fixtures + logic checks.

Run: python3 eval/test_make_sft_data.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_sft_data as m  # noqa: E402

FAILURES = []


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
    smart = pb.system("UR Delver", plan_a, "smart", "STAGE0")
    check("STAGE0 smart system == 9256 chars", len(smart) == 9256,
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


# ----------------------------------------------------------------- runner

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
