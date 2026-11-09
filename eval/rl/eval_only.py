#!/usr/bin/env python3
"""The one guard that keeps eval data out of RL training data. What it refuses, and why: eval/rl/README.md,
"Eval-only data". `reasons` judges a start (MtgEnv.reset, rollout.main); `episode_reasons` judges a recorded episode
again (the readers in rollout.py); `GUARD_VERSION` is written on every episode record.
"""
import hashlib
import json
from pathlib import Path

import evalpath  # noqa: F401
import puzzle_format
import puzzle_twin
import seed_policy
import seed_registry

SUITES = evalpath.EVAL / "suites"
GUARD_VERSION = 2   # 2: content-hashed decks, every suite file, readers re-judge
NO_DECK = "game episode has no deck: the launcher's default decks cannot be judged"


_SPLITS = {}   # (file, mtime, size) of every suite file -> the result: a reader judges thousands of episodes


def puzzle_splits(suites_dir=None, repo=None):
    """{pzl_sha256: {split, ...}} over every record of every suite file that has a `puzzles` list (a record with
    no split contributes None). A twin record (`twin_of`) also contributes the splits of every suite record whose
    pzl_sha256 is its `original_pzl_sha256`, but only once `puzzle_twin.verify_record` has shown that the twin's
    .pzl is that original renamed by its manifest. Neither `twin_of` nor `original_pzl_sha256` is trusted as
    declared. A twin it cannot verify (no manifest beside its .pzl, or an edited board) reads as heldout, and so
    does a verified twin whose original is no non-twin suite record (an unknown original, or a chain)."""
    files = sorted(Path(suites_dir or SUITES).glob("*.json"))
    key = (str(repo),) + tuple((str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    if key not in _SPLITS:
        out, recs = {}, []
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            recs += [r for r in (data.get("puzzles") or []) if isinstance(r, dict)] if isinstance(data, dict) else []
        by_board = {}
        for rec in recs:
            if rec.get("pzl_sha256") and not puzzle_format.is_twin(rec):
                by_board.setdefault(rec["pzl_sha256"], set()).add(rec.get("split"))
        for rec in recs:
            if rec.get("pzl_sha256"):
                splits = out.setdefault(rec["pzl_sha256"], set())
                splits.add(rec.get("split"))
                if puzzle_format.is_twin(rec):
                    manifest = puzzle_twin.find_manifest(rec, repo or evalpath.REPO)
                    ok = manifest is not None and not puzzle_twin.verify_record(rec, manifest, repo or evalpath.REPO)
                    splits.update((by_board.get(rec.get("original_pzl_sha256")) if ok else None) or {"heldout"})
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


_HELD_SHAS = {}   # (path, mtime_ns, size) of every held-out deck file -> {content sha}: reasons() runs per episode


def _stat_key(path):
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)   # a missing file is part of the key, so its return shows through


def _held_shas(held):
    """The content hashes of the held-out deck files, cached per file stat (path, mtime, size) like puzzle_splits."""
    paths = [evalpath.REPO / d["file"] for d in held.values() if d.get("file")]
    key = tuple(_stat_key(p) for p in paths)
    if key not in _HELD_SHAS:
        _HELD_SHAS.clear()
        _HELD_SHAS[key] = {deck_sha(p) for p in paths}
    return _HELD_SHAS[key]


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
    try:
        registry = json.loads((evalpath.EVAL / "holdouts.json").read_text(encoding="utf-8")).get("decks") or {}
    except (OSError, ValueError) as e:   # an unreadable registry must not pass quietly
        return ["held-out deck registry cannot be checked: %s" % e]
    held = {slug: d for slug, d in registry.items() if d.get("status") == "held-out"}
    if not held:
        return ["held-out deck registry declares no held-out decks: the deck check would be vacuous"]
    shas = _held_shas(held)
    if Path(name).stem in held or (digest is not None and digest in shas):
        return ["held-out deck %s (eval/holdouts.json, matched by name or content)" % name]
    return ["deck %s unreadable: whether it is held out cannot be judged" % name] if digest is None else []


def _seed_reasons(seeds):
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
    return why


def reasons(puzzle=None, seeds=(), deck=None, suites_dir=None, game=False):
    """Why this start is eval-only data: [] when it may train a model. `game=True` (a game episode) also refuses a
    start with no deck."""
    why = _seed_reasons(seeds)
    if puzzle is not None:
        why += _puzzle_reasons(puzzle, puzzle_sha(puzzle), suites_dir)
    if deck:
        why += _deck_reasons(deck, deck_sha(deck))
    elif game:
        why.append(NO_DECK)
    return why


def _recorded_hashes(path, recorded, read, what):
    """(hashes to judge, reasons): the recorded hashes plus the file's own when it can be read now."""
    why = []
    recorded = list(dict.fromkeys(h for h in recorded if h))
    if len(recorded) > 1:
        why.append("%s episode records two different hashes (%s): they disagree" % (what, ", ".join(h[:12] for h in recorded)))
    disk = read(path) if path else None
    if disk and any(h != disk for h in recorded):
        why.append("%s %s: the file's hash disagrees with the recorded one" % (what, path))
    return list(dict.fromkeys(recorded + ([disk] if disk else []))), why


def episode_reasons(ep, suites_dir=None):
    """Re-judge a recorded episode from its seed, puzzle hashes and deck. Fails closed: an episode no guard version
    stamped, a puzzle episode with no content hash, an episode with no seed all come back with a reason. Both
    recorded puzzle hashes and the file's own (when readable) are judged and their reasons joined."""
    if not ep.get("guard"):
        return ["episode carries no guard field: no guard saw its start"]
    puzzle, deck = ep.get("puzzle"), ep.get("deck")
    seed = ep.get("seed", 1 if puzzle is not None else None)   # a puzzle's attempt seed defaults to 1
    if seed is None:
        return ["game episode records no seed"]
    why = _seed_reasons([seed])
    if puzzle is not None:
        hashes, more = _recorded_hashes(puzzle, [ep.get("puzzle_sha256"), (ep.get("stamps") or {}).get("pzl_sha256")],
                                        puzzle_sha, "puzzle")
        why += more
        if not hashes:
            return why + ["puzzle episode records no content hash: its split cannot be judged"]
        for h in hashes:
            why += _puzzle_reasons(puzzle, h, suites_dir)
    if deck:
        hashes, more = _recorded_hashes(deck, [ep.get("deck_sha256")], deck_sha, "deck")
        why += more
        for h in hashes or [None]:
            why += _deck_reasons(deck, h)
    elif puzzle is None:
        why.append(NO_DECK)
    return list(dict.fromkeys(why))


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
