#!/usr/bin/env python3
"""Hermetic tests for eval/tournament.py — stdlib only, no network, no JVM.

Covers the pure bracket/draft/parse logic and, most importantly, asserts that a
full --dry-run makes ZERO OpenRouter (urllib) calls and launches ZERO games — the
egress-safety property a verifier flagged as the tool's highest-risk contract.

Run: python3 eval/test_tournament.py   (must exit 0)
"""

import json
import sys
import tempfile
import types
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tournament as T


def ok(msg):
    print("[ok] " + msg)


def test_snake_single_round():
    order = [0, 1, 2, 3]
    assert T._snake(order, rounds=1) == [0, 1, 2, 3]
    # two rounds reverse the second pass
    assert T._snake(order, rounds=2) == [0, 1, 2, 3, 3, 2, 1, 0]
    ok("_snake order")


def test_seed_bracket_pairings():
    picks = [{"seed": i + 1, "label": "s%d" % (i + 1)} for i in range(8)]
    qf = T.seed_bracket(picks)
    # standard seeding: 1v8, 4v5, 2v7, 3v6 so seeds 1 and 2 can only meet in the final
    got = [(a["label"], b["label"]) for a, b in qf]
    assert got == [("s1", "s8"), ("s4", "s5"), ("s2", "s7"), ("s3", "s6")], got
    ok("seed_bracket standard 8-seed pairings")


def test_seed_bracket_requires_8():
    try:
        T.seed_bracket([{"seed": 1, "label": "x"}])
        assert False, "should have exited"
    except SystemExit:
        ok("seed_bracket refuses != 8 models")


def test_match_id_hash_deterministic():
    a, b = T.match_id_hash("QF-1"), T.match_id_hash("QF-1")
    assert a == b and 0 <= a < 997
    assert T.match_id_hash("QF-1") != T.match_id_hash("SF-2")
    ok("match_id_hash deterministic + varied")


def test_label():
    assert T._label("anthropic/claude-opus-4.8") == "claude-opus-4.8"
    assert T._label("openrouter:x-ai/grok-4.5") == "grok-4.5"
    assert T._label("gpt5") == "gpt5"
    ok("_label strips vendor/prefix")


def test_read_winner_identity_keyed():
    with tempfile.TemporaryDirectory() as d:
        jdir = Path(d)
        # a matching game line (seed 7, swap False) plus noise lines
        (jdir / "game-7-0.jsonl").write_text(
            '{"t":"decision"}\n'
            '{"t":"game","seed":7,"swap":false,"winner":"B","turns":40}\n')
        assert T._read_winner(jdir, 7, False) == "B"
        # wrong swap must not match
        assert T._read_winner(jdir, 7, True) == "no-line"
        # unparseable lines are skipped, not fatal
        (jdir / "game-9-0.jsonl").write_text("not json\n{bad\n")
        assert T._read_winner(jdir, 9, False) == "no-line"
    ok("_read_winner identity-keyed + robust to junk")


def test_mock_draft_unique_decks():
    import random
    pool = [{"key": "d%d" % i, "path": "/x/d%d.txt" % i, "blurb": "b"} for i in range(12)]
    models = ["m%d" % i for i in range(8)]
    picks, _ = T.draft(models, pool, random.Random(1), mock=True)
    decks = [p["deck"] for p in picks]
    assert len(decks) == 8 and len(set(decks)) == 8, decks   # all unique
    assert [p["seed"] for p in picks] == list(range(1, 9))   # seed-ordered
    ok("mock draft assigns 8 unique decks in seed order")


def test_dry_run_makes_no_network_or_game_calls():
    """The load-bearing egress test: --dry-run must not hit OpenRouter and must not
    launch a game, even with an API key present in the environment."""
    calls = {"urlopen": 0, "spike": 0}

    real_urlopen = urllib.request.urlopen

    def guard_urlopen(*a, **k):
        calls["urlopen"] += 1
        raise AssertionError("urllib.urlopen called during --dry-run (network egress!)")

    import subprocess
    real_run = subprocess.run

    def guard_run(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and any("run-llm-spike" in str(c) for c in cmd):
            calls["spike"] += 1
            raise AssertionError("launched a game during --dry-run")
        # allow local git provenance calls but neutralize them for hermeticity
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    urllib.request.urlopen = guard_urlopen
    subprocess.run = guard_run
    T.subprocess.run = guard_run  # tournament imported subprocess into its own namespace
    old_argv = sys.argv
    try:
        with tempfile.TemporaryDirectory() as d:
            sys.argv = ["tournament.py", "--models", "a", "b", "c", "d", "e", "f", "g", "h",
                        "--dry-run", "--out", str(Path(d) / "t"),
                        "--forge-home", str(_fake_forge_home(d))]
            import os
            os.environ["OPENROUTER_API_KEY"] = "sk-should-not-be-used"  # present but must be untouched
            rc = T.main()
            assert rc == 0, rc
    finally:
        urllib.request.urlopen = real_urlopen
        subprocess.run = real_run
        T.subprocess.run = real_run
        sys.argv = old_argv
    assert calls == {"urlopen": 0, "spike": 0}, calls
    ok("--dry-run: 0 network calls, 0 game launches (with API key present)")


def _fake_forge_home(d):
    fh = Path(d) / "forge"
    fh.mkdir(parents=True, exist_ok=True)
    (fh / "run-llm-spike.sh").write_text("#!/bin/sh\n")
    return fh


def main():
    test_snake_single_round()
    test_seed_bracket_pairings()
    test_seed_bracket_requires_8()
    test_match_id_hash_deterministic()
    test_label()
    test_read_winner_identity_keyed()
    test_mock_draft_unique_decks()
    test_dry_run_makes_no_network_or_game_calls()
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
