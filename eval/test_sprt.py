#!/usr/bin/env python3
"""Self-test for sprt.py: formula checks + CLI smoke tests.

Run: python3 eval/test_sprt.py
"""

import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sprt  # noqa: E402

SCRIPT = str(Path(__file__).resolve().parent / "sprt.py")

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol


# --------------------------------------------------------------- bounds

def test_bounds():
    lower, upper = sprt.sprt_bounds(0.05, 0.05)
    check("upper bound = +ln(19)", approx(upper, math.log(19), 1e-3), str(upper))
    check("lower bound = -ln(19)", approx(lower, -math.log(19), 1e-3), str(lower))
    check("bounds ~= +-2.9444", approx(upper, 2.9444, 1e-3) and approx(lower, -2.9444, 1e-3))
    for bad_alpha, bad_beta in [(0.0, 0.05), (1.0, 0.05), (0.05, 0.0), (0.05, 1.0)]:
        try:
            sprt.sprt_bounds(bad_alpha, bad_beta)
            check("sprt_bounds rejects alpha/beta outside (0,1): %r/%r" % (bad_alpha, bad_beta), False)
        except ValueError:
            check("sprt_bounds rejects alpha/beta outside (0,1): %r/%r" % (bad_alpha, bad_beta), True)


# --------------------------------------------------------------- binomial

def test_binom_increments():
    win_inc = sprt.binom_llr(1, 0, 0.10, 0.20)
    loss_inc = sprt.binom_llr(0, 1, 0.10, 0.20)
    check("per-win LLR increment = ln(2) ~= 0.6931", approx(win_inc, math.log(2), 1e-3), str(win_inc))
    check("per-loss LLR increment = ln(8/9) ~= -0.1178", approx(loss_inc, math.log(8.0 / 9.0), 1e-3), str(loss_inc))


def test_binom_verdicts():
    # 5 wins, 0 losses: LLR = 5*ln(2) = 3.466 >= upper (2.944) -> H1.
    r = sprt.binom_sprt(5, 0, 0.10, 0.20)
    check("5-0 record crosses upper bound -> H1", r["verdict"] == "H1", str(r))

    # 0 wins, 26 losses: LLR = 26*ln(8/9) = -3.06 <= lower (-2.944) -> H0.
    r = sprt.binom_sprt(0, 26, 0.10, 0.20)
    check("0-26 record crosses lower bound -> H0", r["verdict"] == "H0", str(r))

    # 0 wins, 0 losses: LLR = 0 -> continue (well inside +-2.944).
    r = sprt.binom_sprt(0, 0, 0.10, 0.20)
    check("0-0 record -> continue", r["verdict"] == "continue", str(r))
    check("0-0 record LLR is exactly 0", r["llr"] == 0.0, str(r["llr"]))


def test_binom_expected_n_design_numbers():
    # EVAL-DESIGN.md S6.2 worked example: 0.10 -> 0.20, alpha=beta=0.05.
    e_h0, e_h1 = sprt.binom_expected_n(0.10, 0.20, 0.05, 0.05)
    check("E[n|H0] ~= 72 (+-5)", abs(e_h0 - 72) <= 5, str(e_h0))
    check("E[n|H1] ~= 60 (+-5)", abs(e_h1 - 60) <= 5, str(e_h1))


def test_binom_input_validation():
    for kwargs in [
        dict(wins=-1, losses=0, p0=0.1, p1=0.2),
        dict(wins=0, losses=-1, p0=0.1, p1=0.2),
        dict(wins=0, losses=0, p0=0.2, p1=0.2),
        dict(wins=0, losses=0, p0=0.0, p1=0.2),
        dict(wins=0, losses=0, p0=1.0, p1=0.2),
    ]:
        try:
            sprt.binom_sprt(**kwargs)
            check("binom_sprt rejects %r" % (kwargs,), False)
        except ValueError:
            check("binom_sprt rejects %r" % (kwargs,), True)


# ------------------------------------------------------------- pentanomial

def test_gsprt_midpoint_is_continue():
    # n0=0, n_half=19, n1=1 -> xbar=0.525, exactly midway between s0=0.5 and
    # s1=0.55, so the two squared-distance terms cancel and LLR is exactly 0.
    r = sprt.pent_gsprt(0, 19, 1, s0=0.5, s1=0.55)
    check("xbar at midpoint", approx(r["xbar"], 0.525, 1e-9), str(r["xbar"]))
    check("midpoint LLR ~= 0", approx(r["llr"], 0.0, 1e-9), str(r["llr"]))
    check("midpoint verdict -> continue", r["verdict"] == "continue", str(r))


def test_gsprt_strong_win_reaches_h1():
    # n0=2, n_half=20, n1=78 (N=100): xbar=0.88, var=0.0556 (nonzero),
    # LLR ~= 31.9, comfortably over the 2.944 upper bound.
    r = sprt.pent_gsprt(2, 20, 78, s0=0.5, s1=0.55)
    check("strong-win var is nonzero", r["var"] > 0, str(r["var"]))
    check("strong-win LLR crosses upper -> H1", r["verdict"] == "H1", str(r))


def test_gsprt_zero_variance_raises():
    try:
        sprt.pent_gsprt(0, 0, 5, s0=0.5, s1=0.55)
        check("pent_gsprt raises on zero variance (all-1 pairs)", False)
    except ValueError:
        check("pent_gsprt raises on zero variance (all-1 pairs)", True)
    try:
        sprt.pent_gsprt(0, 5, 0, s0=0.5, s1=0.55)
        check("pent_gsprt raises on zero variance (all-0.5 pairs)", False)
    except ValueError:
        check("pent_gsprt raises on zero variance (all-0.5 pairs)", True)


def test_gsprt_input_validation():
    for kwargs in [
        dict(n0=-1, n_half=0, n1=0),
        dict(n0=0, n_half=0, n1=0),
        dict(n0=1, n_half=1, n1=1, s0=0.5, s1=0.5),
        dict(n0=1, n_half=1, n1=1, s0=-0.1, s1=0.55),
        dict(n0=1, n_half=1, n1=1, s0=0.5, s1=1.5),
    ]:
        try:
            sprt.pent_gsprt(**kwargs)
            check("pent_gsprt rejects %r" % (kwargs,), False)
        except ValueError:
            check("pent_gsprt rejects %r" % (kwargs,), True)


def test_expected_n_degenerate_raises():
    # binom_expected_n called DIRECTLY with p0 == p1 (bypassing binom_sprt's
    # own guard) must hit the zero-increment defensive branch, not divide by
    # zero or return inf.
    try:
        sprt.binom_expected_n(0.2, 0.2, 0.05, 0.05)
        check("binom_expected_n raises on p0 == p1", False)
    except ValueError:
        check("binom_expected_n raises on p0 == p1", True)


# --------------------------------------------------------------------- LOS

def test_los():
    check("LOS(W==L) == 0.5", sprt.los(15, 15) == 0.5, str(sprt.los(15, 15)))
    check("LOS(10, 0) > 0.999", sprt.los(10, 0) > 0.999, str(sprt.los(10, 0)))
    check("LOS(0, 0) == 0.5 (uninformative)", sprt.los(0, 0) == 0.5)
    for w, l in [(15, 32), (7, 3), (1, 0), (0, 1), (50, 20)]:
        a, b = sprt.los(w, l), sprt.los(l, w)
        check("LOS symmetry (%d,%d)" % (w, l), approx(a, 1.0 - b, 1e-9), "%r vs 1-%r" % (a, b))
    try:
        sprt.los(-1, 0)
        check("los rejects negative counts", False)
    except ValueError:
        check("los rejects negative counts", True)


# --------------------------------------------------------------------- CLI

def run_cli(*args):
    return subprocess.run([sys.executable, SCRIPT] + list(args),
                           capture_output=True, text=True)


def test_cli_binom():
    r = run_cli("binom", "--wins", "12", "--losses", "40", "--p0", "0.10", "--p1", "0.20")
    check("cli binom exits 0", r.returncode == 0, r.stderr)
    check("cli binom prints verdict", "verdict:" in r.stdout, r.stdout)

    r = run_cli("binom", "--wins", "12", "--losses", "40", "--p0", "0.10", "--p1", "0.20", "--json")
    check("cli binom --json exits 0", r.returncode == 0, r.stderr)
    blob = json.loads(r.stdout)
    check("cli binom --json has verdict", "verdict" in blob, r.stdout)
    check("cli binom --json verdict matches library",
          blob["verdict"] == sprt.binom_sprt(12, 40, 0.10, 0.20)["verdict"])

    # p0 == p1 is a usage error -> exit 1, no traceback dumped to stdout.
    r = run_cli("binom", "--wins", "1", "--losses", "1", "--p0", "0.2", "--p1", "0.2")
    check("cli binom rejects p0==p1 with exit 1", r.returncode == 1, "rc=%d out=%s err=%s" % (r.returncode, r.stdout, r.stderr))
    check("cli binom p0==p1 prints usage error", "usage error" in r.stderr, r.stderr)

    # negative counts -> exit 1.
    r = run_cli("binom", "--wins", "-1", "--losses", "1", "--p0", "0.1", "--p1", "0.2")
    check("cli binom rejects negative wins with exit 1", r.returncode == 1, r.stderr)


def test_cli_pent():
    r = run_cli("pent", "--n0", "11", "--nhalf", "9", "--n1", "2")
    check("cli pent exits 0", r.returncode == 0, r.stderr)
    check("cli pent prints verdict", "verdict:" in r.stdout, r.stdout)

    r = run_cli("pent", "--n0", "2", "--nhalf", "20", "--n1", "78", "--json")
    check("cli pent --json exits 0", r.returncode == 0, r.stderr)
    blob = json.loads(r.stdout)
    check("cli pent --json verdict is H1", blob["verdict"] == "H1", r.stdout)

    r = run_cli("pent", "--n0", "0", "--nhalf", "0", "--n1", "5")
    check("cli pent rejects zero variance with exit 1", r.returncode == 1, r.stderr)


def test_cli_los():
    r = run_cli("los", "--wins", "15", "--losses", "32")
    check("cli los exits 0", r.returncode == 0, r.stderr)

    r = run_cli("los", "--wins", "15", "--losses", "32", "--json")
    blob = json.loads(r.stdout)
    check("cli los --json has los field", approx(blob["los"], sprt.los(15, 32), 1e-9), r.stdout)

    r = run_cli("los", "--wins", "-3", "--losses", "1")
    check("cli los rejects negative counts with exit 1", r.returncode == 1, r.stderr)


# --------------------------------------------------------------------- run

def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print()
    if FAILURES:
        print("FAILED %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
