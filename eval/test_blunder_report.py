#!/usr/bin/env python3
"""Self-test for blunder_report.py: synthetic fixtures, a hand-written
StubDriver (duck-typed to the contract S3 driver interface, NOT verify_keys),
no real corpus, no network, no engine.
Run: python3 eval/test_blunder_report.py

WHAT WOULD MAKE THIS FAIL:
  * classify_wpl misplacing any S4.2 threshold (0.05/0.10/0.25), missing the
    WP-0.5-crossing blunder rule, or classifying noise (wpl <= 2*wpl_se must
    gate down to 'good' + noise_limited).
  * A textlane-verified blunder leaking into punt_rate (the two denominators
    must stay separate), or punt_rate carrying n > 0 with zero rollout
    adjudications.
  * --driver none under --adjudicate rollout NOT yielding unadjudicable rows
    with reason 'no_engine_available' and exit code 3.
  * An audit lane full of adjudicated blunders NOT setting flaggers_leaky, or
    flagger_lift claiming selectivity (CI excluding 0) when flagged and audit
    rates are identical.
  * An emitted row missing a blunder-report.v1 required key or using a value
    outside the schema enums for flag_lane / adjudication.method /
    adjudication.class.
  * The local rollout_key copy disagreeing with verify_keys.rollout_key.
"""

import json
import sys
import tempfile
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

def game_line(seed=101, swap=False, deck_a="MySeat", deck_b="OpponentSeat"):
    return {
        "t": "game", "seed": seed, "swap": swap,
        "deck_a": deck_a, "deck_b": deck_b,
        "controller_a": "ai", "controller_b": "ai",
        "smart_a": "local:test-a", "smart_b": "local:test-b",
        "fast_a": "x", "fast_b": "y",
        "winner": "A", "turns": 12, "win_condition": "life",
        "duration_ms": 1000, "engine_sha": "deadbeef",
        "overlay_sha": "cafef00d", "deck_sha": "abc123",
    }


def build_prompt(you_seat="MySeat", you_life=20, you_board="Big Beater(7/7)",
                 opp_seat="OpponentSeat", opp_life=6):
    return (
        "== Turn 3 MAIN1 (active: %s) ==\n" % you_seat
        + "YOU [%s] life %d | poison 0/10\n" % (you_seat, you_life)
        + "  hand(1): Island\n"
        + "  board: %s\n" % you_board
        + "  lands: 4 untapped / 4 total (Island x4 [untapped])\n"
        + "  mana available: {U:4} (4 untapped sources)\n"
        + "  yard(0): (none)\n"
        + "OPP [%s] life %d | poison 0/10\n" % (opp_seat, opp_life)
        + "  board: (no nonland permanents)\n"
        + "  lands: 4 untapped / 4 total (Mountain x4 [untapped])\n"
        + "  mana available: {R:4} (4 untapped sources)\n"
        + "  yard(0): (none)\n"
        + "  hand: 3 cards\n"
        + "stack (top first): (empty)\n"
        + "RECENT EVENTS (oldest->newest):\n"
        + "  - filler event\n"
        + "DECISION: You have priority (MAIN1, your main phase). "
        + "Choose ONE action. CHOICE: <number>"
    )


# LETHAL_PASS_DEC fires rule 1 (lethal_available_passed, textlane-verified
# class) with chosen == PASS sentinel (-1 -> last play option).
def lethal_pass_dec(turn=3):
    return {
        "t": "decision", "seat": "MySeat", "turn": turn, "phase": "MAIN1",
        "type": "play", "n_options": 2, "choice": -1, "tier": "smart",
        "latency_ms": 100, "fallback": None,
        "options": ["CAST X", "PASS (do nothing)"],
        "prompt": build_prompt(), "raw_reply": "CHOICE: 2",
    }


def clean_dec(turn=3):
    """Scoreable play decision that fires no text rule (no lethal, chose CAST)."""
    return {
        "t": "decision", "seat": "MySeat", "turn": turn, "phase": "MAIN1",
        "type": "play", "n_options": 2, "choice": 0, "tier": "smart",
        "latency_ms": 100, "fallback": None,
        "options": ["CAST Ponder {U}", "PASS (do nothing)"],
        "prompt": build_prompt(you_board="(no nonland permanents)",
                               opp_life=20),
        "raw_reply": "CHOICE: 1",
    }


def entries_fixture(n_games=1, decisions_per_game=None, seed0=101):
    entries = []
    for i in range(n_games):
        decs = decisions_per_game() if decisions_per_game else [lethal_pass_dec()]
        entries.append({
            "file": "game-%d-1.jsonl" % (seed0 + i),
            "game": game_line(seed=seed0 + i),
            "run_dir": "synth",
            "decisions": decs,
        })
    return entries


class StubDriver(object):
    """Hand-written duck type of the contract-S3 rollout driver: supports()
    -> (bool, reason); rollout(address, n_per_option) -> RolloutResult-shaped
    dict. Configured with option_wp/option_wp_se to return for every row."""

    def __init__(self, option_wp, option_wp_se, ok=True, reason=None,
                 supports_ok=True, supports_reason=""):
        self.option_wp = option_wp
        self.option_wp_se = option_wp_se
        self.ok = ok
        self.reason = reason
        self.supports_ok = supports_ok
        self.supports_reason = supports_reason
        self.calls = 0

    def supports(self, address):
        return (self.supports_ok, self.supports_reason)

    def rollout(self, address, n_per_option=None):
        self.calls += 1
        n = len(self.option_wp)
        return {"schema": "rollout-result.v1", "ok": self.ok,
                "reason": self.reason,
                "rollout_key": br.rollout_key(address),
                "decision_type": address.get("decision_type"),
                "n_options": n, "options": ["opt%d" % i for i in range(n)],
                "option_wp": list(self.option_wp),
                "option_wp_se": list(self.option_wp_se),
                "option_wins": [0] * n, "option_losses": [0] * n,
                "option_draws": [0] * n, "option_invalid": [0] * n,
                "n_per_option": n_per_option or 16,
                "continuation": "ai-log/ai-log",
                "seed_fn": "mix64(base_seed ^ (i*C1) ^ (j*C2))",
                "rollout_seeds": [[1]] * n,
                "caveats": ["rollout_policy_caveat"],
                "engine_sha": "deadbeef", "overlay_sha": "cafef00d",
                "wall_ms": 1}


def scan_and_adjudicate(entries, mode, driver, **kw):
    rows, stats = bf.scan(entries, **{k: v for k, v in kw.items()
                                      if k in ("audit_rate", "sample_seed")})
    br.adjudicate_rows(rows, mode, driver,
                       n_per_option=kw.get("n_per_option", 16),
                       max_adjudications=kw.get("max_adjudications"))
    return rows, stats


# ------------------------------------------------------------------- tests

def test_classification_thresholds():
    check("wpl 0.04 -> good",
          br.classify_wpl(0.04, 0.001, 0.90, 0.86) == ("good", False))
    check("wpl 0.07 -> inaccuracy",
          br.classify_wpl(0.07, 0.001, 0.90, 0.83) == ("inaccuracy", False))
    check("wpl 0.15 -> mistake (no 0.5 crossing)",
          br.classify_wpl(0.15, 0.001, 0.90, 0.75) == ("mistake", False))
    check("wpl 0.30 -> blunder",
          br.classify_wpl(0.30, 0.001, 0.90, 0.60) == ("blunder", False))
    check("wpl 0.30 with se 0.20 -> good + noise_limited",
          br.classify_wpl(0.30, 0.20, 0.90, 0.60) == ("good", True))
    check("boundary 0.05 -> inaccuracy",
          br.classify_wpl(0.05, 0.001, 0.90, 0.85) == ("inaccuracy", False))
    check("boundary 0.10 -> mistake",
          br.classify_wpl(0.10, 0.001, 0.90, 0.80) == ("mistake", False))
    check("boundary 0.25 -> blunder",
          br.classify_wpl(0.25, 0.001, 0.90, 0.65) == ("blunder", False))
    check("wpl_se None gates down to good + noise_limited",
          br.classify_wpl(0.30, None, 0.90, 0.60) == ("good", True))


def test_wp_crossing_rule():
    driver = StubDriver([0.60, 0.44], [0.01, 0.01])
    rows, _stats = scan_and_adjudicate(entries_fixture(), "rollout", driver)
    check("crossing fixture produced a row", len(rows) == 1)
    if rows:
        adj = rows[0]["adjudication"]
        check("crossing row rollout-adjudicated", adj["method"] == "rollout")
        check("wpl 0.16 crossing 0.5 -> blunder despite < 0.25",
              adj["class"] == "blunder"
              and abs(adj["wpl"] - 0.16) < 1e-9, json.dumps(adj))
        check("single_deal_hidden_info caveat always on rollout rows",
              "single_deal_hidden_info" in adj["caveats"])
        check("wp fields present",
              adj["wp_best"] == 0.60 and adj["wp_chosen"] == 0.44)


def test_textlane_never_enters_punt_rate():
    entries = entries_fixture(n_games=3)
    rows, stats = scan_and_adjudicate(entries, "textlane", None)
    summary = br.build_summary(rows, stats, {})
    check("textlane rows adjudicated as textlane-verified blunders",
          all(r["adjudication"]["method"] == "textlane-verified"
              and r["adjudication"]["class"] == "blunder" for r in rows)
          and len(rows) == 3)
    check("punt_rate n == 0 under textlane-only adjudication",
          summary["punt_rate"]["n"] == 0
          and summary["punt_rate"]["est"] is None)
    check("punt_rate_textlane n > 0",
          summary["punt_rate_textlane"]["n"] > 0
          and summary["punt_rate_textlane"]["est"] > 0)
    check("textlane caveat present",
          all("textlane_not_rollout_verified"
              in r["adjudication"]["caveats"] for r in rows))
    check("dq_wpl_mean empty under textlane-only",
          summary["dq_wpl_mean"]["n"] == 0)


def test_unadjudicable_when_no_driver():
    entries = entries_fixture(n_games=2)
    rows, _stats = scan_and_adjudicate(entries, "rollout", None)
    check("no driver -> every rollout row unadjudicable/no_engine_available",
          all(r["adjudication"]["method"] == "unadjudicable"
              and r["adjudication"]["class"] == "unadjudicable"
              and r.get("unadjudicable_reason") == "no_engine_available"
              for r in rows) and len(rows) == 2)
    with tempfile.TemporaryDirectory() as td:
        corpus = Path(td) / "corpus"
        corpus.mkdir()
        for e in entries_fixture(n_games=2):
            with open(corpus / e["file"], "w", encoding="utf-8") as f:
                for d in e["decisions"]:
                    f.write(json.dumps(d) + "\n")
                f.write(json.dumps(e["game"]) + "\n")
        rc = br.main([str(corpus), "--out", str(Path(td) / "out-r"),
                      "--adjudicate", "rollout", "--driver", "none",
                      "--json"])
        check("exit 3 when --adjudicate rollout has no driver", rc == 3)
        rc2 = br.main([str(corpus), "--out", str(Path(td) / "out-t"),
                       "--adjudicate", "textlane", "--driver", "none",
                       "--json"])
        check("exit 0 for textlane without a driver", rc2 == 0)


def test_driver_refusal_falls_back_to_textlane():
    driver = StubDriver([0.6, 0.4], [0.01, 0.01], supports_ok=False,
                        supports_reason="non_deterministic_controllers")
    rows, _stats = scan_and_adjudicate(entries_fixture(), "rollout", driver)
    check("unsupported row falls back to textlane when qualified",
          len(rows) == 1
          and rows[0]["adjudication"]["method"] == "textlane-verified"
          and rows[0].get("textlane_fallback_from")
          == "non_deterministic_controllers")


def test_audit_lane_leakage_flag():
    # audit_rate=1.0: every scoreable row is in the audit lane; every row is
    # also text-flagged (rule 1) and stub-adjudicated as a blunder, so the
    # flagged-lane and audit-lane punt rates are identical (lift == 0).
    driver = StubDriver([0.90, 0.30], [0.01, 0.01])
    entries = entries_fixture(
        n_games=4, decisions_per_game=lambda: [lethal_pass_dec(),
                                               lethal_pass_dec(turn=4)])
    rows, stats = scan_and_adjudicate(entries, "rollout", driver,
                                      audit_rate=1.0)
    summary = br.build_summary(rows, stats, {})
    check("all rows rollout blunders",
          all(r["adjudication"]["class"] == "blunder" for r in rows)
          and len(rows) == 8)
    check("audit lane punt rate measured at 1.0",
          summary["audit_lane_punt_rate"]["est"] == 1.0
          and summary["audit_lane_punt_rate"]["ci95"][0] > 0)
    check("flaggers_leaky True when the audit lane finds blunders",
          summary["flaggers_leaky"] is True)
    lift = summary["flagger_lift"]
    check("flagger_lift CI includes 0 when flagged == audit rates",
          lift["est"] == 0.0 and lift["ci95"][0] <= 0.0 <= lift["ci95"][1])
    check("baselines carry random_flag expected precision",
          summary["baselines"]["random_flag"]
          ["expected_precision_under_random_flagging"] == 1.0)


def test_flaggers_leaky_unmeasured_not_false():
    entries = entries_fixture(n_games=2)
    rows, stats = scan_and_adjudicate(entries, "textlane", None)
    summary = br.build_summary(rows, stats, {})
    check("unadjudicated audit lane -> flaggers_leaky is None (unmeasured)",
          summary["flaggers_leaky"] is None
          and "UNMEASURED" in summary["flaggers_leaky_note"])


def test_noise_gate_via_driver():
    driver = StubDriver([0.90, 0.60], [0.15, 0.15])   # wpl .30, wpl_se ~.212
    rows, _stats = scan_and_adjudicate(entries_fixture(), "rollout", driver)
    check("noisy rollout gated to good + noise_limited",
          len(rows) == 1
          and rows[0]["adjudication"]["class"] == "good"
          and rows[0]["noise_limited"] is True)


def test_schema_conformance():
    schema_path = Path(__file__).resolve().parent / "schemas" / \
        "blunder-report.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required = schema["required"]
    lane_enum = schema["properties"]["flag_lane"]["enum"]
    adj_props = schema["properties"]["adjudication"]
    method_enum = adj_props["properties"]["method"]["enum"]
    class_enum = adj_props["properties"]["class"]["enum"]
    caveat_enum = set(adj_props["properties"]["caveats"]["items"]["enum"])
    # Additive-only deviation reported to the integrator: the track spec
    # mandates the textlane caveat token, which is not yet in the schema enum.
    caveat_enum.add("textlane_not_rollout_verified")

    with tempfile.TemporaryDirectory() as td:
        corpus = Path(td) / "corpus"
        corpus.mkdir()
        for e in entries_fixture(n_games=3, decisions_per_game=lambda: [
                lethal_pass_dec(), clean_dec(turn=4)]):
            with open(corpus / e["file"], "w", encoding="utf-8") as f:
                for d in e["decisions"]:
                    f.write(json.dumps(d) + "\n")
                f.write(json.dumps(e["game"]) + "\n")
        out = Path(td) / "out"
        rc = br.main([str(corpus), "--out", str(out), "--audit-rate", "1.0",
                      "--json"])
        check("full run exits 0", rc == 0)
        lines = [json.loads(x) for x in
                 (out / "decisions.jsonl").read_text().splitlines() if x]
        rows = [x for x in lines if x.get("t") != "summary"]
        summaries = [x for x in lines if x.get("t") == "summary"]
        check("rows emitted and one summary as the final line",
              len(rows) >= 1 and len(summaries) == 1
              and lines[-1].get("t") == "summary")
        all_ok = bool(rows)
        for r in rows:
            for k in required:
                if k not in r:
                    all_ok = False
                    check("row missing required key %s" % k, False)
            adj = r.get("adjudication") or {}
            if r.get("flag_lane") not in lane_enum:
                all_ok = False
            if adj.get("method") not in method_enum:
                all_ok = False
            if adj.get("class") not in class_enum:
                all_ok = False
            for cv in adj.get("caveats") or []:
                if cv not in caveat_enum:
                    all_ok = False
            if adj.get("method") == "textlane-verified":
                if "wp_chosen" in adj or "wp_best" in adj or "wpl" in adj:
                    all_ok = False
                    check("textlane row leaks wp fields", False)
        check("every row conforms to blunder-report.v1 required keys + enums",
              all_ok)
        summary = summaries[0]
        sj = json.loads((out / "summary.json").read_text())
        check("summary.json matches the jsonl summary line", sj == summary)
        for key in ("dq_wpl_mean", "punt_rate", "punt_rate_by_lane",
                    "punt_rate_textlane", "per_game_worst_p95", "flag_rate",
                    "audit_lane_punt_rate", "flagger_lift", "baselines"):
            if key not in summary:
                check("summary missing %s" % key, False)
        check("summary aggregate block keys present", True)
        for b in ("always_flag", "never_flag", "random_flag",
                  "always_good_baseline"):
            if b not in summary["baselines"]:
                check("baseline %s missing" % b, False)
        check("baselines present", True)
        check("shadow lane reported vacuous, not scored",
              summary["n_shadow_rows_available"] == 0
              and summary["shadow_lane_note"]
              == "shadow lane vacuous (no SHADOW_AI=1 corpus)"
              and "shadow_disagree_rate" not in summary)


def test_rollout_key_pinned_reference():
    address = {"engine_sha": "c1c64a5", "overlay_sha": "6c3b0bd",
               "deck_a": "UR Delver", "deck_b": "Burn",
               "controllers": "ai-log/ai-log", "seed": 20017, "swap": False,
               "decision_index": 41, "game_file": "game-20017-1.jsonl"}
    k = br._rollout_key_local(address)
    check("rollout_key 16-hex", len(k) == 16
          and all(c in "0123456789abcdef" for c in k))
    try:
        import verify_keys
        check("local rollout_key == verify_keys.rollout_key",
              verify_keys.rollout_key(address) == k)
    except ImportError:
        print("[skip] verify_keys.py not present yet — cross-impl equality "
              "unchecked")


def test_conformance_with_verify_keys():
    if not br.HAVE_ROLLOUT_DRIVER:
        print("[skip] verify_keys.py not present yet")
        return
    from verify_keys import FixtureRolloutDriver
    for meth in ("supports", "rollout", "enumerate"):
        check("FixtureRolloutDriver has %s" % meth,
              callable(getattr(FixtureRolloutDriver, meth, None)))
    with tempfile.TemporaryDirectory() as td:
        fx = Path(td) / "fixture.jsonl"
        fx.write_text("")
        drv = FixtureRolloutDriver(str(fx))
        addr = {"engine_sha": "deadbeef", "overlay_sha": "cafef00d",
                "deck_a": "MySeat", "deck_b": "OpponentSeat",
                "controllers": "ai/ai", "seed": 101, "swap": False,
                "decision_index": 0, "decision_type": "play",
                "seat": "MySeat", "turn": 3,
                "game_file": "game-101-1.jsonl"}
        res = drv.rollout(addr)
        check("FixtureRolloutDriver.rollout returns a dict with ok/reason",
              isinstance(res, dict) and "ok" in res and "reason" in res)


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
