# Observation review — narration redaction repair (G1/G2/G3)

Repair of the two defects the 2026-09-07 independent review flagged in
`SeatObservation.java`, done 2026-10-12. This file records what was wrong, what
changed, the evidence, and the residual limits. It is deliberately narrow: it
does not claim G1/G2/G3 complete.

## Defect 1 — a game-wide hidden-name dictionary erased public evidence

`capture()` computed, once per capture, the real name of **every** face-down card in
the game (`hiddenFaceNames(cardsById().values())`) and then replaced those names
globally in every narration line (`redactHiddenFaces`). Two harms:

- **Erased true public history.** A public card that merely shared a name with any
  hidden face-down card lost its name from public evidence. The old suite even
  contained positions whose text was annotated away this way.
- **Leaked by construction.** Because the substitution depended on hidden identity,
  changing which card was face-down changed *public* history text, which a reader
  could correlate. It also silently redacted *historical* events using
  *current* face-down state (permissions were not evaluated at evidence time).
- More directly: `redact()` dropped an engine id `(N)` whose card was hidden but
  left the card's **name** in place — so `"Rival casts Thoughtseize (4711)"` (a card
  in a hidden zone) became `"Rival casts Thoughtseize "`, leaking the name.

### Fix

Redaction is now **anchored to the object the event names**, via the engine id. The
engine formats a card reference as `Name (id)` (`CardView.toString`), so the id is a
reliable pointer even when the name is not:

- id resolves to a **visible** object → the id becomes that object's seat handle;
- id resolves to an **object this viewer may not know** (hidden by zone or by face)
  → the adjacent real name and the id are both removed, replaced by
  `a hidden card` / `a face-down card`;
- unresolved id → dropped (unchanged).

The game-wide dictionary is deleted. A public card that shares a hidden card's name
is therefore never erased, and hidden identity no longer influences public text.

## Defect 2 — faithful history bypassed the seat stream via legacy `castHistory`

`render()` builds the opponent's `CAST THIS GAME` / `LANDS PLAYED` from
`PlayerControllerLLM.castHistory(game, …)` (the legacy, pre-faithful renderer) rather
than the seat observation stream. Those strings are engine-formatted and can carry
engine ids, face-down casting identity and hidden names that the seat stream would
have redacted — an incoherent second path for live history.

### Status

**Not yet repaired.** It is a distinct change (routing the cast/land history through
the seat stream, or rendering it from `GameHistory`) and is tracked separately. It is
the remaining G3 "one coherent history" gap. Documented here rather than silently
dropped.

## Evidence

- `SeatObservationTest`:
  - `publicEvidenceSurvivesAHiddenCardWithTheSameName` — a face-up public
    `Willbender` and a hidden face-down `Willbender`; the public reference survives
    (`Viewer resolves Willbender @N`) while the hidden one becomes
    `Rival casts a face-down card`. Fails against the old dictionary.
  - `hiddenHandCardNameIsRedactedByItsEngineId` — a card in the rival's hand named by
    an event loses its name with its id (`Rival casts a hidden card`). Fails against
    the old id-drop-only path.
  - `opponentsFaceDownPermanentHidesItsIdentityButNotFromItsController` (pre-existing)
    still passes — the new object-anchored path preserves the intended redaction.
  - `hiddenStateDoesNotChangeTheObservationHistoryOrToolResults` (pre-existing
    paired-world test) still passes.
- Whole-harness acceptance: **all 9 stages PASS** (`eval/harness_acceptance.sh`),
  198/198 Java tests, 107 Python tests, both canaries unchanged
  (A/108/1004 and A/24/144), faithful stage green. See
  `ACCEPTANCE.md` "Recorded runs", 2026-10-12.

## Provenance

`SeatObservation.VERSION` bumped `observation-faithful-v1` → **`observation-faithful-v2`**.
Narration behavior changed, so the observation identity must change: the faithful
`context_sha` moves from `7896dbb44d9f` to `8b5409f50ab1`. Artifacts produced under
v1 are **not** comparable to v2 and must not share a table. `eval/harness_acceptance.sh`
greps and `docs/harness/PLAYER-HARNESS.md` were updated to the new string.

## Residual limits (explicit)

- **Name-only references are no longer redacted.** If an engine call site logs a
  hidden card by name without its `(id)` suffix, the name now survives. This is the
  deliberate cost of not erasing public evidence; every engine formatting site
  inspected (`CardView.toString`, `GameAction`, `ChangeCombatantsEffect`) appends the
  id, so the common leak path is covered. A hidden user of `getName()` alone would
  not be.
- **Historical permission is still current permission.** The id→object mapping is
  resolved against the game's current state at capture time. A card that was face-up
  when an old event was logged but is now face-down can still have that old public
  text redacted. Resolving this needs event-time permission snapshots the engine does
  not retain; not attempted here.
- Defect 2 above; tokens/face-down-exile fixtures remain thin (G2).
