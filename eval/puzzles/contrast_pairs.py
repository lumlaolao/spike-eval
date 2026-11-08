#!/usr/bin/env python3
"""Contrast pairs of splice puzzles: two boards one or two visible details apart whose winning lines differ.

A pair separates reading the board from a remembered pattern only when no single line wins both sides: then a model
that plays the same line on both fails one. This script takes splice boards (puzzle_gen.splice_board, and the
splice2 boards whose cards are all in puzzle_gen.SPLICE_CARDS), perturbs each in one or two details (the opponent's
life, a Mountain more, fewer or tapped, Thermo-Alchemist tapped, absent or added, a card added, removed or swapped
for another), and asks the board's model (puzzle_gen.splice_best, the model step_regret_board.py engine-witnessed)
for each pair:

  - both sides winnable;
  - no shared winning line: a line is the sequence of spells cast, each with its splice set (Thermo-Alchemist's
    pings are free and taken before every spell, so they are each board's own and not part of the line);
  - the divergence: walk both boards with the same spells while both stay winnable; a position where no common
    spell keeps both winnable is where the lines must part. Its trap kind: "both" when a winning spell of each side
    is castable on the other and loses there, "a_on_b" or "b_on_a" when only one side's is, "none" when each
    side's winning spells cannot even be cast on the other (a contrast only by a missing card).

A pair is "two-way" when every divergence traps both ways, "one-way" when every divergence traps at least one way,
"partial" when some do, "none" otherwise. Within the model, more of any resource at the same life (a Mountain, an
untapped Alchemist, a card) keeps every winning line of the smaller board, so such a change alone never contrasts;
it needs the opponent's life moved with it (two details). The model's assumptions are step_regret_board.py's.

  python3 eval/puzzles/contrast_pairs.py eval/puzzles/hard/gen/splice_*.pzl eval/puzzles/hard/gen2/splice2_*.pzl \
      --out runs/contrast/pairs.jsonl --select 10 --write eval/puzzles/contrast --scripts runs/contrast/scripts
"""
import argparse
import collections
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import puzzle_gen as pg  # noqa: E402

VERSION = "contrast-pairs-v1"
CARDS = pg.SPLICE_CARDS
STOP = ("stop", ())            # the line ends: the opponent is already dead after the last ping
TRAP_ORDER = {"two-way": 0, "one-way": 1, "partial": 2, "none": 3}
DIVERGENCE_ORDER = {"both": 0, "a_on_b": 1, "b_on_a": 1, "none": 2}
WORDS = ["no", "one", "two", "three", "four", "five"]

Board = collections.namedtuple("Board", "hand mountains tapped alch untapped opp")


class NotModeled(Exception):
    """The board holds something the splice model does not cover."""


# ------------------------------------------------------------------ boards

def read_board(text):
    """The Board of a .pzl's [state]: hand (sorted), untapped and tapped Mountains, Thermo-Alchemist (and untapped),
    the opponent's life. Anything else on either side is refused."""
    kv = {}
    section = None
    for ln in text.splitlines():
        if ln.startswith("["):
            section = ln.strip()
        elif section == "[state]" and "=" in ln:
            k, v = ln.split("=", 1)
            kv[k.strip()] = v.strip()
    hand = [c for c in kv.get("p0hand", "").split(";") if c]
    for c in hand:
        if c not in CARDS:
            raise NotModeled("card %r" % c)
    perms = [x for x in kv.get("p0battlefield", "").split(";") if x]
    for x in perms:
        if x not in ("Mountain", "Mountain|Tapped", "Thermo-Alchemist", "Thermo-Alchemist|Tapped"):
            raise NotModeled("permanent %r" % x)
    for k in ("p1hand", "p1battlefield", "p0graveyard", "p0exile"):
        if kv.get(k):
            raise NotModeled("%s=%s" % (k, kv[k]))
    alch = "Thermo-Alchemist" in perms or "Thermo-Alchemist|Tapped" in perms
    return Board(tuple(sorted(hand)), perms.count("Mountain"), perms.count("Mountain|Tapped"), alch,
                 "Thermo-Alchemist" in perms, int(kv["p1life"]))


def start(b):
    """A model position: (hand, mana, Alchemist untapped, damage dealt)."""
    return (b.hand, b.mountains, b.alch and b.untapped, 0)


def moves(s):
    """Every spell castable at position s, each with every splice set it can pay for (any copies, CR 702.47b)."""
    hand, mana = s[0], s[1]
    out = []
    for c in sorted(set(hand)):
        g, r, _, _, arcane, _, _, _, _ = CARDS[c]
        rest = list(hand)
        rest.remove(c)
        for sp in (pg.splice_sets(rest, lambda x: CARDS[x][6] is not None) if arcane else [()]):
            if g + r + sum(CARDS[x][6] for x in sp) <= mana:
                out.append((c, sp))
    return out


def step(b, s, move):
    """The position after a ping (if the Alchemist is untapped) and `move` resolves."""
    hand, mana, u, dealt = s
    c, sp = move
    g, r, add, dmg, _, _, _, _, _ = CARDS[c]
    rest = list(hand)
    rest.remove(c)
    cost = g + r + sum(CARDS[x][6] for x in sp)
    return (tuple(rest), mana - cost + add + sum(CARDS[x][7] for x in sp), b.alch,
            dealt + (1 if b.alch and u else 0) + dmg + sum(CARDS[x][8] for x in sp))


def done(b, s):
    """The opponent is dead once the last ping resolves: the line may stop here."""
    return s[3] + (1 if b.alch and s[2] else 0) >= b.opp


@lru_cache(maxsize=None)
def _reach(hand, mana, alch, u):
    (dmg, _, _), _ = pg.splice_best(list(hand), mana, alch, untapped=u)
    return dmg


def best(b, s=None):
    """The most damage the board can deal from position s (the start by default)."""
    s = s or start(b)
    return s[3] + _reach(s[0], s[1], b.alch, s[2])


def winnable(b, s=None):
    return best(b, s) >= b.opp


def winning_moves(b, s):
    """The moves at s that keep the win, STOP among them when the opponent is already dead."""
    out = {m for m in moves(s) if winnable(b, step(b, s, m))}
    return out | {STOP} if done(b, s) else out


# ------------------------------------------------------------------ pairs

def divergences(a, b):
    """None when one line wins both boards; else every joint position (reached by the same spells, both still
    winnable) where no common spell keeps both winnable: dicts of the prefix, each side's winning moves, and the
    trap kind."""
    seen, out = set(), []

    def go(sa, sb, prefix):
        if (sa, sb) in seen:
            return False
        seen.add((sa, sb))
        wa, wb = winning_moves(a, sa), winning_moves(b, sb)
        common = wa & wb
        if STOP in common:
            return True
        if not common:
            la, lb = set(moves(sa)) | {STOP}, set(moves(sb)) | {STOP}
            on_b, on_a = bool(wa & lb), bool(wb & la)
            trap = "both" if on_a and on_b else "a_on_b" if on_b else "b_on_a" if on_a else "none"
            out.append({"prefix": prefix, "win_a": sorted(wa), "win_b": sorted(wb), "trap": trap})
            return False
        return any(go(step(a, sa, m), step(b, sb, m), prefix + (m,)) for m in sorted(common))

    if not (winnable(a) and winnable(b)):
        raise ValueError("both sides must be winnable")
    return None if go(start(a), start(b), ()) else out


def classify(a, b):
    """The pair's verdict: contrast (no shared winning line), its trap kind and the shallowest divergence."""
    div = divergences(a, b)
    if div is None:
        return {"contrast": False}
    traps = [d["trap"] for d in div]
    kind = ("two-way" if all(t == "both" for t in traps) else "one-way" if "none" not in traps
            else "partial" if any(t != "none" for t in traps) else "none")
    first = min(div, key=lambda d: (len(d["prefix"]), DIVERGENCE_ORDER[d["trap"]]))
    return {"contrast": True, "trap": kind, "depth": len(first["prefix"]), "divergences": len(div),
            "divergence": first}


def perturbations(b):
    """(name, details, cost, board): every one-detail change of b, and every resource change or card swap with the
    opponent's life moved by 1 to 3 (two details). The cost ranks how small the change looks: a life step of 1 is 1,
    a card swap is 1 when the two cards differ only in being Arcane (Lava Spike, Lightning Bolt), else 2."""
    life = lambda x, d: ("opponent at %d" % (x.opp + d), x._replace(opp=x.opp + d))
    out = []
    for d in (1, 2, 3, -1, -2, -3):
        n, x = life(b, d)
        out.append((n, 1, abs(d), x))
    resources = [("one more Mountain", +1, b._replace(mountains=b.mountains + 1))]
    if b.mountains:
        resources += [("one Mountain fewer", -1, b._replace(mountains=b.mountains - 1)),
                      ("a Mountain tapped", -1, b._replace(mountains=b.mountains - 1, tapped=b.tapped + 1))]
    if b.alch and b.untapped:
        resources.append(("Thermo-Alchemist tapped", -1, b._replace(untapped=False)))
    if b.alch:
        resources.append(("no Thermo-Alchemist", -1, b._replace(alch=False, untapped=False)))
    else:
        resources.append(("Thermo-Alchemist added", +1, b._replace(alch=True, untapped=True)))
    for c in sorted(set(b.hand)):
        rest = list(b.hand)
        rest.remove(c)
        resources.append(("one %s fewer" % c, -1, b._replace(hand=tuple(sorted(rest)))))
    for c in sorted(CARDS):
        resources.append(("one more %s" % c, +1, b._replace(hand=tuple(sorted(b.hand + (c,))))))
    for name, sign, x in resources:
        out.append((name, 1, 1, x))
        for d in (1, 2, 3):
            n, y = life(x, sign * d)
            out.append(("%s; %s" % (name, n), 2, 1 + d, y))
    for c in sorted(set(b.hand)):
        for y in sorted(CARDS):
            if y == c:
                continue
            rest = list(b.hand)
            rest.remove(c)
            x = b._replace(hand=tuple(sorted(rest + [y])))
            cost = 1 if {c, y} == {"Lava Spike", "Lightning Bolt"} else 2
            name = "%s -> %s" % (c, y)
            out.append((name, 1, cost, x))
            for d in (1, 2, 3, -1, -2, -3):
                n, z = life(x, d)
                out.append(("%s; %s" % (name, n), 2, cost + abs(d), z))
    return out


# ------------------------------------------------------------------ lines and puzzles

def script(b, line):
    """The line written for the script arm on board b (puzzle_gen.written_line, a ping before every spell the
    Alchemist is untapped for and one at the end): {"script", "dealt"}, or None when b cannot play it."""
    items, u = [], b.alch and b.untapped
    for c, sp in line:
        if u:
            items.append("ping")
        items.append(("cast", c, tuple(sp)))
        u = b.alch
    if u:
        items.append("ping")
    try:
        lines, dealt, _ = pg.written_line(items, list(b.hand), b.mountains, b.alch, False, False,
                                          alch_untapped=b.untapped)
    except ValueError:
        return None
    return {"script": lines, "dealt": dealt, "line": [[c, list(sp)] for c, sp in line]}


def best_line(b, s):
    """The model's best line from s (puzzle_gen.splice_best), as moves."""
    (_, _, line), _ = pg.splice_best(list(s[0]), s[1], b.alch, untapped=s[2])
    return [(x[0], tuple(x[1])) for x in line if x]


def _side_line(x, y, prefix, win_x):
    """x's winning line through the divergence: the prefix, a winning move of x's there (one castable on y first),
    then x's best line. With it, the cross line for y: the same prefix and move, then y's best line from there (the
    most y can deal once it has made x's move), or None when y cannot make that move."""
    sx, sy = _walk(x, prefix), _walk(y, prefix)
    legal_y = set(moves(sy)) | {STOP}
    m = sorted(win_x, key=lambda m: (m not in legal_y, m))[0]
    own = list(prefix) + ([] if m == STOP else [m] + best_line(x, step(x, sx, m)))
    if m not in legal_y:
        return own, None
    cross = list(prefix) + ([] if m == STOP else [m] + best_line(y, step(y, sy, m)))
    return own, cross


def _walk(b, prefix):
    s = start(b)
    for m in prefix:
        s = step(b, s, m)
    return s


def pair_lines(a, b):
    """Each side's line through the shallowest divergence, written for its own board (a_on_a, b_on_b: each must
    win), and the cross lines: a_on_b is A's prefix and winning move made on B, then B's best line (it must fall
    short), b_on_a the same the other way; None when the other board cannot make that move."""
    d = classify(a, b)["divergence"]
    la, a_on_b = _side_line(a, b, d["prefix"], d["win_a"])
    lb, b_on_a = _side_line(b, a, d["prefix"], d["win_b"])
    return {"a_on_a": script(a, la), "b_on_b": script(b, lb), "a_on_b": a_on_b and script(b, a_on_b),
            "b_on_a": b_on_a and script(a, b_on_a)}


def describe(b):
    perms = []
    if b.alch:
        perms.append("Thermo-Alchemist" if b.untapped else "a tapped Thermo-Alchemist")
    perms.append("%s untapped Mountain%s" % (WORDS[b.mountains], "" if b.mountains == 1 else "s"))
    if b.tapped:
        perms.append("%s tapped Mountain%s" % (WORDS[b.tapped], "" if b.tapped == 1 else "s"))
    return ("It is your second main phase. You are at 20 life and control %s. Your hand is %s. The opponent is at %d "
            "life and controls nothing. Reduce the opponent to 0 life this turn."
            % (pg._join(perms), pg._join(b.hand) if b.hand else "empty", b.opp))


def pzl(b, name, solution, rule="contrast-splice"):
    """The board as a puzzle file, in puzzle_gen.splice_board's form (tapped permanents carry |Tapped)."""
    battlefield = ((["Thermo-Alchemist" + ("" if b.untapped else "|Tapped")] if b.alch else [])
                   + ["Mountain"] * b.mountains + ["Mountain|Tapped"] * b.tapped)
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN2"), ("p0life", 20),
             ("p0hand", ";".join(b.hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", b.opp), ("p1library", "Plains;Plains;Plains")]
    return pg.pzl_text(name, describe(b), rule, state, solution, ["life p1 <= 0"])


# ------------------------------------------------------------------ the run

def _fmt(m):
    if m == STOP:
        return "stop"
    c, sp = m
    return c + (" spliced with " + " + ".join(sp) if sp else " alone" if CARDS[c][4] else "")


def evaluate(paths):
    """Every base board's perturbations with their verdicts: (rows, refused bases)."""
    rows, refused = [], []
    for p in paths:
        base = Path(p).stem
        try:
            a = read_board(Path(p).read_text(encoding="utf-8"))
        except NotModeled as e:
            refused.append({"base": base, "refused": str(e)})
            continue
        if not winnable(a):
            refused.append({"base": base, "refused": "the model calls the base lost"})
            continue
        for name, details, cost, b in perturbations(a):
            row = {"version": VERSION, "base": base, "path": str(p), "opp_a": a.opp, "change": name, "details": details,
                   "cost": cost, "board_b": b._asdict(), "winnable_b": winnable(b)}
            if row["winnable_b"]:
                v = classify(a, b)
                row["contrast"] = v["contrast"]
                if v["contrast"]:
                    d = v["divergence"]
                    row.update(trap=v["trap"], depth=v["depth"], divergences=v["divergences"],
                               prefix=[_fmt(m) for m in d["prefix"]], win_a=[_fmt(m) for m in d["win_a"]],
                               win_b=[_fmt(m) for m in d["win_b"]], first_trap=d["trap"])
            rows.append(row)
    return rows, refused


def rank_key(r):
    return (r["details"], TRAP_ORDER[r["trap"]], r["depth"], r["cost"], r["base"], r["change"])


def select(rows, n):
    """The n best-ranked qualifying pairs (trap not "none"), one per base board, a change not yet taken first,
    alternating one- and two-detail pairs so both are sampled."""
    q = sorted((r for r in rows if r.get("contrast") and r["trap"] != "none"), key=rank_key)
    pools = [[r for r in q if r["details"] == k] for k in (1, 2)]
    out, bases, changes = [], set(), set()
    while len(out) < n and any(pools):
        for pool in pools:
            fresh = [r for r in pool if r["base"] not in bases]
            pick = next((r for r in fresh if r["change"] not in changes), fresh[0] if fresh else None)
            pool[:] = [] if pick is None else [r for r in pool if r is not pick]
            if pick is not None:
                bases.add(pick["base"])
                changes.add(pick["change"])
                out.append(pick)
            if len(out) >= n:
                break
    return out


def write_pair(r, k, pzl_dir, script_dir):
    """The pair's two puzzles (each with its own winning line as [solution]) and the four scripts."""
    a = read_board(Path(r["path"]).read_text(encoding="utf-8"))
    b = Board(**r["board_b"])
    lines = pair_lines(a, b)
    pid = "contrast_%02d" % k
    out = {}
    for side, board, own in (("a", a, "a_on_a"), ("b", b, "b_on_b")):
        name = "%s%s" % (pid, side)
        rule = "contrast-splice | pair %s side %s of %s (%s)" % (pid, side, r["base"], r["change"])
        text = pzl(board, name, lines[own]["script"], rule)
        if pzl_dir:
            Path(pzl_dir).mkdir(parents=True, exist_ok=True)
            (Path(pzl_dir) / (name + ".pzl")).write_text(text, encoding="utf-8")
        out[side] = name
    if script_dir:
        Path(script_dir).mkdir(parents=True, exist_ok=True)
        for key, val in lines.items():
            if val is not None:
                (Path(script_dir) / ("%s-%s.txt" % (pid, key))).write_text("\n".join(val["script"]) + "\n")
    return dict(r, pair=pid, lines={k2: (v and {"dealt": v["dealt"], "line": v["line"]}) for k2, v in lines.items()})


def summary(rows, refused):
    c = collections.Counter()
    for r in rows:
        k = r["details"]
        c["tried", k] += 1
        c["both winnable", k] += r["winnable_b"]
        c["contrast", k] += bool(r.get("contrast"))
        if r.get("contrast"):
            c[r["trap"], k] += 1
    bases = len({r["base"] for r in rows})
    lines = ["%d base boards modelled, %d refused" % (bases, len(refused))]
    for k in (1, 2):
        lines.append("%d detail(s): %d tried, %d both winnable, %d no shared winning line; two-way %d, one-way %d, "
                     "partial %d, none %d" % (k, c["tried", k], c["both winnable", k], c["contrast", k],
                                              c["two-way", k], c["one-way", k], c["partial", k], c["none", k]))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pzl", nargs="+", help="base splice boards")
    ap.add_argument("--out", default=None, help="JSONL of every pair tried")
    ap.add_argument("--select", type=int, default=0, help="write the N best-ranked qualifying pairs")
    ap.add_argument("--write", default=None, help="directory for the selected pairs' .pzl files")
    ap.add_argument("--scripts", default=None, help="directory for each pair's four script files")
    a = ap.parse_args(argv)
    rows, refused = evaluate(a.pzl)
    for ln in summary(rows, refused):
        print(ln)
    for r in refused:
        print("refused %s: %s" % (r["base"], r["refused"]))
    chosen = [write_pair(r, k + 1, a.write, a.scripts) for k, r in enumerate(select(rows, a.select))]
    for r in chosen:
        ln = r["lines"]
        print("%s %s | %s | %s depth %d | A: %s | B: %s | A-line on B deals %s of %d | B-line on A %s of %d" % (
            r["pair"], r["base"], r["change"], r["trap"], r["depth"], "; ".join(r["win_a"]), "; ".join(r["win_b"]),
            ln["a_on_b"]["dealt"] if ln["a_on_b"] else "unplayable", r["board_b"]["opp"],
            ("deals %d" % ln["b_on_a"]["dealt"]) if ln["b_on_a"] else "unplayable", r["opp_a"]))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            for r in rows + refused + [dict(r, selected=True) for r in chosen]:
                f.write(json.dumps(r) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
