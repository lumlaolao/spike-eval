#!/usr/bin/env python3
"""Sequential-testing calculator for mtg-forge-llm-agent eval batches
(EVAL-DESIGN.md S6.2, "SPRT for 'is v1 better than v0'").

Three independent calculators, all sharing the same Wald log-likelihood-ratio
(LLR) bounds and stop/continue/keep-playing verdict:

  1. Binomial SPRT on win rate. Caller excludes draws/invalid games first —
     wins (W) and losses (L) are decisive games only.
       LLR = W*ln(p1/p0) + L*ln((1-p1)/(1-p0))
     Also reports Wald-approximate expected sample sizes E[n|H0], E[n|H1] so
     batch planners can size the backstop (design guidance: 4x expected).

  2. Pentanomial GSPRT for paired mirror matches (fishtest-style generalized
     SPRT over pentanomial(-minus-draw-classes) pair-score frequencies, Brief
     B S2). Each seed-pair (two mirror games, seats swapped) yields identity
     A's pair-score in {0, 0.5, 1}; pairs containing a draw/invalid game are
     excluded by the caller. Given counts (n0, n_half, n1):
       N    = n0 + n_half + n1
       xbar = (0.5*n_half + n1) / N
       var  = sum((x - xbar)^2) / N        (biased MLE variance)
       LLR ~= N * ((xbar - s0)^2 - (xbar - s1)^2) / (2 * var)
     This is a normal approximation (CLT over pair scores), the same one
     fishtest uses for chess-engine SPRT testing. Same bounds/verdict as (1).

  3. LOS (likelihood of superiority):
       LOS = 0.5 * (1 + erf((W - L) / sqrt(2*(W + L))))

Shared bounds (Wald, both tests):
       lower = ln(beta / (1 - alpha))     accept H0 at/below this
       upper = ln((1 - beta) / alpha)     accept H1 at/above this
     At alpha = beta = 0.05: bounds = +/- ln(19) ~= +/- 2.9444.

Usage:
  python3 eval/sprt.py binom --wins 12 --losses 40 --p0 0.10 --p1 0.20 \\
      [--alpha 0.05 --beta 0.05] [--json]
  python3 eval/sprt.py pent --n0 11 --nhalf 9 --n1 2 \\
      [--s0 0.5 --s1 0.55 --alpha 0.05 --beta 0.05] [--json]
  python3 eval/sprt.py los --wins 15 --losses 32 [--json]

Exit codes: 0 success, 1 usage/input error (matching replay_match.py; NOTE
this differs from aggregate.py/run_batch.py, which reserve 2 for usage errors
and use 1 for data-quality gate failures — rc=1 means different things across
the two families, so pipelines scripting both should branch on the tool, not
the code alone).
"""

import argparse
import json
import math
import sys

DEFAULT_ALPHA = 0.05
DEFAULT_BETA = 0.05


# --------------------------------------------------------------- core math

def sprt_bounds(alpha, beta):
    """Wald LLR stopping bounds (lower, upper). See module docstring."""
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1), got %r" % (alpha,))
    if not (0.0 < beta < 1.0):
        raise ValueError("beta must be in (0, 1), got %r" % (beta,))
    lower = math.log(beta / (1.0 - alpha))
    upper = math.log((1.0 - beta) / alpha)
    return lower, upper


def verdict(llr, lower, upper):
    """Classify an LLR against SPRT bounds: 'H1', 'H0', or 'continue'."""
    if llr >= upper:
        return "H1"
    if llr <= lower:
        return "H0"
    return "continue"


# ------------------------------------------------------------- 1. binomial

def binom_llr(wins, losses, p0, p1):
    """LLR of a binomial SPRT: H0 p=p0 vs H1 p=p1 (per-game increments)."""
    return (wins * math.log(p1 / p0)
            + losses * math.log((1.0 - p1) / (1.0 - p0)))


def binom_expected_n(p0, p1, alpha, beta):
    """Wald-approximate expected sample sizes (E[n|H0], E[n|H1]).

    Z(p) is the expected per-game LLR increment when the true win rate is p:
        Z(p) = p*ln(p1/p0) + (1-p)*ln((1-p1)/(1-p0))
    Approximating the accept-H1 probability by its design value (alpha under
    H0, 1-beta under H1), the standard Wald expected-sample-size formula is:
        E[n|H0] = ((1-alpha)*lower + alpha*upper)  / Z(p0)
        E[n|H1] = (beta*lower + (1-beta)*upper)    / Z(p1)
    """
    lower, upper = sprt_bounds(alpha, beta)

    def z(p):
        return (p * math.log(p1 / p0)
                + (1.0 - p) * math.log((1.0 - p1) / (1.0 - p0)))

    z0, z1 = z(p0), z(p1)
    if z0 == 0.0 or z1 == 0.0:
        raise ValueError("expected per-game LLR increment is zero "
                          "(p0/p1 too close together)")
    e_n_h0 = ((1.0 - alpha) * lower + alpha * upper) / z0
    e_n_h1 = (beta * lower + (1.0 - beta) * upper) / z1
    return e_n_h0, e_n_h1


def binom_sprt(wins, losses, p0, p1, alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA):
    """Run the binomial SPRT. Returns a result dict."""
    if wins < 0 or losses < 0:
        raise ValueError("wins and losses must be non-negative")
    if not (0.0 < p0 < 1.0) or not (0.0 < p1 < 1.0):
        raise ValueError("p0 and p1 must be in (0, 1)")
    if p0 == p1:
        raise ValueError("p0 and p1 must differ")
    lower, upper = sprt_bounds(alpha, beta)
    llr = binom_llr(wins, losses, p0, p1)
    e_n_h0, e_n_h1 = binom_expected_n(p0, p1, alpha, beta)
    return {
        "wins": wins, "losses": losses, "p0": p0, "p1": p1,
        "alpha": alpha, "beta": beta,
        "llr": llr, "lower": lower, "upper": upper,
        "verdict": verdict(llr, lower, upper),
        "expected_n_h0": e_n_h0, "expected_n_h1": e_n_h1,
    }


# ----------------------------------------------------------- 2. pentanomial

def pent_gsprt(n0, n_half, n1, s0=0.5, s1=0.55,
                alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA):
    """Run the pentanomial GSPRT over paired mirror pair-scores.

    n0/n_half/n1 are pair counts at A-identity pair-score 0, 0.5, 1
    respectively (pairs with a draw/invalid game already excluded by the
    caller). See module docstring for the LLR formula.
    """
    if n0 < 0 or n_half < 0 or n1 < 0:
        raise ValueError("n0, n_half, n1 must be non-negative")
    n_total = n0 + n_half + n1
    if n_total == 0:
        raise ValueError("no pairs supplied (n0 + n_half + n1 == 0)")
    if not (0.0 <= s0 <= 1.0) or not (0.0 <= s1 <= 1.0):
        raise ValueError("s0 and s1 must be in [0, 1]")
    if s0 == s1:
        raise ValueError("s0 and s1 must differ")

    xbar = (0.5 * n_half + n1) / n_total
    var = (n0 * (0.0 - xbar) ** 2
           + n_half * (0.5 - xbar) ** 2
           + n1 * (1.0 - xbar) ** 2) / n_total
    if var == 0.0:
        raise ValueError("pair-score variance is zero (all pairs identical) "
                          "- GSPRT is undefined; need more varied outcomes")

    lower, upper = sprt_bounds(alpha, beta)
    llr = n_total * ((xbar - s0) ** 2 - (xbar - s1) ** 2) / (2.0 * var)
    return {
        "n0": n0, "n_half": n_half, "n1": n1, "n_total": n_total,
        "s0": s0, "s1": s1, "alpha": alpha, "beta": beta,
        "xbar": xbar, "var": var,
        "llr": llr, "lower": lower, "upper": upper,
        "verdict": verdict(llr, lower, upper),
    }


# -------------------------------------------------------------------- 3. LOS

def los(wins, losses):
    """Likelihood of superiority: 0.5*(1 + erf((W-L)/sqrt(2*(W+L)))).

    W==L gives exactly 0.5. W+L==0 makes the formula's denominator zero
    (no evidence either way); treated as the uninformative 0.5 rather than
    raising, since 0 games is a normal (if useless) starting state.
    """
    if wins < 0 or losses < 0:
        raise ValueError("wins and losses must be non-negative")
    if wins + losses == 0:
        return 0.5
    return 0.5 * (1.0 + math.erf((wins - losses) / math.sqrt(2.0 * (wins + losses))))


# ------------------------------------------------------------------- CLI

def _emit(result, human_lines, as_json):
    if as_json:
        json.dump(result, sys.stdout, indent=2)
        print()
    else:
        for line in human_lines:
            print(line)


def cmd_binom(args):
    try:
        r = binom_sprt(args.wins, args.losses, args.p0, args.p1,
                        args.alpha, args.beta)
    except ValueError as e:
        print("usage error: %s" % e, file=sys.stderr)
        return 1
    _emit(r, [
        "=== binomial SPRT ===",
        "wins=%d losses=%d  H0 p0=%.4f  H1 p1=%.4f  alpha=%.3f beta=%.3f"
        % (r["wins"], r["losses"], r["p0"], r["p1"], r["alpha"], r["beta"]),
        "LLR = %.4f   bounds = [%.4f, %.4f]" % (r["llr"], r["lower"], r["upper"]),
        "verdict: %s" % r["verdict"],
        "expected n (Wald approx): E[n|H0]=%.1f  E[n|H1]=%.1f"
        % (r["expected_n_h0"], r["expected_n_h1"]),
    ], args.json)
    return 0


def cmd_pent(args):
    try:
        r = pent_gsprt(args.n0, args.nhalf, args.n1, args.s0, args.s1,
                        args.alpha, args.beta)
    except ValueError as e:
        print("usage error: %s" % e, file=sys.stderr)
        return 1
    _emit(r, [
        "=== pentanomial GSPRT (paired mirror) ===",
        "n0=%d n_half=%d n1=%d  (N=%d)  H0 s0=%.4f  H1 s1=%.4f  alpha=%.3f beta=%.3f"
        % (r["n0"], r["n_half"], r["n1"], r["n_total"], r["s0"], r["s1"],
           r["alpha"], r["beta"]),
        "xbar=%.4f  var=%.4f" % (r["xbar"], r["var"]),
        "LLR = %.4f   bounds = [%.4f, %.4f]" % (r["llr"], r["lower"], r["upper"]),
        "verdict: %s" % r["verdict"],
    ], args.json)
    return 0


def cmd_los(args):
    try:
        p = los(args.wins, args.losses)
    except ValueError as e:
        print("usage error: %s" % e, file=sys.stderr)
        return 1
    _emit({"wins": args.wins, "losses": args.losses, "los": p}, [
        "=== LOS (likelihood of superiority) ===",
        "wins=%d losses=%d" % (args.wins, args.losses),
        "LOS = %.4f" % p,
    ], args.json)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("binom", help="binomial SPRT on win rate")
    b.add_argument("--wins", type=int, required=True)
    b.add_argument("--losses", type=int, required=True)
    b.add_argument("--p0", type=float, required=True, help="H0 win rate")
    b.add_argument("--p1", type=float, required=True, help="H1 win rate")
    b.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    b.add_argument("--beta", type=float, default=DEFAULT_BETA)
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_binom)

    p = sub.add_parser("pent", help="pentanomial GSPRT on paired mirror pair-scores")
    p.add_argument("--n0", type=int, required=True, help="# pairs with A pair-score 0")
    p.add_argument("--nhalf", type=int, required=True, help="# pairs with A pair-score 0.5")
    p.add_argument("--n1", type=int, required=True, help="# pairs with A pair-score 1")
    p.add_argument("--s0", type=float, default=0.5, help="H0 expected pair-score")
    p.add_argument("--s1", type=float, default=0.55, help="H1 expected pair-score")
    p.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    p.add_argument("--beta", type=float, default=DEFAULT_BETA)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pent)

    lo = sub.add_parser("los", help="likelihood of superiority")
    lo.add_argument("--wins", type=int, required=True)
    lo.add_argument("--losses", type=int, required=True)
    lo.add_argument("--json", action="store_true")
    lo.set_defaults(func=cmd_los)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
