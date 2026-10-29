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
import sys
from pathlib import Path

BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
          "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp", "Snow-Covered Mountain",
          "Snow-Covered Forest"}
CARD_LINE = re.compile(r"^\S+\s+([A-Z])\s+(.+?)(?:\s+@.*)?$")
DECK_LINE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def forge_home():
    for c in (os.path.expanduser(os.path.expandvars(os.environ.get("FORGE_HOME", ""))), str(Path.home() / "code/forge"),
              "/root/forge"):
        try:
            if c and (Path(c) / "forge-gui/res/formats").is_dir():
                return Path(c)
        except OSError:  # a candidate this user cannot read (/root/forge for a non-root user) is not a checkout
            continue
    raise SystemExit("no Forge checkout (set FORGE_HOME)")


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


def read_deck(path):
    """[(count, name)] of the main deck; a 'Sideboard' line ends it."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip().lower().startswith("sideboard"):
            break
        m = DECK_LINE.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return out


def problems(deck, fmt, printings):
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
        if n > 4:
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
    bad = 0
    for d in a.decks:
        p = problems(read_deck(d), fmt, printings)
        print("%-44s %s" % (os.path.basename(d), "LEGAL in " + fmt["name"] if not p else "NOT LEGAL"))
        for x in p:
            print("    - " + x)
        bad += bool(p)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
