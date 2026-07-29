#!/usr/bin/env python3
"""Self-test for blunder_flags.py: synthetic fixtures, no real corpus, no network.
Run: python3 eval/test_blunder_flags.py

WHAT WOULD MAKE THIS FAIL:
  * A text rule firing on its near-miss negative fixture (precision zero) or
    not firing on its positive fixture (recall zero) — one positive + one
    negative per rule.
  * scoreable() returning the wrong reason token for degenerate menus,
    fallback rows, auto tier, or unlisted types.
  * audit_sample_ids losing determinism across calls or across a fresh
    subprocess, or drifting more than 2pp from the requested 5% rate.
  * lane_for ranking lanes out of priority order, or lanes_for dropping a
    membership.
  * scan() emitting a measured 0.0 shadow-disagreement rate on a corpus with
    zero shadow_choice rows instead of reporting the lane as vacuous.
  * cluster_bootstrap_ci drifting from the pinned cross-track reference
    values, or the local rollout_key copy disagreeing with verify_keys'.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blunder_flags as bf  # noqa: E402
import blunder_report as br  # noqa: E402

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
        "smart_a": "local:test-a", "smart_b": "local:test-b",
        "fast_a": "x", "fast_b": "y",
        "winner": "A", "turns": 12, "win_condition": "life",
        "duration_ms": 1000, "engine_sha": "deadbeef",
        "overlay_sha": "cafef00d", "deck_sha": "abc123",
    }


def build_prompt(you_seat="MySeat", you_life=20, you_hand="Island, Island",
                 you_board="(no nonland permanents)", you_sources=4,
                 opp_seat="OpponentSeat", opp_life=20,
                 opp_board="(no nonland permanents)", stack="(empty)",
                 active=None, events=None,
                 tail="DECISION: test. CHOICE: <number>"):
    n_hand = len([x for x in you_hand.split(", ") if x]) if you_hand else 0
    ev_lines = "".join("  - %s\n" % e for e in (events or ["filler event"]))
    return (
        "== Turn 3 MAIN1 (active: %s) ==\n" % (active or you_seat)
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
        + ev_lines
        + tail
    )


def dec(seat="MySeat", turn=3, dtype="play", n_options=2, choice=0,
        prompt="P", options=None, fallback=None, tier="smart", **extra):
    d = {
        "t": "decision", "seat": seat, "turn": turn, "phase": "MAIN1",
        "type": dtype, "n_options": n_options, "choice": choice,
        "tier": tier, "latency_ms": 100, "fallback": fallback,
        "options": options if options is not None else ["a", "b"][:max(0, n_options)],
        "prompt": prompt, "raw_reply": "CHOICE: 1",
    }
    d.update(extra)
    return d


def ctx_for(game):
    return {"lands_played_this_turn": 0, "turn": 3,
            "player_names": {game["deck_a"], game["deck_b"]}}


GAME = game_line()


def fired(d, rule):
    return rule in [f["flag"] for f in bf.flag_decision(d, GAME, ctx_for(GAME))]


# ------------------------------------------------------------------- rules

def test_rule_lethal_available_passed():
    p = build_prompt(you_board="Big Beater(7/7)", opp_life=6)
    pos = dec(dtype="play", choice=-1, prompt=p,
              options=["CAST X", "PASS (do nothing)"])
    check("rule1 fires on lethal+PASS", fired(pos, "lethal_available_passed"))
    neg_cast = dec(dtype="play", choice=0, prompt=p,
                   options=["CAST X", "PASS (do nothing)"])
    check("rule1 not on lethal+CAST",
          not fired(neg_cast, "lethal_available_passed"))
    p_nl = build_prompt(you_board="Tiny(2/2)", opp_life=20)
    neg_nl = dec(dtype="play", choice=-1, prompt=p_nl,
                 options=["CAST X", "PASS (do nothing)"])
    check("rule1 not without lethal",
          not fired(neg_nl, "lethal_available_passed"))


def test_rule_attack_declined_with_lethal():
    p = build_prompt(opp_life=6, tail="Declare attackers. PICKS: ...")
    pos = dec(dtype="attackers", choice=0, prompt=p,
              options=["Big Beater (7/7)"])
    check("rule2 fires on 0 attackers with lethal",
          fired(pos, "attack_declined_with_lethal"))
    neg = dec(dtype="attackers", choice=1, prompt=p,
              options=["Big Beater (7/7)"])
    check("rule2 not when attackers declared",
          not fired(neg, "attack_declined_with_lethal"))
    p2 = build_prompt(opp_life=8, tail="Declare attackers. PICKS: ...")
    neg2 = dec(dtype="attackers", choice=0, prompt=p2,
               options=["Big Beater (7/7)"])
    check("rule2 not without lethal",
          not fired(neg2, "attack_declined_with_lethal"))


BLOCK_TAIL = ("COMBAT — declare blockers. Incoming: Scary Thing (5/5). "
              "Your blockers: Wall (0/6), Dude (1/1). "
              "End with EXACTLY: BLOCKS: ...")


def test_rule_forced_block_declined():
    p = build_prompt(you_life=2, tail=BLOCK_TAIL)
    pos = dec(dtype="blockers", choice=0, prompt=p,
              options=["Wall (0/6)", "Dude (1/1)"])
    check("rule3 fires on declined forced block",
          fired(pos, "forced_block_declined"))
    neg = dec(dtype="blockers", choice=1, prompt=p,
              options=["Wall (0/6)", "Dude (1/1)"])
    check("rule3 not when blocks assigned",
          not fired(neg, "forced_block_declined"))
    p2 = build_prompt(you_life=9, tail=BLOCK_TAIL)
    neg2 = dec(dtype="blockers", choice=0, prompt=p2,
               options=["Wall (0/6)", "Dude (1/1)"])
    check("rule3 not when incoming < life",
          not fired(neg2, "forced_block_declined"))


def test_rule_save_vs_lethal_passed():
    p = build_prompt(you_life=4, opp_board="Angry Guy(5/5)")
    pos = dec(dtype="play", choice=-1, prompt=p,
              options=["CAST Fog Effect", "PASS (do nothing)"])
    check("rule4 fires on pass-while-dying with castable",
          fired(pos, "save_vs_lethal_passed"))
    neg_cast = dec(dtype="play", choice=0, prompt=p,
                   options=["CAST Fog Effect", "PASS (do nothing)"])
    check("rule4 not when the spell was cast",
          not fired(neg_cast, "save_vs_lethal_passed"))
    pos_nocast = dec(dtype="play", choice=-1, prompt=p,
                     options=["PLAY LAND Island", "PASS (do nothing)"])
    check("rule4 not without a CAST option",
          not fired(pos_nocast, "save_vs_lethal_passed"))


def test_rule_durdle_pass_with_castable():
    p = build_prompt()
    pos = dec(dtype="play", choice=-1, prompt=p,
              options=["CAST Ponder {U}", "PASS (do nothing)"])
    check("rule5 fires on non-CAST choice with castable",
          fired(pos, "durdle_pass_with_castable"))
    neg = dec(dtype="play", choice=0, prompt=p,
              options=["CAST Ponder {U}", "PASS (do nothing)"])
    check("rule5 not when a spell was cast",
          not fired(neg, "durdle_pass_with_castable"))
    neg2 = dec(dtype="play", choice=-1, prompt=p, phase="COMBAT_DECLARE_BLOCKERS",
               options=["CAST Ponder {U}", "PASS (do nothing)"])
    check("rule5 not outside main phases",
          not fired(neg2, "durdle_pass_with_castable"))
    ev = [f["evidence"] for f in bf.flag_decision(pos, GAME, ctx_for(GAME))
          if f["flag"] == "durdle_pass_with_castable"]
    check("rule5 carries the process_metrics caveat verbatim",
          bool(ev) and bf._DURDLE_CAVEAT in ev[0])


def test_rule_land_drop_skipped():
    events = ["Turn 3 (MySeat)"]
    p = build_prompt(events=events)
    pos = dec(dtype="play", choice=-1, prompt=p,
              options=["PLAY LAND Island", "PASS (do nothing)"])
    check("rule6 fires on skipped land drop", fired(pos, "land_drop_skipped"))
    p_played = build_prompt(events=["Turn 3 (MySeat)",
                                    "MySeat played Island (42)"])
    neg = dec(dtype="play", choice=-1, prompt=p_played,
              options=["PLAY LAND Island", "PASS (do nothing)"])
    check("rule6 not when a land was already played this turn",
          not fired(neg, "land_drop_skipped"))
    p_opp = build_prompt(events=events, active="OpponentSeat")
    neg2 = dec(dtype="play", choice=-1, prompt=p_opp,
               options=["PLAY LAND Island", "PASS (do nothing)"])
    check("rule6 not on the opponent's turn",
          not fired(neg2, "land_drop_skipped"))


def test_rule_discard_only_land():
    hand = "Lightning Bolt {R} (castable), Island"
    p = build_prompt(you_hand=hand, tail="DECISION: discard. PICKS: ...")
    pos = dec(dtype="discard", choice=1, prompt=p,
              options=["Lightning Bolt", "Island"])
    check("rule7 fires on discarding the only land",
          fired(pos, "discard_only_land"))
    neg = dec(dtype="discard", choice=0, prompt=p,
              options=["Lightning Bolt", "Island"])
    check("rule7 not when a spell was discarded",
          not fired(neg, "discard_only_land"))
    hand2 = "Island, Island"
    p2 = build_prompt(you_hand=hand2, tail="DECISION: discard. PICKS: ...")
    neg2 = dec(dtype="discard", choice=0, prompt=p2,
               options=["Island", "Island"])
    check("rule7 not with two lands in hand",
          not fired(neg2, "discard_only_land"))


def test_rule_discard_only_threat():
    hand = "Delver of Secrets {U} 1/1 (castable), Island, Island"
    p = build_prompt(you_hand=hand, tail="DECISION: discard. PICKS: ...")
    pos = dec(dtype="discard", choice=0, prompt=p,
              options=["Delver of Secrets", "Island", "Island"],
              n_options=3)
    check("rule8 fires on discarding the only threat",
          fired(pos, "discard_only_threat"))
    neg = dec(dtype="discard", choice=1, prompt=p,
              options=["Delver of Secrets", "Island", "Island"],
              n_options=3)
    check("rule8 not when a land was discarded",
          not fired(neg, "discard_only_threat"))


def test_rule_burn_to_creature_when_face_lethal():
    p = build_prompt(opp_life=3, stack="1 pending: #1 Lightning Bolt (MySeat)",
                     tail="DECISION: Choose a target. CHOICE: <number>")
    opts = ["OpponentSeat", "Goblin Guide (2/2) [OpponentSeat's Battlefield]"]
    pos = dec(dtype="target", choice=1, prompt=p, options=opts)
    check("rule9 fires on burning a creature with face lethal",
          fired(pos, "burn_to_creature_when_face_lethal"))
    neg_face = dec(dtype="target", choice=0, prompt=p, options=opts)
    check("rule9 not when the player was targeted",
          not fired(neg_face, "burn_to_creature_when_face_lethal"))
    p_high = build_prompt(opp_life=10,
                          stack="1 pending: #1 Lightning Bolt (MySeat)",
                          tail="DECISION: Choose a target. CHOICE: <number>")
    neg_life = dec(dtype="target", choice=1, prompt=p_high, options=opts)
    check("rule9 not when face is not lethal",
          not fired(neg_life, "burn_to_creature_when_face_lethal"))


def test_rule_counter_held_while_dying():
    p = build_prompt(you_life=4, opp_board="Angry Guy(5/5)")
    pos = dec(dtype="play", choice=-1, prompt=p,
              options=["CAST Force of Will {3}{U}{U}", "PASS (do nothing)"])
    check("rule10 fires on holding an answer while dying",
          fired(pos, "counter_held_while_dying"))
    neg = dec(dtype="play", choice=0, prompt=p,
              options=["CAST Force of Will {3}{U}{U}", "PASS (do nothing)"])
    check("rule10 not when the answer was cast",
          not fired(neg, "counter_held_while_dying"))
    pos_no_answer = dec(dtype="play", choice=-1, prompt=p,
                        options=["CAST Ponder {U}", "PASS (do nothing)"])
    check("rule10 not without an ANSWER_CARDS option",
          not fired(pos_no_answer, "counter_held_while_dying"))


def test_rule_chump_free_lethal_block_skipped():
    p = build_prompt(you_life=2, tail=BLOCK_TAIL)
    pos = dec(dtype="blockers", choice=0, prompt=p,
              options=["Wall (0/6)", "Dude (1/1)"])
    check("rule11 fires when a surviving blocker declined",
          fired(pos, "chump_free_lethal_block_skipped"))
    neg = dec(dtype="blockers", choice=0, prompt=p, options=["Dude (1/1)"])
    check("rule11 not when no blocker survives (rule3 still covers it)",
          not fired(neg, "chump_free_lethal_block_skipped")
          and fired(neg, "forced_block_declined"))


# --------------------------------------------------------------- machinery

def test_scoreable():
    ok, r = bf.scoreable(dec(n_options=1, options=["a"]))
    check("scoreable: degenerate menu", (ok, r) == (False, "degenerate_n_options"))
    ok, r = bf.scoreable(dec(fallback="engine-default"))
    check("scoreable: fallback row", (ok, r) == (False, "fallback_row"))
    ok, r = bf.scoreable(dec(dtype="surveil"))
    check("scoreable: unlisted type", (ok, r) == (False, "type_not_scoreable"))
    ok, r = bf.scoreable(dec(tier="auto"))
    check("scoreable: auto tier", (ok, r) == (False, "auto_tier"))
    ok, r = bf.scoreable(dec())
    check("scoreable: clean row", (ok, r) == (True, ""))


def test_audit_sample_deterministic():
    ids = ["row-%04d" % i for i in range(2000)]
    s1 = bf.audit_sample_ids(ids, rate=0.05, sample_seed=20260728)
    s2 = bf.audit_sample_ids(list(reversed(ids)), rate=0.05,
                             sample_seed=20260728)
    check("audit sample identical across calls/order", s1 == s2)
    rate = len(s1) / 2000.0
    check("audit sample rate within 2pp of 5%%", abs(rate - 0.05) <= 0.02,
          "got %.4f" % rate)
    code = ("import sys, json; sys.path.insert(0, %r); import blunder_flags; "
            "ids = ['row-%%04d' %% i for i in range(2000)]; "
            "print(json.dumps(blunder_flags.audit_sample_ids(ids)))"
            % str(Path(__file__).resolve().parent))
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True)
    check("audit sample identical across a subprocess",
          out.returncode == 0 and json.loads(out.stdout) == s1,
          out.stderr[:200])
    s3 = bf.audit_sample_ids(ids, rate=0.05, sample_seed=1)
    check("audit sample changes with seed", s3 != s1)


def test_lane_priority():
    lanes = bf.lanes_for(["lethal_available_passed"], 2, 0, True)
    check("lanes_for reports all three memberships",
          lanes == ["shadow-disagree", "text-flag", "audit-sample"])
    check("lane_for picks shadow-disagree first",
          bf.lane_for(["lethal_available_passed"], 2, 0, True)
          == "shadow-disagree")
    check("lane_for text-flag when shadow agrees",
          bf.lane_for(["x"], 1, 1, True) == "text-flag")
    check("lane_for audit only", bf.lane_for([], None, 0, True) == "audit-sample")
    check("lane_for None when no lane", bf.lane_for([], None, 0, False) is None)


def test_shadow_lane_vacuous_is_reported_not_scored():
    p = build_prompt()
    entries = [{"file": "game-101-1.jsonl", "game": GAME, "run_dir": "synth",
                "decisions": [dec(prompt=p) for _ in range(5)]}]
    _rows, stats = bf.scan(entries)
    check("vacuous shadow lane: n_shadow_rows_available == 0",
          stats["n_shadow_rows_available"] == 0)
    check("vacuous shadow lane note present",
          stats["shadow_lane_note"] == "shadow lane vacuous (no SHADOW_AI=1 corpus)")
    entries2 = [{"file": "game-101-1.jsonl", "game": GAME, "run_dir": "synth",
                 "decisions": [dec(prompt=p, shadow_choice=1, choice=0)]}]
    rows2, stats2 = bf.scan(entries2)
    check("shadow rows counted when present",
          stats2["n_shadow_rows_available"] == 1
          and stats2["n_shadow_disagree"] == 1
          and stats2["shadow_lane_note"] is None)
    check("shadow disagreement creates a shadow-disagree row",
          any(r["flag_lane"] == "shadow-disagree" for r in rows2))


def test_scan_row_contents():
    p = build_prompt(you_board="Big Beater(7/7)", opp_life=6)
    d = dec(dtype="play", choice=-1, prompt=p,
            options=["CAST X", "PASS (do nothing)"])
    entries = [{"file": "game-101-1.jsonl", "game": GAME, "run_dir": "synth",
                "decisions": [d]}]
    rows, stats = bf.scan(entries)
    check("scan emits the flagged row", len(rows) == 1)
    if rows:
        r = rows[0]
        addr = r["address"]
        check("row address carries the replay keys",
              addr["engine_sha"] == "deadbeef" and addr["seed"] == 101
              and addr["controllers"] == "ai/ai"
              and addr["game_file"] == "game-101-1.jsonl"
              and addr["decision_index"] == 0)
        check("row model resolved from the seat's smart spec",
              r["model"] == "local:test-a")
        check("row precision_class high for rule1",
              r.get("precision_class") == "high")
        check("row deck_slug None for synthetic decks (no plan match)",
              r["deck_slug"] is None)
    check("scan stats count the fire",
          stats["flag_counts"].get("lethal_available_passed") == 1
          and stats["n_scoreable"] == 1)


# ------------------------------------------------- pinned shared references

def test_cluster_bootstrap_ci_pinned():
    fixture = [[1.0, 1.0, 1.0, 0.0], [0.0, 0.0], [1.0, 0.0, 1.0], [1.0],
               [0.0, 0.0, 0.0, 1.0, 1.0]]
    r = bf.cluster_bootstrap_ci(fixture)
    check("cbci n/n_clusters", r["n"] == 15 and r["n_clusters"] == 5)
    check("cbci est pinned", abs(r["est"] - 0.5333333333333333) < 1e-12)
    check("cbci se pinned", abs(r["se"] - 0.11691903702304797) < 1e-9,
          "got %r" % r["se"])
    check("cbci ci lo pinned", abs(r["ci95"][0] - 0.3) < 1e-12)
    check("cbci ci hi pinned", abs(r["ci95"][1] - 0.7692307692307693) < 1e-12)
    check("cbci single cluster has no se",
          bf.cluster_bootstrap_ci([[1.0, 0.0]])["se"] is None)
    check("cbci empty", bf.cluster_bootstrap_ci([])["est"] is None)


def test_rollout_key_pinned():
    address = {"engine_sha": "c1c64a5", "overlay_sha": "6c3b0bd",
               "deck_a": "UR Delver", "deck_b": "Burn",
               "controllers": "ai-log/ai-log", "seed": 20017, "swap": False,
               "decision_index": 41, "game_file": "game-20017-1.jsonl"}
    k = br._rollout_key_local(address)
    check("rollout_key 16-hex", len(k) == 16
          and all(c in "0123456789abcdef" for c in k))
    try:
        import verify_keys
        check("rollout_key agrees with verify_keys",
              verify_keys.rollout_key(address) == k)
    except ImportError:
        print("[skip] verify_keys.py not present yet — cross-impl "
              "rollout_key equality unchecked")


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
