#!/usr/bin/env python3
"""Cheap-panel screen for UNVERIFIED rollout candidates: unanimity -> skip.

The rule this rests on (validated 2026-07-31, eval/pipelines/
panel_screen_report.py): on the frozen suite, "all 3 cheap panel models
agree on the answer" predicted frontier-free with precision 1.00 and zero
informative discards, and would skip ~33% of confirm budget on the verified
key population. Candidates have NO key yet, so the screen uses AGREEMENT,
not correctness: if the whole panel independently picks the SAME option, the
position is (a) almost certainly easy and (b) almost certainly not
frontier-informative — skip its ~153-game rollout confirm. Any split, any
parse failure, any transport error keeps the candidate (conservative: doubt
means confirm).

Egress: same boundary as everything else — requires OPENROUTER_ALLOW_EGRESS=1
(explicit human act) + OPENROUTER_API_KEY; refuses otherwise.

Usage:
    python3 eval/pipelines/screen_candidates.py SAMPLE.jsonl \
        --out-keep KEEP.jsonl --out-skip SKIP.jsonl \
        [--models m1,m2,m3] [--workers 8] [--max-tokens 1024]

Stdlib-only, Python 3.9.
"""

import argparse
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import prompt_contract   # noqa: E402
import replay_match      # noqa: E402

DEFAULT_MODELS = ("meta-llama/llama-3.1-8b-instruct",
                  "qwen/qwen-2.5-7b-instruct",
                  "mistralai/mistral-small-3.2-24b-instruct")
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sample", help="candidate rows (status=candidate) jsonl")
    ap.add_argument("--out-keep", required=True,
                    help="candidates the panel SPLIT on -> confirm these")
    ap.add_argument("--out-skip", required=True,
                    help="unanimous candidates -> skipped, with evidence")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--req-timeout", type=float, default=90.0)
    args = ap.parse_args(argv)

    if os.environ.get("OPENROUTER_ALLOW_EGRESS") != "1":
        print("REFUSING: screening sends game snapshots to openrouter.ai; "
              "set OPENROUTER_ALLOW_EGRESS=1 explicitly (holding a key is "
              "not consent).", file=sys.stderr)
        return 2
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY missing (source secrets.env by absolute "
              "path — worktrees cannot see it).", file=sys.stderr)
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    rows = []
    with open(args.sample, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    # Render once per candidate; unrenderable candidates are KEPT (doubt ->
    # confirm) with a note.
    work = []
    unrenderable = []
    for row in rows:
        try:
            rendered = prompt_contract.render(row)
        except ValueError as exc:
            unrenderable.append((row, str(exc)))
            continue
        work.append((row, rendered))

    lock = threading.Lock()
    votes = {}   # id -> {model: menu_number or None}

    def ask(task):
        (row, rendered), model = task
        try:
            text = replay_match._post_chat(
                OPENROUTER_ENDPOINT, model, rendered["messages"],
                args.max_tokens, args.req_timeout, api_key=api_key)
            parsed = prompt_contract.parse(text, rendered)
            num = parsed.get("menu_number")
        except Exception:
            num = None
        with lock:
            votes.setdefault(row.get("id"), {})[model] = num

    tasks = [((row, rendered), m) for (row, rendered) in work for m in models]
    if args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(ask, tasks))
    else:
        for t in tasks:
            ask(t)

    keep, skip = [], []
    for row, rendered in work:
        v = votes.get(row.get("id"), {})
        nums = [v.get(m) for m in models]
        unanimous = (len(nums) == len(models)
                     and all(n is not None for n in nums)
                     and len(set(nums)) == 1)
        row = dict(row)
        row["panel_screen"] = {
            "models": models,
            "choices": dict((m, v.get(m)) for m in models),
            "unanimous": unanimous,
            "rule": "unanimity-skips (validated 2026-07-31, precision 1.00 "
                    "on suite ground truth)",
        }
        (skip if unanimous else keep).append(row)
    for row, err in unrenderable:
        row = dict(row)
        row["panel_screen"] = {"models": models, "choices": {},
                               "unanimous": False,
                               "note": "unrenderable, kept: %s" % err}
        keep.append(row)

    for path, out_rows in ((args.out_keep, keep), (args.out_skip, skip)):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in out_rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
    total = len(keep) + len(skip)
    print("screen: %d candidates -> keep %d (%.0f%%) / skip %d unanimous "
          "(%.0f%% of confirm budget saved); %d unrenderable kept"
          % (total, len(keep), 100.0 * len(keep) / total if total else 0,
             len(skip), 100.0 * len(skip) / total if total else 0,
             len(unrenderable)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
