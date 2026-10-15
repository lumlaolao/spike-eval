#!/usr/bin/env python3
"""Self-test for aggregate.py: synthetic JSONL fixtures + math checks.

Run: python3 eval/test_aggregate.py
"""

import json
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aggregate  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol


# ------------------------------------------------------------- fixtures

def game_line(seed, swap, winner, ctrl_a="llm", ctrl_b="random",
              smart_a="haiku", smart_b=None):
    # Matches the exact wire schema LlmSpikeHeadless.java emits (no more, no less).
    return {"t": "game", "seed": seed, "swap": swap,
            "deck_a": "mono-red", "deck_b": "mono-red",
            "controller_a": ctrl_a, "controller_b": ctrl_b,
            "smart_a": smart_a, "smart_b": smart_b,
            "fast_a": "haiku", "fast_b": "haiku",
            "winner": winner, "turns": 9, "win_condition": "life",
            "duration_ms": 60000}


def decision_line(tier, latency, dtype="play", fallback=None):
    return {"t": "decision", "seat": "A", "turn": 3, "phase": "MAIN1",
            "type": dtype, "n_options": 4, "choice": 2, "tier": tier,
            "latency_ms": latency, "fallback": fallback}


def write_jsonl(d, name, lines):
    with open(Path(d) / name, "w") as f:
        for ln in lines:
            f.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")


def build_fixture(d):
    """Paired mirror: focal arm = identity A = llm[haiku/haiku] vs random.

    IDENTITY-KEYED contract: winner "A" means identity A won regardless of
    swap, and controller_a/smart_a always carry identity A's raw config —
    so the swapped game lines below have the SAME per-identity fields as
    the unswapped ones, only swap:true differs.

    Seeds 1-4: focal sweeps both variants. Seed 5: split. Seed 6: focal
    loses both. Seed 7: one game is a draw (pair excluded from paired
    analysis; decisive half still feeds unpaired arm stats). Plus one
    invalid game, one crashed file with no game line, and one file with
    malformed junk."""
    millis = 1000
    for seed in (1, 2, 3, 4):
        write_jsonl(d, "game-%d-%d.jsonl" % (seed, millis), [game_line(seed, False, "A")])
        write_jsonl(d, "game-%d-%d.jsonl" % (seed, millis + 1), [game_line(seed, True, "A")])
    # seed 5: focal wins unswapped, loses swapped (identity B wins)
    write_jsonl(d, "game-5-1000.jsonl", [game_line(5, False, "A")])
    write_jsonl(d, "game-5-1001.jsonl", [game_line(5, True, "B")])
    # seed 6: focal loses both
    write_jsonl(d, "game-6-1000.jsonl", [game_line(6, False, "B")])
    write_jsonl(d, "game-6-1001.jsonl", [game_line(6, True, "B")])
    # seed 7: draw in one variant -> pair excluded, decisive half kept unpaired
    write_jsonl(d, "game-7-1000.jsonl", [game_line(7, False, "draw")])
    write_jsonl(d, "game-7-1001.jsonl", [game_line(7, True, "A")])
    # seed 8: invalid (cap hit), unpaired
    write_jsonl(d, "game-8-1000.jsonl", [game_line(8, False, "invalid")])
    # seed 9: crashed — decisions but no game line
    write_jsonl(d, "game-9-1000.jsonl",
                [decision_line("smart", 8000), decision_line("smart", 12000)])
    # seed 10: malformed junk lines around a valid decisive game + decisions
    write_jsonl(d, "game-10-1000.jsonl", [
        "{not json at all",
        json.dumps(["a", "list", "not", "a", "dict"]),
        decision_line("smart", 5000, dtype="mulligan"),
        decision_line("fast", 900, dtype="blockers"),
        decision_line("auto", 1, dtype="target", fallback="cli-timeout"),
        json.dumps({"t": "decision"}),  # missing every optional field
        game_line(10, False, "A"),
    ])


# ----------------------------------------------------------------- tests

def test_wilson():
    lo, hi = aggregate.wilson_ci(8, 10)
    # Known Wilson interval for 8/10 at z=1.96: (0.4902, 0.9433)
    check("wilson 8/10 lower", approx(lo, 0.4902), "got %.4f" % lo)
    check("wilson 8/10 upper", approx(hi, 0.9433), "got %.4f" % hi)
    check("wilson 0/0 is (0,1)", aggregate.wilson_ci(0, 0) == (0.0, 1.0))
    lo0, hi0 = aggregate.wilson_ci(0, 20)
    check("wilson 0/20 lower is 0", lo0 == 0.0, "got %.4f" % lo0)
    check("wilson 0/20 upper sane", 0.0 < hi0 < 0.25, "got %.4f" % hi0)
    lo1, hi1 = aggregate.wilson_ci(20, 20)
    check("wilson 20/20 upper is 1", hi1 == 1.0)
    check("wilson symmetric", approx(lo1, 1.0 - hi0) and approx(hi1, 1.0 - lo0))


def test_p90():
    check("p90 of 1..10 is 9", aggregate.p90(list(range(1, 11))) == 9)
    check("p90 single", aggregate.p90([7]) == 7)
    check("p90 empty", aggregate.p90([]) is None)


def test_bootstrap_determinism():
    outcomes = [(1, 1)] * 6 + [(1, 0), (0, 0), (0, 1), (1, 1)]
    r1 = aggregate.paired_bootstrap(outcomes, 2000, random.Random(42))
    r2 = aggregate.paired_bootstrap(outcomes, 2000, random.Random(42))
    check("bootstrap deterministic with fixed seed", r1 == r2, "%s vs %s" % (r1, r2))
    delta, lo, hi = r1
    p = sum(a + b for a, b in outcomes) / (2.0 * len(outcomes))
    check("bootstrap point = 2p-1", approx(delta, 2 * p - 1))
    check("bootstrap CI ordered around point", lo <= delta <= hi,
          "delta=%.3f ci=[%.3f,%.3f]" % (delta, lo, hi))
    d3 = aggregate.paired_bootstrap(outcomes, 2000, random.Random(7))
    check("different rng seed still same point estimate", approx(d3[0], delta))
    # degenerate: all wins -> delta 1, zero-width CI
    dw = aggregate.paired_bootstrap([(1, 1)] * 5, 500, random.Random(1))
    check("all-wins bootstrap collapses to +1", dw == (1.0, 1.0, 1.0), str(dw))
    check("empty outcomes safe", aggregate.paired_bootstrap([], 10) == (0.0, -1.0, 1.0))


def test_fixture_summary():
    with tempfile.TemporaryDirectory() as d:
        build_fixture(d)
        s = aggregate.build_summary(Path(d), resamples=2000, bootstrap_seed=99)

        g = s["games"]
        # 17 files: 14 games for seed pairs 1-7, then seed8 invalid,
        # seed9 crashed (no game line), seed10 decisive-with-junk.
        check("total games", g["total"] == 17, str(g))
        check("decisive count", g["decisive"] == 14, str(g))
        check("draw count", g["draw"] == 1, str(g))
        check("invalid count (incl crash)", g["invalid"] == 2, str(g))
        check("no_game_line count", g["no_game_line"] == 1, str(g))
        # seat-1 wins = A-wins among swap=false + B-wins among swap=true:
        # seeds1-4 unswapped (4) + seed5 both variants (2) + seed6 swapped
        # (1) + seed10 (1) = 8 of 14 decisive.
        check("seat 1 wins", g["seat1_wins"] == 8, str(g))

        arms = s["arms"]
        check("two arms found", set(arms) == {"llm[haiku/haiku]", "random"}, str(set(arms)))
        # Focal decisive record: sweeps 8/8 + seed5 (1/2) + seed6 (0/2)
        # + seed7 swapped win (1/1) + seed10 win (1/1) = 11 of 14.
        check("focal wins", arms["llm[haiku/haiku]"]["wins"] == 11, str(arms["llm[haiku/haiku]"]))
        check("focal games", arms["llm[haiku/haiku]"]["games"] == 14, str(arms["llm[haiku/haiku]"]))
        check("random wins complement", arms["random"]["wins"] == 3
              and arms["random"]["games"] == 14, str(arms["random"]))
        lo, hi = arms["llm[haiku/haiku]"]["wilson95"]
        wl, wh = aggregate.wilson_ci(11, 14)
        check("arm CI matches wilson", approx(lo, wl) and approx(hi, wh))

        p = s["paired"]
        check("focal arm label", p["focal_arm"] == "llm[haiku/haiku]", str(p.get("focal_arm")))
        check("opponent arm label", p["opponent_arm"] == "random", str(p.get("opponent_arm")))
        check("pairs complete", p["n_pairs_complete"] == 7, str(p))
        check("pairs used", p["n_pairs_used"] == 6, str(p))
        check("pair excluded for draw", p["n_pairs_excluded_nondecisive"] == 1, str(p))
        ps = p["per_seed"]
        check("per-seed tallies", ps == {"focal_swept": 4, "split": 1,
                                         "focal_lost_both": 1}, str(ps))
        check("paired focal rate 9/12", approx(p["focal_win_rate"], 9 / 12.0))
        check("paired delta 2p-1", approx(p["delta"], 2 * 9 / 12.0 - 1))
        check("paired CI contains delta",
              p["delta_ci95"][0] <= p["delta"] <= p["delta_ci95"][1], str(p["delta_ci95"]))

        # Determinism of the whole summary path with the same bootstrap seed.
        s2 = aggregate.build_summary(Path(d), resamples=2000, bootstrap_seed=99)
        check("summary bootstrap CI reproducible",
              s2["paired"]["delta_ci95"] == p["delta_ci95"])

        dd = s["decisions"]
        # seed9: 2 smart; seed10: 2 smart (one bare), 1 fast, 1 auto... the
        # bare {"t":"decision"} has no tier -> "unknown".
        check("decision total", dd["total"] == 6, str(dd["total"]))
        check("smart calls", dd["by_tier"]["smart"]["calls"] == 3, str(dd["by_tier"]))
        check("smart mean latency",
              approx(dd["by_tier"]["smart"]["mean_latency_ms"], (8000 + 12000 + 5000) / 3.0, 0.1))
        check("auto fallback rate 100%",
              dd["by_tier"]["auto"]["fallback_rate"] == 1.0, str(dd["by_tier"].get("auto")))
        check("unknown tier tolerated", dd["by_tier"]["unknown"]["calls"] == 1)
        check("type volume", dd["by_type"].get("mulligan") == 1
              and dd["by_type"].get("play") == 2, str(dd["by_type"]))

        warns = "\n".join(s["warnings"])
        check("malformed lines warned", "malformed" in warns, warns)
        check("crash flagged as invalid", "no game line" in warns, warns)


def test_identity_keyed_no_seat_flip():
    """Core identity-keyed contract check: identity A wins EVERY game while
    the seats alternate (swap toggles). If aggregate.py wrongly flipped arm
    attribution on swap, half of A's wins would be credited to B and the
    paired delta would collapse to 0. Correct behavior: paired delta = +1.0
    strongly favoring A, while the seat-1 (first player) win rate sits at
    exactly 50% because A won from each seat equally often."""
    with tempfile.TemporaryDirectory() as d:
        for seed in range(1, 7):
            write_jsonl(d, "game-%d-1000.jsonl" % seed, [game_line(seed, False, "A")])
            write_jsonl(d, "game-%d-1001.jsonl" % seed, [game_line(seed, True, "A")])
        s = aggregate.build_summary(Path(d), resamples=500, bootstrap_seed=3)

        g = s["games"]
        check("identity fixture all decisive", g["decisive"] == 12, str(g))
        check("seat-1 win rate is 50% when dominant arm alternates seats",
              g["seat1_wins"] == 6, str(g))

        arms = s["arms"]
        check("identity A credited with every win (no swap flip)",
              arms["llm[haiku/haiku]"]["wins"] == 12 and arms["llm[haiku/haiku]"]["games"] == 12,
              str(arms))
        check("identity B credited with none",
              arms["random"]["wins"] == 0, str(arms))

        p = s["paired"]
        check("all pairs used", p["n_pairs_used"] == 6, str(p))
        check("focal swept every pair",
              p["per_seed"] == {"focal_swept": 6, "split": 0, "focal_lost_both": 0},
              str(p["per_seed"]))
        check("paired delta is +1.0 (strongly favors A)",
              approx(p["delta"], 1.0), str(p["delta"]))


def test_interleaved_and_duplicates():
    """Two games interleaved into one file (same-millisecond parallel launch)
    must both count; a rerun of the same seed/swap combo must dedupe to the
    latest file."""
    with tempfile.TemporaryDirectory() as d:
        # seed 1: both swap variants landed in ONE file. Identity-keyed:
        # winner "A" = identity A in both, config fields unchanged by swap.
        write_jsonl(d, "game-1-1000.jsonl", [
            game_line(1, False, "A"),
            game_line(1, True, "A"),
        ])
        # seed 2 swap=false ran twice: older says B won, newer says A won
        write_jsonl(d, "game-2-1000.jsonl", [game_line(2, False, "B")])
        write_jsonl(d, "game-2-2000.jsonl", [game_line(2, False, "A")])
        s = aggregate.build_summary(Path(d), resamples=200, bootstrap_seed=1)
        g = s["games"]
        check("interleaved file yields 2 games + 1 deduped", g["total"] == 3, str(g))
        # focal: seed1 both wins + seed2 newer win = 3/3
        arms = s["arms"]
        check("dedupe kept the newer rerun",
              arms["llm[haiku/haiku]"]["wins"] == 3 and arms["llm[haiku/haiku]"]["games"] == 3,
              str(arms))
        check("dedupe warned", any("duplicate game" in w for w in s["warnings"]),
              str(s["warnings"]))
        check("interleaved pair still paired", s["paired"]["n_pairs_used"] == 1,
              str(s["paired"]))


def test_decision_round_trip():
    """A decision line carrying options/prompt/raw_reply with quotes, tabs,
    newlines and non-ASCII (JSON \\uXXXX escapes) must survive serialize ->
    parse_dir -> parse unchanged, field for field."""
    opts = ["YES", "NO", 'label with "quotes"', "tab\tand\nnewline",
            "café ♠ ☃ ♠"]
    prompt = ('SNAPSHOT:\n{"hand":["Lightning Bolt"],"life":20}\n'
              'Confirm "attack with all"?\n\tunicode: ☃ é ♠ 日本語')
    raw = 'final answer: "NO"\n\tbecause ♠ is blocked\ndone é'
    dec = {"t": "decision", "seat": "Identity-A", "turn": 7, "phase": "COMBAT",
           "type": "confirm", "n_options": 5, "choice": 1, "tier": "smart",
           "latency_ms": 1234, "fallback": None,
           "options": opts, "prompt": prompt, "raw_reply": raw}
    with tempfile.TemporaryDirectory() as d:
        # json.dumps (ensure_ascii=True) emits \uXXXX for the non-ASCII chars;
        # parse_dir -> json.loads must decode them back exactly.
        write_jsonl(d, "game-1-1000.jsonl", [dec, game_line(1, False, "A")])
        _, decisions, _ = aggregate.parse_dir(Path(d))
        got = [x for x in decisions if x.get("seat") == "Identity-A"]
        check("round-trip: exactly one decision parsed", len(got) == 1, str(len(got)))
        r = got[0] if got else {}
        check("round-trip: options (quotes/tab/newline/unicode)",
              r.get("options") == opts, repr(r.get("options")))
        check("round-trip: prompt full text preserved",
              r.get("prompt") == prompt, repr(r.get("prompt")))
        check("round-trip: raw_reply full text preserved",
              r.get("raw_reply") == raw, repr(r.get("raw_reply")))
        for k in ("t", "seat", "turn", "phase", "type", "n_options", "choice",
                  "tier", "latency_ms", "fallback"):
            check("round-trip: field %s" % k, r.get(k) == dec[k],
                  "%r vs %r" % (r.get(k), dec[k]))


def test_fallback_gate():
    """Gate fires above the ceiling and passes below, counting only decisions
    of llm-controlled identities; vacuous when no llm arm exists."""
    with tempfile.TemporaryDirectory() as d:
        g = game_line(1, False, "A")  # controller_a=llm, controller_b=random
        g["deck_a"], g["deck_b"] = "Identity-A", "Identity-B"
        lines = [g]
        # 5 clean smart + 5 auto fallbacks on the llm identity -> rate 0.5
        for _ in range(5):
            lines.append({"t": "decision", "seat": "Identity-A", "tier": "smart",
                          "type": "play", "latency_ms": 100, "fallback": None})
        for _ in range(5):
            lines.append({"t": "decision", "seat": "Identity-A", "tier": "auto",
                          "type": "play", "latency_ms": 1, "fallback": "cli-timeout"})
        # A fallback on the NON-llm identity must be ignored by the gate.
        lines.append({"t": "decision", "seat": "Identity-B", "tier": "auto",
                      "type": "play", "latency_ms": 1, "fallback": "x"})
        write_jsonl(d, "game-1-1000.jsonl", lines)

        fg_hi = aggregate.build_summary(Path(d), 200, 1,
                                        max_fallback_rate=0.10)["fallback_gate"]
        check("gate fires above ceiling", fg_hi["failed"] is True, str(fg_hi))
        check("gate rate is 5/10", approx(fg_hi["fallback_rate"], 0.5), str(fg_hi))
        check("gate denom excludes non-llm identity",
              fg_hi["n_llm_decisions"] == 10, str(fg_hi))
        check("gate llm identity listed",
              fg_hi["llm_identities"] == ["Identity-A"], str(fg_hi))

        fg_lo = aggregate.build_summary(Path(d), 200, 1,
                                        max_fallback_rate=0.90)["fallback_gate"]
        check("gate passes below ceiling", fg_lo["failed"] is False, str(fg_lo))

    # Vacuous: no llm arm -> gate never fails even with fallbacks present.
    with tempfile.TemporaryDirectory() as d:
        g = game_line(2, False, "A")
        g["controller_a"], g["controller_b"] = "floor", "random"
        g["deck_a"], g["deck_b"] = "Identity-A", "Identity-B"
        write_jsonl(d, "game-2-1000.jsonl", [g,
            {"t": "decision", "seat": "Identity-A", "tier": "auto",
             "type": "play", "latency_ms": 1, "fallback": "x"}])
        fg = aggregate.build_summary(Path(d), 200, 1)["fallback_gate"]
        check("no llm arm -> gate vacuous", fg["vacuous"] is True, str(fg))
        check("vacuous gate never fails", fg["failed"] is False, str(fg))


def test_mixed_provenance():
    """Two games with differing engine_sha must trip the mixed-provenance flag
    and name engine_sha as a differing field."""
    with tempfile.TemporaryDirectory() as d:
        g1 = game_line(1, False, "A")
        g1["engine_sha"], g1["discipline"] = "aaaaaaaaaaaa", "cited"
        g2 = game_line(1, True, "A")
        g2["engine_sha"], g2["discipline"] = "bbbbbbbbbbbb", "cited"
        write_jsonl(d, "game-1-1000.jsonl", [g1])
        write_jsonl(d, "game-1-1001.jsonl", [g2])
        s = aggregate.build_summary(Path(d), 200, 1)
        check("mixed-provenance flagged", s["provenance_mixed"] is True,
              str(s["provenance"]))
        check("engine_sha named as differing",
              "engine_sha" in s["provenance"]["mixed_fields"],
              str(s["provenance"]["mixed_fields"]))
        check("shared discipline not flagged mixed",
              "discipline" not in s["provenance"]["mixed_fields"],
              str(s["provenance"]["mixed_fields"]))


def test_provenance_legacy_unknown():
    """Legacy game lines with no provenance fields resolve to the documented
    defaults ('unknown'/'unspecified'), no crash, not mixed."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [game_line(1, False, "A")])
        write_jsonl(d, "game-1-1001.jsonl", [game_line(1, True, "A")])
        p = aggregate.build_summary(Path(d), 200, 1)["provenance"]
        check("legacy provenance not mixed", p["mixed"] is False, str(p))
        check("legacy engine_sha -> unknown",
              p["fields"]["engine_sha"] == ["unknown"], str(p["fields"]["engine_sha"]))
        check("legacy deck_sha -> unknown",
              p["fields"]["deck_sha"] == ["unknown"], str(p["fields"]["deck_sha"]))
        check("legacy discipline -> unspecified",
              p["fields"]["discipline"] == ["unspecified"],
              str(p["fields"]["discipline"]))


def identity_line(seed, swap, winner, served="student-stage2-a1b2c3", **kw):
    """A game line carrying the resolved-identity fields (2026-07-26 schema)."""
    g = game_line(seed, swap, winner, smart_a="openrouter:default_model")
    g.update({"discipline": "cited",
              "served_smart_a": served, "served_fast_a": served,
              "served_smart_b": "unknown", "served_fast_b": "unknown",
              "openrouter_base_url": "http://127.0.0.1:8080/v1",
              "checkpoint_sha": "a1b2c3d4e5f6", "model_cfg_sha": "builtin",
              "temperature_a": "0.7", "temperature_b": "n/a",
              "seed_bank": "eval-holdout"})
    g.update(kw)
    return g


def test_model_identity_attributable():
    """A cited run whose endpoint names what answered passes the gate — and the
    served name, not the alias spec, is what identifies the checkpoint."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [identity_line(1, False, "A")])
        write_jsonl(d, "game-1-1001.jsonl", [identity_line(1, True, "A")])
        m = aggregate.build_summary(Path(d), 200, 1)["model_identity"]
        check("attributable run is attributable", m["attributable"] is True, str(m))
        check("attributable run does not fail the gate", m["failed"] is False, str(m))
        check("served name recorded",
              m["served"]["served_smart_a"] == ["student-stage2-a1b2c3"],
              str(m["served"]))
        check("configured alias still reported",
              m["configured"]["smart_a"] == ["openrouter:default_model"],
              str(m["configured"]))
        check("checkpoint sha recorded",
              m["checkpoint_sha"] == ["a1b2c3d4e5f6"], str(m["checkpoint_sha"]))


def test_model_identity_gate_fails_cited_unattributable():
    """The stage1-vs-stage2 failure: the fields are present, the endpoint named
    nothing, and the run claims to be citable. That must fail."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl",
                    [identity_line(1, False, "A", served="unknown")])
        write_jsonl(d, "game-1-1001.jsonl",
                    [identity_line(1, True, "A", served="unknown")])
        m = aggregate.build_summary(Path(d), 200, 1)["model_identity"]
        check("cited+unresolved fails the gate", m["failed"] is True, str(m))
        check("unresolved fields named",
              set(m["unresolved_fields"]) == {"served_smart_a", "served_fast_a"},
              str(m["unresolved_fields"]))


def test_model_identity_exploratory_warns_only():
    """Same missing identity, exploratory discipline: reported, not gated. The
    gate is about what may be CITED, not about blocking iteration."""
    with tempfile.TemporaryDirectory() as d:
        for i, swap in enumerate((False, True)):
            write_jsonl(d, "game-1-100%d.jsonl" % i,
                        [identity_line(1, swap, "A", served="unknown",
                                       discipline="exploratory")])
        m = aggregate.build_summary(Path(d), 200, 1)["model_identity"]
        check("exploratory unresolved does not fail", m["failed"] is False, str(m))
        check("exploratory unresolved still reported",
              bool(m["unresolved_fields"]), str(m))


def test_model_identity_legacy_not_gated():
    """A dir written before the fields existed is unattributable but excused:
    adding the gate must not retroactively condemn every older run."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl",
                    [dict(game_line(1, False, "A"), discipline="cited")])
        write_jsonl(d, "game-1-1001.jsonl",
                    [dict(game_line(1, True, "A"), discipline="cited")])
        m = aggregate.build_summary(Path(d), 200, 1)["model_identity"]
        check("legacy dir flagged legacy", m["legacy"] is True, str(m))
        check("legacy dir not attributable", m["attributable"] is False, str(m))
        check("legacy dir does not fail the gate", m["failed"] is False, str(m))


def test_model_identity_vacuous_without_llm_arm():
    """floor-vs-random has no model to name; demanding one would fail every
    baseline arm."""
    with tempfile.TemporaryDirectory() as d:
        for i, swap in enumerate((False, True)):
            write_jsonl(d, "game-1-100%d.jsonl" % i,
                        [dict(game_line(1, swap, "A", ctrl_a="floor", ctrl_b="random"),
                              discipline="cited", served_smart_a="unknown")])
        m = aggregate.build_summary(Path(d), 200, 1)["model_identity"]
        check("no-llm-arm identity gate is vacuous", m["vacuous"] is True, str(m))
        check("vacuous identity gate does not fail", m["failed"] is False, str(m))


def test_mixed_checkpoint_is_mixed_provenance():
    """Two checkpoints in one directory is a batch that cannot be read as one
    result — the existing MIXED-PROVENANCE machinery now catches it."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [identity_line(1, False, "A")])
        write_jsonl(d, "game-1-1001.jsonl",
                    [identity_line(1, True, "A", served="student-stage1-999999",
                                   checkpoint_sha="ffffffffffff")])
        p = aggregate.build_summary(Path(d), 200, 1)["provenance"]
        check("mixed checkpoints flagged", p["mixed"] is True, str(p["mixed_fields"]))
        check("checkpoint_sha named as the differing field",
              "checkpoint_sha" in p["mixed_fields"], str(p["mixed_fields"]))


def test_harness_decided_gate():
    """Forced picks are not the harness deciding; a TAKEN or LOST row is, and the gate fires on it."""
    with tempfile.TemporaryDirectory() as d:
        g = game_line(3, False, "A")
        g["deck_a"], g["deck_b"] = "Identity-A", "Identity-B"
        lines = [g]
        for _ in range(96):
            lines.append({"t": "decision", "seat": "Identity-A", "tier": "smart", "type": "play", "fallback": None})
        lines.append({"t": "decision", "seat": "Identity-A", "tier": "auto", "type": "targets",
                      "fallback": "targets(forced)"})                      # one legal target: forced
        lines.append({"t": "decision", "seat": "Identity-A", "tier": "smart", "type": "play",
                      "fallback": "play(unparsed)->PASS"})                  # asked, answer not used: LOST
        lines.append({"t": "decision", "seat": "Identity-A", "tier": "auto", "type": "attackers",
                      "fallback": "attackers(forced)"})                    # one candidate in a subset: TAKEN
        lines.append({"t": "decision", "seat": "Identity-A", "tier": "auto", "type": "play",
                      "fallback": "play(turn-cap)->PASS"})                  # a harness rule: TAKEN
        write_jsonl(d, "game-3-1000.jsonl", lines)
        fg = aggregate.build_summary(Path(d), 200, 1)["fallback_gate"]
        check("harness-decided counts TAKEN and LOST, not forced", fg["n_harness_decided"] == 3, str(fg))
        check("3 of 100 is over the 2% ceiling", fg["harness_failed"] is True, str(fg))
        check("the fallback gate itself passes", fg["failed"] is False, str(fg))


def test_strict_score_counts_every_started_game():
    """A game that never finished is not dropped from the strict score (a stall cannot erase a loss)."""
    games = [{"winner": "A", "controller_a": "llm", "controller_b": "ai"},
             {"winner": "draw", "controller_a": "llm", "controller_b": "ai"},
             {"winner": "invalid", "controller_a": "llm", "controller_b": "ai"},
             {"winner": "B", "controller_a": "llm", "controller_b": "ai"}]
    arms = aggregate.summarize_arms(games)
    a = arms[aggregate.arm_label(games[0], "a")]
    check("decisive-only win rate is 1/2", approx(a["win_rate"], 0.5), str(a))
    check("strict score is (1 + 0.5) / 4", approx(a["strict_score"], 0.375), str(a))


def main():
    test_wilson()
    test_p90()
    test_bootstrap_determinism()
    test_fixture_summary()
    test_identity_keyed_no_seat_flip()
    test_interleaved_and_duplicates()
    test_decision_round_trip()
    test_fallback_gate()
    test_harness_decided_gate()
    test_strict_score_counts_every_started_game()
    test_mixed_provenance()
    test_provenance_legacy_unknown()
    test_model_identity_attributable()
    test_model_identity_gate_fails_cited_unattributable()
    test_model_identity_exploratory_warns_only()
    test_model_identity_legacy_not_gated()
    test_model_identity_vacuous_without_llm_arm()
    test_mixed_checkpoint_is_mixed_provenance()
    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
