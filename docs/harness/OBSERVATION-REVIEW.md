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

`render()` built the opponent's `CAST THIS GAME` / `LANDS PLAYED` from
`PlayerControllerLLM.castHistory(game, …)` (the legacy, pre-log-redaction renderer)
rather than the seat observation stream. `castHistory` reads the raw engine log and
strips only a trailing engine id (`" \\(\\d+\\)$"`), so it bypassed redaction — a cast
line, which the engine formats as `"<player> cast <name>"` with **no id**, could carry
a hidden caster's real name that the seat stream would have redacted.

### Fix

`render()` now aggregates the opponent's casts and land drops from the **seat stream**
(`streamCastHistory`), which can only contain what the viewer may see. The aggregation
mirrors `castHistory`'s exact output (count-desc, first-seen tie-break, same cap
wording) and additionally strips the seat-handle annotations redaction introduces
(`" @N"`, `"(was @k)"`). Verified byte-identical on public data: the acceptance
faithful game's `context_sha` is **unchanged** (`8b5409f50ab1`) across this change, so
no version bump is needed — only hidden references differ.

## Evidence

- `SeatObservationTest`:
  - `publicEvidenceSurvivesAHiddenCardWithTheSameName` — a face-up public
    `Willbender` and a hidden face-down `Willbender`; the public reference survives
    (`Viewer resolves Willbender @N`) while the hidden one becomes
    `Rival casts a face-down card`. Fails against the old dictionary.
  - `hiddenHandCardNameIsRedactedByItsEngineId` — a card in the rival's hand named by
    an event loses its name with its id (`Rival casts a hidden card`). Fails against
    the old id-drop-only path.
  - `castHistoryIsSourcedFromTheRedactedStreamNotTheRawLog` — `render()`'s
    `CAST THIS GAME` / `LANDS PLAYED` come from the redacted stream, not the raw log.
    Fails against the old `castHistory` path (defect 2).
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

**Later (2026-10-13): v2 → `observation-faithful-v3`.** A new `KNOWN IN HIDDEN ZONES` section was
added (seat knowledge of cards that publicly moved into an opponent's hidden zone, or that the
engine revealed from one; invalidated on a seen exit). Prompt bytes changed, so this is again a
provenance event: v3 artifacts are not comparable to v2 and must not share a table. The acceptance
greps and `PLAYER-HARNESS.md` were updated to `v3`.

**Later still (2026-10-13): v3 → `observation-faithful-v4`.** Added an explicit `SPENT THIS TURN:`
line (land drop AVAILABLE/USED, spells cast, attackers declared) so legality is stated rather than
inferred from absence. Same discipline: prompt bytes changed, so v4 is a new provenance identity
(v4 artifacts are not comparable to v3).

**Later still (2026-10-13): v5 → `observation-faithful-v6`.** Added a `FACTS` line from Forge's own
arithmetic (both next-combat clocks, their instant-speed mana), labelled as estimates.

**Later still (2026-10-13): v4 → `observation-faithful-v5`.** The seat's plan is no longer dropped at
the turn boundary: it is rendered with the turn it was stated and flagged stale when the turn moves
on, and it is asked for once per turn on either player's turn (so an instant-speed "hold up mana"
plan exists). Prompt bytes changed → new provenance identity.

## Residual limits (explicit)

- **Name-only references to a hidden object are not redacted.** If an engine call site
  logs a hidden card by name with no `(id)` and no source card, the name survives. This
  is the deliberate cost of not erasing public evidence. The engine's cast/land log
  templates and `CardView.toString` all either append the id or set a source card, so
  the common paths are covered; the faithful `CAST`/`LANDS` history is now sourced from
  the redacted stream (defect 2 fixed) rather than the raw log.
- **Historical permission is still current permission.** The id→object mapping is
  resolved against the game's current state at capture time. A card that was face-up
  when an old event was logged but is now face-down can still have that old public
  text redacted. Resolving this needs event-time permission snapshots the engine does
  not retain; not attempted here.
- Tokens and face-down-exile fixtures remain thin (G2).
