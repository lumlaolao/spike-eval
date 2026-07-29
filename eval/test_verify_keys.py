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
  * test_rollout_evidence_math: wp_spread, wp_spread_se, best/runner-up
    indices or separation_z drifting from the hand-worked constants pinned
    below (spread = max-min, spread_se = sqrt(se_hi^2+se_lo^2), separation_z
    = (best-runner_up)/sqrt(se_best^2+se_ru^2)); a zero denominator producing
    inf/NaN instead of None; ties ranking non-deterministically.
  * test_rejected_row_retains_evidence: a rollout_noise_below_threshold row
    coming back WITHOUT rollout_evidence, or with option_wp emptied/nulled —
    that is exactly the discarded-measurement regression this guards; also an
    all-invalid rollout (option_wp all None) failing to be distinguishable
    from a genuine tie via option_invalid + wp_spread=None.
  * test_verified_row_carries_evidence: a verified row NOT carrying
    rollout_evidence (spread must be comparable across decided/undecided).
  * test_answer_key_unchanged: answer_key growing/losing a field, or
    rollout_evidence leaking INSIDE answer_key, or any candidate row gaining
    an answer_key — every existing answer_key consumer must be untouched.
  * test_threshold_flags: --gap-threshold / --mulligan-gap-threshold /
    --min-separation-z not being honoured, their defaults drifting from
    0.20 / 0.15 / 2.0, or the summary failing to record the bar it used.
  * test_summary_spread_distribution: quantiles drifting from the pinned
    linear-interpolation values, the histogram mis-binning (0.30 landing in
    bin 5 through float underestimation), or the block counting exhaustive
    rows / dropping n_no_wp.
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


def null_wp_record(address, options, dtype="play", n_per_option=16):
    """A rollout that RAN but decided nothing: every replay invalid, so every
    per-option wp is None. The engine's own all-garbage output shape."""
    n = len(options)
    rec = fixture_record(address, [0.5] * n, [0.1] * n, options,
                         dtype=dtype, n_per_option=n_per_option)
    rec["option_wp"] = [None] * n
    rec["option_wp_se"] = [None] * n
    rec["option_wins"] = [0] * n
    rec["option_losses"] = [0] * n
    rec["option_draws"] = [0] * n
    rec["option_invalid"] = [n_per_option] * n
    return rec


def at_decision(cand, decision_index):
    """Same candidate, distinct replay address (=> distinct rollout_key)."""
    cand["source"]["decision_index"] = decision_index
    cand["id"] = "cafe%012d" % decision_index
    return cand


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


def _result(wp, se, n_per_option=16, **kw):
    """Minimal rollout-result.v1 for the evidence builder."""
    n = len(wp)
    rec = {"schema": "rollout-result.v1", "ok": True, "reason": None,
           "option_wp": wp, "option_wp_se": se, "n_options": n,
           "option_wins": [0] * n, "option_losses": [0] * n,
           "option_draws": [0] * n, "option_invalid": [0] * n,
           "n_per_option": n_per_option, "continuation": "ai-log/ai-log",
           "seed_fn": vk.SEED_FN, "engine_sha": "c1c64a5",
           "overlay_sha": "6c3b0bd"}
    rec.update(kw)
    return rec


def test_rollout_evidence_math():
    # HAND-WORKED CASE A: wp [0.75, 0.25], se [0.10, 0.05].
    #   spread     = 0.75 - 0.25                   = 0.5
    #   spread_se  = sqrt(0.10^2 + 0.05^2)
    #              = sqrt(0.0125)                  = 0.1118033988749895
    #   sep_z      = 0.5 / 0.1118033988749895      = 4.472135954999579
    #   gate       = max(0.20, 2.0*0.10)           = 0.20  -> decided
    ev = vk.build_rollout_evidence(_result([0.75, 0.25], [0.10, 0.05]),
                                   True, None, "graded-midgame")
    check("evidence A: wp_spread 0.5", abs(ev["wp_spread"] - 0.5) < 1e-12,
          repr(ev["wp_spread"]))
    check("evidence A: wp_spread_se sqrt(0.0125)",
          abs(ev["wp_spread_se"] - 0.1118033988749895) < 1e-12,
          repr(ev["wp_spread_se"]))
    check("evidence A: separation_z 4.472135954999579",
          abs(ev["separation_z"] - 4.472135954999579) < 1e-9,
          repr(ev["separation_z"]))
    check("evidence A: best/runner-up indices",
          ev["best_index"] == 0 and ev["runner_up_index"] == 1)
    check("evidence A: gate numbers recorded",
          abs(ev["gate"]["base_threshold"] - 0.20) < 1e-12
          and abs(ev["gate"]["noise_floor"] - 0.20) < 1e-12
          and abs(ev["gate"]["required_gap"] - 0.20) < 1e-12
          and abs(ev["gate"]["min_separation_z"] - 2.0) < 1e-12,
          str(ev["gate"]))
    check("evidence A: decided flag + null reason",
          ev["decided"] is True and ev["reason"] is None)
    check("evidence A: measurement echoed verbatim",
          ev["option_wp"] == [0.75, 0.25]
          and ev["option_wp_se"] == [0.10, 0.05]
          and ev["n_options"] == 2 and ev["n_per_option"] == 16
          and ev["continuation"] == "ai-log/ai-log"
          and ev["seed_fn"] == vk.SEED_FN)

    # HAND-WORKED CASE B (3 options, best is NOT index 0, runner-up is NOT
    # the min): wp [0.30, 0.90, 0.50], se [0.05, 0.05, 0.05].
    #   best=1, runner_up=2, worst=0
    #   spread     = 0.90 - 0.30                   = 0.6
    #   spread_se  = sqrt(0.0025 + 0.0025)
    #              = sqrt(0.005)                   = 0.07071067811865476
    #   sep_z      = (0.90-0.50) / 0.07071067811865476
    #              = 0.4 / 0.07071067811865476     = 5.656854249492381
    ev = vk.build_rollout_evidence(
        _result([0.30, 0.90, 0.50], [0.05, 0.05, 0.05]), True, None,
        "graded-midgame")
    check("evidence B: best=1 runner_up=2 (not the min)",
          ev["best_index"] == 1 and ev["runner_up_index"] == 2,
          str((ev["best_index"], ev["runner_up_index"])))
    check("evidence B: wp_spread 0.6", abs(ev["wp_spread"] - 0.6) < 1e-12,
          repr(ev["wp_spread"]))
    check("evidence B: wp_spread_se sqrt(0.005)",
          abs(ev["wp_spread_se"] - 0.07071067811865476) < 1e-12,
          repr(ev["wp_spread_se"]))
    check("evidence B: separation_z 5.656854249492381",
          abs(ev["separation_z"] - 5.656854249492381) < 1e-9,
          repr(ev["separation_z"]))

    # separation_z is a DIAGNOSTIC, not the gate: this row's z is 3.53 (well
    # over 2) yet the 0.20 spread bar still rejects it. Conflating the two is
    # the exact mistake this field must not invite.
    #   wp [0.55, 0.45], se [0.02, 0.02]
    #   spread = 0.1, spread_se = sqrt(0.0008) = 0.028284271247461902
    #   sep_z  = 0.1 / 0.028284271247461902 = 3.5355339059327378
    ev = vk.build_rollout_evidence(_result([0.55, 0.45], [0.02, 0.02]),
                                   False, "rollout_noise_below_threshold",
                                   "graded-midgame")
    check("evidence C: separation_z 3.5355339059327378",
          abs(ev["separation_z"] - 3.5355339059327378) < 1e-9,
          repr(ev["separation_z"]))
    check("evidence C: high z, still under the spread bar",
          ev["separation_z"] > 2.0
          and ev["wp_spread"] < ev["gate"]["required_gap"]
          and ev["decided"] is False
          and ev["reason"] == "rollout_noise_below_threshold")

    # zero SE: the difference is undefined, not infinite — must be None, and
    # must stay JSON-serialisable (json.dumps would emit invalid `Infinity`).
    ev = vk.build_rollout_evidence(_result([0.6, 0.4], [0.0, 0.0]),
                                   True, None, "graded-midgame")
    check("evidence: zero-SE separation_z is None, not inf",
          ev["separation_z"] is None and ev["wp_spread_se"] == 0.0)
    check("evidence: serialises as valid JSON",
          "Infinity" not in json.dumps(ev) and "NaN" not in json.dumps(ev))

    # exact tie: spread 0, z 0 — measured, comparable, never decided
    ev = vk.build_rollout_evidence(_result([0.5, 0.5], [0.05, 0.05]),
                                   False, "rollout_noise_below_threshold",
                                   "graded-midgame")
    check("evidence: exact tie -> spread 0, z 0, deterministic ranking",
          ev["wp_spread"] == 0.0 and abs(ev["separation_z"]) < 1e-12
          and ev["best_index"] == 0 and ev["runner_up_index"] == 1)

    # all-None wp (every replay invalid): evidence still emitted, derived
    # numbers None, and option_invalid says WHY. This is the "garbage" case
    # that must be distinguishable from a genuine tie.
    res = _result([None, None], [None, None])
    res["option_invalid"] = [16, 16]
    ev = vk.build_rollout_evidence(res, False,
                                   "rollout_noise_below_threshold",
                                   "graded-midgame")
    check("evidence: all-invalid rollout still emits evidence",
          ev["option_wp"] == [None, None] and ev["wp_spread"] is None
          and ev["wp_spread_se"] is None and ev["separation_z"] is None
          and ev["best_index"] is None
          and ev["option_invalid"] == [16, 16]
          and ev["gate"]["measurable"] is False)

    # single-option degenerate shape: no runner-up, no z, no crash
    ev = vk.build_rollout_evidence(_result([0.7], [0.05]), False,
                                   "rollout_noise_below_threshold",
                                   "graded-midgame")
    check("evidence: 1 option -> runner_up/sep_z None, spread 0",
          ev["runner_up_index"] is None and ev["separation_z"] is None
          and ev["wp_spread"] == 0.0)

    # mulligan uses the 0.15 bar in the recorded gate too
    ev = vk.build_rollout_evidence(_result([0.62, 0.45], [0.03, 0.03]),
                                   True, None, "mulligan")
    check("evidence: mulligan gate records the 0.15 bar",
          abs(ev["gate"]["base_threshold"] - 0.15) < 1e-12
          and abs(ev["gate"]["required_gap"] - 0.15) < 1e-12,
          str(ev["gate"]))


def test_rejected_row_retains_evidence():
    cand = rollout_cand()
    addr = vk.address_of(cand, {})
    with tempfile.TemporaryDirectory() as td:
        fx = Path(td) / "fixture.jsonl"
        # noise-rejected: measured wp MUST survive on the failing row
        fx.write_text(json.dumps(fixture_record(
            addr, [0.55, 0.45], [0.02, 0.02], cand["options"])) + "\n",
            encoding="utf-8")
        out, lane, verified, reason, _ = run_verify_one(
            cand, driver=vk.FixtureRolloutDriver(str(fx)))
        check("rejected row: still a candidate with the reason",
              not verified and out["status"] == "candidate"
              and out.get("unverifiable_reason")
              == "rollout_noise_below_threshold")
        ev = out.get("rollout_evidence")
        check("rejected row: rollout_evidence present", isinstance(ev, dict))
        check("rejected row: option_wp POPULATED (not None, not empty)",
              ev.get("option_wp") == [0.55, 0.45]
              and ev.get("option_wp_se") == [0.02, 0.02],
              str(ev.get("option_wp")))
        check("rejected row: spread/z computed on the failing row",
              abs(ev["wp_spread"] - 0.1) < 1e-12
              and abs(ev["separation_z"] - 3.5355339059327378) < 1e-9,
              str((ev.get("wp_spread"), ev.get("separation_z"))))
        check("rejected row: decided=False + verbatim reason",
              ev["decided"] is False
              and ev["reason"] == "rollout_noise_below_threshold")
        check("rejected row: still NO answer_key", "answer_key" not in out)
        # garbage vs tie: an all-invalid rollout is now distinguishable
        fx.write_text(json.dumps(null_wp_record(addr, cand["options"])) + "\n",
                      encoding="utf-8")
        out2, _, verified2, reason2, _ = run_verify_one(
            cand, driver=vk.FixtureRolloutDriver(str(fx)))
        ev2 = out2.get("rollout_evidence") or {}
        check("rejected row: all-invalid rollout distinguishable from a tie",
              not verified2 and reason2 == "rollout_noise_below_threshold"
              and ev2.get("wp_spread") is None
              and ev2.get("option_invalid") == [16, 16]
              and ev["wp_spread"] is not None,
              str(ev2.get("option_invalid")))
        # no measurement at all -> field ABSENT (absent != measured-and-tied)
        out3, _, _, reason3, _ = run_verify_one(
            cand, driver=vk.FixtureRolloutDriver(str(Path(td) / "none.jsonl")))
        check("no measurement -> rollout_evidence absent, not empty",
              reason3 == "no_engine_available"
              and "rollout_evidence" not in out3)
        out4, _, _, reason4, _ = run_verify_one(cand, driver=None)
        check("no driver -> rollout_evidence absent",
              reason4 == "no_engine_available"
              and "rollout_evidence" not in out4)


def test_verified_row_carries_evidence():
    cand = rollout_cand()
    addr = vk.address_of(cand, {})
    with tempfile.TemporaryDirectory() as td:
        fx = Path(td) / "fixture.jsonl"
        # wp [0.9, 0.2], se [0.05, 0.05]:
        #   spread    = 0.7
        #   spread_se = sqrt(0.005) = 0.07071067811865476
        #   sep_z     = 0.7 / 0.07071067811865476 = 9.899494936611665
        fx.write_text(json.dumps(fixture_record(
            addr, [0.9, 0.2], [0.05, 0.05], cand["options"])) + "\n",
            encoding="utf-8")
        out, lane, verified, reason, _ = run_verify_one(
            cand, driver=vk.FixtureRolloutDriver(str(fx)))
        check("verified row: promoted", verified, str(reason))
        ev = out.get("rollout_evidence") or {}
        check("verified row: carries rollout_evidence too",
              ev.get("option_wp") == [0.9, 0.2]
              and ev.get("decided") is True and ev.get("reason") is None)
        check("verified row: spread comparable with rejected rows",
              abs(ev["wp_spread"] - 0.7) < 1e-12
              and abs(ev["wp_spread_se"] - 0.07071067811865476) < 1e-12
              and abs(ev["separation_z"] - 9.899494936611665) < 1e-9,
              str((ev.get("wp_spread"), ev.get("separation_z"))))


def test_answer_key_unchanged():
    """Nothing that previously read answer_key sees a behaviour change."""
    # exhaustive lane: same three keys as before, evidence never added
    out, _, verified, _, _ = run_verify_one(lethal_cand())
    ak = out.get("answer_key") or {}
    check("answer_key (exhaustive) key set frozen",
          verified and set(ak) == {"verified_by", "option_scores", "caveats"},
          str(sorted(ak)))
    check("exhaustive row has no rollout_evidence (nothing was rolled out)",
          "rollout_evidence" not in out)
    cand = rollout_cand()
    addr = vk.address_of(cand, {})
    with tempfile.TemporaryDirectory() as td:
        fx = Path(td) / "fixture.jsonl"
        fx.write_text(json.dumps(fixture_record(
            addr, [0.9, 0.2], [0.05, 0.05], cand["options"])) + "\n",
            encoding="utf-8")
        out, _, verified, _, _ = run_verify_one(
            cand, driver=vk.FixtureRolloutDriver(str(fx)))
        ak = out.get("answer_key") or {}
        check("answer_key (rollout) key set frozen",
              verified and set(ak) == {"verified_by", "rollout",
                                       "option_scores", "option_wp",
                                       "option_wp_se", "caveats"},
              str(sorted(ak)))
        check("answer_key values unchanged",
              ak["verified_by"] == "rollout"
              and ak["option_scores"] == [10, 0]
              and ak["option_wp"] == [0.9, 0.2]
              and ak["option_wp_se"] == [0.05, 0.05]
              and ak["rollout"]["n_per_option"] == 16
              and ak["caveats"] == ["single_deal_hidden_info"])
        check("rollout_evidence is TOP LEVEL, never inside answer_key",
              "rollout_evidence" not in ak
              and "rollout_evidence" in out)
        # and a rejected row never grows a half-filled answer_key
        fx.write_text(json.dumps(fixture_record(
            addr, [0.55, 0.45], [0.02, 0.02], cand["options"])) + "\n",
            encoding="utf-8")
        out, _, verified, _, _ = run_verify_one(
            cand, driver=vk.FixtureRolloutDriver(str(fx)))
        check("candidate row carries evidence but NO answer_key",
              not verified and "answer_key" not in out
              and out["status"] == "candidate"
              and "rollout_evidence" in out)


def test_threshold_flags():
    # defaults are exactly the pre-flag constants
    check("default gap threshold 0.20",
          abs(vk.DEFAULT_GAP_THRESHOLD - 0.20) < 1e-12)
    check("default mulligan gap threshold 0.15",
          abs(vk.DEFAULT_MULLIGAN_GAP_THRESHOLD - 0.15) < 1e-12)
    check("default min separation z 2.0",
          abs(vk.DEFAULT_MIN_SEPARATION_Z - 2.0) < 1e-12)
    # gap 0.10, se 0.02: rejected at the default bar, accepted at 0.05
    scores, reason = vk.score_from_wp([0.55, 0.45], [0.02, 0.02],
                                      "graded-midgame")
    check("flag: default bar still rejects gap 0.10", scores is None)
    scores, reason = vk.score_from_wp([0.55, 0.45], [0.02, 0.02],
                                      "graded-midgame", 0.05)
    check("flag: --gap-threshold 0.05 admits it",
          scores == [10, 0] and reason is None, str(scores))
    # raising Z re-rejects it: required = max(0.05, 6.0*0.02) = 0.12 > 0.10
    scores, reason = vk.score_from_wp([0.55, 0.45], [0.02, 0.02],
                                      "graded-midgame", 0.05, 0.15, 6.0)
    check("flag: --min-separation-z 6.0 re-rejects it (0.12 bar)",
          scores is None and reason == "rollout_noise_below_threshold")
    # mulligan bar is independently tunable
    scores, _ = vk.score_from_wp([0.62, 0.45], [0.03, 0.03], "mulligan",
                                 0.20, 0.30)
    check("flag: --mulligan-gap-threshold 0.30 rejects gap 0.17",
          scores is None)
    # a tightened bar must never be silently bypassed by a zeroed threshold
    scores, _ = vk.score_from_wp([0.5, 0.5], [0.0, 0.0], "graded-midgame",
                                 0.0, 0.0, 0.0)
    check("flag: zeroed thresholds still reject a zero gap (no div-by-zero)",
          scores is None)
    # end to end: the flag reaches verify_one and the summary records it
    cand = rollout_cand()
    addr = vk.address_of(cand, vk.deck_name_index())
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        fx = td / "f.jsonl"
        fx.write_text(json.dumps(fixture_record(
            addr, [0.55, 0.45], [0.02, 0.02], cand["options"])) + "\n",
            encoding="utf-8")
        cands = td / "c.jsonl"
        cands.write_text(json.dumps(cand) + "\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            rc = vk.main([str(cands), "--out", str(td / "p.jsonl"),
                          "--driver", "fixture", "--fixture", str(fx),
                          "--summary", str(td / "s.json")])
        s = json.loads((td / "s.json").read_text(encoding="utf-8"))
        check("cli: default thresholds recorded in summary",
              rc == 0 and s["n_verified"] == 0
              and abs(s["thresholds"]["gap_threshold"] - 0.20) < 1e-12
              and abs(s["thresholds"]["min_separation_z"] - 2.0) < 1e-12,
              str(s.get("thresholds")))
        with contextlib.redirect_stderr(io.StringIO()):
            rc = vk.main([str(cands), "--out", str(td / "p2.jsonl"),
                          "--driver", "fixture", "--fixture", str(fx),
                          "--summary", str(td / "s2.json"),
                          "--gap-threshold", "0.05"])
        s2 = json.loads((td / "s2.json").read_text(encoding="utf-8"))
        check("cli: --gap-threshold 0.05 verifies the same row",
              rc == 0 and s2["n_verified"] == 1
              and abs(s2["thresholds"]["gap_threshold"] - 0.05) < 1e-12,
              str((s2["n_verified"], s2.get("thresholds"))))


def test_summary_spread_distribution():
    # hand-worked quantiles over spreads [0.1, 0.2, 0.3, 0.9] (type 7):
    #   p25: pos = 0.25*3 = 0.75 -> 0.1 + 0.75*(0.2-0.1) = 0.175
    #   p50: pos = 0.50*3 = 1.5  -> 0.2 + 0.50*(0.3-0.2) = 0.25
    #   p75: pos = 0.75*3 = 2.25 -> 0.3 + 0.25*(0.9-0.3) = 0.45
    vals = [0.1, 0.2, 0.3, 0.9]
    check("quantile p25 = 0.175", abs(vk.quantile(vals, 0.25) - 0.175) < 1e-12,
          repr(vk.quantile(vals, 0.25)))
    check("quantile median = 0.25", abs(vk.quantile(vals, 0.5) - 0.25) < 1e-12,
          repr(vk.quantile(vals, 0.5)))
    check("quantile p75 = 0.45", abs(vk.quantile(vals, 0.75) - 0.45) < 1e-12,
          repr(vk.quantile(vals, 0.75)))
    check("quantile of a single value", vk.quantile([0.42], 0.5) == 0.42)
    check("quantile of nothing is None", vk.quantile([], 0.5) is None)
    evs = [vk.build_rollout_evidence(_result([0.5 + s / 2.0, 0.5 - s / 2.0],
                                             [0.01, 0.01]),
                                     s >= 0.2, None if s >= 0.2 else
                                     "rollout_noise_below_threshold",
                                     "graded-midgame")
           for s in vals]
    res = _result([None, None], [None, None])
    res["option_invalid"] = [16, 16]
    evs.append(vk.build_rollout_evidence(
        res, False, "rollout_noise_below_threshold", "graded-midgame"))
    d = vk.spread_distribution(evs + [None])  # None = an exhaustive row
    check("dist: counts measured rows only",
          d["n_positions_measured"] == 5 and d["n_no_wp"] == 1
          and d["wp_spread"]["n"] == 4,
          str((d["n_positions_measured"], d["n_no_wp"])))
    check("dist: decided/undecided split",
          d["n_decided"] == 3 and d["n_undecided"] == 2,
          str((d["n_decided"], d["n_undecided"])))
    q = d["wp_spread"]
    check("dist: quantiles match the hand-worked values",
          abs(q["min"] - 0.1) < 1e-9 and abs(q["p25"] - 0.175) < 1e-9
          and abs(q["median"] - 0.25) < 1e-9 and abs(q["p75"] - 0.45) < 1e-9
          and abs(q["max"] - 0.9) < 1e-9, str(q))
    # bins of 0.05: 0.1->2, 0.2->4, 0.3->6 (NOT 5: float underestimation),
    # 0.9->18; every measured spread lands in exactly one bin.
    counts = d["wp_spread_hist"]["counts"]
    check("dist: histogram bins (0.30 in bin 6, not 5)",
          len(counts) == 20 and sum(counts) == 4
          and counts[2] == 1 and counts[4] == 1 and counts[6] == 1
          and counts[18] == 1 and counts[5] == 0, str(counts))
    check("dist: spread 1.0 lands in the closed top bin",
          vk.spread_distribution([vk.build_rollout_evidence(
              _result([1.0, 0.0], [0.01, 0.01]), True, None,
              "graded-midgame")])["wp_spread_hist"]["counts"][19] == 1)
    check("dist: empty input is None-safe",
          vk.spread_distribution([])["wp_spread"]["median"] is None)


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
              # engine driver defaults to determinize="library", so the key
              # must carry the determinized slug and NOT the single-deal one.
              and ak.get("caveats", []) == [vk.DETERMINIZED_CAVEAT])
        check("fake engine: probe evidence matched",
              out.get("probe", {}).get("matched") is True)


def test_determinize_contract():
    """The rollout continuation must actually re-deal hidden information.

    WHAT WOULD MAKE THIS FAIL: dropping FORK_ROLLOUT_DETERMINIZE from the
    child env (the engine would silently fall back to its own default and the
    Python-side provenance would be a lie); flipping the default back to
    "none"; letting an unknown mode through the constructor; or emitting the
    single-deal caveat on a determinized key. That last one is the one that
    matters most -- it is a claim about the number that would be exactly
    backwards, and nothing downstream could detect it.
    """
    seen = {}

    class EnvSpy(FakeEngine):
        def _run(self, env, extra):
            if extra["FORK_MODE"] == "rollout":
                seen["determinize"] = extra.get("FORK_ROLLOUT_DETERMINIZE")
            return FakeEngine._run(self, env, extra)

    def outcome(o, j):
        win = (j % 4 != 0) if o == 0 else (j % 4 == 0)
        return {"winner": "A" if win else "B", "forced_ok": True}

    with tempfile.TemporaryDirectory() as td:
        # 1. the default is the FIX, not the defect
        check("determinize: default is 'library'",
              vk.DEFAULT_DETERMINIZE == "library")

        # 2. the mode actually reaches the engine's env
        drv = EnvSpy(PROBE_2OPT, outcome, work_dir=td)
        res = drv.rollout(fake_address(), n_per_option=8)
        check("determinize: passed to the engine as FORK_ROLLOUT_DETERMINIZE",
              seen.get("determinize") == "library", repr(seen))
        check("determinize: recorded on the result",
              res.get("determinize") == "library")

        # 3. ...and onto the answer key, REPLACING the single-deal claim
        out, _, verified, _, _ = run_verify_one(
            rollout_cand(), driver=EnvSpy(PROBE_2OPT, outcome, work_dir=td),
            name_idx=NAME_IDX_WITH_PATHS)
        caveats = (out.get("answer_key") or {}).get("caveats") or []
        check("determinize: key carries the determinized caveat",
              verified and vk.DETERMINIZED_CAVEAT in caveats, str(caveats))
        check("determinize: key does NOT also claim single-deal",
              vk.SINGLE_DEAL_CAVEAT not in caveats, str(caveats))

        # 4. explicit "none" still works and tells the truth about itself
        drv = EnvSpy(PROBE_2OPT, outcome, work_dir=td, determinize="none")
        res = drv.rollout(fake_address(), n_per_option=8)
        check("determinize=none: passed through",
              seen.get("determinize") == "none", repr(seen))
        out, _, verified, _, _ = run_verify_one(
            rollout_cand(),
            driver=EnvSpy(PROBE_2OPT, outcome, work_dir=td,
                          determinize="none"),
            name_idx=NAME_IDX_WITH_PATHS)
        caveats = (out.get("answer_key") or {}).get("caveats") or []
        check("determinize=none: key claims single-deal, not determinized",
              verified and caveats == [vk.SINGLE_DEAL_CAVEAT], str(caveats))

        # 5. a typo is refused, not silently treated as one of the modes --
        #    the whole point is that "looks fixed but isn't" cannot recur
        try:
            FakeEngine(PROBE_2OPT, outcome, work_dir=td, determinize="libary")
            check("determinize: typo refused", False, "constructor accepted it")
        except ValueError:
            check("determinize: typo refused", True)


def test_sub_threshold_reason():
    """"Every option wins" and "options differ slightly" are not the same fact.

    WHAT WOULD MAKE THIS FAIL: collapsing both back to a single reason. That
    regression hides a MINING defect (a position with no decision in it) behind
    a statistics label, and the yield report then cannot say which problem the
    operator has. Measured in the first real pilot: a graded-midgame candidate
    came back with all 5 options at wp=1.0 and was reported identically to a
    position with a real 0.133 spread.
    """
    check("reason: all options win -> already decided",
          vk.sub_threshold_reason([1.0, 1.0, 1.0, 1.0, 1.0])
          == "position_already_decided")
    check("reason: all options lose -> already decided",
          vk.sub_threshold_reason([0.0, 0.02, 0.0]) == "position_already_decided")
    check("reason: genuinely close mid-board -> noise, not decided",
          vk.sub_threshold_reason([0.578, 0.6]) == "rollout_noise_below_threshold")
    check("reason: near-extreme but SPLIT is not decided",
          vk.sub_threshold_reason([1.0, 0.2]) == "rollout_noise_below_threshold")
    check("reason: empty/None degrades to the noise reason",
          vk.sub_threshold_reason([]) == "rollout_noise_below_threshold"
          and vk.sub_threshold_reason(None) == "rollout_noise_below_threshold")
    # one unlucky rollout out of n must not reclassify a won position
    check("reason: 44/45 wins still reads as decided",
          vk.sub_threshold_reason([1.0, 44.0 / 45.0]) == "position_already_decided")
    # and it flows through the real scorer, not just the helper
    scores, reason = vk.score_from_wp([1.0, 1.0, 1.0], [0.0, 0.0, 0.0],
                                      "graded-midgame")
    check("reason: score_from_wp reports it too",
          scores is None and reason == "position_already_decided", str(reason))


def test_screen_rule_is_generous():
    """Screening may waste games; it may not silently delete real puzzles.

    WHAT WOULD MAKE THIS FAIL: testing the point estimate instead of its upper
    confidence bound. That regression drops positions whose small-n spread
    merely undershot -- a false negative that removes a real puzzle from the
    suite permanently, with nothing downstream able to notice. A false positive
    only costs one position's confirmation games, so the two errors are not
    symmetric and the rule must not treat them as if they were.
    """
    def row(spread, se, reason=None):
        r = {"rollout_evidence": {"wp_spread": spread, "wp_spread_se": se}}
        if reason:
            r["unverifiable_reason"] = reason
        return r

    check("screen: clearly flat is dropped",
          not vk.survives_screen(row(0.01, 0.01), "graded-midgame"))
    check("screen: clearly strong is kept",
          vk.survives_screen(row(0.40, 0.05), "graded-midgame"))
    # 0.12 point estimate is under the 0.20 bar, but 0.12 + 1.96*0.06 = 0.238
    # -- consistent with clearing it, so it must survive to confirmation
    check("screen: borderline-but-consistent survives (upper bound, not point)",
          vk.survives_screen(row(0.12, 0.06), "graded-midgame"))
    check("screen: same point estimate with TIGHT error is dropped",
          not vk.survives_screen(row(0.12, 0.001), "graded-midgame"))
    check("screen: mulligan uses its own lower 0.15 bar",
          vk.survives_screen(row(0.15, 0.0), "mulligan")
          and not vk.survives_screen(row(0.15, 0.0), "graded-midgame"))
    check("screen: unmeasured positions are KEPT, not silently dropped",
          vk.survives_screen({"rollout_evidence": {}}, "graded-midgame")
          and vk.survives_screen({}, "graded-midgame"))
    check("screen: an already-decided position is dropped (no n fixes it)",
          not vk.survives_screen(row(0.0, 0.0, "position_already_decided"),
                                 "graded-midgame"))

    # The safe default is safe BY CONSTRUCTION, not by luck: at z=1.96 the
    # slack exceeds the 0.20 bar for every n a screen could use, so only an
    # exactly-flat position can be dropped. If this stops holding, the screen
    # has quietly started deleting borderline puzzles.
    import math
    for n in (8, 16, 25, 45):
        se_spread = math.sqrt(2) * 0.5 / math.sqrt(n)
        check("screen: z=1.96 at n=%d cannot drop a nonzero spread" % n,
              vk.survives_screen(row(1e-6, se_spread), "graded-midgame"),
              "se_spread=%.3f" % se_spread)
    check("screen: z is a real knob (lowering it drops borderline)",
          not vk.survives_screen(row(0.05, 0.05), "graded-midgame",
                                 screen_z=0.5),
          "z=0.5 should drop 0.05 +/- 0.05")


def test_screen_confirm_uses_disjoint_seeds():
    """The confirm pass must not re-read the rollouts that selected the position.

    WHAT WOULD MAKE THIS FAIL: leaving the confirm driver at j_offset=0, so
    both passes draw the same j band. That is winner's curse -- screening keeps
    positions whose sampling noise ran high, and counting those same rollouts
    as evidence inflates every spread it selected on. The bug is invisible in
    any output (the numbers look fine, they are just biased upward), so it has
    to be caught structurally, here.
    """
    seen = []

    class SpyDriver(FakeEngine):
        def _run(self, env, extra):
            if extra["FORK_MODE"] == "rollout":
                seen.append((int(extra["FORK_ROLLOUT_J_OFFSET"]),
                             int(extra["FORK_ROLLOUT_N"])))
            return FakeEngine._run(self, env, extra)

    def outcome(o, j):
        return {"winner": "A" if (o == 0) == (j % 4 != 0) else "B",
                "forced_ok": True}

    with tempfile.TemporaryDirectory() as td:
        screen = SpyDriver(PROBE_2OPT, outcome, work_dir=td, n_per_option=8,
                           j_offset=0)
        confirm = SpyDriver(PROBE_2OPT, outcome, work_dir=td, n_per_option=45,
                            j_offset=8)
        screen.rollout(fake_address(), n_per_option=8)
        confirm.rollout(fake_address(), n_per_option=45)

        offsets = set(o for o, _ in seen)
        check("screen/confirm: two distinct j offsets were used",
              offsets == set([0, 8]), str(seen))
        # the bands must not overlap: screen covers [0,8), confirm [8,53)
        screen_band = set(range(0, 8))
        confirm_band = set(range(8, 8 + 45))
        check("screen/confirm: j bands are disjoint",
              not (screen_band & confirm_band))
        check("screen/confirm: offset is recorded on the result",
              confirm.rollout(fake_address(),
                              n_per_option=45).get("j_offset") == 8)

    # The assertions above drive the drivers directly, so they pass even if
    # main() never sets the confirm offset. That is the same weak-negative-
    # control trap as "the function is still callable" -- so drive the real
    # CLI two-pass path and require that the confirm rollouts used a j band
    # disjoint from the screen's.
    calls = []

    real_cls = vk.RolloutDriver

    def _spy_factory(**kw):
        # FakeEngine.__init__ calls vk.RolloutDriver.__init__, so the real
        # class has to be in place while the spy is being constructed --
        # otherwise that call lands on the patched FUNCTION's object.__init__,
        # silently skipping initialisation.
        vk.RolloutDriver = real_cls
        try:
            d = SpyDriver(PROBE_2OPT, outcome, work_dir=kw.get("work_dir"),
                          n_per_option=kw.get("n_per_option", 16),
                          determinize=kw.get("determinize", "library"),
                          j_offset=kw.get("j_offset", 0),
                          engine_sha=kw.get("engine_sha"),
                          overlay_sha=kw.get("overlay_sha"))
        finally:
            vk.RolloutDriver = _spy_factory
        calls.append(d)
        return d

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cand = rollout_cand()
        cpath = td / "c.jsonl"
        cpath.write_text(json.dumps(cand) + "\n", encoding="utf-8")
        del seen[:]
        real = vk.RolloutDriver
        real_idx = vk.deck_name_index
        vk.RolloutDriver = _spy_factory
        # main() resolves deck paths from the repo's real index; the synthetic
        # candidate's seats are not in it, so without this the row fails with
        # missing_deck_path and never reaches a rollout at all.
        vk.deck_name_index = lambda: NAME_IDX_WITH_PATHS
        try:
            rc = vk.main([str(cpath), "--out", str(td / "o.jsonl"),
                          "--lane", "rollout", "--driver", "engine",
                          "--screen-n", "8", "--n-per-option", "45",
                          "--summary", str(td / "s.json")])
        finally:
            vk.RolloutDriver = real
            vk.deck_name_index = real_idx
        check("screen/confirm via CLI: run succeeded", rc == 0, "rc=%r" % rc)
        offsets = sorted(set(o for o, _ in seen))
        check("screen/confirm via CLI: main() ran BOTH passes",
              len(offsets) == 2, str(seen))
        check("screen/confirm via CLI: confirm band starts at screen_n, not 0",
              offsets == [0, 8], str(offsets))
        # and the two (offset, n) pairs describe non-overlapping j ranges
        bands = set()
        for off, n in seen:
            bands.add((off, off + n))
        lo = sorted(bands)
        check("screen/confirm via CLI: bands do not overlap",
              len(lo) == 2
              and all(lo[i][1] <= lo[i + 1][0] for i in range(len(lo) - 1)),
              str(lo))  # len check first: this passed vacuously on an empty
                        # `seen` before, which is exactly no evidence at all


def test_probe_failure_is_distinguishable_from_drift():
    """"probe never ran" and "position replayed elsewhere" must not look alike.

    WHAT WOULD MAKE THIS FAIL: dropping `reason` from the failed-probe evidence.
    Both cases then render as {"ok": false, "matched": false}, and they demand
    OPPOSITE responses -- drift means the answer key describes a different
    position and must be discarded, a failed probe just means re-probe. Hit for
    real while probing the 24 exhaustive keys: one row came back matched=false
    and telling which case it was required reading verify_one's source.
    """
    with tempfile.TemporaryDirectory() as td:
        # (a) probe cannot run -> exhaustive still adjudicates, caveat + reason
        drv = FakeEngine(PROBE_2OPT, lambda o, j: {}, work_dir=td,
                         rc_map={"enumerate": 5})
        out, _, verified, _, _ = run_verify_one(
            lethal_cand(), driver=drv, name_idx=NAME_IDX_WITH_PATHS)
        pe = out.get("probe") or {}
        check("probe: failure keeps the row verifiable on the exhaustive lane",
              verified and out["status"] == "verified", str(out.get("status")))
        check("probe: failure records a reason",
              pe.get("ok") is False and pe.get("reason"), str(pe))
        check("probe: failure carries the unprobed caveat",
              "unprobed_replay_address"
              in (out.get("answer_key") or {}).get("caveats", []),
              str(out.get("answer_key")))

        # (b) probe RUNS but finds a different menu -> drift, key refused
        drifted = dict(PROBE_2OPT)
        drifted["options"] = ["CAST Something Else", "PASS (do nothing)"]
        drv2 = FakeEngine(drifted, lambda o, j: {}, work_dir=td)
        out2, _, verified2, reason2, _ = run_verify_one(
            lethal_cand(), driver=drv2, name_idx=NAME_IDX_WITH_PATHS)
        check("probe: drift is REFUSED, never verified",
              not verified2 and out2.get("status") != "verified"
              and "answer_key" not in out2, str(out2.get("status")))
        check("probe: drift is named as replay_drift",
              reason2 == "replay_drift", str(reason2))
        check("probe: drift evidence says the probe DID run",
              (out2.get("probe") or {}).get("ok") is True,
              str(out2.get("probe")))


def test_suite_composition():
    """n_verified alone overstates a suite. Report the floor beside the count.

    WHAT WOULD MAKE THIS FAIL: counting duplicates as distinct, or dropping
    random_baseline. Measured near-miss this guards: an uncapped exhaustive
    pass minted 24 keys of which only 15 option-sets were distinct, and most
    positions had 2 options -- a coin flip scores ~50% on that suite. Reporting
    "24 puzzles" would have been true and misleading at once, which is exactly
    the failure mode the 88% lesson exists to prevent.
    """
    lava = ["CAST Lava Spike {R} [deals 3 damage]", "PASS (do nothing)"]
    # same decision, different reminder text -> must collapse to one position
    lava_alt = ["CAST Lava Spike {R} [different reminder]", "PASS (do nothing)"]
    four = ["CAST A", "CAST B", "CAST C", "PASS (do nothing)"]
    def meta(opts, ok):
        # category/lane/reason are what build_summary reads; suite_composition
        # ignores them, but the summary check at the end of this test does not
        return {"verified": ok, "options": opts, "category": "lethal-now",
                "lane": "exhaustive", "reason": None if ok else "x",
                "rollout_evidence": None}

    rows = ([meta(lava, True)] * 4 + [meta(lava_alt, True)]
            + [meta(four, True)] + [meta(four, False)])
    sc = vk.suite_composition(rows)

    check("suite: counts only verified rows",
          sc["n_verified"] == 6, str(sc["n_verified"]))
    check("suite: duplicates collapse (5 Lava Spike rows -> 1 position)",
          sc["n_distinct"] == 2, str(sc["n_distinct"]))
    check("suite: reminder-text differences do not fake diversity",
          sc["n_distinct"] == 2)
    check("suite: duplicate_rate is reported",
          abs(sc["duplicate_rate"] - (1 - 2 / 6.0)) < 1e-12,
          str(sc["duplicate_rate"]))
    # 5 positions at 1/2 + 1 at 1/4 -> (5*0.5 + 0.25)/6
    check("suite: random_baseline is mean(1/n_options)",
          abs(sc["random_baseline"] - (5 * 0.5 + 0.25) / 6.0) < 1e-12,
          str(sc["random_baseline"]))
    check("suite: a 2-option-heavy suite has a baseline near 0.5 (the point)",
          sc["random_baseline"] > 0.4, str(sc["random_baseline"]))
    check("suite: n_options histogram located the triviality",
          sc["n_options_histogram"] == {"2": 5, "4": 1},
          str(sc["n_options_histogram"]))
    check("suite: empty input degrades instead of dividing by zero",
          vk.suite_composition([])["random_baseline"] is None
          and vk.suite_composition([])["duplicate_rate"] is None)
    # and it must reach the summary an operator actually reads
    summary = vk.build_summary(rows, "none", 45, [])
    check("suite: composition block lands in the summary",
          (summary.get("suite_composition") or {}).get("n_distinct") == 2,
          str(summary.get("suite_composition")))


def test_power_check():
    """n must be able to certify the effect the gate itself asks for.

    WHAT WOULD MAKE THIS FAIL: dropping DEFAULT_N_PER_OPTION back below 25
    (the point where the 0.20 base threshold starts binding), or power_check
    reporting noise_bound=False for an n where the noise floor still exceeds
    the base threshold. Both regressions restore a lane that silently cannot
    mint keys: a noise-bound run does not error, it returns yield 0 and is
    indistinguishable from "no position had a real difference".
    """
    # The arithmetic, stated independently of the implementation:
    # floor = z*0.5/sqrt(n) <= base  <->  n >= (z/(2*base))**2 = (2/0.4)**2 = 25
    check("power: min n for 0.20 @ z=2 is 25",
          vk.min_n_for_threshold(0.20, 2.0) == 25,
          str(vk.min_n_for_threshold(0.20, 2.0)))
    # A LOWER bar needs MORE samples to get the noise floor underneath it,
    # so mulligan's 0.15 is the binding category, not the lenient one.
    check("power: min n for 0.15 (mulligan) @ z=2 is 45",
          vk.min_n_for_threshold(0.15, 2.0) == 45,
          str(vk.min_n_for_threshold(0.15, 2.0)))
    check("power: base<=0 is meaningless, not a crash",
          vk.min_n_for_threshold(0.0, 2.0) is None)

    # n=16 -- the OLD default -- is noise-bound, which is the whole finding.
    p16 = vk.power_check(16)
    check("power: n=16 is noise-bound", p16["noise_bound"] is True)
    check("power: n=16 noise floor 0.25 exceeds the 0.20 bar",
          abs(p16["noise_floor"] - 0.25) < 1e-12, str(p16["noise_floor"]))
    check("power: n=16 reports the n it would need",
          p16["min_n_required"] == 45, str(p16["min_n_required"]))

    # ...and the shipped default is not.
    pdef = vk.power_check(vk.DEFAULT_N_PER_OPTION)
    check("power: shipped default is noise-bound in NO category",
          pdef["noise_bound"] is False, str(pdef))
    check("power: shipped default covers the binding (mulligan) category too",
          vk.DEFAULT_N_PER_OPTION >= vk.min_n_for_threshold(0.15, 2.0),
          "DEFAULT_N_PER_OPTION=%r" % vk.DEFAULT_N_PER_OPTION)
    check("power: n=0 degrades instead of dividing by zero",
          vk.power_check(0)["noise_floor"] is None)

    # and it reaches the summary, where an operator can actually see it
    summary = vk.build_summary([], "engine", 16, [])
    check("power: block lands in the summary",
          (summary.get("power") or {}).get("noise_bound") is True,
          str(summary.get("power")))


def test_jobs_parallelism_is_order_stable():
    """--jobs must change only the wall clock, never the artifact.

    WHAT WOULD MAKE THIS FAIL: collecting results in completion order instead
    of by submission index. That regression is invisible on a fast fixture
    driver (everything finishes near-instantly, often in order) which is why
    the fake driver here sleeps by a per-row amount chosen to INVERT the
    natural order -- the last candidate finishes first. Appending as futures
    complete then produces a different file, and a verification artifact whose
    bytes depend on thread scheduling is not reproducible.
    """
    import time

    class SlowFixture(vk.FixtureRolloutDriver):
        def rollout(self, address, n_per_option=None):
            # later candidates return sooner -> completion order is reversed
            time.sleep(0.15 * (1.0 / (1 + address.get("decision_index", 0))))
            # super(), not vk.FixtureRolloutDriver -- the module attribute is
            # monkeypatched to THIS class below, so naming it here would
            # recurse into itself. super() resolves through the MRO captured
            # when the class was defined, which is the real base.
            return super(SlowFixture, self).rollout(address, n_per_option)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cands, recs = [], []
        for i in range(6):
            c = rollout_cand()
            c["decision_index"] = 40 + i
            c["source"] = dict(c["source"], decision_index=40 + i)
            addr = vk.address_of(c, {})
            # distinct WPs so a mis-ordered file differs in content, not just
            # in line order -- an order bug must be impossible to miss
            recs.append(fixture_record(addr, [0.9 - 0.01 * i, 0.2],
                                       [0.02, 0.02], c["options"]))
            cands.append(c)
        cpath = td / "cands.jsonl"
        cpath.write_text("".join(json.dumps(c) + "\n" for c in cands),
                         encoding="utf-8")
        fx = td / "fixture.jsonl"
        fx.write_text("".join(json.dumps(r) + "\n" for r in recs),
                      encoding="utf-8")

        outs = {}
        for jobs in (1, 4):
            op = td / ("out-j%d.jsonl" % jobs)
            real = vk.FixtureRolloutDriver
            vk.FixtureRolloutDriver = SlowFixture
            try:
                rc = vk.main([str(cpath), "--out", str(op), "--lane", "rollout",
                              "--driver", "fixture", "--fixture", str(fx),
                              "--jobs", str(jobs),
                              "--summary", str(td / ("s%d.json" % jobs))])
            finally:
                vk.FixtureRolloutDriver = real
            check("jobs=%d: run succeeded" % jobs, rc == 0, "rc=%r" % rc)
            outs[jobs] = op.read_text(encoding="utf-8")

        check("jobs: parallel output is byte-identical to sequential",
              outs[1] == outs[4],
              "sequential %d bytes vs parallel %d bytes"
              % (len(outs[1]), len(outs[4])))
        check("jobs: output is non-trivial (the comparison means something)",
              len(outs[1].strip().splitlines()) == 6,
              str(len(outs[1].strip().splitlines())))


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
        rs = s.get("rollout_spread") or {}
        check("cli: summary carries the wp_spread distribution",
              rs.get("n_positions_measured") == 1 and rs.get("n_decided") == 1
              and rs.get("n_no_wp") == 0
              and abs(rs["wp_spread"]["median"] - 0.7) < 1e-9
              and len(rs["wp_spread_hist"]["counts"]) == 20, str(rs))
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
