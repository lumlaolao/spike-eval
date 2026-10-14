# G7 remaining work order — the four hard hooks

After the 2026-10-12 routing work, four `core` silent Forge-AI hooks remain (`OWNERSHIP.md`).
They are grouped here because each sits on the **mechanics boundary**: routing them means changing
how the engine *executes* something, not just which legal option is chosen. Rushing them risks a
broken cast, a fumbled trigger, or illegal mana — i.e. the exact "dumb stuff" the harness exists to
eliminate. Each needs its own reviewed PR, its own routing test, and a ledger edit
(`ai-inherited` -> `model`, or `model+ai-fallback` where a mechanical step stays the engine's).

| Hook | What the AI decides today | Why it is hard | Safe approach |
| --- | --- | --- | --- |
| `payManaCost(ManaCost, CostPartMana, SpellAbility, prompt, ManaConversionMatrix, effect)` | which sources tap to pay every spell — colours left open | the method must **actually tap and return success/failure**; the engine's protocol (`ComputerUtilMana`) is intricate. A wrong override breaks *every* spell | keep `ComputerUtilMana` doing the tapping; expose only the *choice among equivalent payment plans* if one exists. If no safe seam exists, **leave it AI and say so** — a wrong model here is worse than an AI here |
| `getCostDecisionMaker(Player, SpellAbility, boolean, prompt)` | which cards satisfy **non-mana** costs (sacrifice for Cabal Therapy flashback, exile from graveyard) | returns a `CostDecisionMakerBase`; the model must be asked *inside the payment loop* | return a thin `CostDecisionMakerBase` subclass that delegates each card pick to the already-model-owned `choosePermanentsToSacrifice` / `chooseCardsForEffect` / `chooseSingleCardForZoneChange`, and falls back to `super` when no model is budgeting |
| `playTrigger(Card host, WrappedAbility, boolean isMandatory)` | triggered abilities: chooses whether to play, and **sets targets/modes** via `prepareSingleSa` -> `brains.doTrigger` | entangled with `WrappedAbility` + targeting; getting order/targets wrong fumbles a trigger silently | route the yes/no (already partly covered by `confirmTrigger`); route **targeting** through the existing `chooseTargetsFor`/`ensureTargetsSet` path rather than re-implementing `doTrigger`. Do NOT re-own mechanics — reuse the target machinery |
| `orderAndPlaySimultaneousSa(List<SpellAbility>)` | orders triggers AND plays them, setting targets | its ordering half is **already model-owned** (`orderSimultaneousSa`, PR #24); only the play/targeting half is AI | low priority: the strategic content (order) already flows through the model. Reclassify its ledger note (done); revisit only if trigger targeting proves load-bearing in the corpus |

## Method (same as the 13 already done)

1. One hook per PR. Read the AI impl and one real engine call site first; write down the
   contract in the PR body.
2. Ask the model **only** where a genuine strategic choice exists; keep the mechanical execution
   on the engine. Prefer reusing an already-model-owned hook over new code.
3. Deterministic legal fallback on every failure branch — **never `super`/Forge AI for strategy**
   on the model path.
4. Routing test that fails against the old code (`aiDelegationsCount() == 0`), a ledger edit, and a
   pin bump (`ControllerOwnershipTest` + `harness_acceptance.expected`). Re-pin canaries only with
   the commit that legitimately moved them.
5. `bash eval/harness_acceptance.sh` green before merge.

## Explicitly acceptable outcomes

- **`payManaCost` may remain AI.** If there is no seam that keeps `ComputerUtilMana`'s tapping
  protocol intact, the honest answer is to leave it and document why. The count in
  `controller-ownership.tsv` is a backlog, not a target to hit at any cost.
- **`orderAndPlaySimultaneousSa` may remain `ai-inherited`** for its play/targeting half, since the
  strategic ordering is already the model's.

## Definition of done for G7

Every `core` strategic/payment hook is either `model`/`model+ai-fallback` (with a routing test) or
carries an explicit "mechanics-boundary, intentionally AI" note that a reviewer accepts. G7 closes
on *classified and justified*, not on *zero AI*.

## Status after reading the engine (2026-10-12)

The four remaining hooks were re-examined against the Forge source (not assumed). Findings:

| Hook | Verdict | Evidence from the source |
| --- | --- | --- |
| `getCostDecisionMaker` | **ROUTED** | `PlayerControllerAi` returns `AiCostDecision`; `CostDecisionMakerBase` exposes `protected player/ability/source`, so a subclass can route the card-choosing `visit(CostSacrifice)` to `choosePermanentsToSacrifice`. Done in `LlmCostDecision`, with the candidate filter **copied verbatim from `ComputerUtil.chooseSacrificeType`** so legality matches. Ownership 4 -> 3. |
| `payManaCost` | **stays AI (no seam)** | `PlayerControllerAi.payManaCost` = `ComputerUtilMana.payManaCost(new Cost(toPay, effect), player, sa, effect)` — the entire mana-payment mechanism in one call; source selection is internal, not a hook. Only which Elves a tap-type mana source (Heritage Druid) taps is the seat's, since #105. |
| `playTrigger` | **stays AI (no targeting seam)** | `playTrigger` = `prepareSingleSa(host, sa, mandatory) && ComputerUtil.playNoStack(...)`; `prepareSingleSa` sets targets/modes via `brains.doTrigger`, which is the AI core. Its yes/no is already model-owned via `confirmTrigger`. |
| `orderAndPlaySimultaneousSa` | **half-routed already** | The strategic ORDER is model-owned (`orderSimultaneousSa`); the inherited loop calls it virtually, then plays/targets via `prepareSingleSa`. Nothing strategic is silent; only the mechanics remain with the engine. |

**G7 conclusion:** 10 of 13 originally-silent hooks are model-owned; the 3 that remain are
no-seam engine mechanics, justified above by reading the source. G7 is *classified and justified*.
