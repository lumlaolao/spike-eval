# Contributing

Bug reports, fixes and new puzzles are welcome. This page says how to build the project, check a change and open a
pull request. The last section holds the stricter rules that the maintainer and the coding agents working here
follow; you do not need them for a first contribution.

## Reporting a problem

Open an issue. The most useful reports say which deck and seed the game used, what the model was asked, and what
happened instead of what should have. If the game wrote a run directory, the row in its `game-*.jsonl` is the best
evidence. For anything involving keys or data leaving your machine, see [SECURITY.md](.github/SECURITY.md) instead.

## Setting up

```bash
bash setup.sh                 # Forge at the pinned commit, the harness on top, the build
bash scripts/install_hooks.sh # once per clone: checks each commit message
```

You need Java 17+, Maven 3.8.1+, git and Python 3. The build by hand is in the
[README](README.md#build-and-test-by-hand).

## Where to make a change

- **Java** lives in `forge-overlay/`. Never edit the Forge checkout itself (`~/code/forge` by default): `install.sh`
  overwrites it. Edit the overlay, then install and build again ([Source of truth](#source-of-truth) has the
  commands).
- **Changes to Forge's own files** are diffs in `engine-patches/`, each listed in `docs/harness/ENGINE-PATCHES.md`.
- **Python** lives in `eval/`. Each script has its tests in `eval/tests/test_<name>.py`.

## Checking a change

```bash
python3 eval/tests/test_<name>.py        # one Python test file
bash eval/harness_acceptance.sh          # everything: compile, all tests, fixed-player games
```

Acceptance needs no API key and costs nothing. You never need a paid model game to contribute. If your change needs
one to be verified, say so in the pull request and the maintainer will run it.

## Opening a pull request

1. Branch from `main` as `type/short-name`, for example `fix/own-trigger-window`.
2. Write the commit subject as `type(scope): what is now true`, at most 100 characters.
3. In the pull request, say what changed, why, and what you ran to check it. `python3 scripts/pr_format.py new-pr`
   prints the body template the gate expects; if you skip it, the maintainer will fit your text to it.

The maintainer runs the review gate and merges. Pull requests are merged with a merge commit, never squashed.

Stage files by name, never `git add -A`: [What must never be committed](#what-must-never-be-committed) has the list.

## For the maintainer and coding agents

The rest of this page is the full process. [docs/STANDARDS.md](docs/STANDARDS.md) is the complete rule book, and
[CLAUDE.md](CLAUDE.md) is what a coding agent reads first.

### The git rule (non-negotiable)

**Every change lands through a pull request, and PRs are merged with a merge
commit. Never squash. Never rebase-merge.**

- Repo settings enforce the **merge method**: merge commits are the only enabled
  method (`allow_merge_commit=true`, `allow_squash_merge=false`,
  `allow_rebase_merge=false`). Squash and rebase-merge are disabled at the repo
  level, so they cannot be used even by mistake.
- Every change lands through a pull request and `scripts/pr_merge.sh`, which
  refuses until the gate in `docs/STANDARDS.md` section 1 passes. Do not push to
  `main` directly.
- History is meant to be readable as a series of reviewed units. A squash throws
  away the intermediate commits that show *how* a change was validated; this repo
  has repeatedly needed that trail (the shadow-labeler build-swap that compared a
  build against itself, the September work that sat uncommitted). Keep it.
- Branch, commit and PR names, the PR body and the merge gate are in
  [docs/STANDARDS.md](docs/STANDARDS.md) sections 1 and 2 (branch `type/short-name`,
  e.g. `fix/own-trigger-window`).

```bash
git switch -c fix/<slug> origin/main
# ... edit, validate, commit (the hook from scripts/install_hooks.sh checks each message) ...
git push -u origin fix/<slug>
python3 scripts/pr_format.py new-pr > /tmp/pr.txt   # title on line 1, then the body skeleton
# fill in every section; save the body (without the title line) as body.md, then open the PR over REST
# (`gh pr create` uses GraphQL, which cloud sessions are refused):
gh api -X POST repos/{owner}/{repo}/pulls -f head=fix/<slug> -f base=main -f title="<line 1>" -F body=@body.md
# acceptance and the three review skills on the final head, each recorded on the PR:
python3 scripts/pr_format.py record-acceptance <n> <sha> <OUT dir> <passed> <total> "<note>"
python3 scripts/pr_format.py record-review <n> <sha> pr-review <verdict> "<note>"   # and simplify, standards
bash scripts/pr_merge.sh <n>             # the merge gate; merges with a merge commit
```

The `pr-format` skill (`.claude/skills/pr-format/SKILL.md`) has the verdicts each skill may record.

### Source of truth

`forge-overlay/` is the source of truth. **Never edit files inside the Forge checkout (`$FORGE_HOME`, `~/code/forge` by default)** —
they are overwritten by `install.sh`. Workflow for any harness change:

```bash
# 1. edit forge-overlay/.../forge/spike/LlmSpikeGui.java
FORGE_HOME=~/code/forge bash install.sh                # 2. copy the overlay into the checkout
(cd ~/code/forge && mvn -q -pl forge-gui-desktop -am install -o -DskipTests)   # 3. build (install, not compile)
```

### What must never be committed

- `secrets.env` (git-ignored) — the real `OPENROUTER_API_KEY` never enters history.
- generated training data under `datasets/` (only each corpus's `meta.jsonl` and README are kept, as the
  seed record the held-out guard reads) and new `runs/` output beyond the JSONL evidence a doc cites.
  Stage files explicitly; `git add -A` at the repo root is always wrong.
- `*.mp4` recordings — archive out-of-band.
- Anything under `models/` or `.venv-mlx/`.

### Validation ladder (cheapest first — always start at the bottom)

0. `bash eval/harness_acceptance.sh` — isolated compile, every Java/Python test
   with a declared-vs-run count gate, pinned zero-model canaries with a class-load
   proof, and loopback fake-model games. Never touches the Forge checkout.
1. `GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor bash "$FORGE_HOME/run-llm-headless.sh"`
   (the copy `install.sh` puts in the checkout, which it runs from) — the regression
   canary (identity A wins after 108 turns, every time).
   For play/attack/block paths use the `random` arm — `floor` short-circuits
   before the menu is built.
2. LLM code paths on the free loopback fake model, as acceptance runs them:
   `python3 eval/fake_model_server.py --port 0 --policy random --answer-tool` (it prints
   `LISTENING <port>`), then the launcher with
   `SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model`
   and `OPENROUTER_BASE_URL=http://127.0.0.1:<port>/v1`. No key, no egress, no cost.
3. A paid game only on the owner's model (`CLAUDE.md`, "Owner rules"), one at a time
   with the `game-loop` skill and `--budget-usd`, the cost stated first.

**Before trusting a green gate, say what would have made it fail.** If you can't,
it isn't evidence.

### Review

Every PR: the `pr-review`, `simplify` and `standards` skills on the diff, with their
verdicts recorded for the head commit ([docs/STANDARDS.md](docs/STANDARDS.md) section 1).
`eval/defect_analyzer.py` is the merge gate for eval runs (`ai_tier_count=0`,
`silent_drop_total=0`); never weaken it to make a run pass.
