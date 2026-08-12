#!/bin/bash
# Contract regression for the puz-v2 era, driven by a LOCAL model.
#
# WHY THIS EXISTS. Commit 5e3736cc minted a new contract era (puz-v2: the
# frozen STATIC_REFERENCE legend, the @N object-id explanation, the
# `PICKS: NONE` zero-pick rule, the parse-retry consequence). Every byte of it
# was implemented and unit-tested, and NONE of it had ever been shown to a
# live model: eval/puzzle_run.py rendered with the default profile and had no
# flag to say otherwise, so the scoring lane could only ever emit puz-v1.
# `--profile` closes that; this script is the thing that exercises it.
#
# A local endpoint is the right driver for this job for three reasons:
#   1. it costs nothing, so the run can be repeated until the contract is
#      right (the OpenRouter balance is exhausted as of 2026-08-02 anyway);
#   2. localhost is exempt from the egress gate BY DESIGN
#      (replay_match.egress_allowed) and a key is never sent to an on-box
#      endpoint, so no consent is spent to test plumbing;
#   3. the whole point is the PARSE/RETRY path, not Magic strength — a weak
#      model exercises it harder than a strong one.
#
# WHAT THIS IS NOT. The scores this prints are NOT leaderboard numbers. A
# locally-quantized model is a different artifact from the API model of the
# same name, and the quantization is part of the measurement. Anything quoted
# from here must carry the model tag and the quant, and must never share a
# leaderboard row with an API score. summary.json records profile,
# contract_sha, model and max_tokens; keep them together.
#
# KNOBS ARE DELIBERATELY LEFT ALONE. An earlier draft of this run proposed
# capping max_tokens "generously" and disabling the model's thinking mode.
# Both are wrong here, and the harness itself says why:
#   * the truncation ladder exists SO THAT the cap is not a guess. Its own
#     docstring records the cost of guessing: kimi-k3 measured 0.600 with 33%
#     interface failures at 4k tokens and 0.720 with 8% at 16k. Overriding the
#     default just moves the guess. max_tokens is stamped as provenance
#     because a different cap is a DIFFERENT MEASUREMENT.
#   * thinking-off is not reachable without corrupting the thing under test.
#     _post_chat sends {model, messages, temperature:0, max_tokens} and has no
#     reasoning toggle, so suppressing it means either injecting /no_think
#     into the byte-pinned system message (which changes render_sha and
#     destroys the regression) or editing a shared egress-gated function to
#     make the run more convenient.
#   * and a reasoning model at the default cap is the BEST available stress
#     test: hidden reasoning that eats the budget returns EMPTY, not
#     truncated, which is exactly the case the ladder's load-bearing ordering
#     exists for (0 firings across 18 finish_reason=length events when the
#     emptiness check ran first on the Java side).
# So: defaults, thinking on. If that produces a low score, that is a fact
# about a 4-bit laptop model, not a reason to retune the harness.
#
# k=1 IS NOT A SHORTCUT. _post_chat hardcodes temperature=0, so additional
# samples of the same position are duplicates, not information. pass_k is
# meaningless at k=1 and is not reported by this script.
#
# --workers 1 IS A CORRECTNESS REQUIREMENT, NOT A THROUGHPUT CHOICE. ollama
# defaults to OLLAMA_NUM_PARALLEL=1: the server serves exactly ONE request at
# a time and queues the rest. puzzle_run times each sample from submission, so
# under the default --workers 4 three of every four samples bank their QUEUE
# WAIT as latency. Measured on a 5-position subset that way: p50 50.2s / p90
# 74.7s and pass_1_within_cap 0.20, against a real per-sample model time of
# 6-13s on the same positions run serially. The whole solve_dynamics block —
# which exists to answer "was the first try right, and how long did it take" —
# is meaningless if it is really reporting scheduler backlog. Parallelism here
# would need OLLAMA_NUM_PARALLEL raised AND enough RAM for the extra KV cache
# per slot, which a 24GB box does not have at 30B.
#
# BOTH ERAS, PAIRED. The same positions are run under puz-v1 and puz-v2. The
# analytic floors are era-invariant (measured 2026-08-11 over all 65 v2.1
# positions: always_pass 0.1231, always_first 0.2615, random 0.2631, oracle
# 1.0000, identical under both profiles), because the profile changes only the
# SYSTEM message. So any v1-vs-v2 difference is model behavior, never a moved
# floor.
#
# Usage: bash eval/pipelines/local_contract_check.sh MODEL [POSITIONS] [N]
#   MODEL      ollama tag, e.g. qwen3:30b-a3b   (must already be pulled)
#   POSITIONS  positions jsonl (default eval/suites/puz-v2.1.jsonl)
#   N          --max-positions (default: unset = all 65)
#
# Reads nothing from the network but the local endpoint. Writes under
# runs/puz/local-<model>-<profile>/.
set -uo pipefail

MODEL="${1:?usage: local_contract_check.sh MODEL [POSITIONS] [N]}"
POSITIONS="${2:-eval/suites/puz-v2.1.jsonl}"
MAXPOS="${3:-}"
ENDPOINT="${ENDPOINT:-http://localhost:11434/v1}"
# MAX_TOKENS: leave UNSET for the primary measurement (harness default 512 +
# the ladder). Set it only for the deliberate second measurement described
# below. Either way puzzle_run stamps max_tokens into summary.json, so the two
# runs are never confusable.
#
# WHY A SECOND MEASUREMENT IS LEGITIMATE HERE. Measured on a 5-position subset
# with qwen3:4b: the ladder fires on 5/5 samples but ADOPTS only 1/5 (puz-v1)
# and 0/5 (puz-v2), because its single retry lands at min(2*512,64000)=1024
# and a local thinking model needs more than that on most real positions. The
# consequence is interface_failure 0.80/1.00 — so the run validates the
# truncation path and NOTHING ELSE. The new legend, the @N ids and the
# `PICKS: NONE` rule are never reached, which is what this script was written
# to test. Raising the cap asks a DIFFERENT question ("does the contract parse
# when the model can finish a sentence"), it does not retune the first one.
# Report both; never merge them.
MAX_TOKENS="${MAX_TOKENS:-}"

cd "$(dirname "$0")/../.." || exit 2
[ -f "$POSITIONS" ] || { echo "no such positions file: $POSITIONS" >&2; exit 2; }

# Fail fast and loudly if the server is not up: a connection-refused storm
# would otherwise surface as 65 transport errors and an exit-3 INCOMPLETE,
# which reads like a model problem rather than a missing daemon.
if ! curl -sf -m 5 "${ENDPOINT%/v1}/api/version" >/dev/null 2>&1 \
   && ! curl -sf -m 5 "$ENDPOINT/models" >/dev/null 2>&1; then
  echo "endpoint $ENDPOINT is not answering — start it (\`ollama serve\`)" >&2
  exit 2
fi

slug="$(printf '%s' "$MODEL" | tr '/:.' '---')"
extra=()
[ -n "$MAXPOS" ] && extra+=(--max-positions "$MAXPOS")
# Distinct output dirs per cap, so the default-cap run is never overwritten by
# the raised-cap one — they are two measurements, not two attempts.
tag=""
if [ -n "$MAX_TOKENS" ]; then
  extra+=(--max-tokens "$MAX_TOKENS")
  tag="-mt${MAX_TOKENS}"
fi

for profile in puz-v1 puz-v2; do
  out="runs/puz/local-${slug}${tag}-${profile}"
  echo "=== $profile -> $out ==="
  rm -rf "$out"
  # ${extra[@]+...} guard: macOS ships bash 3.2, where "${extra[@]}" on an
  # EMPTY array is an unbound-variable error under `set -u`. Only bites when
  # neither MAX_TOKENS nor a position cap is passed — i.e. the default-cap
  # full-suite run, which is the primary measurement.
  python3 eval/puzzle_run.py "$POSITIONS" --out "$out" \
    --backend http --endpoint "$ENDPOINT" --model "$MODEL" \
    --profile "$profile" --k 1 --workers 1 ${extra[@]+"${extra[@]}"}
  echo "  exit=$?"
done

echo
echo "=== CONTRACT REGRESSION SUMMARY ==="
python3 - "${slug}${tag}" <<'PY'
import json, os, sys
slug = sys.argv[1]
rows = []
for profile in ("puz-v1", "puz-v2"):
    p = "runs/puz/local-%s-%s/summary.json" % (slug, profile)
    if not os.path.exists(p):
        print("MISSING %s" % p)
        continue
    s = json.load(open(p))
    m = s.get("metrics", {})
    # Counters live under summary["counters"], NOT at top level.
    c = s.get("counters", {}) or {}
    def est(k):
        v = m.get(k)
        return v.get("est") if isinstance(v, dict) else v
    rows.append({
        "profile": s.get("profile"),
        "sha": s.get("contract_sha"),
        "pass_1": est("pass_1"),
        "iface": est("interface_failure_rate"),
        "n": s.get("n_positions"),
        "samples": s.get("n_samples"),
        "transport": est("transport_error_rate") or s.get("transport_error_rate"),
        "retries": c.get("truncation_retries"),
        "adopted": c.get("truncation_retries_adopted"),
        "dyn": s.get("solve_dynamics") or {},
    })

def fmt(v, nd=4):
    return ("%.*f" % (nd, v)) if isinstance(v, (int, float)) else str(v)

print("%-8s %-18s %-8s %-8s %-6s %-9s %-8s" %
      ("profile", "contract_sha", "pass_1", "iface", "n", "retries", "adopted"))
for r in rows:
    print("%-8s %-18s %-8s %-8s %-6s %-9s %-8s" % (
        r["profile"], r["sha"], fmt(r["pass_1"]), fmt(r["iface"]),
        r["n"], r["retries"], r["adopted"]))

if len(rows) == 2:
    a, b = rows
    print()
    print("era shas differ: %s" % (a["sha"] != b["sha"]))
    for r in rows:
        d = r["dyn"]
        if d:
            def secs(ms):
                return "%.1f" % (ms / 1000.0) if isinstance(ms, (int, float)) else "n/a"
            print("  %-8s within_cap=%s (cap %ss)  p50=%ss p90=%ss max=%ss  "
                  "attempts mean=%s max=%s" % (
                      r["profile"], fmt(d.get("pass_1_within_cap")),
                      d.get("time_cap_s"),
                      secs(d.get("latency_ms_p50")), secs(d.get("latency_ms_p90")),
                      secs(d.get("latency_ms_max")),
                      fmt(d.get("attempts_mean"), 2), d.get("attempts_max")))
print()
print("NOT A LEADERBOARD SCORE — local quantized artifact; quote with model tag + quant.")
PY
