#!/usr/bin/env python3
"""Is a deck legal in a format, by the pinned engine's own data?

    python3 eval/deck_legality.py --format modern eval/decks/modern-*.txt

A format is read from the Forge checkout ($FORGE_HOME, default ~/code/forge or /root/forge):
`forge-gui/res/formats/Sanctioned/<Format>.txt` gives its legal sets (Sets:), its rarities (Rarities:, for
Pauper), its banned and restricted cards and any additional cards; `forge-gui/res/editions/*.txt` gives
every printing (set code, rarity, name). A card is legal when it is not banned and has a printing in a legal
set, or at a legal rarity where the format names rarities. Basic lands are always legal. Exit 1 when any deck
is not legal, with the reason for each card. Stdlib only.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
          "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp", "Snow-Covered Mountain",
          "Snow-Covered Forest"}
CARD_LINE = re.compile(r"^\S+\s+([A-Z])\s+(.+?)(?:\s+@.*)?$")
DECK_LINE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


PIN_FILE = Path(__file__).resolve().parent.parent / "FORGE_COMMIT"


def forge_home():
    """The Forge checkout: $FORGE_HOME, ~/code/forge or /root/forge, the first with a formats folder. It must be at
    the pinned commit (FORGE_COMMIT), whose data the verdicts are about (critique A41)."""
    for c in (os.path.expanduser(os.path.expandvars(os.environ.get("FORGE_HOME", ""))), str(Path.home() / "code/forge"),
              "/root/forge"):
        try:
            if c and (Path(c) / "forge-gui/res/formats").is_dir():
                check_pin(Path(c))
                return Path(c)
        except OSError:  # a candidate this user cannot read (/root/forge for a non-root user) is not a checkout
            continue
    raise SystemExit("no Forge checkout (set FORGE_HOME)")


def check_pin(forge, pin_file=PIN_FILE):
    """Refuse a checkout at another commit than the pin; say so when its commit cannot be read."""
    try:
        pin = pin_file.read_text(encoding="utf-8").strip()
    except OSError:
        return
    try:
        head = subprocess.run(["git", "-C", str(forge), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = ""
    if not head:
        print("deck_legality: cannot read the commit of %s; its data is taken as the pin's (%s)" % (forge, pin[:12]),
              file=sys.stderr)
    elif head != pin:
        raise SystemExit("the Forge checkout %s is at %s, not the pin %s (FORGE_COMMIT): its format data may differ"
                         % (forge, head[:12], pin[:12]))


def read_format(forge, name):
    path = forge / "forge-gui/res/formats/Sanctioned" / ("%s.txt" % name.capitalize())
    if not path.is_file():
        raise SystemExit("no format file %s" % path)
    f = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line and not line.startswith("["):
            k, v = line.split(":", 1)
            f[k.strip()] = v.strip()

    def items(key, sep):
        return {x.strip() for x in f.get(key, "").split(sep) if x.strip()}
    return {"name": f.get("Name", name), "sets": items("Sets", ","), "rarities": items("Rarities", ","),
            "banned": items("Banned", ";"), "restricted": items("Restricted", ";"),
            "additional": items("Additional", ";")}


def read_printings(forge):
    """{card name: {(set code, rarity letter), ...}} from every edition file."""
    printings = {}
    for ed in (forge / "forge-gui/res/editions").glob("*.txt"):
        # Card lines ("267 U Counterspell @artist") sit in [cards] and in art sections ([showcase],
        # [extended art]): MH2's Counterspell is only in the latter. Booster sections hold "Key=value" lines.
        code, in_meta = None, False
        for line in ed.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Code=") and code is None:
                code = line[5:].strip()
            elif line.startswith("["):
                in_meta = line.strip().lower() == "[metadata]"
            elif not in_meta and code and "=" not in line:
                m = CARD_LINE.match(line.strip())
                if m:
                    printings.setdefault(m.group(2).strip(), set()).add((code, m.group(1)))
    return printings


# The formats with real top-8 lists in eval/decks, each deck named "<format>-<archetype>.txt" (SOURCES.md). A
# deck with one of these prefixes is not in the original 16-deck Legacy eval pool, legacy- included.
FORMAT_PREFIXES = ("legacy", "modern", "pioneer", "standard", "pauper", "vintage")


def is_format_deck(stem):
    return stem.split("-", 1)[0] in FORMAT_PREFIXES


def parse_deck(text):
    """([(count, name)] main, [(count, name)] sideboard), one per line as written. The grammar LlmSpikeGui.deckEntries
    reads too (eval/tests/deck_grammar_cases.json pins both): "N Name" lines; a line starting "#" is a comment; a
    "Sideboard" line, or the first blank line after a card, starts the sideboard; any other line is refused."""
    main, side, in_side, marked = [], [], False, False
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("#"):
            continue
        if line.rstrip(":").lower() == "sideboard":
            if marked:
                raise ValueError("line %d: a second Sideboard line" % i)
            in_side = marked = True
            continue
        if not line:
            in_side = in_side or bool(main)
            continue
        m = DECK_LINE.match(line)
        if not m:
            raise ValueError("line %d: %r is not \"<count> <card name>\"" % (i, line))
        (side if in_side else main).append((int(m.group(1)), m.group(2)))
    return main, side


def read_deck(path):
    """[(count, name)] of the main deck, a card's lines summed (critique A41: two lines of one card were two)."""
    try:
        main, _ = parse_deck(Path(path).read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit("%s: %s" % (path, e))
    counts = {}
    for n, name in main:
        counts[name] = counts.get(name, 0) + n
    return [(n, name) for name, n in counts.items()]


def read_copy_limits(forge):
    """{card name: the copies a deck may hold (None: any number)} for the cards whose own keyword lifts the four-copy
    rule ("K:A deck can have any number of cards named CARDNAME.", "K:DeckLimit:7:..."), from the card scripts."""
    limits = {}
    for f in (forge / "forge-gui/res/cardsfolder").rglob("*.txt"):
        text = f.read_text(encoding="utf-8", errors="replace")
        if "A deck can have" not in text:
            continue
        name = next((ln[5:].strip() for ln in text.splitlines() if ln.startswith("Name:")), None)
        cap = re.search(r"^K:DeckLimit:(\d+):", text, re.M)
        if name and cap:
            limits[name] = int(cap.group(1))
        elif name and re.search(r"^K:A deck can have any number of cards named", text, re.M):
            limits[name] = None
    return limits


def problems(deck, fmt, printings, limits=None):
    out = []
    total = sum(n for n, _ in deck)
    if total < 60:
        out.append("%d cards in the main deck (60 or more)" % total)
    for n, name in deck:
        if name in BASICS:
            continue
        if name in fmt["banned"]:
            out.append("%s is banned in %s" % (name, fmt["name"]))
            continue
        if name in fmt["restricted"] and n > 1:
            out.append("%s is restricted in %s (%d copies)" % (name, fmt["name"], n))
        cap = (limits or {}).get(name, 4)
        if cap is not None and n > cap:
            out.append("%d copies of %s" % (n, name))
        if name in fmt["additional"]:
            continue
        ps = printings.get(name)
        if not ps:
            out.append("%s: no printing in the engine's editions (misspelt, or not in this engine)" % name)
            continue
        ok = any((not fmt["sets"] or code in fmt["sets"]) and (not fmt["rarities"] or rar in fmt["rarities"])
                 for code, rar in ps)
        if not ok:
            why = "no printing at %s" % "/".join(sorted(fmt["rarities"])) if fmt["rarities"] else "no printing in a legal set"
            out.append("%s: %s" % (name, why))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--format", required=True, help="legacy, modern, standard, pauper, ...")
    ap.add_argument("decks", nargs="+")
    a = ap.parse_args(argv)
    forge = forge_home()
    fmt = read_format(forge, a.format)
    printings = read_printings(forge)
    limits = read_copy_limits(forge)
    bad = 0
    for d in a.decks:
        p = problems(read_deck(d), fmt, printings, limits)
        print("%-44s %s" % (os.path.basename(d), "LEGAL in " + fmt["name"] if not p else "NOT LEGAL"))
        for x in p:
            print("    - " + x)
        bad += bool(p)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
