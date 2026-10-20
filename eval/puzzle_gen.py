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


FAMILIES = {"ping": ping_board, "storm": storm_board}


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
