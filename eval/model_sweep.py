#!/usr/bin/env python3
"""model_sweep.py — per-model harness compatibility sweep.

Loops through a roster of model specs and plays each one game as seat A
(llm) against the free floor arm as seat B, through run_batch.py, then runs
defect_analyzer.py on each model's output dir and emits a per-model verdict
table (sweep-verdicts.json + stdout).

"Plays correctly" per model means: game completes with a winner, zero
tier="ai" rows, zero silent drops, no unparsed-after-retry fallbacks, and
the new infra fields (attempts / finish_reason / retry_ms) show the
per-model config actually engaged (e.g. no truncation-loop, bounded
retries). Any model failing these gets a FAIL row with the evidence path —
the fix loop picks it up from there.

Usage:
    python3 eval/model_sweep.py [--out runs/improve/model-sweep] \
        [--models spec,spec,...] [--seed 201] [--cap-minutes 20]

Requires OPENROUTER_API_KEY (+ OPENROUTER_ALLOW_EGRESS=1) in the
environment or in <repo>/secrets.env (auto-loaded like tournament.py does).
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

from aggregate import unscorable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

DEFAULT_MODELS = [
    "openrouter:google/gemini-3.5-flash",
    "openrouter:qwen/qwen3.7-max",
    "openrouter:deepseek/deepseek-v4-pro",
    "openrouter:moonshotai/kimi-k3",
    "openrouter:x-ai/grok-4.5",
    "openrouter:openai/gpt-5",
    "openrouter:anthropic/claude-opus-4.8",
    "openrouter:anthropic/claude-fable-5",
]


def load_secrets():
    path = REPO_ROOT / "secrets.env"
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def short_name(spec):
    return spec.split("/")[-1]


def analyze(out_dir):
    """Run defect_analyzer.py; return (metrics dict or None, hard_pass bool)."""
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "eval" / "defect_analyzer.py"), str(out_dir)],
        capture_output=True, text=True)
    metrics_path = out_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else None
    return metrics, r.returncode == 0


def plumbing_checks(out_dir):
    """Per-model plumbing counts over the game jsonl in out_dir. A game the model did not fully play (a dead bridge,
    a cut log: aggregate.unscorable, #430) is not a completed game."""
    checks = {"game_completed": False, "unparsed_fallbacks": 0, "bridge_down_fallbacks": 0,
              "max_attempts": 0, "truncation_retries": 0, "decisions": 0}
    for j in out_dir.glob("*.jsonl"):
        for line in j.read_text().splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("t") == "game" and d.get("winner") and not unscorable(dict(d, _file=j.name), out_dir):
                checks["game_completed"] = True
            if d.get("t") != "decision":
                continue
            checks["decisions"] += 1
            fb = d.get("fallback") or ""
            if "unparsed" in fb:
                checks["unparsed_fallbacks"] += 1
            if "bridge-down" in fb:
                checks["bridge_down_fallbacks"] += 1
            checks["max_attempts"] = max(checks["max_attempts"], d.get("attempts") or 0)
            if d.get("finish_reason") == "length":
                checks["truncation_retries"] += 1
    return checks


def sweep_one(spec, args, seed):
    name = short_name(spec)
    out_dir = pathlib.Path(args.out) / name
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(REPO_ROOT / "eval" / "run_batch.py"),
        "--seeds", str(seed),
        "--controller-a", "llm", "--smart-a", spec, "--fast-a", spec,
        "--controller-b", "floor",
        "--deck-a", str(REPO_ROOT / "eval" / "decks" / "ur-delver.txt"),
        "--deck-b", str(REPO_ROOT / "eval" / "decks" / "burn.txt"),
        "--name-a", "UR Delver", "--name-b", "Burn",
        "--out", str(out_dir),
        "--cap-minutes", str(args.cap_minutes),
        "--exploratory",
    ]
    print(f"[sweep] {name}: seed={seed} -> {out_dir}", flush=True)
    game = subprocess.run(cmd, capture_output=True, text=True)
    (out_dir / "run_batch.out").write_text(game.stdout + "\n--- stderr ---\n" + game.stderr)

    verdict = {"model": spec, "out": str(out_dir), "game_rc": game.returncode}
    metrics, hard_pass = analyze(out_dir)
    verdict["analyzer_hard_pass"] = hard_pass
    if metrics:
        verdict["metrics"] = metrics
    checks = plumbing_checks(out_dir)
    verdict["checks"] = checks
    verdict["pass"] = (game.returncode == 0 and hard_pass and checks["game_completed"]
                       and checks["unparsed_fallbacks"] == 0)
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="runs/improve/model-sweep")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--seed", type=int, default=201)
    ap.add_argument("--cap-minutes", type=float, default=20)
    args = ap.parse_args()

    load_secrets()
    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("OPENROUTER_API_KEY not set (secrets.env not found?) — refusing llm sweep.")
    if os.environ.get("OPENROUTER_ALLOW_EGRESS") != "1":
        sys.exit("OPENROUTER_ALLOW_EGRESS=1 not set — egress consent required (see secrets.env.example).")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    verdicts = []
    for i, spec in enumerate(models):
        verdicts.append(sweep_one(spec, args, args.seed + i))

    out_root = pathlib.Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "sweep-verdicts.json").write_text(json.dumps(verdicts, indent=1))
    print("\n=== MODEL SWEEP VERDICTS ===")
    for v in verdicts:
        c = v["checks"]
        print(f"{'PASS' if v['pass'] else 'FAIL':4}  {short_name(v['model']):24} "
              f"decisions={c['decisions']:3} unparsed={c['unparsed_fallbacks']} "
              f"bridge_down={c['bridge_down_fallbacks']} max_attempts={c['max_attempts']} "
              f"trunc_retries={c['truncation_retries']} completed={c['game_completed']}")
    failed = [v for v in verdicts if not v["pass"]]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
