#!/usr/bin/env python3
"""Self-test for prompt_contract.py: synthetic fixtures, no real corpus, no
network. Run: python3 eval/test_prompt_contract.py

WHAT WOULD MAKE THIS FAIL:
  - test_contract_sha_stable: contract_sha() depending on dict/set iteration
    order, os.environ, the clock, or process identity (subprocess must agree),
    or the pasted frozen literals being tampered with (system_template_sha
    would drift off FROZEN_AGAINST_JAVA_SHA).
  - test_render_deterministic: any nondeterminism in render() — unseeded
    ordering, env reads, clock reads — across two calls or two processes.
  - test_user_message_verbatim: render() stripping, reflowing, or normalizing
    row['prompt'] (including trailing whitespace and non-ASCII).
  - test_frozen_template_matches_java_today: someone editing the frozen
    SYS_*_V1 literals while the Java is unchanged (sha matches but a piece is
    byte-different). If the JAVA drifted instead, the test prints a loud
    [drift] notice and passes — v1 is frozen on purpose; mint v2 to adopt.
  - test_interface_vs_play_split: parse() laundering a formatting failure
    into a Magic decision (out-of-range accepted as PASS, prose scored as a
    play) or failing to mark the explicit PASS menu pick as is_pass/choice=-1.
  - test_strictness_gap: lenient recovering anything beyond a lone in-range
    bare number, or strict accepting one.
  - test_markdown_and_synonym: regressions on the observed failure modes
    (markdown-wrapped tags, ANSWER: synonym, missing colon, trailing
    parenthetical, truncated 'CHOICE: ' with no digit, 157x degenerate
    'CHOICE: 1' repetition).
  - test_families: count/blocks/mulligan acceptance drifting off the harness
    ports (PICKS: NONE must be a valid size-0 pick, BLOCKS overflow must be
    count_out_of_range, decline slot must log choice=-1 with no option_index).
  - test_render_rejects_bad_rows: render() raising SystemExit instead of
    ValueError, or silently rendering a promptless/1-option row.
  - test_plan_resolution: the holdouts.json name map or eval/decks/*.plan
    reads breaking, or plan_sha not being sha256(text)[:16].
  - test_schema_selfconsistent: code emitting a key the schema does not
    declare, or the schema requiring a key the code does not emit.
  - test_no_network_imports: a network library creeping into the module.
  - test_no_answer_key_leakage: render() letting ANY graded field of a
    puzzle-position row reach the message text — answer_key (option_scores /
    option_wp / option_wp_se / best_option_index / caveats), verified_by,
    status, split, or category/category_evidence. `category` is the sharpest
    edge: putting "lethal-now" in the prompt hands the model the answer and
    silently invalidates every PUZ number. This test fails the instant a
    future edit widens render() from its field whitelist (prompt, options,
    n_options, source.seat, source.decision_type) to "render the row".
"""

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompt_contract as pc  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name,
                         (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ------------------------------------------------------------- fixtures

PLAY_PROMPT = (
    "== Turn 3 MAIN1 (active: UR Delver) ==\n"
    "YOU [UR Delver] life 18 | poison 0/10\n"
    "  hand(2): Lightning Bolt {R} (castable), Brainstorm {U} (castable)\n"
    "OPP [Burn] life 6 | poison 0/10\n"
    "stack (top first): (empty)\n"
    "RECENT EVENTS (oldest->newest):\n"
    "  - Turn 3 (UR Delver)\n"
    "  - Ætherling test — unicode survives verbatim\n"
    "\nDECISION: You have priority (MAIN1, your main phase). Choose ONE action:\n"
    "  1) Cast Lightning Bolt\n"
    "  2) Cast Brainstorm\n"
    "  3) Play Volcanic Island\n"
    "  4) PASS (do nothing)\n"
    "Reason briefly, then end with EXACTLY: CHOICE: <number>  \n")


def play_row(n_options=4):
    return {"t": "decision", "seat": "UR Delver", "turn": 3, "phase": "MAIN1",
            "type": "play", "n_options": n_options,
            "options": ["Cast Lightning Bolt", "Cast Brainstorm",
                        "Play Volcanic Island", "PASS (do nothing)"],
            "prompt": PLAY_PROMPT, "tier": "smart", "choice": 0,
            "fallback": None}


def puzzle_row():
    return {"schema": "puzzle-position.v1", "id": "abc123",
            "category": "lethal-now",
            "source": {"engine_sha": "c1c64a5", "overlay_sha": "6c3b0bd",
                       "deck_a": "UR Delver", "deck_b": "Burn",
                       "controllers": "ai/ai", "seed": 20017, "swap": False,
                       "decision_index": 41, "game_file": "game-20017-1.jsonl",
                       "turn": 3, "decision_type": "play",
                       "seat": "UR Delver"},
            "prompt": PLAY_PROMPT,
            "options": ["Cast Lightning Bolt", "Cast Brainstorm",
                        "Play Volcanic Island", "PASS (do nothing)"],
            "n_options": 4, "split": "open", "status": "candidate"}


def target_row(n_options=3):
    return {"t": "decision", "seat": "Burn", "type": "target",
            "n_options": n_options,
            "options": ["Goblin Guide", "Monastery Swiftspear", "Burn player"],
            "prompt": "PRIORITY: you\nDECISION: choose a target\nOptions:\n"
                      "  1) Goblin Guide\n  2) Monastery Swiftspear\n"
                      "  3) Burn player\n"
                      "Reason briefly, then end with EXACTLY: CHOICE: <number>\n"}


def mulligan_row():
    return {"t": "decision", "seat": "Burn", "turn": 0, "type": "mulligan",
            "n_options": 2, "options": ["KEEP", "MULLIGAN"],
            "prompt": "MULLIGAN DECISION. This is a 7-card hand with 2 "
                      "land(s): Mountain, Mountain. End with EXACTLY one "
                      "word: KEEP or MULLIGAN.\n"}


def count_row(n_options=3):
    return {"t": "decision", "seat": "Burn", "type": "attackers",
            "n_options": n_options,
            "options": ["Goblin Guide", "Monastery Swiftspear", "Eidolon"],
            "prompt": "PRIORITY: you\nDECISION: declare attackers\n"
                      "Choose between 0 and 3 of these (by number):\n"
                      "  1) Goblin Guide\n  2) Monastery Swiftspear\n"
                      "  3) Eidolon\nEnd with EXACTLY: PICKS: <numbers>\n"}


def blocks_row(n_options=2):
    return {"t": "decision", "seat": "UR Delver", "type": "blockers",
            "n_options": n_options,
            "options": ["Delver of Secrets", "Murktide Regent"],
            "prompt": "COMBAT — declare blockers. Incoming: Goblin Guide "
                      "(2/2). Your blockers: Delver of Secrets, Murktide "
                      "Regent. End with EXACTLY: BLOCKS: blocker->attacker, "
                      "...   (or BLOCKS: NONE)\n"}


# ----------------------------------------------------------------- tests

def test_contract_sha_stable():
    s1 = pc.contract_sha()
    s2 = pc.contract_sha()
    check("sha_16_lower_hex", len(s1) == 16
          and all(c in "0123456789abcdef" for c in s1), repr(s1))
    check("sha_two_calls_identical", s1 == s2)
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import prompt_contract; "
         "print(prompt_contract.contract_sha())" % str(EVAL_DIR)],
        capture_output=True, text=True)
    check("sha_subprocess_identical",
          out.returncode == 0 and out.stdout.strip() == s1,
          "rc=%s stdout=%r stderr=%r" % (out.returncode, out.stdout[-80:],
                                         out.stderr[-200:]))
    m = pc.contract_manifest()
    check("manifest_template_sha_matches_frozen",
          m["system_template_sha"] == pc.FROZEN_AGAINST_JAVA_SHA,
          "pasted literals were tampered with: %s != %s"
          % (m["system_template_sha"], pc.FROZEN_AGAINST_JAVA_SHA))
    check("manifest_contract_sha", m["contract_sha"] == s1)
    stamped = pc.stamp({"x": 1})
    check("stamp_sets_identity", stamped["contract"] == pc.CONTRACT_VERSION
          and stamped["contract_sha"] == s1 and stamped["x"] == 1)


def test_families():
    # mulligan
    r = pc.render(mulligan_row())
    check("mulligan_menu", r["menu_numbers"] == [1, 2]
          and r["decline_menu_number"] is None
          and r["pass_option_index"] is None)
    p = pc.parse("KEEP", r)
    check("mulligan_keep", p["outcome"] == "ok" and p["option_index"] == 0
          and p["choice"] == 0 and p["menu_number"] == 1)
    p = pc.parse("MULLIGAN", r)
    check("mulligan_mull", p["outcome"] == "ok" and p["option_index"] == 1
          and p["choice"] == 1)
    p = pc.parse("i keep", r)
    check("mulligan_i_keep", p["outcome"] == "ok" and p["option_index"] == 0)
    p = pc.parse("hmm", r)
    check("mulligan_no_tag", p["outcome"] == "interface_failure"
          and p["interface_error"] == "no_tag")
    p = pc.parse("1", r, "lenient")
    check("mulligan_lenient_bare_1", p["outcome"] == "ok"
          and p["option_index"] == 0 and p["recovered_by"] == "bare_number")
    # count
    r = pc.render(count_row())
    p = pc.parse("attack with both\nPICKS: 1,3", r)
    check("count_picks_1_3", p["outcome"] == "ok" and p["picks"] == [0, 2]
          and p["picks_size"] == 2 and p["choice"] == 2)
    p = pc.parse("PICKS: NONE", r)
    check("count_picks_none_valid_size0", p["outcome"] == "ok"
          and p["picks"] == [] and p["picks_size"] == 0 and p["choice"] == 0)
    p = pc.parse("no picks tag here", r)
    check("count_no_tag", p["interface_error"] == "no_tag",
          "bare-word 'picks' in prose must not become a size-0 pick")
    p = pc.parse("PICKS: 9", r)
    check("count_all_oor_digits", p["interface_error"] == "out_of_range")
    p = pc.parse("2", r, "lenient")
    check("count_no_bare_number_recovery", p["interface_error"] == "no_tag",
          "a lone number cannot express a subset")
    # blocks
    r = pc.render(blocks_row())
    p = pc.parse("BLOCKS: NONE", r)
    check("blocks_none", p["outcome"] == "ok" and p["blocks_count"] == 0
          and p["choice"] == 0)
    p = pc.parse("BLOCKS: a->b, c->d", r)
    check("blocks_two", p["outcome"] == "ok" and p["blocks_count"] == 2
          and p["choice"] == 2)
    p = pc.parse("no blocks here", r)
    check("blocks_no_tag", p["interface_error"] == "no_tag")
    p = pc.parse("BLOCKS: a->b, c->d, e->f", r)
    check("blocks_count_out_of_range",
          p["outcome"] == "interface_failure"
          and p["interface_error"] == "count_out_of_range"
          and p["blocks_count"] == 3)
    # non-play single decline slot
    r = pc.render(target_row())
    p = pc.parse("CHOICE: 4", r)      # n_options+1 == decideOne decline slot
    check("single_decline_slot", p["outcome"] == "ok"
          and p["option_index"] is None and p["choice"] == -1)
    p = pc.parse("CHOICE: 5", r)
    check("single_past_decline_oor", p["interface_error"] == "out_of_range")
    # unknown family
    row = play_row()
    row["type"] = "damage-order"
    r = pc.render(row)
    check("unknown_family_renders", r["family"] == "unknown")
    p = pc.parse("CHOICE: 1", r)
    check("unknown_family_unsupported",
          p["outcome"] == "interface_failure"
          and p["interface_error"] == "unsupported_family")


def test_frozen_template_matches_java_today():
    """If the working-tree Java still hashes to FROZEN_AGAINST_JAVA_SHA, every
    pasted literal must be byte-equal to its PreambleBuilder attribute — a
    mismatch there means someone edited the FROZEN literals (the fail case).
    If the Java hash DIFFERS, the Java drifted: print loudly and PASS, because
    v1 is frozen on purpose. This test therefore fails ONLY on tampered
    literals — Java drift is expected lifecycle, literal drift is corruption."""
    try:
        from make_sft_data import PreambleBuilder, DEFAULT_JAVA
        pb = PreambleBuilder(DEFAULT_JAVA)
    except Exception as e:  # unreadable/drifted-beyond-parse Java == drift
        print("[drift] PreambleBuilder(DEFAULT_JAVA) failed (%s); "
              "LlmSpikeGui framing changed since prompt-contract.v1 was "
              "frozen; v1 is frozen ON PURPOSE - mint v2 to adopt" % e)
        check("frozen_template_drift_notice", True)
        return
    pieces = [pb.seat_prefix, pb.seat_suffix, pb.plan_prefix, pb.plan_suffix,
              pb.body, pb.format_verbose, pb.tail]
    java_sha = hashlib.sha256("".join(pieces).encode("utf-8")).hexdigest()[:16]
    if java_sha != pc.FROZEN_AGAINST_JAVA_SHA:
        print("[drift] LlmSpikeGui framing changed since prompt-contract.v1 "
              "was frozen; v1 is frozen ON PURPOSE - mint v2 to adopt "
              "(java=%s frozen=%s)" % (java_sha, pc.FROZEN_AGAINST_JAVA_SHA))
        check("frozen_template_drift_notice", True)
        return
    frozen = [pc.SYS_SEAT_PREFIX_V1, pc.SYS_SEAT_SUFFIX_V1,
              pc.SYS_PLAN_PREFIX_V1, pc.SYS_PLAN_SUFFIX_V1, pc.SYS_BODY_V1,
              pc.SYS_FORMAT_V1, pc.SYS_TAIL_V1]
    names = ["seat_prefix", "seat_suffix", "plan_prefix", "plan_suffix",
             "body", "format_verbose", "tail"]
    for name, a, b in zip(names, frozen, pieces):
        check("frozen_literal_%s_byte_equal" % name, a == b)
    plan = (EVAL_DIR / "decks" / "ur-delver.plan").read_text(
        encoding="utf-8").strip()
    check("assembly_matches_preamblebuilder_base",
          pc._system_message("UR Delver", plan)
          == pb.base("UR Delver", plan, terse=False))


def test_interface_vs_play_split():
    r = pc.render(play_row())
    p = pc.parse("CHOICE: 2", r)
    check("play_ok_choice_2", p["outcome"] == "ok" and p["option_index"] == 1
          and p["choice"] == 1 and p["is_pass"] is False)
    p = pc.parse("I think I will cast the bolt", r)
    check("play_prose_no_tag", p["outcome"] == "interface_failure"
          and p["interface_error"] == "no_tag" and p["option_index"] is None
          and p["choice"] is None)
    for strictness in ("strict", "lenient"):
        p = pc.parse("CHOICE: 9", r, strictness)
        check("play_oor_%s" % strictness,
              p["outcome"] == "interface_failure"
              and p["interface_error"] == "out_of_range"
              and p["menu_number"] == 9,
              "the harness would coerce this to PASS; the contract refuses")
    p = pc.parse("CHOICE: 4", r)      # == n_options: the explicit PASS option
    check("play_explicit_pass", p["outcome"] == "ok"
          and p["option_index"] == 3 and p["choice"] == -1
          and p["is_pass"] is True)
    p = pc.parse(None, r)
    check("play_none_empty_reply", p["interface_error"] == "empty_reply")
    p = pc.parse("", r)
    check("play_blank_empty_reply", p["interface_error"] == "empty_reply")
    p = pc.parse("CHOICE: 0", r)
    check("play_zero_oor", p["interface_error"] == "out_of_range")


def test_markdown_and_synonym():
    r = pc.render(play_row())
    for name, reply in (
            ("markdown_bold", "**CHOICE: 3**"),
            ("markdown_ticks", "`CHOICE: 3`"),
            ("answer_synonym", "ANSWER: 3"),
            ("missing_colon_flexible", "CHOICE 3"),
            ("trailing_parenthetical", "CHOICE: 3 (final answer)")):
        p = pc.parse(reply, r)
        check(name, p["outcome"] == "ok" and p["option_index"] == 2,
              repr(reply))
    # observed: fast-tier reply truncated to 'CHOICE: ' with NO digit
    # (runs/improve/iter-2 game-112 decision idx 124)
    p = pc.parse("I will attack and then CHOICE: ", r)
    check("truncated_tag_no_number", p["outcome"] == "interface_failure"
          and p["interface_error"] == "no_number")
    # observed: gemini degenerate repetition, 157x 'CHOICE: 1' (tournament
    # QF-1 decision_line 87) — last occurrence wins, still parses
    p = pc.parse("CHOICE: 1\n" * 157, r)
    check("degenerate_157x_repetition", p["outcome"] == "ok"
          and p["option_index"] == 0 and p["choice"] == 0)


#: Modules that would give prompt_contract.py the ability to reach the network.
#: The renderer is judged-prompt-construction only; it must never be able to
#: call a model itself. (puzzle_run.py owns the backends and the egress gate.)
NETWORK_MODULES = frozenset([
    "socket", "ssl", "urllib", "http", "ftplib", "telnetlib", "smtplib",
    "asyncio", "requests", "httpx", "subprocess",
])


def _imported_module_roots(src):
    """Top-level package name of every module imported anywhere in `src`.

    AST-based on purpose: a substring scan of the source (the original form of
    this check) reports a false positive on the word 'socket' appearing in a
    docstring, and — worse — a false negative on `__import__("soc" + "ket")`.
    """
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_no_network_imports():
    src = (EVAL_DIR / "prompt_contract.py").read_text(encoding="utf-8")
    imported = _imported_module_roots(src)
    for mod in sorted(NETWORK_MODULES):
        check("no_import_%s" % mod, mod not in imported)
    # __import__/importlib would route around the import-node scan above.
    check("no_dynamic_import",
          "__import__" not in src and "importlib" not in imported)


def test_plan_resolution():
    text, slug, sha, source = pc.resolve_plan("UR Delver")
    want = (EVAL_DIR / "decks" / "ur-delver.plan").read_text(
        encoding="utf-8").strip()
    check("plan_ur_delver_source", source == "deck-plan-file")
    check("plan_ur_delver_slug", slug == "ur-delver")
    check("plan_ur_delver_text", text == want)
    check("plan_ur_delver_sha", sha == hashlib.sha256(
        want.encode("utf-8")).hexdigest()[:16] and len(sha) == 16)
    text, slug, sha, source = pc.resolve_plan("No Such Deck")
    check("plan_unresolved", (text, slug, source) == ("", None, "unresolved")
          and sha == hashlib.sha256(b"").hexdigest()[:16])
    text, slug, sha, source = pc.resolve_plan("X", plan_text="custom")
    check("plan_explicit", source == "explicit" and text == "custom"
          and slug is None)
    # explicit text equal to a known .plan resolves its slug
    text, slug, sha, source = pc.resolve_plan("X", plan_text=want)
    check("plan_explicit_known_text_slug", source == "explicit"
          and slug == "ur-delver")


def test_render_deterministic():
    row = play_row()
    r1 = pc.render(row)
    r2 = pc.render(row)
    check("render_messages_identical", r1["messages"] == r2["messages"])
    check("render_sha_identical", r1["render_sha"] == r2["render_sha"]
          and len(r1["render_sha"]) == 16)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump(row, f)
        fixture = f.name
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, json; sys.path.insert(0, %r); import prompt_contract; "
         "row = json.load(open(%r, encoding='utf-8')); "
         "print(prompt_contract.render(row)['render_sha'])"
         % (str(EVAL_DIR), fixture)],
        capture_output=True, text=True)
    check("render_sha_subprocess_identical",
          out.returncode == 0 and out.stdout.strip() == r1["render_sha"],
          "rc=%s stderr=%r" % (out.returncode, out.stderr[-200:]))
    Path(fixture).unlink()


def test_render_rejects_bad_rows():
    row = play_row()
    row["prompt"] = None
    try:
        pc.render(row)
        check("prompt_none_valueerror", False, "no exception raised")
    except ValueError:
        check("prompt_none_valueerror", True)
    except SystemExit:
        check("prompt_none_valueerror", False, "SystemExit, not ValueError")
    row = play_row()
    del row["prompt"]
    try:
        pc.render(row)
        check("prompt_missing_valueerror", False, "no exception raised")
    except ValueError:
        check("prompt_missing_valueerror", True)
    row = play_row()
    row["n_options"] = 1
    try:
        pc.render(row)
        check("n_options_1_valueerror", False, "no exception raised")
    except ValueError:
        check("n_options_1_valueerror", True)
    except SystemExit:
        check("n_options_1_valueerror", False, "SystemExit, not ValueError")
    # edge: options shorter than n_options -> trusted n_options + warning
    row = play_row()
    row["options"] = row["options"][:2]
    r = pc.render(row)
    check("short_options_warned", r["n_options"] == 4
          and "options_shorter_than_n_options" in r["warnings"])
    # edge: non-str options -> warning, still renders
    row = play_row()
    row["options"] = ["Cast Lightning Bolt", 7, None, "PASS (do nothing)"]
    r = pc.render(row)
    check("non_string_options_warned", "non_string_options" in r["warnings"])


def test_schema_selfconsistent():
    schema = json.loads(
        (EVAL_DIR / "schemas" / "prompt-contract.v1.schema.json").read_text(
            encoding="utf-8"))
    defs = schema["$defs"]
    emitted = {
        "manifest": pc.contract_manifest(),
        "rendered": pc.render(play_row()),
        "parsed": pc.parse("CHOICE: 2", pc.render(play_row())),
    }
    for name, obj in sorted(emitted.items()):
        props = set(defs[name]["properties"])
        required = set(defs[name].get("required", []))
        keys = set(obj)
        check("schema_%s_keys_declared" % name, keys <= props,
              "undeclared: %s" % sorted(keys - props))
        check("schema_%s_required_emitted" % name, required <= keys,
              "missing: %s" % sorted(required - keys))
    # a failure-path parsed object emits the same key set (schema stability)
    p_fail = pc.parse("garbage", pc.render(play_row()))
    check("parsed_key_set_stable_across_outcomes",
          set(p_fail) == set(emitted["parsed"]))


def test_strictness_gap():
    r = pc.render(play_row())
    reply = "I like the second option best.\n3"
    p_strict = pc.parse(reply, r, "strict")
    p_len = pc.parse(reply, r, "lenient")
    check("bare_3_strict_no_tag", p_strict["outcome"] == "interface_failure"
          and p_strict["interface_error"] == "no_tag")
    check("bare_3_lenient_recovered", p_len["outcome"] == "ok"
          and p_len["option_index"] == 2
          and p_len["recovered_by"] == "bare_number")
    for strictness in ("strict", "lenient"):
        p = pc.parse("99", r, strictness)
        check("bare_99_fails_%s" % strictness,
              p["outcome"] == "interface_failure",
              "leniency is about format, never legality")
    both = pc.parse_both(reply, r)
    check("parse_both_shape", both["strict"] == p_strict
          and both["lenient"] == p_len)
    # two digit runs on the last line are ambiguous -> no recovery
    p = pc.parse("attack with 2 then 3", r, "lenient")
    check("ambiguous_two_runs_not_recovered",
          p["outcome"] == "interface_failure")


def test_user_message_verbatim():
    row = play_row()
    r = pc.render(row)
    check("user_verbatim", r["messages"][1]["content"] == row["prompt"])
    check("user_trailing_ws_kept",
          r["messages"][1]["content"].endswith("CHOICE: <number>  \n"))
    check("user_unicode_kept",
          "Ætherling test — unicode survives verbatim"
          in r["messages"][1]["content"])
    # puzzle-position shape: seat/decision_type read from row['source']
    r2 = pc.render(puzzle_row())
    check("puzzle_row_accepted", r2["decision_type"] == "play"
          and r2["family"] == "single"
          and r2["messages"][1]["content"] == PLAY_PROMPT)
    check("puzzle_row_plan_resolved", r2["plan_slug"] == "ur-delver"
          and r2["plan_source"] == "deck-plan-file")
    check("puzzle_vs_flat_same_render_sha",
          r2["render_sha"] == pc.render(play_row())["render_sha"])
    sys_msg = r["messages"][0]["content"]
    check("system_starts_with_seat_frame",
          sys_msg.startswith(pc.SYS_SEAT_PREFIX_V1 + "UR Delver"
                             + pc.SYS_SEAT_SUFFIX_V1))
    check("system_ends_with_tail", sys_msg.endswith(pc.SYS_TAIL_V1))


#: Every field of a puzzle-position row that encodes (or hints at) the graded
#: answer. None of these may reach the model. Values are canaries: unique
#: strings/numbers that cannot occur in a legitimately rendered prompt.
LEAKY_ROW_FIELDS = {
    "category": "CANARYCATEGORYLETHALNOW",
    "category_evidence": "CANARYCATEGORYEVIDENCE",
    "verified_by": "CANARYVERIFIEDBY",
    "status": "CANARYSTATUS",
    "split": "CANARYSPLIT",
    "answer_key": {
        "option_scores": [10, 0, 0, 0],
        "option_wp": ["CANARYWP"],
        "option_wp_se": ["CANARYWPSE"],
        "best_option_index": "CANARYBEST",
        "verified_by": "CANARYKEYVERIFIEDBY",
        "caveats": ["CANARYCAVEAT"],
        "n_per_option": "CANARYN",
    },
    "option_scores": ["CANARYFLATSCORES"],
    "notes": "CANARYNOTES",
}


def test_no_answer_key_leakage():
    """THE benchmark-invalidating failure: the answer reaching the model.

    render() is a field WHITELIST (prompt / options / n_options /
    source.seat / source.decision_type). This asserts the complement: with
    every graded field of the row stuffed full of canaries, not one canary
    byte appears in either message, and the two messages are exactly the
    frozen system template + the verbatim prompt."""
    row = puzzle_row()
    row.update(LEAKY_ROW_FIELDS)
    # ...and canaries nested where a lazy renderer might reach for them.
    row["source"] = dict(row["source"])
    row["source"]["answer_key"] = {"option_scores": ["CANARYSRCSCORES"]}
    row["source"]["category"] = "CANARYSRCCATEGORY"

    rendered = pc.render(row)
    blob = json.dumps(rendered["messages"], sort_keys=True)

    def canaries(obj):
        if isinstance(obj, str):
            return [obj] if obj.startswith("CANARY") else []
        if isinstance(obj, dict):
            out = []
            for k, v in sorted(obj.items()):
                out.extend(canaries(k))
                out.extend(canaries(v))
            return out
        if isinstance(obj, list):
            out = []
            for v in obj:
                out.extend(canaries(v))
            return out
        return []

    planted = canaries(row)
    check("leak_canaries_planted", len(planted) >= 12)
    for c in sorted(set(planted)):
        check("no_leak_%s" % c, c not in blob)
    # Field NAMES are as damning as values: "answer_key"/"option_scores" in
    # the prompt means someone serialized the row into it.
    for name in ("answer_key", "option_scores", "option_wp", "verified_by",
                 "category_evidence", "best_option_index"):
        check("no_leak_fieldname_%s" % name, name not in blob)
    # Belt and braces: the messages are EXACTLY the two known-good strings,
    # so there is nowhere for a third field to hide.
    check("messages_are_exactly_two", len(rendered["messages"]) == 2)
    check("user_is_prompt_verbatim",
          rendered["messages"][1]["content"] == row["prompt"])
    check("system_is_template_plus_seat_and_plan",
          rendered["messages"][0]["content"]
          == pc._system_message("UR Delver", pc.resolve_plan("UR Delver")[0]))
    # A holdout row renders byte-identically to the same row marked open:
    # split must not be observable by the model.
    open_row = puzzle_row()
    open_row["split"] = "open"
    hold_row = puzzle_row()
    hold_row["split"] = "holdout"
    check("split_invisible_to_model",
          pc.render(open_row)["render_sha"] == pc.render(hold_row)["render_sha"])


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_")]
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


def test_v2_static_reference_matches_java():
    """The whole point of puz-v2: the puzzle lane and the live smart-tier lane
    show the model the SAME interface documentation. If the Java constant
    changes without a new frozen profile, this fails — lane drift becomes a
    test failure, not an archaeology find (the v1 exclusion rationale was
    discovered to be false 11 days after freeze)."""
    import make_sft_data
    pb = make_sft_data.PreambleBuilder(str(REPO / make_sft_data.DEFAULT_JAVA)
                                       if hasattr(make_sft_data, 'DEFAULT_JAVA')
                                       else 'forge-overlay/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java')
    check("v2 legend parity with live Java constant",
          pb.static_reference == prompt_contract.SYS_STATIC_REFERENCE_V2,
          "java %d chars vs frozen %d" % (len(pb.static_reference),
                                          len(prompt_contract.SYS_STATIC_REFERENCE_V2)))


def test_v2_profile_mints_new_sha_and_renders():
    sha1 = prompt_contract.contract_sha("puz-v1")
    sha2 = prompt_contract.contract_sha("puz-v2")
    check("v2 sha differs from v1", sha1 != sha2, "%s vs %s" % (sha1, sha2))
    row = {"prompt": "state\nOptions:\n  1) A\n  2) B", "options": ["A", "B"],
           "n_options": 2, "source": {"decision_type": "play", "seat": "X"}}
    r1 = prompt_contract.render(row, profile="puz-v1")
    r2 = prompt_contract.render(row, profile="puz-v2")
    s1 = r1["messages"][0]["content"]
    s2 = r2["messages"][0]["content"]
    check("v2 system message appends the legend",
          s2.startswith(s1) and "SNAPSHOT LEGEND" in s2
          and "@N" in s2 and "re-ask ONCE" in s2,
          "v2 len %d v1 len %d" % (len(s2), len(s1)))
    check("v1 system message unchanged (era preserved)",
          "SNAPSHOT LEGEND" not in s1)
