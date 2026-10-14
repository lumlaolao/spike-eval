#!/usr/bin/env python3
"""Self-test for seed_policy.py: boundary seeds, aggregation, CLI exit codes,
plus the make_sft_data.py integration (the refusal actually fires and
--allow-reserved-seeds actually overrides it).

Run: python3 eval/test_seed_policy.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_policy as sp  # noqa: E402

FAILURES = []
HERE = Path(__file__).resolve().parent


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# --------------------------------------------------------- reserved_block

def test_boundaries():
    cases = [
        (42, None), (7, None),                       # canaries: open
        (500, None), (501, "frozen-legacy"),
        (525, "frozen-legacy"), (526, None),
        (1000, None), (8999, None),                   # dev/exploratory lane
        (19999, None), (20000, "puzzle-mining"),
        (29999, "puzzle-mining"), (30000, "certification-holdout"),
        (30099, "certification-holdout"), (30100, None),
        (-5, None), (0, None),
    ]
    for seed, expect in cases:
        got = sp.reserved_block(seed)
        check("reserved_block(%d) == %r" % (seed, expect), got == expect,
              "got %r" % got)


def test_blocks_table_shape():
    names = [n for n, _, _ in sp.RESERVED_BLOCKS]
    check("three named blocks",
          names == ["frozen-legacy", "puzzle-mining", "certification-holdout"],
          "got %s" % names)
    for name, lo, hi in sp.RESERVED_BLOCKS:
        check("%s: lo <= hi" % name, lo <= hi)
    # blocks don't overlap each other
    ranges = sorted((lo, hi) for _, lo, hi in sp.RESERVED_BLOCKS)
    for (lo1, hi1), (lo2, hi2) in zip(ranges, ranges[1:]):
        check("no overlap between adjacent reserved blocks", hi1 < lo2,
              "%d..%d vs %d..%d" % (lo1, hi1, lo2, hi2))


# ------------------------------------------------------------- check_seeds

def test_check_seeds_clean():
    check("all-clean input -> empty dict",
          sp.check_seeds([1, 42, 500, 526, 19999, 30100]) == {})
    check("empty input -> empty dict", sp.check_seeds([]) == {})


def test_check_seeds_aggregation():
    seeds = [501, 510, 510, 525, 900, 20000, 25000, 29999, 30000, 30099, 40000]
    got = sp.check_seeds(seeds)
    check("three blocks flagged", sorted(got) ==
          ["certification-holdout", "frozen-legacy", "puzzle-mining"],
          "got %s" % sorted(got))
    check("frozen-legacy offenders deduped + sorted",
          got["frozen-legacy"] == [501, 510, 525], "got %s" % got["frozen-legacy"])
    check("puzzle-mining offenders", got["puzzle-mining"] == [20000, 25000, 29999],
          "got %s" % got["puzzle-mining"])
    check("certification-holdout offenders",
          got["certification-holdout"] == [30000, 30099],
          "got %s" % got["certification-holdout"])
    check("clean seeds (900, 40000) contribute no entries",
          all(900 not in v and 40000 not in v for v in got.values()))


def test_check_seeds_single_block():
    got = sp.check_seeds([501])
    check("single offender -> single block, single seed",
          got == {"frozen-legacy": [501]}, "got %s" % got)


# --------------------------------------------------------------------- CLI

def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, str(HERE / "seed_policy.py")] + list(args),
        capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def test_cli_all_clean_exits_0():
    code, out, err = run_cli("1", "900", "40000")
    check("clean seeds -> exit 0", code == 0, "exit %d, stderr=%s" % (code, err))
    check("clean seeds print 'clean'", "clean" in out and "RESERVED" not in out)


def test_cli_any_reserved_exits_1():
    code, out, err = run_cli("501", "900", "20005")
    check("mixed clean+reserved -> exit 1", code == 1, "exit %d" % code)
    check("reserved seed labeled RESERVED", "RESERVED" in out)
    check("reserved seed names its block",
          "frozen-legacy" in out and "puzzle-mining" in out)
    check("clean seed still labeled clean", "900\tclean" in out)
    check("stderr points at the policy doc", sp.DOC_POINTER in err)


def test_cli_no_args_exits_2():
    code, out, err = run_cli()
    check("no args -> exit 2 (usage error)", code == 2, "exit %d" % code)


def test_cli_bad_arg_exits_2():
    code, out, err = run_cli("not-an-int")
    check("non-integer arg -> exit 2", code == 2, "exit %d" % code)


# ------------------------------------- make_sft_data.py integration (D-2)
#
# Proves the wiring actually fires end-to-end (subprocess, not a direct
# function call) so this test FAILS if someone later deletes the guard from
# make_sft_data.py (rather than only testing seed_policy.py in isolation,
# which would stay green even if the integration point were removed).

def _game_line(seed, winner="A"):
    return {"t": "game", "seed": seed, "swap": False,
            "deck_a": "UR Delver", "deck_b": "Burn",
            "controller_a": "llm", "controller_b": "ai",
            "smart_a": "deepseek", "smart_b": "opus",
            "fast_a": "deepseek", "fast_b": "haiku",
            "winner": winner, "turns": 9, "win_condition": "life",
            "duration_ms": 1000, "plan_a": "", "plan_b": "",
            "engine_sha": "eng", "overlay_sha": "ov", "deck_sha": "dk"}


def _decision_line(seat="UR Delver"):
    return {"t": "decision", "seat": seat, "turn": 3, "phase": "MAIN1",
            "type": "play", "n_options": 2, "choice": 0, "tier": "smart",
            "latency_ms": 10, "fallback": None, "options": ["a", "b"],
            "prompt": "state-x", "raw_reply": "CHOICE: 1",
            "finish_reason": "stop"}


def _write_win_source(d, seed, winner="A"):
    """A minimal Stage-1 'win' source: one game dir with one game the LLM
    seat won, seeded `seed`."""
    src = Path(d) / "src"
    src.mkdir()
    with open(src / ("game-%d-1000.jsonl" % seed), "w", encoding="utf-8") as f:
        f.write(json.dumps(_decision_line()) + "\n")
        f.write(json.dumps(_game_line(seed, winner)) + "\n")
    return src


# Sources are tagged :FIX, not :STAGE0, deliberately. These tests are about SEED
# GOVERNANCE; the build tag is incidental. STAGE0 pins static_reference_len=7492
# (what its runs actually saw) while the working tree's STATIC_REFERENCE has grown
# past it, so make_sft_data.py rightly REFUSES to reconstruct STAGE0 smart-tier
# system messages rather than append text those games never saw. That refusal
# wrote no output, and these tests then failed reading a README that was never
# created: a real guard breaking an unrelated test, not a bug in either. FIX
# sets static_reference_on_smart=False, so it never needs the appendix and is
# stable across that drift. Do not "fix" this back to STAGE0 — re-pin
# BUILDS["STAGE0"] only from a checkout at its commit.
def run_make_sft(*args):
    proc = subprocess.run(
        [sys.executable, str(HERE / "make_sft_data.py")] + list(args),
        capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def test_make_sft_data_refuses_reserved_seed():
    """A 'win' source whose only game is on a frozen-legacy seed (510) must
    refuse (exit 2) BEFORE writing any output."""
    with tempfile.TemporaryDirectory() as d:
        src = _write_win_source(d, 510)
        out = Path(d) / "out"
        code, _out, err = run_make_sft("%s:FIX" % src, "--out-dir", str(out))
        check("refuses on a frozen-legacy seed -> exit 2", code == 2,
              "exit %d stderr=%s" % (code, err))
        check("refusal names the reserved block", "frozen-legacy" in err, err)
        check("refusal names the offending seed", "510" in err, err)
        check("refusal points at the policy doc", sp.DOC_POINTER in err, err)
        check("no output directory written on refusal", not out.exists())


def test_make_sft_data_allow_reserved_seeds_overrides():
    """--allow-reserved-seeds lets the same source through: loud banner,
    build proceeds, reserved_seed_override is recorded in the README."""
    with tempfile.TemporaryDirectory() as d:
        src = _write_win_source(d, 510)
        out = Path(d) / "out"
        code, _out, err = run_make_sft(
            "%s:FIX" % src, "--out-dir", str(out), "--allow-reserved-seeds")
        check("override lets the build proceed -> exit 0", code == 0,
              "exit %d stderr=%s" % (code, err))
        check("loud override banner printed to stderr", "OVERRIDDEN" in err, err)
        check("output actually written", (out / "train.jsonl").exists())
        readme = (out / "README.md").read_text(encoding="utf-8")
        check("README records the contamination banner",
              "CONTAMINATED" in readme and "Seed governance" in readme)


def test_make_sft_data_clean_seed_unaffected():
    """A source on an open dev-lane seed (5000, outside every reserved
    block) builds normally with no seed-governance banner anywhere."""
    with tempfile.TemporaryDirectory() as d:
        src = _write_win_source(d, 5000)
        out = Path(d) / "out"
        code, _out, err = run_make_sft("%s:FIX" % src, "--out-dir", str(out))
        check("clean seed builds normally -> exit 0", code == 0,
              "exit %d stderr=%s" % (code, err))
        check("no reserved/override banner for a clean seed",
              "RESERVED" not in err and "OVERRIDDEN" not in err
              and "REFUSING" not in err, err)
        check("output actually written", (out / "train.jsonl").exists())


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
