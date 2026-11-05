#!/usr/bin/env python3
"""Twin cards: a puzzle whose every card is renamed, so a model that knows a card only by its name cannot recall it.

A twin card is a Forge card script with only its name changed: `Name:`, its own name in `Oracle:` and in the
description parameters, and its self-references (`Card.named<Name>`, `DeckHints:Name$<Name>`). The rules, the AI hints
and every number stay as they are, so Forge plays the twin exactly as the original. A twin puzzle is the original
`.pzl` with every name-bearing field rewritten: the zone lists (with `NamedCard:` and `MergedCards:`, and a `|Set:` or
`|Art:` dropped, since a twin has one printing), `Description` (the seat's brief), `Name`, `Rule`, the `[solution]`
line, the `[acceptance]` names and the `# search` / `# trap` comments. Basic lands keep their names (the engine and
the type line name them; `KEEP_NAMES`).

What it guarantees: no original name of a twinned card survives anywhere in the twin `.pzl` or script (whole name,
any case, with a plural or possessive), or the twin is refused. Refused: a card a Forge engine or AI source names in
a string literal (`engine_names`, scanned from the pinned checkout), a card Forge does not have, a script with two
faces or naming another card (`named X`, a meld or partner pair), a twin name that is a real card or a prefix of
another label on its board (ScriptBridge matches "label starts with"), and two cards on one twin name.

What it cannot do: it renames, so it measures name recall, not recall of the rules text, which the seat still reads
whole. Words of a name left in prose (`1 Alchemist(s)` in a generated `Rule:`) are not whole names and stay; the seat
is not shown `Name:` or `Rule:`. It does not prove a twin board plays as its original (that needs the engine: the
proof replay and a name-mapped log comparison), and it does not yet make a suite record (`twin_of`, the inherited
split) or pair scores (`puzzle_score.py`); those come later.

The manifest holds, per card, the original's path and sha256, the twin name and the edited lines, never a whole
Forge script: `install` regenerates each twin from the checkout's original, as `engine_patches.py` does for the
engine, and refuses an original that changed. Twin boards are written under `runs/` only, since some sources are
third-party works. Stdlib only.

  python3 eval/puzzles/puzzle_twin.py make --suite eval/suites/puzzles-rules-sample-v1.json --ids rules-RP_017 \
      --seed 1 --out runs/twins/rules-v1 [--forge F]
  python3 eval/puzzles/puzzle_twin.py install --manifest runs/twins/rules-v1/twin-manifest.json --forge F
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import puzzle_format as pf  # noqa: E402

REPO = evalpath.REPO
SCHEMA = "twin-cards.v1"
MANIFEST = "twin-manifest.json"
CARDSFOLDER = "forge-gui/res/cardsfolder"
TWINS_DIR = CARDSFOLDER + "/zz_twins"
EDITIONS = "forge-gui/res/editions"
EDITION_FILE = EDITIONS + "/Spike Twins.txt"
# An unused code (no edition file in the pinned checkout has it). Custom_Set keeps the twins out of every format and
# of art downloads, and a past date keeps it from being the "upcoming set" (CardDb.java:614-620).
EDITION_CODE = "ZTW"
EDITION = "[metadata]\nCode=%s\nDate=2026-01-01\nName=Spike Twins\nType=Custom_Set\n\n[cards]\n" % EDITION_CODE
# Engine and AI sources scanned for card names in string literals (Dark Depths, Pithing Needle, Squee...): a card
# whose name decides something in Java cannot be renamed without changing how it plays.
ENGINE_SRC = ("forge-core/src/main/java", "forge-game/src/main/java", "forge-ai/src/main/java",
              "forge-gui/src/main/java")
BASICS = ("Plains", "Island", "Swamp", "Mountain", "Forest")
KEEP_NAMES = frozenset(BASICS + ("Wastes",) + tuple("Snow-Covered " + b for b in BASICS))

_ZONE_KEY = re.compile(r"^(?:p\d|human|ai)(play|battlefield|hand|graveyard|library|exile|command|sideboard"
                       r"|precast|putonstack)$")
_STATE_FIXED = re.compile(r"^(?:turn|active\w*|removesummoningsickness|(?:p\d|human|ai)(?:life|counters|manapool"
                          r"|landsplayed\w*|speed|numringtemptedyou))$")
_METADATA_FIXED = {"goal", "turns", "difficulty"}
_METADATA_LABELS = {"name", "rule"}
_DESC_PARAM = re.compile(r"^\s*\w*Desc\w*\$")
_NAMED = re.compile(r"(?<![A-Za-z])named(?: (?=[A-Z])|(?=[A-Z]))")
_PAIRS = re.compile(r"^(?:MeldPair:|AlternateMode:|K:Partner with)")

ONSETS = "b d f g k l m n p r s t v z br dr gr kr st th sh".split()
VOWELS = "a e i o u ae ei io".split()
CODAS = ["", "", "n", "r", "l", "s", "th", "k"]


class TwinRefused(Exception):
    """A card or a board that cannot be twinned; the message names the card or the line."""


def _word_re(name, flags=0):
    """`name` as a whole name, followed by nothing, a plural `s`/`es`, or punctuation (a possessive `'s`)."""
    return re.compile(r"(?<![A-Za-z0-9])%s(?=(?:s|es)?(?![A-Za-z0-9]))" % re.escape(name), flags)


def _leak(name):
    return _word_re(name, re.IGNORECASE)


def _sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ the checkout (read only)

def forge_home():
    """The first of $FORGE_HOME, ~/code/forge and /root/forge that has a readable cardsfolder, or None."""
    env = os.path.expanduser(os.path.expandvars(os.environ.get("FORGE_HOME", "")))
    for c in ([env] if env else []) + [str(Path.home() / "code/forge"), "/root/forge"]:
        try:
            if (Path(c) / CARDSFOLDER).is_dir():
                return Path(c)
        except OSError:  # a folder this user cannot read is not a checkout
            continue
    return None


def real_card_index(forge):
    """{card name: script path relative to the cardsfolder} for every face of every script, the twins left out (a
    twin installed earlier is not a real card, and counting it would make the names depend on the last install)."""
    root = Path(forge) / CARDSFOLDER
    twins = Path(forge) / TWINS_DIR
    index = {}
    for p in sorted(root.rglob("*.txt")):
        if twins in p.parents or any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Name:"):
                index.setdefault(line[5:].strip(), p.relative_to(root).as_posix())
    return index


def engine_names(forge, known=None):
    """Card names that appear as a string literal in a non-comment line of Forge's engine, AI, core or gui source.
    Over-inclusive on purpose (keywords such as Flash, banned-list cards): a false entry only refuses a twin."""
    known = set(real_card_index(forge)) if known is None else set(known)
    lit = re.compile(r'"((?:[^"\\]|\\.)*)"')
    found = set()
    for src in ENGINE_SRC:
        for p in sorted((Path(forge) / src).rglob("*.java")):
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if s.startswith(("//", "*", "/*")):
                    continue
                found.update(m.group(1) for m in lit.finditer(line) if m.group(1) in known)
    return found


# ------------------------------------------------------------------ names

def twin_name(original, seed, attempt=0):
    """A pronounceable invented name with the original's word count (one to three), fixed by (seed, original,
    attempt): the same card gets the same twin on every board of a suite."""
    rng = random.Random(int(_sha("%s\0%s\0%d" % (seed, original, attempt)), 16))
    words = []
    for _ in range(min(3, max(1, len(original.split())))):
        n = rng.choice((2, 2, 3))
        w = "".join(rng.choice(ONSETS) + rng.choice(VOWELS) for _ in range(n)) + rng.choice(CODAS)
        words.append(w.capitalize())
    return " ".join(words)


def _prefix_clash(a, b):
    a, b = a.lower(), b.lower()
    return a != b and (a.startswith(b) or b.startswith(a))


def assign_twins(originals, seed, real_names, avoid=()):
    """{original: twin} for every name, in sorted order so the result does not depend on the order given. A draw
    that is a real card, taken, a prefix clash with a taken twin or a name in `avoid`, or that contains an original
    as a whole name, is drawn again with the next attempt."""
    real = {n.lower() for n in real_names}
    originals = sorted(set(originals))
    labels = set(avoid) | set(originals)
    out, taken = {}, set()
    for orig in originals:
        for attempt in range(1000):
            cand = twin_name(orig, seed, attempt)
            if (cand.lower() in real or cand.lower() in taken
                    or any(_prefix_clash(cand, t) for t in list(out.values()) + list(labels))
                    or any(_leak(o).search(cand) for o in originals)):
                continue
            out[orig] = cand
            taken.add(cand.lower())
            break
        else:
            raise TwinRefused("%s: no free twin name in 1000 draws" % orig)
    return out


def script_file(twin):
    """Forge's file naming: lower case, every other character an underscore."""
    return re.sub(r"[^a-z0-9]+", "_", twin.lower()).strip("_") + ".txt"


# ------------------------------------------------------------------ a card script

def twin_script(text, twin, others=()):
    """(twin script, edits): `text` with its own name replaced in Name:, Oracle:, the description parameters and
    its `named<Name>` / `Name$<Name>` self-references. `edits` lists each changed line (1-based) before and after.
    Refuses a script with two faces or a pair, one that names another card, and one where its name or a name in
    `others` (the originals sharing its boards) is left anywhere."""
    lines = text.split("\n")
    names = [ln[5:].strip() for ln in lines if ln.startswith("Name:")]
    if len(names) != 1 or any(ln.strip() == "ALTERNATE" for ln in lines):
        raise TwinRefused("%s: a card with more than one face is not twinned" % (names[0] if names else "?"))
    name = names[0]
    pat = _word_re(name)
    self_ref = re.compile(r"(named|Name\$)%s(?![A-Za-z0-9])" % re.escape(name))
    out, edits = [], []
    for i, line in enumerate(lines, 1):
        if _PAIRS.match(line):
            raise TwinRefused("%s: names a pair card: %s" % (name, line))
        if line.startswith("Name:"):
            new = "Name:" + twin
        elif line.startswith("Oracle:"):
            new = pat.sub(twin, line)
        else:
            new = self_ref.sub(lambda m: m.group(1) + twin, line)
            new = "|".join(pat.sub(twin, seg) if _DESC_PARAM.match(seg) else seg for seg in new.split("|"))
        if new != line:
            edits.append({"line": i, "before": line, "after": new})
        out.append(new)
    result = "\n".join(out)
    for m in _NAMED.finditer(result):
        rest = result[m.end():]
        if not (rest.startswith(twin) and not re.match(r"[A-Za-z0-9]", rest[len(twin):len(twin) + 1])):
            raise TwinRefused("%s: names another card: named %s" % (name, rest.split("\n")[0][:40]))
    for other in [name] + sorted(set(others) - {name}):
        for line in result.split("\n"):
            if _leak(other).search(line):
                raise TwinRefused("%s: %s is left in the twin script: %s" % (name, other, line))
    return result, edits


# ------------------------------------------------------------------ a puzzle

def _entry_names(key, entry):
    parts = [p.strip() for p in entry.split("|")]
    head = parts[0]
    if key.endswith(("precast", "putonstack")):
        head = head.split("->")[0].strip()
        left, _, right = head.rpartition(":")
        if left and re.match(r"^\w+$", right):
            head = left  # Name:SVar names one ability of the card
    names = []
    if head and not head.isdigit() and not head.startswith(("t:", "T:")):
        names.append(head)
    for p in parts[1:]:
        if p.startswith("NamedCard:"):
            names.append(p[len("NamedCard:"):].strip())
        elif p.startswith("MergedCards:"):
            names.extend(n.strip() for n in p[len("MergedCards:"):].split(",") if n.strip())
    return names


def board_names(pzl_text):
    """Every card name the [state] puts on the board, in any zone, with the names a card is given by NamedCard: or
    MergedCards:. Tokens made from token scripts (`t:`, `T:`) and card ids are not names."""
    names = set()
    for line in pf.parse_pzl(pzl_text).get("state", []):
        key, sep, value = line.partition("=")
        if sep and _ZONE_KEY.match(key.strip().lower()):
            for entry in value.split(";"):
                if entry.strip():
                    names.update(_entry_names(key.strip().lower(), entry))
    return names


def check_board(pzl_text, engine_names, known_names):
    """The names on a board that would be twinned (every one but the basic lands), or TwinRefused naming the card
    Forge does not have or the engine names."""
    names = board_names(pzl_text) - KEEP_NAMES
    unknown = sorted(names - set(known_names))
    if unknown:
        raise TwinRefused("no Forge script for %s" % ", ".join(unknown))
    hard = sorted(names & set(engine_names))
    if hard:
        raise TwinRefused("the engine names %s in its source" % ", ".join(hard))
    return names


def _rewrite_zone_value(key, value, mapping):
    """A zone list with a twinned card's |Set: and |Art: dropped: the twin has one printing, in the twin set."""
    entries = []
    for entry in value.split(";"):
        names = _entry_names(key, entry) if entry.strip() else []
        if names and names[0] in mapping:
            entry = "|".join(p for p in entry.split("|") if not p.strip().startswith(("Set:", "Art:")))
        entries.append(entry)
    return ";".join(entries)


def twin_pzl(pzl_text, mapping, real_names=()):
    """`pzl_text` with every original in `mapping` renamed to its twin, wherever it is written outside the fixed
    keys (goal, turns, phase, life...): in any case in the Name: and Rule: labels, in the exact case elsewhere.
    Refuses two cards on one twin name, a twin that is a real card (`real_names`, any case), a twin that is a prefix
    of another label on the twin board or has one as its prefix, and any original left anywhere in the text (whole
    name, any case: a lower-case mention in the seat's text stops here)."""
    twins = [t.lower() for t in mapping.values()]
    if len(set(twins)) != len(twins):
        raise TwinRefused("two cards on one twin name: %s" % sorted(t for t in set(twins) if twins.count(t) > 1))
    real = {n.lower() for n in real_names}
    clash = sorted(t for o, t in mapping.items() if t.lower() in real and t.lower() != o.lower())
    if clash:
        raise TwinRefused("a twin name is a real card: %s" % ", ".join(clash))
    if not mapping:
        return pzl_text
    alt = "|".join(re.escape(o) for o in sorted(mapping, key=len, reverse=True))
    pat = re.compile(r"(?<![A-Za-z0-9])(%s)(?=(?:s|es)?(?![A-Za-z0-9]))" % alt)
    # Name: and Rule: are labels the seat is never shown, where a generator writes a card in lower case
    # (`Name:fling_05dc5d5cc564`, gen-v1): there any case is renamed; elsewhere only the exact name is.
    lower = {o.lower(): t for o, t in mapping.items()}
    loose = re.compile(pat.pattern, re.IGNORECASE)
    out, section = [], None
    for line in pzl_text.split("\n"):
        s = line.strip()
        m = re.match(r"^\[(\w+)\]$", s)
        if m:
            section = m.group(1).lower()
            out.append(line)
            continue
        key, sep, value = line.partition("=") if section == "state" else line.partition(":")
        k = key.strip().lower()
        if sep and not s.startswith("#") and ((section == "metadata" and k in _METADATA_FIXED)
                                              or (section == "state" and _STATE_FIXED.match(k))):
            out.append(line)
            continue
        if section == "state" and sep and _ZONE_KEY.match(k):
            line = key + sep + _rewrite_zone_value(k, value, mapping)
        if section == "metadata" and sep and k in _METADATA_LABELS:
            out.append(loose.sub(lambda mm: lower[mm.group(1).lower()], line))
        else:
            out.append(pat.sub(lambda mm: mapping[mm.group(1)], line))
    result = "\n".join(out)
    for orig in sorted(mapping):
        for line in result.split("\n"):
            if _leak(orig).search(line):
                raise TwinRefused("%s is left in the twin puzzle: %s" % (orig, line.strip()))
    labels = board_names(result)
    for t in sorted(set(mapping.values())):
        near = sorted(lb for lb in labels if _prefix_clash(t, lb))
        if near:
            raise TwinRefused("the twin %s is a prefix of, or has as prefix, the label %s" % (t, ", ".join(near)))
    return result


# ------------------------------------------------------------------ a suite

def make_twins(suite, ids, forge, out, seed, repo=REPO):
    """Write the twin of each puzzle `ids` names in `suite` to `out`/pzl/<id>-twin.pzl and the manifest to
    `out`/twin-manifest.json; returns the manifest. A board that cannot be twinned is listed under `refused` with
    the reason, and its cards are left out of the manifest unless another board uses them."""
    recs = {r["id"]: r for r in suite.get("puzzles", []) if isinstance(r, dict)}
    missing = [i for i in ids if i not in recs]
    if missing:
        raise TwinRefused("not in the suite: %s" % ", ".join(missing))
    forge, out = Path(forge), Path(out)
    index = real_card_index(forge)
    engine = engine_names(forge, index)
    texts, boards, refused = {}, {}, []
    for pid in ids:
        texts[pid] = pf.pzl_path(recs[pid], repo).read_text(encoding="utf-8")
        try:
            boards[pid] = check_board(texts[pid], engine, index)
        except TwinRefused as e:
            refused.append({"id": pid, "reason": str(e)})
    every = set().union(*boards.values()) if boards else set()
    mapping = assign_twins(every, seed, real_names=index, avoid=KEEP_NAMES)
    cards, bad = {}, {}
    for name in sorted(every):
        cobo = set().union(*(b for b in boards.values() if name in b)) - {name}
        raw = (forge / CARDSFOLDER / index[name]).read_bytes()
        try:
            text, edits = twin_script(raw.decode("utf-8"), mapping[name], others=cobo)
        except TwinRefused as e:
            bad[name] = str(e)
            continue
        cards[name] = {"original": name, "twin": mapping[name], "script": index[name], "original_sha256": _sha(raw),
                       "twin_sha256": _sha(text), "edits": edits}
    puzzles, used = [], set()
    (out / "pzl").mkdir(parents=True, exist_ok=True)
    for pid in ids:
        if pid not in boards:
            continue
        names = boards[pid]
        why = [bad[n] for n in sorted(names) if n in bad]
        try:
            if why:
                raise TwinRefused("; ".join(why))
            twin = twin_pzl(texts[pid], {n: mapping[n] for n in names}, real_names=index)
        except TwinRefused as e:
            refused.append({"id": pid, "reason": str(e)})
            continue
        rel = "pzl/%s-twin.pzl" % pid
        (out / rel).write_text(twin, encoding="utf-8")
        used |= names
        puzzles.append({"id": pid, "twin_id": pid + "-twin", "pzl": rel, "original_pzl": recs[pid].get("pzl"),
                        "original_pzl_sha256": _sha(texts[pid]), "twin_pzl_sha256": _sha(twin),
                        "cards": sorted(names)})
    pin = Path(repo) / "FORGE_COMMIT"
    manifest = {
        "schema": SCHEMA, "seed": seed, "forge_commit": pin.read_text().split()[0] if pin.is_file() else None,
        "suite": suite.get("suite"), "suite_sha256": suite.get("suite_sha256"),
        "edition": {"code": EDITION_CODE, "file": EDITION_FILE},
        "cards": [cards[n] for n in sorted(used)], "puzzles": puzzles,
        "refused": sorted(refused, key=lambda r: ids.index(r["id"]))}
    (out / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def install(manifest, forge):
    """Write each twin script, regenerated from the checkout's original, to <forge>/TWINS_DIR, remove the twins no
    longer in the manifest, and write the edition file; returns the paths written. Every card is checked before
    anything is written: an original whose sha256 moved, a twin that comes out different, or a twin name that is
    now a real card stops it with TwinRefused naming the card."""
    forge = Path(forge)
    index = real_card_index(forge)
    real = {n.lower() for n in index}
    planned = {}
    for c in manifest.get("cards", []):
        src = forge / CARDSFOLDER / c["script"]
        if not src.is_file():
            raise TwinRefused("%s: no original at %s" % (c["original"], src))
        raw = src.read_bytes()
        if _sha(raw) != c["original_sha256"]:
            raise TwinRefused("%s: the original at %s changed since the manifest" % (c["original"], c["script"]))
        text, _ = twin_script(raw.decode("utf-8"), c["twin"])
        if _sha(text) != c["twin_sha256"]:
            raise TwinRefused("%s: the twin %s comes out different" % (c["original"], c["twin"]))
        if c["twin"].lower() in real:
            raise TwinRefused("%s: the twin %s is a real card in %s" % (c["original"], c["twin"], forge))
        planned[script_file(c["twin"])] = text
    folder = forge / TWINS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for old in sorted(folder.glob("*.txt")):
        if old.name not in planned:
            old.unlink()
    written = []
    for fname, text in sorted(planned.items()):
        (folder / fname).write_text(text, encoding="utf-8")
        written.append(folder / fname)
    twins = sorted(c["twin"] for c in manifest.get("cards", []))
    edition = forge / EDITION_FILE
    edition.parent.mkdir(parents=True, exist_ok=True)
    edition.write_text(EDITION + "".join("%d C %s\n" % (i, t) for i, t in enumerate(twins, 1)), encoding="utf-8")
    written.append(edition)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make", help="write twin boards and a manifest under runs/")
    mk.add_argument("--suite", required=True)
    mk.add_argument("--ids", required=True, help="comma-separated puzzle ids")
    mk.add_argument("--seed", type=int, default=1)
    mk.add_argument("--out", required=True, help="a directory under runs/")
    mk.add_argument("--forge", help="the Forge checkout to read (default: FORGE_HOME, ~/code/forge, /root/forge)")
    ins = sub.add_parser("install", help="write the twin scripts and edition into a Forge checkout")
    ins.add_argument("--manifest", required=True)
    ins.add_argument("--forge", required=True, help="the checkout to write into (no default, on purpose)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "make":
            out = Path(a.out).resolve()
            runs = (REPO / "runs").resolve()
            if runs != out and runs not in out.parents:
                ap.error("--out must be under %s: twins of third-party puzzles are never committed" % runs)
            forge = Path(a.forge) if a.forge else forge_home()
            if forge is None or not (forge / CARDSFOLDER).is_dir():
                ap.error("no Forge cardsfolder found; pass --forge")
            ids = [i.strip() for i in a.ids.split(",") if i.strip()]
            m = make_twins(pf.load_suite(a.suite), ids, forge, out, a.seed)
            print("%d twin puzzles, %d cards, %d refused -> %s" % (len(m["puzzles"]), len(m["cards"]),
                                                                  len(m["refused"]), out / MANIFEST))
            for r in m["refused"]:
                print("REFUSED %s: %s" % (r["id"], r["reason"]))
        else:
            written = install(json.loads(Path(a.manifest).read_text(encoding="utf-8")), a.forge)
            print("wrote %d files under %s" % (len(written), a.forge))
    except TwinRefused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
