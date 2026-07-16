#!/usr/bin/env python3
"""Aggregate MTG Forge LLM eval JSONL logs into win rates + decision stats.

Reads every game-*.jsonl in a directory. Prints:
  - per-arm win rates with Wilson 95% CIs (draws/invalids excluded from
    denominators, reported separately)
  - paired analysis when both swap variants of a seed exist (per-seed
    paired outcomes + paired-bootstrap CI on the win-rate delta)
  - decision-level stats: per-tier counts, mean/p90 latency, fallback
    rates, per-decision-type volume

Contract (final, documented in eval/README.md): the game line is
IDENTITY-KEYED, not seat-keyed. winner "A" means deck-identity A (the
DECK_A/CONTROLLER_A/SMART_A configuration) won, REGARDLESS of seat swap;
controller_a/smart_a/... always describe identity A's raw config. swap:true
is pairing/seat metadata only: it records that identity B sat in seat 1
(went first into the players list). Arm attribution therefore never flips
on swap; only the seat-bias diagnostic consults it.

Usage:
  python3 eval/aggregate.py <dir> [--json] [--bootstrap-seed N] [--resamples N]
"""

import argparse
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_RESAMPLES = 10_000
DEFAULT_BOOTSTRAP_SEED = 20260715  # fixed for reproducible CIs


# ---------------------------------------------------------------- parsing

def parse_dir(path):
    """Parse all game-*.jsonl files.

    Returns (games, decisions, warnings). Every game line becomes a game
    record (parallel same-millisecond launches can interleave two games into
    one file, so we don't assume one game per file); afterwards games are
    deduped by (seed, swap), keeping the last seen — files sort by trailing
    millis, so "last" is the most recent rerun. Files with no game line at
    all become synthetic invalid games (crashed before summarizing).
    """
    games, decisions, warnings = [], [], []
    files = sorted(Path(path).glob("game-*.jsonl"))
    if not files:
        warnings.append("no game-*.jsonl files found in %s" % path)
    for fp in files:
        found_game = False
        n_bad = 0
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        n_bad += 1
                        continue
                    if not isinstance(rec, dict):
                        n_bad += 1
                        continue
                    t = rec.get("t")
                    if t == "decision":
                        decisions.append(rec)
                    elif t == "game":
                        rec["_file"] = fp.name
                        games.append(rec)
                        found_game = True
        except OSError as e:
            warnings.append("unreadable file %s: %s" % (fp.name, e))
            continue
        if n_bad:
            warnings.append("%s: skipped %d malformed line(s)" % (fp.name, n_bad))
        if not found_game:
            # Crashed game: no summary line was ever written. Count as invalid.
            m = re.match(r"game-(-?\d+)-\d+\.jsonl$", fp.name)
            games.append({"t": "game",
                          "seed": int(m.group(1)) if m else None,
                          "winner": "invalid", "_no_game_line": True,
                          "_file": fp.name})
            warnings.append("%s: no game line -> counted as invalid" % fp.name)

    # Dedupe reruns of the same (seed, swap) combo, keeping the latest.
    # Crashed games (seed None or synthetic) are never merged away.
    deduped, latest = [], {}
    for g in games:
        seed = g.get("seed")
        if seed is None or g.get("_no_game_line"):
            deduped.append(g)
            continue
        key = (seed, bool(g.get("swap", False)))
        if key in latest:
            warnings.append("duplicate game for seed=%s swap=%s: keeping %s, "
                            "dropping %s" % (key[0], key[1], g["_file"],
                                             latest[key]["_file"]))
            deduped.remove(latest[key])
        latest[key] = g
        deduped.append(g)
    return deduped, decisions, warnings


def arm_label(game, identity):
    """Stable label for a deck-identity's configuration ('a' or 'b').
    Identity-keyed: describes the raw CONTROLLER_x/SMART_x config, never a
    seat. Model names only matter for llm controllers."""
    ctrl = game.get("controller_%s" % identity) or "unknown"
    if ctrl == "llm":
        smart = game.get("smart_%s" % identity) or "?"
        fast = game.get("fast_%s" % identity)
        return "llm[%s]" % smart if not fast else "llm[%s/%s]" % (smart, fast)
    return ctrl


# ------------------------------------------------------------- statistics

def wilson_ci(wins, n, z=1.96):
    """Wilson score interval for a binomial proportion. Returns (lo, hi).
    n=0 gives the maximally uninformative (0, 1)."""
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def paired_bootstrap(pair_outcomes, resamples=DEFAULT_RESAMPLES, rng=None):
    """Bootstrap CI on the win-rate DELTA (focal arm minus opponent) for a
    paired-mirror design.

    pair_outcomes: list of (x1, x2) per seed — x1 = focal arm won the
    unswapped game, x2 = focal arm won the swapped game (both 0/1, both
    games decisive).

    The resampling unit is the SEED (the pair), not the individual game:
    the two games of a seed share deck shuffles and seat assignment, so
    they are correlated and must travel together in each resample —
    resampling single games would understate the variance the pairing
    removed. With two arms and draws excluded, opponent rate = 1 - p, so
    delta = 2p - 1.

    Returns (delta_point, lo, hi).
    """
    if not pair_outcomes:
        return (0.0, -1.0, 1.0)
    if rng is None:
        rng = random.Random(DEFAULT_BOOTSTRAP_SEED)
    k = len(pair_outcomes)
    point_p = sum(x1 + x2 for x1, x2 in pair_outcomes) / (2.0 * k)
    deltas = []
    for _ in range(resamples):
        wins = 0
        for _ in range(k):
            x1, x2 = pair_outcomes[rng.randrange(k)]
            wins += x1 + x2
        deltas.append(2.0 * (wins / (2.0 * k)) - 1.0)
    deltas.sort()
    lo = deltas[int(0.025 * resamples)]
    hi = deltas[min(resamples - 1, int(0.975 * resamples))]
    return (2.0 * point_p - 1.0, lo, hi)


def p90(values):
    """Nearest-rank 90th percentile."""
    if not values:
        return None
    vs = sorted(values)
    return vs[max(0, math.ceil(0.9 * len(vs)) - 1)]


# ------------------------------------------------------------- summaries

def summarize_games(games):
    counts = Counter()
    for g in games:
        w = g.get("winner")
        if w in ("A", "B"):
            counts["decisive"] += 1
            # Seat-bias diagnostic. winner is identity-keyed, so seat 1
            # (first player) won iff identity A won an unswapped game or
            # identity B won a swapped game (swap:true = B sat in seat 1).
            swap = bool(g.get("swap", False))
            if (w == "A") != swap:
                counts["seat1_wins"] += 1
        elif w == "draw":
            counts["draw"] += 1
        else:
            counts["invalid"] += 1  # includes crashed / missing winner
            if g.get("_no_game_line"):
                counts["no_game_line"] += 1
    counts["total"] = len(games)
    return dict(counts)


def summarize_arms(games):
    """Unpaired per-arm win rates over decisive games only.

    Identity-keyed: winner "A" is identity A's win and controller_a/smart_a
    are identity A's config, so attribution never flips on swap."""
    arms = defaultdict(lambda: {"wins": 0, "games": 0})
    for g in games:
        w = g.get("winner")
        if w not in ("A", "B"):
            continue
        la, lb = arm_label(g, "a"), arm_label(g, "b")
        arms[la]["games"] += 1
        arms[lb]["games"] += 1
        arms[la if w == "A" else lb]["wins"] += 1
    out = {}
    for label, d in sorted(arms.items()):
        lo, hi = wilson_ci(d["wins"], d["games"])
        out[label] = {"wins": d["wins"], "games": d["games"],
                      "win_rate": d["wins"] / d["games"] if d["games"] else None,
                      "wilson95": [lo, hi]}
    return out


def summarize_paired(games, resamples, bootstrap_seed):
    """Pair games by seed (swap=False vs swap=True). Focal arm = identity A.

    Identity-keyed contract: winner "A" means identity A won in BOTH swap
    variants (the Java side maps by player name), so no flip on swap —
    swap only tells us which seat identity A occupied, which the paired
    delta deliberately averages out."""
    by_seed = defaultdict(dict)
    dup = 0
    for g in games:
        seed = g.get("seed")
        if seed is None:
            continue
        key = bool(g.get("swap", False))
        if key in by_seed[seed]:
            dup += 1  # rerun of same combo: keep the later file (sorted order)
        by_seed[seed][key] = g

    focal = None
    outcomes = []   # (x1, x2) per usable seed
    tallies = Counter()
    n_pairs = n_excluded_nondecisive = n_excluded_mismatch = 0
    for seed in sorted(by_seed):
        pair = by_seed[seed]
        if False not in pair or True not in pair:
            continue
        n_pairs += 1
        g1, g2 = pair[False], pair[True]
        if focal is None:
            focal = arm_label(g1, "a")
        # Identity A's config must be the same in both variants (the fields
        # are raw config, never post-swap). Drop pairs from a different mix.
        if arm_label(g1, "a") != focal or arm_label(g2, "a") != focal:
            n_excluded_mismatch += 1
            continue
        if g1.get("winner") not in ("A", "B") or g2.get("winner") not in ("A", "B"):
            n_excluded_nondecisive += 1
            continue
        # winner is identity-keyed: "A" = focal won, in either swap variant.
        x1 = 1 if g1["winner"] == "A" else 0
        x2 = 1 if g2["winner"] == "A" else 0
        outcomes.append((x1, x2))
        tallies["WW" if x1 + x2 == 2 else "LL" if x1 + x2 == 0 else "split"] += 1

    if not outcomes:
        return {"n_pairs_complete": n_pairs, "n_pairs_used": 0,
                "note": "no seed has both swap variants decisive"}

    rng = random.Random(bootstrap_seed)
    delta, lo, hi = paired_bootstrap(outcomes, resamples, rng)
    k = len(outcomes)
    focal_wins = sum(x1 + x2 for x1, x2 in outcomes)
    # Opponent label: identity B's config in any game whose identity A is focal.
    opponent = None
    for seed in sorted(by_seed):
        g = by_seed[seed].get(False)
        if g is not None and arm_label(g, "a") == focal:
            opponent = arm_label(g, "b")
            break
    result = {
        "focal_arm": focal,
        "opponent_arm": opponent,
        "n_pairs_complete": n_pairs,
        "n_pairs_used": k,
        "n_pairs_excluded_nondecisive": n_excluded_nondecisive,
        "n_pairs_excluded_config_mismatch": n_excluded_mismatch,
        "duplicate_combos": dup,
        "per_seed": {"focal_swept": tallies["WW"], "split": tallies["split"],
                     "focal_lost_both": tallies["LL"]},
        "focal_wins": focal_wins,
        "focal_win_rate": focal_wins / (2.0 * k),
        "delta": delta,
        "delta_ci95": [lo, hi],
        "resamples": resamples,
        "bootstrap_seed": bootstrap_seed,
    }
    return result


def summarize_decisions(decisions):
    tiers = defaultdict(lambda: {"n": 0, "latencies": [], "fallbacks": 0})
    types = Counter()
    for d in decisions:
        tier = d.get("tier") or "unknown"
        t = tiers[tier]
        t["n"] += 1
        lat = d.get("latency_ms")
        if isinstance(lat, (int, float)):
            t["latencies"].append(lat)
        if d.get("fallback"):
            t["fallbacks"] += 1
        types[d.get("type") or "unknown"] += 1
    tier_out = {}
    for tier, t in sorted(tiers.items()):
        lats = t["latencies"]
        tier_out[tier] = {
            "calls": t["n"],
            "mean_latency_ms": sum(lats) / len(lats) if lats else None,
            "p90_latency_ms": p90(lats),
            "fallback_rate": t["fallbacks"] / t["n"] if t["n"] else None,
            "fallbacks": t["fallbacks"],
        }
    return {"total": len(decisions), "by_tier": tier_out,
            "by_type": dict(types.most_common())}


def build_summary(path, resamples, bootstrap_seed):
    games, decisions, warnings = parse_dir(path)
    return {
        "dir": str(path),
        "games": summarize_games(games),
        "arms": summarize_arms(games),
        "paired": summarize_paired(games, resamples, bootstrap_seed),
        "decisions": summarize_decisions(decisions),
        "warnings": warnings,
    }


# --------------------------------------------------------------- output

def fmt_pct(x):
    return "-" if x is None else "%.1f%%" % (100.0 * x)


def print_human(s):
    g = s["games"]
    print("=== games (%s) ===" % s["dir"])
    print("total: %d   decisive: %d   draws: %d   invalid: %d (of which %d crashed with no game line)"
          % (g.get("total", 0), g.get("decisive", 0), g.get("draw", 0),
             g.get("invalid", 0), g.get("no_game_line", 0)))
    print("draws and invalids are EXCLUDED from all win-rate denominators.")
    if g.get("decisive"):
        # Seat 1 = first player. Computed identity-aware: wins-by-A among
        # swap=false games plus wins-by-B among swap=true games. In a
        # seat-swapped mirror this should hover near 50% even when one arm
        # dominates; a large skew means seat order matters (play/draw
        # advantage) or SWAP_SEATS broke.
        print("seat-bias check: seat 1 (first player) win rate %d/%d decisive (%s)"
              % (g.get("seat1_wins", 0), g["decisive"],
                 fmt_pct(g.get("seat1_wins", 0) / g["decisive"])))

    print("\n=== per-arm win rates (unpaired, decisive games only) ===")
    if not s["arms"]:
        print("(no decisive games)")
    for label, a in s["arms"].items():
        lo, hi = a["wilson95"]
        print("%-24s %3d/%3d  %s  Wilson95 [%.1f%%, %.1f%%]"
              % (label, a["wins"], a["games"], fmt_pct(a["win_rate"]),
                 100 * lo, 100 * hi))

    p = s["paired"]
    print("\n=== paired analysis (seed x seat-swap) ===")
    if p.get("n_pairs_used", 0) == 0:
        print(p.get("note", "no usable pairs"))
        if p.get("n_pairs_complete"):
            print("(complete pairs found: %d)" % p["n_pairs_complete"])
    else:
        print("focal arm: %s   vs   %s" % (p["focal_arm"], p["opponent_arm"]))
        print("pairs used: %d  (complete: %d, excluded non-decisive: %d, config-mismatch: %d)"
              % (p["n_pairs_used"], p["n_pairs_complete"],
                 p["n_pairs_excluded_nondecisive"],
                 p["n_pairs_excluded_config_mismatch"]))
        ps = p["per_seed"]
        print("per-seed: focal swept both %d | split %d | lost both %d"
              % (ps["focal_swept"], ps["split"], ps["focal_lost_both"]))
        print("focal win rate: %d/%d = %s"
              % (p["focal_wins"], 2 * p["n_pairs_used"], fmt_pct(p["focal_win_rate"])))
        print("win-rate delta (focal - opponent): %+.1f pts  "
              "paired-bootstrap 95%% CI [%+.1f, %+.1f]  (%d resamples, seed %d)"
              % (100 * p["delta"], 100 * p["delta_ci95"][0],
                 100 * p["delta_ci95"][1], p["resamples"], p["bootstrap_seed"]))

    d = s["decisions"]
    print("\n=== decisions (%d total) ===" % d["total"])
    for tier, t in d["by_tier"].items():
        mean = "-" if t["mean_latency_ms"] is None else "%.0fms" % t["mean_latency_ms"]
        pp = "-" if t["p90_latency_ms"] is None else "%.0fms" % t["p90_latency_ms"]
        print("tier %-6s calls=%5d  mean=%7s  p90=%7s  fallbacks=%d (%s)"
              % (tier, t["calls"], mean, pp, t["fallbacks"],
                 fmt_pct(t["fallback_rate"])))
    if d["by_type"]:
        print("by type: " + "  ".join("%s=%d" % (k, v)
                                      for k, v in d["by_type"].items()))

    if s["warnings"]:
        print("\n=== warnings ===")
        for w in s["warnings"]:
            print("- " + w)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="directory of game-*.jsonl files (EVAL_OUT)")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON summary instead of text")
    ap.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES,
                    help="bootstrap resamples (default %d)" % DEFAULT_RESAMPLES)
    ap.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED,
                    help="RNG seed for the bootstrap (default %d)" % DEFAULT_BOOTSTRAP_SEED)
    args = ap.parse_args()

    path = Path(args.dir).expanduser()
    if not path.is_dir():
        raise SystemExit("not a directory: %s" % path)

    summary = build_summary(path, args.resamples, args.bootstrap_seed)
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print_human(summary)


if __name__ == "__main__":
    main()
