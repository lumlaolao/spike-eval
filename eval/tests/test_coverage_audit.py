#!/usr/bin/env python3
"""Self-test for coverage_audit.py: synthetic JSONL fixtures + resolver checks.

Run: python3 eval/tests/test_coverage_audit.py
"""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import coverage_audit as ca  # noqa: E402

SCRIPT = str(evalpath.EVAL / "coverage_audit.py")
FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ------------------------------------------------------------- fixtures

def game_line(seed=1, swap=False, deck_sha="unknown",
              deck_a="UR Delver", deck_b="Burn"):
    return {"t": "game", "seed": seed, "swap": swap, "deck_sha": deck_sha,
            "deck_a": deck_a, "deck_b": deck_b, "winner": "A"}


def dec(seat="UR Delver", typ="play", n=2, tier="smart"):
    return {"t": "decision", "seat": seat, "type": typ,
            "n_options": n, "tier": tier, "choice": 0, "turn": 1, "phase": "MAIN1"}


def write(d, name, game, decs):
    with open(Path(d) / name, "w", encoding="utf-8") as f:
        for x in decs:
            f.write(json.dumps(x) + "\n")
        if game is not None:
            f.write(json.dumps(game) + "\n")


def mkdecks(d):
    """Two throwaway deck files; return their sha map."""
    (Path(d) / "ur-delver.txt").write_text("4 Delver of Secrets\n")
    (Path(d) / "mardu-energy.txt").write_text("4 Kroxa\n")
    shas = {}
    for f in Path(d).glob("*.txt"):
        shas[f.stem] = hashlib.sha256(f.read_bytes()).hexdigest()[:12]
    return shas


# ------------------------------------------------------------- unit tests

def test_slugify():
    check("slug ur delver", ca.slugify("UR Delver") == "ur-delver")
    check("slug grixis", ca.slugify("Grixis Reanimator") == "grixis-reanimator")
    check("slug identity", ca.slugify("Identity-A") == "identity-a")
    check("slug non-str", ca.slugify(None) == "")


def test_is_real():
    check("n=2 real", ca.is_real({"n_options": 2}))
    check("n=1 forced", not ca.is_real({"n_options": 1}))
    check("n=0 forced", not ca.is_real({"n_options": 0}))
    check("bool not real", not ca.is_real({"n_options": True}))
    check("missing forced", not ca.is_real({}))


def test_resolve_deck():
    sha_map = {"aaa111": "mardu-energy"}
    stems = {"ur-delver", "mardu-energy"}
    # name-slug wins first
    check("resolve by name", ca.resolve_deck("UR Delver", "aaa111", sha_map, stems)
          == "ur-delver")
    # identity seat -> falls back to deck_sha
    check("resolve by sha", ca.resolve_deck("Identity-A", "aaa111", sha_map, stems)
          == "mardu-energy")
    # neither -> unresolved
    check("resolve unresolved",
          ca.resolve_deck("gpt-5", "zzz", sha_map, stems) == "unresolved:gpt-5")


def test_deck_sha_map_and_tabulate():
    with tempfile.TemporaryDirectory() as decks, tempfile.TemporaryDirectory() as run:
        shas = mkdecks(decks)
        sha_map, stems = ca.load_deck_sha_map(decks)
        check("sha map has ur-delver", sha_map.get(shas["ur-delver"]) == "ur-delver")
        # a mirror run: identity seats, resolved by deck_sha to mardu-energy
        write(run, "game-1-1720000000000.jsonl",
              game_line(seed=1, deck_sha=shas["mardu-energy"],
                        deck_a="Identity-A", deck_b="Identity-B"),
              [dec(seat="Identity-A", typ="play", n=3),
               dec(seat="Identity-A", typ="surveil", n=1),   # forced
               dec(seat="Identity-B", typ="target", n=2)])
        recs, warns, nfiles = ca.load_files([run])
        tab = ca.tabulate(recs, sha_map, stems)
        ov = tab["overall"]
        check("play real counted", ov["play"]["real"] == 1)
        check("surveil forced-only", ov["surveil"]["real"] == 0
              and ov["surveil"]["forced"] == 1)
        check("mardu resolved deck", "mardu-energy" in tab["by_deck"])
        check("mardu real total", sum(v["real"] for v in
              tab["by_deck"]["mardu-energy"].values()) == 2)


def test_dedup_keeps_latest():
    with tempfile.TemporaryDirectory() as decks, tempfile.TemporaryDirectory() as run:
        ca.load_deck_sha_map(decks)
        # same (seed, swap), two files -> keep lexicographically-last
        write(run, "game-1-1000.jsonl", game_line(seed=1),
              [dec(typ="play", n=2)])
        write(run, "game-1-2000.jsonl", game_line(seed=1),
              [dec(typ="play", n=2), dec(typ="target", n=2)])
        recs, warns, nfiles = ca.load_files([run])
        check("dedup files scanned=2", nfiles == 2)
        check("dedup games used=1", len(recs) == 1)
        check("dedup kept later (2 decisions)", len(recs[0]["decisions"]) == 2)


def test_gaps():
    overall = {"play": {"total": 100, "real": 100, "forced": 0},
               "surveil": {"total": 5, "real": 0, "forced": 5},
               "target": {"total": 1, "real": 1, "forced": 0}}
    g = ca.find_gaps(overall, min_share_pct=1.0)
    check("absent has number/modes/reveal",
          all(x in g["absent"] for x in ("number", "modes", "reveal")))
    check("forced_only has surveil", "surveil" in g["forced_only"])
    check("thin has target", any(fam == "target" for fam, _, _ in g["thin"]))
    check("thin excludes play", not any(fam == "play" for fam, _, _ in g["thin"]))


# ------------------------------------------------------------- cli smoke

def test_cli():
    with tempfile.TemporaryDirectory() as decks, tempfile.TemporaryDirectory() as run:
        mkdecks(decks)
        write(run, "game-1-1000.jsonl", game_line(seed=1),
              [dec(typ="play", n=2), dec(typ="attackers", n=1)])
        jf = str(Path(run) / "out.json")
        r = subprocess.run([sys.executable, SCRIPT, run, "--decks-dir", decks,
                            "--json", jf], capture_output=True, text=True)
        check("cli exits 0", r.returncode == 0, r.stderr)
        check("cli prints overall", "OVERALL decision-type" in r.stdout, r.stdout[:200])
        blob = json.loads(Path(jf).read_text())
        check("cli json play real=1", blob["overall"]["play"]["real"] == 1)
        check("cli json attackers forced", blob["overall"]["attackers"]["forced"] == 1)


# --------------------------------------------------------------------- run

def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print()
    if FAILURES:
        print("FAILED %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
