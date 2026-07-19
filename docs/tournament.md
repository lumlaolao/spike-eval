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
2. **Bracket**: standard 8-seed single elim (QF 1v8, 4v5, 2v7, 3v6 → SF → Final), so
   the top two seeds can only meet in the final.
3. **Match**: one bo1 game per pairing in the GUI runner, recorded to
   `<out>/<round>__<a>_vs_<b>.mp4`. Seat (play/draw) is randomized per match. The
   winner is read from the identity-keyed game-summary line, so seat randomization
   never corrupts who advanced.
4. **Output**: `draft.json`, `bracket.json` (resumable state), per-match mp4s, and
   `REPORT.md` with the bracket and champion.

## The deck pool (`eval/decks/`)

12 validated archetypes (every card name checked against Forge's DB; all 60-card
legal). Each `<deck>.txt` has a sibling `<deck>.plan` — a one-line strategy the model
is given when piloting it (this is the per-deck-identity strategy mechanism; the plan
follows the *deck*, not the seat):

`grixis-reanimator` · `mardu-energy` · `ur-delver` · `death-and-taxes` ·
`sneak-and-show` · `lands` · `elves` · `ant-storm` · `miracles` · `goblins` ·
`burn` · `infect`

To add a deck: drop `foo.txt` (a `N Cardname` decklist, blank line before an optional
sideboard) and `foo.plan` (one strategy line) into `eval/decks/`. No code changes.

## Egress consent (required for real runs)

Every draft call and every in-game decision ships the full game snapshot to
`openrouter.ai`. Mirroring the Java factory's gate, the driver **refuses to run real
games or drafts unless `OPENROUTER_ALLOW_EGRESS=1` is set** and `OPENROUTER_API_KEY`
is present. The driver never sets that flag for you — you set it explicitly. Only a
full `--dry-run` is offline and skips the gate; `--mock-draft` alone skips just the
draft-phase calls — the bo1 games are still real, so the gate still applies.

## Running it

```bash
# 0. build must be current
bash install.sh && (cd ~/code/forge && mvn -q -pl forge-gui-desktop -am compile -o)

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

## Cost & time

- **Money**: budget is comfortable. A bo1 single-elim is 7 games; at a few hundred
  model calls per game and mixed frontier prices, expect well under $100 (rough order
  $10–40 depending on how expensive the priciest models are). The draft is 8 tiny
  calls (~free).
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
| `--no-record` | disable mp4 capture (faster; defeats the point) |
| `--record-fps N` | recording frame rate; default 8 |
| `--mock-draft` | assign decks with the seeded RNG (no OpenRouter calls) |
| `--dry-run` | print bracket + would-run commands; launch no games |
| `--draft-only` | run the draft, then stop |
