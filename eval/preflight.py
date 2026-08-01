#!/usr/bin/env python3
"""Pre-run environment + input checks: catch tonight's failure before it costs
a night.

Every check here is a failure that ALREADY happened and surfaced only as N
opaque per-game errors hours later:

  deck names    runs/overnight-3 lost 30/320 positions to "ANT Storm" vs the
                registered "Ant Storm" — driver_reason=missing_deck_path,
                visible only per-row, never surfaced in a summary.
  classpath     a 1232-byte /tmp/forge_cp.txt (vs healthy ~10693) passes a
                bare -s test and then every game dies; the AWS port hit this
                when `mvn compile` was used instead of `install`.
  engine pin    eval results are only comparable within one engine version
                (FORGE_COMMIT); install.sh warns, so preflight warns too.
  disk          runs/ fills quietly; a full disk mid-batch orphans JSONL rows.

Usage:
    python3 eval/preflight.py CANDIDATES.jsonl [--forge-home PATH]
                              [--seeds A-B] [--skip-classpath]

Pure reads, no JVM. Exit 0 = clean, 1 = at least one FAIL. One line per
check: [ok] / [warn] / [FAIL]. Warns do not affect the exit code.

Stdlib-only, Python 3.9 (repo rule).
"""

import argparse
import difflib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_policy    # noqa: E402
from verify_keys import deck_name_index  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FORGE_CP_FILE = "/tmp/forge_cp.txt"
MIN_CP_BYTES = 5000     # ported from the AWS userdata gate: healthy is ~10693
MIN_CP_JARS = 50        # healthy classpath is ~103 jars
MIN_DISK_GB = 10


class Report(object):
    def __init__(self):
        self.failures = []
        self.warnings = []

    def ok(self, name, detail=""):
        print("[ok] %s%s" % (name, (" — " + detail) if detail else ""))

    def warn(self, name, detail):
        print("[warn] %s — %s" % (name, detail))
        self.warnings.append(name)

    def fail(self, name, detail):
        print("[FAIL] %s — %s" % (name, detail))
        self.failures.append(name)


def check_deck_names(rep, candidates_path):
    """Every deck display name in the input must resolve through
    deck_name_index(), and its decklist file must exist. On a miss, suggest
    the closest registered spelling — this is the check that turns
    "ANT Storm: 30 silent per-row failures at hour 6" into a 1-second refusal.
    """
    idx = deck_name_index()
    if not idx:
        rep.fail("deck_index", "deck_name_index() is empty — eval/holdouts.json "
                               "missing or unreadable")
        return
    names = set()
    n_rows = 0
    try:
        with open(str(candidates_path), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                n_rows += 1
                src = row.get("source") or {}
                for k in ("deck_a", "deck_b"):
                    v = src.get(k)
                    if v:
                        names.add(v)
    except OSError as e:
        rep.fail("candidates_readable", str(e))
        return
    if not n_rows:
        rep.fail("candidates_nonempty", "no JSON rows in %s" % candidates_path)
        return
    rep.ok("candidates_readable", "%d rows, %d distinct deck names"
           % (n_rows, len(names)))
    bad = False
    for name in sorted(names):
        ent = idx.get(name)
        if ent is None:
            close = difflib.get_close_matches(name, list(idx), n=1, cutoff=0.6)
            hint = (" — did you mean %r?" % close[0]) if close else ""
            rep.fail("deck_name:%s" % name,
                     "not a registered display name%s (registered: %s)"
                     % (hint, ", ".join(sorted(idx))))
            bad = True
            continue
        slug, path = ent
        if not path or not os.path.exists(path):
            rep.fail("deck_path:%s" % name,
                     "decklist file missing: %s" % path)
            bad = True
    if not bad and names:
        rep.ok("deck_names", "all %d resolve and exist on disk" % len(names))


def check_classpath(rep, cp_file=FORGE_CP_FILE):
    """A missing classpath is a loud, honest failure; a DEGENERATE one (build
    ran `compile` instead of `install`, emitting ~1232 bytes) passes -s tests
    and then kills every game. Gate on size AND jar count."""
    p = Path(cp_file)
    if not p.exists():
        rep.fail("classpath_exists",
                 "%s missing — regenerate ONCE with no games running: "
                 "cd ~/code/forge && mvn -q -o -pl forge-gui-desktop -am "
                 "dependency:build-classpath -Dmdep.outputFile=%s"
                 % (cp_file, cp_file))
        return
    size = p.stat().st_size
    if size < MIN_CP_BYTES:
        rep.fail("classpath_size",
                 "%d bytes < %d — degenerate classpath (was `mvn install` "
                 "run, not just `compile`?)" % (size, MIN_CP_BYTES))
        return
    try:
        entries = p.read_text(encoding="utf-8").strip().split(":")
    except OSError as e:
        rep.fail("classpath_readable", str(e))
        return
    jars = [e for e in entries if e.endswith(".jar")]
    if len(jars) <= MIN_CP_JARS:
        rep.fail("classpath_jars",
                 "only %d .jar entries (healthy ~103) — sibling SNAPSHOT "
                 "jars unresolved" % len(jars))
        return
    rep.ok("classpath", "%d bytes, %d jars" % (size, len(jars)))


def check_forge_pin(rep, forge_home):
    """WARN on drift, matching install.sh's convention: drift does not break a
    run, it breaks comparability of the results."""
    pin_file = REPO / "FORGE_COMMIT"
    if not pin_file.exists():
        rep.warn("forge_pin", "no FORGE_COMMIT file in repo")
        return
    pinned = pin_file.read_text(encoding="utf-8").strip()
    head_file = Path(os.path.expanduser(forge_home)) / ".git"
    if not head_file.exists():
        rep.warn("forge_pin", "%s is not a git checkout" % forge_home)
        return
    import subprocess
    try:
        cur = subprocess.check_output(
            ["git", "-C", os.path.expanduser(forge_home), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL).decode().strip()
    except (subprocess.CalledProcessError, OSError):
        rep.warn("forge_pin", "could not read HEAD of %s" % forge_home)
        return
    if cur != pinned:
        rep.warn("forge_pin",
                 "Forge checkout at %s but overlay validated against %s — "
                 "results will not be comparable" % (cur[:12], pinned[:12]))
    else:
        rep.ok("forge_pin", pinned[:12])


def check_seeds(rep, seeds_spec):
    """Informational: reserved bands are CORRECT for mining (20000-29999 is
    the mining band) and FORBIDDEN for training-data generation. Preflight
    reports band membership; the caller knows which side it is on."""
    if not seeds_spec:
        return
    try:
        lo, hi = (int(x) for x in seeds_spec.split("-", 1))
    except ValueError:
        rep.fail("seeds_spec", "--seeds wants A-B, got %r" % seeds_spec)
        return
    hits = {}
    for name, blo, bhi in seed_policy.RESERVED_BLOCKS:
        if lo <= bhi and hi >= blo:
            hits[name] = (max(lo, blo), min(hi, bhi))
    if hits:
        rep.ok("seeds", "%d-%d overlaps reserved band(s): %s"
               % (lo, hi, ", ".join("%s (%d-%d)" % (k, v[0], v[1])
                                    for k, v in sorted(hits.items()))))
    else:
        rep.ok("seeds", "%d-%d entirely outside reserved bands "
                        "(fine for training data; NOT an eval band)" % (lo, hi))


def check_disk(rep, runs_parent):
    try:
        usage = shutil.disk_usage(str(runs_parent))
    except OSError as e:
        rep.warn("disk", str(e))
        return
    free_gb = usage.free / (1024.0 ** 3)
    if free_gb < MIN_DISK_GB:
        rep.fail("disk", "%.1f GB free < %d GB minimum on %s"
                 % (free_gb, MIN_DISK_GB, runs_parent))
    else:
        rep.ok("disk", "%.0f GB free" % free_gb)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Pre-run checks: deck names, classpath, engine pin, "
                    "seeds, disk. Exit 1 on any FAIL; warns are free.")
    ap.add_argument("candidates", help="candidates/positions jsonl to check")
    ap.add_argument("--forge-home", default="~/code/forge")
    ap.add_argument("--seeds", default=None,
                    help="A-B: report reserved-band membership")
    ap.add_argument("--skip-classpath", action="store_true",
                    help="for hosts that have not built Forge yet")
    args = ap.parse_args(argv)

    rep = Report()
    check_deck_names(rep, args.candidates)
    if not args.skip_classpath:
        check_classpath(rep)
    check_forge_pin(rep, args.forge_home)
    check_seeds(rep, args.seeds)
    check_disk(rep, REPO)
    print()
    if rep.failures:
        print("PREFLIGHT FAILED: %d check(s): %s"
              % (len(rep.failures), ", ".join(rep.failures)))
        return 1
    print("preflight clean (%d warning(s))" % len(rep.warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
