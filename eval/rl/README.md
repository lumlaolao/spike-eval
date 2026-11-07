# eval/rl: the harness as an RL environment

`MtgEnv` (`mtg_env.py`) exposes a Forge puzzle or game as `reset` / `step` with the engine's own verdict as the
reward. `rollout.py` plays episodes with a policy and writes trajectories for GRPO-style training and for
rejection-sampling SFT. Stdlib only, no model call, no egress. Design and limits:
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

Settings are pinned so that every request is a decision: `MEMORY_TOOLS=0 NATIVE_TOOLS=0 RULES_TOOL=0 LOOKAHEAD=0`,
`ANSWER_TOOL=1`. A retried request (same messages) is the same decision: it joins the parked one, and it is counted in
`info["deduped"]`.

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

On `done`, `step` returns `(None, reward, True, info)`. `info` has `valid` (False: do not train on this episode: a
capped game, a draw, a missing row, a step timeout), `status`, `solver_actions`, `decisions`, `deduped`,
`step_ms_median`, `step_ms_max`, `startup_s`, `episode_s`, `jvm_rc`.

`reset(seed=<n>, deck=<file>)` runs a full game against Forge's AI with the same machinery (reward 1 when identity
A wins). It has only been exercised against the stub, not a JVM (see the research note).

One JVM per episode, one port per episode. Run the JVM only under the shared lock on a shared machine
(`~/.forge-agent-lock`); `rollout.py` does not take it.

## Trajectory format (`rollout.py`)

```
python3 eval/rl/rollout.py --puzzle P.pzl [--puzzle Q.pzl] --group-size 8 --policy random --out runs/rl/r.jsonl
```

One JSON object per line; an episode's step lines come first, then its episode line (stream-friendly, appended):

```
{"type":"step","episode_id":"drain_08bb#3","group":"drain_08bb","step":2,"kind":"decision",
 "messages":[...],"legal":{...},"action":2,"args":{"option":2}}
{"type":"episode","episode_id":"drain_08bb#3","group":"drain_08bb","puzzle":"...pzl","seed":4,"puzzle_id":"drain_08bb",
 "reward":0.0,"valid":true,"n_steps":9,"info":{...}}
```

`group` is the puzzle (one group = `--group-size` episodes of the same puzzle with attempt seeds 1..G). `args` is the
exact `answer` call made. Readers in `rollout.py`:

- `load_episodes(path)` -> episode records with their `steps`.
- `grpo_groups(episodes)` -> `{group: [{episode_id, reward, advantage, steps}]}` over valid episodes, with the
  group-relative advantage `(r - mean) / (std + 1e-6)`. A group with equal rewards (every hard puzzle under the
  random policy) has advantage 0 everywhere: no signal, so sample a policy that sometimes wins, or pick puzzles with
  a random-policy solve rate strictly between 0 and 1 (`baselines.random` in `eval/suites/*.json`).
  Train on each step's `(messages, args)` weighted by the episode's advantage (the policy's per-step log-prob of
  the `answer` call given `messages`).
- `sft_examples(episodes)` -> rejection sampling: for every valid solved episode, one chat example per step,
  `messages` plus the assistant `answer` tool call. Keep only reward 1.

The floor to report beside any trained policy's solve rate is the random policy's on the same puzzles (and `first`,
`pass`): `eval/rl` numbers without it are not scores (`.claude/rules/eval.md`). Seeds 501-525 and 20000-30099 are
eval-only (`eval/seed_policy.py`); puzzle splits are `heldout` / `open` in the suites: train on `open` only.

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
for puzzle in batch:
    for g in range(G):
        recs.append(rollout.run_episode(env, policy, puzzle_id, f"{puzzle_id}#{g}", out, {"puzzle": puzzle, "seed": g + 1}))
```

The JVM waits `call_timeout_ms` per request, then re-asks (the env dedupes) and after three attempts takes a fixed
legal option, which the result row records; keep `step_timeout_s` above the policy's slowest generation.
