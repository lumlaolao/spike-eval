#!/usr/bin/env python3
"""Generated hard puzzles (docs/harness/HARD-PUZZLES.md section 3, "generated" mode).

Each family draws a board's numbers at random, computes its one line exactly, and writes a `.pzl` with that line
in its `[solution]` section, in the words the seat is shown (ScriptBridge.java). Nothing here decides that a board
is hard: `scripts/puzzle-gate/gen_gate.py` plays every written line in the engine and keeps a board only when the
line solves, it is 10 to 15 solver actions long, and every floor (random, PASS, first option, greedy, Forge's AI)
fails it.

Two wording rules the engine taught (ping_15, 2026-10-20): a choice with one legal answer (a single creature to
target, exactly two Mountains to sacrifice) is made by the engine and gets no line; a target that could be either
player is named `opponent`.

Families: ping (Thermo-Alchemist untaps between spells), storm (Grapeshot copies), splice (Arcane spells carrying
Desperate Ritual or Glacial Ray), red (all of these with flashback and Kiln Fiend), drain (sacrifice and recursion
loops with Blood Artist and friends), elf (Heritage Druid and Nettle Sentinel into Craterhoof Behemoth) and fling
(prowess creatures attack, then Fling after combat). The splice, red, drain, elf and fling boards set the opponent's
life to the exact maximum of an exhaustive search over a model of the board.

The second suite's families (eval/puzzles/hard/gen2) are 13 to 15 steps, set the life to a searched maximum and
keep a board only when a plain plan, searched to its own best, falls 1 to 3 short: copy (Reverberate, Fork and
Increasing Vengeance aimed at a ritual on the stack; plain: copy only burn) and grave (Cabal Ritual's threshold and
Songs of the Damned count the graveyard; plain: every mana spell first).

  python3 eval/puzzle_gen.py --family ping --count 40 --seed 1 --out DIR
"""
import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

MIN_ACTIONS, MAX_ACTIONS = 10, 15

# Instants and sorceries for the ping family: (name, generic cost, red cost, mana added, damage, target kind).
# Target kinds: "any" (players and creatures), "player", "creature", "none". Phyrexian spells cost nothing here:
# they are cast when no mana of their colour is floating, so the engine pays them with life and asks nothing.
RITUALS = {
    "Desperate Ritual": (1, 1, 3), "Pyretic Ritual": (1, 1, 3), "Rite of Flame": (0, 1, 2),
    "Seething Song": (2, 1, 5),
}
BURN = {"Lightning Bolt": (3, "any"), "Chain Lightning": (3, "any"), "Lava Spike": (3, "player"),
        "Shock": (2, "any")}
FREE = {"Gitaxian Probe": (0, "player"), "Mutagenic Growth": (0, "creature"), "Gut Shot": (1, "any")}
FIREBLAST_DAMAGE = 4


def _join(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def pzl_text(name, description, rule, state, solution, acceptance):
    lines = ["[metadata]", "Name:" + name, "Goal:Survive", "Turns:1", "Difficulty:Hard", "Description:" + description,
             "Rule:" + rule, "[state]"]
    lines += ["%s=%s" % kv for kv in state]
    lines += ["[solution]"] + solution + ["[acceptance]"] + acceptance
    return "\n".join(lines) + "\n"


def ping_board(rng):
    """Thermo-Alchemist untaps on every instant or sorcery, so a ping goes before each spell and one after the
    last. Mana is exact: the rituals open with the lands, the burn spends the pool to nothing, the Phyrexian spells
    follow (life, not mana), Fireblast last once both Mountains are tapped. Kiln Fiend (+3/+0 a spell) moves the
    whole line before combat. Returns None when the draw cannot make a legal board of the right length."""
    alchemists = rng.choice([1, 1, 1, 2])
    electromancer = rng.random() < 0.35
    kiln = rng.random() < 0.4
    fireblast = rng.random() < 0.5
    mountains = 2 if fireblast else rng.choice([1, 2, 2, 3])
    rituals = [rng.choice(sorted(RITUALS)) for _ in range(rng.choice([1, 1, 2]))]
    rituals.sort(key=lambda r: -(RITUALS[r][0] + RITUALS[r][1] - (1 if electromancer and RITUALS[r][0] else 0)))
    # Simulate the rituals from the lands: each must be payable from what is open.
    pool, lands, rites = 0, mountains, 0
    for r in rituals:
        generic, red, added = RITUALS[r]
        if r == "Rite of Flame":       # then {R} for each Rite of Flame already in a graveyard
            added += rites
            rites += 1
        if electromancer:
            generic = max(0, generic - 1)
        cost = generic + red
        if pool + lands < cost:
            return None
        use_lands = min(lands, max(0, cost - pool))
        lands -= use_lands
        pool -= cost - use_lands
        pool += added
    pool += lands                      # what lands are left pay burn
    if fireblast and lands:
        return None                    # a Mountain still untapped at Fireblast would be a choice (and slack)
    burn = [rng.choice(sorted(BURN)) for _ in range(pool)]
    free = rng.sample(sorted(FREE), rng.choice([0, 1, 1, 2, 3]))
    spells = rituals + burn + free + (["Fireblast"] if fireblast else [])
    k = len(spells)
    actions = k + alchemists * (k + 1) + (1 if kiln or electromancer else 0)
    if not MIN_ACTIONS <= actions <= MAX_ACTIONS:
        return None
    creatures = ["Thermo-Alchemist"] * alchemists + (["Goblin Electromancer"] if electromancer else []) \
        + (["Kiln Fiend"] if kiln else [])
    damage = alchemists * (k + 1) + sum(BURN[b][0] for b in burn) + sum(FREE[f][0] for f in free) \
        + (FIREBLAST_DAMAGE if fireblast else 0)
    attackers = []
    growth_on = None
    if kiln:
        damage += 1 + 3 * k
        attackers.append("Kiln Fiend")
        growth_on = "Kiln Fiend"
    if electromancer:
        damage += 2
        attackers.append("Goblin Electromancer")
        growth_on = growth_on or "Goblin Electromancer"
    if "Mutagenic Growth" in free and attackers:
        damage += 2                    # on an attacker; without one it only buys the untap
    herring = rng.random() < 0.5
    hand = spells[:]
    if herring:
        hand.append("Goblin Guide")    # costs a burn spell's red mana for 2 damage and no untap: a trap
    rng.shuffle(hand)
    solution = ["# steps: %d" % actions]
    for s in spells:
        solution += ["activate Thermo-Alchemist"] * alchemists
        solution.append("cast " + s)
        kind = BURN.get(s, FREE.get(s, (0, "any" if s == "Fireblast" else "none")))[1]
        if kind in ("any", "player"):
            solution.append("opponent")
        elif kind == "creature" and len(creatures) > 1:
            solution.append(growth_on or "Thermo-Alchemist")
        if kiln:
            # Two triggers at once are put in order by the seat (red_062a1ef04a5d): either order is the same here.
            solution.append("; ".join(["Thermo-Alchemist"] * alchemists + ["Kiln Fiend"]))
    solution += ["activate Thermo-Alchemist"] * alchemists
    if attackers:
        solution.append("; ".join(attackers))
    phase = "MAIN1" if attackers else "MAIN2"
    battlefield = creatures + ["Mountain"] * mountains
    desc = ("It is your %s main phase. You are at 20 life and control %s and %s untapped Mountain%s. Your hand is "
            "%s. The opponent is at %d life and controls nothing. Reduce the opponent to 0 life this turn."
            % ("first" if phase == "MAIN1" else "second", _join(creatures), ["", "one", "two", "three"][mountains],
               "" if mountains == 1 else "s", _join(hand), damage))
    rule = ("generated-ping | %d Alchemist(s) ping before each of %d spells and once after; rituals open with the "
            "lands, burn spends the pool exactly%s%s%s" % (alchemists, k, "; Fireblast last, after both Mountains "
            "tap" if fireblast else "", "; every spell before combat pumps Kiln Fiend" if kiln else "",
            "; Goblin Guide is a trap" if herring else ""))
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", phase), ("p0life", 20),
             ("p0hand", ";".join(hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", damage), ("p1library", "Plains;Plains;Plains")]
    return {"family": "ping", "actions": actions, "life": damage, "hand": sorted(hand),
            "battlefield": sorted(battlefield), "phase": phase,
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}


def storm_board(rng):
    """Every spell cast before Grapeshot is one more copy, so the whole hand goes first and Grapeshot last. The
    mana is all red and exact: each ritual must be payable when it is cast (Seething Song needs three open; a Rite
    of Flame adds one more for every Rite already in the graveyard, so the Rites go back to back), and burn spent
    before Grapeshot also feeds the storm. A copy's target is a choice, not a step, so steps count casts only.
    Returns None when the draw cannot make a legal board of the right length."""
    mountains = rng.choice([1, 2, 2, 3])
    names = sorted(RITUALS)
    rituals = [rng.choice(names) for _ in range(rng.choice([2, 3, 3, 4]))]
    # An order that can be paid: cheapest first, Rites together, Seething Song once three are open.
    order = sorted(rituals, key=lambda r: (RITUALS[r][0] + RITUALS[r][1], r != "Rite of Flame"))
    # No Lotus Petal: when the engine pays with one it asks for a colour, and a Petal that can make blue turns
    # Gitaxian Probe's Phyrexian cost into a mana-or-life question (34 of 40 storm boards drifted, 2026-10-20).
    pool, lands, rites = 0, mountains, 0
    for r in order:
        generic, red, added = RITUALS[r]
        cost = generic + red
        if r == "Rite of Flame":
            added += rites
            rites += 1
        if pool + lands < cost:
            return None
        use = min(lands, max(0, cost - pool))
        lands -= use
        pool -= cost - use
        pool += added
    pool += lands
    if pool < 2:
        return None
    burn = [rng.choice(sorted(BURN)) for _ in range(pool - 2)]   # Grapeshot takes the last two
    probes = rng.choice([0, 1, 1, 2])
    before = order + ["Gitaxian Probe"] * probes + burn
    steps = len(before) + 1
    if not MIN_ACTIONS <= steps <= MAX_ACTIONS:
        return None
    storm = len(before)
    damage = storm + 1 + sum(BURN[b][0] for b in burn)
    herring = rng.random() < 0.4
    hand = before + ["Grapeshot"] + (["Goblin Guide"] if herring else [])
    rng.shuffle(hand)
    # The rituals in their order; the Probes; the burn; Grapeshot and a target for it and for each copy.
    solution = ["# steps: %d" % steps] + ["cast " + r for r in order]
    solution += sum([["cast Gitaxian Probe", "opponent"] for _ in range(probes)], [])
    for b in burn:
        solution += ["cast " + b, "opponent"]
    solution += ["cast Grapeshot", "opponent"] + ["opponent"] * storm
    battlefield = ["Mountain"] * mountains
    desc = ("It is your second main phase. You are at 20 life and control %s untapped Mountain%s. Your hand is %s. "
            "The opponent is at %d life and controls nothing. Reduce the opponent to 0 life this turn."
            % (["", "one", "two", "three"][mountains], "" if mountains == 1 else "s", _join(hand), damage))
    rule = ("generated-storm | %d spells before Grapeshot make %d copies; rituals paid in order (Rites back to "
            "back, Seething Song once three are open), burn before Grapeshot%s"
            % (storm, storm, "; Goblin Guide is a trap" if herring else ""))
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN2"), ("p0life", 20),
             ("p0hand", ";".join(hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", damage), ("p1library", "Plains;Plains;Plains")]
    return {"family": "storm", "actions": steps, "life": damage, "hand": sorted(hand),
            "battlefield": sorted(battlefield), "phase": "MAIN2",
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}


# Splice family: (generic, red, mana added, damage, arcane, target kind, splice cost or None, splice mana, splice
# damage). Every one is an instant or sorcery, so each untaps Thermo-Alchemist.
SPLICE_CARDS = {
    "Lava Spike": (0, 1, 0, 3, True, "player", None, 0, 0),
    "Glacial Ray": (1, 1, 0, 2, True, "any", 2, 0, 2),
    "Desperate Ritual": (1, 1, 3, 0, True, "none", 2, 3, 0),
    "Lightning Bolt": (0, 1, 0, 3, False, "any", None, 0, 0),
    "Shock": (0, 1, 0, 2, False, "any", None, 0, 0),
}


def splice_best(hand, mana, alchemist):
    """The most damage the hand can deal, and one line that deals it, by exhaustive search over a model of the
    board: all mana red and paid from the pool (lands are only more pool); an Arcane spell may carry any of the
    splice cards still in hand (each paid again and kept); a ping before each spell while Thermo-Alchemist is
    untapped. Returns (with_splice, without), each (damage, steps, line) with line a list of (spell, splices) and
    None for a ping."""
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def go(hand, mana, untapped, allow):
        best = (0, 0, ())
        if untapped and alchemist:
            d, st, ln = go(hand, mana, False, allow)
            best = max(best, (d + 1, st + 1, (None,) + ln), key=lambda x: (x[0], -x[1]))
        for s in sorted(set(hand)):
            rest = list(hand)
            rest.remove(s)
            g, r, add, dmg, arcane, _, _, _, _ = SPLICE_CARDS[s]
            options = [()]
            if arcane and allow:
                splicers = sorted(set(x for x in rest if SPLICE_CARDS[x][6] is not None))
                options = [tuple(c for j, c in enumerate(splicers) if m >> j & 1) for m in range(1 << len(splicers))]
            for sp in options:
                cost = g + r + sum(SPLICE_CARDS[x][6] for x in sp)
                if cost > mana:
                    continue
                gain = add + sum(SPLICE_CARDS[x][7] for x in sp)
                hit = dmg + sum(SPLICE_CARDS[x][8] for x in sp)
                d, st, ln = go(tuple(sorted(rest)), mana - cost + gain, True, allow)
                best = max(best, (d + hit, st + 1, ((s, sp),) + ln), key=lambda x: (x[0], -x[1]))
        return best
    with_splice = go(tuple(sorted(hand)), mana, True, True)
    without = go(tuple(sorted(hand)), mana, True, False)
    return with_splice, without


def splice_board(rng):
    """Arcane spells carry the splice cards still in hand: Desperate Ritual spliced pays 2 and adds 3, Glacial Ray
    spliced pays 2 and deals 2 more, and both stay in hand to be spliced again or cast. Which spell carries what,
    and when the carriers run out, is the puzzle; Thermo-Alchemist pings before each spell. Kept only when the best
    line splices and beats every line that does not."""
    mountains = rng.choice([1, 2, 2, 3])
    hand = [rng.choice(["Desperate Ritual", "Desperate Ritual", "Glacial Ray"])]
    hand += [rng.choice(sorted(SPLICE_CARDS)) for _ in range(rng.choice([4, 5, 5, 6]))]
    alchemist = rng.random() < 0.75
    (dmg, steps, line), (plain, _, _) = splice_best(hand, mountains, alchemist)
    if not any(sp for x in line if x for sp in [x[1]]) or plain >= dmg:
        return None
    if not MIN_ACTIONS <= steps <= MAX_ACTIONS:
        return None
    unused = list(hand)
    for x in line:
        if x:
            unused.remove(x[0])
    if unused:
        return None                    # every card is spent on the best line: no spare card to make it easier
    solution = ["# steps: %d" % steps]
    held, mana = list(hand), mountains
    for x in line:
        if x is None:
            solution.append("activate Thermo-Alchemist")
            continue
        spell, sp = x
        held.remove(spell)
        solution.append("cast " + spell)
        g, r, add = SPLICE_CARDS[spell][:3]
        kind = SPLICE_CARDS[spell][5]
        if kind in ("any", "player"):
            solution.append("opponent")
        # The engine asks only for the splice cards it could pay on top of the spell (splice_3d44093cf0dc: an
        # unaffordable splice is no question at all).
        offered = [c for c in set(held) if SPLICE_CARDS[c][6] is not None and SPLICE_CARDS[c][6] <= mana - g - r]
        mana += add - g - r + sum(SPLICE_CARDS[c][7] - SPLICE_CARDS[c][6] for c in sp)
        if SPLICE_CARDS[spell][4] and offered:
            solution.append("; ".join(sp) if sp else "none")
            if sp:
                # After a splice the whole spell's targets are chosen again (h_1): the carrier's, then each
                # spliced card's that has one.
                solution += ["opponent"] * ((kind in ("any", "player")) + sum(SPLICE_CARDS[c][5] == "any"
                                                                              for c in sp))
    creatures = ["Thermo-Alchemist"] if alchemist else []
    battlefield = creatures + ["Mountain"] * mountains
    rng.shuffle(hand)
    desc = ("It is your second main phase. You are at 20 life and control %s%s untapped Mountain%s. Your hand is %s. "
            "The opponent is at %d life and controls nothing. Reduce the opponent to 0 life this turn."
            % ("Thermo-Alchemist and " if alchemist else "", ["", "one", "two", "three"][mountains],
               "" if mountains == 1 else "s", _join(hand), dmg))
    rule = ("generated-splice | the best line deals %d by splicing; without splicing at most %d" % (dmg, plain))
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN2"), ("p0life", 20),
             ("p0hand", ";".join(hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", dmg), ("p1library", "Plains;Plains;Plains")]
    return {"family": "splice", "actions": steps, "life": dmg, "hand": sorted(hand),
            "battlefield": sorted(battlefield), "phase": "MAIN2",
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}


# The red engine: (generic, red, mana added, damage, arcane, target, splice (generic, red) or None, splice mana,
# splice damage). Special cards are handled by name: Rite of Flame (+1 per Rite in the graveyard), Grapeshot (one
# more copy per spell before it), Fireblast (two Mountains instead of mana), Lava Dart (flashback: a Mountain).
RED = {
    "Desperate Ritual": (1, 1, 3, 0, True, "none", (1, 1), 3, 0),
    "Pyretic Ritual": (1, 1, 3, 0, False, "none", None, 0, 0),
    "Rite of Flame": (0, 1, 2, 0, False, "none", None, 0, 0),
    "Seething Song": (2, 1, 5, 0, False, "none", None, 0, 0),
    "Lightning Bolt": (0, 1, 0, 3, False, "any", None, 0, 0),
    "Shock": (0, 1, 0, 2, False, "any", None, 0, 0),
    "Lava Spike": (0, 1, 0, 3, True, "player", None, 0, 0),
    "Glacial Ray": (1, 1, 0, 2, True, "any", (1, 1), 0, 2),
    "Grapeshot": (1, 1, 0, 1, False, "any", None, 0, 0),
    "Fireblast": (0, 0, 0, 4, False, "any", None, 0, 0),
    "Lava Dart": (0, 1, 0, 1, False, "any", None, 0, 0),
    "Gitaxian Probe": (0, 0, 0, 0, False, "player", None, 0, 0),
}


def red_search(hand, mountains, alchemist, reducer, splice=True):
    """The most damage the hand can deal and one line that deals it, by exhaustive search. Mana is all red and is
    paid from the pool first, then from untapped Mountains; a Mountain is sacrificed (Fireblast, Lava Dart's
    flashback) only once every Mountain is tapped, so no mana is lost to it; Goblin Electromancer takes {1} off each
    spell. Line items: "ping", ("cast", card, splices) or ("flashback", "Lava Dart"). Returns (damage, steps, line)
    without the attack, plus the number of instants and sorceries cast (for Kiln Fiend)."""
    from functools import lru_cache

    def better(a, b):
        return a if (a[0], -a[1]) >= (b[0], -b[1]) else b

    @lru_cache(maxsize=None)
    def go(hand, darts, rites, pool, untapped, mtns, alch, storm):
        best = (0, 0, (), 0)
        if alch:
            d, st, ln, n = go(hand, darts, rites, pool, untapped, mtns, False, storm)
            best = better((d + 1, st + 1, ("ping",) + ln, n), best)
        moves = [("cast", c) for c in sorted(set(hand))] + ([("flashback", "Lava Dart")] if darts else [])
        for kind, c in moves:
            g, r, add, dmg, arcane, _, _, _, _ = RED[c]
            rest = list(hand)
            if kind == "cast":
                rest.remove(c)
            sac = 0
            if c == "Fireblast":
                sac, g, r = 2, 0, 0
            if kind == "flashback":
                sac, g, r = 1, 0, 0
            if sac and (untapped or mtns < sac):
                continue
            options = [()]
            if arcane and splice:
                sp = sorted(set(x for x in rest if RED[x][6]))
                options = [tuple(x for j, x in enumerate(sp) if m >> j & 1) for m in range(1 << len(sp))]
            for chosen in options:
                cg = g + sum(RED[x][6][0] for x in chosen)
                cr = r + sum(RED[x][6][1] for x in chosen)
                if reducer and sac == 0 and c != "Gitaxian Probe":
                    cg = max(0, cg - 1)
                cost = cg + cr
                if cost > pool + untapped:
                    continue
                from_pool = min(pool, cost)
                npool, nun = pool - from_pool, untapped - (cost - from_pool)
                gain = add + sum(RED[x][7] for x in chosen)
                hit = dmg + sum(RED[x][8] for x in chosen)
                nrites = rites
                if c == "Rite of Flame":
                    gain += rites
                    nrites += 1
                if c == "Grapeshot":
                    hit += storm
                ndarts = darts + (1 if c == "Lava Dart" and kind == "cast" else 0) - (1 if kind == "flashback" else 0)
                d, st, ln, n = go(tuple(sorted(rest)), ndarts, nrites, npool + gain, nun, mtns - sac, bool(alchemist),
                                  storm + 1)
                best = better((d + hit, st + 1, ((kind, c, chosen),) + ln, n + 1), best)
        return best
    return go(tuple(sorted(hand)), 0, 0, 0, mountains, mountains, bool(alchemist), 0)


def red_naive(hand, mountains, alchemist, reducer):
    """A plain player: ping whenever it can, cast the affordable spell that deals the most now (rituals when
    nothing else is affordable), never splice, Grapeshot only as the last spell, Fireblast and Lava Dart's
    flashback once the Mountains are tapped."""
    hand, pool, untapped, mtns, dmg, storm, darts, rites, casts = list(hand), 0, mountains, mountains, 0, 0, 0, 0, 0
    alch = bool(alchemist)
    while True:
        if alch:
            dmg, alch = dmg + 1, False
            continue
        choices = []
        for c in set(hand) | ({"Lava Dart*"} if darts else set()):
            name = c.rstrip("*")
            g, r, add, d = RED[name][:4]
            sac = 2 if name == "Fireblast" else 1 if c.endswith("*") else 0
            if sac:
                g = r = 0
            if reducer and not sac and name != "Gitaxian Probe":
                g = max(0, g - 1)
            if sac and (untapped or mtns < sac):
                continue
            if g + r > pool + untapped:
                continue
            if name == "Grapeshot" and len(hand) > 1:
                continue
            choices.append((d + (storm if name == "Grapeshot" else 0), add, c, g + r, sac))
        if not choices:
            return dmg, casts
        d, add, c, cost, sac = max(choices)
        name = c.rstrip("*")
        if c.endswith("*"):
            darts -= 1
        else:
            hand.remove(c)
            darts += name == "Lava Dart"
        from_pool = min(pool, cost)
        pool, untapped = pool - from_pool, untapped - (cost - from_pool)
        if name == "Rite of Flame":
            add += rites
            rites += 1
        pool += add
        mtns -= sac
        dmg += d
        storm += 1
        casts += 1
        alch = bool(alchemist)


def red_board(rng):
    """The red engine: rituals, burn, Grapeshot, Fireblast, Lava Dart's flashback and the two splices, with
    Thermo-Alchemist pinging, Goblin Electromancer reducing and Kiln Fiend attacking. The line is the searched best;
    a board is kept only when a plain player (red_naive) falls short of it, so the board hides something."""
    mountains = rng.choice([1, 2, 2, 3])
    alchemist = rng.random() < 0.6
    reducer = rng.random() < 0.3
    kiln = rng.random() < 0.3
    pool = sorted(RED)
    hand = [rng.choice(pool) for _ in range(rng.choice([5, 6, 6, 7]))]
    if len(set(hand)) < 4:
        return None
    dmg, steps, line, casts = red_search(hand, mountains, alchemist, reducer)
    attackers = (["Kiln Fiend"] if kiln else []) + (["Goblin Electromancer"] if reducer else [])
    attack = (1 + 3 * casts if kiln else 0) + (2 if reducer else 0)
    naive, naive_casts = red_naive(hand, mountains, alchemist, reducer)
    naive += (1 + 3 * naive_casts if kiln else 0) + (2 if reducer else 0)
    total = dmg + attack
    steps += 1 if attackers else 0
    if naive >= total or not MIN_ACTIONS <= steps <= MAX_ACTIONS:
        return None
    solution = ["# steps: %d" % steps]
    held, pool_m, untapped, mtns, storm = list(hand), 0, mountains, mountains, 0
    for item in line:
        if item == "ping":
            solution.append("activate Thermo-Alchemist")
            continue
        kind, c, chosen = item
        g, r = RED[c][0], RED[c][1]
        if c == "Fireblast" or kind == "flashback":
            g = r = 0
        if kind == "cast":
            held.remove(c)
        solution.append("cast " + c)
        tgt = RED[c][5]
        if tgt in ("any", "player"):
            solution.append("opponent")
        free = pool_m + untapped - max(0, g - (1 if reducer and c != "Gitaxian Probe" and g else 0)) - r
        offered = [x for x in set(held) if RED[x][6]
                   and sum(RED[x][6]) <= free] if RED[c][4] else []
        if offered:
            solution.append("; ".join(chosen) if chosen else "none")
            if chosen:
                solution += ["opponent"] * ((tgt in ("any", "player")) + sum(RED[x][5] == "any" for x in chosen))
        if c == "Fireblast" and mtns > 2:
            solution.append("Mountain; Mountain")
        if kind == "flashback" and mtns > 1:
            solution.append("Mountain")
        # Two or more of the seat's triggers at once are ordered by the seat: Thermo-Alchemist's untap, Kiln Fiend's
        # pump, Grapeshot's storm (red_062a1ef04a5d). Any order deals the same here.
        triggers = (["Thermo-Alchemist"] if alchemist else []) + (["Kiln Fiend"] if kiln else []) \
            + (["Grapeshot"] if c == "Grapeshot" else [])
        if len(triggers) > 1:
            solution.append("; ".join(triggers))
        if c == "Grapeshot":
            solution += ["opponent"] * storm
        # track mana the way the search pays it
        cg = g + sum(RED[x][6][0] for x in chosen)
        cr = r + sum(RED[x][6][1] for x in chosen)
        if reducer and c != "Gitaxian Probe" and not (c == "Fireblast" or kind == "flashback"):
            cg = max(0, cg - 1)
        cost = cg + cr
        from_pool = min(pool_m, cost)
        pool_m, untapped = pool_m - from_pool, untapped - (cost - from_pool)
        pool_m += RED[c][2] + sum(RED[x][7] for x in chosen)
        if c == "Rite of Flame":
            pool_m += sum(1 for s_ in solution if s_ == "cast Rite of Flame") - 1
        mtns -= 2 if c == "Fireblast" else 1 if kind == "flashback" else 0
        storm += 1
    if attackers:
        solution.append("; ".join(attackers))
    creatures = (["Thermo-Alchemist"] if alchemist else []) + attackers
    battlefield = creatures + ["Mountain"] * mountains
    phase = "MAIN1" if attackers else "MAIN2"
    rng.shuffle(hand)
    desc = ("It is your %s main phase. You are at 20 life and control %s%s untapped Mountain%s. Your hand is %s. "
            "The opponent is at %d life and controls nothing. Reduce the opponent to 0 life this turn."
            % ("first" if attackers else "second", (_join(creatures) + " and ") if creatures else "",
               ["", "one", "two", "three"][mountains], "" if mountains == 1 else "s", _join(hand), total))
    rule = "generated-red | the searched best line deals %d; a plain player deals %d" % (total, naive)
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", phase), ("p0life", 20),
             ("p0hand", ";".join(hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", total), ("p1library", "Plains;Plains;Plains")]
    return {"family": "red", "actions": steps, "life": total, "naive": naive, "hand": sorted(hand),
            "battlefield": sorted(battlefield), "phase": phase,
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}


# The drain family. Each card: (generic cost, black cost) to cast it, or None when it starts on the battlefield only
# (Cruel Celebrant needs {W}, Ashnod's Altar is not cast here).
DRAIN_COSTS = {"Blood Artist": (1, 1), "Zulaport Cutthroat": (1, 1), "Cruel Celebrant": None,
               "Carrion Feeder": (0, 1), "Viscera Seer": (0, 1), "Reassembling Skeleton": (1, 1),
               "Bloodghast": (0, 2), "Gravecrawler": (0, 1)}
DRAINERS = ("Blood Artist", "Zulaport Cutthroat", "Cruel Celebrant")   # each drains 1 when any of ours dies
COUNTED_OUTLETS = ("Carrion Feeder", "Viscera Seer")                    # "sacrifice a creature", on the stack
ALTAR = "Ashnod's Altar"                                               # a mana ability: {C}{C}, not a step
ZOMBIES = ("Carrion Feeder", "Gravecrawler")


def _drain_moves(st, altar):
    """Every legal move from state `st` = (pool C, untapped Swamps, Swamp in hand, battlefield, graveyard, hand):
    (move, next state, drain, step). Mana: generic is paid from floating {C} first, then Swamps; {B} from Swamps."""
    pool, swamps, land, bf, gy, hand = st

    def pay(generic, black):
        use_c = min(pool, generic)
        need = generic - use_c + black
        return (pool - use_c, swamps - need) if swamps >= need else None

    out = []
    if land:
        nbf = bf | {"Bloodghast"} if "Bloodghast" in gy else bf
        out.append((("land",), (pool, swamps + 1, False, nbf, gy - {"Bloodghast"}, hand), 0, 1))
    for c in sorted(hand):
        paid = pay(*DRAIN_COSTS[c])
        if paid:
            out.append((("cast", c), (paid[0], paid[1], land, bf | {c}, gy, hand - {c}), 0, 1))
    if "Gravecrawler" in gy and any(z in bf for z in ZOMBIES):
        paid = pay(0, 1)
        if paid:
            out.append((("cast", "Gravecrawler"), (paid[0], paid[1], land, bf | {"Gravecrawler"},
                                                   gy - {"Gravecrawler"}, hand), 0, 1))
    if "Reassembling Skeleton" in gy:
        paid = pay(1, 1)
        if paid:
            out.append((("return",), (paid[0], paid[1], land, bf | {"Reassembling Skeleton"},
                                      gy - {"Reassembling Skeleton"}, hand), 0, 1))
    outlets = ([ALTAR] if altar else []) + [o for o in COUNTED_OUTLETS if o in bf]
    for o in outlets:
        for v in sorted(bf):
            drain = sum(1 for d in DRAINERS if d in bf)
            out.append((("sac", o, v), (pool + (2 if o == ALTAR else 0), swamps, land, bf - {v}, gy | {v}, hand),
                        drain, 0 if o == ALTAR else 1))
    return out


def drain_search(swamps, land, bf, gy, hand, altar):
    """The most life the board drains this turn, and the fewest steps that reach it: an exhaustive search over every
    order of casts, sacrifices, returns and the land drop. Returns (drain, steps, moves)."""
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def best(st):
        top = (0, 0, ())
        for move, nxt, drain, step in _drain_moves(st, altar):
            d, s, line = best(nxt)
            cand = (d + drain, s + step, (move,) + line)
            if cand[0] > top[0] or (cand[0] == top[0] and cand[0] > 0 and (cand[1] < top[1] or not top[2])):
                top = cand
        return top

    return best((0, swamps, land, frozenset(bf), frozenset(gy), frozenset(hand)))


def drain_lines(swamps, land, bf, gy, hand, altar, moves, life):
    """The written line for `moves`, played through a model of the stack: after each move every object on the stack
    is resolved by one "pass" in each window the seat is asked (one it is not asked resolves by itself), so the next
    move sees the creature cast, the Skeleton back and the Seer's scry answered. The order of simultaneous death
    triggers is asked (any order is right); Blood Artist's target is the opponent. The line ends where the last drain
    trigger resolves: the game is over there."""
    pool, bf, gy = 0, set(bf), set(gy)
    lines = []
    drained = 0

    def asked():
        # The seat is offered the window when anything but a Swamp's mana is playable (LlmSpikeGui.floatChangesNothing):
        # a sacrifice (Altar or an outlet, with a creature to give it) or the Skeleton's return.
        if bf and (altar or any(o in bf for o in COUNTED_OUTLETS)):
            return True
        return "Reassembling Skeleton" in gy and swamps >= 1 and (pool >= 1 or swamps >= 2)

    def resolve(stack):
        nonlocal drained
        while stack:
            if asked():
                lines.append("pass")
            kind, what = stack.pop()
            if kind == "trigger":
                drained += 1
                if drained >= life:
                    return True
            elif kind == "creature":
                bf.add(what)
            elif kind == "bloodghast":
                lines.append("yes")
                bf.add("Bloodghast")
                gy.discard("Bloodghast")
            elif kind == "scry":
                lines.append("Colossal Dreadmaw")
        return False

    def spend(generic, black):
        nonlocal pool, swamps
        use_c = min(pool, generic)
        pool -= use_c
        swamps -= generic - use_c + black

    for move in moves:
        stack = []
        if move[0] == "land":
            lines.append("play land Swamp")
            swamps += 1
            if "Bloodghast" in gy:
                stack.append(("bloodghast", None))
        elif move[0] == "cast":
            c = move[1]
            lines.append("cast " + c)
            spend(*DRAIN_COSTS.get(c) or (0, 1))
            gy.discard(c)
            stack.append(("creature", c))
        elif move[0] == "return":
            lines.append("activate Reassembling Skeleton")
            spend(1, 1)
            gy.discard("Reassembling Skeleton")
            stack.append(("creature", "Reassembling Skeleton"))
        else:
            _, o, v = move
            lines.append("activate " + o)
            if len(bf) > 1:
                lines.append(v)
            drainers = [d for d in DRAINERS if d in bf]
            bf.discard(v)
            gy.add(v)
            if o == ALTAR:
                pool += 2
            else:
                stack.append(("scry" if o == "Viscera Seer" else "ability", o))
            if len(drainers) > 1:
                lines.append("; ".join(drainers))
            if "Blood Artist" in drainers:
                lines.append("opponent")
            stack += [("trigger", d) for d in reversed(drainers)]   # the first named resolves first
        if resolve(stack):
            break
    while lines and lines[-1] == "pass":
        lines.pop()
    return lines


def drain_board(rng):
    """Sacrifice and recursion: every creature that dies drains the opponent once per drainer on the battlefield
    (Blood Artist, Zulaport Cutthroat, Cruel Celebrant, each counting its own death), Reassembling Skeleton comes back
    for {1}{B}, Gravecrawler is cast again from the graveyard while a Zombie (Carrion Feeder) is out, Bloodghast
    returns on the land drop, and Ashnod's Altar turns each death into {C}{C} for the generic halves. The opponent's
    life is the exact maximum of an exhaustive search, so every death must come with every drainer still out, the
    drainers last, the land after Bloodghast dies, and no {B} wasted. Returns None when the draw is not 10-15 steps."""
    swamps = rng.choice([1, 2, 2, 3, 3, 4, 5])
    altar = rng.random() < 0.5
    bf, gy, hand = set(), set(), set()
    zones = lambda: rng.choice(["bf", "bf", "hand"])   # noqa: E731
    put = {"bf": bf, "hand": hand, "gy": gy}
    for d in rng.sample(DRAINERS, rng.choice([1, 2, 2, 3])):
        put["bf" if d == "Cruel Celebrant" else zones()].add(d)
    for o in COUNTED_OUTLETS:
        if rng.random() < (0.35 if altar else 0.6):
            put[zones()].add(o)
    if not altar and not any(o in bf | hand for o in COUNTED_OUTLETS):
        return None
    for r, p in (("Reassembling Skeleton", 0.75), ("Bloodghast", 0.5), ("Gravecrawler", 0.45)):
        if rng.random() < p:
            put[rng.choice(["bf", "bf", "gy", "hand"])].add(r)
    land = rng.random() < 0.65
    if not bf:
        return None
    drain, steps, moves = drain_search(swamps, land, bf, gy, hand, altar)
    if not MIN_ACTIONS <= steps <= MAX_ACTIONS or drain < 6:
        return None
    lines = drain_lines(swamps, land, bf, gy, hand, altar, moves, drain)
    battlefield = ([ALTAR] if altar else []) + sorted(bf) + ["Swamp"] * swamps
    hand_list = sorted(hand) + (["Swamp"] if land else [])
    graveyard = sorted(gy)
    solution = ["# steps: %d" % steps] + lines
    desc = ("It is your second main phase. You are at 20 life and control %s. Your hand is %s. Your graveyard holds "
            "%s. The opponent is at %d life and controls nothing. Reduce the opponent to 0 life this turn."
            % (_join(([ALTAR] if altar else []) + sorted(bf) + ["%d untapped Swamp%s" % (swamps, "s" if swamps > 1
                                                                                          else "")]),
               _join(hand_list) if hand_list else "empty", _join(graveyard) if graveyard else "nothing", drain))
    cards = bf | gy | hand
    notes = ["every death with every drainer still out, so the drainers die last"]
    if altar:
        notes.append("the Altar's {C}{C} pays the generic halves, the Swamps the {B}")
    if "Reassembling Skeleton" in cards:
        notes.append("the Skeleton comes back for {1}{B} each time")
    if "Gravecrawler" in cards:
        notes.append("Gravecrawler is cast again from the graveyard only while Carrion Feeder (a Zombie) is out")
    if "Bloodghast" in cards and land:
        notes.append("the land drop waits until Bloodghast is in the graveyard")
    rule = "generated-drain | the exhaustive maximum is %d drained in %d steps: %s" % (drain, steps, "; ".join(notes))
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN2"), ("p0life", 20),
             ("p0hand", ";".join(hand_list)), ("p0battlefield", ";".join(battlefield)),
             ("p0graveyard", ";".join(graveyard)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", drain), ("p1library", "Plains;Plains;Plains")]
    state = [kv for kv in state if kv[1] != ""]
    return {"family": "drain", "actions": steps, "life": drain, "hand": sorted(hand_list),
            "battlefield": sorted(battlefield + ["gy:" + g for g in graveyard]), "phase": "MAIN2",
            "search": {"swamps": swamps, "land": land, "bf": sorted(bf), "gy": graveyard, "hand": sorted(hand),
                       "altar": altar},
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}


# The elf family. Hand elves by name and cost in {G}: none has an ability a summoning-sick elf can use, so each is
# only a body (one more creature for Craterhoof, one more elf for Heritage Druid to tap).
ELF_HAND = {"Willow Elf": 1, "Norwood Ranger": 1, "Elvish Mystic": 1, "Llanowar Elves": 1, "Fyndhorn Elves": 1,
            "Boreal Druid": 1, "Elves of Deep Shadow": 1, "Arbor Elf": 1, "Elvish Scout": 1, "Elvish Lyrist": 1,
            "Cylian Elf": 2, "Treetop Warden": 2, "Wirewood Elf": 2, "Wellwisher": 2, "Elvish Warrior": 2}
ELF_MANA = ("Elvish Mystic", "Llanowar Elves", "Fyndhorn Elves")   # may start untapped and able to tap for {G}
HOOF_COST = 8


def elf_search(forests, sentinels, mana_elves, costs):
    """The most damage this turn and the fewest steps that reach it. Heritage Druid taps any three untapped elves
    (itself, the Sentinels, the starting mana elves, summoning-sick ones) for {G}{G}{G}; every green spell untaps every
    Nettle Sentinel; a starting mana elf taps for {G}; Craterhoof Behemoth (haste) gives each creature +X/+X, X the
    creatures, and the untapped creatures that started on the battlefield attack beside it. The Forests float first.
    Returns (damage, steps, moves), moves ("druid", (druid, sentinels, mana elves, sick)), ("tap",), ("cast", i) and
    ("hoof",) last; steps are the casts, Craterhoof and the attack."""
    from functools import lru_cache
    n = len(costs)

    @lru_cache(maxsize=None)
    def best(pool, druid, sent, mana, sick, cast):
        cands = []
        cast_n = bin(cast).count("1")
        if pool >= HOOF_COST:
            x = 1 + sentinels + mana_elves + cast_n + 1
            dmg = (5 + x) + (1 + x if druid else 0) + sentinels * (2 + x) + mana * (1 + x)
            cands.append((dmg, cast_n + 2, (("hoof",),)))
        seen = set()
        for i in range(n):
            if not cast >> i & 1 and pool >= costs[i] and costs[i] not in seen:
                seen.add(costs[i])     # elves of one cost are interchangeable: the lowest index stands for all
                cands.append((best(pool - costs[i], druid, sentinels, mana, sick + 1, cast | 1 << i), ("cast", i)))
        if mana:
            cands.append((best(pool + 1, druid, sent, mana - 1, sick, cast), ("tap",)))
        for a in range(druid + 1):
            for b in range(min(sent, 3 - a) + 1):
                for c in range(min(mana, 3 - a - b) + 1):
                    k = 3 - a - b - c
                    if k <= sick:
                        cands.append((best(pool + 3, druid - a, sent - b, mana - c, sick - k, cast),
                                      ("druid", (a, b, c, k))))
        top = (-1, 0, ())
        for c in cands:
            if len(c) == 2:
                (d, s, line), move = c
                c = (d, s, (move,) + line)
            if c[0] > top[0] or (c[0] == top[0] and c[1] < top[1]):
                top = c
        return top

    return best(forests, 1, sentinels, mana_elves, 0, 0)


def elf_lines(forests, sentinels, mana_names, names, moves):
    """The written line for `moves`. Every Nettle Sentinel's "may untap" is asked when its trigger resolves (yes, also
    when it is untapped); a Druid activation with more than three untapped elves asks which three; a cast elf is
    resolved by one "pass" before a mana activation that would otherwise be taken with it still on the stack, but
    only in a window the seat is asked (one where nothing but a Forest's mana is playable is skipped)."""
    lines = ["activate Forest"] * forests
    druid, sent = True, sentinels
    mana = list(mana_names)            # untapped starting mana elves
    sick = []                          # untapped summoning-sick elves, in the order cast
    for j, move in enumerate(moves):
        if move[0] == "druid":
            a, b, c, k = move[1]
            lines.append("activate Heritage Druid")
            if (1 if druid else 0) + sent + len(mana) + len(sick) > 3:
                lines.append("; ".join(["Heritage Druid"] * a + ["Nettle Sentinel"] * b + mana[:c] + sick[:k]))
            druid, sent, mana, sick = druid and not a, sent - b, mana[c:], sick[k:]
        elif move[0] == "tap":
            lines.append("activate " + mana.pop(0))
        elif move[0] == "cast":
            name = names[move[1]]
            lines += ["cast " + name] + ["yes"] * sentinels
            sent = sentinels
            nxt = moves[j + 1][0] if j + 1 < len(moves) else None
            window = (1 if druid else 0) + sent + len(mana) + len(sick) >= 3 or bool(mana)
            if nxt in ("druid", "tap") and window:
                lines.append("pass")
            sick.append(name)
        else:
            lines += ["cast Craterhoof Behemoth"] + ["yes"] * sentinels
            lines.append("; ".join(["Craterhoof Behemoth"] + ["Nettle Sentinel"] * sentinels
                                   + (["Heritage Druid"] if druid else []) + mana))
    return lines


def elf_board(rng):
    """Heritage Druid and Nettle Sentinel: the Druid taps any three untapped elves for {G}{G}{G}, summoning-sick ones
    included, and each green spell untaps every Sentinel, so the Sentinels are tapped for mana before each cast and the
    elves cast become the next activation's fodder. Craterhoof Behemoth gives every creature +X/+X; only the creatures
    that started on the battlefield and stay untapped attack beside it, so each tap of the Druid itself or a starting
    mana elf costs an attacker. The opponent's life is the exact maximum of an exhaustive search. Returns None when the
    draw is not 10-15 steps (the casts, Craterhoof and the attack)."""
    forests = rng.choice([0, 1, 1, 2])
    sentinels = rng.choice([1, 1, 2, 2, 3])
    mana_names = rng.sample(ELF_MANA, rng.choice([0, 1, 1, 2]))
    pool = sorted(set(ELF_HAND) - set(mana_names))
    names = rng.sample(pool, rng.randint(MIN_ACTIONS - 2, MAX_ACTIONS - 2))
    costs = [ELF_HAND[nm] for nm in names]
    dmg, steps, moves = elf_search(forests, sentinels, len(mana_names), costs)
    if dmg < 0 or moves[-1] != ("hoof",) or not MIN_ACTIONS <= steps <= MAX_ACTIONS:
        return None
    # The mana must bind: with unlimited mana every elf is cast and every starting creature attacks. A board where the
    # best line reaches that is a matter of casting everything; one that falls short asks which elf to leave in hand
    # or which attacker to tap.
    x = 1 + sentinels + len(mana_names) + len(names) + 1
    if dmg >= (5 + x) + (1 + x) + sentinels * (2 + x) + len(mana_names) * (1 + x):
        return None
    lines = elf_lines(forests, sentinels, mana_names, names, moves)
    battlefield = ["Forest"] * forests + ["Heritage Druid"] + ["Nettle Sentinel"] * sentinels + mana_names
    hand = names + ["Craterhoof Behemoth"]
    rng.shuffle(hand)
    solution = ["# steps: %d" % steps] + lines
    count = ["", "one", "two", "three"]
    desc = ("It is your first main phase. You are at 20 life and control %s, all untapped and none of them summoning "
            "sick. Your hand is %s. The opponent is at %d life and controls nothing. Reduce the opponent to 0 life "
            "this turn." % (_join(([("%s Forest%s" % (count[forests], "s" if forests > 1 else ""))] if forests else [])
                                  + ["Heritage Druid", "%s Nettle Sentinel%s" % (count[sentinels],
                                                                                 "s" if sentinels > 1 else "")]
                                  + mana_names), _join(hand), dmg))
    rule = ("generated-elf | the exhaustive maximum is %d damage in %d steps: the Sentinels tapped by the Druid before "
            "each green spell untaps them, the cast elves the Druid's next fodder, Craterhoof last with %s untapped"
            % (dmg, steps, "the Druid" if lines[-1].count("Heritage Druid") else "the Sentinels"))
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN1"), ("p0life", 20),
             ("p0hand", ";".join(hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", dmg), ("p1library", "Plains;Plains;Plains")]
    return {"family": "elf", "actions": steps, "life": dmg, "hand": sorted(hand), "battlefield": sorted(battlefield),
            "phase": "MAIN1",
            "search": {"forests": forests, "sentinels": sentinels, "mana_elves": mana_names, "elves": names},
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}


# The fling family: (generic, red, mana added, damage to the opponent, pump to a target creature, target kind).
# Every card is an instant or sorcery, so each one cast triggers prowess (+1) and Kiln Fiend (+3). No Desperate
# Ritual: Lava Spike is Arcane, and a splice is one more question.
FLING_CARDS = {
    "Rite of Flame": (0, 1, 2, 0, 0, "none"),
    "Pyretic Ritual": (1, 1, 3, 0, 0, "none"),
    "Seething Song": (2, 1, 5, 0, 0, "none"),
    "Lightning Bolt": (0, 1, 0, 3, 0, "any"),
    "Lava Spike": (0, 1, 0, 3, 0, "player"),
    "Shock": (0, 1, 0, 2, 0, "any"),
    "Brute Force": (0, 1, 0, 0, 3, "creature"),
    "Mutagenic Growth": (0, 0, 0, 0, 2, "creature"),
    "Gitaxian Probe": (0, 0, 0, 0, 0, "player"),
    "Fling": (1, 1, 0, 0, 0, "any"),
}
# Base power and what each instant or sorcery cast adds to it.
FLING_CREATURES = {"Monastery Swiftspear": (1, 1), "Soul-Scar Mage": (1, 1), "Kiln Fiend": (1, 3)}


FLING_SORCERIES = {"Rite of Flame", "Lava Spike", "Gitaxian Probe"}


def fling_search(hand, mountains, creatures, mode="best", respond=True):
    """The most damage the turn can deal and one line that deals it, by exhaustive search. The turn has two halves:
    the first main phase, then the attack with every creature left (nobody blocks), then the second main phase. The
    mana pool empties between them; only Mountains left untapped carry over. Mana is all red, paid from the pool
    first, then from Mountains. Fling sacrifices a creature and deals its power, which keeps every pump and trigger
    it got, so a creature that has attacked can still be Flung. Modes restrict the second main phase: "best"
    (anything), "precombat" (nothing after the attack), "fling-only" (only Fling, paid by Mountains held back).

    respond=True plays the way a written line plays (ScriptBridge): the next card is cast at the first priority it
    is castable, so an instant goes on the stack above a ritual that has not resolved, and is paid by a Mountain
    when the pool is empty (fling_f67a2fdc14e8, 2026-10-20: Lightning Bolt cast over Pyretic Ritual tapped the
    Mountain kept for Fling). A card that is not castable yet waits while the stack resolves, one object a pass; a
    sorcery waits for an empty stack. respond=False lets everything resolve before the next cast (a player who
    passes priority on purpose): the bound the plain plans are held to.

    Line items: ("cast", card, creature or None) and ("attack", names), with ("main2",) where the second main phase
    begins. Returns (damage, steps, line); steps count the casts and the attack."""
    from functools import lru_cache

    def better(a, b):
        return a if (a[0], -a[1]) >= (b[0], -b[1]) else b

    def resolve(pool, rites, alive, stack):
        """The top of the stack resolves: (pool, rites, alive, stack)."""
        top, stack = stack[-1], stack[:-1]
        if top[0] == "mana":
            pool += top[1] + (rites if top[2] else 0)
            rites += top[2]
        elif top[0] == "pump":
            alive = tuple((n, p + top[2] if n == top[1] else p) for n, p in alive)
        return pool, rites, alive, stack

    @lru_cache(maxsize=None)
    def go(phase, hand, pool, untapped, rites, alive, stack):
        best = (0, 0, ())
        # End the phase: the stack resolves, then the attack (phase 1) or the turn's end.
        p_, r_, a_, s_ = pool, rites, alive, stack
        while s_:
            p_, r_, a_, s_ = resolve(p_, r_, a_, s_)
        if phase == 1:
            d, st, ln = go(2, hand, 0, untapped, r_, a_, ()) if mode != "precombat" else (0, 0, ())
            names = tuple(n for n, _ in a_)
            atk = (("attack", names),) if names else ()
            best = better((d + sum(p for _, p in a_), st + len(atk), atk + (("main2",),) + ln), best)
        for c in sorted(set(hand)):
            if phase == 2 and mode == "fling-only" and c != "Fling":
                continue
            g, r, add, dmg, pump, kind = FLING_CARDS[c]
            cost = g + r
            # Wait (pass) until the card is castable: the stack resolves one object at a time.
            p_, r_, a_, s_ = pool, rites, alive, stack
            while (cost > p_ + untapped or (c in FLING_SORCERIES and s_)) and s_:
                p_, r_, a_, s_ = resolve(p_, r_, a_, s_)
            if cost > p_ + untapped or (c in FLING_SORCERIES and s_):
                continue
            needs = kind == "creature" or c == "Fling"
            if needs and not a_:
                continue
            from_pool = min(p_, cost)
            npool, nun = p_ - from_pool, untapped - (cost - from_pool)
            rest = list(hand)
            rest.remove(c)
            for who in ([n for n, _ in a_] if needs else [None]):
                hit, left = dmg, a_
                if c == "Fling":
                    hit = dict(a_)[who]
                    left = tuple((n, p) for n, p in a_ if n != who)
                spell = ("mana", add, 1) if c == "Rite of Flame" else ("mana", add, 0) if add else \
                    ("pump", who, pump) if pump else ("none",)
                ns = s_ + (spell,) + tuple(("pump", n, FLING_CREATURES[n][1]) for n, _ in left)
                np_, nr, nl = npool, r_, left
                if not respond:
                    while ns:
                        np_, nr, nl, ns = resolve(np_, nr, nl, ns)
                d, st, ln = go(phase, tuple(sorted(rest)), np_, nun, nr, nl, ns)
                best = better((d + hit, st + 1, (("cast", c, who),) + ln), best)
        return best
    start = tuple(sorted((n, FLING_CREATURES[n][0]) for n in creatures))
    return go(1, tuple(sorted(hand)), 0, mountains, 0, start, ())


def fling_lines(line, creatures):
    """The written line for a fling_search line, in the seat's menu words: a creature is named only when there
    are two to choose from, a player target is `opponent`, two triggers at once are put in order, and the first
    play after the attack waits for the second main phase."""
    out, alive, wait = [], sorted(creatures), False
    for item in line:
        if item[0] == "main2":
            wait = True
            continue
        if item[0] == "attack":
            out.append("; ".join(item[1]))
            continue
        _, c, who = item
        out.append(("at MAIN2: " if wait else "") + "cast " + c)
        wait = False
        kind = FLING_CARDS[c][5]
        if kind in ("any", "player"):
            out.append("opponent")
        if (kind == "creature" or c == "Fling") and len(alive) > 1:
            out.append(who)
        if c == "Fling":
            alive.remove(who)
        if len(alive) > 1:
            out.append("; ".join(alive))
    return out


def fling_board(rng):
    """Prowess creatures and Kiln Fiend grow with every instant and sorcery; the attack deals their power, and Fling
    after combat deals the Flung creature's power a second time. The mana pool empties when the first main phase
    ends, so the mana for Fling must be held: a Mountain left untapped, or a ritual cast after combat (whose
    trigger still grows the creature to be Flung). Kept only when the searched best beats both plain plans: every
    spell before combat (Fling included), and every spell but Fling before combat with Fling paid by Mountains."""
    mountains = rng.choice([1, 1, 2, 2, 3])
    creatures = sorted(rng.sample(sorted(FLING_CREATURES), rng.choice([1, 2, 2])))
    pool = sorted(c for c in FLING_CARDS if c != "Fling")
    hand = ["Fling"] + [rng.choice(pool) for _ in range(rng.choice([8, 9, 9, 10]))]
    if len(set(hand)) < 5:
        return None
    early = fling_search(hand, mountains, creatures, "precombat", respond=False)[0]
    held = fling_search(hand, mountains, creatures, "fling-only", respond=False)[0]
    if max(early, held) >= fling_search(hand, mountains, creatures, respond=False)[0]:
        return None                    # the cheap bound first: the written line deals no more than this
    dmg, steps, line = fling_search(hand, mountains, creatures)
    if max(early, held) >= dmg or not MIN_ACTIONS <= steps <= MAX_ACTIONS:
        return None
    casts = [x[1] for x in line if x[0] == "cast"]
    after = line[[x[0] for x in line].index("main2") + 1:]
    if "Fling" not in casts or len(casts) != len(hand) or not after:
        return None                    # every card is spent, and the line plays on after the attack
    solution = ["# steps: %d" % steps] + fling_lines(line, creatures)
    battlefield = creatures + ["Mountain"] * mountains
    rng.shuffle(hand)
    lands = "%s untapped Mountain%s" % (["", "one", "two", "three"][mountains], "" if mountains == 1 else "s")
    desc = ("It is your first main phase. You are at 20 life and control %s. Your hand is %s. The opponent is at "
            "%d life and controls nothing. Reduce the opponent to 0 life this turn."
            % (_join(creatures + [lands]), _join(hand), dmg))
    rule = ("generated-fling | the searched best deals %d, Fling after combat with mana held for it; every spell "
            "before combat deals at most %d, Fling paid only by held Mountains at most %d" % (dmg, early, held))
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN1"), ("p0life", 20),
             ("p0hand", ";".join(hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", dmg), ("p1library", "Plains;Plains;Plains")]
    return {"family": "fling", "actions": steps, "life": dmg, "early": early, "held": held, "hand": sorted(hand),
            "battlefield": sorted(battlefield), "phase": "MAIN1",
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}


# The second generated suite (eval/puzzles/hard/gen2): lines of 13 to 15 steps, and a trap: a natural plain plan,
# searched to its own best, falls 1 to 3 short of the board's maximum.
GEN2_MIN, GEN2_MAX = 13, 15

# The copy family. Rituals: (cost, mana made, sorcery). Fodder (the spells worth copying for damage): (cost, damage,
# sorcery); every one targets, so each cast and each copy asks for `opponent`. All mana is red.
COPY_RITUALS = {"Rite of Flame": (1, 2, True), "Pyretic Ritual": (2, 3, False), "Seething Song": (3, 5, False)}
COPY_FODDER = {"Lightning Bolt": (1, 3, False), "Lava Spike": (1, 3, True), "Shock": (1, 2, False),
               "Gitaxian Probe": (0, 0, True)}
COPIERS = ("Fork", "Increasing Vengeance", "Reverberate")      # {R}{R}: copy target instant or sorcery spell
IV, IV_FLASHBACK = "Increasing Vengeance", 5                    # from the graveyard ({3}{R}{R}) it copies twice
# Damage a payoff deals for each instant or sorcery cast, and for each one copied (Magecraft counts a copy).
COPY_PAYOFFS = {"Guttersnipe": (2, 0), "Firebrand Archer": (1, 0), "Witherbloom Apprentice": (1, 1)}


def _copy_finish(fodder, copiers, iv_hand, iv_gy, mana, per_cast, per_copy):
    """The burn half of a copy line, once the stack is empty: which fodder to cast with `mana`, and how many copies
    to put on the best of it. Every copier aims at one spell; Increasing Vengeance cast from hand first can follow
    from the graveyard. Returns (damage, steps, plan), plan (fodder cast as ((name, n), ...), copiers from hand, 1 if
    Vengeance is cast from hand, 1 if it is flashed back, mana left)."""
    from itertools import product
    kinds = sorted(set(fodder))
    best = (0, 0, ((), 0, 0, 0, mana))
    for ns in product(*[range(fodder.count(k) + 1) for k in kinds]):
        cast = tuple((k, n) for k, n in zip(kinds, ns) if n)
        cost = sum(COPY_FODDER[k][0] * n for k, n in cast)
        nf = sum(ns)
        top = max([COPY_FODDER[k][1] for k, _ in cast] or [0])
        base = sum(COPY_FODDER[k][1] * n for k, n in cast) + per_cast * nf
        for kc in range(len(copiers) + 1):
            for ih in range(2 if iv_hand else 1):
                for fb in range(2 if iv_gy or ih else 1):
                    spent = cost + 2 * (kc + ih) + IV_FLASHBACK * fb
                    if spent > mana or (kc + ih + fb and not nf):
                        continue
                    copies = kc + ih + 2 * fb
                    d = base + per_cast * (kc + ih + fb) + copies * (top + per_copy)
                    st = nf + kc + ih + fb
                    if (d, -st) > (best[0], -best[1]):
                        best = (d, st, (cast, kc, ih, fb, mana - spent))
    return best


def copy_search(hand, mountains, payoffs, ritual_copies=True):
    """The most damage the board deals and one line that deals it, by exhaustive search. The line has two halves.
    First the rituals, and the copiers aimed at a ritual while it is on the stack (the copy makes the mana again),
    with every order of casts and passes: the stack resolves one object a pass, the pool stays all phase, mana is
    paid from the pool first, then from untapped Mountains. Then, the stack empty, the fodder and the copiers aimed
    at it (_copy_finish). That split loses nothing: a fodder spell cast earlier only ties up mana it could be cast
    with at the end, and a copy of fodder is worth the same then. A copier is never aimed at another copier (a copy
    of one copies a spell it could have aimed at itself).

    ritual_copies=False is the plain plan: a copier only ever copies burn. Line items: ("cast", card, target),
    target an index into the stack of rituals and copiers (None for a ritual); ("flashback", Increasing Vengeance,
    target); ("pass",); ("finish", plan) last. Returns (damage, steps, line); steps count the casts."""
    from functools import lru_cache
    per_cast = sum(COPY_PAYOFFS[p][0] for p in payoffs)
    per_copy = sum(COPY_PAYOFFS[p][1] for p in payoffs)
    fodder = sorted(c for c in hand if c in COPY_FODDER)

    def better(a, b):
        return a if (a[0], -a[1]) > (b[0], -b[1]) else b

    @lru_cache(maxsize=None)
    def go(rituals, copiers, pool, untapped, rites, iv_gy, stack):
        # stack items: ("r", ritual, is_copy) and ("c", copier, target index, flashback)
        if not stack:
            d, st, plan = _copy_finish(fodder, [c for c in copiers if c != IV], IV in copiers, iv_gy,
                                       pool + untapped, per_cast, per_copy)
            best = (d, st, (("finish", plan),))
        else:
            top, rest_stack = stack[-1], stack[:-1]
            p, r, iv, gain = pool, rites, iv_gy, 0
            if top[0] == "r":
                p += COPY_RITUALS[top[1]][1] + (rites if top[1] == "Rite of Flame" else 0)
                r += 1 if top[1] == "Rite of Flame" and not top[2] else 0
            else:
                n = 2 if top[3] else 1
                rest_stack += (("r", rest_stack[top[2]][1], True),) * n
                gain = n * per_copy
                iv = iv or (top[1] == IV and not top[3])
            d, st, ln = go(rituals, copiers, p, untapped, r, iv, rest_stack)
            best = (d + gain, st, (("pass",),) + ln)
        avail = pool + untapped

        def paid(cost):
            use = min(pool, cost)
            return pool - use, untapped - (cost - use)

        for c in sorted(set(rituals)):
            cost, _, sorcery = COPY_RITUALS[c]
            if cost > avail or (sorcery and stack):
                continue
            left = list(rituals)
            left.remove(c)
            d, st, ln = go(tuple(left), copiers, *paid(cost), rites, iv_gy, stack + (("r", c, False),))
            best = better((d + per_cast, st + 1, (("cast", c, None),) + ln), best)
        targets = {}
        for i, o in enumerate(stack):
            if ritual_copies and o[0] == "r" and not o[2]:
                targets.setdefault(o[1], i)     # two of one name on the stack: either is the same play
        plays = [(c, False) for c in sorted(set(copiers))] + ([(IV, True)] if iv_gy else [])
        for c, flash in plays:
            cost = IV_FLASHBACK if flash else 2
            if cost > avail or not targets:
                continue
            left = list(copiers)
            if not flash:
                left.remove(c)
            for t in sorted(targets.values()):
                d, st, ln = go(rituals, tuple(left), *paid(cost), rites, iv_gy and not flash,
                               stack + (("c", c, t, flash),))
                best = better((d + per_cast, st + 1, (("flashback" if flash else "cast", c, t),) + ln), best)
        return best

    return go(tuple(sorted(c for c in hand if c in COPY_RITUALS)), tuple(sorted(c for c in hand if c in COPIERS)),
              0, mountains, 0, False, ())


def copy_lines(hand, mountains, payoffs, line, life):
    """The written line for a copy_search line, played through a model of the stack as the engine plays it: each
    cast puts the spell and one trigger per payoff on the stack (two payoffs: their order is asked); a pass resolves
    the top object; a copy's target is asked when the copier resolves. A card is cast at the first priority it is
    castable (ScriptBridge), so "pass" is written only where the next card could be cast and the line means to let
    the stack resolve first; a wait for a card not yet castable needs no line. The line ends where the opponent
    reaches 0. Returns (lines, damage dealt, casts)."""
    cast, kc, ih, fb, _ = line[-1][1]
    plays = [x for x in line if x[0] != "finish"]
    if cast:
        order = sorted(cast, key=lambda kn: (-COPY_FODDER[kn[0]][1], COPY_FODDER[kn[0]][2], kn[0]))
        top = order[0][0]
        plays.append(("cast", top, None))
        left = [c for c in sorted(hand) if c in COPIERS and c != IV]
        for x in plays:
            if x[0] == "cast" and x[1] in left:
                left.remove(x[1])
        plays += [("cast", c, "top") for c in left[:kc]]
        if ih:
            plays.append(("cast", IV, "top"))
        if fb:
            plays.append(("flashback", IV, "top"))
        rest = [k for k, n in order for _ in range(n)]
        rest.remove(top)
        plays += [("cast", c, None) for c in rest]
    state = {"pool": 0, "untapped": mountains, "rites": 0, "iv_gy": False, "dmg": 0}
    held = list(hand)
    stack = []                         # dicts: {"trig": dmg} or {"name", "copy", "target", "flash"}
    lines = []
    marks = {}                         # "top": the fodder spell the finish copies

    def spells():
        return [o for o in stack if "name" in o]

    def cost_of(play):
        kind, c = play[0], play[1]
        if kind == "flashback":
            return IV_FLASHBACK
        if c in COPY_RITUALS:
            return COPY_RITUALS[c][0]
        if c in COPY_FODDER:
            return COPY_FODDER[c][0]
        return 2

    def castable(play):
        kind, c = play[0], play[1]
        if kind == "flashback" and not state["iv_gy"]:
            return False
        if kind == "cast" and c not in held:
            return False
        sorcery = (COPY_RITUALS.get(c) or COPY_FODDER.get(c) or (0, 0, False))[2]
        if sorcery and stack:
            return False
        if c in COPIERS and not spells():
            return False
        return cost_of(play) <= state["pool"] + state["untapped"]

    def resolve():
        o = stack.pop()
        if "trig" in o:
            state["dmg"] += o["trig"]
            return
        c = o["name"]
        if c in COPIERS:
            n = 2 if o["flash"] else 1
            target = o["target"]
            for _ in range(n):
                stack.append({"name": target["name"], "copy": True, "target": None, "flash": False})
                if target["name"] in COPY_FODDER:
                    lines.append("opponent")
            stack.extend({"trig": COPY_PAYOFFS[p][1]} for p in payoffs if COPY_PAYOFFS[p][1] for _ in range(n))
            if c == IV and not o["flash"]:
                state["iv_gy"] = True
        elif c in COPY_RITUALS:
            state["pool"] += COPY_RITUALS[c][1] + (state["rites"] if c == "Rite of Flame" else 0)
            if c == "Rite of Flame" and not o["copy"]:
                state["rites"] += 1
        else:
            state["dmg"] += COPY_FODDER[c][1]

    def do_cast(play, search_stack):
        kind, c, t = play
        cost = cost_of(play)
        use = min(state["pool"], cost)
        state["pool"] -= use
        state["untapped"] -= cost - use
        if kind == "flashback":
            state["iv_gy"] = False
            lines.append("cast %s ... Flashback" % IV)
        else:
            held.remove(c)
            lines.append("cast " + c)
        target = None
        if c in COPY_FODDER:
            lines.append("opponent")
        if c in COPIERS:
            target = marks["top"] if t == "top" else search_stack[t]
            if len(spells()) > 1:
                lines.append(target["name"])
        o = {"name": c, "copy": False, "target": target, "flash": kind == "flashback"}
        stack.append(o)
        if t is None and c in COPY_FODDER and "top" not in marks:
            marks["top"] = o
        if len(payoffs) > 1:
            lines.append("; ".join(payoffs))
        stack.extend({"trig": COPY_PAYOFFS[p][0]} for p in payoffs)

    casts = 0
    for i, play in enumerate(plays):
        if state["dmg"] >= life:
            break
        if play[0] == "pass":
            # Resolve up to and including the next spell (the triggers above it first).
            nxt = next((p for p in plays[i + 1:] if p[0] != "pass"), None)
            while stack and state["dmg"] < life:
                if nxt is not None and castable(nxt):
                    lines.append("pass")
                spell = "name" in stack[-1]
                resolve()
                if spell:
                    break
            continue
        while not castable(play):
            if not stack:
                raise ValueError("unplayable: %r" % (play,))
            resolve()
        do_cast(play, spells())
        casts += 1
    while stack and state["dmg"] < life:
        resolve()
    return lines, state["dmg"], casts


def copy_board(rng):
    """Copy a ritual while it is on the stack: Reverberate, Fork and Increasing Vengeance copy any instant or sorcery
    spell, and a copied Seething Song makes five more mana for {R}{R}, where a copied Bolt deals 3. The copier must
    be cast while the ritual waits on the stack, so {R}{R} is held back for it, and Vengeance, once in the graveyard,
    copies twice for {3}{R}{R}. Guttersnipe, Firebrand Archer and Witherbloom Apprentice turn every cast (and, for the
    Apprentice, every copy) into damage. The opponent's life is the exact maximum of copy_search; a board is kept
    only when the plain plan (copy only burn) falls 1 to 3 short, the best line takes 13 to 15 casts and leaves no
    mana unspent."""
    payoffs = sorted(rng.sample(sorted(COPY_PAYOFFS), rng.choice([1, 1, 2])))
    mountains = rng.choice([2, 3, 3, 4, 4, 5])
    copiers = [rng.choice(COPIERS) for _ in range(rng.choice([1, 2, 2, 3]))]
    if copiers.count(IV) > 1:
        return None                    # two Vengeances: the hand's cast and the graveyard's read alike
    hand = copiers + [rng.choice(sorted(COPY_RITUALS)) for _ in range(rng.choice([2, 3, 4, 5]))]
    hand += [rng.choice(sorted(COPY_FODDER)) for _ in range(rng.randint(GEN2_MIN - len(hand), GEN2_MAX + 1 - len(hand)))]
    dmg, steps, line = copy_search(hand, mountains, payoffs)
    if not GEN2_MIN <= steps <= GEN2_MAX or line[-1][1][4]:
        return None                    # the wrong length, or mana left unspent
    if not any(x[0] in ("cast", "flashback") and x[1] in COPIERS for x in line[:-1]):
        return None                    # the best line copies no ritual
    trap = copy_search(hand, mountains, payoffs, ritual_copies=False)[0]
    if not 1 <= dmg - trap <= 3:
        return None
    lines, dealt, casts = copy_lines(hand, mountains, payoffs, line, dmg)
    if (dealt, casts) != (dmg, steps):
        return None                    # the model of the stack and the search disagree: never write such a line
    rng.shuffle(hand)
    battlefield = payoffs + ["Mountain"] * mountains
    lands = "%s untapped Mountain%s" % (["", "one", "two", "three", "four", "five"][mountains],
                                        "" if mountains == 1 else "s")
    desc = ("It is your second main phase. You are at 20 life and control %s. Your hand is %s. Your graveyard is "
            "empty. The opponent is at %d life and controls nothing. Reduce the opponent to 0 life this turn."
            % (_join(payoffs + [lands]), _join(hand), dmg))
    rule = ("generated-copy | the exhaustive maximum is %d in %d casts, a copier aimed at a ritual on the stack; "
            "copying only burn deals at most %d" % (dmg, steps, trap))
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN2"), ("p0life", 20),
             ("p0hand", ";".join(hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", ";".join(["Colossal Dreadmaw"] * 10)),
             ("p1life", dmg), ("p1library", ";".join(["Plains"] * 10))]
    solution = ["# steps: %d" % steps] + lines
    return {"family": "copy", "actions": steps, "life": dmg, "trap": trap, "hand": sorted(hand),
            "battlefield": sorted(battlefield), "phase": "MAIN2",
            "search": {"hand": sorted(hand), "mountains": mountains, "payoffs": payoffs},
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}


# The grave family: black mana that counts the graveyard. (cost, mana made, sacrifices a creature as a cost,
# sorcery). Cabal Ritual makes 3, or 5 with seven or more cards in the graveyard as it resolves; Songs of the Damned
# makes one for each creature card there. Every card goes to the graveyard as it resolves. Consume Spirit ({X}{1}{B})
# is the last cast and spends what is left.
GRAVE_CARDS = {
    "Dark Ritual": (1, 3, False, False),
    "Cabal Ritual": (2, None, False, False),
    "Songs of the Damned": (1, None, False, False),
    "Culling the Weak": (1, 4, True, False),
    "Village Rites": (1, 0, True, False),
    "Unearth": (1, 0, False, True),
    "Gitaxian Probe": (0, 0, False, True),
    "Sign in Blood": (2, 0, False, True),
}
GRAVE_MANA = ("Cabal Ritual", "Culling the Weak", "Dark Ritual", "Songs of the Damned")
CORPSE, APPRENTICE, CONSUME = "Walking Corpse", "Witherbloom Apprentice", "Consume Spirit"
UNEARTH_CYCLING = 2


def grave_search(hand, swamps, corpses, apprentice, gy, gy_corpses, mana_first=False):
    """The most damage the board deals and the fewest steps that deal it, by exhaustive search over every order of
    the casts (and Unearth's cycling). Witherbloom Apprentice (on the battlefield when `apprentice` is True, in the
    graveyard when it is "gy", counted in `gy`) drains 1 for each instant or sorcery cast while it is on the
    battlefield; sacrificed as a cost, it drains nothing for that cast. Walking Corpses are the other bodies to
    sacrifice and the creature cards Unearth returns. Each spell resolves before the next is cast: casting one in
    response only ever has less mana to pay with and adds nothing to the graveyard sooner (a sacrifice is a cost, made
    as the spell is cast either way). Sign in Blood drains 2; Consume Spirit deals all the mana left but {1}{B}.

    mana_first=True is the plain plan: every mana spell before any other spell. Moves: ("cast", card, choice) with
    choice the creature sacrificed or returned, or X for Consume Spirit; ("cycle", "Unearth", None). Returns
    (damage, steps, moves); damage -1 when Consume Spirit cannot be cast."""
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def go(hand, mana, gy, gyc, gya, bfc, bfa, spent):
        if CONSUME in hand:
            best = (mana - 2 + bfa, 1, (("cast", CONSUME, mana - 2),)) if mana >= 2 else (-1, 0, ())
        else:
            best = (0, 0, ())
        plays = [(c, "cast") for c in sorted(set(hand)) if c != CONSUME]
        if "Unearth" in hand:
            plays.append(("Unearth", "cycle"))
        for c, how in plays:
            cost, made, sac, _ = GRAVE_CARDS[c]
            if how == "cycle":
                cost, made, sac = UNEARTH_CYCLING, 0, False
            if cost > mana or (mana_first and spent and c in GRAVE_MANA):
                continue
            if sac:
                choices = ([CORPSE] if bfc else []) + ([APPRENTICE] if bfa else [])
            elif c == "Unearth" and how == "cast":
                choices = ([CORPSE] if gyc else []) + ([APPRENTICE] if gya else [])
            else:
                choices = [None]
            rest = list(hand)
            rest.remove(c)
            for ch in choices:
                g, gc, ga, bc, ba = gy, gyc, gya, bfc, bfa
                if sac:                             # the cost: the creature is in the graveyard before anything
                    g += 1
                    gc, bc = (gc + 1, bc - 1) if ch == CORPSE else (gc, bc)
                    ga, ba = (True, False) if ch == APPRENTICE else (ga, ba)
                dmg = int(ba) if how == "cast" else 0
                if ch and not sac:                  # Unearth returns it
                    g -= 1
                    gc, bc = (gc - 1, bc + 1) if ch == CORPSE else (gc, bc)
                    ga, ba = (False, True) if ch == APPRENTICE else (ga, ba)
                if c == "Cabal Ritual":
                    made = 5 if g >= 7 else 3
                elif c == "Songs of the Damned":
                    made = gc + ga
                dmg += 2 if c == "Sign in Blood" else 0
                d, st, ln = go(tuple(rest), mana - cost + made, g + 1, gc, ga, bc, ba,
                               spent or c not in GRAVE_MANA or how == "cycle")
                if d >= 0 and (d + dmg, -(st + 1)) > (best[0], -best[1]):
                    best = (d + dmg, st + 1, ((how, c, ch),) + ln)
        return best

    on = apprentice is True
    gya = apprentice == "gy"
    return go(tuple(sorted(hand)), swamps, gy, gy_corpses, gya, corpses, on, False)


def grave_lines(hand, swamps, corpses, apprentice, gy, gy_corpses, moves):
    """The written line for grave_search's moves. Each spell resolves before the next is cast: a card the seat could
    cast with the last one still on the stack would be cast in response (ScriptBridge), so a "pass" is written for
    each object on the stack (the spell, and the Apprentice's trigger above it) exactly when the next card is
    castable; otherwise the line waits by itself. A sacrifice or Unearth's target is named when there are two or more
    creatures to pick from; Consume Spirit asks X, then its target. Returns (lines, damage, steps)."""
    mana, bfc, bfa = swamps, corpses, apprentice is True
    gyc, gya = gy_corpses, apprentice == "gy"
    lines, dmg = [], 0

    def castable(move):
        how, c, _ = move
        cost, _, sac, sorcery = GRAVE_CARDS.get(c, (2, 0, False, True))
        if how == "cycle":
            return mana >= UNEARTH_CYCLING
        return not sorcery and cost <= mana and (not sac or bfc + bfa > 0)

    for i, (how, c, ch) in enumerate(moves):
        if how == "cycle":
            lines.append("activate Unearth")
            mana -= UNEARTH_CYCLING
            stack = 0                       # cycling is an ability: nothing triggers, it resolves by itself
            made = 0
        else:
            lines.append("cast " + c)
            cost, made, sac, _ = GRAVE_CARDS.get(c, (2 + ch if c == CONSUME else 0, 0, False, True))
            if c == CONSUME:
                lines += [str(ch), "opponent"]
            if sac:
                if bfc + bfa > 1:
                    lines.append(ch)
                gy += 1
                gyc, bfc = (gyc + 1, bfc - 1) if ch == CORPSE else (gyc, bfc)
                gya, bfa = (True, False) if ch == APPRENTICE else (gya, bfa)
            if c == "Unearth" and gyc + gya > 1:
                lines.append(ch)
            if c in ("Gitaxian Probe", "Sign in Blood"):
                lines.append("opponent")
            mana -= cost
            stack = 1 + int(bfa)
            dmg += int(bfa) + (2 if c == "Sign in Blood" else 0) + (ch if c == CONSUME else 0)
            if c == "Cabal Ritual":
                made = 5 if gy >= 7 else 3
            elif c == "Songs of the Damned":
                made = gyc + gya
        nxt = moves[i + 1] if i + 1 < len(moves) else None
        if nxt and castable(nxt):
            lines += ["pass"] * stack
        mana += made                        # the spell resolves
        gy += 1
        if c == "Unearth" and how == "cast":
            gy -= 1
            gyc, bfc = (gyc - 1, bfc + 1) if ch == CORPSE else (gyc, bfc)
            gya, bfa = (False, True) if ch == APPRENTICE else (gya, bfa)
    return lines, dmg, len(moves)


def grave_board(rng):
    """Threshold and Songs: Cabal Ritual makes 5 instead of 3 once seven cards are in the graveyard as it resolves,
    and Songs of the Damned makes one for each creature card there, so the cheap spells, the sacrifices (Culling the
    Weak, Village Rites) and Unearth's cycling go first and the big rituals wait. Witherbloom Apprentice drains 1 for
    each spell, so it is sacrificed only when its body is worth more than the drains left, and Unearth can bring it
    (or a Walking Corpse) back to be sacrificed again. Consume Spirit spends the rest. The opponent's life is the
    exact maximum of grave_search; a board is kept only when the plain plan (every mana spell first) falls 1 to 3
    short and the best line is 13 to 15 steps."""
    swamps = rng.choice([1, 2, 2, 3])
    corpses = rng.choice([0, 1, 1, 2])
    gy_corpses = rng.choice([0, 0, 1, 2])
    fillers = [rng.choice(["Dark Ritual", "Duress", "Gitaxian Probe", "Sign in Blood"])
               for _ in range(rng.choice([0, 1, 2, 3, 4]))]
    pool = ["Dark Ritual", "Cabal Ritual", "Cabal Ritual", "Songs of the Damned", "Culling the Weak", "Village Rites",
            "Unearth", "Gitaxian Probe", "Sign in Blood"]
    hand = [CONSUME] + [rng.choice(pool) for _ in range(rng.choice([12, 13, 14]))]
    apprentice = "gy" if "Unearth" in hand and rng.random() < 0.2 else True
    gy = gy_corpses + len(fillers) + (apprentice == "gy")
    dmg, steps, moves = grave_search(hand, swamps, corpses, apprentice, gy, gy_corpses)
    if not GEN2_MIN <= steps <= GEN2_MAX:
        return None
    trap = grave_search(hand, swamps, corpses, apprentice, gy, gy_corpses, mana_first=True)[0]
    if not 1 <= dmg - trap <= 3:
        return None
    lines, dealt, n = grave_lines(hand, swamps, corpses, apprentice, gy, gy_corpses, moves)
    if (dealt, n) != (dmg, steps):
        return None
    rng.shuffle(hand)
    graveyard = [CORPSE] * gy_corpses + fillers + ([APPRENTICE] if apprentice == "gy" else [])
    rng.shuffle(graveyard)
    battlefield = ([APPRENTICE] if apprentice is True else []) + [CORPSE] * corpses + ["Swamp"] * swamps
    count = ["no", "one", "two", "three"]
    seen = ([APPRENTICE] if apprentice is True else []) + (["%s Walking Corpse%s" % (count[corpses], "s" * (corpses > 1))]
                                                           if corpses else [])
    seen.append("%s untapped Swamp%s" % (count[swamps], "s" * (swamps > 1)))
    desc = ("It is your second main phase. You are at 20 life and control %s. Your hand is %s. Your graveyard holds "
            "%s. The opponent is at %d life and controls nothing. Reduce the opponent to 0 life this turn."
            % (_join(seen), _join(hand), _join(graveyard) if graveyard else "nothing", dmg))
    counters = [c for c in ("Cabal Ritual", "Songs of the Damned") if c in hand] or ["Consume Spirit"]
    rule = ("generated-grave | the exhaustive maximum is %d in %d steps, the graveyard filled before %s resolve%s; "
            "every mana spell first deals at most %d" % (dmg, steps, _join(counters), "s" * (len(counters) == 1), trap))
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN2"), ("p0life", 20),
             ("p0hand", ";".join(hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0graveyard", ";".join(graveyard)),
             ("p0library", ";".join(["Colossal Dreadmaw"] * 12)),
             ("p1life", dmg), ("p1library", ";".join(["Plains"] * 12))]
    state = [kv for kv in state if kv[1] != ""]
    return {"family": "grave", "actions": steps, "life": dmg, "trap": trap, "hand": sorted(hand),
            "battlefield": sorted(battlefield + ["gy:" + g for g in graveyard]), "phase": "MAIN2",
            "search": {"hand": sorted(hand), "swamps": swamps, "corpses": corpses, "apprentice": apprentice,
                       "gy": gy, "gy_corpses": gy_corpses},
            "text": lambda name: pzl_text(name, desc, rule, state, ["# steps: %d" % steps] + lines,
                                          ["life p1 <= 0"])}


FAMILIES = {"ping": ping_board, "storm": storm_board, "splice": splice_board, "red": red_board, "drain": drain_board,
            "elf": elf_board, "fling": fling_board, "grave": grave_board, "copy": copy_board}


def board_key(b):
    """Two draws with the same cards, life and phase are one board."""
    return hashlib.sha256(json.dumps([b["family"], b["hand"], b["battlefield"], b["life"], b["phase"]])
                          .encode()).hexdigest()[:12]


def generate(family, count, seed, tries=20000):
    rng = random.Random(seed)
    seen, out = set(), []
    for _ in range(tries):
        if len(out) >= count:
            break
        b = FAMILIES[family](rng)
        if b is None:
            continue
        key = board_key(b)
        if key in seen:
            continue
        seen.add(key)
        b["key"] = key
        out.append(b)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", choices=sorted(FAMILIES), required=True)
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    boards = generate(a.family, a.count, a.seed)
    for b in boards:
        name = "%s_%s" % (a.family, b["key"])
        (out / (name + ".pzl")).write_text(b["text"](name), encoding="utf-8")
    print("%d %s boards in %s" % (len(boards), a.family, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
