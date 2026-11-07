#!/usr/bin/env python3
"""Run episodes of MtgEnv with a policy and write trajectories as JSONL (format: eval/rl/README.md).

    python3 eval/rl/rollout.py --puzzle eval/puzzles/hard/gen/drain_08bba3eb8d53.pzl --group-size 4 \\
        --policy random --out runs/rl/rollouts.jsonl

One JSON object per line, two kinds:
  {"type": "step", "episode_id", "group", "step", "kind", "messages", "legal", "action", "args", "accepted"}
  {"type": "episode", "episode_id", "run_id", "group", "puzzle"|"seed", "reward", "valid", "n_steps", "info"}
An episode's step lines come first, then its episode line, so a reader can stream. `load_episodes`,
`grpo_groups` and `sft_examples` read the file back. Reward, validity and the training rules: eval/rl/README.md.

A policy is any callable obs -> action (an int is {option}, a list is {options}, a dict is the answer's arguments);
`random` and `first` are built in; `module:function` loads one. Zero model calls. This script does not take the
shared JVM lock: wrap the command in it when other agents share the machine.
"""
import argparse
import importlib
import json
import random
import statistics
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # eval/
sys.path.insert(0, str(HERE))
import evalpath  # noqa: E402,F401
import mtg_env  # noqa: E402
import seed_policy  # noqa: E402
import puzzle_format  # noqa: E402


# ------------------------------------------------------------------ policies

def first_policy(obs):
    """The first legal option everywhere (the lowest numbers, at least one pick when a pick is allowed)."""
    lg = obs.get("legal") or {}
    field = lg.get("field", "option")
    opts = [o["n"] for o in lg.get("options", [])]
    if field == "option":
        return {"option": min(opts)} if opts else {"option": 1}
    if field == "options":
        k = min(max(lg.get("min", 0), 1), lg.get("max", len(opts)), len(opts))
        return {"options": sorted(opts)[:k]}
    return _fallback(lg, rng=None)


def make_random_policy(seed=0):
    """A uniformly random legal answer; its stream is keyed on `seed`, so an episode replays."""
    rng = random.Random(seed)

    def policy(obs):
        lg = obs.get("legal") or {}
        field = lg.get("field", "option")
        opts = [o["n"] for o in lg.get("options", [])]
        if field == "option":
            return {"option": rng.choice(opts) if opts else 1}
        if field == "options":
            lo, hi = lg.get("min", 0), min(lg.get("max", len(opts)), len(opts))
            return {"options": sorted(rng.sample(opts, rng.randint(min(lo, hi), hi)))}
        return _fallback(lg, rng)
    return policy


def _fallback(lg, rng):
    field = lg.get("field")
    if field == "keep":
        return {"keep": True if rng is None else rng.random() < 0.7}
    if field == "number":
        lo, hi = lg.get("min", 0), lg.get("max")
        return {"number": lo if (rng is None or hi is None) else rng.randint(lo, hi)}
    if field == "amounts":
        k, total = lg.get("count", 1), lg.get("total", 0)
        return {"amounts": [total] + [0] * (k - 1)}
    if field == "blocks":
        return {"blocks": []}
    if field == "card":
        return {"card": "Lightning Bolt"}
    return {"option": 1}


def load_policy(spec, seed=0):
    if spec == "random":
        return make_random_policy(seed)
    if spec == "first":
        return first_policy
    mod, _, fn = spec.partition(":")
    if not fn:
        raise SystemExit("--policy is random, first or module:function")
    return getattr(importlib.import_module(mod), fn)


# ------------------------------------------------------------------ rollout

def run_episode(env, policy, group, episode_id, out, reset_kwargs, max_steps=400, run_id=None, eval_only=()):
    """Play one episode, writing its step lines and then its episode line to `out`. Returns the episode record.
    A step line is written once the next observation shows whether the engine took the answer (`accepted`: False
    when that observation is a correction). The env is closed, and its JVM with it, if the policy raises."""
    try:
        obs = env.reset(**reset_kwargs)
        steps, reward, done, info = 0, 0.0, False, {}
        while not done:
            action = policy(obs)
            args = mtg_env.as_arguments(action, obs.get("legal"))
            line = {"type": "step", "episode_id": episode_id, "group": group, "step": obs["step"],
                    "kind": obs["kind"], "messages": obs["messages"], "legal": obs["legal"],
                    "action": action if not isinstance(action, tuple) else list(action), "args": args}
            steps += 1
            if steps >= max_steps:   # a runaway episode is cut and never trained on
                out.write(json.dumps(dict(line, accepted=True), sort_keys=True) + "\n")
                info = {"valid": False, "error": "max_steps"}
                env.close()
                break
            obs, reward, done, info = env.step(action)
            out.write(json.dumps(dict(line, accepted=info.get("accepted", True)), sort_keys=True) + "\n")
    except BaseException:
        env.close()
        raise
    rec = {"type": "episode", "episode_id": episode_id, "run_id": run_id, "group": group, "reward": reward,
           "valid": bool(info.get("valid", False)), "n_steps": steps, "info": info}
    if eval_only:
        rec["eval_only"] = list(eval_only)
    rec.update({k: reset_kwargs[k] for k in ("puzzle", "seed") if k in reset_kwargs})
    rec["puzzle_id"] = Path(reset_kwargs["puzzle"]).stem if "puzzle" in reset_kwargs else None
    out.write(json.dumps(rec, sort_keys=True) + "\n")
    out.flush()
    return rec


def run_group(env, policy_spec, group, resets, out, policy_seed=0, max_steps=400, run_id=None, eval_only=()):
    """One group: len(resets) episodes on the same start (a puzzle) with different seeds. Returns the records.
    `run_id` (a uuid per invocation of this script) is in every episode id, so a crashed run's orphan step lines
    cannot be taken for the steps of a later episode with the same group and index."""
    run_id = run_id or uuid.uuid4().hex[:8]
    recs = []
    for i, kw in enumerate(resets):
        policy = load_policy(policy_spec, seed="%s/%s/%s" % (policy_seed, group, i))
        recs.append(run_episode(env, policy, group, "%s:%s#%d" % (run_id, group, i), out, kw, max_steps, run_id,
                                eval_only))
    return recs


# ------------------------------------------------------------------ seed policy

def puzzle_split(path):
    """'open' or 'heldout' for a .pzl: the split of the suite record that lists it (a suite's pzl is a repository
    path, or forge:<name>.pzl for Forge's own), else the id's own hash (puzzle_format.split_for) on the file stem."""
    p = Path(path)
    for suite in sorted((HERE.parent / "suites").glob("puzzles-*.json")):
        for rec in json.loads(suite.read_text(encoding="utf-8")).get("puzzles", []):
            pzl = rec.get("pzl", "")
            if pzl.startswith("forge:"):
                hit = pzl[len("forge:"):] == p.name
            else:
                hit = (puzzle_format.REPO / pzl).resolve() == p.resolve()
            if hit:
                return rec["split"]
    return puzzle_format.split_for(p.stem)


def eval_only_reasons(puzzles, seeds, attempt_seeds):
    """Why this rollout's data must not train a model: a heldout puzzle, a seed in a reserved eval block."""
    why = ["heldout puzzle %s" % p for p in puzzles if puzzle_split(p) == "heldout"]
    for block, hit in seed_policy.check_seeds(list(seeds) + list(attempt_seeds)).items():
        why.append("reserved seed block %s: %s" % (block, hit[:5]))
    return why


# ------------------------------------------------------------------ readers for training

def load_episodes(path):
    """[{episode record + "steps": [step lines]}], in file order. A trailing episode cut off mid-write is dropped,
    and so are the step lines of a run that died before its episode line: the next episode's step 1 clears them."""
    steps, eps = {}, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") == "step":
            if row.get("step") == 1:
                steps.clear()   # one episode is written at a time: whatever is pending belongs to a dead one
            steps.setdefault(row["episode_id"], []).append(row)
        elif row.get("type") == "episode":
            eps.append(dict(row, steps=steps.pop(row["episode_id"], [])))
    return eps


def trainable(e):
    """An episode a trainer may use: valid (no lost decision, no dead bridge, a verdict) and not marked eval-only."""
    return bool(e.get("valid")) and not e.get("eval_only") and not e.get("info", {}).get("lost_decisions")


def grpo_groups(episodes, normalize=True):
    """{group: [{"episode_id", "reward", "advantage", "steps"}]} for trainable episodes. The advantage is the
    group-relative one, (r - mean) / (std + 1e-6) (or r - mean when normalize is False). A group whose rewards
    are all equal, including one with a single valid episode left after the others were dropped, has advantage 0
    everywhere, i.e. no gradient (the usual all-fail group of a hard puzzle). Steps the engine refused stay in
    `steps` (flagged `accepted: false`): the trainer's loss decides what to do with them; `sft_examples` drops them."""
    groups = {}
    for e in episodes:
        if trainable(e):
            groups.setdefault(e["group"], []).append(e)
    out = {}
    for g, eps in groups.items():
        rs = [e["reward"] for e in eps]
        mean = statistics.fmean(rs)
        std = statistics.pstdev(rs)
        out[g] = [{"episode_id": e["episode_id"], "reward": e["reward"],
                   "advantage": (e["reward"] - mean) / (std + 1e-6) if normalize else e["reward"] - mean,
                   "steps": e["steps"]} for e in eps]
    return out


def sft_examples(episodes):
    """Rejection sampling: one chat example per accepted decision of every trainable, solved episode: the messages
    the policy saw, and the `answer` call it made as the assistant turn. A step the engine refused is not an
    example (its answer was wrong, whatever the episode's reward)."""
    ex = []
    for e in episodes:
        if not (trainable(e) and e.get("reward", 0) > 0):
            continue
        for s in e["steps"]:
            if s.get("accepted") is False:
                continue
            call = {"id": "call_answer_%d" % s["step"], "type": "function",
                    "function": {"name": "answer", "arguments": json.dumps(s["args"])}}
            ex.append({"episode_id": e["episode_id"], "step": s["step"],
                       "messages": s["messages"] + [{"role": "assistant", "content": None, "tool_calls": [call]}]})
    return ex


# ------------------------------------------------------------------ command line

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--puzzle", action="append", default=[], help="a .pzl file (repeatable)")
    ap.add_argument("--game-seed", action="append", type=int, default=[], help="a game seed (repeatable)")
    ap.add_argument("--deck", help="deck file for --game-seed")
    ap.add_argument("--group-size", type=int, default=1, help="episodes per puzzle (seeds 1..G) / per game seed")
    ap.add_argument("--policy", default="random", help="random | first | module:function")
    ap.add_argument("--policy-seed", default="0")
    ap.add_argument("--max-steps", type=int, default=400)
    ap.add_argument("--out", required=True, help="trajectory JSONL (refused when it has content, unless --append)")
    ap.add_argument("--append", action="store_true", help="add to a non-empty --out")
    ap.add_argument("--allow-eval-only", action="store_true",
                    help="play heldout puzzles / reserved seeds anyway, for a floor measurement: the episodes are "
                         "marked eval_only and the readers never train on them")
    ap.add_argument("--keep-out", action="store_true", help="keep the JVM scratch directories")
    ap.add_argument("--forge", help="Forge checkout (default $FORGE_HOME or /root/forge)")
    a = ap.parse_args(argv)
    if not a.puzzle and not a.game_seed:
        ap.error("give --puzzle or --game-seed")
    attempts = range(1, a.group_size + 1)
    game_seeds = [s + i for s in a.game_seed for i in range(a.group_size)]
    eval_only = eval_only_reasons(a.puzzle, game_seeds, attempts if a.puzzle else [])
    if eval_only and not a.allow_eval_only:
        ap.error("eval-only data, never training data (eval/seed_policy.py, docs/eval/PUZZLES.md): %s; "
                 "--allow-eval-only plays it marked eval_only" % "; ".join(eval_only))
    out_path = Path(a.out)
    if out_path.exists() and out_path.stat().st_size and not a.append:
        ap.error("%s is not empty: a rollout does not append unless --append" % a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]
    env = mtg_env.MtgEnv(launcher=mtg_env.JvmLauncher(forge_home=a.forge), keep_out=a.keep_out)
    recs = []
    try:
        with open(out_path, "a", encoding="utf-8") as out:
            for p in a.puzzle:
                resets = [{"puzzle": p, "seed": s} for s in attempts]
                recs += run_group(env, a.policy, Path(p).stem, resets, out, a.policy_seed, a.max_steps, run_id,
                                  eval_only)
            for s in a.game_seed:
                resets = [{"seed": s + i, "deck": a.deck} for i in range(a.group_size)]
                recs += run_group(env, a.policy, "game-%d" % s, resets, out, a.policy_seed, a.max_steps, run_id,
                                  eval_only)
    finally:
        env.close()
    for r in recs:
        print("%s reward=%s valid=%s steps=%d status=%s" % (r["episode_id"], r["reward"], r["valid"], r["n_steps"],
                                                           r["info"].get("status", r["info"].get("winner"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
