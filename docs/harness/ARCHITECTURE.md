# Harness architecture: every part, and why it is built that way

**Keep it true:** a change that alters how the harness works updates this document in the same PR
(`docs/STANDARDS.md` section 7).

This is the map of the harness that lets a language model play full games of Magic: The Gathering inside
the Forge rules engine, and of the benchmark built on it. Each section says what the part does and the
reason for the choice. Deeper records are linked; this document is the overview.

**The principle everything follows (owner, 2026-10-15): only the model limits play quality.** The harness
shows the model the game faithfully, asks it every decision, carries out what it says, and decides
nothing for it. A change that lets the harness decide, hide, cut or coach is a defect, unless it is
measured and opted into as a separate condition.

## 1. The shape of it

```
 Forge rules engine (pinned commit; four marked patches, ENGINE-PATCHES.md)
   │  asks the seat's PlayerController for every decision
   ▼
 PlayerControllerLLM  (forge-overlay/.../spike/LlmSpikeGui.java)
   │  1. builds what the seat may see        SeatObservation (faithful profile)
   │  2. builds the ask                      system prompt (fixed) + archive + state + decision
   │  3. sends it, with the tools            OpenRouterBridge (function calling, prompt cache)
   │  4. reads the answer                    AnswerTool: one `answer` call, validated
   │  5. carries it out                      returns the engine's own legal object
   ▼
 Engine applies it; illegal moves cannot happen: every option shown was produced by the engine
```

Around the seat: the eval layer (`eval/`) launches games, pins their conditions, prices them, scores
them and refuses to pool what is not comparable.

## 2. The engine

**Forge, pinned (`FORGE_COMMIT`, `docs/harness/ENGINE-PIN.md`); the checkout is never edited by hand.**
- *Why Forge:* it implements the full comprehensive rules for a large card pool, with a headless mode.
  The engine decides legality, so the model can never make an illegal move, and the harness never has to
  judge one.
- *Why pinned:* results are only comparable within one engine version. The benchmark version
  (`eval/benchmark_version.py`) includes the commit, and every game line records it.
- *Why unmodified except for listed patches:* a forked rules engine could drift from the one it claims to
  be. Everything we add lives in `forge-overlay/` and is copied in by `install.sh`. The overlay is the source of truth; files in
  the Forge checkout get overwritten.
- *Engine patches (owner, 2026-10-15), only where the seat has no hook:* a patched copy of an engine file at
  its own path in the overlay, every change marked `HARNESS PATCH`, the upstream original's sha256 recorded
  in `docs/harness/ENGINE-PATCHES.md` and checked by `eval/test_engine_patches.py`, so a re-pin forces a
  re-diff. Why: "truly let agents have control" (owner) where no `PlayerController` call exists. Two so far:
  the colour an any-colour source makes for a generic shard (`ComputerUtilMana`), and a companion the seat
  declines is no companion rather than a crash (`Player.assignCompanion`).

## 3. The seat: `PlayerControllerLLM`

**A subclass of Forge's `PlayerControllerAi` that overrides each decision method.**
- *Why subclass the AI controller:* the engine has paths that only work for a non-human controller
  (headless play, payment, triggers). Subclassing keeps those paths and replaces the decisions.
- *Why that is dangerous, and how it is policed:* every method not overridden is decided by Forge's AI.
  `docs/harness/controller-ownership.tsv` lists every controller method, who decides it (model,
  deterministic, Forge AI) and how often the pool reaches it. The acceptance run prints the count of AI
  decisions still reachable in Legacy play, and `[TALLY] ... ai=0` in each game log is the invariant.
  Since harness-epoch-14 no reachable decision falls back to it (section 15). Since harness-epoch-40 no decision of
  any mechanic is left to it either: exert, enlist, splice onto Arcane, coin-flip calls, shield counters, dice
  (ignore, reroll, modify, swap; planar dice), stickers, contraptions, attractions, multiple starting hands and assist
  were Forge AI's ("not in eval decks") and are the seat's, each with a golden check. *Why it mattered:* an
  inherited AI method does not count on `[TALLY] ai=`, so a game that reached one showed ai=0.

**A legal action is never hidden (owner, 2026-10-17: "if it's a legal action we don't disallow it, that's a model
issue, not harness").** Whether something is offered is the engine's legality, never a judgment of what is wise:
- What can be paid is decided by `legallyPayable`: the engine's mana and non-mana tests. Forge AI's
  `ComputerUtilCost.canPayCost` adds its own taste on top. It refused, half the time at random, a planeswalker
  ability that spends all its loyalty, and refused ward costs and casualty creatures it would not pay.
- A cost may be paid with the ability's own target, as a human may (Goblin Bombardment sacrificing the creature it
  aims at).
- Who crews, and which creatures that share a type tap, are the seat's.
- Mana abilities are on the menu, one per card name and ability, so mana can be floated.
- What a choice offers is also only what can be paid: a keyword cost's count (conspire, multikicker, squad,
  replicate), escalate's modes and strive's targets stop at what the engine says the cast can pay, so no answer
  the seat may give fails the cast (harness-epoch-43).
- A creature that can block more than one attacker may: its label says how many, and the `answer` schema allows it.
- An attack the engine refuses (Silent Arbiter's one attacker, a creature that must attack) is recorded (an
  `attackers-refused` row and a `refused by the engine` log line), asked once more with the reason ("at most 1
  creature can attack this combat"), then replaced by the engine's legal declaration
  (`AttackConstraints.getLegalAttackers`, a constraint solve), recorded as taken. The engine re-asks until the
  declaration is legal, so a seat that kept its answer looped (epoch 66; authored puzzles x_0, x_15, x_18).
- A spell whose targets are all "up to" is offered with nothing to target (Finale of Promise), and a mana source whose
  tap damage would be lethal still pays (City of Brass at 1 life; Forge's AI skips it, an engine patch lifts that for
  harness seats), but last, after every safe source (epoch 65). Both were found by the rules puzzle pilot (harness-epoch-52). So were these (epoch 53): an
  activated ability's alternative cost is offered (Forge Anew: equip for {0}; the menu expanded alternative costs for
  spells only), and an "up to one" target with a single candidate is asked with "No target" offered.
- Forge's static `AiCache` is cleared where Forge's AI clears it, at the seat's priority ask (epoch 54). It holds
  answers keyed by the game (`isPreventCombatDamageThisTurn`) that the seat's pricing and combat helpers read; uncleared
  they lasted the whole game, and in the puzzle prover every finished game stayed in memory until the heap ran out.
- A choice list's section headers are not options: Forge's "choose a source" puts divider cards ("--PERMANENTS:--",
  "--SPELLS ON THE STACK:--") into the list and asks again when one is picked (epoch 55, rules pilot RP_108).
- Combat damage is divided as the attacker's controller chooses (the 2024 rules; our games build GameRules with
  orderCombatants false): the seat gives an amount for each blocker and, with trample, the defending player; a split
  that tramples over before every blocker has lethal goes back with the reason; an unusable answer cascades
  lethal-first in the given order. Before epoch 56 the seat named only an order with that cascade, so "all six on the
  second blocker" or "lethal plus one, trample the rest" were never on offer. The legacy rules (orderCombatants true)
  keep the declared order. The prover's search arm branches over the splits. A divided spell's or ability's
  split gives each chosen target at least 1 (CR 601.2d), so it takes at most as many targets as its total; before
  epoch 64 a model or prover split of [2 0] was taken, and Rolling Thunder for 2 could aim at three targets. "Lethal" is the rule's (CR 702.19b:
  toughness minus damage marked, 1 with deathtouch, power under Zilortha), not what would kill: epoch 57 replaced
  Forge AI's estimate, which counted indestructible, protection, shields and doublers.
- A `simulate` preview copies the game with Forge's GameCopier, which threw on a card no copied zone holds (a prepared
  card's spell, Enroot), in the preview's copy and in the simulator's own internal ones. GameCopier is patched to skip
  such a card (epoch 58, ENGINE-PATCHES.md).
- A card may pay for its own ability (Mishra's Factory tapping itself for its {1} animate): Forge AI's mana chooser
  skipped an ability's own card, and a manland already animated this turn, as a taste (epoch 59, ENGINE-PATCHES.md).
  Not when both the ability and the mana ability tap the card (Rishadan Port cannot pay its own {1}; epoch 60,
  runs/loop/g27).
- A mana ability whose cost sacrifices a permanent of the seat's choice (Thermopod) is activated by the seat before the
  engine's payment, as a tap-type cost already was (#105); Forge's payment chose the creature with its AI (epoch 62,
  rules pilot RP_198). The payment's other planned sources stay out of the sacrifice while enough remain.
- A cast an effect makes "if able" that still costs mana (Sphere of Resistance on Omen Machine's free spell) asks
  whether to pay: no player is made to activate a mana ability (CR 605.3a), and unpaid the spell is not cast. An
  optional cast the engine has just confirmed ("Do you want to play X?", suspend) is not asked a second time (epoch
  62, RP_197, RP_209; the `playConfirmed` change is in commit b0258d6, titled `data(rules-pilot)`, not in the
  epoch-62 fix commit 343c0b6).
- Puzzles: while the board is put down, the arms take the first option and draw nothing, and the .pzl's `NamedCard`
  is set exactly afterwards (GameState adds it to the name chosen during setup; RP_207).
- A play is on the menu only if the engine's `canPlay()` and `checkRestrictions` both allow it. `canPlay()` leaves out
  the statics that forbid a play (Pithing Needle, Null Rod, Gaddock Teeg, Rule of Law), which Forge checks only as a
  human's play is made; before epoch 63 the seat was offered, and the harness played, Endbringer's ping under a Needle
  naming it (rules pilot RP_207, batch 10). A spell is asked as Forge's AI asks it, as an LKI copy on the stack cast
  from its zone (asked about the card in hand, Drannith Magistrate's "not cast from their hand" hid every hand spell and
  Grafdigger's Cage never matched a flashback). The same check covers the mana abilities offered and activated first,
  the lookahead's mana steps, and the play itself once the card is on the stack (the engine routine's check).
- Under a block requirement the zero-model arms take a legal block set from the engine's check (each blocker one
  attacker or none, up to 4096 combinations) instead of keeping an illegal draw; the prover's search arm draws once
  among the legal sets (epoch 59, rules pilot RP_185).
- A block set is checked as a whole (`blocksError`), by the engine's `CombatUtil.validateBlocks`: requirements ("all
  creatures able to block it do so") and restrictions (menace). Forge runs that check only for its own GUI, so before
  epoch 52 any block set the seat or an arm declared stood. `validateBlocks` never counts blockers against a global
  cap (Silent Arbiter, Caverns of Despair), so `blocksError` checks the cap too; and at the cap it does not hold a
  requirement against a creature that is not blocking, which cannot be obeyed without breaking the cap (CR 509.1c;
  the engine's requirement test ignores the cap and refused every set under Silent Arbiter and Prized Unicorn). The
  `answer` call is corrected in the same conversation; a text reply is asked once more with the engine's reason; an
  arm draws again (a legal first draw keeps its stream). When the seat's blocks are still illegal after that, or its
  answer is lost (two invalid calls, no call, no reply, an error), the fixed fallback is no blocks unless a
  requirement forbids it; then it is a legal block set chosen without randomness (`legalBlockSet`): up to 4096
  combinations the smallest legal one; past that the smallest legal one of the first-attacker set (each creature on
  the first attacker it can block) and one all-on-it set per attacker, else the first found by a search by
  increasing size capped at 4096 checks. It is an auto row (`blockers(illegal-twice)`, `blockers(unparsed)`,
  `blockers(no-model)`, ...) with a `[BLOCK-INVALID] ... took the legal set` line (epoch 71, issue #311: "no blocks"
  under Prized Unicorn stood illegal, found by the harness-env grader's `combat.block.must-block`). If none is found,
  no blocks stands, illegal (an arm keeps its last draw), and the log says `[BLOCK-INVALID] ... no legal set was
  found` (an arm: `no legal block set in 64 draws`), which `defect_analyzer.py` counts. A random arm whose 64 draws
  found no legal set and that is past the 4096 limit takes the same set.
- The one exception: a window whose only option is floating mana that changes nothing passes unasked, as an empty
  one: a land's ability costing only {T} and mana that only adds mana, into a pool that empties without burn, with no
  trigger on a tap or on mana added. Floating that does change the game (a creature tapped and unable to block,
  Manabarbs, Upwelling's pool, mana burn) is a legal play and is asked (`floatChangesNothing`, harness-epoch-42).

**What the harness may decide without asking (owner rule):**
- a choice with exactly one legal option (forced). A cost that takes exactly as many cards as there are
  candidates is paid with those, by the harness (since harness-epoch-40): left to Forge's AI, the pick could be
  refused (it declined to exile Altar of the Wretched for a craft, so the payment failed);
- the priority menu is PASS only (nothing castable, activatable or playable), or this exact spot (turn,
  phase, stack, board, menu) was already passed;
- a play that failed to pay, was forbidden by a static or was refused by the stack for want of a target is not
  offered again in the same window (turn, phase, top of the stack), as Forge's AI skips a failed ability: the
  seat was offered the same unpayable play 999 times.
  A play that fails to pay, is cancelled at its cost or is refused by the stack for want of a target is undone by
  the engine's own rollback (`GameActionUtil.rollbackAbility`, the human path's): the card goes back to the zone it
  was cast from and the mana paid is refunded; a non-mana cost already paid (a tap, life) is not, as the copied
  payment records none (harness-epoch-70; the copied engine routine's recovery left a card cast from hand in the
  Stack zone).

Anything new asks again: an opponent's spell or ability, every attack and block, a trigger, a new option.
- *Why:* skipping a window the model could act in, keeping a hand for it, or picking its play is a
  heuristic standing in for the model. The old shortcuts (mulligan heuristic and five-card floor, skip heuristics, a cheap
  fast tier) remain only as opt-in measurement switches (`MULLIGAN_HEURISTIC=1`, `SKIP_HEURISTICS=1`,
  `ONE_TIER=0`).

**Combo lines (2026-10-16, harness-epoch-32):** every step of a combo turn is the seat's and can be taken when the rules
allow: a mana ability that costs more than tapping (Lion's Eye Diamond, a Spirit Guide, Lotus Petal) is on the play menu,
so LED can be cracked in response to the seat's own Infernal Tutor; Doomsday's five cards and their order, Ad Nauseam's
every repeat, and each storm copy's target are asked. Golden checks play each line out (`GoldenChecksTest`:
`doomsdaysPileAndItsOrderAreTheSeats`, `adNauseamRepeatsAsOftenAsTheSeatSays`,
`tendrilsStormCopiesResolveAndTheirTargetsAreTheSeats`, `lionsEyeDiamondCanBeCrackedInResponseToInfernalTutor`,
`ritualThenPetalThenAdNauseamIsPaid`). The puzzle prover targets a copy that may take new targets (Twincast, storm) the
same way; under the search arm Forge's AI picked it, so the prover never tried another target (epoch 66, authored z_4).
The stack says what each item is and does: a spell or ability "not yet resolved", a copy effect with the number of
copies the engine will make ("trigger of Tendrils of Agony: Storm (...) -> makes 9 copies"), and none of Forge's debug
notes. Identical items (a batch of storm copies, whose targets come after) have no order to choose and are not asked
(harness-epoch-33). *Why:* Luna read "1 pending" as a choice it was owed, and was asked to order ten storm copies
labelled like the card in hand (C3 ANT).

**Targets on the stack and announced values (2026-10-16, harness-epoch-34):** when the stack is a spell's only target
zone ("counter target spell", Stifle), its choices are the spells and abilities on the stack themselves, as the human
path offers them (`targetCandidates`); an ability is named "ability of <card>: <what it does>". Values a cast announces
are asked as the engine's human path asks them (`announceX`, after `PlaySpellAbility.announceValuesLikeX`): each
`Announce$` variable (a Shoal's X), then an X in any cost part (Toxic Deluge's life), mana X up to what the seat can
pay. A cast at another cost than the card's basic spell (a pitch spell) is labelled with that cost. *Why:* the surface
checks found Luna's Force of Will "countering" a Dark Ritual that then resolved (the target was the card, and Forge
counters only targeted spell abilities), Disrupting Shoal's pitch failing at X = 0, and the pitch labelled with the
mana cost it replaces.

**Ways to play a card (2026-10-16, harness-epoch-35):** every way the engine lists (Forge's `AlternativeCost` and
`OptionalCost`) is offered and paid as the human path does it. Optional costs (kicker, multikicker, buyback, entwine,
bargain) are asked after the seat picks the play (`withOptionalCosts`, CR 601.2b), from the menu and from effects
(cascade, madness), and only when the whole cast stays payable, the picked set included (re-asked once with the reason, then declined). A spell playable only with one of them (retrace and
jump-start from the graveyard, an offering that lowers a cost the seat cannot otherwise pay) is offered with it, and
the hand tag says so. Emerge and offering sacrifices are the seat's, offering only creatures that leave the rest
payable. A spell or trigger with several targets ("any number of target creatures", divided damage) is asked for the
set on every path (`chooseManyTargets`). A creature spell's option shows its rules text. *Why:* Forge's AI asks
optional costs while listing its options, a step this menu does not use, so no kicker, buyback or entwine was ever paid;
Fury's trigger could take one target and the default took Fury itself; Elder Deep-Fiend's emerge was never offered.
Since harness-epoch-39 all 44 are played by a golden check (`eval/play_gaps.txt` is empty):
- **Pricing never touches the spell.** A probe copy gets its own pips-to-reduce list (`withOptional`): a Forge copy
  shares that list with the original, so pricing a Defiler's "pay 2 life, {W} less" left {W} off the real spell and
  the plain Savannah Lions was offered and cast with no land and no life paid.
- **Harmonize is priced with its tap.** The menu checks each power the seat could tap (`harmonizePowers`, with the
  card's cast spell set as the engine sets it), and the seat picks the power from the payable ones only.
- **An option says what the card does.** A cast at another cost (prowl, surge, spectacle, overload, a pitch) shows
  the text of the face being cast; the engine's description is only the cost and its reminder. The cost shown is the
  cost paid (a Defiler's reduction taken off), and alternative costs keep their printed names (Web-slinging).

**When the model gives no usable answer:** a fixed legal option (the first, or decline; no attack for
attackers), recorded on the decision row with why. Never Forge's AI, and never an option read out of the
model's plan text (the "plan-named" fallbacks went in harness-epoch-30, review M2).
- *Why fixed and recorded:* a fallback that chooses well would hide a broken ask; a fixed one keeps the
  defect visible in the numbers (`eval/defect_analyzer.py` counts them).

## 4. What the model sees: the observation

**`SeatObservation` (faithful profile, `CONTEXT_PROFILE=v4-faithful`).**
- The state as the seat's player could know it:
  - its own hand;
  - every public zone;
  - the opponent's hand count, plus cards revealed to it;
  - what was cast and happened since its last decision;
  - mana available, and what each hand card needs.
- **What a player keeps track of** (as MTGA shows it): the opponent's cards cast and lands played this
  game, the rules text of every opponent card seen, hidden cards revealed to the seat, cards seen going
  into an opponent's hidden zone (KNOWN IN HIDDEN ZONES), and the top of the seat's own library that it
  put or saw there (scry, surveil, Ponder, Brainstorm), top first.
  - *Why the library top is checked on every render:* it is the seat's knowledge, not engine truth, so
    it may never claim more than the seat knows. Drawn cards drop off, a shuffle (the controller's
    `cheatShuffle` hook, delegated unchanged) forgets it, and anything that stops matching is forgotten.
- Objects are named by handles (`@12`), never by engine ids.
- **Every fact in it is asked of the engine, the way Forge's own AI asks, never re-derived by the
  harness.** Re-deriving is how the seat came to be told less than Forge AI knows.
  - *Why:* the mana line used to count untapped sources per colour from card types. The three Tron
    lands read as 3 mana instead of 7, Ancient Tomb as 1, and a summoning-sick Llanowar Elves as 1.
    The seat was told it was short of mana that Forge's payment code knew it had (g7, Kozilek's Command).
  - Where each fact comes from:

    | Fact | Engine source |
    |---|---|
    | mana available, per source and in total | each mana ability's `canPlay()` and `amountOfManaGenerated`, as in `ComputerUtilMana.getAvailableManaEstimate`; plus the mana pool; mana with a spend restriction (`AbilityManaPart.getManaRestrictions`) listed apart |
    | "(castable)" | `ComputerUtilMana.canPayManaCost`, and the play menu's own offer check |
    | X up to N | `canPayManaCost` at each X |
    | which sources are spare in a payment | `ComputerUtilMana.getManaSourcesToPayCost`, and `canPlay()` on each source |
    | legal attackers and blockers, unblocked damage | `CombatUtil.canAttack` / `canBlock`, `ComputerUtilCombat.sumDamageIfUnblocked` |
    | lethal damage in a damage assignment | `ComputerUtilCombat.getEnoughDamageToKill` |
    | [sick], [tapped] | `Card.isSick()` / `isTapped()` |
    | the shuffle after a Ponder order | the ability (`MayShuffle`, or a Shuffle effect), not the message wording |
    | X in the mode and target questions | the announced `getXManaCostPaid` of the spell |
    | a card's name in a question | the engine's name, with an effect's engine id removed, its source's too when the source is an effect (one function, `safeName`; observation-faithful-v42, g33) |
    | a filter in a cost or a name ask ("another blue card", "three black creatures", "a nonland card") | the engine's filter (`Card.Blue+Other`) said in words (`filterParts`, `plainFilters`, `nameConstraint`), in every cost the seat reads and in the state's stack and history lines (`SeatObservation.redact`), under the faithful profile (legacy profiles keep the engine's text). Control and ownership ("creatures an opponent controls"), a chosen or shared name, and a bare filter (a plural) are said too. Any other condition is left out of a counted phrase ("a card"), reads "the creatures the card describes" in a bare one, and leaves a name ask to the card's own text. A creature type Forge pluralised with an "s" takes its own plural from Forge's type list (`typePlurals`: "Elfs" reads "Elves", g10) |
    | CAST THIS GAME, LANDS PLAYED | the engine's `GameEventSpellAbilityCast` / `GameEventLandPlayed` (a copy is not a cast), not the log line's wording |

  - Every `canPayManaCost` a harness seat asks runs with the card's AI-only payment hints hidden
    (`PlayerControllerLLM.seatCanPay`, `AI_PHYREXIAN_HINT`, epoch 68). `AIPhyrexianPayment$ Never` tells Forge's
    AI not to pay Gitaxian Probe's {U/P} with life; obeyed by the seat's check, Probe with no blue source was "(not
    castable now)" and off the menu, and a cost payable only with life went to the AI's payment, which refused it.
    The same hint is hidden around Forge AI's cost check in an effect cast or a trigger (`ComputerUtil.playStack`).
    The seat pays Phyrexian life itself (`phyrexianByLife` chooses the split, `payManaCost` pays the life): a split is
    offered only when the engine can pay the rest with mana, one way to pay is taken without asking (a `tier=auto`
    row), and the Phyrexian life is paid only after the mana. If a mana source's own life cost (Horizon Canopy) has
    left too little, the Phyrexian life is not paid and the mana is refunded; that source's life is not. Found by
    h_0's written line.

  - The one hint still worked out by the harness: "(need 1 more B)" compares the cost's coloured pips
    with the per-colour mana. It is shown only when the engine has already said the card cannot be
    paid, and only for plain costs.
  - Tested in `SeatObservationTest` (Tron 7, Ancient Tomb 2, a sick dork 0, a shortfall named in mana).
- *Why faithful:* hidden information must stay hidden, or the benchmark measures something else.
  `SeatObservationTest` renders two games that differ only in hidden state and requires identical
  observations.
- *Why handles:* a stable, short name for "this copy", usable in tools and answers. Engine ids leak
  internals and change between games.
- *Why uncut:* the harness no longer truncates event text, card text, reveals or history
  (`docs/harness/MODEL-LIMITS.md`). Cuts were a harness limit on the model.

**Every block says where it comes from.**
- The header of each block names its source:
  - engine state: YOU, OPP, `STACK (engine, ...)`, COMBAT, `FACTS (engine estimates ...)`;
  - Forge's game log: `GAME HISTORY (Forge game log, ...)`, `SINCE YOUR LAST DECISION (Forge game log, ...)`,
    REVEALS TO YOU;
  - the card database: OPPONENT CARDS YOU HAVE SEEN;
  - the harness's record of what the seat saw: `KNOWN IN HIDDEN ZONES (harness record: ...)`;
  - the seat's own words: YOUR PLAN, YOUR OPPONENT PROFILE, YOUR CARD INTERACTIONS.
- The guide repeats it in one paragraph, and says that no other model's words appear anywhere.
- *Why:* the log is in the engine's words and names players. A line like "Rival scries 1", or one naming
  the seat's own player, could be read as a message to the seat or as its own earlier output. The owner
  asked that what comes from the Forge log never be confused with agent output.
- Legacy profiles keep their bytes; only the faithful profile carries the labels (`GameHistoryTest`).

**Every public facet of the state is shown (2026-10-16, observation-faithful-v35).** Probed facet by facet against the
engine's state (`SeatObservationTest.everyPublicStateFacetIsShown`, `whatAPermanentRemembersIsShown`,
`aSuspendedCardShowsItsTimeCounters`): counters, damage, attachments, loyalty, tapped, summoning sickness, face-down and
phased-out permanents (a phased-out one used to vanish), poison and other player counters, the monarch, the initiative,
the city's blessing, day or night; what a permanent remembers (Pithing Needle's named card, Cavern of Souls' type, a
chosen colour, player or number, Chrome Mox's imprint, a token), also on lands; counters and waiting states off the
battlefield (a suspended card's time counters, an adventure, foretold, plotted). *Why:* a Needle naming Jace read
"Pithing Needle [untapped]", and a suspended Ancestral Vision showed no countdown.

**How to read the state is explained once, in the system prompt (`SeatObservation.GUIDE`).**
- *Why:* the per-message legend cost about 200 tokens a call outside the cache. It also disagreed with an
  older one in the system prompt (`docs/harness/PROMPT-AUDIT.md`, A1).

## 5. The prompt

**System message, fixed for the whole game** (`seatFraming`):
- the role;
- `<your_deck>`: the deck's plan and list;
- `<how_this_works>`: the engine, the tools, answering by call;
- `<reading_the_state>`: the guide;
- `<reference>`: terms and keywords, the same in every format and in a puzzle. It named the Legacy eval pool's
  archetypes until harness-epoch-30, which told only a Legacy seat what it could face (review 2026-10-16).

A puzzle replaces the role with `<puzzle>`: the author's goal and what the solver is told. For a Forge puzzle that is
Forge's description from its goal line on (goal, turn limit, text; the name and difficulty label are dropped since
epoch 69, the neutral brief of BENCHMARK-V2 section 3); a rules puzzle (an `[acceptance]` section) gets only its
turn limit and description (`LlmSpikePuzzle.seatBrief`, epoch 67), because its Forge goal is just the turn bound
(148 of the 526 converted puzzles in `eval/puzzles/rules-pilot/manifest.jsonl` say `Goal:Survive` and have `life p1 <= 0`
in their `[acceptance]` section, 2026-10-20) and its name states the rule it tests. The
Puzzle Goal card, which `inspect` reads, carries the same brief (`tellGoalCard`), and Forge's HumanControl warning
stays in front of it. A result row's `goal_oracle` is that card's text as the seat sees it.

**User message per decision:**
- the `<archive>` brief (what older evidence exists and how to search it);
- the state;
- the decision with its numbered options, last.

- *Why this order:* the prompt-cache prefix is exact. Tools, then the system prompt, then the history must
  not change between calls, so nothing per-decision goes early (`docs/harness/PROMPT-CRAFT.md` rules
  17–19). The question goes last because queries at the end of long inputs are answered better (rule 12).
- *Why facts, not coaching:* advice in the prompt is the harness's strategy, and it changes what is being
  measured. Principles, reasoning caps, worked examples and decision-text advice were removed (audit
  C1–C5). The deck's own plan stays: it is information a player has.
- *Why plain language:* capitals and "you MUST" cause over-triggering on current models (rule 10).
- *Why no versions in the prompt:* the model gains nothing from them, and a changing prefix would break the cache.
  Every stamp is on the game line instead (`docs/harness/VERSIONS.md`, generated from the code).
- The guide describes every section and hand tag the state can show; since observation-faithful-v37 it lists the
  alternative-cost, optional-cost and timing tags and says a mana ability's ACTIVATE only fills the pool.

## 6. The answer: a tool call, nothing else

**`AnswerTool`: one fixed `answer` function for every decision.** All its fields are optional:
- the answer fields: `option`, `options`, `blocks`, `amounts`, `keep`, `number`, `card`;
- the play extras: `steps` under `PLAN_EXECUTE`, `yield`, `profile`, `lines`, `keep_untapped`;
- `why`, one sentence for the record.

Each call is validated against the decision it answers. An illegal call gets the reason and one more
round in the same conversation; a second illegal call loses the decision.

A tool ask's decision text comes from the question builders the text lane and the ai-teacher seat share.
`AnswerTool.rephrase` removes their text answer line ("Reason briefly, then end with EXACTLY: ..."), and the
ask's one answer line is the `ANSWER:` line appended after it; no reasoning cap is left in (answer-tool-v11).
A text format that survives is logged as `[TEXT-FORMAT-LEAK]` and fails acceptance.
- *Why a call:* text parsing is where most harness defects came from. Models wrote `CHOICE` and `PICKS`
  together, put the answer mid-prose, or wrote tool markup as text. A schema makes the answer structured,
  and validation makes a wrong one explainable.
- *Why one fixed schema instead of one per decision:* tool definitions open the cache prefix. A schema
  that changed every decision discarded the whole cache on every call. A per-decision enum is kept as a
  measurement arm (`ANSWER_SCHEMA=per-decision`).
- *Why `tool_choice` is per model* (`AnswerTool.toolChoiceFor`, `PROMPT-CRAFT.md` rule 2):
  - **OpenAI models get "required":** measured on Luna, it keeps the reasoning, and a text-only reply
    becomes impossible.
  - **Anthropic models get "auto":** Opus 5.5, Sonnet 5.5 and Fable 5.1 refuse forcing, and a forced call
    skips thinking on the rest.
  - **Never the function by name:** that skips the reasoning on both.
- *Why every optional field's default means nothing:* a model may fill every field with its first or
  empty value. Luna did, and the old first `yield` value took it out of its own windows. `yield` starts
  with "none", and an empty `keep_untapped` is "not said" (answer-tool-v8).
- *Why a yield never passes the seat's own main phase:* a yield passes the windows with nothing of the
  opponent's on the stack until the turn it names. Given at the seat's own upkeep ("keep mana open"), it
  also passed that turn's main phases, so the seat lost its land drop and every sorcery-speed play
  (`runs/luna-v8/g3`, two turns). The main phases with an empty stack always ask (answer-tool-v9).

**No text alternative (answer-tool-v7).**
- A reply that calls nothing is reminded up to twice in the same conversation, then the decision is lost.
  Prose is never parsed as an answer.
- A tool ask that still carries a text answer format is a `[TEXT-FORMAT-LEAK]`.
- `GoldenChecksToolTest` runs every golden check with the seat answering by call, and fails on a leak.
- **Zero text parsing on a tool ask:** a decision reads the typed `AnswerTool.Answer`, never the reply.
  The text parsers are reached through a guard that refuses on any ask that offered `answer`, logs
  `[TEXT-PARSE]` and counts it (`textParses=` on `[TALLY]`); the answertool acceptance stage and
  `GoldenChecksToolTest` fail on one. The row's `raw_reply` is the answer rendered for the record only.
- *Why:* if a prompt offers another way to answer, a model will use it. The text grammar remains only
  for a backend with no function calling (the Claude CLI lane) and for the `ANSWER_TOOL=0` measurement
  arm.
- **The one exception (owner, 2026-10-24):** a decision model (`jev:`) cannot call anything. Its typed answer is
  converted into the `answer` call before validation, so everything downstream is unchanged (section 9, "The
  Decisions bridge").

**The tool list never changes within a game:** `answer`, `set_plan`, the lookups, and `simulate` on a
lookahead seat, declared on every ask. A call that does not apply gets an error saying why.
- *Why:* the same cache reason. A tool that comes and goes resets the prefix.

## 7. What the model can look up or do: native tools

**Function definitions in `NativeTools`**, mirrored in `eval/tooluse/tools.json`:

| Tool | What it does |
|---|---|
| `inspect` | the full text and state of a visible object or a card by name |
| `memory_search`, `memory_read` | the evidence archive of this game, which the brief summarises |
| `memory_note` | the seat's own notebook |
| `rules_lookup` | the comprehensive rules, effective date stated (`docs/harness/RULES-TOOL.md`). Offered only with `MEMORY_TOOLS=1` and `RULES_TOOL=1` together, so `run_batch.py` and `puzzle_solve.py` refuse `--rules-tool` without `--memory-tools` (alone it recorded `RULES_TOOL=1` for a benchmark pilot that never had the tool, 2026-10-20) |
| `simulate` | the lookahead: what an option resolves to, on a copy of the game (`LOOKAHEAD-DESIGN.md`). The copy shuffles every library and re-deals each opponent's hand from its hand and library, so a preview reveals no hidden card (lookahead-preview-v6, `LookaheadProbeTest.hiddenCardsDoNotChangeThePreview`). While the seat aims a play it is making, `option` is a target's number and the play is previewed at it (lookahead-preview-v10, seat-tools-v11; g03 asked there and was refused). A preview restores the targets of every ability of the chain it aimed (`aPreviewLeavesNoTargetOnAnyAbilityOfTheChain`: Arc Trail's second target stayed set, and the real cast skipped asking for it). A mana ability is a step like any other and the report shows the mana pool (lookahead-preview-v12; g10's line "cast Heritage Druid twice, then tap Gaea's Cradle" was refused whole). A counterspell's preview is aimed at the spell on the copy's stack (the copier maps only cards and players, and a counter aimed at the card fizzled), and the report counts exile as well as graveyards (lookahead-preview-v15, g28: Force of Negation against Seam Rip was unavailable, then showed Seam Rip resolving; `aForceOfNegationPitchOnTheOpponentsTurnIsPreviewed`) |
| `report_unclear_decision` | the model reports a confusing ask, which feeds the defect backlog |

Limits: 16 calls an ask, 1,000 a game. These are loop guards, not rationing.
- *Why native calls, not a text command grammar:* across 48 recorded games the text grammar refused 30
  of the model's 43 tool requests for their form. In the tool-use suite, the deciding fact came back
  from a tool in 87% of asks with native calls, against 13% with the grammar
  (`docs/harness/PLAYER-HARNESS.md`, "Tool protocol").
- *Why lookups instead of a bigger prompt:* progressive disclosure, as with skills. The prompt stays
  short and stable; depth (Oracle text, rules, old events) is fetched when wanted.
- *Why the rules come from a tool:* the model should look the rules up, not recall them; a model's
  memory of the comprehensive rules is unreliable at the edges.
- *Why no MCP in the seat:* MCP is a transport for tools. The tools are already native definitions in
  the request, so a server would add latency and give no new capability.

## 8. Memory across decisions

- **The archive (`ContextMemory`):** every event the seat saw, searchable with `memory_search`. Each ask
  starts with a short brief of what is there.
- **The plan:** `set_plan` (goal and next steps), shown back on later asks as "a note, not a commitment".
  - *Why a note:* every decision is re-made on the state then. The agent guides of Anthropic, OpenAI, Google and
    xAI all describe a loop in which the model sees the environment's result before each step. A plan carried out
    without asking again (`PLAN_EXECUTE`, opt-in for measurement only) is a predefined workflow, and Magic has
    hidden information and an opponent acting in between (WORK-PLAN item 16).
- **The opponent read and the card lines:** fields of the play answer.
- *Why an archive and not a growing transcript:* a full transcript grows without bound and drowns the
  current state. The archive keeps everything, retrievable, while the prompt stays current
  (`docs/harness/MATCH-MODEL.md`).
- *Why a fresh state per decision and not one long conversation (as Claude Code runs):* the board changes at
  every step and the engine holds the true state, so each decision gets it rendered fresh. Models are about
  39% worse in multi-turn conversations than single-turn (Laban et al., ICLR 2026) and less reliable as the
  input grows (Chroma, context rot). The research is in `docs/harness/REVIEW-2026-10-16.md`, phase 1b.
- **Conversations as conditions:** `TURN_CHAT=1` keeps this turn's earlier decisions in the conversation
  (a multi-step line in one train of thought) and starts over each turn; `ONE_CHAT=1` keeps the whole game.
  Both replay a decision as it happened: its DECISION part, the model's assistant messages unchanged with
  their tool calls and `reasoning_details`, and a result for every call (OpenRouter's reasoning guide: pass
  reasoning back unchanged and in order). Acceptance stage `turnchat` checks every request is a valid
  tool-call sequence. Opt-in until a measured block shows one keeps lines more coherent (review C3).

## 9. Talking to models: bridges

- **`OpenRouterBridge`** (`openrouter:<model>` specs) is the default. It handles:
  - function calling;
  - `cache_control` for Anthropic models (OpenAI and DeepSeek models cache on their own);
  - reasoning passed back unchanged across tool rounds;
  - per-model settings (`ModelCfg`): token caps and timeouts;
  - readable reasoning, kept on the call row where the provider returns it;
  - routing by latency (`provider.sort`, `OPENROUTER_PROVIDER_SORT`, default `latency`), and the provider
    that served each call on its row. A tool request adds `require_parameters` to the same `provider` object (one
    key: a body with it twice is read differently by different parsers, and a last-wins one drops the latency sort).
  - *Why latency routing:* Luna calls stalled for up to 6 minutes while answering in ordinary length (646
    output tokens in 352 s, against a median of 253 in 9 s). The time was the endpoint's, not the model's
    thinking. OpenRouter's default routing weighs price; the model is the same on every endpoint.
  - a model whose reasoning cannot be turned off (`reasoning.mandatory` on OpenRouter's model list, 2026-10-24; the
    list is `MANDATORY_REASONING_FLOOR` in `LlmSpikeGui.java`, hashed into `model_cfg_sha`) is never sent
    `enabled:false`, which it refuses: wherever "none" would be sent (the fast tier, the truncation retry) it gets its
    lowest effort, and its truncation retry keeps the whole token budget, since it still reasons.
    `eval/tool_use_suite.py` sends the same reasoning field wherever "none" is sent, and its floored retry keeps its
    budget (its copy of the table is pinned to the Java map by its test); its budgets are its own.
  - prices: `eval/pricing/prices.json` holds each model's list price, which reports use, and under `max` its dearest
    endpoint that takes tools, which `--budget-usd` checks against (latency routing may land on a priority tier).
    The file's `comment` names the models with a long-context tier and where each starts. Haiku 5.5's `max` is its
    long-context rate (above 100K prompt tokens a call); the other tiers start at 200K or more, and the largest
    prompt recorded in one call is 69,326 tokens (`runs/c3/ant-storm-turn/game-4101-1792129038688.jsonl`).
  - a stalled connection is retried (`HttpDeadline`, `OPENROUTER_IDLE_MS`, default 90 s, 0 off): no headers, or
    bytes and then silence, for that long, inside the ask's deadline.
  - *Why:* in the C3 combo games some connections sent nothing for minutes and held the ask to its full
    timeout, 15-25 minutes a game; a fresh connection answers in seconds.
- **`ClaudeBridge`** (the Claude CLI lane): text only, kept for the CLI.
- **`DecisionsBridge`** (`jev:<model>` specs, first TypeSafe's Jev 1.14): a decision model on OpenRouter's Decisions
  API. See "The Decisions bridge" below.
- **Egress boundary:** an `openrouter:` model receives the game state. Nothing starts unless
  `OPENROUTER_ALLOW_EGRESS=1` is set by the owner, and no tool sets it.
- **The game model:** GPT-6 Luna (`openrouter:openai/gpt-6-luna`) for every game unless the owner names
  another, at about $0.06–0.07 a game (one tier, every decision reasoning).
- *Why OpenRouter:* one API for many providers, so a model comparison changes one string.
- *Why the cache matters:* nearly all input is the same system prompt, tools and history. Recorded Luna
  asks read 54% of their prompt tokens from the cache on an ask's first round, and 92% on later rounds.

### The Decisions bridge (`jev:` seats)

**Owner decision, 2026-10-24:** TypeSafe's Jev 1.14 joins the frontier comparison on the hardest puzzles
(`eval/puzzle_solve.py`). Jev is not a chat model: it is served only on OpenRouter's Decisions API
(`POST /api/alpha/decisions`), writes no text and calls no tool. It answers typed questions about a `state`: a
`choice` among keyed options (with a probability for each) or a `noul` (the probability of yes). The owner approved
an exception to "every decision is answered by a tool call" (`CLAUDE.md`) for it: **Jev's typed answer is turned into
the harness's own `answer` call**, so `AnswerTool` validates it, the decision row records it (`answer_via: "tool"`),
and the correction round and the fixed legal option apply exactly as for any model.

- **One request per ask.** `state` is an object, `{"game": <text>}` (the Decisions API docs send an object or an
  array), whose text is the seat's system preamble followed by the ask's messages as the harness renders them (the
  observation and the DECISION, and on a correction round the reason the last answer was refused). The `[JEV]` log
  line gives its size (`state_bytes`). The questions are built from the decision's typed legal values (`AnswerTool.Spec`, handed to the bridge
  by `LLMBridge.chat(..., spec)`), never parsed out of the prompt text; option keys are the option numbers the
  prompt shows.
- **Endpoint, egress, key:** `OPENROUTER_BASE_URL` with its `/v1` replaced by `/alpha/decisions`. Both bridges are
  built through `LLMBridgeFactory.requireOpenRouterAccess` (egress consent, then a key for a real endpoint), checked
  before any other refusal, so a `jev:` seat sends nothing without `OPENROUTER_ALLOW_EGRESS=1` unless the endpoint is
  loopback, and `OpenRouterBridge.authorizationHeader` never sends the key to loopback. `puzzle_solve.py` refuses a `jev:` model without the opt-in as it does an `openrouter:` one.
- **Bill:** `usage.input_tokens` and `usage.output_tokens` become the call's `prompt_tokens` and `completion_tokens`
  (priced by `eval/cost_report.py`: `typesafe/jev-1.14` is $0.042 per million input tokens, output free), and the
  provider's `usage.cost` is the `context_call` row's `cost_usd` (an additive key, written only by a bridge that
  reports a cost). A call that fails reports no usage of its own. `model_id` resolves the `jev:` prefix.
- **Version:** `DecisionsBridge.VERSION` (`decisions-bridge-v1`) is stamped per identity on the game line,
  `decisions_bridge_version_a` / `_b`, only for a `jev:` seat (keyed in `aggregate.PROVENANCE_FIELDS` with default
  "none", and a per-identity player field in `rating.py`, so the opponent's condition is unchanged), and on the
  puzzle row of a solver with a `jev:` tier (smart or fast, as on the game line), where `puzzle_score.py` refuses to pool two versions. Acceptance pins the literal from a
  loopback answer in the javatests log, and checks that a game with no `jev:` seat carries no such key. No existing seat's prompt or answer path
  changed, so no other stamp and not `HARNESS_EPOCH` moved.

How each decision kind (`AnswerTool.Kind`) is asked:

| Kind | Example decisions | Questions | Becomes |
|---|---|---|---|
| `ONE` | play menu, target, optional trigger, mode | one `choice`, criteria `"1".."n"` = the option labels | `option` = the chosen key |
| `MANY`, at most one pick | splice, sacrifice one, scry 1, attack with one creature, a cost of one | one `choice` over the options, plus `"0"` = none when none is legal | `options` = `[k]`, or `[]` for `"0"` |
| `MANY`, more than one pick | tap three creatures, trigger order, attackers, discard two | one `noul` per option ("should option i be one of the picks?") | every option at p >= 0.5, likeliest first, clamped to the decision's min and max; a CANCEL option is taken alone only when it is at p >= 0.5 and the likeliest, else never (`DecisionsBridge.topPicks`). An ordering decision reads the picks in that order |
| `MANY_REPEAT` | modes that may repeat | one `choice` per mode: how many times, `"0".."max"` (up to 39) | the modes, each its count of times; above the maximum the surest counts are kept, below the minimum the surest picked mode is repeated (`DecisionsBridge.repeatPicks`) |
| `KEEP` | mulligan | one `noul` ("keep this hand?") | `keep` = p >= 0.5 |
| `NUMBER` | X, a number of counters, a wide keyword cost | one `choice` over every value, when the range has at most 40 | `number` = the chosen key; a wider range is not asked |
| `BLOCKS` | declare blockers | one `choice` per blocker: `"0"` = no block, `"j"` = attacker j; a blocker that may block several attackers gets one `noul` per attacker instead | the pairs; a multi-blocker blocks every attacker at p >= 0.5, up to its limit |
| `ALLOC` | divide damage | one `choice` over every split, when there are at most 40 | `amounts` = that split; more splits are not asked |
| `CARD` | name a card | none: there is no closed list of options | not asked |

A decision that is not asked (a card name, a range or division over 40 values, a mode count over 39, an option list
without labels; each listed in `docs/harness/MODEL-LIMITS.md`), a
non-200, a timeout, and an answer that is malformed or names a key the criteria do not have, all give **no answer**:
the bridge logs `[JEV-NO-ANSWER]` with the reason, the seat's bridge retry runs, and then the caller takes its fixed
legal option, never Forge's AI. A plain text ask (a decision site that offered no `answer`) is not asked either.

The three puzzles of the first Jev run (`eval/suites/puzzles-hard-gen2-v1.json`: `hard-splice2-325cd33e0a57`,
`hard-hoofdrain-60c5afda2f25`, `hard-drain2-0ab6f6ae05d9`) asked Luna these decision types
(`runs/puzzles/hard-gen2-luna/<id>/game-*.jsonl`, 122 decision rows, 116 of them asked): play, target and
confirm-trigger (`ONE`, 81); splice, attackers and scry (`MANY`, 0 or 1 of them, 13) and sacrifice (`MANY`, exactly 1
with CANCEL, 16), each one `choice`; cards-for-effect (tap exactly 3 of 4, with CANCEL, 1) and trigger-order (both of 2,
in order, 5), each a `noul` per option. Every one maps; none is a `CARD`, `NUMBER`, `ALLOC` or `BLOCKS` decision (the
6 splice and counter-type rows at `tier=auto` were never asked).

What Jev cannot do, by construction:
- **No lookups.** It cannot call `inspect`, the memory tools, `rules_lookup` or `simulate`, so a `jev:` seat with
  `MEMORY_TOOLS=1` or `LOOKAHEAD=1` is refused when it is built, and `run_batch.py` (`--memory-tools`, `--lookahead`)
  and `puzzle_solve.py` (`--memory-tools`) refuse those flags for it before anything runs.
- **No text answer.** Without the answer tool (`ANSWER_TOOL=0` or `NATIVE_TOOLS=0`) every decision would be a text
  ask it cannot take and would be lost, so such a seat is refused when built, and `run_batch.py` refuses
  `--text-answers` and `--text-tools` for it.
- **No plan, no notes, no yield, no `lines` or `profile`:** the play answer's optional fields are never given, and
  `set_plan` is never called; every decision is made on the state then.
- **No reasoning text:** the row's `raw_reply` is the answer rendered for the record, with no reason before it.
- **Independent questions:** the nouls of a multi-pick are answered in parallel and cannot see each other, so a
  pick list is assembled from separate yes/no judgements, not chosen as a set.

## 10. Costs

- Every call row carries:
  - prompt, completion, cached and cache-write tokens;
  - its price, from `eval/cost_report.py` with `eval/pricing/prices.json`.
- A puzzle attempt's result row carries what a model seat spent (`PlayerControllerLLM.budget()`, harness-epoch-69):
  `model_calls`, `prompt_tokens`, `completion_tokens`, `model_ms`, `lookups`. Tokens come from the bridge's bill
  (`LLMBridge.getBilledPromptTokens()`/`getBilledCompletionTokens()`), read as a difference around each ask:
  `OpenRouterBridge` bills every send that came back, a truncation retry's first send included. A bridge that keeps no
  bill returns -1 and the seat adds its last usage only when the ask answered. A bridge that wraps another
  (`ChatSessionBridge`) must pass the bill through, or the seat falls back to last-call usage.
  - *Why:* reading the last call's usage once an ask counted a failed ask's stale tokens again and lost a retry's
    first send (pr-review of #282).
- `run_batch.py --budget-usd` is required for paid games: it stops starting games at the budget.
- The owner rule is a small block first, with the expected cost stated before it starts.
- *Why:* small per-game costs add up unseen; `eval/cost_report.py` prices any run, and `--plan N` the next N games.

## 11. Zero-model arms and canaries

| Arm | What it does |
|---|---|
| `floor` | always passes |
| `random` | uniform over the same legal menus |
| `ai` | Forge's AI, the opponent and a baseline |
| `pass`, `first`, `script` | puzzle arms |
| `greedy:` (a model spec, `CONTROLLER_A=llm`, `LOOKAHEAD=1`) | the one-move-deep floor (`GreedyBridge`): on an empty stack, previews each cast and non-mana activation with the seat's `simulate` and takes the largest drop in the opponent's life, else passes (a preview also resolves what is already on the stack, so with something there, or without the faithful STACK line, it passes); attacks with everything; a target ask takes what the chosen play's preview assumed (the opponent's copy of a name first, each used once across a chain's asks), else ranks the opponent's side first and the seat's own last, below declining an "up to" ask, never CANCEL; any other ask the do-nothing answer; plays no land. Its stored rate carries its env and harness epoch |
| `script:<file>` (a model spec, `CONTROLLER_A=llm`) | a written solution line played by the model seat (`ScriptBridge`): each line names the option the seat is shown, the bridge answers `answer` with it, and a line no option fits is a drift, never a guess |
| `search` | the puzzle prover: both seats draw from one shared line of choices (`PuzzleArms.Search`), and `PuzzleSearch` replays prefixes to find a winning line and beat every opponent reply on it, then (rules puzzles) searches each solver deviation for a second solution; a rule puzzle's `[acceptance]` section (`PuzzleAcceptance`) decides what a solve is |

- *Why the prover:* Forge's puzzle mode tests one opponent line, its AI's, so a solve can come from the AI
  blocking badly. A proof (`eval/BENCHMARK-V1.md`, phase 2) beats every opponent choice the engine offers, and
  every leaf of it is a solve Forge scored. A decideMany subset is drawn through `PuzzleArms.subset`, which
  makes the old draws for every other arm (no canary moves) and one include-or-not per option for search.

- *Why written lines:* a 12-action line with three options a step is one line in half a million, which no rollout
  budget finds (h_0 at budget 600, `docs/harness/HARD-PUZZLES.md` section 4). A person can write it. Played through the
  model seat, the line sees the menus a model sees (floating mana, costly mana abilities, splice and alternative
  costs) and the engine checks every step. Unmatched priority menus pass and unmatched mana-untap asks are left to
  the engine, both counted; a result row's `script_played` is true only when every line was taken with no drift.
  `puzzle_baselines.py line` reads the row (`eval/PUZZLES.md`, "Proving a written line"), including the seat's
  `plays_not_made`: a play the line chose that did not happen voids it, and a row without the count is refused.
  The seat's two tiers share one bridge, so a script has one position. Against `CONTROLLER_B=search` the prover
  plays the line as the solver, so every branch it enumerates is an opponent reply the fixed line must beat.
  `script:relaxed:<file>` (the minimality replays) answers a decision the line does not fit with the do-nothing
  option instead of drifting.

- The seed-42 floor and random mirrors are pinned canaries, at A/108/1828 and A/55/365 (Linux pins, `eval/harness_acceptance.expected`).
- *Why:* a score is meaningless without the trivial baseline it beats (the "88% lesson" in `CLAUDE.md`).
  A canary that cannot move cannot catch a regression, so any change to an automatic path re-pins it
  with evidence.

## 12. The eval layer

**Formats and decks.** The Legacy pool (16 decks) is the original benchmark. Beside it, real MTGO Challenge top-8
lists for six formats (Legacy, Modern, Pioneer, Standard, Pauper, Vintage; `eval/decks/SOURCES.md`), named
`<format>-<archetype>.txt` (`deck_legality.FORMAT_PREFIXES`), run with `run_batch.py --format <f>`.
- The engine plays any deck, so the format is what the seat is told (`GAME_FORMAT`, in the system prompt)
  and what the game line records (`game_format`, an experiment condition: formats never pool).
- `eval/deck_legality.py` checks a deck against the pinned engine's own format data (legal sets, ban lists,
  Pauper's rarities); `--format` refuses a deck that is not legal.
- A deck with no `.plan` gets no plan text, only its decklist and card texts. *Why:* the fallback used to be
  the default decks' plan, so a Standard deck was told it was Grixis Reanimator.

- **`run_batch.py`** launches games:
  - it pins every condition explicitly (ambient environment is scrubbed) and records provenance;
  - provenance: `overlay_sha` hashes every overlay source (`forge-overlay/*/src/main/**`: the harness
    classes and the engine patches), and cited mode refuses when any installed file differs from the
    checkout (`benchmark_version.overlay_tree_sha`; it covered two files until 2026-10-16, review H3);
  - unset `--context-profile` is the faithful seat (`v4-faithful`), as in the play scripts; the legacy
    `v1-compat` (coaching, a reasoning cap, a 25-decision turn cap) runs only as a named arm;
  - it resumes;
  - it plays a crashed game once more and sets the failed attempt aside (`--retries`);
  - it enforces the budget;
  - it refuses an llm seat that does not name both of its models, in every mode: a seat with no controller
    is an llm seat, and an unset model fell through to the harness default (Opus), so a Luna mirror
    launched with seat B unset played Luna against Opus 5.5 with every stamp healthy (issue #234);
  - cited mode forces `--jobs 1` only when a seat runs on the local claude CLI (parallel JVMs starve it);
    `openrouter:` games wait on the network and may run in parallel (review E3).
  - *Why pinned:* a baseline arm that silently inherited a setting would be pooled with a different
    measurement.
- **`aggregate.py`** reports win rates with confidence intervals, kept apart per condition.
  **`defect_analyzer.py`** is the run oracle: `ai_tier_count=0` and no silent drops, or the run fails.
- **Seed governance (`eval/seed_policy.py`):** some seed blocks are reserved for evaluation, and no
  training data may come from them.
- **The three headline scores:**
  - **STR-AI:** win rate against Forge's AI over fixed panels (`eval/SCORING.md`). The panels file pins
    the pilot condition (profile, tiers, answer tool, tools, lookahead, chat windows, plans): `run_batch.py
    --panel` launches and `panel_score.py` scores no other. *Why:* a score is a property of one seat; two
    settings under one name would be two benchmarks.
  - **STR-HELDOUT:** the same on a held-out seed band; to become the private mined puzzle split
    (`eval/BENCHMARK-V1.md`, owner decision 2026-10-18).
  - **PUZ:** the puzzle suite (`eval/PUZZLES.md`), where Forge enforces the goal and a solve is judged
    by the engine. Its hard suite (`eval/suites/puzzles-hard-gen-v1.json`) is generated (`eval/puzzle_gen.py`): each
    board's own written line is its reference line (`arm: written`), replayed against Forge's AI, and a board is
    active only when every floor failed it. *Why:* the proven rules puzzles are mostly one or two actions; a
    generated board is 10 to 15 steps with the opponent at exactly what its line deals, and is fresh, so it
    cannot be memorised.
    A second suite (`eval/suites/puzzles-hard-gen2-v1.json`) adds a trap to each board: a natural plain plan,
    searched to its own best, falls 1 to 3 short of the searched maximum. A third
    (`eval/suites/puzzles-hard-gen3-v1.json`, 68 boards) adds Goblin token engines, land sequencing
    (landfall), Equipment and infect at the same bar.
- **Ratings (`rating.py`):** Bradley-Terry with the Forge AI pinned at 1500, a first-player term, a
  deck-matchup term and cluster-bootstrap CIs.
- **Benchmark version:** reports pool only when they share engine, overlay, decks, seeds, panels,
  puzzles and tools.
- *Why so many refusals:* each one stops a number from meaning something other than what it says.
- **The demo viewer (`demo_server.py`, `docs/harness/DEMO.md`)** replays a recorded game, or follows a running
  one, in a browser: the board as the seat's prompt showed it (`demo_state.py`), each decision with the model calls,
  tool requests, provider reasoning and answer that produced it (`demo_events.py`), and the objects the model's own
  calls and words name (`demo_highlights.py`). It reads the JSONL and the console log beside it and writes nothing
  into a run. Card images come from a local cache (`demo_cards.py prefetch` fetches them from Scryfall by card name;
  the server fetches a miss only with `--fetch-images`). *Why read-only:* it can then never change what the seat
  sees or decides, needs no engine and no model, and works on every recorded game.
- **The audience feed (`DemoFeed.java`, `DEMO_FEED=1`)** is the one engine-side piece of the demo: on each game
  event it writes engine truth (both hands, every zone, the stack, combat, each llm seat's `@handle -> engine id`
  map) to `feed-<seed>-<ms>.jsonl` beside the game JSONL, with the JSONL's length at that moment so the viewer can
  put each snapshot between the decision rows it fell between. It renders no prompt and calls no seat, so the seat's
  prompts are byte-identical with it on and off (`DemoFeedTest`); no version stamp covers it for that reason. It is
  off unless set; `run_batch.py` passes it as `--env DEMO_FEED=1`, recorded in the manifest (not a condition key). The seat hands out `@handles`
  while it renders a prompt, with no game event before the decision row, so `EvalLog.write` also calls
  `DemoFeed.beforeDecisionRow()`, which writes a board-less `feed_handles` row at that row's offset: a card first shown
  in a prompt is mapped by its own decision.
  *Why:* the audience should see both hands; the seat never may, and the JSONL holds only what the seat saw.
- **The side by side (`demo_compare.py`)** builds this harness and an older commit of it (same engine pin, the feed
  ported in) in isolation and plays both on one seed and deck pair from the fixed sets in `eval/demo/decks.json`; the
  viewer marks the first decision where the games part (`demo_events.divergence`). `--parallel` plays both arms at
  once, each in its own JVM, so they can be watched live (`demo_server.py --live OUT/now --live OUT/then`); two panes
  open in watch mode, one play button running each game on its own recorded clock (a puzzle pair opens in full
  detail, where its puzzle panel is).
- **Puzzle mode (`demo_puzzle.py`)**: a puzzle run's pane shows the brief the seat was told (the result row's
  `goal_oracle`, else the .pzl's Description), who played (the puzzle's written line, another script, or a model),
  the result row, and behind a reveal the .pzl's intended line, which the seat never sees. Solution steps are mapped
  onto decisions only for a clean written-line run (no wait, untap fallback, drift or exhaustion in the script) of
  the file that was played (`pzl_changed` when the .pzl's hash no longer matches the row's); another seed's result is
  never borrowed. A puzzle pane is finished on its result row (a puzzle writes no game row).
- **The harness-building environment (`docs/harness/HARNESS-ENV-FRAMEWORK.md`)** treats building this harness as an
  agent task. `harness_env_sandbox.py` builds the agent's sandbox from an episode spec (`eval/harness_env/`) and
  proves no withheld file, token or history is in it; `harness_env_grade.py` grades a submitted overlay with the
  hidden tests in a workspace outside it (`surface-v0`, our own classes only). The instance grader
  (`forge/harnessenv/` in the overlay's test tree, run by `harness_env_grade.py instances`) drives any seat built
  through `harness-env-abi/1` (`SeatFactory`, an OpenAI-compatible scripted model on loopback) over 69 hand
  instances and sets generated from the card scripts (`harness_env_generate.py`), judges by engine outcomes, and
  counts Forge's AI inside a patched `PlayerControllerAi`, the engine's or the submission's own marked copy, which is
  refused when it counts a hook name fewer times than the engine's file (any count gates the benchmark to 0). Ours
  (`HarnessEnvOurs`) scores 0.979; the default bridge, the ablations and the toys 0.
  `harness_env_episode.py` runs an episode end to end and `harness_env_calibrate.py` compares scoring settings.
  The candidate's seat still runs inside the grader's JVM and could rewrite its report (an open hole, framework
  section 4.7), so an episode emits a candidate's number only with `--trust-candidate`; intake scores 0 a
  submission with an unmarked engine patch or a class in `forge.harnessenv`.
  It reads the harness and never changes it. *Why outside:* a grader the
  candidate can see or run is a target, not a measure.
- **The eval page (`demo_eval.py`, `/eval.html`)** shows an acceptance run's stages, PUZ from the recorded results
  via `puzzle_score.score`, a hard-suite check as a labelled pilot (`--hard-results`, never pooled), and the STR
  scores as not yet run with their cost.

## 13. How a change is checked

1. **`eval/harness_acceptance.sh`:**
   - an isolated compile;
   - every Java and Python test, with a declared-vs-run count;
   - the pinned canaries, with a class-load proof that the new build ran;
   - loopback fake-model games for tools, answers, the faithful profile, lookahead and rules;
   - `scoring`: a puzzle and a panel cell played through the launchers a paid run uses and scored by
     `puzzle_score.py` and `panel_score.py` (since 2026-10-16, review M4).
   - It never touches the Forge checkout.
2. **Golden checks:** every decision type the seat owns, through the live controller, in both the text
   and the tool protocol.
   - Every prompt a golden check asks is linted (`AskContract`): no CARDNAME, no engine card id, no engine filter
     syntax (`Card.nonLand`), no Forge debugging note on a trigger (the label words come from the engine's own
     localizer), no option cut short. `match_review.py` flags the same in games as LEAK.
   - Acceptance stage `surfaces` (2026-10-16): the Java tests record, for each decision row, the engine hooks
     on its call stack (`-Dharness.surface.report`, off in play). Every hook the ledger marks as a decision
     the seat answers (`controller-ownership.tsv`, kind strategic or payment) must have been reached with the
     model asked. The ones not yet proven are listed in `eval/surface_gaps.txt`, which may only shrink.
   - The same stage checks the ways to play a card: each play the seat makes records how it was played
     (`play:alt:Evoke`, `play:opt:Kicker1`, from which zone, which face), and every kind the engine lists must be
     played by a golden check or listed in `eval/play_gaps.txt` (may only shrink).
   - **Every mechanic (`KeywordSurfaceSweepTest`, 2026-10-17):** for each of Forge's keywords, three real cards
     that have it, in each zone and turn moment a card can be played from (own main phase, own combat with an
     unblocked attacker, own upkeep, the opponent's turn). Parity: what the engine lets a human do with the card
     (`Card.getAllPossibleAbilities`, the list the GUI offers on a click, kept when payable with a legal target)
     must be on the seat's menu, and the menu must offer nothing else. Playthrough: each such play is made by the
     seat with a fake model that answers every ask in its own format, resolves, hands nothing to Forge's AI,
     reads no answer it cannot parse and keeps `AskContract` on every ask. Any gap fails the test. The five
     keywords with no card to sweep are pinned (no card uses Absorb, Frenzy or Poisonous; the agendas are
     Conspiracy draft only).
   - **The keyword gauntlet (`eval/keyword_gauntlet.sh`, on demand, no model cost):** the sweep's 553 cards in 28
     decks (`eval/gauntlet/`), each deck's LLM seat on the loopback fake model (`--policy first --answer-tool`:
     the first option of every ask) against the next deck's Forge AI, in full games. Triggers, combat, upkeep and
     end-step mechanics reach the seat as a game brings them. `eval/gauntlet_scan.py` fails on a choice Forge's
     AI made, an unreadable answer, a play that failed to pay, a crash or an unfinished game. *Why both:* the
     sweep proves each play is reachable and plays once; only games bring the states in between. The first run
     found a craft the seat could never pay (with one candidate, the exile was left to Forge's AI, which declined
     to exile Altar of the Wretched) and chose 999 times in one window, and a StackOverflowError (floating mana
     asked while the hand was priced). Its second run, with failed payments counted, found three more: an
     ability offered at X=1 with no counter to remove (Arcee: the menu now checks the costs beyond mana at the
     least X, and X is capped by what can pay it), a spree mode chosen that the mana could not cover (modes are
     offered only when payable, and checked together), and a convoke payment one short (creatures that may
     convoke are not counted as mana when a land is kept untapped; a convoke that leaves the rest unpayable is
     asked again with the reason).
     `GAUNTLET_POLICY=random` answers every ask with a uniformly random legal answer instead (seeded per deck, so a
     game replays): the targets, picks, splits, numbers, blocks and mulligans `first` never gives. Its first run
     found four more: a cost the cast could not pay offered as a count to pay (conspire with no creatures to tap;
     escalate's second mode; Squad and Replicate "any number of times" answered 1.2 billion, which hung the payment
     and ran out of heap). Keyword costs, escalate modes and strive targets are now offered only as many times as
     the engine says the cast can pay (`payableKeywordTimes`, `escalateCost`, `keywordCost`). Its second run found
     a CANCEL answer to a tap cost paid anyway: writing the decision row cleared the cancel before it was returned,
     only with the game log on, which no golden check had; `GoldenChecksToolTest` now runs with the log on.
     `GAUNTLET_TOOLS=1` gives the seat its lookups, and the random policy previews, inspects and searches at random.
     Its first run found a StackOverflowError: a hand tag priced inside another pricing switched the guard off when
     done, so a pool-mana choice reached from the next tag asked the seat, whose prompt priced the hand again. The
     guard is now restored, never cleared, and building any prompt counts as pricing from start to end, so no hook a
     prompt build reaches can ask (`renderSnapshot`; `pricingInsidePricingKeepsTheGuardOn`).
   - *Why measured, not listed by hand:* a hand-kept map says which test should reach a hook; the record says
     which test did. A per-type list let a hook that only ever took forced picks look covered.
3. **Optional live-model checks (`eval/test_live_answer_tool.py`, `LIVE_MODEL_TESTS=1`, about a cent):**
   - short real chats with the game model on fixed decisions, using the real `answer` function and the
     per-model `tool_choice`;
   - every reply must be a call with a legal option, a cast must not fill in a yield, forcing must keep the
     reasoning, and a free spell (Force of Will, no land) must be answerable;
   - skipped in every ordinary run, so the suite stays free.
4. **`eval/tool_use_suite.py`:** tool reach, correctness and restraint, each beside its trivial
   baseline, before any game is played.
5. **One game at a time** (the `game-loop` skill): play it, read it decision by decision, fix what the
   harness did wrong, then play the next. A batch is not how the harness is tested.

- *Why this order:* cheapest first. The rule the canary history earned: before trusting a green gate,
  say what would have made it fail.
- **Instructions for agents** (2026-10-17, from Anthropic's Claude Code guidance: CLAUDE.md is loaded in every
  session, so keep it under 200 lines and move the rest where it loads on demand): `CLAUDE.md` holds what applies
  everywhere; `.claude/rules/` holds path-scoped rules that load when matching files are touched (the overlay, the
  eval, the seat's prompts and tools); `.claude/skills/` holds the workflows; `docs/harness/INDEX.md` marks each
  document current or historical. `eval/test_docs_index.py` keeps CLAUDE.md under 200 lines and the index complete.

## 14. Deliberately not done

| Not done | Why |
|---|---|
| Naming the answer function in `tool_choice` | it skips the reasoning (Anthropic and OpenAI alike); "required" is used where it keeps it |
| `strict: true` by default | not every provider supports it; `ANSWER_TOOL_STRICT=1` measures it |
| Agent Skills or MCP inside the seat | the native tools already provide them |
| Forge AI as a fallback | it would decide for the model |
| Coaching in the prompt | it would measure the harness's strategy |
| Cutting text to save tokens | the cache makes repeated text cheap, and a cut hides information |

## 15. Known gaps (see `docs/harness/WORK-PLAN.md`)

- **Forge's AI decides nothing the seat can decide** (priority 0, A, harness-epoch-14):
  - damage order, the mana keep, card naming, modes, targets, cost picks and empty choices no longer
    fall back to it;
  - nor do the twenty choices it used to make outright: Fact or Fiction piles, opening-hand abilities,
    colours, counter kinds, replacement order, edicts, clash, combat taxes and others.
  - Delve is asked the same way: in the real payment, how many cards (any number up to the generic part)
    and which; an affordability test counts every card, unasked. So are an "up to" cost reduction (Mana
    Matrix: the whole reduction while pricing; asked inside the menu it re-entered the snapshot and overflowed,
    #225) and an emerge or offering sacrifice. The least delve stated is the engine's own test payment of the rest
    (an estimate let Become Immense delve none and fail to pay; harness-epoch-45).
  - A damage assignment order already declared (legacy ordering, `overrideOrder` false) is followed, not asked
    again (#224); a free split is the seat's.
  - Convoke and improvise, retargeting, redirection and mana from the pool are the seat's too. A convoke
    choice asked from an affordability test is answered as "every helper could pay" without asking; the
    engine does not say which calls are tests, so the seat's real payment marks its own window.
  - An effect played without the stack (an opening-hand ability, a replacement effect, a trigger that
    skips the stack) gets the seat's modes and targets, and its cost is paid with the seat's cost
    decision: the engine routine hard-codes Forge's AI there, so the harness runs a copy of it.
  - A spell or copy with several targets is retargeted as a set: the seat picks up to as many as it had,
    and none, or a set the spell cannot have, keeps the old one. A divided spell keeps its portions, as
    the engine does for a human: exactly as many new targets, each taking an old portion the seat assigns.
  - The colour an any-colour source makes for a generic shard during payment is the seat's through the
    first engine patch (`ComputerUtilMana`); a coloured shard forces its colour.
  - Whether to reveal a companion is the seat's (an optional choice); a declined companion is no companion, through
    the second engine patch (`Player.assignCompanion` crashed on it).
  - The resolve line of a state-dependent pump (+X where X is the target's power, e.g. Berserk) reads the
    target's power before the effect applies, through the third engine patch (MagicStack built the GAME
    HISTORY's resolve line after the pump and made it contradict the stack preview the seat was shown: g06
    seed 4106 T7 showed "+5/+0" and "+10/+0" in the same turn's consecutive prompts).
  - **Mechanics no deck in the pool reaches:** dice, stickers, contraptions.
- **Mana payment:** the engine maps the chosen sources to the cost. The seat decides which sources stay
  untapped when it matters (`keep_untapped`, or the mana-sources ask). The planner can be more hopeful than the
  real payment: when the engine cannot pay around the kept sources, the hold is released and the cost paid again,
  recorded as an auto row (harness-epoch-45). A cancelled modal spell drops its modes, so the next cast asks again.
- **The Claude CLI lane** answers in text, because it has no function calling.
