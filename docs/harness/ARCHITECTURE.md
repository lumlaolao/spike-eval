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
 Forge rules engine (pinned commit; four marked patches as diffs, ENGINE-PATCHES.md)
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
- *Engine patches (owner, 2026-10-15), only where the seat has no hook:* a unified diff against the upstream
  file at `FORGE_COMMIT`, in `engine-patches/<path>.patch` (Forge is GPL: the repository carries our changes,
  not Forge's files), every change marked `HARNESS PATCH`, the upstream original's sha256 recorded in
  `docs/harness/ENGINE-PATCHES.md`. `eval/engine_patches.py` applies each to the original read from git at the
  pin wherever a build needs it (ENGINE-PATCHES.md, "How a patch reaches the game"); a patch that does not apply
  stops that build, and `eval/tests/test_engine_patches.py` also fails on a moved original, so a re-pin forces a
  re-diff. `overlay_sha` hashes each patch as the file it
  makes. Why: "truly let agents have control" (owner) where no `PlayerController` call exists. Four files are
  patched (the table in `ENGINE-PATCHES.md`): `ComputerUtilMana` (the colour an any-colour source makes for a generic
  shard, a lethal-tap source, a card paying for its own ability), `Player.assignCompanion` (a declined companion is no
  companion rather than a crash), `MagicStack` (a resolve line read before the effect) and `GameCopier` (a card no
  copied zone holds).

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
- A play is not offered when a part of it that must target has no legal target: casting it only fails ("Couldn't add
  to stack, failed to target"). That is legality, not judgment: the engine refuses the play. A sub-ability counts too
  (Govern the Guildless's forecast targets in its sub-ability and was offered with no creature in play;
  harness-epoch-77), but only when no earlier part targets: its targets may depend on that one (Searing Blaze's
  "creature that player controls", Goblin Welder), unknown before the cast, so it is left to the cast
  (`aSubAbilityTargetThatDependsOnTheFirstTargetDoesNotHideThePlay`).
- **The one exception is a measurement arm:** under `PROPOSE_ACTIONS=1` the play menu is built as always but not
  shown; the seat proposes its play, its attack and its blocks, and the engine checks them (section 6, "The propose
  condition"). Nothing legal is refused there either: a proposal is accepted exactly when the menu's own checks allow it.
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
- Each block of a set is judged with the set's other blocks in place, the cap first (`firstBlockNotAllowed`; see "A
  block is judged beside the others" in section 6, "The propose condition").
- A block set is checked as a whole (`blocksError`), by the engine's `CombatUtil.validateBlocks`: requirements ("all
  creatures able to block it do so") and restrictions (menace). Forge runs that check only for its own GUI, so before
  epoch 52 any block set the seat or an arm declared stood. `validateBlocks` never counts blockers against a global
  cap (Silent Arbiter, Caverns of Despair), so `blocksError` checks the cap too. Its requirement test also ignores
  the cap (it refused every set under Silent Arbiter and Prized Unicorn), so at the cap an objection naming a creature
  that is not blocking is dropped only when every blocker in the set blocks an attacker it has a requirement toward
  (no swap obeys more requirements, CR 509.1c, as near as one requirement per blocker can tell) and the restriction
  half of `validateBlocks` ("can't block alone", menace), which that objection hid, holds; otherwise it stands. The
  `answer` call is corrected in the same conversation; a text reply is asked once more with the engine's reason; an
  arm draws again (a legal first draw keeps its stream). When the seat's blocks are still illegal after that, or its
  answer is lost (two invalid replies, no call, no reply, an error), the fixed fallback is no blocks unless a
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
- a declined optional search is followed by the engine's "Cancel Search?", and a NO asks the same pick again, for
  as long as the pair repeats (`ChangeZoneEffect`). The NO is heard once: the pick asked again says that declining
  again ends the search (under every context profile, legacy included, as the auto YES applies to all of them), and
  a second decline answers the next "Cancel Search?" YES, as an auto row. That YES carries out the seat's own
  second decline, made after it was told what that decline does. Only the confirm that follows a declined pick
  counts: an Optional search's "Search your library?" has the same confirm mode and is not a "Cancel Search?"
  (harness-epoch-72, issue #312: the grader's `costs.x`, Green Sun's Zenith, looped 61 asks with no outcome).

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

Each call is validated against the decision it answers. A reply's answer is its first `answer` call that
validates, so an incomplete call before a legal one does no harm (answer-tool-v12, harness-epoch-73). A reply
whose every `answer` call is illegal gets the reasons and one more round in the same conversation; a second
such reply loses the decision. An illegal call before the answer is not merged into it: its fields are not used,
and its result says so. A play decision on a tool ask reads nothing from the reply's text.
- *Why the first valid call:* in the frontier5 run a model's analysis call before its legal move cost it a decision
  and a winnable board ([FRONTIER5.md](FRONTIER5.md), "Decisions the harness lost").

A tool ask's decision text comes from the question builders the text lane and the ai-teacher seat share.
`AnswerTool.rephrase` removes their text answer line ("Reason briefly, then end with EXACTLY: ..."), and the
ask's one answer line is the `ANSWER:` line appended after it; no reasoning cap is left in (answer-tool-v11).
A text format that survives is logged as `[TEXT-FORMAT-LEAK]` and fails acceptance.
- *Why a call:* text parsing is where most harness defects came from. Models wrote `CHOICE` and `PICKS`
  together, put the answer mid-prose, or wrote tool markup as text. A schema makes the answer structured,
  and validation makes a wrong one explainable.
- *Why one fixed schema instead of one per decision:* tool definitions open the cache prefix. A schema
  that changed every decision discarded the whole cache on every call. A per-decision enum is kept as a
  measurement arm (`ANSWER_SCHEMA=per-decision`), reachable only from a direct JVM launch: a batch pins the default
  (`run_batch.CONDITION_ENV_KEYS`).
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
  for the `ANSWER_TOOL=0` measurement arm (the Claude CLI text lane that used it is retired).
- **The one exception (owner, 2026-10-24):** a decision model (`jev:`) cannot call anything. Its typed answer is
  converted into the `answer` call before validation, so everything downstream is unchanged (section 9, "The
  Decisions bridge").
- **The Claude CLI seat (`claude-cli:`, owner, 2026-10-24, a development lane)** answers by a call too: Claude Code's forced
  structured-output call, whose schema is the ask's own `answer` and `set_plan`, becomes those calls before validation
  (section 9, "The Claude CLI answer bridge").

### The propose condition (`PROPOSE_ACTIONS=1`, harness-epoch-74, targets epoch 75, costs epoch 76, combat epoch 78, owner 2026-11-03)

The menu makes illegal plays impossible, so the benchmark cannot see whether a model knows the rules. This arm
removes the menu from the play decision and treats the rules engine as a type checker: the seat proposes a play,
the engine checks it, and a rejected proposal comes back with the rule it breaks so the seat can repair it.
- **What the seat answers:** `answer.action`, a fixed-schema object: `verb` (play_land, cast, activate, pass), `card`
  (a handle), `ability` (its number in YOUR ACTIVATED ABILITIES, which lists special actions such as suspend and
  foretell too), `face`, `cost` (another way to cast it, by the card's name for it, "Prototype", "alternative", or
  "flash" for a cast a card allows when it could not otherwise be cast), `targets` (a handle, "you" or "opponent").
  Zero text parsing still holds: it is a tool call.
- **The menu is the oracle** (`matchProposal`): the menu is enumerated exactly as for a menu seat; a proposal is
  accepted when it names one of its plays, and that play is carried out the usual way (targets, modes, X and costs
  are the usual follow-up asks). Soundness is the menu's: `ProposeActionsTest.everyMenuPlayIsReachableByAProposal`
  builds every menu play's proposal and checks it lands on that play.
- **A rejection names the rule, never the alternatives** (`proposalRejection`): "ILLEGAL (category): one sentence",
  the first engine check the play fails, in the categories timing, cost, zone, target, land drop, control, type and
  restriction. Kaggle Game Arena says only "illegal"; a list of what would be legal is a hidden menu. A malformed
  proposal (an unknown handle, ability 3 of a card with two, `option` instead of `action`) is a `FORMAT:` error,
  recorded apart.
- **The terse arm** (`PROPOSE_TERSE=1`, with the propose arm only; harness-epoch-80, answer-tool-v17): a rules
  refusal says only "ILLEGAL: that play is not legal now." (an attack or blocks: "that declaration";
  `AnswerTool.shownRefusal`), Game Arena's form, and the preamble and the `action` schema say the seat is told only
  that it is illegal. It measures the message's share of repair
  (Gumaan 2026: a failure message can make a repeat more likely). The substitution is made where the refusal is sent
  (`AnswerTool.converse`), so the check, the categories and the four tries are the detailed arm's. A `FORMAT:` error
  keeps its text: it says what is malformed, not which rule applies. The row keeps the engine's diagnosis in
  `error` and `category`, and what the seat was told in `error_shown`, so both arms count the same rules
  (`proposal_audit.py`'s `repair` and `refusal_text`). Stamped `propose_terse` on the game line and puzzle row and
  `answer-tool-v17+propose+terse`; keyed by `aggregate.PROVENANCE_FIELDS` and `puzzle_score.BRIDGE_CONDITIONS`;
  pinned by `run_batch.py --propose-terse` and `puzzle_solve.py --propose-terse`, each refused without
  `--propose-actions`. The preamble's general ANSWERS sentence ("you are told why") is the same in both arms: it is
  about answers to listed decisions, and the b2 block was played with it. Results: the research note's section "Does
  the refusal's reason matter? The terse arm".
- **Four tries** (`AnswerTool.MAX_INVALID_PROPOSALS`), then the fixed legal option, PASS. A decision lost to four
  refusals is the model's error, not a lost answer: `match_review` lists it as PROPOSAL and the harness-decided gate
  does not count it (`proposal_audit.lost_to_refusals`, one rule for both: `answer_via` lost after four invalid
  replies); lost any other way it is LOST. Two
  elsewhere. Repair gains level off after two to five rounds of checker feedback (COPRA, Goedel-Prover-V2).
- **What the state hides and adds:** the hand's "(castable)" tags are gone (they run the menu's own legality check);
  YOUR ACTIVATED ABILITIES lists, numbered, what each of the seat's cards can activate, as state, not legality. The
  guide (`SeatObservation.guide()`) says so. Stamped `observation-faithful-v42+propose`, `answer-tool-v17+propose`.
- **The same windows are asked:** the window skips are the menu's (a window whose only play is PASS is not asked), so
  the two arms see the same spots. That says one thing a menu-less player would not know: when the seat is asked,
  something other than passing is legal. *Why accepted:* asking every priority window would multiply the calls
  several times and make the arms incomparable.
- **No preview:** `simulate` takes menu numbers, so it is not offered; `run_batch.py` refuses `--propose-actions` with
  `--lookahead`, with `--plan-execute` (its steps are menu numbers), with `--text-answers` and outside `--exploratory`.
- **Costs (epoch 76):** `cost` names another way to cast a card, matched by its exact name ("flash" is not
  Flashback). An optional additional cost (Kicker, buyback, entwine) or "normal" names the normal cast, whose cost
  ask follows as under the menu: refusing it would count a legal cast as a rules error (pr-review of #342). A cast
  with no `cost` is the normal cast only: when the menu holds just another way (Evoke, a pitch spell, Flashback from
  the graveyard), the seat is told to name it; the harness never picks the way to cast for it.
- **Forecast** (rule 702.57) is an ability parameter, not one of Forge's keywords, so the keyword sweep never drew it:
  activated from the hand in its owner's upkeep only, it is on the upkeep menu, in YOUR ACTIVATED ABILITIES from the
  hand, and refused by rule outside the upkeep (`aForecastAbilityIsOfferedAndProposableOnlyInTheUpkeep`).
- **The record:** each play row carries `proposals` (every call, its verdict and category) and `first_try_legal` (the
  first well-formed proposal was legal); the game line `propose_actions`, a puzzle row `propose_actions: true`.
  `eval/proposal_audit.py` reads them. Its headline is first-try legality **of non-pass proposals**: a pass is
  always legal, so the all-proposals rate scores 1.0 for a seat that only passes (the 88% lesson). It prints a strict
  variant beside it (a malformed first try that names a play counted as not legal) and a Wilson interval on each.
- **Targets (epoch 75):** a play with one targeting step and one target, among cards and players, names it in
  `targets`; it is checked with the play (`proposalTarget`) and set at the target step without a list. A wrong one is
  "ILLEGAL (target)" with the static ability that forbids it (hexproof, shroud, protection: Forge's
  `StaticAbilityCantTarget.cantTarget`), its zone, or "not something it can target". The proposed target is consumed
  by that play's target step only (`ensureTargetsSet`); a later trigger's target is always asked.
- **The checker audit** (`PROPOSE_AUDIT=1`, diagnostic): at every play decision each menu play is proposed as the
  guide describes and must land on itself. Over the keyword gauntlet (552 cards, both answer policies) it checked
  21,493 plays; the misses it found (suspend and foretell unlisted, prototype and flash casts unnamed) are fixed, and
  the rest were one may-play cast listed twice, the same play.
  It restores the pending proposed target it touches (before, a targeted play audited last could pre-set the seat's
  next target unasked), but it registers handles early, so an audited game's prompts can differ: it is a condition,
  stamped `propose_audit` on the game line and puzzle row and never pooled with a plain run. `run_batch.py` pins it
  off and the puzzle runner strips it: it runs only in the keyword gauntlet.
- **The static rules probe's engine labeller** (`RulesProbeLabelExport`, diagnostic, never a score): the same checker
  labels every proposal the solver could make on a built `.pzl` board and on one-line near-miss variants of it, with
  no game and no model; the label is the checker's (null legal, "ILLEGAL (category)" its category and sentence), so
  it inherits that checker's defects. What it proposes, what it drops (FORMAT, `SUBSTITUTED`), how to run it and why
  in shards: its class comment. Always-on tests: `RulesProbeLabelTest`. Items, floors, contrast pairs and audit
  samples: `eval/rules_probe.py` (`engine`, `engine-report`, `audit-sample`). Results:
  `eval/research/2026-11-05-rules-probe.md`, section 9.
- **Side-effect settings are conditions too.** The shadow labeller (`SHADOW_AI`, `run_batch.py --shadow-ai`) asks
  Forge's AI on the side for a DAgger label; its targets are reset before the seat's cast, but an AI timeout weakens
  the game's isolation (`[SHADOW] ... WEAKENED`), so it is stamped `shadow_ai` on the game line, keyed by
  `aggregate.PROVENANCE_FIELDS`, expected on resume, and stripped from puzzle games (PR #345; STANDARDS section 4).
  The puzzle runner strips every condition `run_batch.py` pins (`CONDITION_ENV_KEYS`) and `SHADOW_AI` from the
  shell, so a puzzle game runs only the condition `puzzle_solve.py` sets for it; the tournament (`tournament.py`)
  strips the same list, so a bracket game runs at the JVM's defaults (`eval/tests/test_launchers.py` lists every
  launcher and how its games get their conditions).
- **Combat (epoch 78, answer-tool-v16):** the attack and the blocks are proposed too, so the candidate lists (which
  creatures can attack, which can block, and what) are not shown. `answer.declare_attackers` is a list of
  `{attacker, defender}` by handle (`defender` "opponent" by default, or a planeswalker's or battle's handle; the
  attack-target ask is not made); `answer.declare_blockers` a list of `{blocker, attacker}` by handle; an empty list
  attacks or blocks with none. Both properties are in the schema under the arm only, beside `action`.
  - *The verdict is the menu's own:* an attacker is checked with `CombatUtil.canAttack(creature, defender)`, the
    attack as a whole with `CombatUtil.validateAttackers` (global limits, alone/with-others restrictions, requirements)
    on a scratch combat; each block with `CombatUtil.canBlock(attacker, blocker, combat)` with the set's other blocks
    in place (`firstBlockNotAllowed`), and the set with `blocksError` (the requirement and restriction halves of
    `validateBlocks` and the global block cap) (`attackProposalError`, `blockProposalError`). The sentence only says
    which rule failed; a failure to word it is a generic restriction, never a changed verdict.
  - *A block is judged beside the others:* `canBlock` refuses a block while some requirement the blocker could obey
    looks unmet, so judged on a combat with no blocks yet, Hill Giant blocking Grizzly Bears beside Wall of Stone
    blocking Gaea's Protector ("must be blocked if able") was refused, though the Wall obeys the requirement (CR
    509.1c; pr-review of #349). The menu's tool check had the same flaw and uses the same rule now
    (`theMenuAcceptsABlockBesideAMustBeBlockedAttacker`), and so does the text grammar's `BLOCKS:` line, which added
    its pairs one by one, so the order they were written in decided (epoch 79,
    `theTextBlocksAreJudgedAsASetInEitherOrder`).
  - *Categories the checker names:* summoning sickness, tapped, defender, evasion (flying without flying or reach,
    any keyword static such as shadow or landwalk, "can't be blocked"), number (menace and other minimum or maximum
    blockers, Silent Arbiter's caps, a creature blocking more attackers than it may), requirement (a creature that
    attacks or blocks each combat if able, lure, goad, must-block), restriction (Ensnaring Bridge and every other
    "can't attack" or "can't block" static, named with its source; can't attack or block alone), and zone, control,
    type and combat (an attacker that is not attacking, a defender that cannot be attacked). Read from Forge's own
    statics (`StaticAbilityCantAttackBlock`: Defender and Flying are keyword statics there) and its
    `AttackConstraints`.
  - *Four tries, then the menu's fallback:* no attack (or, when attacking with none breaks a requirement, the
    engine's legal declaration, `makeAttackLegal`, as on the menu), and the fixed block fallback (`fallbackBlocks`:
    none, or the smallest legal set under a requirement). Never Forge's AI.
  - *The record:* the attackers and blockers rows carry `proposals` (each entry under `declare_attackers` or
    `declare_blockers`) and `first_try_legal`, by the play's rule; their `raw_reply` ends in the menu's PICKS / BLOCKS
    line over the hidden lists. `proposal_audit.py` gives each its own sheet (`attack`, `block`), never pooled with
    the play headline; an empty declaration is the combat pass there, so the combat headline is first-try legality
    of non-empty declarations.
  - *The same windows are asked:* as for plays, the seat is asked to attack only when some creature can attack and to
    block only when some creature can block (the menu's skips), which says that much.
- **Not yet hidden:** the targets of a play with modes, several targets or divided amounts, a spell on the stack as a
  target, the cost choices, the members of a band (asked as a list after a banding creature attacks) and the order of
  damage assignment keep their candidate lists (each can be checked with `sa.canTarget` and
  `AttackingBand.isValidBand`).

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
  (a multi-step line in one train of thought) and starts over each turn; `ONE_CHAT=1` keeps the whole game
  (`ONE_CHAT_TURNS=N` keeps the last N pairs under either, stamped `one_chat_version` `+turns=N` when a chat is on;
  every launcher pins it to its default).
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
    output tokens in 352 s, against a median of 253 in 9 s; after latency routing a game's 153 calls had p50
    6.7 s and max 28 s, `GAME-LOOP-LOG.md`, seed 86 of 2026-10-15). The time was the endpoint's, not the model's
    thinking. OpenRouter's default routing weighs price; the model is the same on every endpoint.
  - a model whose reasoning cannot be turned off (`reasoning.mandatory` on OpenRouter's model list, 2026-10-24; the
    list is `MANDATORY_REASONING_FLOOR` in `LlmSpikeGui.java`, hashed into `model_cfg_sha`) is never sent
    `enabled:false`, which it refuses: wherever "none" would be sent (the fast tier, the truncation retry) it gets its
    lowest effort, and its truncation retry keeps the whole token budget, since it still reasons.
    `eval/tool_use_suite.py` sends the same reasoning field wherever "none" is sent, and its floored retry keeps its
    budget (its copy of the table is pinned to the Java map by its test); its budgets are its own.
  - prices: `eval/pricing/prices.json` holds each model's list price, which reports use, and under `max` its dearest
    endpoint that takes tools, which `--budget-usd` checks against (latency routing may land on a priority tier).
    `puzzle_solve.py --max-price-at-list` rules that out: it sets `OPENROUTER_MAX_PRICE` to the list price, and the
    bridge adds it to its one `provider` object as `max_price`, its fields strings as OpenRouter's schema types them
    (`OpenRouterBridge.maxPriceField`). It is a condition, not mere routing: at list an open-weight model may find only
    a 4-bit host, so the game line stamps `openrouter_max_price`, keyed in `aggregate.PROVENANCE_FIELDS` with default ""
    (the provider sort, which only orders endpoints, stays in `NON_BEHAVIORAL_PROVENANCE`). The file's `comment` names
    the models with a long-context tier, where each starts, and which `max` counts one, with the largest prompt recorded
    in one call.
  - precision: `puzzle_solve.py --quantizations fp8,bf16` sets `OPENROUTER_QUANTIZATIONS`, and the bridge adds
    `quantizations` to the same object (`OpenRouterBridge.quantizationsField`), so OpenRouter serves the model only
    at those precisions; an endpoint whose precision is not stated (`unknown`) is not served unless listed. An
    open-weight model is run at its maker's released precision (owner, 2026-10-26: never a host that compresses it
    further). That is a condition, not routing: the game line stamps `openrouter_quantizations`, keyed in
    `aggregate.PROVENANCE_FIELDS` with default "" (a game from before the filter was unfiltered).
  - both filters: an unreadable value is refused, never dropped; a puzzle resume refuses another value; each puzzle row
    names the filters it ran under (`openrouter_max_price`, `openrouter_quantizations`), and `puzzle_score.py` never
    pools rows under different ones (`BRIDGE_CONDITIONS`); a batch sends neither (it scrubs an ambient value,
    `RUN_DEFINING_KEYS`, and refuses one in `--env`). Under either, a 404, a 5xx, or a 400 naming the filter's fields
    (not OpenRouter's relayed "Provider returned error", a request error) is logged `[ROUTING-FILTER]` with its status
    and keeps native tools: it can mean no endpoint the filter allows, or the filter's shape refused (OpenRouter's reply
    to such an exclusion is not recorded). The puzzle row counts the decisions that cost after every retry
    (`routing_refusals`; `routing_http_refusals` counts the refusals themselves, recovered ones included).
    `puzzle_score.py` refuses a row with a lost decision rather than score it as the model's, and `--resume` replays it;
    one the seat's retry recovered, any other 400, a 429 or a timeout is scored, as without a filter.
  - a stalled connection is retried (`HttpDeadline`, `OPENROUTER_IDLE_MS`, default 90 s, 0 off): no headers, or
    bytes and then silence, for that long, inside the ask's deadline.
  - *Why:* in the C3 combo games some connections sent nothing for minutes and held the ask to its full
    timeout, 15-25 minutes a game; a fresh connection answers in seconds.
- **The bare-name CLI text lane is retired** (owner, 2026-10-25). A bare Claude name (`opus`, `haiku`) as a model seat
  is refused, saying to use `claude-cli:<model>` with `CLAUDE_CLI_SEAT=1` (`LLMBridgeFactory.create` and
  `requireCliOptIn`, `run_batch.py`, `puzzle_solve.py`, `run_suite.py`). It answered in text, which the owner's
  tool-call rule forbids for a backend that can call. It also inherited the whole shell, and it was unstamped and
  unpriced. A recorded game line that names one still reads (`cost_report.is_cli_lane`), and `panel_score.py` refuses it.
- **No default model.** A model seat (controller `llm`) whose tier names no model (`SMART_MODEL_X` / `FAST_MODEL_X`,
  else `SMART_MODEL` / `FAST_MODEL`) is refused when the game is built (`LlmSpikeGui.missingModel`); it used to fall to
  the CLI's `opus` and `haiku`. A seat that asks no model (floor, random, the puzzle arms) gets an inert bridge on both
  tiers whatever they name (`LLMBridgeFactory.placeholder`): it starts nothing, and if it is ever asked it logs
  `[PLACEHOLDER-ASKED]`, which `eval/gauntlet_scan.py` counts as a defect. A spec that names a "cli:" lane but is not `claude-cli:<model>` is refused
  (`LLMBridgeFactory.create`).
- **`ClaudeCliBridge`** (`claude-cli:<model>` specs): a Claude model through Claude Code in print mode, answering by
  structured output; a development lane, used only with `CLAUDE_CLI_SEAT=1` and never in a benchmark score. See "The
  Claude CLI answer bridge" below.
- **`DecisionsBridge`** (`jev:<model>` specs, first TypeSafe's Jev 1.14): a decision model on OpenRouter's Decisions
  API. See "The Decisions bridge" below.
- **Egress boundary:** an `openrouter:` model receives the game state. Nothing starts unless
  `OPENROUTER_ALLOW_EGRESS=1` is set, either in the person's own shell or in `secrets.env`, which `setup.sh` writes it
  into only when they answer yes. No tool sets it on its own; `play-vs-llm.sh` only reads it. A `claude-cli:` seat's consent is its own
  opt-in (see "The Claude CLI answer bridge").
- **The game model:** GPT-6 Luna (`openrouter:openai/gpt-6-luna`) for every game unless the owner names
  another, one tier (every decision reasoning); what a game costs is in `CLAUDE.md` ("Cost of paid runs").
- *Why OpenRouter:* one API for many providers, so a model comparison changes one string.
- *Why the cache matters:* nearly all input is the same system prompt, tools and history. Recorded Luna
  asks read 54% of their prompt tokens from the cache on an ask's first round, and 92% on later rounds (the four
  Luna games of `runs/prompt-ab/new`, 2026-10-15).

### The run record (`Transcript`)

- **What:** with `TRANSCRIPTS=1` and `EVAL_OUT`, every HTTP round-trip `OpenRouterBridge` and `DecisionsBridge` make
  goes through `Transcript.send`, which writes the exact request body before the call and the exact response body
  (or why none came) after it; a puzzle game's whole Forge game log is kept too. The files, their rows and how a run
  is archived: `docs/eval/PUZZLES.md`, "Keeping a run". `puzzle_solve.py` always turns it on, refuses a build without it,
  and copies the `.pzl` and the suite into the run; `play-vs-llm.sh` turns it on too; `eval/archive_run.py` checks, packs and verifies a finished run.
- *Why:* the owner wants the frontier puzzle run kept whole, "from the models' chats to the engine logs"
  (2026-10-25). The rows held only what the seat rendered: a `context_call` row's `prompt` is the new user turn
  (with `messages` only on a later round), and no row held the system prompt, the tools, `tool_choice`, the
  sampling and reasoning settings, the `provider` object, `reasoning_details`, the generation id, the served model
  or the provider's cost; the truncated first completion of a retry and the body of a refusal (cut to 90
  characters in `jvm.log`) were lost; a `jev:` call's questions and Jev's answer probabilities were not kept; the
  engine log was text at the end of `jvm.log`, its multi-line entries split; the position was a path and a hash.
- **Not a condition:** the bridges send the same bytes with it on or off (`TranscriptTest`), so it moves no epoch
  and is not stamped on the game line; `Transcript.VERSION` (`transcript-v1`) is on the puzzle row and manifest.
- **Never cut, never a secret, never in the way:** each row is one append of its whole bytes, and a failed append
  moves the record to a fresh file (`[TRANSCRIPT] FAILED`, `transcript_errors`); the request is on disk before it is
  sent, so a JVM that dies mid-call keeps it; any recording failure is caught inside `Transcript`, so it never costs
  a reply; a row holding the API key's value is refused. Not covered: the Claude CLI lane, the body of a response
  cut off by its deadline (its status and headers are kept, `HttpDeadline.LAST_INFO`), and a response body that was
  not valid UTF-8 (the bridges read bodies as text).

### The Decisions bridge (`jev:` seats)

**Owner decision, 2026-10-24:** TypeSafe's Jev 1.14 joins the frontier comparison on the hardest puzzles
(`eval/puzzles/puzzle_solve.py`). Jev is not a chat model: it is served only on OpenRouter's Decisions API
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
  "none", and a per-identity player field in `rating.py`, so the opponent's condition is unchanged), and on the puzzle
  row of a solver with a `jev:` tier (smart or fast, as on the game line), where `puzzle_score.py` refuses to pool two
  versions. Acceptance pins the literal from a loopback answer in the javatests log, and checks that a game with no
  `jev:` seat carries no such key. No existing seat's prompt or answer path changed, so no other stamp and not
  `HARNESS_EPOCH` moved.

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

### The Claude CLI answer bridge (`claude-cli:` seats)

**Owner decisions, 2026-10-24:** Claude Opus 5.5 may play a seat through the Claude Code CLI in print mode
(`claude-cli:claude-opus-5-5`), so it can be tested "with harnesses". This is a **development lane** on the owner's
own Claude subscription:
- **used only when the operator chooses it**: `CLAUDE_CLI_SEAT=1`. The JVM (`LLMBridgeFactory.claudeCli`, and
  `requireCliOptIn` where a model seat is built: `buildPlayers`, the seat's bridges), `run_batch.py` and
  `puzzle_solve.py` refuse a `claude-cli:` model seat without it, and say what the lane is for
  (`eval/cost_report.py` `cli_opt_in_refusal`). No tool sets it;
- **never part of a benchmark score**: `run_batch.py`'s cited mode and `--panel` refuse it. `panel_score.py` refuses
  a cell where such a seat played. The game line and the puzzle row carry `dev_lane: "claude-cli"`, so such a game
  never pools with a benchmark game (`aggregate.PROVENANCE_FIELDS`, an environment field in `rating.py`), and
  `puzzle_score.py` heads its report "DEVELOPMENT LANE ... not a benchmark score".

`CLAUDE.md` names this exception under "Owner rules"; its default game model is unchanged. The lane answers under the
owner rule "every decision is answered by a tool call". `claude -p --json-schema <schema>` makes Claude Code end the
model's turn with a forced structured-output tool call. Claude Code validates that call's input against the schema
and returns it as `structured_output`. No text is parsed.

- **The schema** is built from the ask's own tool list (`ClaudeCliBridge.schema`), never copied:
  - an object whose required `answer` property is the `answer` function's parameters and description;
  - and whose optional `set_plan` property is `set_plan`'s.
  - These are the fixed definitions every `openrouter:` seat gets (`AnswerTool.fixedDefinition`, `setPlanDefinition`),
    so the schema is the same on every ask of a game.
- **The answer:** `structured_output.answer` becomes the `answer` call and `structured_output.set_plan` the `set_plan`
  call (`ClaudeCliBridge.turn`). `AnswerTool.converse` validates them, so everything after is as for any tool seat:
  - an illegal answer gets its one correction;
  - a result with no `answer` is reminded `AnswerTool.MAX_NUDGES` times;
  - a lost decision takes the fixed legal option;
  - every row and stamp is written the same way.

  The `result` text the CLI returns beside it is kept as the turn's content (the row's reasoning) and is never read
  for the answer.
- **One process per model turn.**
  - *Why:* each decision is rendered fresh (section 8), so no conversation is kept across decisions. A persistent CLI
    session would add one that the other seats do not have.
  - A correction or reminder round is a new process. Its stdin carries the decision, then each earlier turn of that
    decision as text ("YOUR EARLIER REPLY TO THIS DECISION", the answer it gave, "THE HARNESS REPLIED: ...").
  - Not determined: whether a persistent `--input-format stream-json` session returns `structured_output` on every
    `result` event. It was not probed without a real model call.
- **The argv** (`ClaudeCliBridge.argv`): `claude -p --model <model> --output-format json --json-schema <schema>
  --system-prompt-file <file> --tools "" --strict-mcp-config --no-session-persistence --safe-mode --setting-sources ""
  --disable-slash-commands [--effort <level>]`, with the decision on stdin.
  - The seat's own system prompt replaces Claude Code's, so the condition is this harness, not Claude Code's coding
    prompt.
  - `--bare` is not used: it accepts only `ANTHROPIC_API_KEY` auth, and the owner's CLI logs in by OAuth.
  - That argv was probed on CLI 2.1.310 with `HTTPS_PROXY` and `ANTHROPIC_BASE_URL` pointed at a closed loopback port,
    so no request left the machine. Its `init` event listed `StructuredOutput` as the only tool, and no MCP server,
    skill or slash command.
- **Isolation of the process:**
  - It runs in an empty temp directory, and no key is put on its command line.
  - Its environment is an allow-list (`ClaudeCliBridge.KEEP_ENV`, `childEnv`). It holds what the CLI needs to run,
    reach the network and log in to the owner's subscription: PATH, HOME, locale, proxy and CA variables,
    `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CONFIG_DIR`. A Bedrock / Vertex / Foundry switch is kept
    with that provider's credentials only (on when set to "1" or "true"; Vertex's `VERTEX_REGION_*` included); `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` only when the operator sets
    `CLAUDE_CLI_ALLOW_API_KEY=1`. The log says once per bridge which login the child had (`loginSource`).
  - Everything else is dropped. That includes `MAX_THINKING_TOKENS`, `ANTHROPIC_MODEL` and its per-tier defaults, the
    launching session's `CLAUDE*` variables and the harness's `OPENROUTER*` ones. None of the kept variables chooses
    the model, its thinking or its effort.
- **Settings the seat needs are set, never inherited.** Each is pinned by `run_batch.py` and `puzzle_solve.py` (both
  tiers; unset is the CLI's own default, recorded as "cli-default"), stamped, and refused on a resume or a pool that
  mixes values:
  - effort: `--claude-cli-effort` → `CLAUDE_BRIDGE_EFFORT` → the CLI's `--effort`. It is stamped `claude_effort_smart`
    / `_fast`, and `claude_cli_effort` on the puzzle row.
  - thinking budget: `--claude-cli-thinking-tokens` → `CLAUDE_CLI_THINKING_TOKENS` → the child's `MAX_THINKING_TOKENS`.
    It is stamped `claude_cli_thinking_tokens_a` / `_b` on the game line, and `claude_cli_thinking_tokens` on the puzzle
    row.
- **Deadline:** `CLAUDE_DEADLINE_MS`, else the model's `ModelCfg` timeout (`timeoutMs`, the same for both tiers on this
  lane). After it the process and its children are killed. The prompt is written to stdin on its own thread, so a
  process that never reads it cannot hold the call past its deadline.
- **Failure:** any of these gives no answer (`[CLAUDE-CLI-NO-ANSWER]` in the log, with the tail of the process's
  stderr): a non-zero exit, an `is_error` result, output that is no result, or the deadline. The seat's bridge retry
  runs, then the caller takes its fixed legal option, never Forge's AI.
- **Bill:** the CLI's `total_cost_usd` (which includes its own side calls to a smaller model) is summed over an ask's
  attempts. A billed attempt that failed and the retry that answered both count, and that sum is the `context_call`
  row's `cost_usd`.
  - `eval/cost_report.py` prices a `claude-cli:` seat by the sum of those rows, at list price and budget alike.
  - When a token-priced seat shares the name, its rows (those with no `cost_usd`) are priced from their tokens and the
    two spends add.
  - A process killed at its deadline reports nothing.
  - `usage` gives the prompt, cached, cache-write and completion tokens. The served model is the `modelUsage` key with
    the most output tokens.
- **Version:** `ClaudeCliBridge.VERSION` (`claude-cli-bridge-v2`). It is stamped per identity on the game line,
  `claude_cli_bridge_version_a` / `_b`, only for a `claude-cli:` seat (`aggregate.PROVENANCE_FIELDS`, default "none";
  per-identity player fields in `rating.py`). It is also on a `claude-cli:` solver's puzzle row, where `puzzle_score.py`
  refuses to pool two versions, efforts or thinking budgets.
  - The spec names the lane, so it never pools with a bare `opus` or an `openrouter:` Opus.
  - No existing seat's prompt or answer path changed, so no other stamp moved, and neither did `HARNESS_EPOCH`.

What it cannot do in phase 1, by construction (refused when built, and by `run_batch.py` and `puzzle_solve.py` before
anything runs):
- **No lookups, no `simulate`:** `MEMORY_TOOLS=1` or `LOOKAHEAD=1` is refused. Phase 2 would expose `NativeTools`
  through an MCP stdio server the CLI calls (`--mcp-config`). That needs the seat's lookup executor to be reachable from
  a separate process.
- **No text answer:** `ANSWER_TOOL=0` or `NATIVE_TOOLS=0` is refused. So is a kept conversation (`ONE_CHAT`,
  `TURN_CHAT`), which wraps only an OpenRouter bridge.
- **Parallel CLIs:** `puzzle_solve.py` requires one JVM at a time for it (`-j 1`); `run_batch.py` warns above `--jobs 1`.

**Egress:** the lane sends the game state to Anthropic under the operator's own CLI login, not to openrouter.ai.
`OPENROUTER_ALLOW_EGRESS` is consent for openrouter.ai and is not asked for. The lane's own consent is its explicit
opt-in, `CLAUDE_CLI_SEAT=1` (owner, 2026-10-24).

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
| `human` | a person, on Forge's own human controller and match screen (`play-vs-llm.sh`). Only the GUI match seats one (`LlmSpikeGui.buildPlayers(true)`); `buildPlayers()` (the headless and fork runners) and `seatFor` (the puzzle runner's seats) refuse it, and `run_batch.py`'s `--controller-a/b` does not offer it, so no person's game reaches a score. One seat at most. With a person seated, Forge's "AI can't play these cards well" list is not shown (`guiRules`): no Forge AI plays the model's seat. The model's seat is unchanged |
| `pass`, `first`, `script` | puzzle arms |
| `greedy:` (a model spec, `CONTROLLER_A=llm`, `LOOKAHEAD=1`) | the one-move-deep floor (`GreedyBridge`): on an empty stack, previews each cast and non-mana activation with the seat's `simulate` and takes the largest drop in the opponent's life, else passes (a preview also resolves what is already on the stack, so with something there, or without the faithful STACK line, it passes); attacks with everything; a target ask takes what the chosen play's preview assumed (the opponent's copy of a name first, each used once across a chain's asks), else ranks the opponent's side first and the seat's own last, below declining an "up to" ask, never CANCEL; any other ask the do-nothing answer; plays no land. Its stored rate carries its env and harness epoch |
| `script:<file>` (a model spec, `CONTROLLER_A=llm`) | a written solution line played by the model seat (`ScriptBridge`): each line names the option the seat is shown, the bridge answers `answer` with it, and a line no option fits is a drift, never a guess |
| `search` | the puzzle prover: both seats draw from one shared line of choices (`PuzzleArms.Search`), and `PuzzleSearch` replays prefixes to find a winning line and beat every opponent reply on it, then (rules puzzles) searches each solver deviation for a second solution; a rule puzzle's `[acceptance]` section (`PuzzleAcceptance`) decides what a solve is |

- *Why the prover:* Forge's puzzle mode tests one opponent line, its AI's, so a solve can come from the AI
  blocking badly. A proof (`docs/eval/BENCHMARK-V1.md`, phase 2) beats every opponent choice the engine offers, and
  every leaf of it is a solve Forge scored. A decideMany subset is drawn through `PuzzleArms.subset`, which
  makes the old draws for every other arm (no canary moves) and one include-or-not per option for search.

- *Why written lines:* a 12-action line with three options a step is one line in half a million, which no rollout
  budget finds (h_0 at budget 600, `docs/harness/HARD-PUZZLES.md` section 4). A person can write it. Played through the
  model seat, the line sees the menus a model sees (floating mana, costly mana abilities, splice and alternative
  costs) and the engine checks every step. Unmatched priority menus pass and unmatched mana-untap asks are left to
  the engine, both counted; a result row's `script_played` is true only when every line was taken with no drift.
  `puzzle_baselines.py line` reads the row (`docs/eval/PUZZLES.md`, "Proving a written line"), including the seat's
  `plays_not_made`: a play the line chose that did not happen voids it, and a row without the count is refused.
  The seat's two tiers share one bridge, so a script has one position. Against `CONTROLLER_B=search` the prover
  plays the line as the solver, so every branch it enumerates is an opponent reply the fixed line must beat.
  `script:relaxed:<file>` (the minimality replays) answers a decision the line does not fit with the do-nothing
  option instead of drifting.

- The seed-42 floor and random mirrors are pinned canaries, at A/108/1828 and A/55/365 (Linux pins, `eval/harness_acceptance.expected`).
- *Why:* a score is meaningless without the trivial baseline it beats (the "88% lesson" in `CLAUDE.md`).
  A canary that cannot move cannot catch a regression, so any change to an automatic path re-pins it
  with evidence.

**Playing the seat yourself.** `setup.sh` takes a fresh clone to a built Forge at the pin (a shallow checkout,
recorded in `.forge-home`) and writes `secrets.env`. `play-vs-llm.sh` seats a person (`CONTROLLER_A=human`) against
the model's seat on Forge's match screen. The seat plays the panels' `pilot_condition`, every other key a batch
scrubs (`run_batch.build_env`) is unset so the caller's shell changes nothing the seat is asked or shown, and
`TRANSCRIPTS=1` keeps every request; the game goes to `runs/human/<time>/` (git-ignored).
`eval/play_commentary.py` (`--commentary`) prints a line per model decision as the game goes, which spoils its hand.
A game against a person is never a score.

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
  - provenance: `overlay_sha` hashes every overlay source (`forge-overlay/*/src/main/**`, the harness
    classes) and every engine patch as the file it makes (`engine-patches/`), and cited mode refuses when
    any installed file differs from the checkout's (`benchmark_version.overlay_tree_sha` and
    `checkout_sources`; it covered two files until 2026-10-16, review H3);
  - unset `--context-profile` is the faithful seat (`v4-faithful`), as in the play scripts; the legacy
    `v1-compat` (coaching, a reasoning cap, a 25-decision turn cap) runs only as a named arm;
  - it resumes;
  - it plays a crashed game once more and sets the failed attempt aside (`--retries`);
  - it enforces the budget;
  - it refuses an llm seat that does not name both of its models, in every mode: a seat with no controller
    is an llm seat, and an unset model fell through to the harness default (Opus), so a Luna mirror
    launched with seat B unset played Luna against Opus 5.5 with every stamp healthy (issue #234);
  - cited mode refuses a seat on the local Claude CLI outright (`claude-cli:`: a development lane, owner
    2026-10-24); `openrouter:` games wait on the network and may run in parallel (review E3);
  - *Why pinned:* a baseline arm that silently inherited a setting would be pooled with a different
    measurement.
- **`aggregate.py`** reports win rates with confidence intervals, kept apart per condition.
  **`defect_analyzer.py`** is the run oracle: `ai_tier_count=0` and no silent drops, or the run fails.
- **Seed governance (`eval/seed_policy.py`):** some seed blocks are reserved for evaluation, and no
  training data may come from them.
- **The three headline scores:**
  - **STR-AI:** win rate against Forge's AI over fixed panels (`docs/eval/SCORING.md`). The panels file pins
    the pilot condition (profile, tiers, answer tool, tools, lookahead, chat windows, plans): `run_batch.py
    --panel` launches and `panel_score.py` scores no other. *Why:* a score is a property of one seat; two
    settings under one name would be two benchmarks.
  - **STR-HELDOUT:** the same on a held-out seed band; to become the private mined puzzle split
    (`docs/eval/BENCHMARK-V1.md`, owner decision 2026-10-18).
  - **PUZ:** the puzzle suite (`docs/eval/PUZZLES.md`), where Forge enforces the goal and a solve is judged
    by the engine. Its hard suite (`eval/suites/puzzles-hard-gen-v1.json`) is generated (`eval/puzzles/puzzle_gen.py`): each
    board's own written line is its reference line (`arm: written`), replayed against Forge's AI, and a board is
    active only when every floor failed it. *Why:* the proven rules puzzles are mostly one or two actions; a
    generated board is 10 to 15 steps with the opponent at exactly what its line deals, and is fresh, so it
    cannot be memorised.
    A second suite (`eval/suites/puzzles-hard-gen2-v1.json`) adds a trap to each board: a natural plain plan,
    searched to its own best, falls 1 to 3 short of the searched maximum. A third
    (`eval/suites/puzzles-hard-gen3-v1.json`, 68 boards) adds Goblin token engines, land sequencing
    (landfall), Equipment and infect at the same bar.
    Each game runs under a wall-clock cap (`puzzle_solve.py --game-cap-minutes`, 30 by default, recorded per
    puzzle as `record.game_cap_ms`); a capped game is unsolved. The cap is a condition: a resume may raise it and
    then replays every puzzle played under the lower one in full, and `puzzle_score.py` refuses rows from two caps.
    Each model call's deadline (`--call-timeout-minutes`) and the smart tier's token cap (`--max-tokens`, which is also
    its thinking budget) are recorded the same way. A resume under another value is refused, and the scorer refuses to
    pool them (`docs/eval/PUZZLES.md`).
    *Why:* frontier games of two models reached the win after 30 minutes, and the owner raised the cap
    (`docs/harness/FRONTIER5.md`, "How it ran", names the rows). Replaying only cut-off games would bias the score
    toward fast games.
- **`puzzle_watch.py`** watches a `puzzle_solve.py` run, live or after, from the run's `puzzle-run.json` and result
  rows, and writes nothing (`docs/eval/PUZZLES.md`, "Watching a run").
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
  (`HarnessEnvOurs`) scores 0.979; the default bridge, the ablations and the toys 0 (2026-10-22,
  `HARNESS-ENV-FRAMEWORK.md` section 4.9).
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
   - an isolated compile, which records the overlay hash it compiled in `overlay-sha.txt`; the puzzle launchers
     (`puzzle_baselines.Build`) run only a build whose hash is the checkout's, unless `--allow-stale-build`, which
     the run's provenance records (`docs/eval/PUZZLES.md`);
   - every Java and Python test, with a declared-vs-run count;
   - the pinned canaries, with a class-load proof that the new build ran;
   - loopback fake-model games for tools, answers, the faithful profile, lookahead, rules and the turn chat,
     and a written puzzle line played by a script seat (`ScriptBridge`; the stage table is in
     `docs/harness/ACCEPTANCE.md`);
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
   - **The keyword gauntlet (`eval/keyword_gauntlet.sh`, on demand, no model cost):** the sweep's cards (552 of its
     553; 1996 World Champion is left out, as the script says) and Forge's twelve Forecast cards (`kw28`: Forecast is
     an ability parameter, not a keyword, so the sweep never drew one) in 29 decks (`eval/gauntlet/`), each deck's
     LLM seat on the loopback fake model (`--policy first --answer-tool`: the first option of every ask) against
     the next deck's Forge AI, in full games. Triggers, combat, upkeep and
     end-step mechanics reach the seat as a game brings them. `eval/gauntlet_scan.py` fails on a choice Forge's
     AI made, an unreadable answer, a play that failed to pay or to target, a crash or an unfinished game. The engine's
     "failed to target" line names a card, not a player, so it is set apart (listed, not counted) only when the card is
     in the Forge AI opponent's deck and not the seat's, by the decks the game log names; anything it cannot attribute
     counts, since the seat can fail a target on paths that log no line of its own (PR #344). *Why both:* the
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
3. **Optional live-model checks (`eval/tests/test_live_answer_tool.py`, `LIVE_MODEL_TESTS=1`, about a cent):**
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
  document current or historical. `eval/tests/test_docs_index.py` keeps CLAUDE.md under 200 lines and the index complete.

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
- **The Claude CLI text lane** answered in text, because it had no function calling; it is retired (2026-10-25).
