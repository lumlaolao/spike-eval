#!/usr/bin/env python3
"""Seed-bank registry: the mechanical answer to "was this number measured on
seeds the model was trained on?"

The registry (eval/seed_registry.json) declares what each seed band is FOR.
This module enforces it in both directions:

  check    Every eval-only seed must be absent from every training corpus
           (datasets/*/meta.jsonl). One hit is contamination -> exit 1.
           This is the CI gate; run it after every corpus build.

  audit    Retroactive labelling: for each dir under runs/, report which bank
           its seeds came from and which training corpora contain them. Existing
           runs stay readable instead of being silently orphaned by the new rule
           -- you can see exactly which past numbers were contaminated and by how
           much. Report-only unless --strict.

  classify Bank + policy for a seed spec. run_batch.py calls the same code path
           to refuse a cited batch that draws from a training band.

Why declaration rather than inference: inferring "held out" from "not currently
in a corpus" makes every seed held-out until the moment someone harvests it, so
the guarantee expires silently. A declared band is a decision that a later
harvest VIOLATES rather than quietly overwrites.

WHAT WOULD MAKE `check` FAIL (per CLAUDE.md: an unfalsifiable gate is not
evidence): a training corpus containing a seed inside an eval-only band. It also
fails on a corpus it cannot read -- a meta.jsonl whose rows carry no `seed` key
scans as clean under a naive reader, which is the exact shape of a gate that
can't fail. Those are reported as UNREADABLE and fail the check.

Stdlib only. Tests: python3 eval/test_seed_registry.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = Path(__file__).with_name("seed_registry.json")

# Policy vocabulary. Anything else in the JSON is a registry bug, not a new rule.
POLICIES = ("eval-only", "train", "harness")
UNREGISTERED = "unregistered"


class RegistryError(Exception):
    """Malformed registry file. Never swallowed: a registry we can't parse must
    stop the run, not degrade to 'everything is unregistered'."""


class Registry:
    """Declared seed banks, loaded once and queried per seed."""

    def __init__(self, banks, path=None):
        self.banks = banks
        self.path = path

    @property
    def eval_only_banks(self):
        return [b for b in self.banks if b["policy"] == "eval-only"]

    def bank_for(self, seed):
        """The first bank whose ranges contain `seed`, or None. Bands are
        expected to be disjoint; overlap_errors() enforces that at load."""
        for b in self.banks:
            for lo, hi in b["ranges"]:
                if lo <= seed <= hi:
                    return b
        return None

    def policy_for(self, seed):
        b = self.bank_for(seed)
        return b["policy"] if b else UNREGISTERED

    def is_burned(self, seed):
        """True if `seed` sits in an eval-only band but was already harvested (or
        played by a corpus-generating run) before that band was declared. Burned
        seeds keep their bank — the point is to record the damage, not to pretend
        the band was always smaller — but they cannot carry a cited eval."""
        b = self.bank_for(seed)
        if not b:
            return False
        return any(lo <= seed <= hi for lo, hi in b["burned"])

    def burned_in(self, seeds):
        return sorted(s for s in set(seeds) if self.is_burned(s))

    def bank_name_for(self, seed):
        b = self.bank_for(seed)
        return b["name"] if b else UNREGISTERED

    def classify(self, seeds):
        """{bank_name: sorted seeds} over an iterable of seeds."""
        out = {}
        for s in sorted(set(seeds)):
            out.setdefault(self.bank_name_for(s), []).append(s)
        return out


def load_registry(path=None):
    path = Path(path) if path else DEFAULT_REGISTRY
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise RegistryError("cannot read registry %s: %s" % (path, e))
    except (json.JSONDecodeError, ValueError) as e:
        raise RegistryError("malformed registry %s: %s" % (path, e))
    if not isinstance(raw, dict) or not isinstance(raw.get("banks"), list):
        raise RegistryError("registry %s has no 'banks' list" % path)

    banks = []
    for i, b in enumerate(raw["banks"]):
        where = "%s bank #%d" % (path.name, i)
        if not isinstance(b, dict):
            raise RegistryError("%s is not an object" % where)
        name = b.get("name")
        policy = b.get("policy")
        if not name:
            raise RegistryError("%s has no name" % where)
        if policy not in POLICIES:
            raise RegistryError("%s (%s) has unknown policy %r; expected one of %s"
                                % (where, name, policy, ", ".join(POLICIES)))
        def parse_ranges(key, required):
            out = []
            for r in b.get(key) or []:
                if (not isinstance(r, (list, tuple)) or len(r) != 2
                        or not all(isinstance(x, int) for x in r)):
                    raise RegistryError("%s (%s) has a malformed %s entry %r; want [lo, hi]"
                                        % (where, name, key, r))
                lo, hi = r
                if hi < lo:
                    raise RegistryError("%s (%s) has an inverted %s range [%d, %d]"
                                        % (where, name, key, lo, hi))
                out.append((lo, hi))
            if required and not out:
                raise RegistryError("%s (%s) declares no ranges" % (where, name))
            return out

        ranges = parse_ranges("ranges", True)
        burned = parse_ranges("burned", False)
        # A burn outside the band it belongs to is a typo that would silently
        # protect nothing, so reject it rather than carry it.
        for blo, bhi in burned:
            if not any(lo <= blo and bhi <= hi for lo, hi in ranges):
                raise RegistryError("%s (%s) burns [%d, %d], which is not inside its own "
                                    "ranges %s" % (where, name, blo, bhi, ranges))
        if burned and policy != "eval-only":
            raise RegistryError("%s (%s) declares burned seeds but its policy is %s; burning "
                                "only means something for an eval-only band"
                                % (where, name, policy))
        banks.append({"name": name, "policy": policy, "ranges": ranges,
                      "burned": burned, "purpose": b.get("purpose", "")})

    errs = overlap_errors(banks)
    if errs:
        raise RegistryError("overlapping seed bands make a seed's policy "
                            "ambiguous:\n  " + "\n  ".join(errs))
    return Registry(banks, path=path)


def overlap_errors(banks):
    """Human-readable descriptions of every pair of overlapping bands. A seed
    that is both 'train' and 'eval-only' would silently take whichever bank was
    listed first, so this is a load-time error rather than a warning."""
    errs = []
    for i in range(len(banks)):
        for j in range(i + 1, len(banks)):
            for lo1, hi1 in banks[i]["ranges"]:
                for lo2, hi2 in banks[j]["ranges"]:
                    lo, hi = max(lo1, lo2), min(hi1, hi2)
                    if lo <= hi:
                        errs.append("%s [%d, %d] overlaps %s [%d, %d] at [%d, %d]"
                                    % (banks[i]["name"], lo1, hi1,
                                       banks[j]["name"], lo2, hi2, lo, hi))
    return errs


# --------------------------------------------------------------- corpora

def corpus_seeds(datasets_dir=None):
    """Seeds present in every training corpus.

    Returns (seeds_by_corpus, unreadable). seeds_by_corpus maps corpus name ->
    set of seeds. `unreadable` names corpora whose meta.jsonl exists and has
    rows but exposes no `seed` on any of them: those scan as clean, so they are
    surfaced as gate failures rather than counted as evidence of cleanliness."""
    root = Path(datasets_dir) if datasets_dir else REPO_ROOT / "datasets"
    seeds_by_corpus, unreadable = {}, []
    if not root.is_dir():
        return seeds_by_corpus, unreadable
    for meta in sorted(root.glob("*/meta.jsonl")):
        name = meta.parent.name
        seeds, n_rows = set(), 0
        with open(meta, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                n_rows += 1
                s = rec.get("seed")
                if isinstance(s, int):
                    seeds.add(s)
        seeds_by_corpus[name] = seeds
        if n_rows and not seeds:
            unreadable.append(name)
    return seeds_by_corpus, unreadable


def _played_seeds(runs_dir=None):
    """Every seed any run directory has played. Used to tell a meaningful PASS
    from a vacuous one."""
    root = Path(runs_dir) if runs_dir else REPO_ROOT / "runs"
    return run_seeds(root) if root.is_dir() else set()


def run_seeds(run_dir):
    """Seeds a run directory played, from its game-<seed>-*.jsonl filenames."""
    seeds = set()
    for fp in Path(run_dir).rglob("game-*.jsonl"):
        m = re.match(r"game-(-?\d+)-\d+\.jsonl$", fp.name)
        if m:
            seeds.add(int(m.group(1)))
    return seeds


# ----------------------------------------------------------------- check

def check_contamination(registry, seeds_by_corpus):
    """Violations of the eval-only promise: [(bank, corpus, sorted seeds)].

    Burned seeds are excluded — they are declared, pre-existing damage, and
    failing on them forever would make the gate permanently red and therefore
    ignored. New contamination of a non-burned eval seed still fails."""
    violations = []
    for bank in registry.eval_only_banks:
        for corpus, seeds in sorted(seeds_by_corpus.items()):
            hits = sorted(s for s in seeds
                          if registry.bank_name_for(s) == bank["name"]
                          and not registry.is_burned(s))
            if hits:
                violations.append((bank["name"], corpus, hits))
    return violations


def burned_contamination(registry, seeds_by_corpus):
    """The declared, already-harvested eval seeds: [(bank, corpus, seeds)].
    Reported every run so a growing burn list stays visible instead of
    quietly becoming the normal state of the band."""
    out = []
    for bank in registry.eval_only_banks:
        for corpus, seeds in sorted(seeds_by_corpus.items()):
            hits = sorted(s for s in seeds
                          if registry.bank_name_for(s) == bank["name"]
                          and registry.is_burned(s))
            if hits:
                out.append((bank["name"], corpus, hits))
    return out


def check_suites(registry, suites_dir=None):
    """Do the frozen suites' declared arm seeds actually satisfy the registry?

    eval/suites/*.json declare per-arm seed ranges, and stage3-v1's seed_policy
    explicitly defers to this registry once it exists. Checking them here is
    what makes that deferral real: a suite arm whose range straddles a burned or
    training seed is a contaminated eval that nobody has run yet, which is the
    cheapest possible moment to find it.

    Returns [(suite, arm, [problem, ...])]."""
    root = Path(suites_dir) if suites_dir else REPO_ROOT / "eval" / "suites"
    out = []
    if not root.is_dir():
        return out
    for fp in sorted(root.glob("*.json")):
        try:
            suite = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as e:
            out.append((fp.name, "(file)", ["unreadable suite: %s" % e]))
            continue
        for arm in suite.get("arms") or []:
            spec = arm.get("seeds")
            if not spec:
                continue
            try:
                seeds = parse_seed_spec(str(spec))
            except ValueError as e:
                out.append((fp.name, arm.get("name", "?"), ["bad seed spec: %s" % e]))
                continue
            problems = []
            burned = registry.burned_in(seeds)
            if burned:
                problems.append("%d burned seed(s): %s"
                                % (len(burned), _fmt_seed_list(burned)))
            bad = sorted(s for s in set(seeds) if registry.policy_for(s) != "eval-only")
            if bad:
                problems.append("%d seed(s) not in an eval-only band: %s"
                                % (len(bad), _fmt_seed_list(bad)))
            if problems:
                out.append((fp.name, arm.get("name", "?"), problems))
    return out


def cmd_check(args):
    registry = load_registry(args.registry)
    seeds_by_corpus, unreadable = corpus_seeds(args.datasets)
    violations = check_contamination(registry, seeds_by_corpus)

    print("=== eval-only contamination check ===")
    print("registry: %s" % registry.path)
    for b in registry.eval_only_banks:
        print("eval-only bank %s %s%s"
              % (b["name"], fmt_ranges(b["ranges"]),
                 "  BURNED " + fmt_ranges(b["burned"]) if b["burned"] else ""))
    print("training corpora scanned: %d (%d distinct seeds)"
          % (len(seeds_by_corpus), len(set().union(*seeds_by_corpus.values()))
             if seeds_by_corpus else 0))
    for name, seeds in sorted(seeds_by_corpus.items()):
        print("  %-32s %5d seeds" % (name, len(seeds)))

    burned_hits = burned_contamination(registry, seeds_by_corpus)
    if burned_hits:
        print("\ndeclared BURN (pre-declaration contamination, frozen and labeled — not a failure, "
              "but these seeds can never carry a cited eval):")
        for bank, corpus, hits in burned_hits:
            print("  - bank %s x corpus %s: %d seed(s) %s"
                  % (bank, corpus, len(hits), _fmt_seed_list(hits)))

    failed = False
    if unreadable:
        failed = True
        print("\n!!! UNREADABLE CORPUS !!! these have rows but no `seed` field, so this "
              "check cannot see what they contain and their clean result means nothing:")
        for name in unreadable:
            print("  - %s" % name)

    if violations:
        failed = True
        print("\n!!! CONTAMINATION !!! eval-only seeds found in training data:")
        for bank, corpus, hits in violations:
            print("  - bank %s x corpus %s: %d seed(s) %s"
                  % (bank, corpus, len(hits), _fmt_seed_list(hits)))

    suite_problems = check_suites(registry, args.suites)
    if suite_problems:
        failed = True
        print("\n!!! SUITE CONFLICT !!! frozen suite arms whose declared seeds cannot "
              "carry a cited eval:")
        for suite, arm, problems in suite_problems:
            print("  - %s arm %s:" % (suite, arm))
            for p in problems:
                print("      %s" % p)

    if not violations and not unreadable:
        print("\ncorpus scan: PASS (no un-burned eval-only seed appears in any training corpus)")
        # A band nothing has played yet cannot be contaminated, so a PASS here says nothing
        # about discipline until an eval actually draws from it. Same honesty the fallback
        # gate applies to a dir with no llm arm. Burned seeds don't count: they were played
        # by training runs, which is the opposite of the band being exercised as a holdout.
        played = sum(1 for s in _played_seeds(args.runs)
                     if registry.policy_for(s) == "eval-only" and not registry.is_burned(s))
        if not played:
            print("NOTE: no run under runs/ has played a live eval-only seed yet, so this PASS is "
                  "vacuous -- it confirms the band is still clean, not that any eval used it.")

    if failed:
        print("\ncheck: FAILED", file=sys.stderr)
        return 1
    print("check: passed")
    return 0


# ----------------------------------------------------------------- audit

def audit_runs(registry, runs_dir=None, datasets_dir=None):
    """Per-run contamination labelling. Returns a list of dicts, newest-agnostic
    (sorted by dir name), one per run directory that played at least one game."""
    root = Path(runs_dir) if runs_dir else REPO_ROOT / "runs"
    seeds_by_corpus, _ = corpus_seeds(datasets_dir)
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        seeds = run_seeds(d)
        if not seeds:
            continue
        overlap = {}
        for corpus, cseeds in seeds_by_corpus.items():
            hit = sorted(seeds & cseeds)
            if hit:
                overlap[corpus] = hit
        contaminated = sorted(set().union(*overlap.values())) if overlap else []
        out.append({
            "run": d.name,
            "n_seeds": len(seeds),
            "banks": registry.classify(seeds),
            "policies": sorted({registry.policy_for(s) for s in seeds}),
            "corpus_overlap": overlap,
            "n_contaminated": len(contaminated),
            "contaminated_frac": len(contaminated) / len(seeds),
        })
    return out


def cmd_audit(args):
    registry = load_registry(args.registry)
    rows = audit_runs(registry, args.runs, args.datasets)
    print("=== run contamination audit ===")
    print("A run is contaminated to the extent its seeds appear in a training corpus.")
    print("This is retroactive labelling of work already done, not a verdict on the harness.\n")
    print("%-42s %6s %6s  %s" % ("run", "seeds", "contam", "banks / corpora"))
    print("-" * 100)
    dirty = 0
    for r in rows:
        if r["n_contaminated"]:
            dirty += 1
        banks = ",".join(sorted(r["banks"]))
        corp = ",".join(sorted(r["corpus_overlap"])) or "-"
        print("%-42s %6d %5d%%  %s | %s"
              % (r["run"], r["n_seeds"], round(100 * r["contaminated_frac"]), banks, corp))
    print("-" * 100)
    print("%d/%d run dirs draw on at least one seed that is in a training corpus."
          % (dirty, len(rows)))
    if args.strict and dirty:
        print("audit: FAILED (--strict)", file=sys.stderr)
        return 1
    return 0


# -------------------------------------------------------------- classify

def cmd_classify(args):
    registry = load_registry(args.registry)
    seeds = parse_seed_spec(args.seeds)
    by_bank = registry.classify(seeds)
    for bank, ss in sorted(by_bank.items()):
        policy = UNREGISTERED if bank == UNREGISTERED else registry.bank_for(ss[0])["policy"]
        print("%-18s %-12s %d seed(s) %s" % (bank, policy, len(ss), _fmt_seed_list(ss)))
    return 0


def parse_seed_spec(spec):
    """'1..20' inclusive range, '3,7,42' list, or '5'. Same grammar as
    run_batch.py --seeds so the two never disagree about what was requested."""
    spec = spec.strip()
    m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)", spec)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi < lo:
            raise ValueError("seed range end < start: %s" % spec)
        return list(range(lo, hi + 1))
    try:
        return [int(s) for s in spec.split(",") if s.strip()]
    except ValueError:
        raise ValueError("bad seed spec: %s" % spec)


# ------------------------------------------------- cited-eval enforcement

def cited_seed_violations(registry, seeds, seeds_by_corpus=None, datasets_dir=None):
    """Reasons this seed set may not carry a cited eval. Empty list == usable.

    Two independent checks, both required:
      1. DECLARED: every seed must sit in an eval-only bank. A train/harness/
         unregistered seed fails even if no corpus has harvested it yet.
      2. ACTUAL: no seed may appear in a training corpus right now. This catches
         the case the declaration missed -- a band relabelled after a harvest.

    Check 1 without check 2 trusts the registry to be honest about history;
    check 2 without check 1 lets today's unharvested seed become tomorrow's
    training row. Neither alone is the guarantee."""
    if seeds_by_corpus is None:
        seeds_by_corpus, _ = corpus_seeds(datasets_dir)
    problems = []

    bad_policy = {}
    for s in sorted(set(seeds)):
        policy = registry.policy_for(s)
        if policy != "eval-only":
            bad_policy.setdefault(policy, []).append(s)
    for policy, ss in sorted(bad_policy.items()):
        problems.append(
            "%d seed(s) are %s, not eval-only: %s" % (len(ss), policy, _fmt_seed_list(ss)))

    burned = registry.burned_in(seeds)
    if burned:
        problems.append("%d seed(s) are BURNED — inside an eval-only band but harvested or "
                        "played by a corpus-generating run before the band was declared: %s"
                        % (len(burned), _fmt_seed_list(burned)))

    for corpus, cseeds in sorted(seeds_by_corpus.items()):
        hits = sorted(set(seeds) & cseeds)
        if hits:
            problems.append("%d seed(s) already appear in training corpus %s: %s"
                            % (len(hits), corpus, _fmt_seed_list(hits)))
    return problems


def seed_bank_label(registry, seeds):
    """One label for the whole batch, stamped onto every game line as
    `seed_bank`. Mixed banks are named explicitly rather than picking a
    winner -- a batch straddling two banks is a fact the artifact should
    carry, not smooth over."""
    names = sorted(set(registry.bank_name_for(s) for s in seeds))
    if not names:
        return UNREGISTERED
    return names[0] if len(names) == 1 else "mixed[%s]" % "+".join(names)


# ------------------------------------------------------------- formatting

def fmt_ranges(ranges):
    return "[" + ", ".join("%d-%d" % (lo, hi) for lo, hi in ranges) + "]"


def _fmt_seed_list(seeds, limit=12):
    seeds = list(seeds)
    head = ", ".join(str(s) for s in seeds[:limit])
    return head if len(seeds) <= limit else "%s ... (+%d more)" % (head, len(seeds) - limit)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", help="path to seed_registry.json (default eval/seed_registry.json)")
    ap.add_argument("--datasets", help="datasets dir to scan (default <repo>/datasets)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="fail if any eval-only seed is in a training corpus")
    c.add_argument("--runs", help="runs dir, read only to tell a real PASS from a vacuous one "
                                  "(default <repo>/runs)")
    c.add_argument("--suites", help="frozen-suite dir to reconcile against the registry "
                                    "(default <repo>/eval/suites)")
    c.set_defaults(func=cmd_check)

    a = sub.add_parser("audit", help="label existing runs/ dirs with their contamination")
    a.add_argument("--runs", help="runs dir to audit (default <repo>/runs)")
    a.add_argument("--strict", action="store_true",
                   help="exit 1 if any run dir is contaminated (default: report only)")
    a.set_defaults(func=cmd_audit)

    cl = sub.add_parser("classify", help="show the bank and policy for a seed spec")
    cl.add_argument("seeds", help="'1..20', '3,7,42', or '5'")
    cl.set_defaults(func=cmd_classify)

    args = ap.parse_args()
    try:
        return args.func(args)
    except RegistryError as e:
        print("seed_registry: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
