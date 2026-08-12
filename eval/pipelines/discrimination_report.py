#!/usr/bin/env python3
"""Which positions actually rank models, and what should we mine next?

WHY THIS EXISTS. `puz_index leaderboard` already reports free/dead/
discriminating for a given set of runs and refuses to rank when the suite
cannot separate models. It does not say WHICH KIND of position carries the
discriminating signal, which is the question mining needs answered. Measured
2026-08-12 on puz-v2.1: the suite is 54% `lethal-now` and that category
discriminates at 6%, so more than half the mining budget buys positions that
every model solves.

Run it over any set of recorded PUZ run dirs (ideally one per MODEL, all on the
same suite):

    python3 eval/pipelines/discrimination_report.py \
        runs/puz/v2.1-tournament/anthropic_claude-fable-5 \
        runs/puz/v2.1-tournament/google_gemini-3.5-flash \
        runs/puz/v2.1-tournament/x-ai_grok-4.5

    # add --cost to print the rollout-hours model for a target
    python3 eval/pipelines/discrimination_report.py RUNS... --cost 60

TWO DATA-HYGIENE RULES THIS SCRIPT ENFORCES, both of which silently corrupted a
first pass of the analysis:

  1. FILTER ON outcome=="ok", NEVER ON outcome!="interface_failure". A row whose
     request died with HTTP 402 records outcome='interface_failure' and
     interface_error='empty_reply' while its OWN `error` field holds
     'HTTPError: HTTP Error 402: Payment Required'. summary.json gets this right
     (counters.transport_errors=130, interface_failures=0,
     interface_failure_rate=None) — it is the per-row `outcome` that is
     ambiguous. Counting those rows as model failures makes every position look
     non-free and reported `lethal-now` at a bogus 100% discriminating.
  2. ONLY SCORE POSITIONS EVERY MODEL ANSWERED. Of the 8 v2.1-tournament runs
     only 3 are complete (opus-4.8 / kimi-k3 / gpt-5 are 130/130 402s,
     deepseek-v4-pro 77/130, qwen3.7-max 48/130). Mixing partial runs shrinks
     the shared set silently — including deepseek dropped it from 65 to 27.

INTERPRETING THE OUTPUT. `disc rate` per category is the actionable number.
Check it against the option-count bands too: category and n_options are
correlated (save-vs-lethal mean 6.16 options vs lethal-now 3.46), but
controlling for the band does NOT explain the effect — at 4-5 options
lethal-now discriminates 0% and save-vs-lethal 57%. Option count is a
THRESHOLD inside the right category, not an independent lever.

CAVEAT THE SCRIPT CANNOT FIX. The disc label is only weakly stable across model
sets: a frontier trio and two local models agreed on 73% of bucket labels but
their DISC sets overlapped just 8 of 14/17 (Jaccard 0.35, ~2.2x chance).
Positions selected against weak models will not all discriminate for a stronger
roster. Use local models as a cheap probe, then confirm survivors against at
least one frontier model. Related: `save-vs-lethal` scores 42% disc on the
frontier trio but only 11-32% locally, because weak models find those positions
DEAD rather than discriminating — it is the right category for ranking a
frontier roster and the wrong one to screen with small local models.
`graded-midgame` is the more stable choice (27-45% across all arms).

BEFORE YOU "FIX" MINING WITH THIS OUTPUT — READ select_sample.py FIRST.
The obvious conclusion from a run of this script is "stop mining lethal-now".
That is ALREADY DONE: eval/pipelines/select_sample.py DEFAULT_CAPS covers only
save-vs-lethal / mulligan / graded-midgame, and it iterates over the CAPS, so
any category absent from that dict (lethal-now included) is dropped before any
rollout is spent. The lethal-now positions in puz-v2.1 are LEGACY, mined in
overnight-{1,2} and mine-round-4..7 before that weighting existed. So the
headline-inflation they cause is a REPORTING problem, not a mining one, and the
cheap fix is to quote the `disc` column from `puz_index leaderboard` next to
`overall` rather than to re-mine anything. Two things that ARE stale:
mine_puzzles.DEFAULT_TARGETS still budgets lethal-now 300 (2x every other
category, mirroring EVAL-DESIGN.md S3.2) though it never binds because
mine_round.sh mines uncapped; and graded-midgame is capped 40 against
save-vs-lethal's 220 despite comparable and more stable disc rates.
"""
import argparse
import collections
import json
import os
import sys

RESULT_SCHEMA = "puzzle-result.v1"
# Measured constants, cited so a stale number is obvious.
MIN_PER_POSITION = 2.7   # ~56 rollout games/min @6 JVMs, ~153 games/position @n=45
VERIFY_YIELD = 0.21      # commit 23e446f0: two-way panel splits verify at 21%


def load_run(run_dir):
    """position_id -> list of solved bools, from VALID rows only."""
    per = collections.defaultdict(list)
    path = os.path.join(run_dir, "results.jsonl")
    with open(path) as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("schema") != RESULT_SCHEMA:
                continue
            # Rule 1: only genuinely-answered rows.
            if row.get("outcome") != "ok" or row.get("error"):
                continue
            per[row["position_id"]].append(row.get("score") == 10)
    return per


def load_suite(positions_file):
    meta = {}
    with open(positions_file) as fh:
        for line in fh:
            p = json.loads(line)
            meta[p["id"]] = p
    return meta


def band(n):
    return "3" if n == 3 else ("4-5" if n <= 5 else "6+")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dirs", nargs="+")
    ap.add_argument("--positions", default="eval/suites/puz-v2.1.jsonl")
    ap.add_argument("--cost", type=int, default=None,
                    help="also print rollout-hours to reach N discriminating")
    args = ap.parse_args(argv)

    meta = load_suite(args.positions)
    runs = {}
    for d in args.run_dirs:
        per = load_run(d)
        if not per:
            print("SKIP %s — no valid rows (all transport errors?)" % d)
            continue
        runs[d] = per
    if len(runs) < 2:
        print("need >=2 runs with valid data to classify positions", file=sys.stderr)
        return 2

    print("%-56s %s" % ("run", "positions answered"))
    for d, per in runs.items():
        print("  %-54s %d" % (os.path.basename(d), len(per)))

    # Rule 2: intersect.
    common = set.intersection(*[set(p) for p in runs.values()])
    dropped = max(len(p) for p in runs.values()) - len(common)
    print("\nscored on %d positions answered by ALL %d runs (%d dropped as partial)"
          % (len(common), len(runs), dropped))

    buckets = collections.Counter()
    bycat = collections.defaultdict(collections.Counter)
    grid = collections.defaultdict(collections.Counter)
    nopt = collections.defaultdict(list)
    for pid in common:
        solved = [any(runs[d][pid]) for d in runs]
        k = "free" if all(solved) else ("dead" if not any(solved) else "disc")
        buckets[k] += 1
        m = meta.get(pid, {})
        n = len(m.get("options") or [])
        bycat[m.get("category")][k] += 1
        grid[(m.get("category"), band(n))][k] += 1
        nopt[k].append(n)

    total = sum(buckets.values())
    free_share = buckets["free"] / float(total) if total else 0
    print("split: free=%d dead=%d DISCRIMINATING=%d" %
          (buckets["free"], buckets["dead"], buckets["disc"]))
    print("  => solving only the free positions already scores %.3f" % free_share)

    print("\n%-18s %-6s %-6s %-6s %-8s %s" %
          ("category", "free", "dead", "disc", "n", "disc rate"))
    for cat, c in sorted(bycat.items(), key=lambda kv: -(kv[1]["disc"] / float(sum(kv[1].values())))):
        tot = sum(c.values())
        print("%-18s %-6d %-6d %-6d %-8d %.0f%%" %
              (cat, c["free"], c["dead"], c["disc"], tot, 100.0 * c["disc"] / tot))

    print("\ncontrolling for option count (category is the driver, not n_options):")
    print("  %-18s %-6s %-6s %-6s %s" % ("category", "band", "disc", "n", "rate"))
    for (cat, b), c in sorted(grid.items()):
        tot = sum(c.values())
        print("  %-18s %-6s %-6d %-6d %.0f%%" % (cat, b, c["disc"], tot, 100.0 * c["disc"] / tot))
    print()
    for k in ("free", "disc", "dead"):
        if nopt[k]:
            print("  %-5s n=%-3d mean n_options=%.2f"
                  % (k, len(nopt[k]), sum(nopt[k]) / float(len(nopt[k]))))

    if args.cost is not None:
        need = args.cost - buckets["disc"]
        print("\n=== rollout cost to reach %d discriminating (need %d more) ==="
              % (args.cost, need))
        print("  assumptions: %.1f min/position @6 JVMs, %.0f%% verify yield"
              % (MIN_PER_POSITION, 100 * VERIFY_YIELD))
        if need <= 0:
            print("  already there")
            return 0
        strategies = [("current mix (all categories)",
                       buckets["disc"] / float(total))]
        for cat, c in sorted(bycat.items()):
            tot = sum(c.values())
            rate = c["disc"] / float(tot)
            if rate > 0:
                strategies.append(("%s only" % cat, rate))
        print("  %-38s %-9s %-12s %s" % ("strategy", "disc/pos", "verified", "hours"))
        for name, rate in sorted(strategies, key=lambda s: -s[1]):
            verified = need / rate
            hours = (verified / VERIFY_YIELD) * MIN_PER_POSITION / 60.0
            print("  %-38s %-9.2f %-12.0f %.0f h" % (name, rate, verified, hours))
        print("  NOT modelled: the disc label is only ~2.2x-chance stable across")
        print("  model sets, so some selected positions will not discriminate for")
        print("  a different roster (see module docstring).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
