# Handoff: make the harness let the model be the bottleneck

**For a fresh coding agent. Read this whole file before touching anything, then read the four
documents it names. The task is to think, then act, on one idea; not to run a checklist.**

## The idea (the owner's words, restated)

> "The root of these issues is the harness is not perfect. We need the harness to let the model be the
> bottleneck."

Translate that into a testable goal: **the only thing that should ever cap how well the model plays is
the model.** If the seat plays worse than the model could, the harness did one of three things — it
never asked the model, it asked the model blind, or it took the model's answer and didn't use it.
Every harness defect filed in this repo is one of those three, or a mechanical bug (a fumble). Your
job is to make "the harness is the bottleneck" measurable and then shrink it.

Two things this does NOT mean, because getting them wrong is the whole failure mode:

- **It is not "let the model do everything."** Mechanical execution stays the engine's
  (`ComputerUtilMana` taps the lands, `TargetChoices` applies targets, `brains.doTrigger` sets modes).
  The harness is the bottleneck when it *decides* or *withholds*; it is not a bottleneck when it
  *executes* what the model chose. The line is: the model owns every **strategic choice**; the engine
  owns every **mechanism**.
- **It is not "fill gaps with a heuristic."** A deterministic fallback that picks for the model (even a
  sensible one) is the harness being the bottleneck in a different costume. Fallbacks are only legal
  as *failure* branches (dead bridge, unparseable reply) and must be logged; they are never the
  happy-path answer. See `CLAUDE.md` engine gotchas and `docs/STANDARDS.md` §4.

## Current state (what a prior session did — trust but verify)

The repo is `~/code/mtg-forge-llm-agent` (the harness). The engine is a **pinned** Forge checkout at
`~/code/forge`, `FORGE_COMMIT = 34a8662b7966a6ee757411feae2ae8ff38cce436` (bumped this session; a
pin bump is a provenance event — runs across it are not comparable).

Built this session (all merged to `main`):

- **A map of the interface**: `docs/harness/ROOT-INTERFACES.md`. The seat (`LlmSpikeGui.PlayerControllerLLM`)
  is a *partial* implementation of `forge.game.player.PlayerController`. `PlayerController` declares
  **108** decision methods; the seat overrides **43**; **27** of those call `super` somewhere. The
  ledger (`controller-ownership.tsv`) marks **69** `ai-inherited`, of which **29 are `strategic`**
  (3 `core`, 26 `rare`). The map names, per engine subsystem, the interface method, the engine
  implementation, what the seat does, and the gap.
- **Fixed, each with its own issue**: targeting is no longer a partial override (#124: single-target,
  multi-target, sub-ability chains, modal-via-modes, and divided target sets are all the seat's now);
  a combat declaration that throws takes a legal default instead of the AI (#126); `allowRepeat`
  modal modes are the model's (#127); a `delayedReveal` pick is the model's (#128); two opt-in
  switches are stamped/keyed/pinned (#117).
- **20 probe decks** (`eval/decks/hook-*.txt`, mapped in `docs/harness/HOOK-DECKS.md`) so the
  AI-owned hooks the archetype pool misses are actually reachable in a game. Two of them found real
  bugs (a divided-spell resolution NPE; the `mardu-energy` pool deck has no Galvanic Discharge).

Not done, and this is where you come in:

- The **"asked blind" axis has never been audited.** There is no systematic check that the prompt
  carries the information a decision turns on. It has only been found by reading individual games.
- The **"asked and ignored" axis** (parser/router fidelity: does the effect do what `raw_reply` said?)
  is likewise found only by reading.
- The **map is incomplete for `super` calls**: the 27 partial overrides were inventoried, but only the
  strategic ones (delayedReveal / allowRepeat / a declaration throw) were fixed; the rest are
  "harmless when the list is empty/forced" — that claim needs to be *checked per site*, not assumed.
- **"Perfect harness" is not a written contract.** There is no definition, and no gate, for "the model
  is the bottleneck."

## The taxonomy to audit against (this is the thinking part)

For every decision the engine hands the seat, ask these in order. Each "no" is a harness defect:

1. **Was it asked?** Did the model get a decision row for this choice, or did the engine's AI answer
   because the seat didn't override the hook / delegated via `super`? (`OWNERSHIP.md` +
   `controller-ownership.tsv`; `[TALLY] ai=0` is the invariant.)
2. **Was the right action even on the menu?** If a play a human would consider is missing, find out
   why in the engine before calling it a defect (mana, timing, legality). This is the play-loop axis.
3. **Was it asked *with what it turns on*?** Does the prompt contain the rules text of every card
   involved, the opponent's visible board and mana, relevant graveyards, earlier reveals, the model's
   own counters (energy, poison), life totals? The `Galvanic Discharge` decision ("how much {E} to
   spend, and is that lethal to that creature") turns on *energy + every creature's toughness* — both
   are rendered today, but nothing *guarantees* the next such decision is. This axis is unaudited.
4. **Was the answer used?** The log line after the decision must do what `raw_reply` chose. A reply
   that says one thing and a game that does another is a parser/router defect.
5. **Did it ask for something and not get it?** Every tool call and its result; a model that asks three
   ways for the same thing is telling you what the harness lacks.
6. **Anything a person watching would call wrong**, even if no row flags it.

There is also a subtler class the map exposes: **an engine script's own ordering can make a decision
blind even when the harness routes it.** `Galvanic Discharge` (Forge script) asks the energy *number*
before the target, so the model commits to X before knowing what it's killing. That is not our code,
but it is the harness being the bottleneck in the only sense that matters to the player. Note it;
decide whether it is in scope.

## What to produce (concrete asks, in priority order)

1. **A ranked list of the "asked blind" defects**, with evidence, produced by a *method* rather than by
   reading one game. Start from `eval/coverage_audit.py` (it tabulates decision families and the decks
   that exercise them) and `eval/match_review.py`. Propose the check that would have caught each.
   `galvanic discharge` is the template of the kind of thing to look for.
2. **A systematic `super`-call audit.** For each of the 27 partial overrides, decide, by reading the
   engine, whether the delegating condition can ever be a *real strategic choice*. File a
   `harness-defect` issue for each that can (the hunter's format: Defect / What happened / What should
   have happened / Evidence / Where / Reproduce without the game / Fixed when / Detector gap / Seen).
3. **A written definition of "the perfect harness"** as a contract with gates, e.g.: every decision
   type has either a model row or a documented mechanical reason it does not; every prompt is asserted
   to contain the fields the decision turns on; every parser is asserted to round-trip. If a rule is
   worth having, `docs/STANDARDS.md` should carry it and a test should enforce it.
4. **Then fix what you found**, one class per PR, per the repo's method.

## How this repo works (do not skip)

- **`forge-overlay/` is the source of truth for Java. Never edit `~/code/forge`** — `install.sh`
  copies the overlay into the checkout, and the checkout is a build scratch copy. `CLAUDE.md` and
  `docs/STANDARDS.md` are binding.
- **Build/validate**: `FORGE_HOME=~/code/forge bash install.sh`, then
  `cd ~/code/forge && mvn -q -pl forge-gui-desktop -am install -o -DskipTests`, then
  `bash eval/harness_acceptance.sh` (whole-harness gate: isolated compile, all Java/Python tests,
  zero-model canaries, loopback fake-model games). It must pass on the PR head.
- **Reading a game is how defects are found here.** `game-loop` (in `.claude/skills/`): one match,
  read it, fix the harness, next. Paid games use only `openrouter:deepseek/deepseek-v4.1-flash`,
  always with `--budget-usd`, ~$0.04/game; never set `OPENROUTER_ALLOW_EGRESS` yourself.
- **A zero-model `random`-arm game reaches the full menu and costs nothing** — use it to reach a hook;
  it does not build prompts, so use a loopback (`faketool`) game or an LLM game to read a prompt.
- **Skills** (`pr-format`, `pr-review`, `simplify`, `standards`, `game-loop`, `harness-audit`,
  `defect-hunt`, `defect-fix`) are the repo's method. **NOTE: the owner asked to pause the three
  review skills and merge directly for now** — confirm with the owner whether that still holds before
  you rely on the `pr_merge.sh` gate.
- **The shared engine checkout is single-writer**: take the lock (`~/.forge-agent-lock`, see
  `defect-hunt` SKILL "Sharing a machine") around any install/build/game/acceptance run.

## The one rule that resolves most arguments

**A finding is a harness defect unless the prompt shows the model had everything it needed to decide
well.** If the model had what it needed and chose badly, that is the result — record it as a puzzle
candidate and move on. If it did not, the harness is the bottleneck and you fix the harness.

Start by reading `docs/harness/ROOT-INTERFACES.md` and running `python3 eval/coverage_audit.py` over a
few run directories. Then tell the owner what you found and which axis you will attack first.
