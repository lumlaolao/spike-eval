# Contributing

## The git rule (non-negotiable)

**Every change lands through a pull request, and PRs are merged with a merge
commit. Never squash. Never rebase-merge.**

- Repo settings enforce the **merge method**: merge commits are the only enabled
  method (`allow_merge_commit=true`, `allow_squash_merge=false`,
  `allow_rebase_merge=false`). Squash and rebase-merge are disabled at the repo
  level, so they cannot be used even by mistake.
- The **PR requirement** is a team rule, not a server-enforced gate: GitHub
  branch protection is unavailable on this private repo without GitHub
  Pro/Enterprise (the API returns 403). Do not push to `main` directly.
- History is meant to be readable as a series of reviewed units. A squash throws
  away the intermediate commits that show *how* a change was validated; this repo
  has repeatedly needed that trail (the shadow-labeler build-swap that compared a
  build against itself, the September work that sat uncommitted). Keep it.
- Branch names: `<area>/<short-slug>`, e.g. `harness/observation-fidelity`,
  `eval/pzl-roster-local`, `chore/pr-workflow`.

```bash
git checkout -b harness/<slug> main
# ... edit, validate, commit ...
git push -u origin harness/<slug>
gh pr create --base main --fill
gh pr merge <n> --merge      # merge commit, the only allowed method
```

## Source of truth

`forge-overlay/` is the source of truth. **Never edit files inside the Forge checkout (`$FORGE_HOME`: `~/code/forge` on the Mac, `/root/forge` in the cloud)** —
they are overwritten by `install.sh`. Workflow for any harness change:

```bash
# 1. edit forge-overlay/.../forge/spike/LlmSpikeGui.java
bash install.sh                                        # 2. copy overlay into $FORGE_HOME
cd $FORGE_HOME && mvn -q -pl forge-gui-desktop -am install -o   # 3. build (install, not just compile)
```

## What must never be committed

- `secrets.env` (git-ignored) — the real `OPENROUTER_API_KEY` never enters history.
- generated training data under `datasets/` (only each corpus's `meta.jsonl` and README are kept, as the
  seed record the held-out guard reads) and new `runs/` output beyond the JSONL evidence a doc cites.
  Stage files explicitly; `git add -A` at the repo root is always wrong.
- `*.mp4` recordings — archive out-of-band.
- Anything under `models/` or `.venv-mlx/`.

## Validation ladder (cheapest first — always start at the bottom)

0. `bash eval/harness_acceptance.sh` — isolated compile, every Java/Python test
   with a declared-vs-run count gate, pinned zero-model canaries with a class-load
   proof, and loopback fake-model games. Never touches the Forge checkout.
1. `GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor bash run-llm-headless.sh`
   — the regression canary (identity A wins after 108 turns, every time).
   For play/attack/block paths use the `random` arm — `floor` short-circuits
   before the menu is built.
2. `SMART_MODEL=haiku FAST_MODEL=haiku` for LLM code paths. Never test with opus.
3. Frontier models only for user-requested showcases.

**Before trusting a green gate, say what would have made it fail.** If you can't,
it isn't evidence.

## Review

Non-trivial changes: stage, run the `verifier` agent against `git diff --cached`,
address findings, then open the PR. `eval/defect_analyzer.py` is the merge gate
for eval runs (`ai_tier_count=0`, `silent_drop_total=0`); never weaken it to make
a run pass.
