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


def _count(names):
    """'two Dark Rituals'-free plain listing: repeated names are written each time, as the hand shows them."""
    return _join(names)


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
               "" if mountains == 1 else "s", _count(hand), damage))
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


STORM_RITUALS = {"Rite of Flame": (0, 1, 2), "Desperate Ritual": (1, 1, 3), "Pyretic Ritual": (1, 1, 3),
                 "Seething Song": (2, 1, 5)}
STORM_FREE = ("Gitaxian Probe", "Lotus Petal")


def storm_board(rng):
    """Every spell cast before Grapeshot is one more copy, so the whole hand goes first and Grapeshot last. The
    mana is all red and exact: each ritual must be payable when it is cast (Seething Song needs three open; a Rite
    of Flame adds one more for every Rite already in the graveyard, so the Rites go back to back), and burn spent
    before Grapeshot also feeds the storm. A copy's target is a choice, not a step, so steps count casts only.
    Returns None when the draw cannot make a legal board of the right length."""
    mountains = rng.choice([1, 2, 2, 3])
    names = sorted(STORM_RITUALS)
    rituals = [rng.choice(names) for _ in range(rng.choice([2, 3, 3, 4]))]
    # An order that can be paid: cheapest first, Rites together, Seething Song once three are open.
    order = sorted(rituals, key=lambda r: (STORM_RITUALS[r][0] + STORM_RITUALS[r][1], r != "Rite of Flame"))
    petals = rng.choice([0, 1, 1, 2])
    pool, lands, rites, petal_mana = 0, mountains, 0, petals
    for r in order:
        generic, red, added = STORM_RITUALS[r]
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
    pool += lands + petal_mana
    if pool < 2:
        return None
    burn = [rng.choice(sorted(BURN)) for _ in range(pool - 2)]   # Grapeshot takes the last two
    probes = rng.choice([0, 1, 1, 2])
    before = order + ["Lotus Petal"] * petals + ["Gitaxian Probe"] * probes + burn
    steps = len(before) + 1
    if not MIN_ACTIONS <= steps <= MAX_ACTIONS:
        return None
    storm = len(before)
    damage = storm + 1 + sum(BURN[b][0] for b in burn)
    herring = rng.random() < 0.4
    hand = before + ["Grapeshot"] + (["Goblin Guide"] if herring else [])
    rng.shuffle(hand)
    # Petals are cast first (free) so the engine can spend them when it pays; the rituals in their order; the
    # Probes; the burn; Grapeshot and a target for it and for each copy.
    solution = ["# steps: %d" % steps] + ["cast Lotus Petal"] * petals + ["cast " + r for r in order]
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
    untapped. Returns (damage, steps, line) with line a list of (spell, splices) and None for a ping, or the best
    without splicing when allow_splice is False."""
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def go(hand, mana, untapped, allow):
        best = (0, 0, ())
        if untapped and alchemist:
            d, st, ln = go(hand, mana, False, allow)
            best = max(best, (d + 1, st + 1, (None,) + ln), key=lambda x: (x[0], -x[1]))
        for i, s in enumerate(sorted(set(hand))):
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
        offered = [x for x in set(held) if RED[x][4] is not None and RED[x][6]
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


FAMILIES = {"ping": ping_board, "storm": storm_board, "splice": splice_board, "red": red_board}


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
