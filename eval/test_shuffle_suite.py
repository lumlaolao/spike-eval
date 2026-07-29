#!/usr/bin/env python3
"""Self-test for shuffle_suite.py. Pure, offline, no engine, no network.

Run: python3 eval/test_shuffle_suite.py

WHAT WOULD MAKE THIS FAIL (per test, the code change that breaks it):
  * test_scores_follow_the_permutation: permuting the prompt without permuting
    option_scores, or permuting them by different orders. That silently
    re-keys every answer -- the suite would look fine and score nonsense.
  * test_pass_stays_last: moving the PASS slot. prompt_contract hardcodes
    pass_option_index = n_options-1 for play decisions, so a relocated PASS
    corrupts the always_pass floor and declined-slot accounting.
  * test_non_menu_text_is_untouched: rewriting anything outside the menu block.
    The board state and log must survive byte-for-byte or the position is no
    longer the position that was verified.
  * test_refuses_rather_than_passing_through: emitting an unshuffled row when
    the menu cannot be parsed or disagrees with options[]. A suite that claims
    to be balanced and is not is worse than the skew it replaced.
  * test_determinism: seeding from file order instead of (salt, id).
  * test_collapses_always_first: a shuffle that does not actually move the
    answer off slot 1 across a corpus.
"""

import json
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shuffle_suite as sh  # noqa: E402
import verify_keys as vk    # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("[ok] %s" % name)
    else:
        FAILURES.append(name)
        print("[FAIL] %s%s" % (name, (" — " + detail) if detail else ""))


PREAMBLE = (
    "You are playing Magic.\n"
    "  - Life: Elves 19 > 18\n"
    "  - Some log line with a ) paren in it\n"
    "\n"
    "DECISION: You have priority (MAIN2, your main phase). Choose ONE action:\n"
)
TRAILER = "Reason briefly, then end with EXACTLY: CHOICE: <number>"


def make_row(pid, opts, answer_idx):
    menu = "".join("  %d) %s\n" % (i + 1, o) for i, o in enumerate(opts))
    scores = [0] * len(opts)
    scores[answer_idx] = 10
    return {"id": pid, "status": "verified", "options": list(opts),
            "prompt": PREAMBLE + menu + TRAILER,
            "answer_key": {"option_scores": scores, "caveats": []}}


OPTS = ["CAST Alpha {G}", "CAST Beta {G}", "CAST Gamma {G}",
        "PASS (do nothing)"]


def test_scores_follow_the_permutation():
    row = make_row("a", OPTS, 0)          # answer is Alpha
    new, reason = sh.shuffle_row(row, random.Random(1))
    check("scores: shuffle succeeded", new is not None, str(reason))
    order = new["option_permutation"]
    check("scores: permutation is a bijection",
          sorted(order) == list(range(len(OPTS))), str(order))
    # the option that scores 10 must still be Alpha, wherever it landed
    scored = [o for o, s in zip(new["options"],
                                new["answer_key"]["option_scores"]) if s == 10]
    check("scores: the correct OPTION is unchanged, only its slot moved",
          scored == ["CAST Alpha {G}"], str(scored))
    # and the prompt's numbering must agree with the new options list
    _s, _e, texts = sh.parse_menu(new["prompt"])
    check("scores: prompt menu matches the permuted options list",
          [t.strip() for t in texts] == [str(o).strip()
                                         for o in new["options"]],
          str(texts))
    check("scores: prompt is renumbered 1..n",
          all(("  %d) " % (i + 1)) in new["prompt"]
              for i in range(len(OPTS))))


def test_pass_stays_last():
    for seed in range(12):
        new, _ = sh.shuffle_row(make_row("p%d" % seed, OPTS, 1),
                                random.Random(seed))
        check("pass-last: seed %d keeps PASS in the final slot" % seed,
              sh.is_pass_text(new["options"][-1]), str(new["options"]))
        _s, _e, texts = sh.parse_menu(new["prompt"])
        check("pass-last: seed %d prompt agrees" % seed,
              sh.is_pass_text(texts[-1]), str(texts))


def test_non_menu_text_is_untouched():
    row = make_row("b", OPTS, 2)
    new, _ = sh.shuffle_row(row, random.Random(7))
    before = row["prompt"].split("DECISION:")[0]
    after = new["prompt"].split("DECISION:")[0]
    check("faithful: everything before the menu is byte-identical",
          before == after)
    check("faithful: the CHOICE trailer survives",
          new["prompt"].endswith(TRAILER))
    check("faithful: a log line containing ') ' is not eaten as a menu entry",
          "Some log line with a ) paren in it" in new["prompt"])
    check("faithful: line count unchanged",
          len(row["prompt"].split("\n")) == len(new["prompt"].split("\n")))


def test_refuses_rather_than_passing_through():
    ok = make_row("c", OPTS, 0)

    no_menu = dict(ok, prompt="no menu here at all")
    check("refuse: unparseable menu",
          sh.shuffle_row(no_menu, random.Random(0))[1] == "menu_unparseable")

    short = dict(ok, options=OPTS[:2])
    check("refuse: options/menu length disagree",
          sh.shuffle_row(short, random.Random(0))[1] == "menu_length_mismatch")

    drifted = dict(ok, options=["CAST Something Else"] + list(OPTS[1:]))
    check("refuse: options text disagrees with the prompt",
          sh.shuffle_row(drifted, random.Random(0))[1] == "menu_text_mismatch")

    nokey = dict(ok, answer_key={"option_scores": [10, 0]})
    check("refuse: option_scores length disagrees",
          sh.shuffle_row(nokey, random.Random(0))[1]
          == "missing_or_mismatched_option_scores")

    midpass = make_row("d", ["CAST A", "PASS (do nothing)", "CAST B"], 0)
    check("refuse: PASS not in the last slot",
          sh.shuffle_row(midpass, random.Random(0))[1] == "pass_not_last")

    # and a refusal must NOT reach the output
    rows = [ok, no_menu, drifted]
    out, refusals, rep = sh.shuffle_all(rows)
    check("refuse: refused rows are dropped, not passed through",
          rep["n_shuffled"] == 1 and sum(refusals.values()) == 2,
          json.dumps(rep))
    check("refuse: every emitted row is marked shuffled",
          all(r.get("shuffled") for r in out))


def test_determinism():
    rows = [make_row("r%02d" % i, OPTS, i % 3) for i in range(8)]
    a = sh.shuffle_all(rows, salt=5)[0]
    b = sh.shuffle_all(list(reversed(rows)), salt=5)[0]
    by_id_a = dict((r["id"], r["option_permutation"]) for r in a)
    by_id_b = dict((r["id"], r["option_permutation"]) for r in b)
    check("determinism: same salt+id -> same permutation regardless of order",
          by_id_a == by_id_b)
    c = sh.shuffle_all(rows, salt=6)[0]
    by_id_c = dict((r["id"], r["option_permutation"]) for r in c)
    check("determinism: a different salt gives a different suite",
          by_id_a != by_id_c)


def test_collapses_always_first():
    """The point of the module, measured the way the scorer measures it."""
    # 40 positions whose answer is ALWAYS at slot 0 -> always_first == 1.0
    rows = [make_row("s%03d" % i, OPTS, 0) for i in range(40)]
    meta_before = [{"verified": True, "options": r["options"],
                    "option_scores": r["answer_key"]["option_scores"]}
                   for r in rows]
    before = vk.suite_composition(meta_before)["always_first_baseline"]
    out, _, _ = sh.shuffle_all(rows, salt=11)
    meta_after = [{"verified": True, "options": r["options"],
                   "option_scores": r["answer_key"]["option_scores"]}
                  for r in out]
    after = vk.suite_composition(meta_after)["always_first_baseline"]
    check("collapse: pool really was fully skewed", before == 1.0, str(before))
    # 3 movable slots -> expected 1/3; allow sampling slack at n=40
    check("collapse: always_first falls to about 1/(n-1)",
          after < 0.55, "after=%r" % after)
    check("collapse: no position was discarded to achieve it",
          len(out) == len(rows), "%d vs %d" % (len(out), len(rows)))


def test_salt_search_flattens_the_true_floor():
    """always_first at chance is NOT proof the suite is balanced.

    WHAT WOULD MAKE THIS FAIL: search_salt optimising always_first instead of
    the best-constant-index policy, or the search discarding rows to hit its
    target. Measured: a shuffled 23-position suite drew a 0.696 constant-index
    floor on its first salt -- 'always answer 1' beat two thirds of it -- and
    400 salts brought that to 0.348 while keeping every key.
    """
    # 3 options, PASS pinned -> 2 movable slots, so the achievable floor is
    # ~0.5 and a bad draw is much worse.
    opts = ["CAST A", "CAST B", "PASS (do nothing)"]
    rows = [make_row("q%02d" % i, opts, 0) for i in range(20)]

    first = sh.shuffle_all(rows, salt=1)[0]
    found = sh.search_salt(rows, range(1, 200))
    check("salt: the search returned a (salt, floor) pair", found is not None)
    salt, floor = found
    best = sh.shuffle_all(rows, salt=salt)[0]

    check("salt: it never costs a key",
          len(best) == len(rows), "%d vs %d" % (len(best), len(rows)))
    check("salt: the chosen draw is no worse than the default draw",
          floor <= sh.max_constant_index_baseline(first) + 1e-12,
          "%r vs %r" % (floor, sh.max_constant_index_baseline(first)))
    check("salt: it gets near the 2-movable-slot optimum of ~0.5",
          floor <= 0.55, "floor=%r" % floor)
    check("salt: the reported floor is what the suite actually measures",
          abs(sh.max_constant_index_baseline(best) - floor) < 1e-12)
    # the objective must be the MAX over constant policies, not slot 1 alone
    check("salt: max_constant_index_baseline >= always_first on any draw",
          all(sh.max_constant_index_baseline(sh.shuffle_all(rows, salt=s)[0])
              >= vk.suite_composition(
                  [{"verified": True, "options": r["options"],
                    "option_scores": r["answer_key"]["option_scores"]}
                   for r in sh.shuffle_all(rows, salt=s)[0]]
              )["always_first_baseline"] - 1e-12
              for s in (1, 2, 3, 7)))
    check("salt: re-running the search is deterministic",
          sh.search_salt(rows, range(1, 200)) == found)
    # and the chosen salt alone must reproduce the suite, or the report lies
    check("salt: --salt <chosen> reproduces it byte-for-byte",
          [r["option_permutation"] for r in sh.shuffle_all(rows, salt=salt)[0]]
          == [r["option_permutation"] for r in best])


def test_cli_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        rows = [make_row("c%02d" % i, OPTS, 0) for i in range(6)]
        src = td / "in.jsonl"
        src.write_text("".join(json.dumps(r) + "\n" for r in rows),
                       encoding="utf-8")
        out = td / "out.jsonl"
        rc = sh.main([str(src), "--out", str(out),
                      "--report", str(td / "rep.json")])
        check("cli: exit 0", rc == 0, "rc=%r" % rc)
        got = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        check("cli: all rows emitted", len(got) == 6, str(len(got)))
        rep = json.loads((td / "rep.json").read_text(encoding="utf-8"))
        check("cli: report records salt and counts",
              rep["n_shuffled"] == 6 and rep["keep_pass_last"] is True)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            print("--- %s ---" % name)
            fn()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
