#!/usr/bin/env python3
"""Emit hand-curated raw items (target schema) into eval/puzzles/modern/raw/items/.

Questions and answers here are paraphrased from the cited sources (facts +
rulings), not copied verbatim, per the corpus licensing note in README.md.
Run: python3 make_curated.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, os.pardir, "raw", "items"))


def CI(art, date, title, q, board, ans, areas, diff, mv, cards, notes, fmt="Commander/casual"):
    return {
        "source_name": "Cranial Insertion",
        "source_url": f"https://www.cranial-insertion.com/article/{art}",
        "title": title, "date": date, "era": date[:4] + "+", "format": fmt,
        "question": q, "board_state": board, "answer": ans,
        "answer_authority": "judge-article", "rules_areas": areas,
        "difficulty": diff, "machine_verifiable": mv, "cards": cards,
        "notes": f"Cranial Insertion #{art}, judge-authored rules column. {notes} "
                 "Question/answer paraphrased from the article (fetched 2026-10-12). "
                 "Cranial Insertion is not affiliated with Wizards of the Coast.",
        "verified": True,
    }


def TAJ(url, date, title, q, ans, areas, diff, mv, cards, notes):
    return {
        "source_name": "Ask a Magic Judge",
        "source_url": url,
        "title": title, "date": date, "era": date[:4] + "+", "format": "Commander/casual",
        "question": q, "board_state": "described in the question (no separate board state)",
        "answer": ans, "answer_authority": "judge-article", "rules_areas": areas,
        "difficulty": diff, "machine_verifiable": mv, "cards": cards,
        "notes": f"Ask a Magic Judge (magicjudge.tumblr.com), judge-run rules blog. {notes} "
                 "Question/answer paraphrased from the post (fetched via the Tumblr read API 2026-10-12).",
        "verified": True,
    }


def OR(url, date, title, q, board, ans, areas, diff, cards, notes):
    return {
        "source_name": "Wizards official card rulings",
        "source_url": url,
        "title": title, "date": date, "era": date[:4] + "+", "format": "Modern",
        "question": q, "board_state": board, "answer": ans,
        "answer_authority": "official-ruling", "rules_areas": areas,
        "difficulty": diff, "machine_verifiable": True, "cards": cards,
        "notes": f"Official Wizards card ruling (Gatherer), published {date}, surfaced via Scryfall's "
                 f"rulings API (fetched 2026-10-12). {notes} The scenario is written for this corpus; "
                 "the ruling text is the ground truth.",
        "verified": True,
    }


CI_ITEMS = [
    CI(4362, "2025-02-24",
       "Leonardo da Vinci copies The Aetherspark: still a planeswalker, but it can't attach",
       "You activate Leonardo da Vinci to make a 0/2 Thopter copy of your The Aetherspark. Is the copy still a planeswalker, and can its +1 ability attach it to a creature?",
       "You control Leonardo da Vinci and The Aetherspark (legendary artifact planeswalker - Equipment). The copy is a 0/2 Thopter in addition to its other types; you have another creature to target.",
       "The copy is still a planeswalker: its type line is Legendary Artifact Planeswalker Creature - Equipment Thopter, 0/2, with flying and all of The Aetherspark's abilities. Its +1 ability can be activated and gives a target creature a +1/+1 counter, but the copy never becomes attached - a permanent that is a creature can't be attached to another permanent.",
       ["layers", "copying", "type-changing", "activated-abilities"], "hard", True,
       ["Leonardo da Vinci", "The Aetherspark"],
       "Layer 1 copy effect plus layer 4 type change yields an Equipment that is also a creature."),

    CI(4362, "2025-02-24",
       "Fraying Omnipotence, Tinybones, and the order of instructions on a card",
       "You control Tinybones, Bauble Burglar as your only creature and cast Fraying Omnipotence. Do you get to exile the cards your opponent discards, or is Tinybones sacrificed before that?",
       "You control Tinybones, Bauble Burglar. Fraying Omnipotence: each player loses half their life, then discards half their hand, then sacrifices half the creatures they control (round up each time).",
       "You exile them. Fraying Omnipotence's instructions are followed in the order printed - life loss, then discard, then sacrifices - with no state-based actions checked in between. Tinybones is still on the battlefield when the opponent discards, so its ability triggers and exiles those cards; the sacrifice happens last.",
       ["resolving", "state-based-actions", "triggered-abilities"], "hard", True,
       ["Fraying Omnipotence", "Tinybones, Bauble Burglar"],
       "Resolution order per CR 608.2c; SBAs are not checked mid-resolution."),

    CI(4365, "2025-03-03",
       "The One Ring's upkeep trigger and Goblin Welder: last known information",
       "You are at 1 life and control The One Ring with three burden counters. In your upkeep, can you use Goblin Welder's ability to sacrifice The One Ring before its upkeep trigger resolves and thereby avoid the life loss?",
       "You control The One Ring (three burden counters) and Goblin Welder; you are at 1 life.",
       "No. You can respond to the trigger and weld The One Ring away, but when the ability resolves with The One Ring off the battlefield the game uses last known information - it had three burden counters, so you still lose 3 life and lose the game.",
       ["triggered-abilities", "priority", "last-known-information", "stack"], "hard", True,
       ["The One Ring", "Goblin Welder"],
       "Triggered ability uses LKI for the counter count."),

    CI(4368, "2025-03-10",
       "Giant Warthog's blocker is blinked out before combat damage",
       "You attack with Giant Warthog and the defender blocks with Bear Cub, then blinks the Bear Cub out of the way before the combat damage step. How much damage does the defending player take?",
       "Attacker: Giant Warthog (5/5, trample). Defender declares Bear Cub (2/2) as a blocker; before damage, Lilysplash Mentor's ability exiles and returns Bear Cub.",
       "5. A blocked attacker whose blockers have all left combat before the damage step normally deals no damage to the player, but because the Warthog has trample it assigns all of its 5 damage to the player it is attacking.",
       ["combat", "damage-assignment", "keywords"], "hard", True,
       ["Giant Warthog", "Bear Cub", "Lilysplash Mentor"],
       "CR 509.1h / 702.19 trample rules: leftover damage goes to the attacked player."),

    CI(4368, "2025-03-10",
       "Fireshrieker, trample, and a planeswalker that is being attacked",
       "Your attacking Giant Warthog (5/5) is equipped with Fireshrieker and is attacking Nathan's Personal Decoy (a planeswalker). Nathan blocks with Claim Jumper (3/3). What is the most damage the Warthog can deal to Personal Decoy this turn?",
       "Attacker: Giant Warthog 5/5 with double strike (Fireshrieker) and trample, attacking the planeswalker Personal Decoy. Blocker: Claim Jumper, 3/3.",
       "7. In the first combat damage step the Warthog must assign lethal (3) to Claim Jumper, letting 2 trample over to Personal Decoy; Claim Jumper dies. In the second damage step the attacker is still blocked but has no blockers left, so with trample it assigns all 5 damage to Personal Decoy. 2 + 5 = 7.",
       ["combat", "damage-assignment", "keywords"], "expert", True,
       ["Giant Warthog", "Fireshrieker", "Claim Jumper", "Personal Decoy"],
       "Double strike gives two damage steps; trample routes leftover damage to the attacked planeswalker."),

    CI(4368, "2025-03-10",
       "Furnace of Rath, trample, and a triple block: which life totals are possible?",
       "Alex controls Furnace of Rath and attacks Nancy with an 8/8 trampling Giant Warthog (5/5 plus Vulshok Battlegear). Nancy triple-blocks with a 1/1 Rabbit token, a 2/2 Bear Cub and a 2/2 Cave Tiger. Which amounts of life can Nancy lose to the Warthog?",
       "Attacker: Giant Warthog (5/5 base) with Vulshok Battlegear (+3/+3) and trample = 8/8. Furnace of Rath doubles all damage dealt by a source. Blockers: 1/1 Rabbit token, 2/2 Bear Cub, 2/2 Cave Tiger.",
       "6, 4, 2 or 0 - never 11. Damage is assigned before Furnace of Rath doubles it, and Alex has only 8 damage to assign, with at least lethal (pre-doubling) required on each blocker before anything tramples. The usual line (1 to the Rabbit, 2 to the Cub, 2 to the Tiger, 3 to Nancy) doubles to 6 life lost; over-assigning to blockers legally gives 4, 2 or 0.",
       ["damage-assignment", "replacement", "combat", "keywords"], "expert", True,
       ["Giant Warthog", "Vulshok Battlegear", "Furnace of Rath", "Cave Tiger"],
       "Classic trap: doubling is a replacement effect applied after assignment."),

    CI(4371, "2025-03-17",
       "Plaxcaster Frogling gives itself shroud, so Murder fizzles",
       "Your opponent casts Murder targeting your Plaxcaster Frogling, which still has a +1/+1 counter. You activate the Frogling's ability to give it shroud. Does Murder fizzle?",
       "You control Plaxcaster Frogling with a +1/+1 counter; your opponent casts Murder targeting it.",
       "Yes. The Frogling can target itself, its ability resolves first giving it shroud, and Murder then has only illegal targets, so it doesn't resolve and simply goes to the graveyard.",
       ["targets", "activated-abilities", "stack", "priority"], "medium", True,
       ["Plaxcaster Frogling", "Murder"],
       "Single-target spell countered by game rules when its only target becomes illegal."),

    CI(4371, "2025-03-17",
       "Winter, Cynical Opportunist can't return a battle's back face",
       "Winter, Cynical Opportunist's delirium ability puts a card from your graveyard onto the battlefield. Can it put Invasion of Ikoria onto the battlefield as Zilortha, Apex of Ikoria?",
       "You control Winter, Cynical Opportunist and have Invasion of Ikoria (a battle with a creature back face) in your graveyard.",
       "No. When an effect puts a double-faced card onto the battlefield without specifying which face, it is put onto the battlefield with its front face up.",
       ["zone-changes", "continuous-effects"], "medium", True,
       ["Winter, Cynical Opportunist", "Invasion of Ikoria"],
       "Default face-up rule for double-faced cards put onto the battlefield."),

    CI(4371, "2025-03-17",
       "What counts as a creature card leaving the graveyard for Insidious Roots?",
       "Which of these triggers Insidious Roots: Ojer Kaslem returning from your graveyard transformed, Mosswood Dreadknight cast from your graveyard as its Adventure, or Enduring Vitality returning as an enchantment?",
       "You control Insidious Roots. Three separate scenarios move a creature card out of your graveyard: a transforming permanent, an Adventure spell, and an enduring enchantment return.",
       "All three. Insidious Roots only asks that one or more creature cards left your graveyard; it does not care which zone the card goes to or what it is after the move.",
       ["triggered-abilities", "zone-changes", "continuous-effects"], "hard", True,
       ["Insidious Roots", "Ojer Kaslem, Deepest Growth", "Mosswood Dreadknight"],
       "Trigger condition depends on the card's type in the graveyard only."),

    CI(4380, "2025-04-07",
       "New Way Forward: lethal damage and decking at the same time is a draw",
       "New Way Forward prevents damage and its delayed trigger draws you that many cards and deals that much damage to your opponent. If the damage would be lethal but you would draw from an empty library, what happens?",
       "You are about to draw more cards than your library holds; the delayed trigger also deals lethal damage to your opponent.",
       "The game is a draw. After the delayed triggered ability finishes resolving, state-based actions are checked: your opponent is at 0 or less life and you have attempted to draw from an empty library, so both players lose the game simultaneously.",
       ["state-based-actions", "triggered-abilities", "replacement"], "hard", True,
       ["New Way Forward"],
       "Two simultaneous loss conditions from SBAs -> draw (CR 104.4a)."),

    CI(4383, "2025-04-14",
       "Does an artifact creature spell trigger Eshki Dragonclaw?",
       "You control Eshki Dragonclaw and cast Boulderborn Dragon (an artifact creature) before combat. Do you get Eshki's beginning-of-combat trigger?",
       "Eshki Dragonclaw: at the beginning of combat on your turn, if you've cast both a creature spell and a noncreature spell this turn, draw a card and put two +1/+1 counters on Eshki.",
       "No. A creature spell is never also a noncreature spell, even if the spell has another card type such as artifact. Eshki needs one creature spell plus one noncreature spell.",
       ["triggered-abilities", "casting", "type-changing"], "medium", True,
       ["Eshki Dragonclaw", "Boulderborn Dragon"],
       "'Creature spell' and 'noncreature spell' are mutually exclusive."),

    CI(4386, "2025-04-21",
       "Anafenza, Unyielding Lineage endures twice when she dies beside two creatures",
       "Dragonback Assault simultaneously kills your opponent's Anafenza, Unyielding Lineage, Bearer of Glory and Abzan Devotee. Does Anafenza endure zero, once or twice?",
       "Three nontoken creatures controlled by the opponent (Anafenza plus two others) are destroyed simultaneously.",
       "Twice. Anafenza sees the other two nontoken creatures die at the same time as herself, so two endure triggers are created. Because she is gone when they resolve, the +1/+1 counter mode is impossible and each trigger creates a 2/2 Spirit token.",
       ["triggered-abilities", "state-based-actions", "zone-changes"], "hard", True,
       ["Anafenza, Unyielding Lineage", "Dragonback Assault"],
       "Simultaneous-death triggers from a creature that also dies."),

    CI(4386, "2025-04-21",
       "A trigger with one remaining legal target still resolves",
       "Gurmag Rakshasa's enters ability targets an opponent's creature and your 2/2 Zombie Druid token. The opponent responds with Snakeskin Veil, giving their creature hexproof. Does your Zombie Druid still get +2/+2?",
       "Gurmag Rakshasa enters; its triggered ability has two targets. The opponent gives their creature hexproof in response.",
       "Yes. The trigger still has one legal target when it tries to resolve, so it resolves and does as much as it can - the Zombie Druid still gets +2/+2. If every target had become illegal, the trigger would have been removed from the stack and done nothing.",
       ["triggered-abilities", "targets", "stack"], "medium", True,
       ["Gurmag Rakshasa", "Snakeskin Veil"],
       "CR 608.2b: do as much as possible with remaining legal targets."),

    CI(4389, "2025-04-28",
       "Beholding a Dragon on the stack: Shiko and Dispelling Exhale",
       "You cast Shiko, Paragon of the Way; your opponent counters it; you respond with Dispelling Exhale, whose additional cost can behold a Dragon. Can you behold Shiko, which is itself a Dragon on the stack?",
       "Shiko, Paragon of the Way is a Spirit Dragon spell on the stack being countered; you cast Dispelling Exhale.",
       "No. To behold a Dragon you must either reveal a Dragon card from your hand or choose a Dragon permanent on the battlefield. Shiko on the stack is neither a card in hand nor a permanent, so you cannot behold it and don't get the augmented effect.",
       ["casting", "alternative-costs", "stack"], "medium", True,
       ["Shiko, Paragon of the Way", "Dispelling Exhale"],
       "Behold looks only at hand and battlefield."),

    CI(4400, "2025-05-26",
       "Mobilize tokens don't count for battalion",
       "You attack with exactly Stadium Headliner and Legion Loyalist. Can you stack your triggers so Stadium Headliner's mobilize token arrives first and enables Legion Loyalist's battalion?",
       "You control Stadium Headliner (mobilize 1) and Legion Loyalist (battalion - whenever this creature and at least two other creatures attack, ...). You declare exactly those two as attackers.",
       "No. Battalion counts the creatures declared as attackers. A token that is put onto the battlefield attacking was never declared as an attacker, so with only two creatures declared, battalion never triggers.",
       ["triggered-abilities", "combat", "tokens"], "medium", True,
       ["Stadium Headliner", "Legion Loyalist"],
       "Attack triggers count declared attackers, not creatures put onto the battlefield attacking."),

    CI(4400, "2025-05-26",
       "State-based actions fire before Obstinate Baloth can save your opponent",
       "Your opponent is at 2 life. Hopeless Nightmare's enters ability makes them discard Obstinate Baloth and then lose 2 life. Do they gain 4 life from the Baloth in time?",
       "Opponent at 2 life. Resolving Hopeless Nightmare's triggered ability: they discard a card, then lose 2 life. The discarded card is Obstinate Baloth.",
       "No, they lose the game. After the Nightmare's ability finishes resolving, state-based actions are checked before the Baloth's triggered ability can be put on the stack. They are at 0 life, so they lose before Baloth's trigger ever resolves.",
       ["state-based-actions", "triggered-abilities", "stack", "priority"], "hard", True,
       ["Hopeless Nightmare", "Obstinate Baloth"],
       "SBAs are checked before triggers are put on the stack (CR 704.3 and 603.3b ordering)."),

    CI(4407, "2025-06-09",
       "Blood Moon, Urza's Saga, and the 2025 Saga rules change",
       "Explain what happens when Blood Moon turns Urza's Saga into a Mountain after chapter II has resolved. Is the Saga sacrificed for losing its chapter abilities, and does it keep the abilities granted by chapters I and II?",
       "You control Urza's Saga with two lore counters, and a Blood Moon makes nonbasic lands Mountains and removes their abilities.",
       "Under the current rules a Saga that loses its chapter abilities is not sacrificed; Urza's Saga simply stays on the battlefield with two lore counters. Blood Moon's type-changing effect applies in layer 4 and removes the abilities printed on the card, but the abilities granted by the resolutions of chapters I and II are separate continuous effects created by those abilities and are applied in layer 6 - so the Saga still has its mana ability and its Construct-making ability.",
       ["layers", "continuous-effects", "type-changing", "state-based-actions"], "expert", True,
       ["Urza's Saga", "Blood Moon"],
       "Reflects the June 2025 rules update to Sagas plus the layer 4 vs layer 6 split."),

    CI(4410, "2025-06-16",
       "Ice Magic's mana value on the stack is unchanged by its mode",
       "You cast Ice Magic choosing the Blizzaga mode and paying the extra cost. What is Ice Magic's mana value while it is on the stack?",
       "Ice Magic has a mode that adds an extra cost to cast it.",
       "Still 2. The mode adds to the total cost you pay, not to the spell's mana cost, so its mana value is unchanged.",
       ["costs", "casting", "mana"], "medium", True,
       ["Ice Magic"],
       "Additional costs do not change mana value (CR 202.3)."),

    CI(4410, "2025-06-16",
       "Aerith Gainsborough and two simultaneous state-based actions",
       "Aerith Gainsborough has two +1/+1 counters. Your opponent's spell puts five -1/-1 counters on her. Do you still get her dies trigger, which puts X +1/+1 counters on each legendary creature you control?",
       "Aerith Gainsborough (2/2) with two +1/+1 counters; five -1/-1 counters are put on her.",
       "Yes. In the same state-based-action check, one SBA removes two +1/+1 and two -1/-1 counters, while another sends Aerith to the graveyard for having 0 or less toughness. Both apply at once, so there is never a moment when she is on the battlefield without her +1/+1 counters; her dies trigger sees X = 2.",
       ["counters", "state-based-actions", "triggered-abilities", "last-known-information"], "hard", True,
       ["Aerith Gainsborough"],
       "Counter-removal SBA and lethal-toughness SBA occur simultaneously."),

    CI(4416, "2025-06-30",
       "Sudden Spoiling and Marvin, Murderous Mimic: layer 6 ordering",
       "Your opponent casts Sudden Spoiling, so your creatures lose all abilities. Afterwards you cast Marvin, Murderous Mimic. Does Marvin gain the activated abilities of Drowner of Secrets, which now has none?",
       "Sudden Spoiling has resolved this turn; you then cast Marvin, Murderous Mimic while controlling Drowner of Secrets.",
       "No. Sudden Spoiling's ability-removing effect and Marvin's ability-granting effect both apply in layer 6, and Sudden Spoiling's effect has already stripped Drowner of Secrets of its abilities, so there is nothing for Marvin to take.",
       ["layers", "continuous-effects", "copying", "activated-abilities"], "expert", True,
       ["Sudden Spoiling", "Marvin, Murderous Mimic", "Drowner of Secrets"],
       "Layer 6 dependency/timestamp ordering."),

    CI(4416, "2025-06-30",
       "Flickering White Auracite lets Charismatic Conqueror see it enter",
       "You control White Auracite, which exiled your opponent's Charismatic Conqueror. You Flicker the Auracite. Does Charismatic Conqueror's ability trigger when the returning Auracite enters?",
       "White Auracite's enters ability exiled Charismatic Conqueror until the Auracite leaves the battlefield; then the Auracite is exiled and returned by Flicker.",
       "Yes. When Flicker exiles White Auracite, the exiled Charismatic Conqueror returns to the battlefield right away (the Auracite's linked exile ends), so the Conqueror is already on the battlefield at the moment Flicker returns White Auracite - and it triggers.",
       ["triggered-abilities", "zone-changes"], "medium", True,
       ["White Auracite", "Charismatic Conqueror"],
       "Two-part exile-until-leaves ability resolves before the same spell's return instruction."),

    CI(4362, "2025-02-24",
       "The cleanup step repeats until it is clean (Ketramose and Currency Converter)",
       "You control Ketramose, the New Dawn and Currency Converter, have eight cards in hand, and discard down to seven in your cleanup step. If you exile the discarded card to Ketramose in order to draw a card, do you get to keep eight cards?",
       "You control Ketramose, the New Dawn and Currency Converter with eight cards in hand at the beginning of your cleanup step.",
       "No. If state-based actions or triggered abilities occur during a cleanup step, the game starts another cleanup step after they are dealt with, repeating until a clean cleanup step completes. You may draw back up to eight, but you will immediately have to discard down to seven again. The loop is driven by your own choices and will eventually run out of cards or life, so you must stop it.",
       ["turn-structure", "state-based-actions", "triggered-abilities", "priority"], "hard", True,
       ["Ketramose, the New Dawn", "Currency Converter"],
       "Repeated cleanup steps (CR 514.3a)."),

    CI(4365, "2025-03-03",
       "Ninjutsu-ing in Kaito, Bane of Nightmares triggers Tinybones Joins Up",
       "You control Tinybones Joins Up, whose ability triggers whenever a legendary creature you control enters. On your turn you ninjutsu in Kaito, Bane of Nightmares. Does it trigger?",
       "Tinybones Joins Up is on the battlefield; Kaito, Bane of Nightmares enters via ninjutsu on your turn.",
       "Yes. On your turn Kaito is a legendary creature on the battlefield thanks to its own ability, so the enters-the-battlefield condition is satisfied even though the card is not a creature in other zones.",
       ["triggered-abilities", "zone-changes", "type-changing"], "medium", True,
       ["Tinybones Joins Up", "Kaito, Bane of Nightmares"],
       "Conditional type-changing (Kaito is a creature only on your turn) is evaluated on the battlefield."),
]

TAJ_ITEMS = [
    TAJ("https://magicjudge.tumblr.com/post/728636430596947968/if-i-have-elenda-on-the-battlefield-and-get-hit",
        "2023-09-16",
        "Elenda, the Dusk Rose dies and returns: new object, no counters, but the buff counts",
        "Your Elenda is on the battlefield when a board wipe resolves, and you cast Supernatural Stamina in response so that she returns. Do the creatures dying put counters on the returning Elenda, and how big are the tokens she makes?",
        "Elenda is a new object after changing zones, so the +1/+1 counter triggers created by the creatures that died were created by an Elenda that no longer exists and put no counters on the new one. The number of Vampire tokens is based on Elenda's power when she died - which includes the +2/+0 from Supernatural Stamina - even though the returning Elenda is not the same object.",
        ["triggered-abilities", "zone-changes", "last-known-information"], "hard", True,
        ["Elenda, the Dusk Rose", "Supernatural Stamina"],
        "Zone changes create new objects; dies-trigger counts use LKI."),

    TAJ("https://magicjudge.tumblr.com/post/728600362698850305/i-have-garruks-uprising-gratuitous-violence-and",
        "2023-09-16",
        "Garruk's Uprising + Gratuitous Violence + Atarka, World Render: trample math with doubling",
        "You control Garruk's Uprising, Gratuitous Violence and Atarka, World Render. You attack with Atarka and a 5/4 Dragon Spirit token; the token is blocked by four 1/1 Slivers. How does the damage work out?",
        "Damage is assigned before it is doubled, including for trample, so you cannot assign the doubled numbers. First strike damage step: the blocked 5/4 assigns 1 damage to each of the four 1/1 Slivers and 1 to the player, each of which doubles to 2; Atarka deals 6, doubled to 12. Second damage step: the 5/4 (now unblocked) deals 5, doubled to 10, and Atarka deals 6, doubled to 12.",
        ["damage-assignment", "replacement", "combat", "keywords"], "expert", True,
        ["Garruk's Uprising", "Gratuitous Violence", "Atarka, World Render"],
        "Doubling is applied after damage assignment; Atarka grants attacking Dragons double strike."),

    TAJ("https://magicjudge.tumblr.com/post/726101644658524160/does-smeagol-helpful-guides-first-ability",
        "2023-08-19",
        "Smeagol, Helpful Guide has an intervening-if clause",
        "Does Smeagol, Helpful Guide's first ability trigger at all if no creature has died by the beginning of your end step? Could you sacrifice a creature to Braids, Arisen Nightmare in response to make it trigger?",
        "No. Smeagol's first ability ('At the beginning of your end step, if a creature died under your control this turn, the Ring tempts you') has an intervening-if clause. The condition must already be true as the end step begins, and it is checked again on resolution. If nothing died earlier in the turn, the ability never triggers, so Braids cannot help.",
        ["triggered-abilities", "turn-structure"], "medium", True,
        ["Smeagol, Helpful Guide", "Braids, Arisen Nightmare"],
        "Intervening-if triggers check the condition at trigger time and at resolution."),

    TAJ("https://magicjudge.tumblr.com/post/726099723055939584/so-the-red-terror-gets-a-1-1-counter-every-time",
        "2023-08-19",
        "The Red Terror counts 'deals' events, not targets",
        "The Red Terror gets a +1/+1 counter whenever a red source you control deals damage. Does it trigger twice if one red source damages two things, e.g. Dual Shot?",
        "No - it triggers once per instance of the verb 'deals' involving damage to permanents and/or players, not once per affected object. Dual Shot and Blasphemous Act deal damage once each even when they hit several things, and combat damage (even with trample) is a single trigger. A modal spell such as Fiery Confluence gets you one trigger per instance of damage among the modes you chose.",
        ["triggered-abilities", "damage"], "hard", True,
        ["The Red Terror", "Dual Shot", "Fiery Confluence"],
        "Puzzle hinges on published wording ('deals damage to one or more ...') and damage events."),

    TAJ("https://magicjudge.tumblr.com/post/726097877559099392/if-i-manifest-nexus-of-fate-with-say-with-a",
        "2023-08-19",
        "A manifested Nexus of Fate dies face down and stays dead",
        "You manifest Nexus of Fate with Scroll of Fate, and the manifested 2/2 dies. Does Nexus of Fate's 'shuffle it into your library instead' ability save it?",
        "No, it stays in the graveyard. A replacement effect must exist before the event it would modify. A face-down Nexus of Fate has no abilities while it is dying, so it has no shuffle ability before it enters the graveyard, and by the time it is face up there it is too late to apply.",
        ["replacement", "face-down", "zone-changes"], "hard", True,
        ["Nexus of Fate", "Scroll of Fate"],
        "Face-down cards have no abilities; replacement effects need to exist before the event."),

    TAJ("https://magicjudge.tumblr.com/post/725282495429574656/if-my-opponent-has-a-platinum-emperion-out-and-i",
        "2023-08-10",
        "Damage is still dealt under Platinum Emperion",
        "Your opponent controls Platinum Emperion, so their life total can't change. You attack with Etali, Primal Sickness and they don't block. Do they still get poison counters?",
        "Yes. Dealing damage to a player does more than change life: the damage is still dealt even when the life total is locked, so Etali's trigger sees 11 damage dealt and gives 11 poison counters - which loses them the game. Platinum Emperion only stops the life-loss result.",
        ["damage", "life-totals", "triggered-abilities"], "medium", True,
        ["Platinum Emperion", "Etali, Primal Sickness"],
        "Distinguishes the damage event from its life-total result."),

    TAJ("https://magicjudge.tumblr.com/post/725374925944340480/i-cast-a-clown-car-while-i-control-omarthis",
        "2023-08-11",
        "Clown Car and Omarthis: one 'for each' effect is one instance of counters",
        "You cast Clown Car with X=several while controlling Omarthis, Ghostfire Initiate, crewing the Vehicle before the enters trigger resolves. Does Omarthis get one counter per +1/+1 counter Clown Car receives?",
        "No, just one. Clown Car's ability is a single instance of creating tokens and a single instance of putting +1/+1 counters on the Vehicle; effects that do something 'for each' of something are one instance of that action that scales. Omarthis triggers once for that instance.",
        ["triggered-abilities", "counters", "tokens"], "medium", True,
        ["Clown Car", "Omarthis, Ghostfire Initiate"],
        "'For each' scales a single action; it does not create multiple events."),
]

OR_ITEMS = [
    OR("https://scryfall.com/card/mkm/13/doorkeeper-thrull",
       "2024-02-02",
       "Doorkeeper Thrull and two permanents entering simultaneously",
       "You control Doorkeeper Thrull ('Artifacts and creatures entering don't cause abilities to trigger'). A creature you control and Doorkeeper Thrull itself enter the battlefield at the same time. Do their enters-the-battlefield abilities trigger?",
       "You control a Doorkeeper Thrull; a creature you control and the Thrull enter at the same time.",
       "No. If Doorkeeper Thrull and another creature or artifact enter the battlefield at the same time, neither one causes triggered abilities to trigger as they enter.",
       ["triggered-abilities", "static-abilities"], "medium",
       ["Doorkeeper Thrull"],
       "Official Gatherer ruling for the MKM printing."),

    OR("https://scryfall.com/card/mkm/13/doorkeeper-thrull",
       "2024-02-02",
       "Doorkeeper Thrull suppresses triggers, not replacement effects",
       "You control Doorkeeper Thrull. Your opponent casts a creature that enters tapped with a +1/+1 counter and has an enters-the-battlefield triggered ability. What happens?",
       "Doorkeeper Thrull on the battlefield; an opposing creature with an 'enters tapped with a +1/+1 counter' entry and an ETB trigger resolves.",
       "The creature still enters tapped and with its +1/+1 counter, because replacement effects (including abilities that apply 'as this permanent enters the battlefield') are unaffected by Doorkeeper Thrull. Only the triggered ability is suppressed, so nothing triggers.",
       ["replacement", "triggered-abilities", "static-abilities"], "hard",
       ["Doorkeeper Thrull"],
       "Official Gatherer ruling distinguishing replacement effects from triggered abilities."),

    OR("https://scryfall.com/card/dsk/227/bottomless-pool-locker-room",
       "2024-09-20",
       "A Room card's characteristics off the battlefield (combined doors)",
       "A Room card such as Bottomless Pool // Locker Room is in your graveyard. What are its characteristics there, and if an effect let you cast a permanent spell with mana value 3 or less from your graveyard, which door could you cast?",
       "Bottomless Pool // Locker Room is a Room card with two doors and a shared type line; it is currently in the graveyard.",
       "While a Room card is in any zone other than the stack or the battlefield, its characteristics are the combination of its two doors - Bottomless Pool // Locker Room has mana value 6 in the graveyard. If an effect lets you cast a permanent spell with certain characteristics, you consider only the door you are casting, so you could cast Bottomless Pool this way but not the other door.",
       ["double-faced", "zone-changes", "casting", "costs"], "hard",
       ["Bottomless Pool // Locker Room"],
       "Official Gatherer ruling for the Duskmourn Room mechanic."),
]


def write(name, items):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"{name}: {len(items)} items -> {path}")


def main():
    os.makedirs(OUT, exist_ok=True)
    write("curated_ci.jsonl", CI_ITEMS)
    write("curated_taj.jsonl", TAJ_ITEMS)
    write("curated_official.jsonl", OR_ITEMS)


if __name__ == "__main__":
    main()
