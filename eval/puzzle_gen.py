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
Desperate Ritual or Glacial Ray), splice2 (the same, 13 to 15 steps, with Kiln Fiend and Goblin Electromancer, kept
only with a trap: the better of two plain plans falls 1 to 3 short), red (all of these with flashback and Kiln
Fiend), drain (sacrifice and recursion loops with Blood Artist and friends), elf (Heritage Druid and Nettle Sentinel
into Craterhoof Behemoth) and fling (prowess creatures attack, then Fling after combat). The splice, splice2, red,
drain, elf and fling boards set the opponent's life to the exact maximum of an exhaustive search over a model of the
board.

The super-hard families drain2, elf2 and hoofdrain (elves into Craterhoof with drainers and Viscera Seer) also hold
each board to 13 to 15 steps, no mana left unused at the end of the best line (no Swamp untapped or land drop
unplayed; no green floating past Craterhoof), and a trap: the best plain plan (DRAIN_PLANS, ELF_PLANS,
DRAIN_ELF_PLANS: one natural habit imposed on an otherwise exhaustive search, the best over the habits that apply)
falls 1 to 3 short of the maximum. A batch never repeats a life total or a structure (`shape`). Each such board states
its search inputs and trap as `# search:` and `# trap:` comments in its [solution], so a test recomputes it.

  python3 eval/puzzle_gen.py --family ping --count 40 --seed 1 --out DIR
  python3 eval/puzzle_gen.py --family drain2 --count 40 --seed 2 --tries 40000 --out DIR
"""
import argparse
import hashlib
import itertools
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


def splice_sets(rest, splices):
    """Every multiset of splice cards an Arcane spell can carry from the cards `rest` still in hand: Forge offers
    each copy, so two Desperate Rituals can ride one spell (CR 702.47b forbids only one card twice)."""
    names = sorted(set(x for x in rest if splices(x)))
    return [tuple(n for n, k in zip(names, ks) for _ in range(k))
            for ks in itertools.product(*[range(rest.count(n) + 1) for n in names])]


def splice_best(hand, mana, alchemist):
    """The most damage the hand can deal, and one line that deals it, by exhaustive search over a model of the
    board: all mana red and paid from the pool (lands are only more pool); an Arcane spell may carry any of the
    splice cards still in hand, any copies of each (each paid again and kept); a ping before each spell while
    Thermo-Alchemist is untapped. Returns (with_splice, without), each (damage, steps, line) with line a list of
    (spell, splices) and None for a ping."""
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
            options = splice_sets(rest, lambda x: SPLICE_CARDS[x][6] is not None) if arcane and allow else [()]
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
    items = ["ping" if x is None else ("cast", x[0], x[1]) for x in line]
    try:
        lines, dealt, _ = written_line(items, hand, mountains, alchemist, False, False)
    except ValueError:
        return None
    if dealt != dmg:
        return None
    solution = ["# steps: %d" % steps] + lines
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


SORCERIES = {"Lava Spike", "Rite of Flame", "Grapeshot", "Gitaxian Probe"}   # cast only on an empty stack


def red_cost(c, kind, chosen, reducer):
    """What casting `c` (with the splice cards `chosen`) costs: (mana, Mountains sacrificed)."""
    if c == "Fireblast":
        return 0, 2
    if kind == "flashback":
        return 0, 1
    g = RED[c][0] + sum(RED[x][6][0] for x in chosen)
    r = RED[c][1] + sum(RED[x][6][1] for x in chosen)
    if reducer and c != "Gitaxian Probe":
        g = max(0, g - 1)
    return g + r, 0


def red_search(hand, mountains, alchemist, reducer, splice=True, kiln=False):
    """The most damage the hand can deal and one line that deals it, by exhaustive search. Mana is all red and is
    paid from the pool first, then from untapped Mountains; a Mountain is sacrificed (Fireblast, Lava Dart's
    flashback) only once every Mountain is tapped, so no mana is lost to it; Goblin Electromancer takes {1} off each
    spell. An Arcane spell carries any multiset of the splice cards in hand (`splice` True), none (False), or only
    a maximal payable one ("max": every splice card it can still pay for, the greedy splicer). With `kiln`, each
    instant or sorcery cast adds Kiln Fiend's +3 to the damage, so a cast that only pumps the Fiend counts (its base
    power and Goblin Electromancer's attack are the caller's). Among the lines that deal the most, one that leaves
    the least mana unspent, then the fewest steps. Line items: "ping", ("cast", card, splices) or ("flashback",
    "Lava Dart", ()). Returns (damage, steps, line, casts, mana left)."""
    from functools import lru_cache

    def better(a, b):
        return a if (a[0], -a[4], -a[1]) >= (b[0], -b[4], -b[1]) else b

    @lru_cache(maxsize=None)
    def go(hand, darts, rites, pool, untapped, mtns, alch, storm):
        best = (0, 0, (), 0, pool + untapped)
        if alch:
            d, st, ln, n, left = go(hand, darts, rites, pool, untapped, mtns, False, storm)
            best = better((d + 1, st + 1, ("ping",) + ln, n, left), best)
        moves = [("cast", c) for c in sorted(set(hand))] + ([("flashback", "Lava Dart")] if darts else [])
        for kind, c in moves:
            add, dmg, arcane = RED[c][2], RED[c][3], RED[c][4]
            rest = list(hand)
            if kind == "cast":
                rest.remove(c)
            sac = red_cost(c, kind, (), reducer)[1]
            if sac and (untapped or mtns < sac):
                continue
            options = splice_sets(rest, lambda x: RED[x][6]) if arcane and splice and kind == "cast" else [()]
            options = [o for o in options if red_cost(c, kind, o, reducer)[0] <= pool + untapped]
            if splice == "max":
                options = [o for o in options if not any(
                    o.count(x) < rest.count(x) and red_cost(c, kind, o + (x,), reducer)[0] <= pool + untapped
                    for x in set(rest) if RED[x][6])]
            for chosen in options:
                cost = red_cost(c, kind, chosen, reducer)[0]
                from_pool = min(pool, cost)
                npool, nun = pool - from_pool, untapped - (cost - from_pool)
                gain = add + sum(RED[x][7] for x in chosen)
                hit = dmg + sum(RED[x][8] for x in chosen) + (3 if kiln else 0)
                nrites = rites
                if c == "Rite of Flame":
                    gain += rites
                    nrites += 1
                if c == "Grapeshot":
                    hit += storm
                ndarts = darts + (1 if c == "Lava Dart" and kind == "cast" else 0) - (1 if kind == "flashback" else 0)
                d, st, ln, n, left = go(tuple(sorted(rest)), ndarts, nrites, npool + gain, nun, mtns - sac,
                                        bool(alchemist), storm + 1)
                best = better((d + hit, st + 1, ((kind, c, chosen),) + ln, n + 1, left), best)
        return best
    return go(tuple(sorted(hand)), 0, 0, 0, mountains, mountains, bool(alchemist), 0)


def written_line(line, hand, mountains, alchemist, reducer, kiln):
    """The written [solution] for a red_search line, played on a model of the stack the way the script arm plays it
    (ScriptBridge): each line is taken at the first priority where it fits, and while it does not fit the stack
    resolves one object a pass. A card is castable the moment its own cost is, so a spell would go on the stack
    over a ritual that has not resolved, and a splice that needs that ritual's mana is then not offered at all
    (splice_2b37741c0e25, 2026-10-20: 11 of 40 splice lines stopped there). So before each cast the line passes,
    one "pass" an object, until no unresolved ritual is left: every cast sees the pool the search paid it from.
    A pass is written only where the seat is asked (the cast fits, so it is). Lava Spike, Rite of Flame,
    Grapeshot and Gitaxian Probe wait for an empty stack by themselves.

    Each cast is followed by its questions in the engine's order: the target, the splice cards it could pay for
    ("none" when it takes none), every target again after a splice (h_1), the Mountains a sacrifice takes when
    there is a choice, and the order of two or more of the seat's triggers (the first named resolves first). A
    Grapeshot copy's target is asked when the storm trigger resolves.

    Returns (lines, damage the model deals, mana left). Raises ValueError when the line cannot be played as the
    search meant it."""
    out, held, stack = [], list(hand), []
    s = {"pool": 0, "untapped": mountains, "mtns": mountains, "alch": bool(alchemist), "rites": 0, "yard": 0,
         "dealt": 0, "power": 0}
    storm = 0

    def resolve():
        top = stack.pop()
        if top[0] == "obj":
            _, add, rite, hit, dart = top
            s["pool"] += add + (s["rites"] if rite else 0)
            s["rites"] += rite
            s["dealt"] += hit
            s["yard"] += dart
        elif top[0] == "untap":
            s["alch"] = True
        elif top[0] == "pump":
            s["power"] += 3
        elif top[0] == "storm":
            out.extend(["opponent"] * top[1])
            stack.extend([("obj", 0, 0, 1, 0)] * top[1])
        elif top[0] == "ping":
            s["dealt"] += 1

    def fits(item):
        if item == "ping":
            return s["alch"]
        kind, c, _ = item
        if c in SORCERIES and stack:
            return False
        if kind == "flashback":
            return s["yard"] > 0 and s["mtns"] >= 1
        if c not in held:
            return False
        cost, sac = red_cost(c, kind, (), reducer)
        return s["mtns"] >= sac if sac else cost <= s["pool"] + s["untapped"]

    for item in line:
        while not fits(item):
            if not stack:
                raise ValueError("%r cannot be played on an empty stack" % (item,))
            resolve()
        if item == "ping":
            out.append("activate Thermo-Alchemist")
            s["alch"] = False
            stack.append(("ping",))
            continue
        while any(o[0] == "obj" and (o[1] or o[2]) for o in stack):
            out.append("pass")
            resolve()
        kind, c, chosen = item
        if kind == "cast":
            held.remove(c)
            out.append("cast Fireblast ... cost: Sacrifice two Mountains" if c == "Fireblast" else "cast " + c)
        else:
            out.append("cast Lava Dart ... from your graveyard")
        tgt = RED[c][5]
        if tgt in ("any", "player"):
            out.append("opponent")
        avail = s["pool"] + s["untapped"]
        if kind == "cast" and RED[c][4]:
            offered = {x for x in held if RED[x][6] and red_cost(c, kind, (x,), reducer)[0] <= avail}
            if not set(chosen) <= offered or any(chosen.count(x) > held.count(x) for x in chosen):
                raise ValueError("%s: splice %r not offered" % (c, chosen))
            if offered:
                out.append("; ".join(chosen) if chosen else "none")
                if chosen:
                    out.extend(["opponent"] * ((tgt in ("any", "player")) + sum(RED[x][5] == "any" for x in chosen)))
        elif chosen:
            raise ValueError("%s carries no splice" % c)
        cost, sac = red_cost(c, kind, chosen, reducer)
        if cost > avail or (sac and s["untapped"]):
            raise ValueError("%s: cost %d of %d, %d Mountains untapped" % (c, cost, avail, s["untapped"]))
        if sac and s["mtns"] > sac:
            out.append("; ".join(["Mountain"] * sac))
        from_pool = min(s["pool"], cost)
        s["pool"] -= from_pool
        s["untapped"] -= cost - from_pool
        s["mtns"] -= sac
        if kind == "flashback":
            s["yard"] -= 1
        triggers = (["Thermo-Alchemist"] if alchemist else []) + (["Kiln Fiend"] if kiln else []) \
            + (["Grapeshot"] if c == "Grapeshot" else [])
        if len(triggers) > 1:
            out.append("; ".join(triggers))
        add = RED[c][2] + sum(RED[x][7] for x in chosen)
        hit = RED[c][3] + sum(RED[x][8] for x in chosen)
        stack.append(("obj", add, 1 if c == "Rite of Flame" else 0, hit, 1 if c == "Lava Dart" and kind == "cast"
                      else 0))
        for t in reversed(triggers):
            stack.append({"Thermo-Alchemist": ("untap",), "Kiln Fiend": ("pump",), "Grapeshot": ("storm", storm)}[t])
        storm += 1
    while stack:
        resolve()
    return out, s["dealt"] + s["power"], s["pool"] + s["untapped"]


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
    dmg, steps, line, _, _ = red_search(hand, mountains, alchemist, reducer, kiln=kiln)
    base = attack_base(kiln, reducer)
    naive, naive_casts = red_naive(hand, mountains, alchemist, reducer)
    naive += base + (3 * naive_casts if kiln else 0)
    total = dmg + base
    steps += 1 if base else 0
    if naive >= total or not MIN_ACTIONS <= steps <= MAX_ACTIONS:
        return None
    b = red_board_dict("red", hand, mountains, alchemist, reducer, kiln, line, steps, total,
                       "the searched best line deals %d; a plain player deals %d" % (total, naive), rng)
    if b:
        b["naive"] = naive
    return b


# The super-hard splice family's other cards: Fireblast and Lava Dart are left out (their sacrifice wants every
# Mountain tapped first, which the search models only by order, not by floating mana).
SPLICE2_OTHERS = ("Lava Spike", "Lava Spike", "Lightning Bolt", "Shock", "Pyretic Ritual", "Rite of Flame",
                  "Seething Song", "Gitaxian Probe", "Grapeshot")
SPLICE2_STEPS = (13, 15)
SPLICE2_TRAP = (1, 3)        # how far short the better plain plan must fall


def splice2_board(rng):
    """Super-hard splice: Desperate Ritual and Glacial Ray spliced onto Arcane spells (any copies), with
    Thermo-Alchemist pinging before each spell and, on some boards, Kiln Fiend (+3 an instant or sorcery) and Goblin
    Electromancer ({1} off each) attacking. The opponent's life is the exact maximum of red_search, and the line
    spends every mana. Kept only when it is 13 to 15 steps and two plain plans, each played as well as it can be,
    fall 1 to 3 short (the trap): never splicing, and splicing every card it can pay for onto each Arcane spell (the
    greedy splicer). A plan 4 or more short is too plainly different; one that wins is no trap."""
    mountains = rng.choice([1, 2, 2, 3])
    alchemist = rng.random() < 0.9
    kiln = rng.random() < 0.45
    reducer = rng.random() < 0.3
    hand = ["Desperate Ritual"] * rng.choice([1, 2, 2, 3]) + ["Glacial Ray"] * rng.choice([0, 1, 1, 2])
    hand += [rng.choice(SPLICE2_OTHERS) for _ in range(rng.choice([6, 7, 7, 8]) - len(hand))]
    if hand.count("Grapeshot") > 1:
        return None
    dmg, steps, line, _, left = red_search(hand, mountains, alchemist, reducer, kiln=kiln)
    base = attack_base(kiln, reducer)
    total, steps = dmg + base, steps + (1 if base else 0)
    if left or not SPLICE2_STEPS[0] <= steps <= SPLICE2_STEPS[1] \
            or not any(x != "ping" and x[2] for x in line):
        return None
    never = red_search(hand, mountains, alchemist, reducer, False, kiln)[0] + base
    if not SPLICE2_TRAP[0] <= total - never:
        return None
    greedy = red_search(hand, mountains, alchemist, reducer, "max", kiln)[0] + base
    trap = max(never, greedy)
    if not SPLICE2_TRAP[0] <= total - trap <= SPLICE2_TRAP[1]:
        return None
    why = ("the searched best line deals %d, spending every mana; trap %d (never splicing deals %d, splicing every "
           "payable card onto each Arcane spell %d)" % (total, trap, never, greedy))
    b = red_board_dict("splice2", hand, mountains, alchemist, reducer, kiln, line, steps, total, why, rng)
    if b:
        b.update(trap=trap, never=never, greedy=greedy)
    return b


def attack_base(kiln, reducer):
    """What the attackers deal before any pump: Kiln Fiend's 1 power, Goblin Electromancer's 2."""
    return (1 if kiln else 0) + (2 if reducer else 0)


def red_board_dict(family, hand, mountains, alchemist, reducer, kiln, line, steps, total, why, rng):
    """The board for a red_search line, written by written_line; None when the written line would not play as the
    search meant it or would deal other than `total`."""
    try:
        lines, dealt, _ = written_line(line, hand, mountains, alchemist, reducer, kiln)
    except ValueError:
        return None
    if dealt + attack_base(kiln, reducer) != total:
        return None
    attackers = (["Kiln Fiend"] if kiln else []) + (["Goblin Electromancer"] if reducer else [])
    solution = ["# steps: %d" % steps] + lines + (["; ".join(attackers)] if attackers else [])
    creatures = (["Thermo-Alchemist"] if alchemist else []) + attackers
    battlefield = creatures + ["Mountain"] * mountains
    phase = "MAIN1" if attackers else "MAIN2"
    shown = list(hand)
    rng.shuffle(shown)
    desc = ("It is your %s main phase. You are at 20 life and control %s%s untapped Mountain%s. Your hand is %s. "
            "The opponent is at %d life and controls nothing. Reduce the opponent to 0 life this turn."
            % ("first" if attackers else "second", (_join(creatures) + " and ") if creatures else "",
               ["", "one", "two", "three"][mountains], "" if mountains == 1 else "s", _join(shown), total))
    rule = "generated-%s | %s" % (family, why)
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", phase), ("p0life", 20),
             ("p0hand", ";".join(shown)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", total), ("p1library", "Plains;Plains;Plains")]
    return {"family": family, "actions": steps, "life": total, "hand": sorted(hand), "attackers": attackers,
            "battlefield": sorted(battlefield), "phase": phase,
            "search": {"hand": sorted(hand), "mountains": mountains, "alchemist": alchemist, "reducer": reducer,
                       "kiln": kiln, "attack_base": attack_base(kiln, reducer)},
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


# The plain plans a drain board is held to (drain2): each is a natural rule a player might follow, applied on its own
# to the search; the trap is the best of those that bind on the board.
DRAIN_PLANS = {
    "land_first": "play the land first",
    "drainers_last": "cast the drainers last",
    "sac_asap": "sacrifice as soon as a death drains",
}


def _drain_plan_applies(plan, land, hand):
    if plan == "land_first":
        return land
    if plan == "drainers_last":
        return any(d in hand for d in DRAINERS) and any(c not in DRAINERS for c in hand)
    return True


def _drain_plan_moves(plan, st, moves):
    """The moves a plain plan allows from `st`."""
    if plan == "land_first" and st[2]:
        return [m for m in moves if m[0][0] == "land"]
    if plan == "drainers_last":
        other = any(m[0][0] == "cast" and m[0][1] not in DRAINERS for m in moves)
        return [m for m in moves if not (other and m[0][0] == "cast" and m[0][1] in DRAINERS)]
    if plan == "sac_asap" and any(m[0][0] == "sac" and m[2] for m in moves):
        return [m for m in moves if m[0][0] == "sac"]
    return moves


def drain_search(swamps, land, bf, gy, hand, altar, plan=None, spend=False):
    """The most life the board drains this turn, and the fewest steps that reach it: an exhaustive search over every
    order of casts, sacrifices, returns and the land drop. Returns (drain, steps, moves). `plan` holds the search to
    a plain plan (DRAIN_PLANS); `spend` breaks a tie between equal lines toward the one that leaves fewer Swamps
    untapped and no land in hand (drain_unused)."""
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def best(st):
        top = (0, 0, (), (st[1] + st[2]) if spend else 0)
        for move, nxt, drain, step in _drain_plan_moves(plan, st, _drain_moves(st, altar)):
            d, s, line, w = best(nxt)
            cand = (d + drain, s + step, (move,) + line, w)
            if cand[0] > top[0] or (cand[0] == top[0] and cand[0] > 0
                                    and ((cand[1], cand[3]) < (top[1], top[3]) or not top[2])):
                top = cand
        return top

    return best((0, swamps, land, frozenset(bf), frozenset(gy), frozenset(hand)))[:3]


def drain_unused(swamps, land, bf, gy, hand, altar, moves):
    """Mana the line leaves unused: the Swamps still untapped at its end, and the land drop not taken."""
    st = (0, swamps, land, frozenset(bf), frozenset(gy), frozenset(hand))
    for move in moves:
        st = next(nxt for m, nxt, _, _ in _drain_moves(st, altar) if m == move)
    return st[1] + st[2]


def drain_plain(swamps, land, bf, gy, hand, altar):
    """What each plain plan that binds on the board drains: {plan: drain}."""
    return {p: drain_search(swamps, land, bf, gy, hand, altar, plan=p)[0] for p in DRAIN_PLANS
            if _drain_plan_applies(p, land, hand)}


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
    return _drain_board(swamps, land, bf, gy, hand, altar, drain, steps, moves)


def _record_lines(search, trap):
    """drain2, elf2 and hoofdrain state their search's inputs and the trap in the [solution] as comments (the line
    reader skips `#` lines), so a test can recompute a committed board (board_record)."""
    if not trap:
        return []
    return ["# search: " + json.dumps(search, sort_keys=True), "# trap: " + json.dumps(trap, sort_keys=True)]


def board_record(text):
    """The family, life, steps, search inputs and trap a drain2, elf2 or hoofdrain board states."""
    rec = {"family": text.split("Rule:generated-", 1)[1].split()[0],
           "life": int(text.split("\np1life=", 1)[1].split()[0])}
    for line in text.splitlines():
        for key in ("steps", "search", "trap"):
            if line.startswith("# %s: " % key):
                rec[key] = json.loads(line.split(": ", 1)[1])
    return rec


def research(rec):
    """Recompute a board from its stated search inputs: (best, steps, unused mana, {plan: value})."""
    s = rec["search"]
    if rec["family"] == "drain2":
        args = (s["swamps"], s["land"], set(s["bf"]), set(s["gy"]), set(s["hand"]), s["altar"])
        d, st, moves = drain_search(*args, spend=True)
        return d, st, drain_unused(*args, moves), drain_plain(*args)
    costs = [ELF_HAND[n] for n in s["elves"]]
    args = (s["forests"], s["sentinels"], len(s["mana_elves"]), costs)
    kw = dict(priest=s["priest"], others=tuple(HOOFDRAIN_OTHERS[o] for o in s["others"]),
              drain=sum(o in DRAINERS for o in s["others"]))
    d, st, moves = elf_search(*args, spend=True, **kw)
    return d, st, elf_unused(*args, moves, s["priest"]), elf_plain(*args, **kw)


def _trap_note(trap, plans, unit):
    """The Rule line's trap clause: the best plain plan, what it deals and how far short it falls."""
    names = [p for p, v in trap["plans"].items() if v == trap["best"]]
    return ("; the best plain plan (%s) %s %d, %d short" % (" / ".join(plans[p] for p in names), unit, trap["best"],
                                                           trap["short"]))


def _drain_board(swamps, land, bf, gy, hand, altar, drain, steps, moves, trap=None):
    """The board dict for a searched drain line (`trap` set for drain2)."""
    lines = drain_lines(swamps, land, bf, gy, hand, altar, moves, drain)
    battlefield = ([ALTAR] if altar else []) + sorted(bf) + ["Swamp"] * swamps
    hand_list = sorted(hand) + (["Swamp"] if land else [])
    graveyard = sorted(gy)
    search = {"swamps": swamps, "land": land, "bf": sorted(bf), "gy": sorted(gy), "hand": sorted(hand), "altar": altar}
    solution = ["# steps: %d" % steps] + _record_lines(search, trap) + lines
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
    family = "drain2" if trap else "drain"
    rule = "generated-%s | the exhaustive maximum is %d drained in %d steps: %s%s" % (
        family, drain, steps, "; ".join(notes), _trap_note(trap, DRAIN_PLANS, "drains") if trap else "")
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN2"), ("p0life", 20),
             ("p0hand", ";".join(hand_list)), ("p0battlefield", ";".join(battlefield)),
             ("p0graveyard", ";".join(graveyard)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", drain), ("p1library", "Plains;Plains;Plains")]
    state = [kv for kv in state if kv[1] != ""]
    return {"family": family, "trap": trap, "unused": 0, "actions": steps, "life": drain, "hand": sorted(hand_list),
            "battlefield": sorted(battlefield + ["gy:" + g for g in graveyard]), "phase": "MAIN2",
            "search": search,
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}




HARD_MIN, HARD_MAX = 13, 15            # drain2 and elf2: the "super hard" length
TRAP_MIN, TRAP_MAX = 1, 3              # how far short the best plain plan must fall


def _trap(best, plain):
    """The trap record for a board whose searched best is `best`, or None when no plain plan binds, one wins, or the
    best of them falls 4 or more short."""
    if not plain:
        return None
    top = max(plain.values())
    if not TRAP_MIN <= best - top <= TRAP_MAX:
        return None
    return {"plans": plain, "best": top, "short": best - top}


def drain2_board(rng):
    """The drain engine made super hard: two or three sacrifice outlets that differ (Ashnod's Altar's mana, Carrion
    Feeder the Zombie that lets Gravecrawler back, Viscera Seer's scry), more recursion, Bloodghast with the land drop
    more often. Kept only at 13 to 15 steps with every Swamp and the land drop spent on the best line, and when the
    best plain plan (DRAIN_PLANS: land first, drainers last, sacrifice at once) falls 1 to 3 short."""
    swamps = rng.choice([2, 3, 3, 4, 4, 5, 5, 6])
    outlets = rng.sample((ALTAR,) + COUNTED_OUTLETS, rng.choice([2, 2, 3]))
    if not any(o in COUNTED_OUTLETS for o in outlets):
        return None
    altar = ALTAR in outlets
    bf, gy, hand = set(), set(), set()
    put = {"bf": bf, "hand": hand, "gy": gy}
    for d in rng.sample(DRAINERS, rng.choice([1, 2, 2, 3])):
        put["bf" if d == "Cruel Celebrant" else rng.choice(["bf", "hand", "hand"])].add(d)
    for o in outlets:
        if o != ALTAR:
            put[rng.choice(["bf", "bf", "hand"])].add(o)
    for r, p in (("Reassembling Skeleton", 0.85), ("Bloodghast", 0.7), ("Gravecrawler", 0.6)):
        if rng.random() < p:
            put[rng.choice(["bf", "gy", "hand"])].add(r)
    land = rng.random() < (0.9 if "Bloodghast" in bf | gy | hand else 0.6)
    if not bf:
        return None
    args = (swamps, land, bf, gy, hand, altar)
    drain, steps, moves = drain_search(*args, spend=True)
    if not HARD_MIN <= steps <= HARD_MAX or drain < 9 or drain_unused(*args, moves):
        return None
    trap = _trap(drain, drain_plain(*args))
    if trap is None:
        return None
    b = _drain_board(*args, drain, steps, moves, trap)
    b["shape"] = ("drain2", swamps, land, altar, tuple(sorted(
        (zone, "drainer" if c in DRAINERS else c) for zone, cards in (("bf", bf), ("gy", gy), ("hand", hand))
        for c in cards)))
    return b


# The elf family. Hand elves by name and cost in {G}: none has an ability a summoning-sick elf can use, so each is
# only a body (one more creature for Craterhoof, one more elf for Heritage Druid to tap).
ELF_HAND = {"Willow Elf": 1, "Norwood Ranger": 1, "Elvish Mystic": 1, "Llanowar Elves": 1, "Fyndhorn Elves": 1,
            "Boreal Druid": 1, "Elves of Deep Shadow": 1, "Arbor Elf": 1, "Elvish Scout": 1, "Elvish Lyrist": 1,
            "Cylian Elf": 2, "Treetop Warden": 2, "Wirewood Elf": 2, "Wellwisher": 2, "Elvish Warrior": 2}
ELF_MANA = ("Elvish Mystic", "Llanowar Elves", "Fyndhorn Elves")   # may start untapped and able to tap for {G}
HOOF_COST = 8
PRIEST = "Priest of Titania"         # starts on the battlefield: {G} per elf on the battlefield


# The plain plans an elf board is held to (elf2, hoofdrain), each applied on its own; the trap is the best that binds.
ELF_PLANS = {
    "hoof_asap": "Craterhoof as soon as the mana for it can be made",
    "never_tap_sentinels": "never tap a Nettle Sentinel",
    "tap_out_first": "tap the mana creatures before anything else",
    "keep_druid": "never tap the Druid itself",
    "keep_mana_creatures": "never tap a mana elf or the Priest: they attack",
}
DRAIN_ELF_PLANS = dict(ELF_PLANS, sac_early="sacrifice each elf once the Druid has tapped it",
                       attack_only="attack without sacrificing",
                       sac_new_only="sacrifice only the elves cast this turn")


def _elf_plan_applies(plan, sentinels, mana_elves, priest, drain):
    if plan == "never_tap_sentinels":
        return sentinels > 0
    if plan in ("tap_out_first", "keep_mana_creatures"):
        return bool(mana_elves or priest)
    if plan in ("sac_early", "attack_only", "sac_new_only"):
        return bool(drain)
    return True


def elf_search(forests, sentinels, mana_elves, costs, priest=False, plan=None, spend=False, others=(), drain=0):
    """The most damage this turn and the fewest steps that reach it. Heritage Druid taps any three untapped elves
    (itself, the Sentinels, the starting mana elves, Priest of Titania, summoning-sick ones) for {G}{G}{G}; every green
    spell untaps every Nettle Sentinel; a starting mana elf taps for {G}; a starting Priest of Titania taps for {G} per
    elf on the battlefield; Craterhoof Behemoth (haste) gives each creature +X/+X, X the creatures, and the untapped
    creatures that started on the battlefield attack beside it. The Forests float first.
    `others` are the base powers of the starting creatures that are not elves (hoofdrain: the drainers and Viscera
    Seer); they attack. With `drain` drainers and the Seer out, every creature that will not attack (the elves cast,
    the starting ones tapped) is sacrificed after Craterhoof's trigger has resolved, X already counted: `drain` each.
    Returns (damage, steps, moves), moves ("druid", (druid, sentinels, mana elves, priest, sick)), ("tap",),
    ("priest",), ("cast", i) and ("hoof",) last; steps are the casts, Craterhoof, the sacrifices and the attack. `plan`
    holds the search to a plain plan (DRAIN_ELF_PLANS); `spend` breaks a tie toward the line that floats less mana
    past Craterhoof."""
    from functools import lru_cache
    by_cost = {}
    for i, c in enumerate(costs):
        by_cost.setdefault(c, []).append(i)
    kinds = sorted(by_cost)
    elves0 = 1 + sentinels + mana_elves + (1 if priest else 0)
    pr0 = 1 if priest else 0

    def reach(pool, druid, sent, mana, pr, sick, elves):
        """The most mana the mana abilities alone make from here."""
        return pool + pr * elves + max((mana - m) + 3 * ((druid + sent + sick + m) // 3) for m in range(mana + 1))

    @lru_cache(maxsize=None)
    def best(pool, druid, sent, mana, pr, sick, cast, gone):
        cands = []
        cast_n = sum(cast)
        elves = elves0 + cast_n - gone
        if pool >= HOOF_COST:
            x = elves0 + len(others) + cast_n - gone + 1
            dmg = (5 + x) + (1 + x if druid else 0) + sentinels * (2 + x) + (mana + pr) * (1 + x) \
                + sum(p + x for p in others)
            sacs = cast_n + (0 if plan == "sac_new_only" else (1 - druid) + (mana_elves - mana) + (pr0 - pr))
            sacs = sacs if drain and plan != "attack_only" else 0
            cands.append((dmg + drain * sacs, cast_n + 2 + sacs, (("hoof",),), pool - HOOF_COST if spend else 0))
        tap_out = plan == "tap_out_first" and (mana or pr)
        hurry = plan == "hoof_asap" and reach(pool, druid, sent, mana, pr, sick, elves) >= HOOF_COST
        need = HOOF_COST + sum(c * (len(by_cost[c]) - cast[j]) for j, c in enumerate(kinds))
        more = pool < need or tap_out  # past what the hand can spend, more mana taps an attacker for nothing
        if not (tap_out or hurry):
            for j, c in enumerate(kinds):
                if cast[j] < len(by_cost[c]) and pool >= c:
                    nc = cast[:j] + (cast[j] + 1,) + cast[j + 1:]
                    cands.append((best(pool - c, druid, sentinels, mana, pr, sick + 1, nc, gone),
                                  ("cast", by_cost[c][cast[j]])))
        keep = plan == "keep_mana_creatures"
        if mana and more and not keep:
            cands.append((best(pool + 1, druid, sent, mana - 1, pr, sick, cast, gone), ("tap",)))
        if pr and more and not keep:
            cands.append((best(pool + elves, druid, sent, mana, 0, sick, cast, gone), ("priest",)))
        if more and not tap_out:
            for a in range(0 if plan == "keep_druid" else druid + 1):
                for b in range(0 if plan == "never_tap_sentinels" else min(sent, 3 - a) + 1):
                    for c in range(0 if keep else min(mana, 3 - a - b) + 1):
                        for p in range(0 if keep else min(pr, 3 - a - b - c) + 1):
                            k = 3 - a - b - c - p
                            if k > sick:
                                continue
                            g = gone + k if plan == "sac_early" else gone
                            cands.append((best(pool + 3, druid - a, sent - b, mana - c, pr - p, sick - k, cast, g),
                                          ("druid", (a, b, c, p, k))))
        top = (-1, 0, (), 0)
        for c in cands:
            if len(c) == 2:
                (d, s, line, w), move = c
                c = (d, s, (move,) + line, w)
            if c[0] > top[0] or (c[0] == top[0] and (c[1], c[3]) < (top[1], top[3])):
                top = c
        return top

    d, s, line, w = best(forests, 1, sentinels, mana_elves, pr0, 0, (0,) * len(kinds), 0)
    return d, s, line


def elf_unused(forests, sentinels, mana_elves, costs, moves, priest=False):
    """The green mana still floating when Craterhoof Behemoth is cast by `moves`."""
    pool, n = forests, 0
    for move in moves:
        if move[0] == "cast":
            pool -= costs[move[1]]
            n += 1
        elif move[0] == "druid":
            pool += 3
        elif move[0] == "tap":
            pool += 1
        elif move[0] == "priest":
            pool += 1 + sentinels + mana_elves + 1 + n
        else:
            pool -= HOOF_COST
    return pool


def elf_plain(forests, sentinels, mana_elves, costs, priest=False, others=(), drain=0, stop=None):
    """What each plain plan that binds on the board deals: {plan: damage}, -1 when it never casts Craterhoof. With
    `stop`, the plans after the first that deals `stop` or more are left out (the board is dropped either way)."""
    out = {}
    for p in DRAIN_ELF_PLANS if drain else ELF_PLANS:
        if _elf_plan_applies(p, sentinels, mana_elves, priest, drain):
            out[p] = elf_search(forests, sentinels, mana_elves, costs, priest, plan=p, others=others, drain=drain)[0]
            if stop is not None and out[p] >= stop:
                break
    return out


def elf_lines(forests, sentinels, mana_names, names, moves, priest=False, others=()):
    """The written line for `moves`. Every Nettle Sentinel's "may untap" is asked when its trigger resolves (yes, also
    when it is untapped); a Druid activation with more than three untapped elves asks which three; a cast elf is
    resolved by one "pass" before a mana activation that would otherwise be taken with it still on the stack, but
    only in a window the seat is asked (one where nothing but a Forest's mana is playable is skipped).
    With Viscera Seer among `others` (hoofdrain) every window is asked: Craterhoof and its trigger resolve by a
    "pass" each, then the Seer sacrifices every creature that will not attack, each death's drain triggers put in
    order and resolved (Blood Artist's target the opponent) and the scry answered, before the attack."""
    seer = "Viscera Seer" in others
    lines = ["activate Forest"] * forests
    druid, sent, pr = True, sentinels, priest
    mana = list(mana_names)            # untapped starting mana elves
    sick = []                          # untapped summoning-sick elves, in the order cast
    for j, move in enumerate(moves):
        if move[0] == "druid":
            a, b, c, p, k = move[1]
            lines.append("activate Heritage Druid")
            if (1 if druid else 0) + sent + len(mana) + (1 if pr else 0) + len(sick) > 3:
                lines.append("; ".join(["Heritage Druid"] * a + ["Nettle Sentinel"] * b + mana[:c]
                                       + [PRIEST] * p + sick[:k]))
            druid, sent, mana, pr, sick = druid and not a, sent - b, mana[c:], pr and not p, sick[k:]
        elif move[0] == "tap":
            lines.append("activate " + mana.pop(0))
        elif move[0] == "priest":
            lines.append("activate " + PRIEST)
            pr = False
        elif move[0] == "cast":
            name = names[move[1]]
            lines += ["cast " + name] + ["yes"] * sentinels
            sent = sentinels
            nxt = moves[j + 1][0] if j + 1 < len(moves) else None
            window = (1 if druid else 0) + sent + len(mana) + (1 if pr else 0) + len(sick) >= 3 or bool(mana) or pr \
                or seer
            if nxt in ("druid", "tap", "priest") and window:
                lines.append("pass")
            sick.append(name)
        else:
            lines += ["cast Craterhoof Behemoth"] + ["yes"] * sentinels
            drainers = [o for o in others if o in DRAINERS]
            if seer and drainers:
                lines += ["pass", "pass"]          # Craterhoof, then its trigger: X is counted
                tapped = ([] if druid else ["Heritage Druid"]) + [m for m in mana_names if m not in mana] \
                    + ([PRIEST] if priest and not pr else [])
                for victim in [names[m[1]] for m in moves if m[0] == "cast"] + tapped:
                    lines += ["activate Viscera Seer", victim]
                    if len(drainers) > 1:
                        lines.append("; ".join(drainers))
                    if "Blood Artist" in drainers:
                        lines.append("opponent")
                    lines += ["pass"] * len(drainers) + ["pass", "Colossal Dreadmaw"]
            lines.append("; ".join(["Craterhoof Behemoth"] + ["Nettle Sentinel"] * sentinels
                                   + (["Heritage Druid"] if druid else []) + mana + ([PRIEST] if pr else [])
                                   + list(others)))
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
    return _elf_board(rng, forests, sentinels, mana_names, names, dmg, steps, moves)


def _elf_board(rng, forests, sentinels, mana_names, names, dmg, steps, moves, priest=False, trap=None, others=()):
    """The board dict for a searched elf line (`trap` set for elf2 and hoofdrain, `others` for hoofdrain)."""
    lines = elf_lines(forests, sentinels, mana_names, names, moves, priest, others)
    battlefield = ["Forest"] * forests + ["Heritage Druid"] + ["Nettle Sentinel"] * sentinels + mana_names \
        + ([PRIEST] if priest else []) + list(others)
    hand = names + ["Craterhoof Behemoth"]
    rng.shuffle(hand)
    search = {"forests": forests, "sentinels": sentinels, "mana_elves": mana_names, "elves": names, "priest": priest,
              "others": list(others)}
    solution = ["# steps: %d" % steps] + _record_lines(search, trap) + lines
    count = ["", "one", "two", "three", "four"]
    desc = ("It is your first main phase. You are at 20 life and control %s, all untapped and none of them summoning "
            "sick. Your hand is %s. The opponent is at %d life and controls nothing. Reduce the opponent to 0 life "
            "this turn." % (_join(([("%s Forest%s" % (count[forests], "s" if forests > 1 else ""))] if forests else [])
                                  + ["Heritage Druid", "%s Nettle Sentinel%s" % (count[sentinels],
                                                                                 "s" if sentinels > 1 else "")]
                                  + mana_names + ([PRIEST] if priest else []) + list(others)), _join(hand), dmg))
    family = "hoofdrain" if others else "elf2" if trap else "elf"
    rule = ("generated-%s | the exhaustive maximum is %d damage in %d steps: the Sentinels tapped by the Druid before "
            "each green spell untaps them, the cast elves the Druid's next fodder, Craterhoof last with %s untapped"
            % (family, dmg, steps, "the Druid" if lines[-1].count("Heritage Druid") else "the Sentinels"))
    if priest:
        rule += "; Priest of Titania taps for one per elf out, or attacks"
    if others:
        rule += ("; after Craterhoof's trigger has counted X, Viscera Seer sacrifices every creature that will not "
                 "attack, each death draining once per drainer")
    if trap:
        rule += _trap_note(trap, DRAIN_ELF_PLANS, "deals")
    state = [("turn", 1), ("activeplayer", "p0"), ("activephase", "MAIN1"), ("p0life", 20),
             ("p0hand", ";".join(hand)), ("p0battlefield", ";".join(battlefield)),
             ("p0library", "Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw;Colossal Dreadmaw"),
             ("p1life", dmg), ("p1library", "Plains;Plains;Plains")]
    return {"family": family, "trap": trap, "unused": 0, "actions": steps, "life": dmg, "hand": sorted(hand),
            "battlefield": sorted(battlefield), "phase": "MAIN1",
            "search": search,
            "text": lambda name: pzl_text(name, desc, rule, state, solution, ["life p1 <= 0"])}


def elf2_board(rng):
    """The elf engine made super hard: more Sentinels and mana elves, and a starting Priest of Titania (one {G} per elf
    out, so it taps late, or it attacks). Kept only at 13 to 15 steps with nothing floating past Craterhoof, when
    the mana binds, and when the best plain plan (ELF_PLANS: Craterhoof at once, never tap a Sentinel, tap the mana
    creatures first, the Druid on the new elves first) falls 1 to 3 short."""
    forests = rng.choice([0, 1, 1, 2, 2, 3])
    sentinels = rng.choice([1, 1, 2, 2, 3])
    mana_names = rng.sample(ELF_MANA, rng.choice([0, 1, 1, 2, 3]))
    priest = rng.random() < 0.4
    pool = sorted(set(ELF_HAND) - set(mana_names))
    names = rng.sample(pool, min(len(pool), rng.randint(HARD_MIN - 2, HARD_MAX - 1)))
    costs = [ELF_HAND[nm] for nm in names]
    args = (forests, sentinels, len(mana_names), costs)
    dmg, steps, moves = elf_search(*args, priest=priest, spend=True)
    if dmg < 0 or not HARD_MIN <= steps <= HARD_MAX or elf_unused(*args, moves, priest):
        return None
    x = 1 + sentinels + len(mana_names) + priest + len(names) + 1
    if dmg >= (5 + x) + (1 + x) + sentinels * (2 + x) + (len(mana_names) + priest) * (1 + x):
        return None                    # the mana must bind (elf_board)
    trap = _trap(dmg, elf_plain(*args, priest=priest, stop=dmg))
    if trap is None:
        return None
    b = _elf_board(rng, forests, sentinels, mana_names, names, dmg, steps, moves, priest, trap)
    b["shape"] = ("elf2", forests, sentinels, len(mana_names), priest, tuple(sorted(costs)))
    return b


# hoofdrain: the starting creatures that are not elves, by base power.
HOOFDRAIN_OTHERS = {"Blood Artist": 0, "Zulaport Cutthroat": 1, "Cruel Celebrant": 1, "Viscera Seer": 1}


def hoofdrain_board(rng):
    """Elves and aristocrats: the elf engine into Craterhoof Behemoth, with one or two drainers and Viscera Seer out.
    Craterhoof's trigger counts X when it resolves, so the creatures that will not attack (the elves cast, the
    starting ones the Druid tapped) are sacrificed after it, never before; tapping the Druid itself trades its attack
    for a drain. Kept only at 13 to 15 steps with nothing floating past Craterhoof, when the mana binds, and when the
    best plain plan (DRAIN_ELF_PLANS) falls 1 to 3 short."""
    forests = rng.choice([0, 1, 1, 2, 2, 3])
    sentinels = rng.choice([1, 1, 2, 2, 3])
    mana_names = rng.sample(ELF_MANA, rng.choice([0, 0, 1, 1, 2]))
    priest = rng.random() < 0.35
    drainers = rng.sample(DRAINERS, rng.choice([1, 1, 2]))
    others = sorted(drainers) + ["Viscera Seer"]
    pool = sorted(set(ELF_HAND) - set(mana_names))
    names = rng.sample(pool, rng.randint(4, 7))
    costs = [ELF_HAND[nm] for nm in names]
    args = (forests, sentinels, len(mana_names), costs)
    kw = dict(priest=priest, others=tuple(HOOFDRAIN_OTHERS[o] for o in others), drain=len(drainers))
    dmg, steps, moves = elf_search(*args, spend=True, **kw)
    if dmg < 0 or not HARD_MIN <= steps <= HARD_MAX or elf_unused(*args, moves, priest):
        return None
    x = 1 + sentinels + len(mana_names) + priest + len(others) + len(names) + 1
    if dmg >= (5 + x) + (1 + x) + sentinels * (2 + x) + (len(mana_names) + priest) * (1 + x) \
            + sum(HOOFDRAIN_OTHERS[o] + x for o in others) + len(drainers) * len(names):
        return None                    # the mana must bind (elf_board)
    trap = _trap(dmg, elf_plain(*args, stop=dmg, **kw))
    if trap is None:
        return None
    b = _elf_board(rng, forests, sentinels, mana_names, names, dmg, steps, moves, priest, trap, others)
    b["shape"] = ("hoofdrain", forests, sentinels, len(mana_names), priest, len(drainers), tuple(sorted(costs)))
    return b


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


FAMILIES = {"ping": ping_board, "storm": storm_board, "splice": splice_board, "splice2": splice2_board,
            "red": red_board, "drain": drain_board, "elf": elf_board, "fling": fling_board, "drain2": drain2_board,
            "elf2": elf2_board, "hoofdrain": hoofdrain_board}


def board_key(b):
    """Two draws with the same cards, life and phase are one board."""
    return hashlib.sha256(json.dumps([b["family"], b["hand"], b["battlefield"], b["life"], b["phase"]])
                          .encode()).hexdigest()[:12]


def iter_boards(family, count, seed, tries=20000):
    """`count` boards of a family, each yielded as it is drawn. A board that states its structure (drain2, elf2,
    hoofdrain: `shape`, the cards reduced to their roles) is also one board with any other of that structure or of
    the same life total, so a batch never repeats a life total."""
    rng = random.Random(seed)
    seen, n = set(), 0
    for _ in range(tries):
        if n >= count:
            break
        b = FAMILIES[family](rng)
        if b is None:
            continue
        key = board_key(b)
        alike = [key] + ([("shape", b["shape"]), ("life", b["life"])] if "shape" in b else [])
        if any(k in seen for k in alike):
            continue
        seen.update(alike)
        b["key"] = key
        n += 1
        yield b


def generate(family, count, seed, tries=20000):
    return list(iter_boards(family, count, seed, tries))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", choices=sorted(FAMILIES), required=True)
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tries", type=int, default=20000, help="draws before giving up on --count")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for b in iter_boards(a.family, a.count, a.seed, a.tries):
        name = "%s_%s" % (a.family, b["key"])
        (out / (name + ".pzl")).write_text(b["text"](name), encoding="utf-8")
        n += 1
    print("%d %s boards in %s" % (n, a.family, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
