#!/usr/bin/env python3
"""Self-test for preflight.py against the real historical failure: the
"ANT Storm" case-typo that cost 30/320 positions in runs/overnight-3.

Run: python3 eval/test_preflight.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preflight  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def row(deck_a, deck_b="Burn"):
    return {"id": "x", "prompt": "p", "options": ["1) A", "2) B"],
            "source": {"deck_a": deck_a, "deck_b": deck_b}}


def run_deck_check(rows):
    """Run ONLY the deck-name check and return (failures, printed)."""
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        path = f.name
    rep = preflight.Report()
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        preflight.check_deck_names(rep, path)
    return rep.failures, buf.getvalue()


def test_case_typo_rejected_with_suggestion():
    fails, out = run_deck_check([row("ANT Storm")])
    check("ANT_Storm_rejected", any(f.startswith("deck_name:ANT Storm")
                                    for f in fails), str(fails))
    check("suggestion_names_registered_spelling", "Ant Storm" in out,
          out.strip())


def test_registered_spelling_accepted():
    fails, _ = run_deck_check([row("Ant Storm")])
    check("Ant_Storm_accepted", not fails, str(fails))


def test_unknown_name_rejected():
    fails, _ = run_deck_check([row("Totally Fake Deck")])
    check("unknown_deck_rejected", bool(fails), str(fails))


def test_empty_input_fails():
    fails, _ = run_deck_check([])
    check("empty_candidates_fail", "candidates_nonempty" in fails, str(fails))


def test_classpath_degenerate_rejected():
    """The 1232-byte AWS failure: file EXISTS but is degenerate."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("a.jar:b.jar")
        path = f.name
    rep = preflight.Report()
    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        preflight.check_classpath(rep, cp_file=path)
    check("degenerate_classpath_rejected",
          any(x.startswith("classpath") for x in rep.failures),
          str(rep.failures))


def test_classpath_healthy_accepted():
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        # Realistic entries: ~/.m2 repository paths run ~100 chars each, so a
        # healthy 103-jar classpath is ~10 KB (the measured figure is 10693).
        f.write(":".join(
            "/home/u/.m2/repository/org/example/component-%03d/1.0-SNAPSHOT/"
            "component-%03d-1.0-SNAPSHOT.jar" % (i, i) for i in range(103)))
        path = f.name
    rep = preflight.Report()
    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        preflight.check_classpath(rep, cp_file=path)
    check("healthy_classpath_accepted", not rep.failures, str(rep.failures))


def main():
    test_case_typo_rejected_with_suggestion()
    test_registered_spelling_accepted()
    test_unknown_name_rejected()
    test_empty_input_fails()
    test_classpath_degenerate_rejected()
    test_classpath_healthy_accepted()
    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all preflight tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
