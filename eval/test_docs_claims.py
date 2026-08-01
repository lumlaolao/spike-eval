#!/usr/bin/env python3
"""Mechanical doc-drift gate: narrow claims in the docs that have already
drifted once must now be checked by machine, not by prose review.

History that earned each check (2026-07-31):
  1. CLAUDE.md's ladder step 0 said "(21 files" while eval/test_*.py was 24.
  2. runs/overnight-3 lost 30/320 positions to a deck-name case typo
     ("ANT Storm" vs the registered "Ant Storm") that no doc or tool caught.
  3. CLAUDE.md claimed seed policy was "enforced by run_batch.py and CI" —
     no CI configuration exists anywhere in the repo.
  4. Reserved seed bands are quoted in CLAUDE.md prose and declared in
     eval/seed_policy.py; nothing kept them in sync.

Keep this file MECHANICAL — four narrow assertions. A doc gate that drifts
into style-checking becomes noise and gets deleted.

Run: python3 eval/test_docs_claims.py
"""

import glob
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_policy   # noqa: E402
import verify_keys   # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CLAUDE_MD = REPO / "CLAUDE.md"

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def _read(p):
    with open(str(p), "r", encoding="utf-8") as f:
        return f.read()


def _doc_paths():
    docs = [CLAUDE_MD, REPO / "README.md"]
    docs.extend(sorted(REPO.glob("HANDOFF*.md")))
    return [d for d in docs if d.exists()]


# 1. ladder-step-0 test-file count ------------------------------------------

def test_ladder_file_count():
    text = _read(CLAUDE_MD)
    m = re.search(r"\((\d+) files, incl\.", text)
    actual = len(glob.glob(str(REPO / "eval" / "test_*.py")))
    check("claude_md_has_file_count_claim", m is not None)
    if m:
        check("ladder_step0_file_count",
              int(m.group(1)) == actual,
              "CLAUDE.md says %s, eval/test_*.py is %d" % (m.group(1), actual))


# 2. deck display names quoted in docs resolve ------------------------------

def test_deck_name_case_variants():
    """Any case-variant of a registered MULTI-word deck display name in a doc
    is a typo waiting to become a run failure ("ANT Storm" cost 30/320
    positions in overnight-3). Single-word names (Burn, Lands, ...) are
    excluded: they are ordinary English words and would false-positive on
    prose; the runtime guard for those lives in eval/preflight.py."""
    idx = verify_keys.deck_name_index()
    multi = [n for n in idx if " " in n]
    check("deck_index_has_multiword_names", len(multi) >= 4,
          "only %d multi-word names" % len(multi))
    bad = []
    for doc in _doc_paths():
        text = _read(doc)
        for name in multi:
            for m in re.finditer(re.escape(name), text, re.IGNORECASE):
                if m.group(0) != name:
                    bad.append("%s: %r (registered: %r)"
                               % (doc.name, m.group(0), name))
    check("no_deck_name_case_variants_in_docs", not bad, "; ".join(bad[:5]))


# 3. "enforced by ... CI" requires CI config to exist -----------------------

def test_ci_claims_backed_by_config():
    ci_exists = any((REPO / p).exists()
                    for p in (".github/workflows", ".gitlab-ci.yml", ".circleci"))
    offenders = []
    for doc in _doc_paths():
        for i, line in enumerate(_read(doc).splitlines(), 1):
            if "enforc" in line.lower() and re.search(r"\bCI\b", line):
                offenders.append("%s:%d" % (doc.name, i))
    check("ci_enforcement_claims_backed",
          ci_exists or not offenders,
          "docs claim CI enforcement but no CI config exists: %s"
          % ", ".join(offenders))


# 4. reserved seed bands in CLAUDE.md match seed_policy.py ------------------

def test_reserved_bands_match():
    text = _read(CLAUDE_MD)
    for name, lo, hi in seed_policy.RESERVED_BLOCKS:
        # CLAUDE.md uses an en dash; accept ASCII hyphen too.
        pat = "%d[-–]%d" % (lo, hi)
        check("band_%s_in_claude_md" % name,
              re.search(pat, text) is not None,
              "band %d-%d (%s) not quoted in CLAUDE.md" % (lo, hi, name))
    # And no band is quoted in CLAUDE.md that the policy does not declare.
    declared = set()
    for _, lo, hi in seed_policy.RESERVED_BLOCKS:
        declared.add((lo, hi))
    quoted = set()
    for m in re.finditer(r"\b(\d{3,5})[-–](\d{3,5})\b", text):
        lo, hi = int(m.group(1)), int(m.group(2))
        # Only ranges that overlap a reserved band are seed-band claims;
        # other numeric ranges (line numbers, years) are out of scope.
        for dlo, dhi in declared:
            if lo <= dhi and hi >= dlo:
                quoted.add((lo, hi))
    undeclared = quoted - declared
    check("no_undeclared_reserved_bands", not undeclared,
          "CLAUDE.md quotes band(s) %s overlapping reserved space but not "
          "declared in seed_policy.RESERVED_BLOCKS" % sorted(undeclared))


# 5. CLAUDE.md's Java-ladder rung matches the Makefile's jtest target -------

def test_jtest_selector_matches_makefile():
    """CLAUDE.md §0b drifted once already in the very commit that added this
    gate (it still named -Dtest=LlmSpikeForkTest while make jtest ran
    ForkTest+SpikeTest). The Makefile is the single implementation; CLAUDE.md
    must quote ITS selector."""
    mk = REPO / "Makefile"
    check("makefile_exists", mk.exists())
    if not mk.exists():
        return
    m = re.search(r"-Dtest='([^']+)'", _read(mk))
    check("makefile_has_jtest_selector", m is not None)
    if m:
        check("claude_md_quotes_makefile_jtest_selector",
              m.group(1) in _read(CLAUDE_MD),
              "Makefile runs -Dtest='%s' but CLAUDE.md never quotes it"
              % m.group(1))


def main():
    test_ladder_file_count()
    test_deck_name_case_variants()
    test_ci_claims_backed_by_config()
    test_reserved_bands_match()
    test_jtest_selector_matches_makefile()
    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all docs-claims checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
