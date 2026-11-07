# eval/rl: the harness as an RL environment

`MtgEnv` (`mtg_env.py`) exposes a Forge puzzle or game as `reset` / `step` with the engine's own verdict as the
reward. `rollout.py` plays episodes with a policy and writes trajectories for GRPO-style training and for
rejection-sampling SFT. `eval_only.py` keeps eval data out of the training data. Stdlib only, no model call, no
egress. Design and limits:
`eval/research/2026-11-06-rlvr-env-feasibility.md`; measurements: `eval/research/2026-11-07-rl-play-env.md`.

## How it works

The game JVM (`LlmSpikeHeadless`, the real seat code, nothing in `forge-overlay/` changed) is told its model is a
loopback endpoint (`OPENROUTER_BASE_URL=http://127.0.0.1:<port>/v1`, `openrouter:rl/policy`; loopback needs neither
the egress opt-in nor a key, and the env removes both from the JVM's environment). Every decision the engine asks of
the seat is one blocking chat-completions request. The env's server parks it:

- **observation** = the request: `messages` (resent in full each time), the parsed legal set `legal` (via
  `fake_model_server.Policy.decision_props`, plus each option's text) and the `answer` tool `schema`.
- **action** = the `answer` call written back as the reply.
- **done** = the JVM exits; the reward is read from the puzzle result row (`solved` = 1) or the game line
  (`winner == "A"` = 1).

Settings are pinned so that every request is a decision (`mtg_env.DECISION_ONLY`): `MEMORY_TOOLS=0 RULES_TOOL=0
LOOKAHEAD=0`, `ANSWER_TOOL=1`, and `NATIVE_TOOLS=1` (the `answer` function travels with the native tools; with 0 the
seat is on the text lane and the env's tool-call reply is never read). The env replies only with `answer`, so the
lookup functions the request also offers are never called. The JVM's environment is the shell's minus every
`OPENROUTER*`, `PZL_*`, `FORK_*`, `CLAUDE_*` name and `run_batch.PINNED_ENV_KEYS` / `RUN_DEFINING_KEYS`
(`scrubbed_environ` raises if `run_batch` cannot be imported). The server accepts only loopback, and refuses a
request that carries an `Authorization` header.

A retried request is the same decision: while the ask is unanswered it joins the parked one (`info["deduped"]`).
A request byte-identical to the one just answered is replied with that answer and counted in
`info["deduped_after_answer"]`. The env cannot tell such a retry from a genuinely new decision with identical
messages (the messages carry the whole state, so that needs a state that did not change); that decision would be
answered with the previous action and not reach the policy. Such an episode is `valid=False` (the policy did not
choose that step); the count is 0 in a clean run.

```python
import sys; sys.path.insert(0, "eval/rl")
from mtg_env import MtgEnv
env = MtgEnv()                         # FORGE_HOME (default /root/forge) must be built; nothing is built here
obs = env.reset(puzzle="eval/puzzles/hard/gen/drain_08bba3eb8d53.pzl", seed=1)   # seed = the attempt seed
done = False
while not done:
    obs_next, reward, done, info = env.step({"option": 2})   # or 2, [1, 3], True, {"blocks": []}
    obs = obs_next
env.close()
```

`obs = {"episode", "step", "messages", "legal", "schema", "kind"}`.
`legal` is `{"field": "option", "options": [{"n": 1, "text": "PASS"}, ...]}`; `field` is one of `option`,
`options` (with `min`, `max`), `number` (`min`, `max`), `amounts` (`count`, `total`), `keep`, `blocks`, `card`
(the last three have no closed list). `kind` is `"correction"` when the engine refused the previous answer and
re-asks (the message list ends with the error). An action is the `answer` call's arguments (a dict), or an int
(`option` / `number`), a list (`options` / `amounts`), a bool (`keep`). An illegal action is not filtered by the
env: the engine refuses it and the next observation is a correction, which is itself a step.

A non-final `step` returns `info = {"step", "args", "accepted"}`: `accepted` is False when the next observation is a
correction, i.e. the engine refused `args`. The engine takes a second refusal of the same decision as lost and
decides with a fixed legal option (`AnswerTool.converse`, "two invalid replies").

On `done`, `step` returns `(None, reward, True, info)`. The reward is 1.0 for a **valid** solved puzzle / won game
(identity A), else 0.0. `info["valid"]` is False, and the episode must not be trained on, for: a capped game or a
draw, a missing result row or game line, a step timeout, a solve tainted by a deck-out, a decision lost to a dead
bridge (`bridge_down_decisions` in the puzzle row, `bridge_down_decisions_a` in the game line) or a routing filter
(`routing_refusals`, the puzzle row only: the game line has no such field), a decision the harness took with its fixed option after two refused answers
(`info["lost_decisions"]`, counted from the game's own decision rows, `answer_via == "lost"`), and a JVM that did
not exit 0 (a finished puzzle or game exits 0: `LlmSpikePuzzle`, `LlmSpikeHeadless`; the code is `info["jvm_rc"]`).
Also in `info`: `stamps` (the row's or game line's `engine_sha`, `overlay_sha`, `harness_epoch`,
`observation_version`, `pzl_sha256`, whichever it carries), `status`, `solver_actions`, `decisions`, `deduped`, `deduped_after_answer`, `corrections` (refused
steps), `step_ms_median`, `step_ms_max`, `startup_s`, `episode_s`.

## Eval-only data

`MtgEnv.reset` calls `eval_only.reasons` for every episode and raises `EnvError` unless `allow_eval_only=True`, in
which case the episode's `info["eval_only"]` lists the reasons and `rollout` writes them on the episode record; no
reader trains on such an episode. So a trainer's own loop hits the guard, not only the command line. It refuses:

- a **seed** in `eval/seed_policy.py`'s reserved blocks or in any bank `eval/seed_registry.py` marks eval-only (the
  STR-AI panel seeds 7800-7999 are in the registry only); a puzzle's attempt seed counts;
- a **deck** declared `held-out` in `eval/holdouts.json` (painter, sneak-and-show), by file or slug;
- a **puzzle** by its bytes: the file's sha256 is looked up among every suite record's `pzl_sha256`, so a renamed
  copy keeps its split; it is heldout if any record with those bytes says heldout, and a puzzle no record lists is
  refused too (its split is unknown).

A game with no `deck=` plays the launcher's default decks, which the guard cannot see: pass the deck.

`reset(seed=<n>, deck=<file>)` runs a full game against Forge's AI with the same machinery. It has run once against
a JVM (G1 in the research note: one game, a loss, 38 decisions) and otherwise against the stub.

One JVM per episode, one port per episode. Run the JVM only under the shared lock on a shared machine
(`~/.forge-agent-lock`); `rollout.py` does not take it.

## Trajectory format (`rollout.py`)

```
python3 eval/rl/rollout.py --puzzle P.pzl [--puzzle Q.pzl] --group-size 8 --policy random --out runs/rl/r.jsonl
```

One JSON object per line; an episode's step lines come first, then its episode line (stream-friendly):

```
{"type":"step","episode_id":"3f9a1c2e:drain_08bb#3","group":"drain_08bb","step":2,"kind":"decision",
 "messages":[...],"legal":{...},"action":2,"args":{"option":2},"accepted":true}
{"type":"episode","episode_id":"3f9a1c2e:drain_08bb#3","run_id":"3f9a1c2e","group":"drain_08bb","puzzle":"...pzl",
 "seed":4,"puzzle_id":"drain_08bb","reward":0.0,"valid":true,"n_steps":9,"info":{...}}
```

`group` is the puzzle (one group = `--group-size` episodes of the same puzzle with attempt seeds 1..G); in game mode
a group is G **different** games (seeds s..s+G-1, `group` = `game-<s>`), not G attempts at one start, so its
advantage compares different positions. A run refuses a group it would play twice (a puzzle given twice, game seeds
that overlap). The episode record carries `stamps`; `--append` refuses to add to a file whose episodes ran on other
stamps (a rebuilt engine, another interface epoch), and to a file that already mixes them. `args` is the
exact `answer` call made; `accepted` is False for a step the engine refused. The episode id starts with a run id (a
uuid per `rollout.py` invocation), and `--out` must be empty unless `--append`, so a crashed run's orphan step lines
cannot be glued to a later episode; `load_episodes` also drops pending step lines when a step 1 arrives, and an
episode whose steps are not numbered 1..`n_steps`. An episode is written whole when it ends, and `--append` starts a
new line if the file does not end in one (a crashed run's cut line). `rollout.py` refuses eval-only data (above);
`--allow-eval-only` plays it for a floor measurement. Readers in `rollout.py`:

- `load_episodes(path)` -> episode records with their `steps`.
- `grpo_groups(episodes)` -> `{group: [{episode_id, reward, advantage, steps}]}` over trainable episodes (valid, no
  lost decision, not `eval_only`), with the group-relative advantage `(r - mean) / (std + 1e-6)`
  (`normalize=False`: `r - mean`). 4 solved of 40 gives +3.0 and -0.33. A group with equal rewards (every hard puzzle
  under the random policy) has advantage 0 everywhere: no signal. So has a group left with **one** valid episode
  after the others were dropped (a lost decision, a dead bridge): its mean is its own reward. Pick puzzles with
  a random-policy solve rate strictly between 0 and 1 (`baselines.random` in `eval/suites/*.json`) and watch the
  count of dropped episodes: dropping the unlucky ones biases a group. `steps` holds only the steps the engine
  took (a step with `accepted: false` is dropped, as in `sft_examples`).
  Train on each step's `(messages, args)` weighted by the episode's advantage (the policy's per-step log-prob of
  the `answer` call given `messages`).
- `sft_examples(episodes)` -> rejection sampling: for every trainable solved episode, one chat example per
  **accepted** step, `messages` plus the assistant `answer` tool call. A step the engine refused is never an example.

The floor to report beside any trained policy's solve rate is the random policy's on the same puzzles (and `first`,
`pass`): `eval/rl` numbers without it are not scores (`.claude/rules/eval.md`). Train on `open` puzzles only (the suites' `split`) and on seeds
outside every eval-only block ("Eval-only data").

## Plugging in a trained policy

A policy is any callable `obs -> action`. For a model behind vLLM or HF transformers, turn the observation into the
prompt (`obs["messages"]`, tool schema `obs["schema"]`), sample, and map the output to an action. Constrained
decoding to `obs["legal"]` gives the mask:

```python
# my_policy.py
def policy(obs):
    lg = obs["legal"]
    if lg.get("field") == "option":
        allowed = [o["n"] for o in lg["options"]]
        n = my_model.sample_choice(obs["messages"], allowed)       # vLLM guided_choice / logits restricted
        return {"option": n}
    return rollout._fallback(lg, None)                              # keep / number / blocks: your own head
```

`python3 eval/rl/rollout.py --policy my_policy:policy ...` uses it from the command line, or in a trainer:

```python
env = MtgEnv(call_timeout_ms=900000, step_timeout_s=300)       # the JVM waits this long for a reply
for puzzle in batch:      # an eval-only puzzle or seed raises EnvError here (allow_eval_only=True marks it instead)
    for g in range(G):
        recs.append(rollout.run_episode(env, policy, puzzle_id, f"{puzzle_id}#{g}", out, {"puzzle": puzzle, "seed": g + 1}))
```

The JVM waits `call_timeout_ms` per request, then re-asks (the env dedupes) and after three attempts takes a fixed
legal option, which the result row records; keep `step_timeout_s` above the policy's slowest generation.
