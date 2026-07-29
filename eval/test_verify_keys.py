#!/usr/bin/env python3
"""Self-test for verify_keys.py: synthetic candidates, zero engine, zero JVM,
zero network. The fork-mode subprocess boundary is mocked at RolloutDriver._run
(a FakeEngine writes the same jsonl the Java fork modes would); nothing here
ever spawns java or opens a socket — the no-egress test disables socket.socket
outright for a full CLI run and it must still pass.

Run: python3 eval/test_verify_keys.py

WHAT WOULD MAKE THIS FAIL (per test, the code change that breaks it):
  * test_rollout_key_pinned: any edit to rollout_key's 9-field payload, its
    field ORDER, the join character, or the [:16] truncation changes the
    pinned literal b386291ec4e5ae3d — this is the cross-track contract with
    t5's byte-identical private copy in blunder_report.
  * test_seed_fn_mirror: LlmSpikeFork.java changing mix64's finalizer
    constants or ROLLOUT_C1/C2 breaks the pinned SplitMix64 known-answer
    vector (mix64(C1) == 0xE220A8397B1DCDAF) and/or the constants-grep
    against the Java source; a seed collision across distinct
    (option_i, rollout_j) pairs breaks the 512-distinct assertion — a
    collision there silently correlates rollouts and inflates confidence.
  * test_exhaustive_lane: adjudicate_exhaustive minting a key when the burn
    spell is smaller than OPP life, when EVERY option is lethal (degenerate),
    when the stack is non-empty, or when the option text carries a creature
    target — each has its own assertion; the promoted row losing its
    unprobed_replay_address caveat under --driver none also fails here.
  * test_not_silently_promoted: any failing row coming back with
    status=verified, with an answer_key, or WITHOUT an unverifiable_reason.
  * test_route_lane: widening v1 scope (e.g. attackers rows entering the
    rollout lane, free-win losing its non_empty_stack_v1_restriction).
  * test_score_from_wp: weakening the gap gate (threshold or the 2*max(se)
    term), losing the mulligan-specific 0.15 bar, switching HALF-UP rounding
    back to banker's, or dropping the CI-overlap tie-to-10 rule.
  * test_rollout_lane_fake_engine: the aggregation miscounting wins/losses/
    draws/invalid, wp leaving [0,1], se not matching sqrt(p(1-p)/n), se NOT
    shrinking when n quadruples, rollout_seeds colliding across options, or
    forced_ok=False rows entering the wp denominator.
  * test_rollout_lane_fixture: a fixture-verified row losing option_wp /
    option_wp_se / option_scores consistency; a fixture_miss row being
    promoted instead of no_engine_available + driver_reason; a probe whose
    options differ from the candidate's NOT flagged replay_drift.
  * test_replay_drift_rc4: the engine's rc==4 replay-drift signal being
    laundered into a generic engine_error (or worse, ok=True).
  * test_additive_echo: verify_one dropping or mutating ANY miner field
    (schemas are additive-only), including unknown future fields.
  * test_metric_block: yield_rate losing est/se/ci95/n or se failing to
    shrink as n grows — a score without its floor/uncertainty is not a score.
  * test_cli_end_to_end: exit code drift (0 OK / 2 nothing usable), summary
    losing n_in/n_verified/yield_rate/coverage_note, --lane exhaustive
    letting a rollout row through, or unparseable lines killing the run
    instead of landing in warnings.
  * test_no_egress: ANY code path of a full --driver fixture CLI run opening
    a socket (socket.socket is replaced with a raiser and the run must still
    succeed), the module source importing a network module, _base_env adding
    an egress opt-in to the subprocess environment, CHILD_ENV_DENYLIST losing
    the egress flag or model keys, or _base_env failing to STRIP an inherited
    denylisted var (proved with a fake API key — never the egress flag).
"""

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_keys as vk  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ------------------------------------------------- python mirror of the Java
# SplitMix64-style rollout seed function (LlmSpikeFork.mix64 / rolloutSeed).
# Java signed-long arithmetic == unsigned arithmetic mod 2^64 for ^, *, and
# >>> on the masked non-negative values below.

_M64 = (1 << 64) - 1
ROLLOUT_C1 = 0x9E3779B97F4A7C15
ROLLOUT_C2 = 0xC2B2AE3D27D4EB4F


def mix64(z):
    z &= _M64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _M64
    return (z ^ (z >> 31)) & _M64


def py_rollout_seed(base, option, j):
    return mix64((base ^ ((option * ROLLOUT_C1) & _M64)
                  ^ ((j * ROLLOUT_C2) & _M64)) & _M64)


# ------------------------------------------------------------------ fixtures

def lethal_prompt(opp_life=3, stack="(empty)"):
    return ("Turn 5, main1. YOU [MySeat] life 18\n"
            "  board: Mountain [T]\n"
            "OPP [OpponentSeat] life %d\n"
            "  board: (none)\n"
            "stack (top first): %s\n" % (opp_life, stack))


def lethal_cand(opp_life=3, options=None, prompt=None, dtype="play",
                category="lethal-now", extra=None):
    if options is None:
        options = ["CAST Lightning Bolt", "PASS (do nothing)"]
    row = {
        "schema": "puzzle-position.v1",
        "id": "cafe0001cafe0001",
        "category": category,
        "source": {
            "engine_sha": "c1c64a5", "overlay_sha": "6c3b0bd",
            "deck_a": "MySeat", "deck_b": "OpponentSeat",
            "controllers": "ai-log/ai-log", "seed": 20017, "swap": False,
            "decision_index": 41, "game_file": "game-20017-1.jsonl",
            "turn": 5, "decision_type": dtype, "seat": "MySeat",
        },
        "prompt": lethal_prompt(opp_life) if prompt is None else prompt,
        "options": options,
        "n_options": len(options),
        "status": "candidate",
        "replayable": True,
        "split": "open",
        "category_evidence": "synthetic",
    }
    if extra:
        row.update(extra)
    return row


def rollout_cand(category="graded-midgame", dtype="play", options=None,
                 controllers="ai-log/ai-log", seat="MySeat", prompt=None):
    if options is None:
        options = ["CAST Big Play", "PASS (do nothing)"]
    if prompt is None:
        prompt = lethal_prompt(opp_life=12)  # contains the empty-stack mark
    c = lethal_cand(options=options, prompt=prompt, dtype=dtype,
                    category=category)
    c["source"]["controllers"] = controllers
    c["source"]["seat"] = seat
    # distinct replay address from lethal_cand: a different decision of the
    # same game must never share a rollout_key with the exhaustive row
    c["source"]["decision_index"] = 55
    return c


# name_idx entries carry (slug, decklist_path); RolloutDriver.supports
# requires deck paths to resolve, so engine-driver tests inject these.
NAME_IDX_WITH_PATHS = {"MySeat": ("myseat", "/dev/null"),
                       "OpponentSeat": ("oppseat", "/dev/null")}


def fixture_record(address, option_wp, option_wp_se, options,
                   dtype="play", n_per_option=16):
    n = len(option_wp)
    return {
        "schema": "rollout-result.v1", "ok": True, "reason": None,
        "rollout_key": vk.rollout_key(address), "decision_type": dtype,
        "n_options": n, "options": options,
        "option_wp": option_wp, "option_wp_se": option_wp_se,
        "option_wins": [int(round(w * n_per_option)) for w in option_wp],
        "option_losses": [n_per_option - int(round(w * n_per_option))
                          for w in option_wp],
        "option_draws": [0] * n, "option_invalid": [0] * n,
        "n_per_option": n_per_option, "continuation": "ai-log/ai-log",
        "seed_fn": vk.SEED_FN,
        "rollout_seeds": [[py_rollout_seed(20017, i, j)
                           for j in range(n_per_option)] for i in range(n)],
        "caveats": [], "engine_sha": "c1c64a5", "overlay_sha": "6c3b0bd",
        "wall_ms": 1,
    }


def run_verify_one(cand, driver=None, lane="both", name_idx=None):
    return vk.verify_one(cand, driver, lane, 16,
                         name_idx if name_idx is not None else {},
                         "shashasha", "20260728T000000Z")


class FakeEngine(vk.RolloutDriver):
    """Mocks the fork-mode subprocess boundary: _run writes the exact jsonl
    the Java enumerate/rollout fork modes would. Never spawns anything."""

    def __init__(self, probe, outcome_fn, rc_map=None, **kw):
        vk.RolloutDriver.__init__(self, forge_home=None, **kw)
        self._probe = probe
        self._outcome = outcome_fn
        self._rc = rc_map or {}

    def _launcher(self):
        return Path("/dev/null")  # non-None: "engine present"

    def _run(self, env, extra):
        mode = extra["FORK_MODE"]
        if mode in self._rc:
            return self._rc[mode], ""
        if mode == "enumerate":
            row = dict(self._probe)
            row["t"] = "fork_enumerate"
            out = Path(env["EVAL_OUT"]) / "probe.jsonl"
            out.write_text(json.dumps(row) + "\n", encoding="utf-8")
            return 0, ""
        o = int(extra["FORK_ROLLOUT_OPTION"])
        n = int(extra["FORK_ROLLOUT_N"])
        base = int(extra["FORK_ROLLOUT_SEED_BASE"])
        lines = []
        for j in range(n):
            rec = {"t": "fork_rollout", "option": o,
                   "rollout_seed": py_rollout_seed(base, o, j)}
            rec.update(self._outcome(o, j))
            lines.append(json.dumps(rec))
        Path(extra["FORK_ROLLOUT_OUT"]).write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        return 0, ""


def fake_address(dtype="play", n_per_option=16):
    return {"engine_sha": "c1c64a5", "overlay_sha": "6c3b0bd",
            "deck_a": "MySeat", "deck_b": "OpponentSeat",
            "controllers": "ai-log/ai-log", "seed": 20017, "swap": False,
            "decision_index": 41, "decision_type": dtype, "seat": "MySeat",
            "turn": 5, "game_file": "game-20017-1.jsonl",
            "deck_a_path": "/dev/null", "deck_b_path": "/dev/null"}


PROBE_2OPT = {"type": "play", "seat": "MySeat", "turn": 5, "phase": "MAIN1",
              "n_options": 2, "choice": 0,
              "options": ["CAST Big Play", "PASS (do nothing)"]}


# -------------------------------------------------------------------- tests

def test_rollout_key_pinned():
    address = {"engine_sha": "c1c64a5", "overlay_sha": "6c3b0bd",
               "deck_a": "UR Delver", "deck_b": "Burn",
               "controllers": "ai-log/ai-log", "seed": 20017, "swap": False,
               "decision_index": 41, "game_file": "game-20017-1.jsonl"}
    k = vk.rollout_key(address)
    check("rollout_key pinned literal", k == "b386291ec4e5ae3d", k)
    check("rollout_key 16-hex", len(k) == 16
          and all(c in "0123456789abcdef" for c in k))
    # extra address fields (deck paths, decision_type) must NOT change the key
    extra = dict(address, deck_a_path="/x", decision_type="play", seat="A")
    check("rollout_key ignores non-payload fields",
          vk.rollout_key(extra) == k)


def test_seed_fn_mirror():
    # SplitMix64 known-answer vector: mix64(0x9E3779B97F4A7C15) is splitmix64
    # stream(seed=0) output #1. rolloutSeed(0, 1, 0) == mix64(C1).
    check("mix64(0) == 0", mix64(0) == 0)
    check("SplitMix64 known-answer vector",
          py_rollout_seed(0, 1, 0) == 0xE220A8397B1DCDAF,
          hex(py_rollout_seed(0, 1, 0)))
    check("seed fn deterministic",
          py_rollout_seed(20017, 3, 7) == py_rollout_seed(20017, 3, 7))
    seeds = set()
    total = 0
    for i in range(8):
        for j in range(64):
            seeds.add(py_rollout_seed(20017, i, j))
            total += 1
    check("distinct (option_i, rollout_j) pairs -> distinct seeds",
          len(seeds) == total, "%d/%d" % (len(seeds), total))
    check("SEED_FN string documents this exact function",
          vk.SEED_FN == "mix64(base_seed ^ (i*C1) ^ (j*C2))")
    # pin the mirror against the Java source of truth (constants must appear)
    java = (vk.REPO / "forge-overlay" / "forge-gui-desktop" / "src" / "main"
            / "java" / "forge" / "spike" / "LlmSpikeFork.java")
    if java.is_file():
        src = java.read_text(encoding="utf-8", errors="replace")
        for const in ("0x9E3779B97F4A7C15", "0xC2B2AE3D27D4EB4F",
                      "0xBF58476D1CE4E5B9", "0x94D049BB133111EB"):
            check("java source pins %s" % const, const + "L" in src)
    else:
        print("[skip] LlmSpikeFork.java not in this checkout")


def test_exhaustive_lane():
    # promotion: Bolt (3) >= opp life 3, PASS scores 0
    out, lane, verified, reason, missing = run_verify_one(lethal_cand())
    check("exhaustive: verified", verified and out["status"] == "verified")
    check("exhaustive: lane", lane == "exhaustive")
    check("exhaustive: option_scores [10, 0]",
          out["answer_key"]["option_scores"] == [10, 0])
    check("exhaustive: verified_by", out["answer_key"]["verified_by"] == "exhaustive")
    check("exhaustive: unprobed caveat with --driver none",
          "unprobed_replay_address" in out["answer_key"]["caveats"])
    check("exhaustive: stamps tool/utc/sha",
          out.get("verified_by_tool") == "verify_keys.v1"
          and out.get("verified_utc") and out.get("contract_sha") == "shashasha")
    check("exhaustive: evidence names spell and life",
          "Lightning Bolt" in out.get("adjudication_evidence", "")
          and "opp_life=3" in out.get("adjudication_evidence", ""))
    # burn smaller than OPP life must NOT verify
    out, _, verified, reason, _ = run_verify_one(lethal_cand(opp_life=5))
    check("exhaustive: bolt < life 5 not verified",
          not verified and reason == "exhaustive_no_lethal_option")
    # degenerate: every option lethal
    out, _, verified, reason, _ = run_verify_one(lethal_cand(
        options=["CAST Lightning Bolt", "CAST Lava Spike"]))
    check("exhaustive: all-lethal degenerate",
          not verified and reason == "degenerate_all_correct")
    # non-empty stack
    out, _, verified, reason, _ = run_verify_one(lethal_cand(
        prompt=lethal_prompt(stack="#1 Counterspell (OpponentSeat)")))
    check("exhaustive: non-empty stack refused",
          not verified and reason == "non_empty_stack_v1_restriction")
    # missing prompt
    out, _, verified, reason, _ = run_verify_one(lethal_cand(prompt=""))
    check("exhaustive: missing prompt",
          not verified and reason == "missing_prompt")
    # creature-targeted burn is NOT face lethal
    out, _, verified, reason, _ = run_verify_one(lethal_cand(
        options=["CAST Lightning Bolt -> Tarmogoyf (2/3)", "PASS (do nothing)"]))
    check("exhaustive: creature-targeted bolt scores 0, not verified",
          not verified and reason == "exhaustive_no_lethal_option")


def test_not_silently_promoted():
    failing = [
        lethal_cand(opp_life=5),                              # no lethal
        lethal_cand(options=["CAST Lightning Bolt",
                             "CAST Lava Spike"]),             # degenerate
        lethal_cand(dtype="attackers"),                       # out of scope
        lethal_cand(category="free-win"),                     # mid-stack
        lethal_cand(prompt=""),                               # no prompt
    ]
    for i, cand in enumerate(failing):
        out, _, verified, reason, _ = run_verify_one(cand)
        ok = (not verified and out["status"] == "candidate"
              and "answer_key" not in out
              and out.get("unverifiable_reason"))
        check("failing row %d stays candidate with reason" % i, bool(ok),
              "status=%s reason=%s" % (out["status"], reason))


def test_route_lane():
    cases = [
        ("lethal-now", "play", "exhaustive", None),
        ("lethal-now", "attackers", None, "count_encoded_no_v1_rollout"),
        ("forced-block", "blockers", None, "count_encoded_no_v1_rollout"),
        ("free-win", "play", None, "non_empty_stack_v1_restriction"),
        ("mulligan", "mulligan", "rollout", None),
        ("save-vs-lethal", "play", "rollout", None),
        ("graded-midgame", "play", "rollout", None),
        ("graded-midgame", "attackers", None, "count_encoded_no_v1_rollout"),
        ("some-new-category", "play", None, "unsupported_decision_type"),
    ]
    for cat, dtype, want_lane, want_reason in cases:
        lane, reason = vk.route_lane(
            {"category": cat, "source": {"decision_type": dtype}})
        check("route %s@%s -> %s" % (cat, dtype, want_lane or want_reason),
              lane == want_lane and reason == want_reason,
              "got %s/%s" % (lane, reason))
    # lane filter: an exhaustive row under --lane rollout is filtered, not run
    out, lane, verified, reason, _ = run_verify_one(lethal_cand(), lane="rollout")
    check("lane filter -> lane_filtered_out",
          not verified and reason == "lane_filtered_out" and lane == "none")


def test_score_from_wp():
    # clean separation: max->10, min->0, everything in [0, 10]
    scores, reason = vk.score_from_wp([0.9, 0.2], [0.05, 0.05], "graded-midgame")
    check("wp clean gap -> [10, 0]", scores == [10, 0] and reason is None)
    # gap gate: 0.10 < 0.20 threshold
    scores, reason = vk.score_from_wp([0.55, 0.45], [0.02, 0.02], "graded-midgame")
    check("gap 0.10 rejected at 0.20 bar",
          scores is None and reason == "rollout_noise_below_threshold")
    # mulligan-specific 0.15 bar: gap 0.17 passes for mulligan, fails otherwise
    scores, reason = vk.score_from_wp([0.62, 0.45], [0.03, 0.03], "mulligan")
    check("mulligan gap 0.17 passes 0.15 bar", scores is not None)
    scores, reason = vk.score_from_wp([0.62, 0.45], [0.03, 0.03], "graded-midgame")
    check("same gap fails non-mulligan 0.20 bar",
          scores is None and reason == "rollout_noise_below_threshold")
    # noise gate scales with SE: gap 0.30 but 2*max(se)=0.40
    scores, reason = vk.score_from_wp([0.6, 0.3], [0.05, 0.20], "graded-midgame")
    check("gap < 2*max(se) rejected",
          scores is None and reason == "rollout_noise_below_threshold")
    # CI-overlap tie forced to 10 (never punish a tie the rollout can't split)
    scores, reason = vk.score_from_wp([0.80, 0.78, 0.20],
                                      [0.02, 0.02, 0.02], "graded-midgame")
    check("CI-overlap runner-up forced to 10",
          scores is not None and scores[0] == 10 and scores[1] == 10
          and scores[2] == 0, str(scores))
    # HALF-UP rounding: raw 0.5 -> 1 (banker's would give 0)
    scores, reason = vk.score_from_wp([0.0, 0.05, 1.0],
                                      [0.001, 0.001, 0.001], "graded-midgame")
    check("half-up rounding at raw 0.5", scores is not None and scores[1] == 1,
          str(scores))
    if scores is not None:
        check("scores within [0, 10]", all(0 <= s <= 10 for s in scores))
    # missing wp/se -> rejected, never promoted
    check("None wp rejected",
          vk.score_from_wp([None, 0.5], [0.1, 0.1], "x")[0] is None)
    check("empty se rejected",
          vk.score_from_wp([0.9, 0.1], [None, None], "x")[0] is None)


def test_rollout_lane_fake_engine():
    with tempfile.TemporaryDirectory() as td:
        # option 0 wins 3/4, option 1 wins 1/4 -> gap 0.5, clean key
        def outcome(o, j):
            win = (j % 4 != 0) if o == 0 else (j % 4 == 0)
            return {"winner": "A" if win else "B", "forced_ok": True}
        drv = FakeEngine(PROBE_2OPT, outcome, work_dir=td)
        res = drv.rollout(fake_address(), n_per_option=16)
        check("fake engine: ok", res["ok"] is True, str(res.get("reason")))
        check("fake engine: wp [0.75, 0.25]",
              res["option_wp"] == [0.75, 0.25], str(res["option_wp"]))
        check("fake engine: wp within [0,1]",
              all(0.0 <= w <= 1.0 for w in res["option_wp"]))
        exp_se = (0.75 * 0.25 / 16) ** 0.5
        check("fake engine: se == sqrt(p(1-p)/n)",
              all(abs(s - exp_se) < 1e-12 for s in res["option_wp_se"]))
        check("fake engine: counts partition n_per_option",
              all(res["option_wins"][i] + res["option_losses"][i]
                  + res["option_draws"][i] + res["option_invalid"][i] == 16
                  for i in range(2)))
        flat = [s for per_opt in res["rollout_seeds"] for s in per_opt]
        check("fake engine: all rollout seeds distinct across options",
              len(set(flat)) == len(flat) == 32)
        check("fake engine: seeds match the python mirror",
              res["rollout_seeds"][1][3] == py_rollout_seed(20017, 1, 3))
        # se shrinks as n grows (p = 0.5 at both n: se halves when n x4)
        def coin(o, j):
            return {"winner": "A" if j % 2 == 0 else "B", "forced_ok": True}
        se_small = FakeEngine(PROBE_2OPT, coin, work_dir=td) \
            .rollout(fake_address(), n_per_option=8)["option_wp_se"][0]
        se_big = FakeEngine(PROBE_2OPT, coin, work_dir=td) \
            .rollout(fake_address(), n_per_option=32)["option_wp_se"][0]
        check("se shrinks as n grows (halves when n x4)",
              abs(se_small - 2.0 * se_big) < 1e-12,
              "se(n=8)=%r se(n=32)=%r" % (se_small, se_big))
        # forced_ok=False rows are invalid, excluded from the wp denominator
        def invalid0(o, j):
            if j == 0:
                return {"winner": "A", "forced_ok": False}
            return {"winner": "A" if j <= 8 else "B", "forced_ok": True}
        res = FakeEngine(PROBE_2OPT, invalid0, work_dir=td) \
            .rollout(fake_address(), n_per_option=16)
        check("invalid rows excluded from denominator",
              res["option_invalid"][0] == 1
              and abs(res["option_wp"][0] - 8.0 / 15.0) < 1e-12,
              str((res["option_invalid"], res["option_wp"])))
        # full promotion through verify_one
        drv = FakeEngine(PROBE_2OPT, outcome, work_dir=td)
        out, lane, verified, reason, missing = run_verify_one(
            rollout_cand(), driver=drv, name_idx=NAME_IDX_WITH_PATHS)
        check("fake engine: verify_one promotes",
              verified and out["status"] == "verified"
              and lane == "rollout", str(reason))
        ak = out.get("answer_key") or {}
        check("fake engine: answer_key fields",
              ak.get("verified_by") == "rollout"
              and ak.get("option_scores") == [10, 0]
              and ak.get("option_wp") == [0.75, 0.25]
              and len(ak.get("option_wp_se") or []) == 2
              and ak.get("rollout", {}).get("seed_fn") == vk.SEED_FN
              and "single_deal_hidden_info" in ak.get("caveats", []))
        check("fake engine: probe evidence matched",
              out.get("probe", {}).get("matched") is True)


def test_replay_drift_rc4():
    with tempfile.TemporaryDirectory() as td:
        drv = FakeEngine(PROBE_2OPT, lambda o, j: {}, work_dir=td,
                         rc_map={"enumerate": 4})
        out, lane, verified, reason, _ = run_verify_one(
            rollout_cand(), driver=drv, name_idx=NAME_IDX_WITH_PATHS)
        check("rc==4 probe -> replay_drift, not promoted",
              not verified and reason == "replay_drift"
              and out.get("unverifiable_reason") == "replay_drift"
              and "answer_key" not in out)
        # probe options differing from the candidate's is also drift
        probe = dict(PROBE_2OPT, options=["CAST Something Else", "PASS"])
        drv = FakeEngine(probe, lambda o, j: {"winner": "A", "forced_ok": True},
                         work_dir=td)
        out, lane, verified, reason, _ = run_verify_one(
            rollout_cand(), driver=drv, name_idx=NAME_IDX_WITH_PATHS)
        check("probe option mismatch -> replay_drift",
              not verified and reason == "replay_drift")


def test_rollout_lane_fixture():
    cand = rollout_cand()
    addr = vk.address_of(cand, {})
    with tempfile.TemporaryDirectory() as td:
        fx = Path(td) / "fixture.jsonl"
        # clean promotion
        rec = fixture_record(addr, [0.9, 0.2], [0.05, 0.05],
                             cand["options"])
        fx.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        drv = vk.FixtureRolloutDriver(str(fx))
        out, lane, verified, reason, missing = run_verify_one(cand, driver=drv)
        check("fixture: promoted", verified and out["status"] == "verified",
              str(reason))
        ak = out.get("answer_key") or {}
        check("fixture: option_wp/se/scores internally consistent",
              ak.get("option_wp") == [0.9, 0.2]
              and ak.get("option_wp_se") == [0.05, 0.05]
              and ak.get("option_scores") == [10, 0]
              and all(0.0 <= w <= 1.0 for w in ak.get("option_wp", [])))
        check("fixture: no engine flagged missing", missing is False)
        # noise below threshold: NOT promoted
        rec = fixture_record(addr, [0.55, 0.45], [0.02, 0.02],
                             cand["options"])
        fx.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        drv = vk.FixtureRolloutDriver(str(fx))
        out, lane, verified, reason, _ = run_verify_one(cand, driver=drv)
        check("fixture: noisy rollout not promoted",
              not verified and reason == "rollout_noise_below_threshold"
              and "answer_key" not in out)
        # fixture miss -> no_engine_available + verbatim driver_reason
        drv = vk.FixtureRolloutDriver(str(Path(td) / "empty.jsonl"))
        out, lane, verified, reason, _ = run_verify_one(cand, driver=drv)
        check("fixture miss -> no_engine_available",
              not verified and reason == "no_engine_available"
              and out.get("driver_reason") == "fixture_miss")
        # non-deterministic source game refused before any driver work
        bad = rollout_cand(controllers="llm/ai")
        out, lane, verified, reason, _ = run_verify_one(bad, driver=drv)
        check("llm-controller source -> non_deterministic_source_game",
              not verified and reason == "non_deterministic_source_game")
        # mulligan lane through the fixture driver (0.15 bar)
        mull = rollout_cand(category="mulligan", dtype="mulligan",
                            options=["KEEP", "MULLIGAN"],
                            prompt="hand(7): ...\n")  # no stack mark needed
        maddr = vk.address_of(mull, {})
        rec = fixture_record(maddr, [0.62, 0.45], [0.03, 0.03],
                             mull["options"], dtype="mulligan")
        fx.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        drv = vk.FixtureRolloutDriver(str(fx))
        out, lane, verified, reason, _ = run_verify_one(mull, driver=drv)
        check("fixture: mulligan verified at 0.15 bar",
              verified and out["answer_key"]["option_scores"] == [10, 0],
              str(reason))
        # combo seat gets the rollout_policy_caveat
        combo = rollout_cand(seat="Doomsday")
        combo["source"]["deck_a"] = "Doomsday"
        caddr = vk.address_of(combo, {})
        rec = fixture_record(caddr, [0.9, 0.2], [0.05, 0.05],
                             combo["options"])
        fx.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        drv = vk.FixtureRolloutDriver(str(fx))
        out, lane, verified, reason, _ = run_verify_one(
            combo, driver=drv, name_idx={"Doomsday": ("doomsday", None)})
        check("combo seat carries rollout_policy_caveat",
              verified and "rollout_policy_caveat"
              in out["answer_key"]["caveats"])


def test_additive_echo():
    cand = lethal_cand(extra={"weird_future_field": {"x": [1, 2]},
                              "seed_block": "blk-7"})
    out, _, verified, _, _ = run_verify_one(cand)
    check("echo: verified", verified)
    missing = [k for k in cand if k not in out]
    check("echo: every input key survives", not missing, str(missing))
    changed = [k for k in cand
               if k != "status" and out.get(k) != cand.get(k)]
    check("echo: no input value mutated (except status)", not changed,
          str(changed))
    check("echo: unknown future field intact",
          out.get("weird_future_field") == {"x": [1, 2]})


def test_metric_block():
    m = vk.metric_block(8, 16)
    check("metric_block fields", all(k in m for k in
          ("est", "se", "ci95", "n", "method")))
    check("metric_block est", abs(m["est"] - 0.5) < 1e-12)
    check("metric_block ci95 ordered",
          m["ci95"][0] <= m["est"] <= m["ci95"][1])
    m4 = vk.metric_block(32, 64)
    check("metric_block se shrinks as n grows", m4["se"] < m["se"],
          "se(16)=%r se(64)=%r" % (m["se"], m4["se"]))
    m0 = vk.metric_block(0, 0)
    check("metric_block n=0 -> est None, no crash",
          m0["est"] is None and m0["n"] == 0)


def test_cli_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        rcand = rollout_cand()
        addr = vk.address_of(rcand, vk.deck_name_index())
        fx = td / "fixture.jsonl"
        fx.write_text(json.dumps(fixture_record(
            addr, [0.9, 0.2], [0.05, 0.05], rcand["options"])) + "\n",
            encoding="utf-8")
        cands = td / "cands.jsonl"
        rows = [lethal_cand(),                    # exhaustive verified
                lethal_cand(category="free-win"),  # unverifiable
                rcand]                             # rollout verified
        with open(str(cands), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
            f.write("{not json\n")                 # -> warnings, not a crash
        out = td / "positions.jsonl"
        summ = td / "summary.json"
        buf_err = io.StringIO()
        with contextlib.redirect_stderr(buf_err):
            rc = vk.main([str(cands), "--out", str(out),
                          "--driver", "fixture", "--fixture", str(fx),
                          "--summary", str(summ)])
        check("cli: exit 0", rc == 0, "rc=%r" % rc)
        got = [json.loads(l) for l in
               out.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("cli: row count preserved", len(got) == 3)
        by_cat = dict((r["category"], r) for r in got)
        check("cli: lethal verified",
              by_cat["lethal-now"]["status"] == "verified")
        check("cli: free-win unverifiable with reason",
              by_cat["free-win"]["status"] == "candidate"
              and by_cat["free-win"]["unverifiable_reason"]
              == "non_empty_stack_v1_restriction")
        check("cli: rollout row verified",
              by_cat["graded-midgame"]["status"] == "verified")
        s = json.loads(summ.read_text(encoding="utf-8"))
        check("cli: summary schema", s["schema"] == "verify-keys-summary.v1")
        check("cli: summary counts", s["n_in"] == 3 and s["n_verified"] == 2
              and s["n_unverifiable"] == 1)
        yr = s["yield_rate"]
        check("cli: yield_rate carries est/se/ci95/n",
              all(k in yr for k in ("est", "se", "ci95", "n"))
              and abs(yr["est"] - 2.0 / 3.0) < 1e-9)
        check("cli: coverage_note present", bool(s.get("coverage_note")))
        check("cli: bad line landed in warnings",
              any("unparseable" in w for w in s.get("warnings", [])))
        # --lane exhaustive filters the rollout row out instead of running it
        with contextlib.redirect_stderr(io.StringIO()):
            rc = vk.main([str(cands), "--out", str(td / "p2.jsonl"),
                          "--driver", "fixture", "--fixture", str(fx),
                          "--summary", str(td / "s2.json"),
                          "--lane", "exhaustive"])
        s2 = json.loads((td / "s2.json").read_text(encoding="utf-8"))
        check("cli: --lane exhaustive filters rollout row",
              s2["n_verified"] == 1
              and s2["by_category"]["graded-midgame"]["by_reason"]
              .get("lane_filtered_out") == 1)
        # empty candidates -> exit 2
        empty = td / "empty.jsonl"
        empty.write_text("")
        with contextlib.redirect_stderr(io.StringIO()):
            rc = vk.main([str(empty), "--out", str(td / "p3.jsonl")])
        check("cli: empty candidates -> exit 2", rc == 2, "rc=%r" % rc)


def test_no_egress():
    src = Path(vk.__file__).read_text(encoding="utf-8")
    for bad in ("import urllib", "import socket", "import http",
                "import requests"):
        check("module source free of %r" % bad, bad not in src)
    # _base_env must add only fork/replay keys — never an egress opt-in
    drv = vk.RolloutDriver(forge_home=None)
    env = drv._base_env(fake_address(), "/tmp/x")
    added = set(env) - set(os.environ)
    allowed_prefixes = ("FORK_", "GAME_", "SWAP_", "CONTROLLER_", "DECK_",
                        "ENGINE_", "OVERLAY_", "EVAL_")
    stray = [k for k in added if not k.startswith(allowed_prefixes)]
    check("_base_env adds only fork/replay keys", not stray, str(stray))
    check("_base_env never grants egress",
          "OPENROUTER_ALLOW_EGRESS" not in added)
    # the JVM child env DENYLIST must cover the egress opt-in and model keys,
    # and _base_env must actually strip an inherited denylisted var. The test
    # sets a FAKE API key to prove the strip — never the egress flag itself.
    denylist = getattr(vk.RolloutDriver, "CHILD_ENV_DENYLIST", ())
    check("child-env denylist covers egress opt-in and model keys",
          "OPENROUTER_ALLOW_EGRESS" in denylist
          and "OPENROUTER_API_KEY" in denylist)
    had = os.environ.get("OPENROUTER_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = "fake-key-for-strip-test"
    try:
        env = drv._base_env(fake_address(), "/tmp/x")
        check("_base_env strips inherited denylisted vars",
              "OPENROUTER_API_KEY" not in env
              and all(k not in env for k in denylist))
    finally:
        if had is None:
            del os.environ["OPENROUTER_API_KEY"]
        else:
            os.environ["OPENROUTER_API_KEY"] = had
    # a full fixture-driver CLI run with socket.socket DISABLED must succeed
    def _no_socket(*a, **kw):
        raise AssertionError("egress attempted: socket opened during verify")
    real_socket = socket.socket
    real_create = socket.create_connection
    socket.socket = _no_socket
    socket.create_connection = _no_socket
    try:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = td / "c.jsonl"
            cands.write_text(json.dumps(lethal_cand()) + "\n",
                             encoding="utf-8")
            fx = td / "f.jsonl"
            fx.write_text("")
            with contextlib.redirect_stderr(io.StringIO()):
                rc = vk.main([str(cands), "--out", str(td / "p.jsonl"),
                              "--driver", "fixture", "--fixture", str(fx),
                              "--summary", str(td / "s.json")])
            check("no-egress: full CLI run with sockets disabled", rc == 0,
                  "rc=%r" % rc)
    finally:
        socket.socket = real_socket
        socket.create_connection = real_create


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
