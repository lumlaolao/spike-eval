# Controller ownership ledger (gate G7, first stage)

`controller-ownership.tsv` classifies all 130 public overridable `PlayerController` hooks
by who decides them on the LLM seat. `ControllerOwnershipTest` regenerates the inventory
by reflection on every run and fails when the ledger and the compiled controller disagree
in either direction, so an override cannot appear or vanish without a reviewed ledger edit.
The test also pins the inherited AI cheat-shuffle hook inert on both launch paths (engine
default, preference, and every shipped AI profile).

Columns: `impl` (LLM / AI / BASE, from reflection), `kind` (strategic, payment,
information, mechanics, ui, match, exotic), `ownership`, `legacy_reach` (core / rare /
none for the 16 eval decks), `notes` (evidence and call-site counts).

Ownership values:

- `model`: model-routed with deterministic fallbacks, never Forge AI.
- `model+ai-fallback`: model-routed, but a failure branch calls `super` (recorded as a
  `tier=ai` row, which the defect analyzer counts).
- `ai-observed`: overridden only to log; Forge AI decides (`tier=ai` row).
- `ai-inherited`: not overridden; Forge AI decides silently with no jsonl row. **The
  analyzer's `ai=0` invariant cannot see these.**
- `deterministic`, `base-inherited`, `wrapper`: no model and no strategic AI content.

## Initial counts (2026-09-07, before direct implementation)

| impl | rows |
| --- | --- |
| LLM | 29 (9 model, 12 model+ai-fallback, 4 ai-observed, 4 deterministic) |
| AI | 87 (86 ai-inherited, 1 wrapper) |
| BASE | 14 |

Silent Forge-AI hooks that are strategic or payment decisions AND reachable in Legacy
play: **18**. The test prints this as `[OWNERSHIP] ...` and fails if it grows.

## The 18 open decisions (all `ai-inherited`, `core`)

| Hook | What Forge AI decides for the model |
| --- | --- |
| chooseBinary (Boolean) | play/draw, tap/untap, odds/evens, left/right |
| chooseCardsToDelve | which graveyard cards are exiled for delve (Murktide) |
| chooseCardsToDiscardToMaximumHandSize | cleanup-step discards |
| chooseColor | colour naming (Painter's Servant and similar) |
| chooseNumber (params overload) | numeric choices from the 4 engine call sites that pass params; **bypasses the model's 4-arg override** |
| chooseOptionalCosts | kicker / buyback / entwine |
| chooseProtectionType | Mother of Runes / Giver of Runes colour |
| chooseSomeType | creature/card type naming (Cavern of Souls, Engineered Plague) |
| chooseStartingPlayer | play first after winning the toss (AI always plays) |
| confirmPayment | whether to pay an optional cost part |
| confirmReplacementEffect | optional replacement effects (dredge, may-enter-tapped) |
| confirmTrigger | accept or decline "may" triggers |
| getCostDecisionMaker | which cards satisfy non-mana costs (sacrifice for Cabal Therapy flashback, exile from graveyard) |
| orderAndPlaySimultaneousSa | trigger order AND trigger targets via `brains.doTrigger` |
| orderSimultaneousSa | trigger order |
| payCostToPreventEffect | whether to pay for Daze / Force Spike / Spell Pierce / Mana Tithe |
| payManaCost | which sources pay every spell (colours left open) |
| playTrigger | triggered-ability targets and modes via `brains.doTrigger` |

Two information hooks were captured on 2026-09-07: both `reveal` overloads are now
overridden. Under the `v4-faithful` context profile every reveal delivered to the seat is
appended to its observation stream (E-id, turn, searchable, archived); legacy profiles
keep the inherited AiCardMemory-only behaviour so their prompt bytes are unchanged.
`notifyOfValue` (45 engine call sites: chosen names, types, numbers) remains uncaptured.

Footnotes worth knowing: `isAI()` returns true for the LLM seat and 14 engine call sites
branch on it; `chooseEntitiesForEffect` is an AI loop that dispatches each pick to the
model-owned `chooseSingleEntityForEffect`, so its strategic content does flow through the
model.

## Next steps for G7

1. Route the 18 open decisions through the model with deterministic fallbacks, one hook
   per change, each with a routing test and a ledger edit (`ai-inherited` -> `model`).
   Highest impact first: `payCostToPreventEffect`, `confirmTrigger`,
   `chooseCardsToDiscardToMaximumHandSize`, `getCostDecisionMaker`, `playTrigger`.
2. Override the params overloads (`chooseNumber`, `chooseBinary`) so they delegate to the
   model-owned overloads instead of bypassing them.
3. Capture `notifyOfValue` into the seat stream.
4. Count inherited hook invocations at runtime (a test-only counting subclass over a full
   seeded game) so `legacy_reach` becomes measured, not estimated.

## Direct implementation after Claude stopped (2026-09-07)

Codex now implements locally at the user's request. Numeric params/list overloads and both binary overloads are model-owned. The original range overload also no longer delegates exceptions to Forge AI. Large integer ranges accept a strict `NUMBER: <integer>` response over the entire legal range; the previous min/mid/max menu omitted legal choices. Small ranges and sparse explicit lists retain indexed menus. Full int boundaries use long arithmetic to avoid overflow. Binary option ordering matches the human controller for all eight engine enum values. Invalid replies use audited deterministic fallbacks.

After numeric/binary routing the reflection inventory was 33 LLM / 83 AI / 14 BASE. Remaining silent core strategic/payment hooks then: 16 (numeric params and binary Boolean removed from the original 18). Numeric list and binary params also remove two rare AI bypasses. Trigger, payment, and other previously listed routes remain unfinished. The initial ledger counts above are historical, not a completion claim.

## Optional confirmations (direct implementation)

`confirmTrigger`, `confirmPayment`, and `confirmReplacementEffect` now ask the model and record their decisions. Mandatory triggers are forced legal accepts. Failed optional confirmations decline with an explicit automatic fallback. Confirmation never resets or reselects targets; trigger targeting and cost execution remain separate unfinished ownership routes. `confirmAction` also no longer delegates its exception branch to Forge AI.

Inventory after these overrides: 36 LLM / 80 AI / 14 BASE; 13 silent core strategic/payment hooks remain. This is not a claim that payment execution or trigger targeting is model-owned.
