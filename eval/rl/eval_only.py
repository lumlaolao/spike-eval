#!/usr/bin/env python3
"""The one guard that keeps eval data out of RL training data (eval/rl/README.md, "Eval-only data").

`reasons(puzzle=, seeds=, deck=)` says why a start must not train a model; `MtgEnv.reset` calls it for every episode
and refuses unless `allow_eval_only`, which marks the episode instead. Three sources, none of them a guess:
  seeds   eval/seed_policy.py's reserved blocks and every bank eval/seed_registry.py marks eval-only (the STR-AI
          panel seeds 7800..7999 are only in the registry);
  decks   eval/holdouts.json (the decks make_sft_data.enforce_heldout_decks keeps out of corpora);
  puzzles the suites' records, matched by the file's bytes (pzl_sha256), never by its name: a copy of a heldout
          puzzle is heldout. Open only if every record with those bytes says "open"; a puzzle no record lists, or a
          record with another split or none, is refused.
Decks are matched by content too: the sha256 of the sorted, stripped, non-blank, non-comment card lines, so a
renamed or reformatted copy of a held-out deck is held out.

`GUARD_VERSION` is written on every episode record with the start's content hashes. `episode_reasons` re-judges a
recorded episode from them, so the readers (rollout.py) never trust an `eval_only` key and drop an episode no guard saw.
"""
import hashlib
import json
from pathlib import Path

import evalpath  # noqa: F401
import make_sft_data
import puzzle_format
import seed_policy
import seed_registry

SUITES = evalpath.EVAL / "suites"
GUARD_VERSION = 2   # 2: content-hashed decks, every suite file, readers re-judge


_SPLITS = {}   # (file, mtime, size) of every suite file -> the result: a reader judges thousands of episodes


def puzzle_splits(suites_dir=None):
    """{pzl_sha256: {split, ...}} over every record of every suite file that has a `puzzles` list (a record with
    no split contributes None)."""
    files = sorted(Path(suites_dir or SUITES).glob("*.json"))
    key = tuple((str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    if key not in _SPLITS:
        out = {}
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            for rec in (data.get("puzzles") or []) if isinstance(data, dict) else []:
                if rec.get("pzl_sha256"):
                    out.setdefault(rec["pzl_sha256"], set()).add(rec.get("split"))
        _SPLITS.clear()
        _SPLITS[key] = out
    return _SPLITS[key]


def puzzle_sha(path):
    """The sha256 of a puzzle file, or None when it cannot be read."""
    try:
        return puzzle_format.sha256_file(path)
    except OSError:
        return None


def deck_sha(path):
    """The sha256 of a deck's normalised card list (sorted, stripped, no blank or # lines), or None."""
    try:
        lines = [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeDecodeError):
        return None
    return hashlib.sha256("\n".join(sorted(l for l in lines if l and not l.startswith("#"))).encode()).hexdigest()


def _puzzle_reasons(name, digest, suites_dir):
    if digest is None:
        return ["puzzle %s unreadable: its split cannot be judged" % name]
    splits = puzzle_splits(suites_dir).get(digest)
    if not splits:
        return ["puzzle %s is not listed in any suite (matched by content): its split is unknown" % name]
    if "heldout" in splits:
        return ["heldout puzzle %s (a suite record with these bytes says heldout)" % name]
    return [] if splits == {"open"} else ["puzzle %s is not open in every suite record (splits %s)" % (
        name, sorted(map(str, splits)))]


def _deck_reasons(name, digest):
    slugs, note = make_sft_data.load_heldout_deck_slugs()
    if note:   # an unreadable registry must not pass quietly
        return ["held-out deck registry cannot be checked: %s" % note]
    registry = json.loads((evalpath.EVAL / "holdouts.json").read_text(encoding="utf-8"))["decks"]
    held = {deck_sha(evalpath.REPO / registry[s]["file"]) for s in slugs if registry[s].get("file")}
    if Path(name).stem in slugs or (digest is not None and digest in held):
        return ["held-out deck %s (eval/holdouts.json, matched by name or content)" % name]
    return ["deck %s unreadable: whether it is held out cannot be judged" % name] if digest is None else []


def reasons(puzzle=None, seeds=(), deck=None, suites_dir=None, puzzle_sha256=None, deck_sha256=None):
    """Why this start is eval-only data: [] when it may train a model. The hashes are for a recorded start whose
    files are gone (`episode_reasons`); otherwise they are read from the files."""
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
        why += _puzzle_reasons(puzzle, puzzle_sha256 or puzzle_sha(puzzle), suites_dir)
    if deck:
        why += _deck_reasons(deck, deck_sha256 or deck_sha(deck))
    return why


def episode_reasons(ep, suites_dir=None):
    """Re-judge a recorded episode from its seed, puzzle hash and deck. Fails closed: an episode no guard version
    stamped, a puzzle episode with no content hash, an episode with no seed all come back with a reason."""
    if not ep.get("guard"):
        return ["episode carries no guard field: no guard saw its start"]
    seed = ep.get("seed", 1 if ep.get("puzzle") is not None else None)   # a puzzle's attempt seed defaults to 1
    if seed is None:
        return ["game episode records no seed"]
    sha = ep.get("puzzle_sha256") or (ep.get("stamps") or {}).get("pzl_sha256")
    if ep.get("puzzle") is not None and not sha:
        return ["puzzle episode records no content hash: its split cannot be judged"]
    return reasons(puzzle=ep.get("puzzle"), seeds=[seed],
                   deck=ep.get("deck"), suites_dir=suites_dir, puzzle_sha256=sha, deck_sha256=ep.get("deck_sha256"))


def duplicate_groups(puzzles, game_seeds, group_size):
    """What a run would play twice: the same puzzle, the same game seed (a game group is seeds s..s+G-1)."""
    why = []
    keys = [puzzle_sha(p) or str(Path(p).resolve()) for p in puzzles]   # by content; the path if unreadable
    why += ["puzzle %s given twice" % puzzles[keys.index(k)] for k in sorted({k for k in keys if keys.count(k) > 1})]
    seeds = [s + i for s in game_seeds for i in range(group_size)]
    if len(seeds) != len(set(seeds)):
        why.append("game seeds %s would be played twice (groups of %d from %s)" % (
            sorted({s for s in seeds if seeds.count(s) > 1})[:5], group_size, list(game_seeds)))
    return why
