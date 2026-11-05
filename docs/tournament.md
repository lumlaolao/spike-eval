# LLM Legacy Tournament

An 8-model, single-elimination, best-of-1 tournament where each model **drafts its
own Legacy deck** from a shared pool and every match is **recorded to an mp4**.

This is a *showcase* mode, not a cited eval: models differ, decks differ, seats are
randomized, and strategy plans are injected (each deck's `.plan`). None of these
numbers are comparable to the trustworthy `run_batch.py` baseline — that's fine,
that's not what this is for.

## What it does

1. **Draft** (`--models m1 … m8`, seed order = listed order): each model is shown the
   deck pool with one-line blurbs and picks a deck. Picks are **unique** — a drafted
   deck is removed from the pool — so all 8 players end on different archetypes. One
   cheap OpenRouter call per model.
2. **Bracket**: a random draw (seeded by `--seed`) shuffles the 8 players into slots
   1-8; the quarter-finals are adjacent slots (1v2, 3v4, 5v6, 7v8), then SF → Final.
   The draw is saved in `bracket.json` and reused unless `--redraw`.
3. **Match**: one bo1 game per pairing in the GUI runner, recorded to
   `<out>/<round>__<a>_vs_<b>.mp4`. Seat (play/draw) is randomized per match. The
   winner is read from the identity-keyed game-summary line, so seat randomization
   never corrupts who advanced.
4. **Output**: `draft.json`, `bracket.json` (resumable state), per-match mp4s, and
   `REPORT.md` with the bracket and champion.

## The deck pool (`eval/decks/`)

The pool is every `eval/decks/*.txt` except the format top-8 lists
(`deck_legality.is_format_deck`, `eval/decks/SOURCES.md`); `load_pool()` in
`eval/tournament.py` builds it. Each `<deck>.txt` has a sibling `<deck>.plan` — a
one-line strategy the model is given when piloting it (the plan follows the *deck*,
not the seat). The Legacy archetypes in it:

`ant-storm` · `burn` · `cephalid-breakfast` · `death-and-taxes` · `doomsday` ·
`elves` · `food-chain` · `goblins` · `grixis-reanimator` · `infect` · `lands` ·
`mardu-energy` · `miracles` · `painter` · `sneak-and-show` · `ur-delver`

The 20 `hook-*` decks ([harness/HOOK-DECKS.md](harness/HOOK-DECKS.md)) are not format
lists either, so `load_pool()` offers them in the draft too.

To add a deck: drop `foo.txt` (a `N Cardname` decklist, blank line before an optional
sideboard) and `foo.plan` (one strategy line) into `eval/decks/`. No code changes.

## Egress consent (required for real runs)

Every draft call and every in-game decision ships the full game snapshot to
`openrouter.ai`. Mirroring the Java factory's gate, the driver **refuses to run real
games or drafts unless `OPENROUTER_ALLOW_EGRESS=1` is set** and `OPENROUTER_API_KEY`
is present. The driver never sets that flag for you — you set it explicitly. Only a
full `--dry-run` is offline and skips the gate; `--mock-draft` alone skips just the
draft-phase calls — the bo1 games are still real, so the gate still applies.

**Where the key goes.** Put your key (from https://openrouter.ai/keys) in
`secrets.env` at the repo root — it's git-ignored and auto-loaded by the driver:

```
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_ALLOW_EGRESS=1        # uncomment this line to consent
```

Inline env (`OPENROUTER_API_KEY=... python3 ...`) still works and overrides the file.

## Running it

```bash
# 0. build must be current
FORGE_HOME=~/code/forge bash install.sh && (cd ~/code/forge && mvn -q -pl forge-gui-desktop -am install -o -DskipTests)
#    (or, on a fresh machine: bash setup.sh)

# 1. FREE plumbing check — no key, no egress, no games launched
python3 eval/tournament.py --models a b c d e f g h --mock-draft --dry-run

# 2. cheap end-to-end smoke test — ONE recorded game on a throwaway model,
#    proves the OpenRouter bridge parses live responses before frontier spend
OPENROUTER_API_KEY=sk-... OPENROUTER_ALLOW_EGRESS=1 \
  python3 eval/tournament.py --smoke qwen/qwen3-8b --out runs/smoke

# 3. the real tournament (8 frontier models)
OPENROUTER_API_KEY=sk-... OPENROUTER_ALLOW_EGRESS=1 \
  python3 eval/tournament.py --out runs/tournament-1 \
    --models anthropic/claude-opus-4.8 anthropic/claude-fable-5 openai/gpt-5 \
             x-ai/grok-4.5 google/gemini-3.5-flash qwen/qwen3.7-max \
             moonshotai/kimi-k3 deepseek/deepseek-v4-pro
```

`--draft-only` stops after the draft. The run is resumable: re-invoke with the same
`--out` and completed matches are skipped (state in `bracket.json`).
A bracket's games run at the JVM's defaults: the tournament strips every condition `run_batch.py` pins from the
shell (PR #347). `bracket.json` does not record a condition, so do not resume a bracket begun before that change
from a shell that exported one; start it again with a new `--out`.

## Cost & time

- **Money**: a bo1 single-elim is 7 games plus 8 draft calls. No tournament has been
  priced; the driver has no `--budget-usd`, so say the expected cost before a real run.
- **Wall-clock is the real cost**, not money. Recording forces GUI mode; a frontier
  bo1 game runs tens of minutes, and the **first** game on each new deck also spends
  minutes downloading card art from Scryfall (one-time; it caches). Budget hours, and
  run it from a session with a display. `--cap-minutes` (default 60) bounds each game.

## Flags

| flag | meaning |
|---|---|
| `--models …` | 8 bare OpenRouter ids (no `openrouter:` prefix) |
| `--smoke MODEL` | one recorded game, MODEL vs itself, two random decks |
| `--out DIR` | output dir (default `runs/tournament-<ts>`) |
| `--seed N` | base RNG seed (draft + shuffles + seat coin-flips); default 100 |
| `--cap-minutes N` | per-game wall-clock cap; default 60 |
| `--retries N` | replays of a drawn/invalid game before a seeded coin-flip; default 2 |
| `--forge-home DIR` | the Forge checkout holding `run-llm-spike.sh`; default `$FORGE_HOME` as given if set, else `~/code/forge` or `/root/forge` |
| `--no-record` | disable mp4 capture (faster; defeats the point) |
| `--record-fps N` | recording frame rate; default 8 |
| `--mock-draft` | assign decks with the seeded RNG (no OpenRouter calls) |
| `--dry-run` | print bracket + would-run commands; launch no games |
| `--draft-only` | run the draft, then stop |
| `--redraft` | draft again even if `--out` already has a `draft.json` for these models |
| `--draw-only` | draft, make and print the bracket draw, then stop |
| `--redraw` | draw the bracket again even if `bracket.json` has one |
| `--max-matches N` | play at most N new matches this invocation; default 0 (all remaining) |
