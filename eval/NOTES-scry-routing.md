# Scry/reveal routing (Group 4, task 5) — what was implemented, what wasn't

Report C2 flagged three PlayerController hooks as candidates for routing: `orderMoveToZoneList`,
the scry confirm step, and the "may reveal" confirm step. All three turned out to be clean,
non-tangled override points, so the code change landed instead of being skipped — this file records
the scope for anyone auditing later.

## Routed (in LlmSpikeGui.java)

- **`arrangeForScry(CardCollection topN)`** and **`arrangeForSurveil(CardCollection topN)`** — both
  return `ImmutablePair<CardCollection toTop, CardCollection toOther>` and were previously
  unoverridden (fell straight through to `PlayerControllerAi`'s built-in logic, zero jsonl/log
  trace — the exact SF-2 "bare Preordain scry with no preceding [ROUTER] line at all" gap). Now
  share one private router (`arrangeTopN`) that shows the model every revealed card via
  `candidateLabel`, asks it to list which to KEEP ON TOP (in order — index 0 becomes the new top
  card per `GameAction.scry`'s reverse-then-push), and sends everything unlisted to the bottom of
  the library (scry) or the graveyard (surveil). Deterministic "keep everything on top, as-is"
  fallback on no-budget/unparsed/bridge-down/error — never Forge AI.

- **`orderMoveToZoneList(CardCollectionView cards, ZoneType destinationZone, SpellAbility source)`**
  — the generic "you looked at N cards, put them back in an order" hook used by Ponder, Brainstorm's
  put-back, Sensei's Divining Top's 3-card peek, Dig/DigUntil, and several other rearrange-style
  effects. Also previously unoverridden. Now routed through `decideMany` with `min=max=size` (the
  model must name all N cards, in order); falls back to the engine-given order (not Forge AI) on
  no-budget/unparsed/bridge-down/error. `cards.size() <= 1` short-circuits immediately (no real
  choice) without a model call or a jsonl row.

Both routes reuse `candidateLabel`/`srcOf`/`auto`/`decideMany` exactly like every other routed
decision in the file — no new plumbing, no new failure-ladder shape.

## Deliberately NOT touched (out of scope for this task)

- **Counterbalance's own reveal** (`counterbalance.txt`: `OptionalDecider$ You` trigger -> `Dig` with
  `DigNum$ 1`, `LibraryPosition$ 0`) reveals exactly one card and returns it to the same top-of-library
  slot — `DigNum=1` means there is never a real ordering choice, so it correctly never reaches the new
  `orderMoveToZoneList` override (the `size() <= 1` short-circuit is the correct behavior, not a gap).
  The "may reveal" yes/no itself already goes through the existing `confirmAction` override. Whether
  the revealed card's *identity* lands in the model's INTEL/snapshot text is a Group-3 (prompt/game-
  state) concern, not a Group-4 (routing) one — not re-litigated here.

- Any card whose "look/reveal" flow never calls one of the three hooks above (e.g. a scripted
  effect that reveals via a raw `PlayerController.reveal(...)` call with no accompanying order/choice
  hook) is unaffected by this change — there was no clean override point to route.
