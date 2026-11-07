#!/usr/bin/env python3
"""The one guard that keeps eval data out of RL training data (eval/rl/README.md, "Eval-only data").

`reasons(puzzle=, seeds=, deck=)` says why a start must not train a model; `MtgEnv.reset` calls it for every episode
and refuses unless `allow_eval_only`, which marks the episode instead. Three sources, none of them a guess:
  seeds   eval/seed_policy.py's reserved blocks and every bank eval/seed_registry.py marks eval-only (the STR-AI
          panel seeds 7800..7999 are only in the registry);
  decks   eval/holdouts.json (the decks make_sft_data.enforce_heldout_decks keeps out of corpora);
  puzzles the suites' records, matched by the file's bytes (pzl_sha256), never by its name: a copy of a heldout
          puzzle is heldout. Heldout if any record with those bytes says so; a puzzle no record lists is refused.
"""
import json
from pathlib import Path

import evalpath  # noqa: F401
import make_sft_data
import puzzle_format
import seed_policy
import seed_registry

SUITES = evalpath.EVAL / "suites"


def puzzle_splits(suites_dir=None):
    """{pzl_sha256: {split, ...}} over every puzzle suite record."""
    out = {}
    for f in sorted(Path(suites_dir or SUITES).glob("puzzles-*.json")):
        for rec in json.loads(f.read_text(encoding="utf-8")).get("puzzles", []):
            if rec.get("pzl_sha256"):
                out.setdefault(rec["pzl_sha256"], set()).add(rec.get("split"))
    return out


def _puzzle_reasons(path, suites_dir):
    try:
        digest = puzzle_format.sha256_file(path)
    except OSError as e:
        return ["puzzle %s unreadable (%s): its split cannot be judged" % (path, e)]
    splits = puzzle_splits(suites_dir).get(digest)
    if not splits:
        return ["puzzle %s is not listed in any suite (matched by content): its split is unknown" % path]
    return ["heldout puzzle %s (a suite record with these bytes says heldout)" % path] if "heldout" in splits else []


def _deck_reasons(deck):
    slugs, note = make_sft_data.load_heldout_deck_slugs()
    if note:   # an unreadable registry must not pass quietly
        return ["held-out deck registry cannot be checked: %s" % note]
    path = Path(deck)
    registry = json.loads((evalpath.EVAL / "holdouts.json").read_text(encoding="utf-8"))["decks"]
    files = {(evalpath.REPO / registry[s]["file"]).resolve() for s in slugs if registry[s].get("file")}
    return ["held-out deck %s (eval/holdouts.json)" % path] if path.stem in slugs or path.resolve() in files else []


def reasons(puzzle=None, seeds=(), deck=None, suites_dir=None):
    """Why this start is eval-only data: [] when it may train a model."""
    why = []
    seeds = sorted({int(s) for s in seeds})
    for block, hit in seed_policy.check_seeds(seeds).items():
        why.append("reserved seed block %s: %s" % (block, hit[:5]))
    registry = seed_registry.load_registry()
    banks = {}
    for s in seeds:
        if registry.policy_for(s) == "eval-only":
            banks.setdefault(registry.bank_name_for(s), []).append(s)
    for bank, hit in banks.items():
        why.append("seed bank %s is eval-only in seed_registry: %s" % (bank, hit[:5]))
    if puzzle is not None:
        why += _puzzle_reasons(Path(puzzle), suites_dir)
    if deck:
        why += _deck_reasons(deck)
    return why


def duplicate_groups(puzzles, game_seeds, group_size):
    """What a run would play twice: the same puzzle, the same game seed (a game group is seeds s..s+G-1)."""
    why = []
    paths = [str(Path(p).resolve()) for p in puzzles]
    why += ["puzzle %s given twice" % p for p in sorted({p for p in paths if paths.count(p) > 1})]
    seeds = [s + i for s in game_seeds for i in range(group_size)]
    why += ["game seeds %s would be played twice (groups of %d from %s)" % (
        sorted({s for s in seeds if seeds.count(s) > 1})[:5], group_size, list(game_seeds))
        for _ in [0] if len(seeds) != len(set(seeds))]
    return why
