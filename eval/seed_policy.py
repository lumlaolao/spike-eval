#!/usr/bin/env python3
"""Seed governance: reserved eval seed blocks that must never feed SFT/training data.

Single source of truth for the reserved seed ranges defined in
`docs/eval/EVAL-DESIGN.md` §6.5 ("Seed governance"). Three blocks are reserved:

  frozen-legacy           501-525      frozen baseline anchor block (v0 /
                                        teacher / ceiling comparisons); burned
                                        for held-out measurement, kept for
                                        tracking continuity.
  puzzle-mining           20000-29999  golden-suite mining generations (§3.2).
  certification-holdout   30000-30099  STR + PUZ held-out certification runs;
                                        opened at release time, then retired.

Threat model: a future SFT/training-data build (`make_sft_data.py`,
`mine_puzzles.py`, or anything downstream) accidentally consumes games played
on one of these seeds, contaminating the held-out measurement those seeds
exist to protect. This module is the shared guard EVAL-DESIGN.md §6.5 calls
for ("`make_sft_data.py` and `mine_puzzles.py` must both refuse seed-block
overlap — one shared guard, `eval/seed_policy.py`").

Seeds 1000-8999 (dev/exploratory/SPRT lanes) and the 42/7 harness canaries are
NOT reserved by this module — they are open for any use, including SFT.

API:
  RESERVED_BLOCKS         tuple of (name, lo, hi) triples, in the order above.
  reserved_block(seed)    -> block name containing `seed`, or None if clean.
  check_seeds(seeds)      -> {block_name: sorted [offending seeds]}; an empty
                             dict means every seed in the input is clean.

CLI:
  python3 eval/seed_policy.py SEED [SEED ...]
      Prints one classification line per seed; exits 1 if any seed is
      reserved, 0 if every seed is clean (2 on a usage/parse error).
"""

import sys

# (name, lo, hi) inclusive ranges. See EVAL-DESIGN.md §6.5 for the rationale
# behind each block; keep this table and that doc's table in sync.
RESERVED_BLOCKS = (
    ("frozen-legacy", 501, 525),
    ("puzzle-mining", 20000, 29999),
    ("certification-holdout", 30000, 30099),
)

DOC_POINTER = "docs/eval/EVAL-DESIGN.md §6.5"


def reserved_block(seed):
    """Return the reserved block name containing `seed`, or None if clean."""
    for name, lo, hi in RESERVED_BLOCKS:
        if lo <= seed <= hi:
            return name
    return None


def check_seeds(seeds):
    """Classify an iterable of ints against the reserved blocks.

    Returns {block_name: sorted [offending seeds]} — only blocks with at
    least one offender appear. An empty dict means every seed is clean (or
    the input was empty). Duplicate seeds are deduped per block."""
    offenders = {}
    for seed in seeds:
        name = reserved_block(seed)
        if name is None:
            continue
        offenders.setdefault(name, set()).add(seed)
    return {name: sorted(s) for name, s in offenders.items()}


def _main(argv):
    if not argv:
        print("usage: python3 eval/seed_policy.py SEED [SEED ...]",
              file=sys.stderr)
        return 2
    try:
        seeds = [int(a) for a in argv]
    except ValueError:
        print("all arguments must be integers", file=sys.stderr)
        return 2
    any_reserved = False
    for seed in seeds:
        name = reserved_block(seed)
        if name is None:
            print("%d\tclean" % seed)
        else:
            any_reserved = True
            print("%d\tRESERVED (%s)" % (seed, name))
    if any_reserved:
        print("", file=sys.stderr)
        print("some seed(s) above are in reserved eval blocks — see %s"
              % DOC_POINTER, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
