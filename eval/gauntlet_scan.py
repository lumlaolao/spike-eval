"""Reads a keyword gauntlet run (eval/keyword_gauntlet.sh) and lists every harness defect it shows.

Per game: exit code, turns, winner and the seat's [TALLY] line. Then every line that marks a defect: a choice
Forge's AI made (FALLBACK->AI, ai=N in the tally), an answer the seat could not read, a text parse or a text answer
format on a tool ask, a placeholder bridge asked (PLACEHOLDER-ASKED: a seat that asks no model reached one), a play
that failed to pay or to target (a failed target on a card only the opponent's deck holds, which the seat never played,
is listed apart, not counted), a decision lost, a crash, and (with GAUNTLET_TOOLS=1) a lookup the harness refused or
that broke (tool_audit's REFUSED and FAILED). A game with no game line or a non-zero exit also counts. Exits 1 when
anything is found.

usage: python3 eval/gauntlet_scan.py <run dir>
"""
import collections
import glob
import json
import re
import sys

import deck_legality
import tool_audit

# The engine's "failed to target" line names the card, not the player. It is set apart (reported, not counted) only
# when the card is in the Forge AI opponent's deck and not the seat's, by the decks the game log names, and the seat
# never played it (a stolen card is the seat's: Control Magic, Gonti): the AI's own play (runs/gauntlet-forecast-first
# g-kw27: Karlov's Crossbow). Anything else counts, a line the decks cannot attribute included: the seat fails a target
# on paths that log no seat line (an effect's cast, a trigger).
FAILED_TARGET = re.compile(r"^(?:STACK_ADD: )?(.+?) \(\d+\) - \[Couldn't add to stack, failed to target\]")
DECK_LINE = re.compile(r"^\[DECK\] loaded '([^']*)': .*\((.+)\)\s*$", re.M)
# A seat play names its card, a creature with its power/toughness, before the handle: "activates Canopy Crawler 2/2 @32".
SEAT_PLAY = re.compile(r"\[ROUTER\] play: (?:casts |activates |plays |land )?(.+?)(?: -?\d+/-?\d+)? @\d+")


def deck_cards(path):
    """The card names a deck file lists, or None when it cannot be read."""
    try:
        return {name for _, name in deck_legality.read_deck(path)}
    except OSError:
        return None


def opponent_only(log):
    """Card names only the Forge AI opponent's deck holds (its deck name ends "-ai") and the seat never played; empty
    when either deck is not named or cannot be read, so nothing is set apart."""
    seat, ai = None, None
    for name, path in DECK_LINE.findall(log):
        cards = deck_cards(path)
        if name.endswith("-ai"):
            ai = cards
        elif seat is None:
            seat = cards
    if ai is None or seat is None:
        return set()
    return ai - seat - {m.group(1) for m in SEAT_PLAY.finditer(log)}


BAD = re.compile(r"FALLBACK->AI|\(unparsed|TEXT-PARSE|TEXT-FORMAT-LEAK|PLACEHOLDER-ASKED|Couldn't add to stack"
                 r"|failed to target|FAILED to pay|Exception|Error\b|threw|AI looped")


def scan(root):
    findings = collections.Counter()
    others = collections.Counter()   # failed-target lines that are not a seat play: reported, not counted
    games = 0
    for d in sorted(glob.glob(root + "/g-*")):
        games += 1
        tag = d.rsplit("/", 1)[1]
        log = open(d + "/game.log", errors="replace").read()
        tally = re.findall(r"\[TALLY\][^\n]*", log)
        game = None
        for f in glob.glob(d + "/game-*.jsonl"):
            for line in open(f):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("t") == "game":
                    game = row
                if row.get("answer_via") == "lost":
                    findings["decision lost: %s" % row.get("type")] += 1
        tools = tool_audit.audit(glob.glob(d + "/game-*.jsonl"))
        for kind in ("REFUSED", "FAILED"):
            if tools["by_outcome"][kind]:
                findings["%s: lookup %s x%d, e.g. %s" % (tag, kind, tools["by_outcome"][kind],
                                                         tools["examples"].get(kind, "")[:160])] += 1
        exit_code = open(d + "/exit_code").read().strip() if glob.glob(d + "/exit_code") else "?"
        print(tag, "exit", exit_code, "turns", game and game.get("turns"), "winner", game and game.get("winner"),
              (tally[-1] if tally else "no tally")[:90])
        if exit_code != "0":
            findings["%s: exit %s" % (tag, exit_code)] += 1
        if game is None or game.get("winner") in (None, "invalid"):
            findings["%s: no finished game" % tag] += 1
        if tally and not re.search(r"\bai=0\b", tally[-1]):
            findings["%s: Forge AI decided for the seat (%s)" % (tag, tally[-1])] += 1
        theirs = opponent_only(log)
        for line in log.split("\n"):
            if BAD.search(line) and "JAVA_TOOL" not in line:
                line = line.strip()
                key = re.sub(r"\d+", "N", line)[:220]
                ft = FAILED_TARGET.match(line)
                if ft and ft.group(1) in theirs:
                    others["%s: %s" % (tag, key)] += 1
                else:
                    findings[key] += 1
    print()
    print("%d games, %d defect lines" % (games, sum(findings.values())))
    for k, n in findings.most_common():
        print(n, k)
    if others:
        print("not the seat's (a card only the Forge AI opponent's deck holds, never played by the seat), %d lines:" % sum(others.values()))
        for k, n in others.most_common():
            print(" ", n, k)
    return 0 if games and not findings else 1


if __name__ == "__main__":
    sys.exit(scan(sys.argv[1]))
