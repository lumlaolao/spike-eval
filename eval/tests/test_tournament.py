#!/usr/bin/env python3
"""Hermetic tests for eval/tournament.py — stdlib only, no network, no JVM.

Covers the pure bracket/draft/parse logic and, most importantly, asserts that a
full --dry-run makes ZERO OpenRouter (urllib) calls and launches ZERO games — the
egress-safety property a verifier flagged as the tool's highest-risk contract.

Run: python3 eval/tests/test_tournament.py   (must exit 0)
"""

import io
import signal
import subprocess
import sys
import tempfile
import time
import types
import urllib.request
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

import argparse
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import tournament as T  # noqa: E402
import run_batch  # noqa: E402


def ok(msg):
    print("[ok] " + msg)


def test_a_tournament_game_never_inherits_a_condition_from_the_shell():
    # pr-review of #347: _match_env copied the whole shell, so an exported ONE_TIER, PROPOSE_ACTIONS or SHADOW_AI reached
    # every bracket game. Every condition run_batch.py pins is dropped; the tournament's own settings still arrive.
    names = {n: "1" for n in run_batch.CONDITION_ENV_KEYS + ("SHADOW_AI",)}
    # A name added to PINNED_ENV_KEYS alone (the list the tournament strips) must still be tested here.
    assert tuple(names) == run_batch.PINNED_ENV_KEYS, (tuple(names), run_batch.PINNED_ENV_KEYS)
    a = {"path": "/a.txt", "label": "A", "model": "x/a"}
    b = {"path": "/b.txt", "label": "B", "model": "x/b"}
    args = argparse.Namespace(cap_minutes=1, forge_home=tempfile.mkdtemp(), record=False, record_fps=4)
    with mock.patch.dict(os.environ, names), mock.patch.object(T, "_engine_sha", lambda h: "e"), \
            mock.patch.object(T, "_overlay_sha", lambda h: "o"):
        env = T._match_env(a, b, 7, False, Path("/x.mp4"), Path(tempfile.mkdtemp()), args)
    assert [n for n in names if n in env] == [], [n for n in names if n in env]
    assert env["CONTROLLER_A"] == "llm" and env["GAME_SEED"] == "7"
    ok("a tournament game drops the shell's conditions")


def _group_gone(pgid, wait_s=10):
    """True once process group `pgid` is empty (a killed orphan is reaped by init, not at once). A group still alive
    after wait_s is killed, so a failing check leaves nothing running."""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    return False


def _launch_stub(fh):
    """A launcher whose background job ignores SIGINT (as one of a non-interactive bash does), like a JVM."""
    Path(fh, "run-llm-spike.sh").write_text("echo $$ > pgid\nsleep 60 & sleep 60\n")


def test_a_backstop_stops_its_own_match_and_nothing_else():
    # stop_group signals the launcher's own group only, and kills what ignores SIGINT after the grace.
    outsider = subprocess.Popen(["sleep", "60"])
    proc = subprocess.Popen(["bash", "-c", "sleep 60 & sleep 60"], start_new_session=True)
    try:
        time.sleep(0.3)
        T.stop_group(proc, grace_s=1)
        assert _group_gone(proc.pid), "the match's process group is still alive"
        assert outsider.poll() is None, "a process outside the match was signalled"
    finally:
        _group_gone(proc.pid, wait_s=0)
        outsider.kill()
        outsider.wait()
    ok("a backstop stops its own match's process group and nothing else")


def _launch_with_wait_raising(exc):
    """_launch_and_read on the stub launcher with its first wait() raising `exc`. Returns (result or the exception
    raised, the launcher's pid)."""
    real = subprocess.Popen
    started = []

    def popen(*a, **kw):
        p = real(*a, **kw)
        started.append(p)
        waits = iter([exc])

        def wait(timeout=None):
            e = next(waits, None)
            if e:
                raise e
            return real.wait(p, timeout=timeout)
        p.wait = wait
        return p

    with tempfile.TemporaryDirectory() as fh, tempfile.TemporaryDirectory() as jd:
        _launch_stub(fh)
        args = argparse.Namespace(cap_minutes=1, forge_home=fh)
        stop = T.stop_group
        with mock.patch.object(T.subprocess, "Popen", popen), mock.patch.object(T, "log"), \
                mock.patch.object(T, "stop_group", lambda p: stop(p, grace_s=1)):
            try:
                result = T._launch_and_read(dict(os.environ), Path(jd), 1, False, args)
            except BaseException as e:      # noqa: B902 (the interrupt under test)
                result = e
    return result, started[0].pid


def test_an_interrupted_match_stops_its_own_launcher_and_jvm():
    # The match runs in its own session, so a Ctrl-C reaches only this process: the launcher and JVM (which may be
    # calling a paid model) must be stopped on the way out, not left playing.
    result, pid = _launch_with_wait_raising(KeyboardInterrupt())
    assert _group_gone(pid), "the match's launcher and JVM are still running after the interrupt"
    assert isinstance(result, KeyboardInterrupt), "the interrupt is not swallowed: %r" % (result,)
    ok("an interrupted match stops its own launcher and JVM")


def test_a_match_past_its_backstop_is_stopped_and_reads_as_a_timeout():
    result, pid = _launch_with_wait_raising(subprocess.TimeoutExpired("run-llm-spike.sh", 1))
    assert _group_gone(pid), "the match's launcher and JVM are still running after the backstop"
    assert result == "timeout", result
    ok("a match past its backstop is stopped and reads as a timeout")


def test_main_installs_the_hangup_handler_and_a_second_signal_is_ignored():
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        for sig in saved:
            signal.signal(sig, signal.SIG_DFL)
        with mock.patch.object(sys, "argv", ["tournament.py", "--help"]), redirect_stdout(io.StringIO()):
            try:
                T.main()
            except SystemExit:
                pass
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler) and handler is signal.getsignal(signal.SIGHUP), "main() did not install the handler"
        try:
            handler(signal.SIGTERM, None)
            raise AssertionError("the handler did not raise SystemExit")
        except SystemExit as e:
            assert e.code == 128 + signal.SIGTERM, e.code
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN, "a second SIGTERM would interrupt the stop"
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN, "a SIGHUP would interrupt the stop"
    finally:
        for sig, h in saved.items():
            signal.signal(sig, h)
    ok("main() installs the hangup handler, and the handler ignores a second signal while the match stops")


def test_a_killed_tournament_stops_its_match():
    # SIGTERM or SIGHUP to the tournament: the match is in its own session and would play on unless the tournament
    # unwinds (exit_on_hangup, installed by main) and its finally stops the group.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        with tempfile.TemporaryDirectory() as fh, tempfile.TemporaryDirectory() as jd:
            _launch_stub(fh)
            child = subprocess.Popen([sys.executable, "-c", (
                "import argparse, os, sys; from pathlib import Path; from unittest import mock\n"
                "sys.path.insert(0, %r); import evalpath; import tournament as T\n"
                "T.exit_on_hangup(); stop = T.stop_group\n"
                "with mock.patch.object(T, 'stop_group', lambda p: stop(p, grace_s=1)):\n"
                "    T._launch_and_read(dict(os.environ), Path(%r), 1, False,\n"
                "                       argparse.Namespace(cap_minutes=1, forge_home=%r))\n")
                % (str(Path(T.__file__).parent), jd, fh)])
            pgid_file, deadline = Path(fh, "pgid"), time.time() + 20
            while not (pgid_file.is_file() and pgid_file.read_text().strip()) and time.time() < deadline:
                time.sleep(0.1)
            pgid = int(pgid_file.read_text())
            time.sleep(0.3)
            os.kill(child.pid, sig)
            time.sleep(0.3)
            os.kill(child.pid, sig)          # a second one during the stop's grace must not cut the SIGKILL short
            try:
                code = child.wait(timeout=20)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
            assert _group_gone(pgid), "signal %d left the match's launcher and JVM running" % sig
            assert code == 128 + sig, code
    ok("a tournament killed by SIGTERM or SIGHUP stops its match")


def test_snake_single_round():
    order = [0, 1, 2, 3]
    assert T._snake(order, rounds=1) == [0, 1, 2, 3]
    # two rounds reverse the second pass
    assert T._snake(order, rounds=2) == [0, 1, 2, 3, 3, 2, 1, 0]
    ok("_snake order")


def test_draw_bracket_is_permutation_and_pairs_adjacent():
    import random
    picks = [{"seed": i + 1, "label": "s%d" % (i + 1)} for i in range(8)]
    draw = T.draw_bracket(picks, random.Random(3))
    # a draw is a permutation of all 8 players (no drops, no dupes)
    assert sorted(p["label"] for p in draw) == sorted(p["label"] for p in picks)
    qf = T.qf_pairs_from_draw(draw)
    # 4 QF pairs from adjacent slots (1v2, 3v4, 5v6, 7v8); all 8 appear exactly once
    assert len(qf) == 4
    flat = [x["label"] for pair in qf for x in pair]
    assert sorted(flat) == sorted(p["label"] for p in picks)
    # deterministic for a fixed rng seed
    assert [p["label"] for p in draw] == [p["label"] for p in T.draw_bracket(picks, random.Random(3))]
    ok("draw_bracket permutation + adjacent QF pairing + seed-deterministic")


def test_draw_bracket_requires_8():
    import random
    try:
        T.draw_bracket([{"seed": 1, "label": "x"}], random.Random(0))
        assert False, "should have exited"
    except SystemExit:
        ok("draw_bracket refuses != 8 models")


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


def test_parser_number_and_alias():
    opts = [{"key": "burn"}, {"key": "elves"}, {"key": "sneak-and-show"}]
    # explicit number, in range / out of range
    assert T._parse_number_pick("blah\nPICK: 2", opts)["key"] == "elves"
    assert T._parse_number_pick("PICK: 9", opts) is None
    # anchored 'PICK: N' on its own line beats a stray 'pick 1' in prose
    assert T._parse_number_pick("i'd pick 1 in another meta\nPICK: 3", opts)["key"] == "sneak-and-show"
    # alias: last-mentioned deck name wins; only ever returns something in opts
    assert T._alias_pick("considered burn but going Sneak and Show", opts)["key"] == "sneak-and-show"
    assert T._alias_pick("no deck named here", opts) is None or \
        T._alias_pick("no deck named here", opts)["key"] in {o["key"] for o in opts}
    # resolve: explicit number in CONTENT wins over a different choice in reasoning
    assert T._resolve_pick("PICK: 1", "I really want Sneak and Show", opts)["key"] == "burn"
    # resolve: content truncated with no number -> reasoning's stated choice recovers it
    assert T._resolve_pick("Fair decks like burn are risky, so",
                           "I'll go with Sneak and Show", opts)["key"] == "sneak-and-show"
    ok("pick parser: number wins, bounds-checked, alias fallback, reasoning recovery")


def test_run_bracket_one_at_a_time():
    """--max-matches 1 plays exactly one NEW match per call, resumes, never replays,
    and produces the correct champion. run_match is stubbed (seat A always wins)."""
    import types
    picks = [{"seed": i + 1, "model": "m%d" % i, "label": "m%d" % i,
              "deck": "d%d" % i, "path": "/x", "blurb": "b"} for i in range(8)]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        args = types.SimpleNamespace(max_matches=1, dry_run=False, redraw=False,
                                     draw_only=False, record=False, retries=0,
                                     seed=5, cap_minutes=1, forge_home=Path(d))
        real_run_match = T.run_match
        calls = []

        def stub(a, b, mid, out_dir, ag, rng):
            calls.append(mid)
            return a, b, {"winner": "A", "mp4": "x"}   # seat A (first of pair) wins

        T.run_match = stub
        try:
            import random as _r
            champ = None
            counts = []
            for i in range(8):   # 7 matches; the 8th call is a harmless no-op
                state, champ = T.run_bracket(picks, out, args, _r.Random(5))
                total = sum(len(state["rounds"].get(r, {})) for r in ("QF", "SF", "F"))
                counts.append(total)
            # exactly one new match per call until all 7 are done, then it stays 7
            assert counts == [1, 2, 3, 4, 5, 6, 7, 7], counts
            assert len(calls) == 7, calls              # never launched an 8th/duplicate
            # champion is slot-0's lineage (seat A always wins): QF1->SF1->F winner = draw[0]
            draw0 = state["draw"][0]
            assert champ["label"] == draw0, (champ["label"], draw0)
        finally:
            T.run_match = real_run_match
    ok("run_bracket: one match per call, resumes, no replay, correct champion")


def _fake_forge_home(d):
    fh = Path(d) / "forge"
    fh.mkdir(parents=True, exist_ok=True)
    (fh / "run-llm-spike.sh").write_text("#!/bin/sh\n")
    return fh


def main():
    test_snake_single_round()
    test_draw_bracket_is_permutation_and_pairs_adjacent()
    test_draw_bracket_requires_8()
    test_match_id_hash_deterministic()
    test_label()
    test_read_winner_identity_keyed()
    test_mock_draft_unique_decks()
    test_parser_number_and_alias()
    test_run_bracket_one_at_a_time()
    test_dry_run_makes_no_network_or_game_calls()
    test_a_tournament_game_never_inherits_a_condition_from_the_shell()
    test_a_backstop_stops_its_own_match_and_nothing_else()
    test_an_interrupted_match_stops_its_own_launcher_and_jvm()
    test_a_match_past_its_backstop_is_stopped_and_reads_as_a_timeout()
    test_a_killed_tournament_stops_its_match()
    test_main_installs_the_hangup_handler_and_a_second_signal_is_ignored()
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
