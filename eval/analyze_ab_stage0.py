#!/usr/bin/env python3
"""analyze_ab_stage0.py — A/B win-rate delta + Stage-0 token/cost calibration.

Thin analysis layer built ON TOP of aggregate.py (imported, not duplicated):
aggregate.py already parses game-*.jsonl, keys winners by IDENTITY (not seat),
and computes Wilson/paired-bootstrap CIs. This script adds the two things the
ab-quality-1 / stage0-calibration runs need that aggregate.py does not:

  ab   OLD_DIR FIX_DIR
       Paired A/B across two arms that played the SAME seed x swap set. For
       each (seed, swap) decisive in BOTH arms, compares whether the LLM
       identity (identity A = "UR Delver") won under OLD vs under FIX, and runs
       an exact two-sided McNemar / sign test on the discordant pairs. Also
       prints each arm's standalone LLM win rate (via aggregate) for context.

  stage0 DIR
       Per-game token totals (prompt / completion / total) summed from the
       per-decision prompt_tokens / completion_tokens fields (TASK 4 logging;
       null rows — floor/auto/ai/Claude-CLI — are ignored). Reports mean /
       median / p90 tokens-per-game and model-calls-per-game, then costs it at
       OpenRouter-deepseek and DeepSeek-direct pricing, the latter WITH and
       WITHOUT the static-preamble cache-hit discount, and projects to a
       10k-game run.

IMPORTANT — arm identity lives in the DIRECTORY, not the provenance stamp.
The OLD and FIX ab-quality-1 arms were run from shadow-classpath launchers that
share ~/code/forge's installed spike SOURCE, so their game lines carry the SAME
overlay_sha and (for the scratch forge-home) engine_sha=unknown. Do NOT try to
tell the arms apart by provenance; the arm IS which out-dir the file is in.

Pricing is PLACEHOLDER (see DEFAULT_* below and --help). deepseek-v4-pro /
gemini-3.5-flash are this project's forward roster; plug real per-1M-token
quotes in via the flags before quoting any dollar figure.

Usage:
  python3 eval/analyze_ab_stage0.py ab   runs/ab-quality-1/old runs/ab-quality-1/fix
  python3 eval/analyze_ab_stage0.py stage0 runs/stage0-calibration
  # override pricing (USD per 1,000,000 tokens):
  python3 eval/analyze_ab_stage0.py stage0 DIR --or-in 0.30 --or-out 1.20 \
      --dd-in 0.27 --dd-cache-in 0.07 --dd-out 1.10 --preamble-tokens 2300
"""

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

# Reuse the battle-tested parser + stats from aggregate.py (same dir).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aggregate  # noqa: E402

# ---- PLACEHOLDER pricing, USD per 1,000,000 tokens. Update with real quotes.
# Rationale for the defaults: patterned on DeepSeek's published V3-era tiers
# (direct: cheap cache-hit input, ~4x that on cache-miss input, ~1.1 output;
# OpenRouter: a modest markup and — for deepseek — no prompt-cache pass-through
# assumed, i.e. every input token billed at the miss rate). These are guesses
# for the *-v4-pro tier; they are NOT authoritative.
DEFAULT_OR_IN = 0.30          # OpenRouter deepseek input  ($/M)
DEFAULT_OR_OUT = 1.20         # OpenRouter deepseek output ($/M)
DEFAULT_DD_IN = 0.27          # DeepSeek-direct input, cache MISS ($/M)
DEFAULT_DD_CACHE_IN = 0.07    # DeepSeek-direct input, cache HIT  ($/M)
DEFAULT_DD_OUT = 1.10         # DeepSeek-direct output ($/M)
DEFAULT_PREAMBLE_TOKENS = 2300  # measured static smart-tier system preamble


# ------------------------------------------------------------------ helpers

def _decisive(g):
    return g.get("winner") in ("A", "B")


def _median(xs):
    return statistics.median(xs) if xs else None


def fmt(x, nd=1):
    return "-" if x is None else ("%%.%df" % nd) % x


def fmt_usd(x):
    return "-" if x is None else "${:,.4f}".format(x)


# --------------------------------------------------------------- A/B compare

def load_games_by_key(path):
    """Return {(seed, swap): game_record} for decisive games (identity-keyed).

    Reuses aggregate.parse_dir so winner semantics are identical to the main
    reporter. Non-decisive / crashed games are dropped from the map but counted.
    """
    games, _dec, warnings = aggregate.parse_dir(path)
    by_key, nondecisive, total, lost = {}, 0, 0, 0
    # parse_dir dedupes per experiment condition, so one (seed, swap) may come
    # back twice when a dir mixes conditions. That is not one arm: refuse to
    # pick silently.
    conditions = {g.get("_experiment") for g in games if not g.get("_no_game_line")}
    if len(conditions) > 1:
        warnings = list(warnings) + [
            "%s holds %d experiment conditions; an A/B arm must be one condition "
            "(run aggregate.py on it to see them)" % (path, len(conditions))]
    for g in games:
        seed = g.get("seed")
        if seed is None:
            continue
        total += 1
        # Not the model's play (a dead bridge or a cut log, aggregate.unscorable, #430): counted apart, never a pair.
        if aggregate.unscorable(g, path):
            lost += 1
            continue
        if not _decisive(g):
            nondecisive += 1
            continue
        key = (seed, bool(g.get("swap", False)))
        if key in by_key:
            raise SystemExit("%s: seed=%s swap=%s appears under two experiment "
                             "conditions; split the directory before comparing"
                             % (path, seed, key[1]))
        by_key[key] = g
    return by_key, {"total": total, "nondecisive": nondecisive, "unscorable": lost,
                    "warnings": warnings}


def exact_two_sided_binom_p(b, c):
    """Exact two-sided sign/McNemar p-value on discordant counts (b, c).

    Under H0 each of the n=b+c discordant pairs is a fair coin. p = P(a Binom(n,
    0.5) is at least as extreme as the observed split), doubled and clamped to 1.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def ab_compare(old_dir, fix_dir):
    old_map, old_meta = load_games_by_key(old_dir)
    fix_map, fix_meta = load_games_by_key(fix_dir)

    # aggregate's own per-arm view (Wilson CI on the LLM identity vs opponent).
    old_sum = aggregate.build_summary(old_dir, aggregate.DEFAULT_RESAMPLES,
                                      aggregate.DEFAULT_BOOTSTRAP_SEED)
    fix_sum = aggregate.build_summary(fix_dir, aggregate.DEFAULT_RESAMPLES,
                                      aggregate.DEFAULT_BOOTSTRAP_SEED)

    shared = sorted(set(old_map) & set(fix_map))
    # Paired A/B on the LLM identity (A). x=1 iff identity A won that game.
    tally = Counter()   # WW / LL / old_only / fix_only
    detail = []
    for key in shared:
        oa = 1 if old_map[key]["winner"] == "A" else 0
        fa = 1 if fix_map[key]["winner"] == "A" else 0
        if oa == 1 and fa == 1:
            tally["both_win"] += 1
        elif oa == 0 and fa == 0:
            tally["both_loss"] += 1
        elif oa == 0 and fa == 1:
            tally["fix_only"] += 1   # FIX flipped a loss into a win
        else:
            tally["old_only"] += 1   # OLD won, FIX lost (regression)
        detail.append((key, oa, fa))

    b = tally["fix_only"]   # improvements
    c = tally["old_only"]   # regressions
    p = exact_two_sided_binom_p(b, c)

    def arm_llm_rate(summary):
        """Pull identity-A (the llm) arm win rate out of aggregate's arms map."""
        best = None
        for label, a in summary["arms"].items():
            if label.startswith("llm["):
                best = (label, a)
        return best

    return {
        "old_dir": str(old_dir), "fix_dir": str(fix_dir),
        "old_meta": old_meta, "fix_meta": fix_meta,
        "n_shared_decisive": len(shared),
        "old_arm": arm_llm_rate(old_sum), "fix_arm": arm_llm_rate(fix_sum),
        "old_paired": old_sum["paired"], "fix_paired": fix_sum["paired"],
        "tally": dict(tally), "mcnemar_b_fix_only": b, "mcnemar_c_old_only": c,
        "mcnemar_p_two_sided": p, "detail": detail,
    }


def print_ab(r):
    print("=" * 72)
    print("A/B QUALITY COMPARE — LLM identity 'UR Delver' vs Forge-AI 'Burn'")
    print("  OLD arm: %s" % r["old_dir"])
    print("  FIX arm: %s" % r["fix_dir"])
    print("  (arm identity = the DIRECTORY; provenance stamps are identical.)")
    print("=" * 72)
    for tag, meta, arm in (("OLD", r["old_meta"], r["old_arm"]),
                           ("FIX", r["fix_meta"], r["fix_arm"])):
        if arm:
            label, a = arm
            lo, hi = a["wilson95"]
            print("%s  %-18s %2d/%2d = %s%%  Wilson95 [%.1f%%, %.1f%%]   "
                  "(games total=%d, non-decisive=%d, unscorable=%d)"
                  % (tag, label, a["wins"], a["games"],
                     fmt(100 * a["win_rate"] if a["win_rate"] is not None else None),
                     100 * lo, 100 * hi, meta["total"], meta["nondecisive"], meta["unscorable"]))
        else:
            print("%s  (no decisive llm games yet; total=%d non-decisive=%d unscorable=%d)"
                  % (tag, meta["total"], meta["nondecisive"], meta["unscorable"]))

    print("\n--- paired A/B (same seed x swap decisive in BOTH arms) ---")
    print("shared decisive games: %d" % r["n_shared_decisive"])
    t = r["tally"]
    print("  both win : %d   both loss: %d" % (t.get("both_win", 0), t.get("both_loss", 0)))
    print("  FIX-only win (FIX fixed a loss): %d   OLD-only win (FIX regressed): %d"
          % (r["mcnemar_b_fix_only"], r["mcnemar_c_old_only"]))
    net = r["mcnemar_b_fix_only"] - r["mcnemar_c_old_only"]
    denom = r["n_shared_decisive"] or 1
    print("  net win-rate delta (FIX - OLD) over shared games: %+d/%d = %+.1f pts"
          % (net, r["n_shared_decisive"], 100.0 * net / denom))
    print("  exact two-sided McNemar/sign test on %d discordant pairs: p = %.4f"
          % (r["mcnemar_b_fix_only"] + r["mcnemar_c_old_only"], r["mcnemar_p_two_sided"]))
    if r["mcnemar_b_fix_only"] + r["mcnemar_c_old_only"] < 6:
        print("  NOTE: <6 discordant pairs — the sign test cannot reach p<0.05 "
              "two-sided no matter the split; treat as directional only.")
    print("\n(For each arm's own seat-swap paired-bootstrap CI vs the AI opponent,"
          " run: python3 eval/aggregate.py <arm-dir>)")


# --------------------------------------------------------------- Stage 0

def game_token_totals(path):
    """Per game file: (prompt_sum, completion_sum, n_model_calls, n_decisions).

    A 'model call' = a decision row with a non-null prompt_tokens OR
    completion_tokens (i.e. a real OpenRouter model consultation). Rows with
    both null (floor/auto/ai/deterministic, or Claude-CLI which reports no
    usage) are counted in n_decisions but contribute 0 tokens and 0 calls.
    Only files that contain a final game line are included (completed games).
    """
    out = []
    for fp in sorted(Path(path).glob("game-*.jsonl")):
        p_sum = c_sum = calls = decs = sm_calls = sm_prompt = 0
        has_game = False
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(d, dict):
                        continue
                    if d.get("t") == "game":
                        has_game = True
                        continue
                    if d.get("t") != "decision":
                        continue
                    decs += 1
                    pt, ct = d.get("prompt_tokens"), d.get("completion_tokens")
                    got = False
                    if isinstance(pt, (int, float)):
                        p_sum += pt
                        got = True
                        if d.get("tier") == "smart":
                            sm_calls += 1
                            sm_prompt += pt
                    if isinstance(ct, (int, float)):
                        c_sum += ct
                        got = True
                    if got:
                        calls += 1
        except OSError:
            continue
        out.append({"file": fp.name, "prompt": p_sum, "completion": c_sum,
                    "total": p_sum + c_sum, "calls": calls,
                    "smart_calls": sm_calls, "smart_prompt": sm_prompt,
                    "decisions": decs, "completed": has_game})
    return out


def stage0_report(path, px):
    rows = game_token_totals(path)
    completed = [r for r in rows if r["completed"] and r["calls"] > 0]
    n = len(completed)

    def stats(key):
        xs = [r[key] for r in completed]
        return {"mean": (sum(xs) / n) if n else None, "median": _median(xs),
                "p90": aggregate.p90(xs), "min": min(xs) if xs else None,
                "max": max(xs) if xs else None, "sum": sum(xs)}

    s_prompt, s_comp, s_total = stats("prompt"), stats("completion"), stats("total")
    s_calls = stats("calls")

    tot_prompt = s_prompt["sum"]
    tot_comp = s_comp["sum"]
    tot_calls = s_calls["sum"]

    # Cache model (per-tier): only the byte-stable SMART-tier system preamble
    # (px.preamble tokens) can be a prefix-cache hit, and only when it clears
    # DeepSeek's 1024-token minimum cacheable prefix; hits bill in 64-token
    # blocks, so the effective cached span is preamble rounded DOWN to 64. The
    # fast tier's small preamble sits below the 1024 floor -> never caches.
    # The volatile game-state suffix never caches on either tier. First smart
    # call of each game warms the cache (miss); the rest of that game's smart
    # calls hit. (Earlier revision wrongly credited every call — including
    # fast-tier — with a full-preamble hit, overstating the saving ~40%.)
    tot_smart_calls = sum(r["smart_calls"] for r in completed)
    cacheable_span = (px.preamble // 64) * 64 if px.preamble >= 1024 else 0
    cached_input = max(0, tot_smart_calls - n) * cacheable_span
    cached_input = min(cached_input, sum(r["smart_prompt"] for r in completed))
    miss_input = tot_prompt - cached_input

    def per_game(v):
        return (v / n) if n else None

    M = 1_000_000.0
    costs = {
        "openrouter_no_cache": (tot_prompt * px.or_in + tot_comp * px.or_out) / M,
        "deepseek_direct_no_cache": (tot_prompt * px.dd_in + tot_comp * px.dd_out) / M,
        "deepseek_direct_cache": (miss_input * px.dd_in + cached_input * px.dd_cache_in
                                  + tot_comp * px.dd_out) / M,
    }

    print("=" * 72)
    print("STAGE-0 TOKEN / COST CALIBRATION — %s" % path)
    print("  model: deepseek-v4-pro via OpenRouter (STAGE0 grown-preamble build)")
    print("=" * 72)
    print("games seen: %d files | completed-with-model-calls: %d" % (len(rows), n))
    if not n:
        print("no completed games with model calls yet — nothing to calibrate.")
        return {"n": 0}
    print("\n--- tokens per game (completed games only) ---")
    print("            mean      median    p90       min     max")
    for name, s in (("prompt", s_prompt), ("completion", s_comp), ("total", s_total)):
        print("%-11s %-9s %-9s %-9s %-7s %-7s"
              % (name, fmt(s["mean"], 0), fmt(s["median"], 0), fmt(s["p90"], 0),
                 fmt(s["min"], 0), fmt(s["max"], 0)))
    print("%-11s %-9s %-9s %-9s %-7s %-7s"
          % ("calls", fmt(s_calls["mean"], 1), fmt(s_calls["median"], 0),
             fmt(s_calls["p90"], 0), fmt(s_calls["min"], 0), fmt(s_calls["max"], 0)))

    print("\n--- corpus totals ---")
    print("prompt=%d  completion=%d  total=%d  model_calls=%d"
          % (tot_prompt, tot_comp, tot_prompt + tot_comp, tot_calls))
    print("cache split (smart-tier preamble=%d tok, 64-tok blocks, 1024 floor; "
          "smart calls=%d): cached_input=%d  miss_input=%d"
          % (px.preamble, tot_smart_calls, cached_input, miss_input))

    print("\n--- pricing (USD per 1M tokens; PLACEHOLDER — verify before quoting) ---")
    print("OpenRouter: in=$%.4f out=$%.4f | DeepSeek-direct: in=$%.4f cache_in=$%.4f out=$%.4f"
          % (px.or_in, px.or_out, px.dd_in, px.dd_cache_in, px.dd_out))
    print("\n--- cost ---")
    print("scenario                        total($)      per-game($)   per-10k-games($)")
    for name, tot in costs.items():
        pg = tot / n
        print("%-30s  %-12s  %-12s  %-12s"
              % (name, fmt_usd(tot), fmt_usd(pg), fmt_usd(pg * 10000)))
    cache_saving = costs["deepseek_direct_no_cache"] - costs["deepseek_direct_cache"]
    print("\nDeepSeek-direct cache saving: %s total (%s/game, %s per 10k) "
          "= %.1f%% off the no-cache direct cost."
          % (fmt_usd(cache_saving), fmt_usd(cache_saving / n),
             fmt_usd(cache_saving / n * 10000),
             100.0 * cache_saving / costs["deepseek_direct_no_cache"]
             if costs["deepseek_direct_no_cache"] else 0.0))
    print("\nNOTE: this Stage-0 run went via OpenRouter (no DeepSeek-direct key on"
          " this machine); token counts are provider-identical, so the direct-"
          "pricing columns are valid projections. The eventual 10k-game run goes"
          " provider-direct to capture the cache discount above.")
    return {"n": n, "tokens_per_game_mean_total": s_total["mean"], "costs": costs}


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ab", help="paired A/B win-rate compare of two arm dirs")
    a.add_argument("old_dir")
    a.add_argument("fix_dir")
    a.add_argument("--json", action="store_true")

    s = sub.add_parser("stage0", help="Stage-0 token/cost calibration of a dir")
    s.add_argument("dir")
    s.add_argument("--or-in", type=float, default=DEFAULT_OR_IN)
    s.add_argument("--or-out", type=float, default=DEFAULT_OR_OUT)
    s.add_argument("--dd-in", type=float, default=DEFAULT_DD_IN)
    s.add_argument("--dd-cache-in", type=float, default=DEFAULT_DD_CACHE_IN)
    s.add_argument("--dd-out", type=float, default=DEFAULT_DD_OUT)
    s.add_argument("--preamble-tokens", type=int, default=DEFAULT_PREAMBLE_TOKENS)
    s.add_argument("--json", action="store_true")

    args = ap.parse_args()

    if args.cmd == "ab":
        for d in (args.old_dir, args.fix_dir):
            if not Path(d).is_dir():
                raise SystemExit("not a directory: %s" % d)
        r = ab_compare(Path(args.old_dir), Path(args.fix_dir))
        if args.json:
            r.pop("detail", None)
            json.dump(r, sys.stdout, indent=2, default=str)
            print()
        else:
            print_ab(r)
    else:
        if not Path(args.dir).is_dir():
            raise SystemExit("not a directory: %s" % args.dir)

        class PX:
            or_in, or_out = args.or_in, args.or_out
            dd_in, dd_cache_in, dd_out = args.dd_in, args.dd_cache_in, args.dd_out
            preamble = args.preamble_tokens
        res = stage0_report(Path(args.dir), PX)
        if args.json:
            json.dump(res, sys.stdout, indent=2, default=str)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
