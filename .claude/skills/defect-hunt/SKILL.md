---
name: defect-hunt
description: Stage one of the defect backlog for mtg-forge-llm-agent. Play one game at a time, read it decision by decision, and file each harness defect as a self-contained GitHub issue that another agent can verify and fix without the game. Never fixes anything itself.
---

# Defect hunt: play, read, file. Never fix.

This is stage one of a two-stage loop. Hunters play games and write defect notes; fixers
(`defect-fix`) each take one note, reproduce it, fix it and open a PR. The split exists so that
many agents can work at once without colliding: hunters only add issues, fixers only touch the
code for the issue they claimed.

It is the `game-loop` skill with step 4 ("fix what the harness got wrong") replaced by "file it".
Read `.claude/skills/game-loop/SKILL.md` and `.claude/skills/harness-audit/SKILL.md` first; this
file only adds what is different. `CLAUDE.md` and `docs/STANDARDS.md` still apply in full.

**What you are hunting:** decisions the model did not get to make, made blind, or made and had
thrown away. The rule from `harness-audit` holds: a finding is a HARNESS defect unless the prompt
shows the model had what it needed. Bad play by a model that was shown everything is a puzzle
candidate, never an issue.

**The sheet is not enough.** On the claude -p lane in October 2026, `match_review.py` came back
clean on three games that between them held six real defects: alternative costs never offered,
Forge AI picking which card a cost exiled, Ponder's order reversed, a shuffle ask that undid the
order, Cabal Therapy naming a card from the wrong hand, zero tokens recorded. Every one was found
by reading rows. A game you only ran the scripts on has not been hunted.

## 0. Once per repository: the labels

```bash
gh label create harness-defect --color B60205 --description "Harness defect found by reading a game" 2>/dev/null
gh label create sev:blocker  --color 5C0000 2>/dev/null
gh label create sev:high     --color D93F0B 2>/dev/null
gh label create sev:medium   --color FBCA04 2>/dev/null
gh label create sev:low      --color C2E0C6 2>/dev/null
gh label create claimed      --color 1D76DB --description "A fixer is on it; see its CLAIM comment" 2>/dev/null
gh label create hunt-log     --color BFD4F2 2>/dev/null
gh label create needs-owner  --color 5319E7 --description "Needs the owner's decision before anyone fixes it" 2>/dev/null
gh label create cannot-reproduce --color EEEEEE 2>/dev/null
```

Then make sure one open issue titled `Defect hunt: game log` with the label `hunt-log` exists
(create it if not, and pin it). Each game gets one comment there (step 6).
Use the GitHub MCP tools instead of `gh` where that is what your session has.

## 1. Before each game

Everything in `game-loop` step 0 (`uptime`, no game JVM before a rebuild, clean tree, tail of
`docs/harness/GAME-LOOP-LOG.md`), plus what the backlog already knows, so you do not rediscover it:

```bash
gh issue list --label harness-defect --state all --limit 200 --json number,title,state,labels
gh issue view <hunt-log issue> --comments | tail -80     # which decks and seeds were played last
git log --oneline -15 origin/main                        # fixes that landed since
```

Build from current `origin/main` (`game-loop` step 4.4 for the commands), never from a fixer's
branch, so a defect you file is a defect on main. Note the short sha; every issue cites it.

## 2. Play one game

The command is `game-loop` step 1. Choose the lane by what you are allowed to spend:

- **Claude CLI (default where `claude` is logged in):** `--smart-a haiku --fast-a haiku`. No API
  key and no egress flag. Never opus. This is the lane the six defects above were found on.
- **OpenRouter:** only the model the owner has authorised (`docs/STANDARDS.md` section 6), always
  with `--budget-usd`, and only when `secrets.env` already holds the egress opt-in. Never set
  `OPENROUTER_ALLOW_EGRESS` yourself.

Pick the deck the hunt log shows least played, and vary which seat it takes. A defect usually
belongs to a card type (alternative costs, library ordering, card naming, tap costs), and one deck
shows few. Use a seed outside the reserved blocks (`eval/seed_policy.py`) that the hunt log has not
used. Write to `runs/hunt/g<NN>` and play one game at a time; the next one starts after this one
is filed.

## 3. Read it

Run the sheet (`game-loop` step 2) and record its counts. Then do `game-loop` step 3 in full: the
log top to bottom, then every turn of the seat, row by row, asking its six questions. For each row
you open:

```bash
R=runs/hunt/g$N/game-<seed>-<swap>.jsonl
sed -n '<line>p' $R | python3 -c 'import json,sys; r=json.load(sys.stdin)
print({k: r.get(k) for k in ("seat","turn","phase","type","tier","fallback","choice","n_options")})
print("OPTIONS:", *r.get("options") or [], sep="\n  ")
print("REPLY:", r.get("raw_reply")); print("PROMPT:\n" + (r.get("prompt") or "<none>"))'
```

Look hardest where the October games found defects: every cost that is paid with a choice (exile,
return, tap, sacrifice, discard), every alternative or optional cost, every ordering of cards, every
ask that follows another ask (fast and smart tiers are separate conversations and do not see each
other), every naming or choosing of a card or player, and every place the log does something the
reply did not say.

## 4. Turn each finding into a defect, or drop it

For every candidate, before you write anything:

1. **Classify it** with the `harness-audit` table and its three questions (was the fact on the
   screen, was the action on the menu, was the model asked). MODEL findings go to the game's log
   comment as PUZZLE lines, not into the backlog.
2. **Find what should have happened, in the engine.** Read the card script under
   `forge-gui/res/cardsfolder/` and the engine class that resolves it in the pinned checkout
   (`FORGE_COMMIT`). "Should" comes from the card's text and the engine, never from memory.
3. **Find where the harness made it happen.** The method or branch in `forge-overlay/` that built
   the menu, wrote the prompt, parsed the reply or paid the cost, as `path:line` at the sha you
   built. Grep the `fallback` string or the log line; most decisions route through
   `PlayerControllerLLM` in `LlmSpikeGui.java` or `LlmCostDecision.java`. If you cannot find it
   in a reasonable search, say so under **Where** rather than guess.
4. **Check it is new.** Search the backlog (open and closed), `GAME-LOOP-LOG.md` OPEN lines, and
   the "Not done" section of recent PRs. If it is already filed, add a comment to that issue with
   the new occurrence (run, row, deck, sha) instead of a new issue: how often a defect fires is how
   fixers choose. If a closed issue's defect is back, reopen it with the evidence.
5. **One defect per issue.** Two symptoms with one cause are one issue; one symptom with two
   causes is two. A fixer must be able to close it with one PR.

### Severity

| Label | When |
|---|---|
| `sev:blocker` | Forge AI made the choice (`tier=ai`, `[FALLBACK->AI]`, or an engine AI paying a cost the seat should pick); a decision missing from the record; or the game did something other than what the reply chose (an order reversed, another target executed) |
| `sev:high` | the model was never asked a real choice (TAKEN, `auto` on a multi-option choice), the right action was never on the menu, or a tool request was REFUSED or FAILED |
| `sev:medium` | the model was asked but not shown the fact the decision turns on; a clear answer lost to the parser; a truncation that repeats |
| `sev:low` | the record is wrong but no decision was (tokens, stamps, served model); cosmetic prompt text |

Add `needs-owner` when the fix is a choice only the owner can make (a budget, a model cap, a
change of what counts as a condition). Fixers skip those.

## 5. File the issue

Title: `<kind>: <what goes wrong, in the game's words>` where kind is one of `not-asked`,
`not-offered`, `not-shown`, `wrong-effect`, `parse`, `tool`, `budget`, `record`, `infra`.
Example: `wrong-effect: Ponder puts the card the seat names first third from the top`.
Labels: `harness-defect` and one `sev:` label.

The body must pass the **fresh-agent test**: someone with only `main` and this issue, no run
directory and no memory of the game, can tell where the defect lives, reproduce it, and recognise
the fix. `runs/` is never committed, so everything the fixer needs is pasted into the issue.

~~~markdown
## Defect
One sentence: what the harness does wrong, in general terms (not just this game).

## What happened
Game: `runs/hunt/gNN`, seed S, swap 0/1, <deck> (seat) vs <deck> (Forge AI), lane `haiku` (claude -p),
built from main @ <sha>. Turn T, decision `<type>`, jsonl line L.
What the model was shown, chose, and what the game then did, in two or three sentences.

## What should have happened
What a correct harness does here, and why: quote the card text and cite the engine source
(`forge-gui/res/cardsfolder/p/ponder.txt`, `forge-game/.../SomeEffect.java:123`).

## Evidence
<details><summary>jsonl line L (trimmed)</summary>

```json
{the row's seat, turn, type, tier, fallback, choice, options, raw_reply; the prompt cut to the lines that matter}
```
</details>

Log lines around it, verbatim (`game-S-swap.log:N-M`).

## Where
`forge-overlay/.../LlmSpikeGui.java:1234` `methodName`: what it does that causes this.
Or: "Not located. Searched for <strings>."

## Reproduce without the game
The cheapest way to show it, in order of preference:
1. A unit test: which test class, the fixture (cards, zones, the ask), and the assertion that fails on main today.
2. A zero-model game that reaches it: `--controller-a random` with a deck holding the card, and what to grep for.
3. Only if neither reaches it: a model-lane game, and which card has to be drawn.

## Fixed when
The observable result: the row that should exist, the option that should be on the menu,
the log line that should follow the reply. A fixer's test asserts this.

## Detector gap
Which of `match_review.py` / `tool_audit.py` / `defect_analyzer.py` should have flagged it and
the signal it could use, or "a reading-only defect" when no script could see it.

## Seen
- runs/hunt/gNN, line L, main @ <sha>
~~~

Copy the game's run directory to `/mnt/project-files/hunt-runs/g<NN>/` when that folder exists, and
add the path under **Seen**: fixers in the same project can then open the full rows. The issue
must still stand without it.

Nothing goes in an issue that is not game data: no API keys, no contents of `secrets.env`, no
local paths outside the repository.

## 6. Log the game, then the next

One comment on the `Defect hunt: game log` issue, in the `GAME-LOOP-LOG.md` entry format, citing
issues instead of commits:

```
### g12  2026-10-14  seed 71  Elves (seat) vs Death and Taxes (Forge AI)  lost T9  haiku  main @ 3f1c2aa
Sheet: TAKEN 0, LOST 0, TOOL 0, SLOW 0; analyzer PASS.
- FILED  #41 sev:blocker  Heritage Druid's tap cost is paid by Forge AI (jsonl:88)
- SEEN   #37 again (jsonl:120)
- PUZZLE T6 attacked Quirion Ranger into an untapped Thalia with a pump in hand (jsonl:131)
- clean  mulligan, blocks, the two scries, every target
- not read  the cleanup discards (say what you skipped)
```

Then go to step 1. Stop when the budget the owner gave is spent, or when three games in a row put
nothing new in the backlog; say which in the thread.

## What a hunter never does

- Edit `forge-overlay/`, commit, push or open a PR. Not even a one-line fix: a fixer may already
  have claimed it, and the split is what keeps the agents from colliding.
- Rebuild or run acceptance while any game runs on the same machine, its own or another
  agent's (`pgrep -f LlmSpikeHeadless`).
- File what it has not confirmed in the row and the engine. A wrong issue costs a fixer a whole
  cycle. If unsure whether it is the harness or the model, the row decides; if the row cannot,
  say so in the issue and use `sev:low`.
- Launch a batch. One game, read, filed, then the next.
