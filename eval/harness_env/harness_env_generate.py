#!/usr/bin/env python3
"""The hidden grader's instance generator: surfaces drawn from the engine's card scripts with a secret seed
(docs/harness/HARNESS-ENV-FRAMEWORK.md, sections 4.2 and 4.6).

Each template is a board shape the grader knows how to build for any card that fits it (forge.harnessenv.grader
.GeneratedInstances): a creature with a combat keyword the seat must block, a creature that blocks with one, a
creature to attack with between two defenders, a single-target spell, a "sacrifice a creature" activated cost, and
a charm. The index reads every script under cardsfolder and keeps a card for a template only when its board can use
it (one face, not an Alchemy rebalance, one target for the spell template, the sacrifice as the whole cost).

A set is drawn per template with a seed: the same seed gives the same set byte for byte, and only the seed's hash is
written. Cards split by a hash of their name: a tenth are the dev pool, the only cards a public dev slice may use,
and the graded split never draws from it, so a dev slice reveals no graded card. The set carries the generator and
template versions, the engine pin and a hash over its instances; scores are compared only within one set hash.

What it cannot do: say that an instance is a good test. A card whose board offers one legal answer is kept (the
asked check then requires nothing); the instance file says what was drawn, the report says what it measured.

Usage: harness_env_generate.py <cardsfolder> <out.json> [--per-template N] [--split heldout|dev|all]
       the seed comes from $HARNESS_ENV_SEED (required for heldout; dev uses the public seed "dev")
"""
import hashlib
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import engine_patches

GENERATOR = "gen-v1"
FORMAT = "harness-env-instances/1"
CARDSFOLDER = os.path.join(str(engine_patches.forge_home() or os.path.expanduser("~/code/forge")),
                           "forge-gui/res/cardsfolder")
# template -> (family, hook); the Java side builds each (GeneratedInstances.build).
TEMPLATES = {
    "attacker-keyword": ("combat", "declareBlockers"),
    "blocker-keyword": ("combat", "declareBlockers"),
    "attacker": ("combat", "declareAttackers"),
    "single-target-spell": ("targets", "chooseTargetsFor"),
    "sac-outlet": ("costs", "getCostDecisionMaker"),
    "charm": ("modes", "chooseModeForAbility"),
}
ATTACK_KEYWORDS = {"Flying", "Menace", "Trample", "First Strike", "Double Strike", "Deathtouch", "Fear", "Intimidate",
                   "Shadow", "Horsemanship", "Skulk", "Lifelink"}
BLOCK_KEYWORDS = {"Reach", "Flying", "First Strike", "Double Strike", "Deathtouch", "Defender"}
SKIP_TYPES = ("Conspiracy", "Scheme", "Plane", "Phenomenon", "Vanguard", "Dungeon", "Attraction", "Contraption")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_dev(card):
    """A tenth of the card pool, fixed by name: the only cards a public dev slice may draw."""
    return int(sha("dev-pool|" + card), 16) % 10 == 0


def instance_id(template, card):
    return sha(template + "|" + card)[:16]


def set_sha(instances):
    return sha(json.dumps(instances, sort_keys=True))[:16]


def parse(text):
    """{'name', 'types', 'pt', 'cost', 'keywords', 'abilities'} for a one-face script, or None."""
    if "\nALTERNATE" in text or text.count("Name:") != 1:
        return None
    card = {"keywords": set(), "abilities": []}
    for line in text.splitlines():
        key, _, val = line.partition(":")
        if key == "Name":
            card["name"] = val.strip()
        elif key == "Types":
            card["types"] = val.split()
        elif key == "PT":
            card["pt"] = val.strip()
        elif key == "ManaCost":
            card["cost"] = val.strip()
        elif key == "K":
            card["keywords"].add(val.split(":")[0].strip())
        elif key == "A":
            card["abilities"].append(val.strip())
    if not card.get("name") or card["name"].startswith("A-") or not card.get("types"):
        return None
    if any(t in card["types"] for t in SKIP_TYPES):
        return None
    return card


def templates_for(card):
    out = []
    types, kws, abil = card["types"], card["keywords"], card["abilities"]
    creature = "Creature" in types and "pt" in card
    if creature and kws & ATTACK_KEYWORDS:
        out.append("attacker-keyword")
    if creature and kws & BLOCK_KEYWORDS:
        out.append("blocker-keyword")
    if creature and "Defender" not in kws:
        out.append("attacker")
    spell = "Instant" in types or "Sorcery" in types
    x_cost = "X" in card.get("cost", "").split()
    if spell and not x_cost and len(abil) == 1 and abil[0].startswith("SP$") and "ValidTgts$" in abil[0] \
            and not re.search(r"TargetMin\$|TargetMax\$|Charm|Announce\$|Divided", abil[0]):
        out.append("single-target-spell")
    if any(a.startswith("AB$") and re.search(r"\|\s*Cost\$\s*Sac<1/Creature>\s*\|", a) for a in abil):
        out.append("sac-outlet")
    if spell and not x_cost and any(a.startswith("SP$ Charm") for a in abil):
        out.append("charm")
    return out


def index(cardsfolder):
    """{template: sorted card names} over every script under cardsfolder."""
    out = {t: set() for t in TEMPLATES}
    for root, _, files in os.walk(cardsfolder):
        for f in files:
            if not f.endswith(".txt"):
                continue
            with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                card = parse(fh.read())
            if card:
                for t in templates_for(card):
                    out[t].add(card["name"])
    return {t: sorted(v) for t, v in out.items()}


def generate(idx, seed, per_template=5, split="heldout"):
    """A versioned instance set: per_template cards per template from the split, drawn with seed."""
    instances = []
    for t in sorted(TEMPLATES):
        pool = [c for c in idx.get(t, []) if split == "all" or (split == "dev") == is_dev(c)]
        rng = random.Random(sha(seed + "|" + t))
        for card in sorted(rng.sample(pool, min(per_template, len(pool)))):
            family, hook = TEMPLATES[t]
            instances.append({"id": instance_id(t, card), "template": t, "card": card, "family": family, "hook": hook})
    instances.sort(key=lambda i: (i["template"], i["card"]))
    return {"format": FORMAT, "generator": GENERATOR, "templates": sorted(TEMPLATES), "split": split,
            "seed_sha": sha(seed)[:16], "per_template": per_template, "instances": instances, "set_sha": set_sha(instances)}


def main(argv):
    args = argv[1:]
    if len(args) < 2:
        print(__doc__)
        return 2
    folder, out, rest = args[0], args[1], args[2:]
    per, split = 5, "heldout"
    while rest:
        flag = rest.pop(0)
        if flag == "--per-template" and rest:
            per = int(rest.pop(0))
        elif flag == "--split" and rest:
            split = rest.pop(0)
        else:
            print(__doc__)
            return 2
    seed = "dev" if split == "dev" else os.environ.get("HARNESS_ENV_SEED")
    if not seed:
        print("HARNESS_ENV_SEED is required for a %s set (the seed is the secret; only its hash is written)" % split)
        return 2
    s = generate(index(folder), seed, per, split)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1, sort_keys=True)
    print("%s: %d instances, set %s, seed %s, split %s" % (out, len(s["instances"]), s["set_sha"], s["seed_sha"], split))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
