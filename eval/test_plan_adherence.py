#!/usr/bin/env python3
"""Self-test for plan_adherence.py: synthetic fixtures, no real corpus, no
network, no engine. Run: python3 eval/test_plan_adherence.py

WHAT WOULD MAKE THIS FAIL:
  * test_all_16_specs_load_and_validate — a typo'd card name in any
    eval/plans/*.milestones.json (the dominant text-extractor failure mode;
    this test is the whole reason the spec files are data and not code), a
    missing spec/plan file, or a deck field not matching the filename stem.
  * test_deck_resolution_by_plan_text — resolve_deck_slug trusting deck_a
    (model names!) instead of plan text, or guessing instead of returning
    None for an unknown plan.
  * per-kind tests — any extractor drifting from its documented semantics
    (deploy turn boundary, within-turn ordering, offered-denominator, zone
    counting, requires_state gating, the process_metrics port, face/creature
    classification).
  * test_plan_hold — the t-1 -> t adjacency rule miscounting transitions.
  * test_requires_state_casts_this_turn — counting casts before the last
    turn marker (inventing a storm count the prompt does not carry).
  * test_segment_comparison — downstream win rate not identity-keyed or
    missing its n / wide CI on n=2.
  * test_baselines_present — any attainment/rate block shipping without its
    explicit floor fields (the 88% lesson).
  * test_cluster_bootstrap_pinned — the byte-identical shared
    cluster_bootstrap_ci copy drifting from the contract's pinned values.
  * test_no_runs_reads — this test file acquiring a dependency on real logs.
  * test_cluster_order_invariance — cluster_block being fed clusters in
    dict-insertion order instead of key-sorted order (the bootstrap resamples
    BY INDEX, so ordering changes se/ci95 silently).
  * test_determinism_byte_identical — main() ignoring --generated-utc, or any
    nondeterminism creeping into summary.json / milestones.jsonl between two
    runs over the identical corpus.
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_adherence as pa  # noqa: E402
import process_metrics  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PLANS_DIR = Path(__file__).resolve().parent / "plans"
DECKS_DIR = Path(__file__).resolve().parent / "decks"

EXPECTED_SLUGS = [
    "ant-storm", "burn", "cephalid-breakfast", "death-and-taxes", "doomsday",
    "elves", "food-chain", "goblins", "grixis-reanimator", "infect", "lands",
    "mardu-energy", "miracles", "painter", "sneak-and-show", "ur-delver"]

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# --------------------------------------------------------------- fixtures

def game_line(seed=101, swap=False, deck_a="claude-opus-4.8", deck_b="Opp",
              controller_a="llm", controller_b="ai", winner="A",
              plan_a=None, plan_b=None):
    g = {"t": "game", "seed": seed, "swap": swap,
         "deck_a": deck_a, "deck_b": deck_b,
         "controller_a": controller_a, "controller_b": controller_b,
         "smart_a": "m-a", "smart_b": "m-b", "fast_a": "m-a", "fast_b": "m-b",
         "winner": winner, "turns": 10, "win_condition": "life",
         "duration_ms": 1000, "engine_sha": "deadbeef",
         "overlay_sha": "cafef00d", "deck_sha": "abc123"}
    if plan_a is not None:
        g["plan_a"] = plan_a
    if plan_b is not None:
        g["plan_b"] = plan_b
    return g


def drow(dtype="play", seat="claude-opus-4.8", turn=1, choice=-1,
         options=None, prompt=None, tier="smart", fallback=None):
    options = options if options is not None else ["PASS (do nothing)"]
    return {"t": "decision", "seat": seat, "turn": turn, "phase": "MAIN1",
            "type": dtype, "n_options": len(options), "choice": choice,
            "tier": tier, "latency_ms": 5, "fallback": fallback,
            "options": options, "prompt": prompt, "raw_reply": None}


def build_prompt(you_seat="claude-opus-4.8", you_life=20,
                 you_hand="Island, Island", you_board="(no nonland permanents)",
                 you_yard="(none)", opp_seat="Opp", opp_life=20,
                 opp_board="(no nonland permanents)", events=None):
    ev_lines = "".join("  - %s\n" % e for e in (events or ["Turn 1 (%s)" % you_seat]))
    n_hand = len([x for x in you_hand.split(", ") if x]) if you_hand else 0
    return (
        "== Turn 3 MAIN1 (active: %s) ==\n" % you_seat
        + "YOU [%s] life %d | poison 0/10\n" % (you_seat, you_life)
        + "  hand(%d): %s\n" % (n_hand, you_hand)
        + "  board: %s\n" % you_board
        + "  lands: 2 untapped / 2 total (Island x2 [untapped])\n"
        + "  mana available: {U:2} (2 untapped sources)\n"
        + "  yard(1): %s\n" % you_yard
        + "OPP [%s] life %d | poison 0/10\n" % (opp_seat, opp_life)
        + "  board: %s\n" % opp_board
        + "  lands: 2 untapped / 2 total (Mountain x2 [untapped])\n"
        + "  mana available: {R:2} (2 untapped sources)\n"
        + "  yard(0): (none)\n"
        + "  hand: 4 cards\n"
        + "stack (top first): (empty)\n"
        + "RECENT EVENTS (oldest->newest):\n"
        + ev_lines
        + "\nDECISION: You have priority (MAIN1, your main phase). "
        + "Choose ONE action:\n  1) x\nReason briefly, then end with "
        + "EXACTLY: CHOICE: <number>")


def ur_plan():
    return (DECKS_DIR / "ur-delver.plan").read_text(encoding="utf-8")


def burn_plan():
    return (DECKS_DIR / "burn.plan").read_text(encoding="utf-8")


def write_game(dir_path, fname, game, decisions):
    with open(Path(dir_path) / fname, "w", encoding="utf-8") as f:
        f.write(json.dumps(game) + "\n")
        for d in decisions:
            f.write(json.dumps(d) + "\n")


# ----------------------------------------------------------------- tests

def test_all_16_specs_load_and_validate():
    specs, errors = pa.load_specs(PLANS_DIR)
    check("specs_no_validation_errors", errors == [], "; ".join(errors[:5]))
    check("specs_all_16_present", sorted(specs) == EXPECTED_SLUGS,
          "got %s" % sorted(specs))
    for slug, spec in sorted(specs.items()):
        check("spec_%s_deck_matches_stem" % slug, spec.get("deck") == slug)
        pf = spec.get("plan_file")
        check("spec_%s_plan_file_exists" % slug,
              isinstance(pf, str) and (REPO / pf).is_file(), repr(pf))
        n_ms = len(spec.get("milestones") or [])
        check("spec_%s_6_to_14_milestones" % slug, 6 <= n_ms <= 14,
              "%d milestones" % n_ms)
        deck_txt = (DECKS_DIR / ("%s.txt" % slug)).read_text(encoding="utf-8")
        for ms in spec.get("milestones") or []:
            for name in pa._spec_names(ms):
                check("spec_%s_%s_card_in_decklist" % (slug, ms.get("id")),
                      name in deck_txt, repr(name))
                break  # one line per milestone keeps output readable; the
                # full check already ran inside load_specs/validate_spec
        fx = spec.get("fixtures")
        check("spec_%s_fixtures_listed_or_noted" % slug,
              (isinstance(fx, list) and len(fx) > 0)
              or isinstance(spec.get("fixtures_note"), str))


def test_deck_resolution_by_plan_text():
    g1 = game_line(deck_a="claude-opus-4.8", plan_a=ur_plan())
    check("resolve_model_name_seat_via_plan",
          pa.resolve_deck_slug(g1, "a") == "ur-delver")
    g2 = game_line(deck_a="Identity-A", plan_a=burn_plan())
    check("resolve_identity_seat_via_plan",
          pa.resolve_deck_slug(g2, "a") == "burn")
    g3 = game_line(deck_a="Some Unknown Pilot", plan_a="win the game somehow")
    check("resolve_unknown_plan_is_none", pa.resolve_deck_slug(g3, "a") is None)
    g4 = game_line(deck_a="UR Delver", plan_a=None)
    check("resolve_display_name_fallback",
          pa.resolve_deck_slug(g4, "a") == "ur-delver")
    # unresolved seats are counted, not guessed
    with tempfile.TemporaryDirectory() as td:
        write_game(td, "game-9-1.jsonl",
                   game_line(deck_a="X", deck_b="Y",
                             plan_a="mystery", plan_b="mystery"),
                   [drow(seat="X")])
        _rows, _pd, counters, _w = pa.evaluate_corpus([td], {})
        check("unresolved_counter_increments",
              counters["n_seats_unresolved"] == 2, str(counters))
    check("seat_to_ident_tie_prefers_a",
          pa.seat_to_ident({"deck_a": "M", "deck_b": "M"}, "M") == "a")


def test_kind_deploy_by_turn():
    ms = {"turn_max": 2, "require_prefix": "CAST",
          "chosen_option_matches": ["Delver of Secrets"]}
    opts = ["CAST Delver of Secrets {U} [..]", "PASS (do nothing)"]
    rows_t2 = [drow(turn=1, choice=-1), drow(turn=2, choice=0, options=opts)]
    r = pa.eval_deploy_by_turn(ms, rows_t2, {}, {})
    check("deploy_attains_at_turn_2", r["attained"] and r["attain_turn"] == 2)
    rows_t3 = [drow(turn=3, choice=0, options=opts)]
    r = pa.eval_deploy_by_turn(ms, rows_t3, {}, {})
    check("deploy_misses_at_turn_3",
          not r["attained"] and r["attain_turn"] is None)
    # prefix gate: a target row containing the name must not count as a cast
    rows_tgt = [drow(dtype="target", turn=1, choice=0,
                     options=["Delver of Secrets (1/1)[untapped]"])]
    r = pa.eval_deploy_by_turn(ms, rows_tgt, {}, {})
    check("deploy_requires_prefix", not r["attained"])


def test_kind_sequence_order():
    ms = {"first": ["Dark Ritual"], "then": ["Ad Nauseam"],
          "within_turn": True, "require_prefix": "CAST"}
    ok = [drow(turn=4, choice=0, options=["CAST Dark Ritual {B} [..]"]),
          drow(turn=4, choice=0, options=["CAST Ad Nauseam 3{B}{B} [..]"])]
    r = pa.eval_sequence_order(ms, ok, {}, {})
    check("sequence_attains_in_order_same_turn",
          r["attained"] and r["attain_turn"] == 4)
    wrong_order = [drow(turn=4, choice=0, options=["CAST Ad Nauseam 3{B}{B} [..]"]),
                   drow(turn=4, choice=0, options=["CAST Dark Ritual {B} [..]"])]
    check("sequence_rejects_reversed_order",
          not pa.eval_sequence_order(ms, wrong_order, {}, {})["attained"])
    split_turns = [drow(turn=3, choice=0, options=["CAST Dark Ritual {B} [..]"]),
                   drow(turn=4, choice=0, options=["CAST Ad Nauseam 3{B}{B} [..]"])]
    check("sequence_within_turn_rejects_cross_turn",
          not pa.eval_sequence_order(ms, split_turns, {}, {})["attained"])
    ms_x = dict(ms, within_turn=False)
    check("sequence_cross_turn_allows_later_then",
          pa.eval_sequence_order(ms_x, split_turns, {}, {})["attained"])


def test_kind_resource_discipline():
    ms = {"chosen_option_matches": ["Wasteland"], "denominator": "offered",
          "require_prefix": "CAST"}
    wl = "CAST Wasteland [{T}, Sacrifice CARDNAME: Destroy target nonbasic..]"
    rows = [drow(turn=1, choice=0, options=[wl, "PASS (do nothing)"]),
            drow(turn=2, choice=0, options=[wl, "PASS (do nothing)"]),
            drow(turn=3, choice=-1, options=[wl, "PASS (do nothing)"]),
            drow(turn=4, choice=-1, options=["PASS (do nothing)"])]  # not offered
    r = pa.eval_resource_discipline(ms, rows, {}, {})
    check("resource_discipline_2_of_3",
          r["numerator"] == 2 and r["denominator"] == 3, str(r))


def test_kind_combo_assembly_progress():
    ms = {"piece_names": ["Food Chain", "Squee, the Immortal", "Goblin Matron"],
          "zones": ["hand", "board", "yard"], "min_count": 3}
    prompt = build_prompt(
        you_hand="Food Chain 1{G}{G} (castable), Forest",
        you_board="Squee, the Immortal(1/1), Mountain",
        you_yard="Goblin Matron")
    rows = [drow(turn=3, prompt=prompt)]
    r = pa.eval_combo_assembly_progress(ms, rows, {}, {})
    check("combo_counts_across_three_zones",
          r["attained"] and r["attain_turn"] == 3, str(r))
    ms4 = dict(ms, min_count=4)
    check("combo_min_count_respected",
          not pa.eval_combo_assembly_progress(ms4, rows, {}, {})["attained"])
    # opponent's zones must not leak into the count
    p_opp = build_prompt(you_hand="Island",
                         opp_board="Squee, the Immortal(1/1)")
    r = pa.eval_combo_assembly_progress(dict(ms, min_count=1),
                                        [drow(turn=2, prompt=p_opp)], {}, {})
    check("combo_ignores_opp_board", not r["attained"])


def test_kind_payoff_condition():
    ms = {"chosen_option_matches": ["Lightning Bolt"],
          "require_prefix": "CAST",
          "requires_state": {"opp_life_at_most": 6}}
    bolt = ["CAST Lightning Bolt {R} [..]", "PASS (do nothing)"]
    low = [drow(turn=5, choice=0, options=bolt,
                prompt=build_prompt(opp_life=5))]
    high = [drow(turn=5, choice=0, options=bolt,
                 prompt=build_prompt(opp_life=15))]
    check("payoff_attains_at_low_life",
          pa.eval_payoff_condition(ms, low, {}, {})["attained"])
    check("payoff_rejects_at_high_life",
          not pa.eval_payoff_condition(ms, high, {}, {})["attained"])


def test_kind_protection_usage_matches_process_metrics():
    threats = list(process_metrics.THREATS)
    prots = list(process_metrics.PROTECTION)
    spec = {"threat_names": threats}
    ms = {"chosen_option_matches": prots,
          "requires_state": "own_threat_on_battlefield"}
    daze = "CAST Daze 1{U} [Counter target spell..]"
    board_prompt = build_prompt(you_seat="UR Delver", opp_seat="Burn",
                                you_board="Delver of Secrets(1/1)")
    empty_prompt = build_prompt(you_seat="UR Delver", opp_seat="Burn")
    rows = [
        drow(seat="UR Delver", turn=2, choice=0,
             options=[daze, "PASS (do nothing)"], prompt=board_prompt),
        drow(seat="UR Delver", turn=3, choice=-1,
             options=[daze, "PASS (do nothing)"], prompt=board_prompt),
        drow(seat="UR Delver", turn=4, choice=0,
             options=[daze, "PASS (do nothing)"], prompt=empty_prompt),
    ]
    r = pa.eval_protection_usage(ms, rows, spec, {})
    # same rows through the ORIGINAL process_metrics implementation
    entries = [{"file": "game-1-1.jsonl",
                "game": game_line(deck_a="UR Delver", deck_b="Burn",
                                  controller_a="llm"),
                "decisions": rows}]
    pm = process_metrics.plan_adherence(entries, {"UR Delver"},
                                        {"UR Delver", "Burn"})
    pu = pm["protection_usage"]
    check("protection_port_same_opportunities",
          r["denominator"] == pu["opportunities"] == 2,
          "%s vs %s" % (r["denominator"], pu["opportunities"]))
    check("protection_port_same_used",
          r["numerator"] == pu["used"] == 1,
          "%s vs %s" % (r["numerator"], pu["used"]))


def test_kind_target_split():
    game = game_line(deck_a="UR Delver", deck_b="Burn")
    ms = {"spells": ["Lightning Bolt"], "report": "face_vs_creature_ratio"}
    prompt = build_prompt(you_seat="UR Delver", opp_seat="Burn") + (
        "\nDECISION: Choose the TARGET for Lightning Bolt {R} "
        "[CARDNAME deals 3 damage to any target.]")
    opts = ["Burn", "UR Delver",
            "Monastery Swiftspear (1/2)[tapped] [Burn's Battlefield]"]
    rows = [
        drow(dtype="target", seat="UR Delver", turn=2, choice=0,
             options=opts, prompt=prompt),                       # face
        drow(dtype="target", seat="UR Delver", turn=3, choice=2,
             options=opts, prompt=prompt),                       # creature
        drow(dtype="target", seat="UR Delver", turn=4, choice=0,
             options=["Burn", "UR Delver", "Wasteland[untapped]"],
             prompt=prompt),                                     # face
        drow(dtype="target", seat="UR Delver", turn=5, choice=2,
             options=["Burn", "UR Delver", "Wasteland[untapped]"],
             prompt=prompt),                                     # other
    ]
    r = pa.eval_target_split(ms, rows, {}, game)
    check("target_split_face_creature_other",
          r["numerator"] == 2 and r["denominator"] == 3
          and "2/1/1" in r["evidence"], str(r))
    floor = sum(r["_floor_terms"]) / len(r["_floor_terms"])
    check("target_split_uniform_floor",
          abs(floor - (2.0 / 3.0)) < 1e-9, str(floor))
    # spell filter: a row for another spell is excluded
    other_prompt = prompt.replace("Lightning Bolt", "Searing Blaze")
    r2 = pa.eval_target_split(ms, [drow(dtype="target", seat="UR Delver",
                                        turn=2, choice=0, options=opts,
                                        prompt=other_prompt)], {}, game)
    check("target_split_spell_filter", r2["numerator"] == 0
          and r2["denominator"] == 0)


def test_plan_hold():
    ms = {"piece_names": ["Delver of Secrets"], "zones": ["board"],
          "min_count": 1}
    on = build_prompt(you_board="Delver of Secrets(1/1)")
    off = build_prompt(you_board="(no nonland permanents)")
    rows = [drow(turn=1, prompt=off), drow(turn=2, prompt=on),
            drow(turn=3, prompt=on), drow(turn=4, prompt=on),
            drow(turn=5, prompt=off)]
    r = pa.eval_combo_assembly_progress(ms, rows, {}, {})
    check("plan_hold_2_of_3",
          r["hold_num"] == 2 and r["hold_den"] == 3, str(r))


def test_requires_state_casts_this_turn():
    events = [
        "Turn 3 (Opp)",
        "Opp cast Lightning Bolt",
        "Turn 4 (claude-opus-4.8)",
        "claude-opus-4.8 cast Dark Ritual",
        "claude-opus-4.8 cast Dark Ritual",
        "claude-opus-4.8 cast Infernal Tutor",
    ]
    prompt = build_prompt(events=events)
    d = drow(prompt=prompt)
    check("casts_this_turn_counts_since_last_marker",
          pa.casts_this_turn(prompt) == 3, str(pa.casts_this_turn(prompt)))
    check("casts_at_least_3_holds",
          pa.state_holds({"casts_this_turn_at_least": 3}, d, {}))
    check("casts_at_least_4_does_not_hold",
          not pa.state_holds({"casts_this_turn_at_least": 4}, d, {}))


def test_segment_comparison_and_baselines():
    plan = ur_plan()
    spec = {
        "schema": "milestones.v1", "deck": "ur-delver",
        "plan_file": "eval/decks/ur-delver.plan",
        "threat_names": ["Delver of Secrets"],
        "combo_pieces": [],
        "milestones": [
            {"id": "m_first", "kind": "deploy_by_turn", "weight": 1.0,
             "description": "synthetic", "turn_max": 99,
             "require_prefix": "CAST",
             "chosen_option_matches": ["Delver of Secrets"]},
            {"id": "m_next", "kind": "deploy_by_turn", "weight": 1.0,
             "description": "synthetic", "turn_max": 99,
             "require_prefix": "CAST",
             "chosen_option_matches": ["Murktide Regent"]},
            {"id": "m_rate", "kind": "resource_discipline", "weight": 1.0,
             "description": "synthetic", "denominator": "offered",
             "require_prefix": "CAST",
             "chosen_option_matches": ["Wasteland"]},
            {"id": "m_split", "kind": "target_split", "weight": 1.0,
             "description": "synthetic",
             "spells": ["Lightning Bolt"],
             "report": "face_vs_creature_ratio"},
        ],
        "fixtures": [], "notes": "synthetic"}
    delver = ["CAST Delver of Secrets {U} [..]", "PASS (do nothing)"]
    murk = ["CAST Murktide Regent 5{U}{U} [..]", "PASS (do nothing)"]
    wl = ["CAST Wasteland [{T}, Sacrifice CARDNAME: ..]", "PASS (do nothing)"]
    tgt_prompt = build_prompt(you_seat="P1", opp_seat="Opp") + (
        "\nDECISION: Choose the TARGET for Lightning Bolt {R} [..]")

    def decisions(seat):
        return [
            drow(seat=seat, turn=2, choice=0, options=delver),
            drow(seat=seat, turn=5, choice=0, options=murk),
            drow(seat=seat, turn=3, choice=0, options=wl),
            drow(seat=seat, turn=3, choice=-1, options=wl),
            drow(dtype="target", seat=seat, turn=4, choice=0,
                 options=["Opp", "P1"], prompt=tgt_prompt),
        ]

    with tempfile.TemporaryDirectory() as td:
        write_game(td, "game-1-1.jsonl",
                   game_line(seed=1, deck_a="P1", deck_b="Opp",
                             plan_a=plan, winner="A"),
                   decisions("P1"))
        write_game(td, "game-2-1.jsonl",
                   game_line(seed=2, deck_a="P1", deck_b="Opp",
                             plan_a=plan, winner="B"),
                   decisions("P1"))
        rows, per_deck, counters, warns = pa.evaluate_corpus(
            [td], {"ur-delver": spec})
        summary = pa.build_summary(rows, per_deck, counters,
                                   {"ur-delver": spec}, warns, 1)
    dk = summary["by_deck"]["ur-delver"]
    seg = dk["segments"]["m_first"]
    wr = seg["downstream_win_rate"]
    check("segment_n_reached_2", seg["n_reached"] == 2, str(seg))
    check("segment_win_rate_half", abs(wr["est"] - 0.5) < 1e-9 and wr["n"] == 2)
    check("segment_ci_spans_wide",
          wr["ci95"][0] < 0.2 and wr["ci95"][1] > 0.8, str(wr["ci95"]))
    check("segment_turns_to_next",
          seg["next_milestone_id"] == "m_next"
          and abs(seg["mean_turns_to_next"] - 3.0) < 1e-9, str(seg))
    # baselines: every attainment block carries its explicit floor
    for mid, m in dk["milestones"].items():
        if "attain_rate" in m:
            check("baseline_never_attained_%s" % mid,
                  m["baselines"]["never_attained"] == 0.0
                  and m["baselines"]["always_attained"] == 1.0
                  and "margin_over_never_attained" in m, str(m.get("baselines")))
        if "rate" in m:
            check("baseline_rate_endpoints_%s" % mid,
                  m["baselines"]["always_take_rate"] == 1.0
                  and m["baselines"]["never_take_rate"] == 0.0
                  and m["baselines"]["n_opportunities"] == m["n_opportunities"])
        if "face_rate" in m:
            check("baseline_uniform_floor_%s" % mid,
                  "uniform_random_face_rate" in m["baselines"]
                  and "margin_over_uniform_random" in m)
    check("summary_top_level_baselines",
          summary["baselines"]["never_attained"] == 0.0
          and summary["baselines"]["always_attained"] == 1.0)
    check("rate_carries_denominator",
          dk["milestones"]["m_rate"]["n_opportunities"] == 4
          and dk["milestones"]["m_rate"]["numerator"] == 2)
    # jsonl rows carry the required schema fields
    r0 = rows[0]
    for field in ("schema", "run_dir", "game_file", "seat", "ident", "deck",
                  "milestone_id", "kind", "attained", "attain_turn",
                  "numerator", "denominator", "hold_num", "hold_den", "won",
                  "model", "evidence"):
        if field not in r0:
            check("row_field_%s" % field, False, "missing")
            break
    else:
        check("row_fields_complete", True)


def test_cluster_bootstrap_pinned():
    fixture = [[1.0, 1.0, 1.0, 0.0], [0.0, 0.0], [1.0, 0.0, 1.0], [1.0],
               [0.0, 0.0, 0.0, 1.0, 1.0]]
    r = pa.cluster_bootstrap_ci(fixture)
    check("cbci_n", r["n"] == 15 and r["n_clusters"] == 5)
    check("cbci_est", abs(r["est"] - 0.5333333333333333) < 1e-12)
    check("cbci_se", abs(r["se"] - 0.11691903702304797) < 1e-9)
    check("cbci_lo", abs(r["ci95"][0] - 0.3) < 1e-12)
    check("cbci_hi", abs(r["ci95"][1] - 0.7692307692307693) < 1e-12)
    check("cbci_single_cluster_no_se",
          pa.cluster_bootstrap_ci([[1.0, 0.0]])["se"] is None)
    check("cbci_empty", pa.cluster_bootstrap_ci([])["est"] is None)


def _order_spec():
    return {
        "schema": "milestones.v1", "deck": "ur-delver",
        "plan_file": "eval/decks/ur-delver.plan",
        "threat_names": ["Delver of Secrets"],
        "combo_pieces": [],
        "milestones": [
            {"id": "m_dep", "kind": "deploy_by_turn", "weight": 1.0,
             "description": "synthetic", "turn_max": 99,
             "require_prefix": "CAST",
             "chosen_option_matches": ["Delver of Secrets"]},
            {"id": "m_rate", "kind": "resource_discipline", "weight": 1.0,
             "description": "synthetic", "denominator": "offered",
             "require_prefix": "CAST",
             "chosen_option_matches": ["Wasteland"]},
        ],
        "fixtures": [], "notes": "synthetic"}


def _order_corpus(td):
    """Three games with DISTINCT per-game cluster values so the bootstrap
    can tell cluster orderings apart."""
    plan = ur_plan()
    delver = ["CAST Delver of Secrets {U} [..]", "PASS (do nothing)"]
    wl = ["CAST Wasteland [{T}, Sacrifice CARDNAME: ..]", "PASS (do nothing)"]
    per_game = [
        # (delver_choice, wl_choice_1, wl_choice_2)
        # NOT a palindrome in either metric: reversing the game order must
        # yield a cluster sequence different from the key-sorted one, or the
        # order-invariance test cannot detect an unsorted cluster_block feed.
        (0, 0, -1),    # attained;     rate obs [1, 0]
        (-1, 0, 0),    # not attained; rate obs [1, 1]
        (-1, -1, -1),  # not attained; rate obs [0, 0]
    ]
    for i, (dc, w1, w2) in enumerate(per_game):
        write_game(td, "game-%d-1.jsonl" % (i + 1),
                   game_line(seed=i + 1, deck_a="P1", deck_b="Opp",
                             plan_a=plan, winner="A"),
                   [drow(seat="P1", turn=2, choice=dc, options=delver),
                    drow(seat="P1", turn=3, choice=w1, options=wl),
                    drow(seat="P1", turn=3, choice=w2, options=wl)])


def test_cluster_order_invariance():
    """se/ci95 of every clustered block must not depend on the dict-insertion
    order of game files: the bootstrap resamples BY INDEX from a seeded RNG,
    so before the explicit key-sort this reordering changed ci95."""
    spec = _order_spec()
    with tempfile.TemporaryDirectory() as td:
        _order_corpus(td)
        rows, per_deck, counters, warns = pa.evaluate_corpus(
            [td], {"ur-delver": spec})
        acc = per_deck["ur-delver"]
        s_fwd = pa.summarize_deck("ur-delver", acc, spec)
        # Reverse the per-milestone result lists: by_file is now built in an
        # insertion order that DIFFERS from sorted order.
        for mid in list(acc["results"]):
            acc["results"][mid] = list(reversed(acc["results"][mid]))
        s_rev = pa.summarize_deck("ur-delver", acc, spec)
    a1 = s_fwd["milestones"]["m_dep"]["attain_rate_clustered"]
    a2 = s_rev["milestones"]["m_dep"]["attain_rate_clustered"]
    r1 = s_fwd["milestones"]["m_rate"]["rate"]
    r2 = s_rev["milestones"]["m_rate"]["rate"]
    check("order_fixture_nontrivial",
          a1["n_clusters"] == 3 and a1["se"] is not None and a1["se"] > 0
          and r1["n_clusters"] == 3, "%r %r" % (a1, r1))
    check("attain_cluster_block_order_invariant", a1 == a2,
          "fwd=%r rev=%r" % (a1, a2))
    check("rate_cluster_block_order_invariant", r1 == r2,
          "fwd=%r rev=%r" % (r1, r2))


def test_determinism_byte_identical():
    """Two full main() runs over the identical corpus with the same
    --generated-utc must produce byte-identical summary.json and
    milestones.jsonl."""
    stamp = "20260101T000000Z"
    with tempfile.TemporaryDirectory() as td:
        corpus = Path(td) / "corpus"
        corpus.mkdir()
        _order_corpus(corpus)
        out1 = Path(td) / "o1"
        out2 = Path(td) / "o2"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc1 = pa.main([str(corpus), "--out", str(out1),
                           "--generated-utc", stamp])
            rc2 = pa.main([str(corpus), "--out", str(out2),
                           "--generated-utc", stamp])
        check("byte_runs_exit_0", rc1 == 0 and rc2 == 0,
              "rc1=%r rc2=%r" % (rc1, rc2))
        s1 = (out1 / "summary.json").read_bytes()
        s2 = (out2 / "summary.json").read_bytes()
        m1 = (out1 / "milestones.jsonl").read_bytes()
        m2 = (out2 / "milestones.jsonl").read_bytes()
    check("summary_json_byte_identical", s1 == s2)
    check("milestones_jsonl_byte_identical", m1 == m2)
    check("generated_utc_pinned", ('"generated_utc": "%s"' % stamp).encode()
          in s1, s1[:200].decode("utf-8", "replace"))


def test_no_runs_reads():
    src = Path(__file__).read_text(encoding="utf-8")
    literal = "runs" + "/"
    check("no_runs_literal_in_tests", literal not in src)


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
