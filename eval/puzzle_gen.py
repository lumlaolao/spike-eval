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

Families: ping (Thermo-Alchemist untaps between spells), storm (Grapeshot copies), drain (sacrifice and recursion
loops with Blood Artist and friends) and elf (Heritage Druid and Nettle Sentinel into Craterhoof Behemoth). The drain
and elf boards set the opponent's life to the exact maximum of an exhaustive search over a model of the board.

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
    # The search tries the lowest unused index of each cost; name the elves in the order they are cast.
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


FAMILIES = {"ping": ping_board, "storm": storm_board, "drain": drain_board, "elf": elf_board}


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
