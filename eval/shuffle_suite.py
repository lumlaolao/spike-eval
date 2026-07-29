#!/usr/bin/env python3
"""Remove positional skew at the source: permute each position's option menu.

The problem
-----------
The engine emits menu options in its own order and the strong play tends to land
at slot 1, so a model that replies "1" every time scored **0.92** on the
exhaustive lane and **0.70** on the combined 46-key suite, against a 0.35
random baseline. A benchmark a constant beats is not measuring play.

`select_suite.py` answers this by discarding positions until the aggregate is
balanced. That works, but it is a permanent tax: it threw away 18 of 30 keys,
and a rollout key costs ~153 full games to mint. Paying 2.5x per usable key
forever, on a suite that needs to reach hundreds of positions, is the wrong
trade. Selection remains the right tool for the always_PASS floor (below).

What this does instead
----------------------
Rewrites the numbered menu inside `prompt` into a seeded random order and
applies the same permutation to `answer_key.option_scores` and `options`. The
strong play then lands uniformly across slots, so "always answer 1" collapses to
chance BY CONSTRUCTION and every verified key stays usable.

Why this is a data transform and NOT a contract change
------------------------------------------------------
`prompt_contract.render` passes `row["prompt"]` through verbatim, so the menu
the model sees is a property of the DATA, not of the contract code. Rewriting
the row leaves `prompt_contract.py` — and therefore `contract_sha` — untouched,
and artifacts stay comparable. That is the whole reason this is safe to do.

**PASS stays pinned to the last slot.** Only the non-PASS options are permuted.
`prompt_contract` hardcodes `pass_option_index = n_options - 1` for `play`
decisions, so moving PASS would silently corrupt the always_pass floor and the
declined-slot accounting. Keeping it last means:
  * always_first drops to ~1/(n-1) on positions whose answer is not PASS — the
    skew this module exists to remove;
  * always_pass is UNCHANGED, and still has to be balanced by `select_suite.py`.
Use both: shuffle first, then select on the PASS floor only.

Faithfulness
------------
Menu order is presentation, not game state: the board, the log, life totals and
every other line of the prompt are copied byte-for-byte. Only the ordering of a
list the engine itself ordered arbitrarily changes.

Refusing rather than guessing
-----------------------------
If the menu cannot be parsed, or the parsed entries disagree with `options`, the
row is REFUSED and reported — never passed through unchanged. Silently emitting
an unshuffled row would produce a suite that claims to be balanced and is not,
which is worse than the skew it was meant to fix.
"""

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: A menu line: two leading spaces, 1-based number, ") ", then the option text.
#: Anchored to the line start so option text containing ") " cannot match.
MENU_LINE = re.compile(r"^  (\d+)\) (.*)$")

#: The menu block opens on the DECISION line and closes on the CHOICE trailer.
CHOICE_TRAILER = "Reason briefly, then end with EXACTLY:"

DEFAULT_SALT = 20260729


def is_pass_text(text):
    return str(text).strip().upper().startswith("PASS")


def parse_menu(prompt):
    """-> (start_line_idx, end_line_idx_exclusive, [option_text, ...]) or None.

    Requires the numbers to be exactly 1..n in order; anything else means this
    is not the menu shape this module knows how to rewrite, and it declines.
    """
    if not isinstance(prompt, str):
        return None
    lines = prompt.split("\n")
    start = end = None
    texts = []
    for i, line in enumerate(lines):
        m = MENU_LINE.match(line)
        if m:
            if start is None:
                start = i
            elif i != end:
                return None            # non-contiguous menu block
            texts.append(m.group(2))
            end = i + 1
            if int(m.group(1)) != len(texts):
                return None            # numbering is not 1..n in order
    if start is None or len(texts) < 2:
        return None
    return start, end, texts


def permute_prompt(prompt, order, start, end, texts):
    """Rewrite lines[start:end] as the menu in `order`, renumbered 1..n."""
    lines = prompt.split("\n")
    new = ["  %d) %s" % (k + 1, texts[order[k]]) for k in range(len(order))]
    return "\n".join(lines[:start] + new + lines[end:])


def shuffle_row(row, rng, keep_pass_last=True):
    """-> (new_row, None) on success, or (None, reason) on refusal.

    `order[k] = j` means new slot k holds what was slot j.
    """
    prompt = row.get("prompt")
    parsed = parse_menu(prompt)
    if parsed is None:
        return None, "menu_unparseable"
    start, end, texts = parsed

    options = row.get("options") or []
    if len(options) != len(texts):
        return None, "menu_length_mismatch"
    # The prompt is the ground truth the model reads; if `options` disagrees
    # with it, permuting scores by option index would mis-key the answer.
    for a, b in zip(options, texts):
        if str(a).strip() != str(b).strip():
            return None, "menu_text_mismatch"

    scores = (row.get("answer_key") or {}).get("option_scores")
    if not isinstance(scores, list) or len(scores) != len(texts):
        return None, "missing_or_mismatched_option_scores"

    n = len(texts)
    idx = list(range(n))
    if keep_pass_last:
        pass_idx = [i for i in idx if is_pass_text(texts[i])]
        if len(pass_idx) > 1:
            return None, "multiple_pass_options"
        if pass_idx and pass_idx[0] != n - 1:
            # PASS is expected last for play decisions; if the corpus ever
            # violates that, refuse rather than silently relocating it.
            return None, "pass_not_last"
        movable = [i for i in idx if not is_pass_text(texts[i])]
        rng.shuffle(movable)
        order = movable + [i for i in idx if is_pass_text(texts[i])]
    else:
        order = list(idx)
        rng.shuffle(order)

    out = dict(row)
    out["prompt"] = permute_prompt(prompt, order, start, end, texts)
    out["options"] = [options[j] for j in order]
    ak = dict(row.get("answer_key") or {})
    ak["option_scores"] = [scores[j] for j in order]
    out["answer_key"] = ak
    # Additive provenance: the permutation is what makes this auditable and
    # reproducible. order[k]=j means new slot k came from old slot j.
    out["option_permutation"] = order
    out["shuffled"] = True
    return out, None


def shuffle_all(rows, salt=DEFAULT_SALT, keep_pass_last=True):
    """-> (shuffled_rows, refusals dict, report). Deterministic given salt:
    each row is seeded from (salt, its id), so order does not depend on file
    order and a single row can be reproduced in isolation."""
    out, refusals = [], {}
    for row in rows:
        if row.get("status") != "verified":
            continue
        rng = random.Random("%s|%s" % (salt, row.get("id")))
        new, reason = shuffle_row(row, rng, keep_pass_last)
        if new is None:
            refusals[reason] = refusals.get(reason, 0) + 1
            continue
        out.append(new)
    report = {"schema": "suite-shuffle.v1", "salt": salt,
              "keep_pass_last": bool(keep_pass_last),
              "n_in": len(rows), "n_shuffled": len(out),
              "refusals": refusals}
    return out, refusals, report


def max_constant_index_baseline(rows):
    """Best score any 'always answer j' policy gets on `rows`, in [0,1].

    This -- not always_first -- is the floor a shuffled suite has to push down.
    A uniform permutation makes each slot equally likely PER ROW, but at suite
    sizes in the tens the multinomial lands unevenly: the first shuffled
    17-position suite drew answers {slot1:5, slot2:8, slot3:3, slot4:1}, so
    "always answer 2" scored 0.47 while always_first read 0.29.
    """
    hits, n = {}, 0
    for r in rows:
        sc = (r.get("answer_key") or {}).get("option_scores")
        if not isinstance(sc, list) or not sc:
            continue
        n += 1
        for j, s in enumerate(sc):
            if s == 10:
                hits[j] = hits.get(j, 0) + 1
    if not n or not hits:
        return None
    return max(hits.values()) / float(n)


def search_salt(rows, salts, keep_pass_last=True):
    """Pick the salt whose draw flattens the answer-slot distribution best.

    Why this is not cheating: the menu order is arbitrary presentation that the
    engine happened to choose, and every candidate salt is a legitimate uniform
    permutation of it. Selecting among them changes only WHERE the answer is
    printed -- never which positions are in the suite, never which option is
    correct, and nothing about any model. It is what a well-built
    multiple-choice exam does, and unlike `select_suite`'s discard-to-balance
    it costs ZERO verified keys.

    Deterministic and auditable: the chosen salt is recorded in the report, so
    `--salt <chosen>` alone reproduces the suite byte-for-byte.
    """
    best = None
    for salt in salts:
        out, _refusals, _rep = shuffle_all(rows, salt, keep_pass_last)
        score = max_constant_index_baseline(out)
        if score is None:
            continue
        # ties -> lowest salt, so the search is order-independent
        if best is None or score < best[1]:
            best = (salt, score)
    return best


def _read(paths):
    rows = []
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print("cannot read %s: %s" % (p, exc), file=sys.stderr)
            continue
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Permute each position's option menu so 'always answer 1' "
                    "collapses to chance. Data transform only -- prompt_contract "
                    "and contract_sha are untouched.")
    ap.add_argument("positions", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--salt", type=int, default=DEFAULT_SALT,
                    help="permutation salt; same salt + same ids -> same suite")
    ap.add_argument("--balance-salts", type=int, default=0, metavar="N",
                    help="try N salts starting at --salt and keep the one that "
                         "minimises the best-constant-index floor. Costs no "
                         "keys (unlike select_suite's discard-to-balance); the "
                         "winning salt is recorded so the result is "
                         "reproducible with --salt alone.")
    ap.add_argument("--shuffle-pass-too", action="store_true",
                    help="also move the PASS slot. NOT recommended: "
                         "prompt_contract hardcodes pass_option_index to the "
                         "last slot for play decisions, so this corrupts the "
                         "always_pass floor and declined-slot accounting.")
    args = ap.parse_args(argv)

    rows = _read(args.positions)
    keep_pass_last = not args.shuffle_pass_too

    salt, search = args.salt, None
    if args.balance_salts > 1:
        candidates = list(range(args.salt, args.salt + args.balance_salts))
        found = search_salt(rows, candidates, keep_pass_last)
        if found is not None:
            salt, floor = found
            base = max_constant_index_baseline(
                shuffle_all(rows, args.salt, keep_pass_last)[0])
            search = {"n_salts": len(candidates), "salt_lo": candidates[0],
                      "salt_hi": candidates[-1], "chosen_salt": salt,
                      "floor_at_chosen": floor, "floor_at_first_salt": base,
                      "objective": "max_constant_index_baseline"}

    shuffled, refusals, report = shuffle_all(rows, salt, keep_pass_last)
    report["salt_search"] = search
    report["max_constant_index_baseline"] = \
        max_constant_index_baseline(shuffled)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out), "w", encoding="utf-8") as f:
        for r in shuffled:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    rp = Path(args.report) if args.report else out.parent / "shuffle.json"
    with open(str(rp), "w", encoding="utf-8") as f:
        f.write(json.dumps(report, sort_keys=True, indent=2) + "\n")

    print("shuffled %d/%d verified positions -> %s"
          % (report["n_shuffled"], report["n_in"], out), file=sys.stderr)
    if refusals:
        print("REFUSED (emitted for NO row rather than passed through "
              "unshuffled): %s" % json.dumps(refusals, sort_keys=True),
              file=sys.stderr)
    return 0 if report["n_shuffled"] else 3


if __name__ == "__main__":
    sys.exit(main())
