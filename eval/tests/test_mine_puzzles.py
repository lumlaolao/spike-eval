#!/usr/bin/env python3
"""Self-test for mine_puzzles.py: synthetic fixture jsonl, no real corpus needed.

Run: python3 eval/tests/test_mine_puzzles.py
"""

import json
import sys
import tempfile
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import mine_puzzles as mp  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# --------------------------------------------------------------- fixtures

def game_line(seed=101, swap=False, controller_a="ai", controller_b="ai",
              deck_a="MySeat", deck_b="OpponentSeat"):
    return {
        "t": "game", "seed": seed, "swap": swap,
        "deck_a": deck_a, "deck_b": deck_b,
        "controller_a": controller_a, "controller_b": controller_b,
        "smart_a": "x", "smart_b": "y", "fast_a": "x", "fast_b": "y",
        "winner": "A", "turns": 12, "win_condition": "life",
        "duration_ms": 1000, "engine_sha": "deadbeef", "overlay_sha": "cafef00d",
        "deck_sha": "abc123",
    }


def build_prompt(you_seat="MySeat", you_life=20, you_hand="Island, Island",
                  you_board="(no nonland permanents)", you_sources=4,
                  opp_seat="OpponentSeat", opp_life=20,
                  opp_board="(no nonland permanents)", stack="(empty)",
                  tail="DECISION: test. CHOICE: <number>"):
    n_hand = len([x for x in you_hand.split(", ") if x]) if you_hand else 0
    return (
        "== Turn 3 MAIN1 (active: %s) ==\n" % you_seat
        + "YOU [%s] life %d | poison 0/10\n" % (you_seat, you_life)
        + "  hand(%d): %s\n" % (n_hand, you_hand)
        + "  board: %s\n" % you_board
        + "  lands: %d untapped / %d total (Island x%d [untapped])\n" % (you_sources, you_sources, you_sources)
        + "  mana available: {U:%d} (%d untapped sources)\n" % (you_sources, you_sources)
        + "  yard(0): (none)\n"
        + "OPP [%s] life %d | poison 0/10\n" % (opp_seat, opp_life)
        + "  board: %s\n" % opp_board
        + "  lands: 4 untapped / 4 total (Mountain x4 [untapped])\n"
        + "  mana available: {R:4} (4 untapped sources)\n"
        + "  yard(0): (none)\n"
        + "  hand: 3 cards\n"
        + "stack (top first): %s\n" % stack
        + "RECENT EVENTS (oldest->newest):\n"
        + "  - filler event\n"
        + tail
    )


def dec(seat="MySeat", turn=3, dtype="play", n_options=2, choice=0,
        prompt="P", options=None, fallback=None, **extra):
    d = {
        "t": "decision", "seat": seat, "turn": turn, "phase": "MAIN1",
        "type": dtype, "n_options": n_options, "choice": choice,
        "tier": "smart", "latency_ms": 100, "fallback": fallback,
        "options": options if options is not None else ["a", "b"][:max(0, n_options)],
        "prompt": prompt, "raw_reply": "CHOICE: 1",
    }
    d.update(extra)
    return d


def write_game_file(path, game, decisions):
    with open(path, "w", encoding="utf-8") as f:
        for d in decisions:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        f.write(json.dumps(game, ensure_ascii=False) + "\n")


# --------------------------------------------------------- candidate rows

# 1. lethal-now: own untapped power (7) >= opp life (6), type=play.
LETHAL_NOW_PROMPT = build_prompt(
    you_life=20, you_board="Big Beater(7/7)", opp_life=6,
    opp_board="(no nonland permanents)")
LETHAL_NOW_ROW = dec(dtype="play", n_options=2, prompt=LETHAL_NOW_PROMPT,
                     options=["CAST Fireball", "PASS"])

# non-lethal counterpart: own power (2) < opp life (20) -> must NOT match.
NONLETHAL_PROMPT = build_prompt(
    you_life=20, you_board="Tiny Guy(2/2)", opp_life=20,
    opp_board="(no nonland permanents)")
NONLETHAL_ROW = dec(dtype="play", n_options=2, prompt=NONLETHAL_PROMPT,
                    options=["CAST X", "PASS"])

# 2. save-vs-lethal: opp board power (5) >= own life (4), type=play.
SAVE_VS_LETHAL_PROMPT = build_prompt(
    you_life=4, you_board="(no nonland permanents)", opp_life=20,
    opp_board="Angry Guy(5/5)")
SAVE_VS_LETHAL_ROW = dec(dtype="play", n_options=2, prompt=SAVE_VS_LETHAL_PROMPT,
                         options=["CAST Fog Effect", "PASS"])

# 3. forced-block: incoming power (5) >= own life (2), type=blockers.
FORCED_BLOCK_PROMPT = build_prompt(
    you_life=2, opp_life=20,
    tail=("COMBAT — declare blockers. Incoming: Scary Thing (5/5). "
          "Your blockers: My Blocker (1/1), Other Blocker (0/1). "
          "Assign blocks. End with EXACTLY: BLOCKS: ..."))
FORCED_BLOCK_ROW = dec(dtype="blockers", n_options=2, prompt=FORCED_BLOCK_PROMPT,
                       options=["My Blocker (1/1)", "Other Blocker (0/1)"])

# 4. free-win: opponent spell on stack + Force of Will in hand.
FREE_WIN_PROMPT = build_prompt(
    you_hand="Force of Will 3{U}{U} (need 1 more U), Island",
    stack="1 pending: #1 Emrakul, the Aeons Torn (OpponentSeat)")
FREE_WIN_ROW = dec(dtype="play", n_options=2, prompt=FREE_WIN_PROMPT,
                   options=["CAST Force of Will", "PASS"])

# 5. mulligan: always a candidate.
MULLIGAN_PROMPT = build_prompt(tail="MULLIGAN DECISION. Keep or mulligan?")
MULLIGAN_ROW = dec(dtype="mulligan", n_options=2, prompt=MULLIGAN_PROMPT,
                   options=["KEEP", "MULLIGAN"])

# negative rows sharing the LETHAL_NOW-triggering board/life text, but each
# individually ineligible — these MUST NOT be emitted, and their presence is
# exactly what would leak through if the eligibility filter were deleted.
FALLBACK_ROW = dec(dtype="play", n_options=2, prompt=LETHAL_NOW_PROMPT,
                   options=["CAST Fireball", "PASS"],
                   fallback="forced(only-legal)")
SINGLE_OPTION_ROW = dec(dtype="play", n_options=1, prompt=LETHAL_NOW_PROMPT,
                        options=["PASS"])
NULL_PROMPT_ROW = dec(dtype="play", n_options=2, prompt=None,
                      options=["CAST Fireball", "PASS"])


def make_scan_dir(tmp):
    """One corpus dir with two game files: a deterministic (ai/ai) game
    carrying the candidate + negative rows, and an llm/ai game (for the
    replayable=False check) carrying one more mulligan row."""
    corpus = Path(tmp) / "corpus"
    corpus.mkdir()

    det_decisions = [
        NULL_PROMPT_ROW,          # index 0: ineligible (no prompt)
        SINGLE_OPTION_ROW,        # index 1: ineligible (n_options=1)
        FALLBACK_ROW,             # index 2: ineligible (fallback set)
        LETHAL_NOW_ROW,           # index 3: lethal-now candidate
        NONLETHAL_ROW,            # index 4: no match (below threshold)
        SAVE_VS_LETHAL_ROW,       # index 5: save-vs-lethal candidate
        FORCED_BLOCK_ROW,         # index 6: forced-block candidate
        FREE_WIN_ROW,             # index 7: free-win candidate
        MULLIGAN_ROW,             # index 8: mulligan candidate
    ]
    write_game_file(corpus / "game-101-1000.jsonl", game_line(seed=101, controller_a="ai", controller_b="ai"),
                    det_decisions)

    llm_decisions = [MULLIGAN_ROW]
    write_game_file(corpus / "game-202-2000.jsonl",
                    game_line(seed=202, controller_a="llm", controller_b="ai"),
                    llm_decisions)

    # graded-midgame pool: 20 eligible play rows spread turns 3-8 across a
    # third game file, for the stratification/determinism checks.
    grade_decisions = []
    for i in range(20):
        turn = 3 + (i % 6)
        grade_decisions.append(dec(dtype="play", n_options=2, turn=turn,
                                   prompt=NONLETHAL_PROMPT,
                                   options=["CAST X", "PASS"]))
    write_game_file(corpus / "game-303-3000.jsonl", game_line(seed=303, controller_a="ai", controller_b="ai"),
                    grade_decisions)
    return corpus


# ------------------------------------------------------------------ tests

def _run_mine(tmp, corpus, out_name="candidates.jsonl", **kw):
    out_path = Path(tmp) / out_name
    argv = [str(corpus), "--out", str(out_path), "--sample-seed", "20260725"]
    for k, v in kw.items():
        argv += ["--%s" % k.replace("_", "-"), str(v)]
    rc = mp.main(argv)
    check("main() exits 0 (%s)" % out_name, rc == 0)
    lines = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    return lines


def test_each_category_triggers_exactly_once():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        lines = _run_mine(tmp, corpus)
        by_cat = {}
        for l in lines:
            by_cat.setdefault(l["category"], []).append(l)

        for cat in ("lethal-now", "save-vs-lethal", "forced-block", "free-win"):
            n = len(by_cat.get(cat, []))
            check("category %s emits exactly 1 candidate from fixture" % cat, n == 1,
                  "got %d" % n)
        # mulligan matches unconditionally, and the fixture deliberately adds
        # one mulligan row to the llm/ai game too (for the replayable check),
        # so 2 is correct here.
        n_mull = len(by_cat.get("mulligan", []))
        check("category mulligan emits 2 candidates (one per fixture game)",
              n_mull == 2, "got %d" % n_mull)

        if by_cat.get("lethal-now"):
            ev = by_cat["lethal-now"][0]["category_evidence"]
            check("lethal-now evidence mentions the matched totals",
                  "total=7" in ev and "opp_life=6" in ev, ev)
        if by_cat.get("save-vs-lethal"):
            ev = by_cat["save-vs-lethal"][0]["category_evidence"]
            check("save-vs-lethal evidence mentions matched power/life",
                  "opp_board_power=5" in ev and "own_life=4" in ev, ev)
        if by_cat.get("forced-block"):
            ev = by_cat["forced-block"][0]["category_evidence"]
            check("forced-block evidence mentions matched power/life",
                  "incoming_power=5" in ev and "own_life=2" in ev, ev)
        if by_cat.get("free-win"):
            ev = by_cat["free-win"][0]["category_evidence"]
            check("free-win evidence names the stack card and hand answer",
                  "Emrakul" in ev and "Force of Will" in ev and "LOW-PRECISION" in ev, ev)


def test_negative_rows_never_emitted():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        # Restrict to the tactic categories: graded-midgame has NO content
        # heuristic by design (turn-range + type is its whole eligibility),
        # so NONLETHAL_ROW (index 4) is legitimately a graded-midgame
        # candidate — it must NOT show up under any of the other categories.
        lines = _run_mine(tmp, corpus,
                          categories="lethal-now,save-vs-lethal,forced-block,free-win,mulligan")
        # None of the negative rows' decision_index should ever appear for
        # game-101: index 0 (null prompt), 1 (n_options=1), 2 (fallback set),
        # 4 (non-lethal board, no category above should match it either).
        bad_indices = {0, 1, 2, 4}
        offenders = [l for l in lines
                     if l["source"]["game_file"] == "game-101-1000.jsonl"
                     and l["source"]["decision_index"] in bad_indices]
        check("no candidate emitted from null-prompt/single-option/fallback/"
              "non-lethal rows", len(offenders) == 0,
              "offenders: %r" % offenders)


def test_eligibility_filter_is_load_bearing():
    """FALLBACK_ROW and SINGLE_OPTION_ROW both reuse the LETHAL_NOW-triggering
    prompt (own power 7 >= opp life 6) — if scan()'s `eligible(d)` gate were
    ever deleted or bypassed, these rows would start passing check_lethal_now
    and this test would fail (extra lethal-now candidates at decision_index
    1 and 2, alongside the legitimate one at index 3)."""
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        lines = _run_mine(tmp, corpus)
        lethal = [l for l in lines if l["category"] == "lethal-now"
                  and l["source"]["game_file"] == "game-101-1000.jsonl"]
        indices = sorted(l["source"]["decision_index"] for l in lethal)
        check("only the eligible lethal-now row (index 3) is emitted, "
              "not the fallback (2) or single-option (1) rows sharing its "
              "board text", indices == [3], "got %r" % indices)


def test_decision_index_is_file_order_not_eligible_order():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        lines = _run_mine(tmp, corpus)
        want = {"lethal-now": 3, "save-vs-lethal": 5, "forced-block": 6,
                "free-win": 7}
        for cat, idx in want.items():
            got = [l["source"]["decision_index"] for l in lines
                   if l["category"] == cat
                   and l["source"]["game_file"] == "game-101-1000.jsonl"]
            check("%s decision_index == %d (0-based, ALL decision rows in "
                  "file order, not just eligible ones)" % (cat, idx),
                  got == [idx], "got %r" % got)
        mull = [l["source"]["decision_index"] for l in lines
                if l["category"] == "mulligan"
                and l["source"]["game_file"] == "game-101-1000.jsonl"]
        check("mulligan decision_index == 8", mull == [8], "got %r" % mull)


def test_replayable_flag():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        lines = _run_mine(tmp, corpus)
        det = [l for l in lines if l["source"]["game_file"] == "game-101-1000.jsonl"
               and l["category"] == "mulligan"]
        llm = [l for l in lines if l["source"]["game_file"] == "game-202-2000.jsonl"]
        check("ai/ai game -> replayable=True", det and det[0]["replayable"] is True)
        check("llm/ai game -> replayable=False", llm and llm[0]["replayable"] is False)


def test_schema_required_fields_for_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        lines = _run_mine(tmp, corpus)
        check("wrote at least one candidate", len(lines) > 0)
        source_required = ("engine_sha", "overlay_sha", "deck_a", "deck_b",
                            "controllers", "seed", "swap", "decision_index",
                            "decision_type", "seat", "turn")
        all_ok = True
        for l in lines:
            ok = (
                l.get("schema") == "puzzle-position.v1"
                and isinstance(l.get("id"), str) and len(l["id"]) == 16
                and l.get("category") in mp.CATEGORIES
                and isinstance(l.get("source"), dict)
                and all(k in l["source"] for k in source_required)
                and isinstance(l.get("prompt"), str)
                and isinstance(l.get("options"), list) and len(l["options"]) >= 2
                and isinstance(l.get("n_options"), int) and l["n_options"] >= 2
                and l.get("status") == "candidate"
                and isinstance(l.get("replayable"), bool)
                and l.get("split") == "open"
                and isinstance(l.get("category_evidence"), str)
                and "answer_key" not in l
            )
            if not ok:
                all_ok = False
                print("  bad candidate: %r" % l)
        check("every candidate carries all status=candidate required fields "
              "(and omits answer_key)", all_ok)


def test_graded_midgame_deterministic_across_runs():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        lines1 = _run_mine(tmp, corpus, out_name="run1.jsonl",
                          categories="graded-midgame", max_per_category=8)
        lines2 = _run_mine(tmp, corpus, out_name="run2.jsonl",
                          categories="graded-midgame", max_per_category=8)
        ids1 = sorted(l["id"] for l in lines1)
        ids2 = sorted(l["id"] for l in lines2)
        check("graded-midgame sample has requested size", len(ids1) == 8,
              "got %d" % len(ids1))
        check("same --sample-seed -> byte-identical id set across two runs",
              ids1 == ids2, "%r vs %r" % (ids1, ids2))

        # different seed should (with overwhelming probability, 20 rows / 8
        # picks) select a different subset.
        lines3 = _run_mine(tmp, corpus, out_name="run3.jsonl",
                          categories="graded-midgame", max_per_category=8,
                          sample_seed=999999)
        ids3 = sorted(l["id"] for l in lines3)
        check("different --sample-seed changes the selected subset",
              ids3 != ids1)

        turns = sorted({l["source"]["turn"] for l in lines1})
        check("graded-midgame sample spans multiple turns (stratified, not "
              "clumped)", len(turns) >= 4, "turns=%r" % turns)


def test_only_requested_categories_scanned():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        lines = _run_mine(tmp, corpus, categories="mulligan")
        cats = {l["category"] for l in lines}
        check("--categories mulligan emits only mulligan candidates",
              cats == {"mulligan"}, "got %r" % cats)


def test_exclude_train_meta_skips_matching_game():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        meta_path = Path(tmp) / "meta.jsonl"
        # marks game-101 (seed=101, swap=False) as SFT-contaminated
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"seed": 101, "swap": False, "source_file": "x"}) + "\n")
        lines = _run_mine(tmp, corpus, exclude_train_meta=str(meta_path))
        offenders = [l for l in lines if l["source"]["seed"] == 101]
        check("games listed in --exclude-train-meta are skipped entirely",
              len(offenders) == 0, "offenders: %r" % offenders)
        others = [l for l in lines if l["source"]["seed"] != 101]
        check("un-listed games still get excluded_from_sft_overlap=True",
              others and all(l.get("excluded_from_sft_overlap") is True for l in others))


def test_exclude_train_meta_unrecoverable_warns_and_marks_false():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        meta_path = Path(tmp) / "meta_bad.jsonl"
        # rows with no 'seed' field at all -> identity not recoverable
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"prebuilt_kind": "x", "line": 1}) + "\n")
        exclude_set, reliable, any_given, warnings = mp.load_exclude_meta([str(meta_path)])
        check("unrecoverable meta file is detected", not reliable)
        check("a warning is printed naming the file", any("UNAVAILABLE" in w for w in warnings), warnings)

        lines = _run_mine(tmp, corpus, exclude_train_meta=str(meta_path))
        check("candidates from an unrecoverable-meta run are conservatively "
              "marked excluded_from_sft_overlap=False (never guessed clean)",
              lines and all(l.get("excluded_from_sft_overlap") is False for l in lines))


# ---------------------------------------------------------------- runner

def test_id_unique_per_line_and_category_salted():
    # One decision can match several categories; each emitted line must carry
    # a UNIQUE id (category is part of the hash). This test fails if category
    # is ever dropped from the id composition (the 382-lines/359-ids collision
    # found by the pre-commit verifier on runs/stage0-calibration).
    d = {"t": "decision", "type": "play", "seat": "A", "turn": 5, "phase": "MAIN1",
         "n_options": 3, "choice": 0, "options": ["a", "b", "c"],
         "prompt": "YOU [A] life 20", "fallback": None}
    g = {"engine_sha": "e" * 40, "overlay_sha": "o" * 12, "seed": 4242,
         "swap": False, "deck_a": "X", "deck_b": "Y",
         "controller_a": "random", "controller_b": "random"}
    a = mp.make_candidate("lethal-now", d, g, "c", "game-4242.jsonl", 7,
                          "ev", None, False)
    b = mp.make_candidate("save-vs-lethal", d, g, "c", "game-4242.jsonl", 7,
                          "ev", None, False)
    check("same decision, different category -> different id", a["id"] != b["id"],
          "%s vs %s" % (a["id"], b["id"]))
    check("same decision, same category -> stable id",
          a["id"] == mp.make_candidate("lethal-now", d, g, "c",
                                       "game-4242.jsonl", 7, "ev", None,
                                       False)["id"])
    check("source block (replay address) identical across categories",
          a["source"] == b["source"])
    # Same seed/swap/index/category but a DIFFERENT game file must get a
    # different id: seeds are reused across match games, and llm games are
    # non-deterministic, so the game instance is part of identity.
    c2 = mp.make_candidate("lethal-now", d, g, "c", "game-4242-RERUN.jsonl", 7,
                           "ev", None, False)
    check("same tuple, different game file -> different id", a["id"] != c2["id"],
          "%s vs %s" % (a["id"], c2["id"]))
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        lines = _run_mine(tmp, corpus)
        ids = [l["id"] for l in lines]
        check("all emitted ids unique across the fixture corpus",
              len(ids) == len(set(ids)), "%d lines / %d ids" % (len(ids), len(set(ids))))


def test_schema_file_cross_check():
    # Read the ACTUAL schema file and cross-check a real emitted candidate, so
    # the hand-maintained field list in the other test cannot silently drift
    # from eval/schemas/puzzle-position.v1.schema.json.
    schema_path = (evalpath.EVAL / "schemas"
                   / "puzzle-position.v1.schema.json")
    with open(schema_path) as fh:
        schema = json.load(fh)
    check("schema: answer_key is NOT unconditionally required",
          "answer_key" not in schema["required"], str(schema["required"]))
    cond = schema.get("if", {})
    check("schema: conditional keys on status==candidate",
          cond.get("properties", {}).get("status", {}).get("const") == "candidate",
          json.dumps(cond))
    check("schema: non-candidate instances still require answer_key",
          "answer_key" in schema.get("else", {}).get("required", []),
          json.dumps(schema.get("else")))
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_scan_dir(tmp)
        lines = _run_mine(tmp, corpus)
        sample = lines[0]
        missing = [k for k in schema["required"] if k not in sample]
        check("emitted candidate carries every schema-required field",
              not missing, "missing: %r" % missing)
        unknown = [k for k in sample if k not in schema["properties"]]
        check("emitted candidate has no fields absent from the schema",
              not unknown, "unknown: %r" % unknown)


def test_the_v23_mana_line_still_parses():
    line = ("  mana available: {W:3 U:1 C:1} (3 untapped sources; each is counted under every colour it can make, "
            "so the colours add up to more than can be spent)")
    check("v23 mana line gives 3 sources", mp._first_int(mp._MANA_SOURCES_RE, line) == 3, line)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print("--- %s ---" % t.__name__)
        t()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
