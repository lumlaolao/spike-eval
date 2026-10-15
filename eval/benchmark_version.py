#!/usr/bin/env python3
"""Benchmark version id: one hash over everything that decides what a score means.

Two scores are comparable only when they were measured under the same engine, harness, decks, seeds,
tools, observation and anchors (eval/research/2026-10-15-scoring-design.md section 0.4,
eval/BENCHMARK-RESEARCH.md point 9). This module names that whole condition with one id, `bv1-<12
hex>`, so a panel or rating report can carry it and two reports from different versions refuse to
pool.

What the id hashes:

  engine         FORGE_COMMIT (and every game line's engine_sha must equal it)
  overlay        the game lines' overlay_sha; without games, the same hash run_batch.py computes
                 (sha256[:12] of LlmSpikeGui.java + LlmSpikeHeadless.java) over this checkout's overlay
  deck_pool      sha12 of each panel deck file; a file that no longer matches its pin in the panels
                 file is an error, not a new version
  seed_registry  sha12 of eval/seed_registry.json
  panels         sha12 of the panels file (eval/suites/panels-v1.json)
  puzzle_suites  sha12 of each frozen puzzle suite (eval/suites/puzzles-*.json); none exist yet
  tools          sha12 of eval/tooluse/tools.json (the native tool definitions)
  anchors        the panels file's `anchors` block (Forge AI = 1500, the rating prior, WR_AImirror)
  observed       context_profile, context_sha and harness_epoch as the game lines recorded them

What it cannot see: the overlay_sha stamp covers only the two spike files, so a change to another
overlay class (NativeTools.java, SeatObservation.java) moves the id only through harness_epoch,
context_sha or the tool definitions. Model identity is not part of the version: it is what a score
is ABOUT, not the condition it was measured under.

Usage:
  python3 eval/benchmark_version.py                       # the id of this checkout, no games
  python3 eval/benchmark_version.py --games DIR [DIR...]  # stamped with what the games recorded
  python3 eval/benchmark_version.py --check-pool R1.json R2.json   # exit 1 unless one version
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PANELS = REPO_ROOT / "eval" / "suites" / "panels-v1.json"
SCHEME = "bv1"
OVERLAY_SPIKE = Path("forge-overlay/forge-gui-desktop/src/main/java/forge/spike")
# Must match run_batch.SPIKE_FILES (order included): the game line's overlay_sha is that hash.
OVERLAY_FILES = ("LlmSpikeGui.java", "LlmSpikeHeadless.java")
PUZZLE_SUITE_GLOB = "puzzles-*.json"
# Game-line fields that describe the observation and the harness interface. Each must hold one value
# across every game a report covers.
OBSERVED_FIELDS = ("context_profile", "context_sha", "harness_epoch")
NOT_OBSERVED = "(no games)"


class VersionError(Exception):
    """The inputs do not describe one benchmark version."""


def _sha12(b):
    return hashlib.sha256(b).hexdigest()[:12]


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _file_sha(path):
    try:
        return _sha12(Path(path).read_bytes())
    except OSError as e:
        raise VersionError("unreadable %s: %s" % (path, e))


def load_panels(path=None):
    p = Path(path) if path else DEFAULT_PANELS
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise VersionError("unreadable panels file %s: %s" % (p, e))


def overlay_sha_of_checkout(repo=REPO_ROOT):
    blobs = []
    for name in OVERLAY_FILES:
        try:
            blobs.append((Path(repo) / OVERLAY_SPIKE / name).read_bytes())
        except OSError:
            return "unknown"
    return _sha12(b"".join(blobs))


def deck_pool_shas(panels, repo=REPO_ROOT):
    """{deck name: sha12}, verified against the panels file's pins."""
    out, drift = {}, []
    for d in panels.get("deck_pool") or []:
        actual = _file_sha(Path(repo) / d["path"])
        if actual != d.get("sha12"):
            drift.append("%s: file %s, pinned %s" % (d["name"], actual, d.get("sha12")))
        out[d["name"]] = actual
    if drift:
        raise VersionError("deck file(s) changed since the panels file was frozen (a new deck pool is a "
                           "new panels version, not a silent edit): " + "; ".join(drift))
    return out


def single_value(games, field):
    """The one value `field` has across `games`; VersionError when it has several."""
    vals = sorted({_canon(g.get(field, "(absent)")) for g in games})
    if len(vals) > 1:
        raise VersionError("games disagree on %s: %s; they belong to different benchmark versions and "
                           "do not pool" % (field, ", ".join(vals)))
    return json.loads(vals[0])


def components(repo=REPO_ROOT, panels_path=None, games=None):
    """Every input to the version id, as a dict. `games` are game-line dicts (crashed placeholders,
    which carry no fields, are ignored)."""
    repo = Path(repo)
    panels_path = Path(panels_path) if panels_path else repo / "eval" / "suites" / "panels-v1.json"
    panels = load_panels(panels_path)
    try:
        engine = (repo / "FORGE_COMMIT").read_text(encoding="utf-8").strip()
    except OSError as e:
        raise VersionError("unreadable FORGE_COMMIT: %s" % e)
    real = [g for g in (games or []) if not g.get("_no_game_line")]
    comp = {
        "scheme": SCHEME,
        "engine": engine,
        "deck_pool": deck_pool_shas(panels, repo),
        "seed_registry": _file_sha(repo / "eval" / "seed_registry.json"),
        "panels": _file_sha(panels_path),
        "puzzle_suites": {p.name: _file_sha(p) for p in sorted((repo / "eval" / "suites").glob(PUZZLE_SUITE_GLOB))},
        "tools": _file_sha(repo / "eval" / "tooluse" / "tools.json"),
        "anchors": panels.get("anchors") or {},
    }
    if real:
        recorded = single_value(real, "engine_sha")
        if recorded != engine:
            raise VersionError("games were played on engine %s but FORGE_COMMIT is %s" % (recorded, engine))
        comp["overlay"] = single_value(real, "overlay_sha")
        comp["observed"] = {f: single_value(real, f) for f in OBSERVED_FIELDS}
    else:
        comp["overlay"] = overlay_sha_of_checkout(repo)
        comp["observed"] = {f: NOT_OBSERVED for f in OBSERVED_FIELDS}
    return comp


def version_id(comp):
    return "%s-%s" % (SCHEME, _sha12(_canon(comp).encode("utf-8")))


def stamp(repo=REPO_ROOT, panels_path=None, games=None):
    """{'id', 'components'} for a report to carry."""
    comp = components(repo, panels_path, games)
    return {"id": version_id(comp), "components": comp}


def differing_components(a, b):
    """Top-level component names whose values differ between two stamps."""
    ca, cb = a.get("components") or {}, b.get("components") or {}
    return sorted(k for k in set(ca) | set(cb) if _canon(ca.get(k)) != _canon(cb.get(k)))


def check_poolable(stamps):
    """Raise VersionError unless every stamp names one version. A report without a stamp cannot be
    pooled at all: nothing says what it measured."""
    stamps = list(stamps)
    if any(not s or not s.get("id") for s in stamps):
        raise VersionError("a report carries no benchmark version; it cannot be pooled")
    first = stamps[0]
    for s in stamps[1:]:
        if s["id"] != first["id"]:
            raise VersionError("benchmark versions differ (%s vs %s; components: %s); reports from "
                               "different versions never pool"
                               % (first["id"], s["id"], ", ".join(differing_components(first, s))))
    return first["id"]


def games_of_dirs(dirs):
    import aggregate                      # lazy: this module stays importable on its own
    out = []
    for d in dirs:
        for e in aggregate.parse_dir_full(d)["experiments"]:
            out.extend(e["games"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", nargs="+", metavar="DIR", help="run directories whose game lines to stamp from")
    ap.add_argument("--panels", help="panels file (default eval/suites/panels-v1.json)")
    ap.add_argument("--check-pool", nargs="+", metavar="REPORT",
                    help="JSON reports (panel_score.py / rating.py --json); exit 1 unless they share one version")
    a = ap.parse_args(argv)
    try:
        if a.check_pool:
            stamps = []
            for p in a.check_pool:
                stamps.append(json.loads(Path(p).read_text(encoding="utf-8")).get("benchmark_version"))
            print("poolable: %s" % check_poolable(stamps))
            return 0
        s = stamp(panels_path=a.panels, games=games_of_dirs(a.games) if a.games else None)
    except VersionError as e:
        print("benchmark_version: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(s, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
