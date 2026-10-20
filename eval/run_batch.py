#!/usr/bin/env python3
"""Batch driver for the MTG Forge LLM eval.

Orchestrates N headless games by invoking `bash run-llm-headless.sh` from
FORGE_HOME with per-game env vars (GAME_SEED, SWAP_SEATS, EVAL_OUT, ...).
Each game is a separate JVM process. Resumable: seed/swap combos whose JSONL
in --out already contains a final game line RECORDED UNDER THIS BATCH'S
CONDITION are skipped. The condition (decks, identities, controllers, models,
context profile, memory tools, checkpoint, shas, cap, plans, env overrides) is
written to <out>/batch-manifest.json on first launch; a later invocation with a
different condition against the same --out is refused, so one directory is
always one experiment. --context-profile, --memory-tools, --rules-tool,
--text-tools, --text-answers, --answer-tool-strict, --lookahead, --one-tier/--two-tier, --no-coaching,
--one-chat, --turn-chat, --plan-execute and --tier-fast-types are the only way to set CONDITION_ENV_KEYS (ambient
values are scrubbed, --env is refused for them); unset means the JVM's historical baseline.

Two modes:
  CITED (default) — the only mode whose numbers may be quoted. Enforces a
    mirror-deck setup (same decklist both seats, distinct identity names),
    forces the eval-confound switches off (MULLIGAN_HEURISTIC=0,
    SKIP_HEURISTICS=0, no plans), pins --jobs 1 when a seat uses the claude CLI, and stamps full provenance
    (engine/overlay/deck shas + discipline). Refuses to run on any violation
    or on spike-file drift.
  --exploratory — skips the mirror/heuristic/jobs guards for quick pokes.
    Still stamps provenance (discipline=exploratory) but prints a loud banner
    that the numbers are NOT citable.

Panels and sanity arms (eval/SCORING.md):
  --panel mirror|cross launches every cell of a frozen panel (eval/suites/panels-v1.json), one
    block directory per cell under --out, all capped by one --budget-usd (required). A cross-panel
    cell is the one asymmetric matchup cited mode accepts: its decks must hash to the panel's pins.
  --sanity-arms plays floor, random and the loopback fake model (empty and garbage replies) as
    identity A on the scored block's seeds and decks, in <out>__sanity-<arm>; sanity_arms.py
    checks that none of them beats the floor.

Examples:
  # cited mirror run (numbers citable)
  python3 eval/run_batch.py --seeds 1..20 --pairs --deck eval/decks/mardu-energy.txt \
      --controller-a llm --controller-b llm --smart-a haiku --smart-b haiku
  # exploratory poke (not citable)
  python3 eval/run_batch.py --seeds 3,7,42 --exploratory --jobs 2 \
      --deck-a eval/decks/grixis-reanimator.txt --deck-b eval/decks/mardu-energy.txt
  # the mirror panel, a 3-seed block per cell, with its sanity arms
  python3 eval/run_batch.py --panel mirror --seeds 7800..7802 --sanity-arms --budget-usd 6 \
      --controller-a llm --smart-a openrouter:deepseek/deepseek-v4.1-flash \
      --fast-a openrouter:deepseek/deepseek-v4.1-flash --out runs/panel-mirror-deepseek
"""

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cost_report  # noqa: E402  what a batch directory has cost so far (--budget-usd, the [cost] line)
import seed_policy  # noqa: E402  seed governance (EVAL-DESIGN.md §6.5), the authority
import seed_registry  # noqa: E402  enforcement layer over seed_policy's blocks
import panel_score  # noqa: E402  the panels file: cells, deck pins, published seeds (--panel)
import sanity_arms  # noqa: E402  the zero-cost arms beside a scored block (--sanity-arms)
import benchmark_version  # noqa: E402  the overlay's file list and its hash (OVERLAY_SHA, drift)

PRINT_LOCK = threading.Lock()

# Repo root = parent of eval/. runs/ and forge-overlay/ live here.
REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_FILE = REPO_ROOT / "eval" / "suites" / "panels-v1.json"
# Env keys cited mode owns; a user --env override of these is refused.
CITED_FORCED_KEYS = ("MULLIGAN_HEURISTIC", "SKIP_HEURISTICS", "PLAN_A", "PLAN_B")
# Set only by their dedicated flags (listed in the module docstring).
# The condition a batch runs when --context-profile is not given: the faithful seat, the same default as
# run-llm-headless.sh. v1-compat (the JVM's own unset default, kept byte-identical for old artifacts)
# carries coaching, a two-sentence reasoning cap and a 25-decision turn cap; it is only ever a named arm.
DEFAULT_CONTEXT_PROFILE = "v4-faithful"
CONDITION_ENV_KEYS = ("CONTEXT_PROFILE", "MEMORY_TOOLS", "RULES_TOOL", "NATIVE_TOOLS", "LOOKAHEAD", "ONE_TIER", "NO_COACHING",
                      "ANSWER_TOOL", "ANSWER_TOOL_STRICT",
                      "ONE_CHAT", "TURN_CHAT", "PLAN_EXECUTE", "TIER_FAST_TYPES", "GAME_FORMAT")
# Condition env keys that a pre-flag manifest could only have recorded as an --env override. The
# resume comparison folds them out of `env_overrides` and back into their own condition field, so a
# directory made with `--env LOOKAHEAD=1` (or PLAN_EXECUTE / TIER_FAST_TYPES) resumes under the flag.
PROMOTED_ENV_KEYS = ("LOOKAHEAD", "PLAN_EXECUTE", "TIER_FAST_TYPES")
# Env keys that DEFINE the run (decks, identities, arms, models, seeding,
# caps, provenance). Cited mode refuses --env overrides of these — otherwise
# e.g. `--env DECK_A=other.txt` would silently break the mirror while the run
# stays stamped "cited". They are also scrubbed from the ambient environment
# in build_env so a stray exported var can't leak into any batch.
RUN_DEFINING_KEYS = (
    "DECK_A", "DECK_B", "DECK_A_NAME", "DECK_B_NAME",
    "CONTROLLER_A", "CONTROLLER_B",
    "SMART_MODEL", "FAST_MODEL",
    "SMART_MODEL_A", "SMART_MODEL_B", "FAST_MODEL_A", "FAST_MODEL_B",
    "GAME_SEED", "SWAP_SEATS", "EVAL_OUT", "GAME_CAP_MS",
    "ENGINE_SHA", "OVERLAY_SHA", "DECK_SHA", "RUN_DISCIPLINE",
    "CHECKPOINT_SHA", "SEED_BANK",
    # Context shape and the seat's tools are experimental CONDITIONS (they change
    # what the model can see and do); only their flags set them, never the
    # ambient environment or --env.
) + CONDITION_ENV_KEYS
# Per-knob context overrides (CTX_RECENT_EVENTS, ...) stamp a "+ovr" profile
# name and a different context_sha on the game line. Cited mode refuses them;
# exploratory mode allows them via --env and records them in the manifest.
CTX_OVERRIDE_PREFIX = "CTX_"
# Batch-level record of the condition every game in --out was launched under.
MANIFEST_NAME = "batch-manifest.json"
MANIFEST_SCHEMA = "batch-manifest.v1"
# Manifest fields that DEFINE the condition: a resume whose values differ on
# any of these is refused, because the games would land in one directory as
# two experiments (aggregate.py would keep them apart, but the operator asked
# for one batch, and a silent second condition is exactly the mixing this
# guard exists to prevent).
CONDITION_KEYS = (
    "discipline", "deck_a", "deck_b", "name_a", "name_b",
    "controller_a", "controller_b", "smart_a", "smart_b", "fast_a", "fast_b",
    "plan_a", "plan_b", "context_profile", "memory_tools", "rules_tool", "native_tools", "answer_tool", "answer_tool_strict",
    "lookahead", "one_tier", "no_coaching", "one_chat", "turn_chat",
    "plan_execute", "tier_fast_types",
    "ctx_overrides", "checkpoint_sha", "engine_sha", "overlay_sha", "deck_sha",
    "cap_minutes", "shadow_ai", "env_overrides", "fake_policy",
)
# --env values that must never be written to a manifest on disk.
SECRET_KEY_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def log(msg):
    with PRINT_LOCK:
        print(msg, flush=True)


def _refuse(msg):
    """Print a clear refusal to stderr and exit 2 (guard violation)."""
    print("run_batch: REFUSING (cited mode): " + msg, file=sys.stderr)
    print("           pass --exploratory to bypass this guard "
          "(numbers become non-citable).", file=sys.stderr)
    sys.exit(2)


def parse_seeds(spec):
    """Accept '1..20' (inclusive range) or '3,7,42' (explicit list) or '5'."""
    spec = spec.strip()
    m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)", spec)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi < lo:
            raise argparse.ArgumentTypeError("seed range end < start: %s" % spec)
        return list(range(lo, hi + 1))
    try:
        return [int(s) for s in spec.split(",") if s.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("bad --seeds spec: %s" % spec)


# ----------------------------------------------------------- provenance

def _sha12_bytes(b):
    return hashlib.sha256(b).hexdigest()[:12]


def _read_bytes(p):
    try:
        return Path(p).read_bytes()
    except OSError:
        return None


def _engine_sha(forge_home):
    """git -C <forge-home> rev-parse HEAD, or 'unknown' on any failure."""
    try:
        out = subprocess.run(["git", "-C", str(forge_home), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        sha = out.stdout.strip()
        return sha if out.returncode == 0 and sha else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _overlay_sha(forge_home):
    """sha256[:12] over every overlay source as INSTALLED under forge_home (benchmark_version.
    overlay_tree_sha over this checkout's file list): the harness classes and the engine patches.
    'unknown' if any is unreadable. It covered only LlmSpikeGui/LlmSpikeHeadless until 2026-10-16."""
    return benchmark_version.overlay_tree_sha(forge_home, benchmark_version.overlay_files(REPO_ROOT))


def _deck_sha(path):
    """sha256[:12] of the decklist file, or 'unknown'."""
    if not path:
        return "unknown"
    b = _read_bytes(path)
    return _sha12_bytes(b) if b is not None else "unknown"


def _checkpoint_sha(spec):
    """Identity of the model weights under test, for the game line.

    --checkpoint takes either a path (an adapter dir or weights file, hashed) or
    an opaque string (a tag or a hash you computed elsewhere, passed through).
    A directory hashes its file NAMES and CONTENTS in sorted order, so swapping
    an adapter without renaming it still changes the sha.

    This is the operator's claim about which weights are being served. The
    game line ALSO records what the endpoint said answered (served_*); when
    those two disagree, the run is not what its driver thought it was."""
    if not spec:
        return "unknown"
    p = Path(spec).expanduser()
    if not p.exists():
        return spec  # opaque tag: record it verbatim
    if p.is_file():
        b = _read_bytes(p)
        return _sha12_bytes(b) if b is not None else "unknown"
    h = hashlib.sha256()
    for f in sorted(p.rglob("*")):
        if not f.is_file():
            continue
        h.update(str(f.relative_to(p)).encode("utf-8"))
        b = _read_bytes(f)
        if b is None:
            return "unknown"
        h.update(b)
    return h.hexdigest()[:12]


def _spike_drift(forge_home, overlay_root=None):
    """Overlay files (paths relative to forge-overlay) whose INSTALLED bytes under forge_home differ
    from this checkout's overlay, or that are missing on either side. Empty list == no drift. Every
    overlay source counts: an uninstalled AnswerTool or engine patch is as stale as LlmSpikeGui."""
    overlay_root = Path(overlay_root) if overlay_root else REPO_ROOT / "forge-overlay"
    drift = []
    for rel in benchmark_version.overlay_files(overlay_root.parent):
        inst = _read_bytes(Path(forge_home) / rel)
        over = _read_bytes(overlay_root / rel)
        if inst is None or over is None or inst != over:
            drift.append(rel)
    return drift


# ------------------------------------------------------------- env build

def build_env(args, seed, swap, out_dir):
    env = os.environ.copy()
    # Scrub every run-defining / heuristic key from the ambient environment:
    # only flags, --env (re-applied below), and forced_env may set them.
    for k in RUN_DEFINING_KEYS + CITED_FORCED_KEYS:
        env.pop(k, None)
    for k in [k for k in env if k.startswith(CTX_OVERRIDE_PREFIX)]:
        env.pop(k, None)
    env["GAME_SEED"] = str(seed)
    # Context condition, PINNED EXPLICITLY. The play scripts (run-llm-headless.sh) default
    # CONTEXT_PROFILE/MEMORY_TOOLS/RULES_TOOL for a human's own games; a batch must never inherit
    # those, or a baseline arm would silently stamp the match model -- either refused as a condition
    # mismatch or, worse, pooled with a different measurement. Every condition key is set here,
    # always, to the requested value or its documented baseline. (This was a real bug: the always-on
    # play defaults leaked into a v1-compat arm.)
    env["CONTEXT_PROFILE"] = args.context_profile or DEFAULT_CONTEXT_PROFILE
    env["MEMORY_TOOLS"] = "1" if getattr(args, "memory_tools", False) else "0"
    env["RULES_TOOL"] = "1" if getattr(args, "rules_tool", False) else "0"
    # The answer tool is a function call too: NATIVE_TOOLS stays on for it even without --memory-tools.
    answer = bool(getattr(args, "answer_tool", False))
    env["NATIVE_TOOLS"] = "1" if getattr(args, "native_tools", False) or answer else "0"
    env["ANSWER_TOOL"] = "1" if answer else "0"
    env["ANSWER_TOOL_STRICT"] = "1" if answer and getattr(args, "answer_tool_strict", False) else "0"
    env["LOOKAHEAD"] = "1" if getattr(args, "lookahead", False) else "0"
    env["ONE_TIER"] = "1" if getattr(args, "one_tier", False) else "0"
    env["NO_COACHING"] = "1" if getattr(args, "no_coaching", False) else "0"
    env["ONE_CHAT"] = "1" if getattr(args, "one_chat", False) else "0"
    env["TURN_CHAT"] = "1" if getattr(args, "turn_chat", False) else "0"
    env["PLAN_EXECUTE"] = "1" if getattr(args, "plan_execute", False) else "0"
    env["TIER_FAST_TYPES"] = normalize_tier_fast_types(getattr(args, "tier_fast_types", None))
    # Explicitly clear SWAP_SEATS when not swapping so an inherited value
    # can't silently flip seats.
    if swap:
        env["SWAP_SEATS"] = "1"
    else:
        env.pop("SWAP_SEATS", None)
    env["EVAL_OUT"] = str(out_dir)
    if args.cap_minutes > 0:
        env["GAME_CAP_MS"] = str(int(args.cap_minutes * 60_000))
    # D4 DAgger: SHADOW_AI is controlled ONLY by --shadow-ai (scrub any ambient
    # value first so a stray export can't silently label — or un-label — a run).
    env.pop("SHADOW_AI", None)
    if getattr(args, "shadow_ai", False):
        env["SHADOW_AI"] = "1"

    passthrough = {
        "SMART_MODEL_A": args.smart_a,
        "SMART_MODEL_B": args.smart_b,
        "FAST_MODEL_A": args.fast_a,
        "FAST_MODEL_B": args.fast_b,
        "CONTROLLER_A": args.controller_a,
        "CONTROLLER_B": args.controller_b,
        "DECK_A": args.deck_a,
        "DECK_B": args.deck_b,
        "GAME_FORMAT": getattr(args, "format", None),
        "PLAN_A": args.plan_a,
        "PLAN_B": args.plan_b,
    }
    for key, val in passthrough.items():
        if val is not None:
            env[key] = val
    for kv in args.env or []:
        if "=" not in kv:
            raise SystemExit("--env expects KEY=VALUE, got: %s" % kv)
        k, _, v = kv.partition("=")
        env[k] = v
    # Forced/provenance env wins over everything (heuristic switches, identity
    # names, ENGINE_SHA/OVERLAY_SHA/DECK_SHA/RUN_DISCIPLINE). Cited mode has
    # already refused any user --env that touches the forced heuristic keys.
    for k, v in (args.forced_env or {}).items():
        env[k] = v
    # A sanity arm's loopback endpoint. Not recorded in the manifest: its port changes on every
    # launch, and fake_policy already names the condition.
    for k, v in (getattr(args, "loopback_env", None) or {}).items():
        env[k] = v
    return env


def run_one(args, seed, swap, out_dir):
    """Run one game. Returns (seed, swap, status, exit_code)."""
    env = build_env(args, seed, swap, out_dir)
    log_path = out_dir / ("game-%d-%d.log" % (seed, 1 if swap else 0))
    # The in-game wall-clock cap is GAME_CAP_MS (worker.join(capMs) in
    # LlmSpikeHeadless). The subprocess timeout below is a backstop for a WEDGED
    # JVM only. A HEALTHY game's subprocess wall time is NOT just the cap: the
    # cap timer starts (LlmSpikeHeadless.main sets t0) AFTER Forge model init
    # (non-lazy load of ~33k card scripts, LOAD_CARD_SCRIPTS_LAZILY=false) + deck
    # load, and after the cap fires the JVM grants an in-flight model call a grace
    # window before System.exit so its jsonl row is never orphaned. That grace
    # was widened from a flat 15s in the 4cfdaec cap-shutdown-race fix and is now
    # LlmSpikeGui.ModelCfg.maxDecisionMs() + 10s (~912s with the 300s timeout). So a
    # healthy game's total wall time ~= [JVM boot + card-script load + deck load,
    # BEFORE the cap timer] + GAME_CAP_MS + the in-JVM post-cap grace + writeGameSummary/shutdown
    # writes, all inflated under N-way parallel contention.
    #
    # The old `cap*60*2 + 300` formula scaled ONLY with the cap and lacked a fixed
    # floor for the (cap-independent) grace + card-script load, so small-cap
    # parallel runs were killed at cap+grace (2 games in the first 5-JVM parallel
    # run — see the project journal). Keep the 2x-cap load-scaling slush AND add
    # explicit fixed floors for the grace and the startup/deck-load/shutdown work
    # so the backstop can only WIDEN, never regress a previously-passing run,
    # while staying finite (a truly wedged JVM is still killed).
    #
    # GRACE_S is a documented constant, not derived live: the source of truth
    # (ModelCfg.maxDecisionMs) is Java-side and not cheaply callable from Python.
    # 1000 = the ~912s built-in grace (three attempts of the 300s timeout, raised
    # 2026-10-15 with the 32000-token cap) + margin. ContextProfileTest asserts it.
    GRACE_S = 1000
    # JVM boot + non-lazy ~33k-card-script parse + deck load (all before the cap
    # timer) + post-cap game-summary/shutdown writes, generous for parallel
    # contention where all concurrent JVMs load card scripts at once.
    STARTUP_SHUTDOWN_S = 600
    timeout = (args.cap_minutes * 60 * 2 + GRACE_S + STARTUP_SHUTDOWN_S
               if args.cap_minutes > 0 else None)
    log("[start] seed=%d swap=%d -> %s" % (seed, swap, log_path.name))
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.run(
                ["bash", "run-llm-headless.sh"],
                cwd=str(args.forge_home),
                env=env,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        code = proc.returncode
    except subprocess.TimeoutExpired:
        log("[timeout] seed=%d swap=%d killed after backstop timeout" % (seed, swap))
        return (seed, swap, "timeout", -1)
    except OSError as e:
        log("[error] seed=%d swap=%d failed to launch: %s" % (seed, swap, e))
        return (seed, swap, "launch-error", -1)

    if code != 0:
        log("[fail] seed=%d swap=%d exit=%d (see %s)" % (seed, swap, code, log_path))
        return (seed, swap, "nonzero-exit", code)
    # Exit 0 but no game line means the game crashed before summarizing;
    # aggregate.py will count the file (if any) as invalid.
    if not has_game_line(out_dir, seed, swap):
        log("[warn] seed=%d swap=%d exited 0 but wrote no game line" % (seed, swap))
        return (seed, swap, "no-game-line", code)
    # A game line whose recorded condition is not the one we launched means
    # the JVM did not honor the requested profile/tools: never count it done.
    if not has_game_line(out_dir, seed, swap, resume_expectation(args)):
        log("[fail] seed=%d swap=%d wrote a game line under a DIFFERENT condition "
            "than requested (check context_profile/memory_tools on the line)"
            % (seed, swap))
        return (seed, swap, "condition-mismatch", code)
    log("[done] seed=%d swap=%d" % (seed, swap))
    return (seed, swap, "ok", code)


def iter_game_lines(out_dir, seed=None):
    """Yield every parseable game line in out_dir (optionally one seed's
    files). Defensive: skip unparseable lines and unreadable files."""
    pattern = "game-%d-*.jsonl" % seed if seed is not None else "game-*.jsonl"
    for path in sorted(Path(out_dir).glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(rec, dict) and rec.get("t") == "game":
                        yield rec
        except OSError:
            continue


# Sentinel: the game line may lack this field entirely (legacy harness).
ABSENT = object()


def game_line_matches(rec, expect):
    """expect: {field: [acceptable values]} where ABSENT accepts a missing
    field. A field the line carries with a value outside the list is a
    mismatch: such a line was recorded under a different condition."""
    for field, allowed in (expect or {}).items():
        if field not in rec:
            if ABSENT in allowed:
                continue
            return False
        v = rec[field]
        if not any(a is not ABSENT and a == v for a in allowed):
            return False
    return True


def resume_expectation(args):
    """Which recorded fields a completed game line must show to count as
    THIS batch's game. Only fields the runner controls explicitly are
    demanded; the batch manifest guards the rest."""
    expect = {}
    if getattr(args, "context_profile", None):
        expect["context_profile"] = [args.context_profile]
    else:
        expect["context_profile"] = [DEFAULT_CONTEXT_PROFILE]
    if getattr(args, "memory_tools", False):
        expect["memory_tools"] = [True]
    else:
        expect["memory_tools"] = [False, ABSENT]
    if getattr(args, "native_tools", False):
        expect["native_tools"] = [True]
    else:
        expect["native_tools"] = [False, ABSENT]
    if getattr(args, "rules_tool", False):
        expect["rules_tool"] = [True]
    else:
        expect["rules_tool"] = [False, ABSENT]
    on = getattr(args, "lookahead", False)
    expect["lookahead"] = [on] if on != getattr(args, "unstamped_lookahead", False) else [on, ABSENT]
    # A game line written before the answer tool existed answered in text.
    expect["answer_tool"] = [True] if getattr(args, "answer_tool", False) else [False, ABSENT]
    expect["one_tier"] = [True] if getattr(args, "one_tier", False) else [False, ABSENT]
    expect["no_coaching"] = [True] if getattr(args, "no_coaching", False) else [False, ABSENT]
    expect["one_chat"] = [True] if getattr(args, "one_chat", False) else [False, ABSENT]
    expect["turn_chat"] = [True] if getattr(args, "turn_chat", False) else [False, ABSENT]
    # The two opt-in play switches (issue #117). A line with no stamp is read as what a pre-flag
    # manifest recorded (an --env override), else off, so a batch run with `--env TIER_FAST_TYPES=...`
    # before the flag resumes instead of replaying its finished games as "a DIFFERENT condition".
    pe = bool(getattr(args, "plan_execute", False))
    up = bool(getattr(args, "unstamped_plan_execute", None))
    expect["plan_execute"] = [pe] if pe != up else [pe, ABSENT]
    tft = normalize_tier_fast_types(getattr(args, "tier_fast_types", None))
    utft = normalize_tier_fast_types(getattr(args, "unstamped_tier_fast_types", None))
    expect["tier_fast_types"] = [tft] if tft != utft else [tft, ABSENT]
    for field, val in (("controller_a", args.controller_a), ("controller_b", args.controller_b),
                       ("smart_a", args.smart_a), ("smart_b", args.smart_b),
                       ("fast_a", args.fast_a), ("fast_b", args.fast_b)):
        if val is not None:
            expect[field] = [val]
    return expect


def has_game_line(out_dir, seed, swap, expect=None):
    """True if some game-<seed>-*.jsonl in out_dir has a final game line
    matching this seed/swap combo AND (when expect is given) the recorded
    condition. A game played under another profile/tool setting is not
    "already complete" for this batch."""
    for rec in iter_game_lines(out_dir, seed):
        if rec.get("seed") == seed and bool(rec.get("swap", False)) == swap:
            if game_line_matches(rec, expect):
                return True
    return False


def plan_combos(out_dir, combos, expect=None):
    """Split (seed, swap) combos into (todo, skipped, foreign): skipped are
    complete under this condition; foreign are combos for which a game line
    exists only under a DIFFERENT condition (they are replayed, and the
    operator is told)."""
    todo, skipped, foreign = [], [], []
    for seed, swap in combos:
        if has_game_line(out_dir, seed, swap, expect):
            skipped.append((seed, swap))
            continue
        if expect and has_game_line(out_dir, seed, swap, None):
            foreign.append((seed, swap))
        todo.append((seed, swap))
    return todo, skipped, foreign


# ------------------------------------------------------------ manifest

def _scrub_env_overrides(kvs):
    """--env KEY=VALUE list -> {KEY: VALUE}, with secret-looking values
    replaced by a marker so the manifest never stores a credential."""
    out = {}
    for kv in kvs or []:
        if "=" not in kv:
            continue
        k, _, v = kv.partition("=")
        out[k] = "<redacted>" if any(h in k.upper() for h in SECRET_KEY_HINTS) else v
    return out


def batch_condition(args, cited):
    """The condition every game of this batch is launched under, as recorded
    in the manifest and compared on resume."""
    forced = getattr(args, "forced_env", None) or {}
    overrides = _scrub_env_overrides(args.env)
    ctx = {k: v for k, v in overrides.items() if k.startswith(CTX_OVERRIDE_PREFIX)}
    return {
        "discipline": "cited" if cited else "exploratory",
        "deck_a": args.deck_a, "deck_b": args.deck_b,
        "name_a": args.name_a, "name_b": args.name_b,
        "controller_a": args.controller_a, "controller_b": args.controller_b,
        "smart_a": args.smart_a, "smart_b": args.smart_b,
        "fast_a": args.fast_a, "fast_b": args.fast_b,
        "plan_a": args.plan_a, "plan_b": args.plan_b,
        "context_profile": getattr(args, "context_profile", None),
        "memory_tools": bool(getattr(args, "memory_tools", False)),
        "rules_tool": bool(getattr(args, "rules_tool", False)),
        "native_tools": bool(getattr(args, "native_tools", False)),
        "answer_tool": bool(getattr(args, "answer_tool", False)),
        "answer_tool_strict": bool(getattr(args, "answer_tool", False) and getattr(args, "answer_tool_strict", False)),
        "lookahead": bool(getattr(args, "lookahead", False)),
        "one_tier": bool(getattr(args, "one_tier", False)),
        "no_coaching": bool(getattr(args, "no_coaching", False)),
        "one_chat": bool(getattr(args, "one_chat", False)),
        "turn_chat": bool(getattr(args, "turn_chat", False)),
        "plan_execute": bool(getattr(args, "plan_execute", False)),
        "tier_fast_types": normalize_tier_fast_types(getattr(args, "tier_fast_types", None)),
        "ctx_overrides": ctx,
        "checkpoint_sha": forced.get("CHECKPOINT_SHA"),
        "engine_sha": forced.get("ENGINE_SHA"),
        "overlay_sha": forced.get("OVERLAY_SHA"),
        "deck_sha": forced.get("DECK_SHA"),
        "cap_minutes": args.cap_minutes,
        "shadow_ai": bool(getattr(args, "shadow_ai", False)),
        # A sanity arm's seat on the loopback fake model (sanity_arms.py): which policy answers.
        "fake_policy": getattr(args, "fake_policy", None),
        "env_overrides": {k: v for k, v in overrides.items()
                          if not k.startswith(CTX_OVERRIDE_PREFIX)},
    }


def native_tools_requested(args):
    """Native function calling is the standard tool protocol: it is in effect whenever the seat has
    tools at all (--memory-tools), some LLM seat is on a backend that supports it (openrouter:), and
    --text-tools did not opt out. Mirrors NativeTools.usedBy + the game line's native_tools stamp."""
    if not getattr(args, "memory_tools", False) or getattr(args, "text_tools", False):
        return False
    seats = [(getattr(args, "controller_a", None), getattr(args, "smart_a", None), getattr(args, "fast_a", None)),
             (getattr(args, "controller_b", None), getattr(args, "smart_b", None), getattr(args, "fast_b", None))]
    # Each tier decides alone (NativeTools.seats): one openrouter tier on a model-driven seat is enough.
    return any((c or "llm") == "llm" and (spec or "").startswith("openrouter:")
               for c, smart, fast in seats for spec in (smart, fast))


def answer_tool_requested(args):
    """The answer tool (AnswerTool: every decision answered by a function call) is the standard decision
    protocol: in effect whenever some LLM seat is on a backend with function calling (openrouter:),
    unless --text-answers (or --text-tools, no function calling at all) opted out. Unlike the lookups it
    does not need --memory-tools. Mirrors AnswerTool.seats + the game line's answer_tool stamp."""
    if getattr(args, "text_answers", False) or getattr(args, "text_tools", False):
        return False
    seats = [(getattr(args, "controller_a", None), getattr(args, "smart_a", None), getattr(args, "fast_a", None)),
             (getattr(args, "controller_b", None), getattr(args, "smart_b", None), getattr(args, "fast_b", None))]
    return any((c or "llm") == "llm" and (spec or "").startswith("openrouter:")
               for c, smart, fast in seats for spec in (smart, fast))


def spent_usd(out_dir):
    """Estimated spend recorded in a batch directory so far (cost_report: finished and unfinished games),
    crashed attempts set aside under crashed/ included: they were billed all the same."""
    prices = cost_report.load_prices()
    crashed = Path(out_dir) / CRASHED_DIR
    return (cost_report.report_dir(str(out_dir), prices)["usd"]
            + (cost_report.report_dir(str(crashed), prices)["usd"] if crashed.is_dir() else 0.0))


# A crashed attempt's files go here before its game is played again, so aggregate.py (which reads the
# directory's own game files) never counts them, while spend and the record keep them.
CRASHED_DIR = "crashed"
# Failures a second attempt can fix: the JVM died, wrote no game line, or hung. A condition mismatch or
# a launch error would fail the same way again.
RETRYABLE = ("nonzero-exit", "no-game-line", "timeout")


def attempt_jsonl(log_path):
    """The game file an attempt wrote, as its JVM named it in its log ("[EVAL] jsonl <name>"), or None."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"\[EVAL\] jsonl (game-[0-9-]+\.jsonl)\s*$", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def play_with_retries(play, seed, swap, out_dir, retries, over_budget):
    """play() up to retries+1 times while it fails in a RETRYABLE way, setting each failed attempt aside.
    Returns the last result, or (seed, swap, "budget", 0) when the budget stops the first attempt."""
    result = None
    for attempt in range(retries + 1):
        if over_budget():
            return result or (seed, swap, "budget", 0)
        result = play()
        if result[2] not in RETRYABLE or attempt == retries:
            return result
        moved = set_aside(out_dir, seed, swap, attempt + 1)
        log("[retry] seed=%d swap=%d attempt %d %s; set aside %s and playing it again"
            % (seed, swap, attempt + 1, result[2], ", ".join(moved) or "nothing"))
    return result


def set_aside(out_dir, seed, swap, attempt):
    """Move one crashed attempt's log and game file under crashed/, named by attempt. Returns the moved
    names. The batch manifest is copied there once, so cost_report can price the unfinished game."""
    out_dir = Path(out_dir)
    dest = out_dir / CRASHED_DIR
    dest.mkdir(exist_ok=True)
    man = out_dir / "batch-manifest.json"
    if man.is_file() and not (dest / man.name).exists():
        shutil.copy(str(man), str(dest / man.name))
    log_path = out_dir / ("game-%d-%d.log" % (seed, 1 if swap else 0))
    moved = []
    name = attempt_jsonl(log_path)
    for p in ([out_dir / name] if name else []) + [log_path]:
        if p.is_file():
            target = dest / ("attempt%d-swap%d-%s" % (attempt, 1 if swap else 0, p.name))
            p.rename(target)
            moved.append(target.name)
    return moved


def normalize_tier_fast_types(v):
    """TIER_FAST_TYPES as it is compared, stamped and passed: lowercased, de-duplicated, sorted,
    comma-joined; empty string when unset. Matches PlayerControllerLLM.tierFastTypesLabel so the
    --tier-fast-types value, the manifest and the game line all name the same condition."""
    if not v:
        return ""
    return ",".join(sorted({p.strip().lower() for p in str(v).split(",") if p.strip()}))


def condition_diff_norm(m, k):
    """A condition field as compared on resume. A manifest written before native_tools existed ran
    without it; one written before a flag recorded its env key, if at all, as an --env override."""
    env = m.get("env_overrides") or {}
    if k in ("native_tools", "answer_tool", "answer_tool_strict", "one_tier", "no_coaching", "one_chat", "turn_chat"):
        return bool(m.get(k))
    if k == "lookahead":
        return bool(m.get(k)) or env.get("LOOKAHEAD") == "1"
    if k == "plan_execute":
        return bool(m.get(k)) or env.get("PLAN_EXECUTE") == "1"
    if k == "tier_fast_types":
        return normalize_tier_fast_types(m.get(k)) or normalize_tier_fast_types(env.get("TIER_FAST_TYPES"))
    if k == "env_overrides":
        return {e: v for e, v in env.items() if e not in PROMOTED_ENV_KEYS}
    return m.get(k)


def condition_diff(old, new):
    """[(key, old_value, new_value)] over CONDITION_KEYS that differ."""
    return [(k, condition_diff_norm(old, k), condition_diff_norm(new, k)) for k in CONDITION_KEYS
            if condition_diff_norm(old, k) != condition_diff_norm(new, k)]


def load_manifest(out_dir):
    p = Path(out_dir) / MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError("unreadable %s: %s" % (p, e))
    if not isinstance(m, dict) or m.get("schema") != MANIFEST_SCHEMA:
        raise ManifestError("%s is not a %s manifest" % (p, MANIFEST_SCHEMA))
    return m


class ManifestError(Exception):
    pass


def legacy_dir_condition(out_dir):
    """Condition fields recoverable from the game lines of a directory that
    predates the manifest, or None when there are no game lines. Fields the
    lines do not carry are omitted (unknowable, not 'matches')."""
    field_map = {
        "context_profile": "context_profile", "memory_tools": "memory_tools",
        "rules_tool": "rules_tool", "native_tools": "native_tools", "answer_tool": "answer_tool",
        "controller_a": "controller_a", "controller_b": "controller_b",
        "smart_a": "smart_a", "smart_b": "smart_b", "fast_a": "fast_a", "fast_b": "fast_b",
        "name_a": "deck_a", "name_b": "deck_b", "discipline": "discipline",
        "checkpoint_sha": "checkpoint_sha", "engine_sha": "engine_sha",
        "overlay_sha": "overlay_sha", "deck_sha": "deck_sha",
    }
    seen = {}
    n = 0
    for rec in iter_game_lines(out_dir):
        n += 1
        # A game line written before native_tools existed ran the text grammar (or no tools).
        # Without this a legacy directory of text-grammar games would accept native-tools games.
        rec = dict({"native_tools": False, "answer_tool": False}, **rec)
        for key, field in field_map.items():
            if field in rec:
                seen.setdefault(key, set()).add(json.dumps(rec[field], sort_keys=True))
    if not n:
        return None
    return {"n_game_lines": n,
            "fields": {k: [json.loads(v) for v in sorted(vals)] for k, vals in seen.items()}}


def unstamped_lookahead(manifest):
    """Whether a game line with no `lookahead` stamp ran with it on. Lines from before the stamp
    (2026-10-13) cannot say for themselves; a manifest from before --lookahead can (its
    --env LOOKAHEAD=1). Under a manifest that has the key, every line was written by a JVM that
    stamps it, so an unstamped one is off, as run_one judged it when it was written."""
    if manifest is None or "lookahead" in manifest.get("condition", {}):
        return False
    return condition_diff_norm(manifest.get("condition", {}), "lookahead")


def unstamped_play_switch(manifest, key, env_key):
    """The value a game line with no `<key>` stamp ran with, when a manifest from before that flag
    recorded it as an --env override (`--env PLAN_EXECUTE=1`, `--env TIER_FAST_TYPES=...`); None when
    the manifest already has the key (every line was written by a JVM that stamps it) or cannot say.
    Mirrors unstamped_lookahead: these two switches were settable by --env before they got flags."""
    if manifest is None or key in manifest.get("condition", {}):
        return None
    return condition_diff_norm(manifest.get("condition", {}), key)


def lookahead_problem(out_dir, unstamped, condition):
    """(key, recorded, requested) when the directory's games did not all run with the requested
    lookahead value, each line read by its own stamp or, unstamped, as `unstamped`; else None."""
    want = bool(condition.get("lookahead"))
    seen = {bool(rec.get("lookahead", unstamped)) for rec in iter_game_lines(out_dir)}
    return ("lookahead", sorted(seen), want) if seen and seen != {want} else None


def check_resume_condition(out_dir, condition, cited):
    """Refuse (return a message) when out_dir already holds games launched
    under a different condition; None when resuming is safe. Legacy dirs
    without a manifest are compared on whatever their game lines recorded;
    in cited mode an unverifiable legacy dir is refused outright."""
    manifest = load_manifest(out_dir)
    if manifest is not None:
        # A manifest older than --text-tools cannot say which protocol ran; its game lines can.
        recorded = manifest.get("condition", {})
        lines = legacy_dir_condition(out_dir) if "native_tools" not in recorded else None
        # A manifest from before --lookahead that recorded nothing reads as off; every game line
        # stamped with the requested value proves otherwise.
        want_la = bool(condition.get("lookahead"))
        stamps = [rec.get("lookahead") for rec in iter_game_lines(out_dir)]
        proven = ("lookahead" not in recorded and not condition_diff_norm(recorded, "lookahead")
                  and stamps and all(s == want_la for s in stamps))
        diff = [d for d in condition_diff(recorded, condition)
                if not ((proven and d[0] == "lookahead") or (lines and d[0] == "native_tools"))]
        if lines and lines["fields"]["native_tools"] != [bool(condition.get("native_tools"))]:
            diff.append(("native_tools", lines["fields"]["native_tools"], bool(condition.get("native_tools"))))
        la = lookahead_problem(out_dir, unstamped_lookahead(manifest), condition)
        if la and not any(d[0] == "lookahead" for d in diff):
            diff.append(la)
        if diff:
            return ("%s already holds games under a different condition; a batch "
                    "directory is one experiment. Differences:\n           - "
                    % out_dir
                    + "\n           - ".join("%s: recorded %r, requested %r" % d for d in diff)
                    + "\n           Use a new --out for the new condition.")
        return None
    legacy = legacy_dir_condition(out_dir)
    if legacy is None:
        return None
    problems = []
    for key, vals in legacy["fields"].items():
        want = condition.get(key)
        if key == "context_profile" and want is None:
            want = DEFAULT_CONTEXT_PROFILE
        if key in ("memory_tools", "rules_tool", "native_tools", "answer_tool"):
            want = bool(want)
        if key in ("controller_a", "controller_b", "smart_a", "smart_b",
                   "fast_a", "fast_b") and want is None:
            continue  # runner left it to the JVM default; cannot compare
        if len(vals) > 1 or vals[0] != want:
            problems.append("%s: recorded %s, requested %r" % (key, vals, want))
    la = lookahead_problem(out_dir, False, condition)
    if la:
        problems.append("%s: recorded %s, requested %r" % la)
    unverifiable = [k for k in ("context_profile", "memory_tools", "rules_tool", "checkpoint_sha")
                    if k not in legacy["fields"]]
    if problems:
        return ("%s has no %s but its %d game line(s) record a different condition:"
                "\n           - " % (out_dir, MANIFEST_NAME, legacy["n_game_lines"])
                + "\n           - ".join(problems)
                + "\n           Use a new --out for the new condition.")
    if unverifiable and cited:
        return ("%s has no %s and its game lines do not record %s, so the "
                "condition of the games already there cannot be verified. Cited "
                "batches may not resume into it; use a new --out."
                % (out_dir, MANIFEST_NAME, ", ".join(unverifiable)))
    if unverifiable:
        log("[warn] %s predates the batch manifest and its game lines do not "
            "record %s; the condition of the existing games is unverified."
            % (out_dir, ", ".join(unverifiable)))
    return None


def write_manifest(out_dir, condition, seeds, pairs, jobs, argv=None):
    """Create or extend the batch manifest. The condition is written once;
    every invocation (initial or resume) is appended to `invocations`."""
    p = Path(out_dir) / MANIFEST_NAME
    existing = load_manifest(out_dir)
    entry = {"utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "seeds": seeds, "pairs": bool(pairs), "jobs": jobs,
             "argv": list(argv) if argv is not None else None}
    if existing is None:
        m = {"schema": MANIFEST_SCHEMA, "condition": condition, "invocations": [entry]}
    else:
        m = existing
        m.setdefault("invocations", []).append(entry)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
    return m


# ----------------------------------------------------------- cited guards

def format_guard(args):
    """--format: every deck must be legal in it by the pinned engine's format data, or nothing launches."""
    if not getattr(args, "format", None):
        return
    import deck_legality
    forge = deck_legality.forge_home()
    fmt = deck_legality.read_format(forge, args.format)
    printings = deck_legality.read_printings(forge)
    for d in {x for x in (args.deck_a, args.deck_b) if x}:
        bad = deck_legality.problems(deck_legality.read_deck(d), fmt, printings)
        if bad:
            print("run_batch: REFUSING: %s is not legal in %s:\n  - %s" % (d, fmt["name"], "\n  - ".join(bad)),
                  file=sys.stderr)
            sys.exit(2)


def cited_pre_guards(args):
    """Guards 1-3 (spec C). Pure — no forge-home, no dir creation. Runs
    FIRST so a misconfigured cited run refuses before anything launches."""
    # --- guard 1: mirror deck ---
    da, db = args.deck_a, args.deck_b
    if not da or not db:
        _refuse("a mirror deck is required — pass --deck PATH (sets both seats) "
                "or matching --deck-a/--deck-b.")
    pa = Path(da).expanduser().resolve()
    pb = Path(db).expanduser().resolve()
    # The one asymmetric matchup cited mode accepts: a cross-panel cell, whose deck pair is fixed by
    # the frozen panels file and whose two decklists still hash to the pins recorded there.
    pin = getattr(args, "cross_pin", None)
    if pa != pb and pin:
        if (_deck_sha(pa), _deck_sha(pb)) != tuple(pin):
            _refuse("cross-panel decks do not match their pins %s (got %s, %s)"
                    % (list(pin), _deck_sha(pa), _deck_sha(pb)))
    elif pa != pb:
        _refuse("mirror-only: DECK_A and DECK_B must resolve to the SAME file "
                "(got %s\n           vs %s)." % (pa, pb))
    if not pa.is_file():
        _refuse("deck file does not exist: %s" % pa)
    if pa.stat().st_size == 0:
        _refuse("deck file is empty: %s" % pa)
    args.deck_a = str(pa)
    args.deck_b = str(pb)

    na = args.name_a or "Identity-A"
    nb = args.name_b or "Identity-B"
    if na == nb:
        _refuse("DECK_A_NAME and DECK_B_NAME must differ for identity keying "
                "(both were %r) — use --name-a/--name-b." % na)
    args.name_a, args.name_b = na, nb

    # --- guard 2: forced heuristic switches, no user override ---
    for kv in args.env or []:
        k = kv.split("=", 1)[0]
        if k in CITED_FORCED_KEYS:
            _refuse("cited mode forces %s and forbids overriding it via --env." % k)
        if k in RUN_DEFINING_KEYS:
            _refuse("cited mode forbids --env %s — it defines the run; use the "
                    "dedicated flag instead so guards and provenance see it." % k)
        if k.startswith(CTX_OVERRIDE_PREFIX):
            _refuse("cited mode forbids per-knob context overrides (--env %s); a "
                    "cited condition is a named --context-profile, nothing else." % k)
    if args.plan_a or args.plan_b:
        _refuse("plans (PLAN_A/PLAN_B) are an expert-knowledge confound and are "
                "forbidden in cited mode.")

    # --- guard 3: single JVM when a seat runs on the local claude CLI ---
    # Parallel JVMs starve the CLI and inflate fallbacks. An openrouter: game waits on the network
    # (eval/CONCURRENCY.md section 7), so it may run in parallel; warn_parallel_jobs still caps by RAM.
    bare = sorted(set(m for m in effective_llm_models(args) if not m.startswith("openrouter:")))
    if args.jobs != 1 and bare:
        _refuse("cited mode requires --jobs 1 with claude-CLI model specs %s (got --jobs %d); "
                "parallel JVMs starve the claude CLI and inflate fallbacks." % (bare, args.jobs))

    # --- guard 3b: held-out seeds only ---
    cited_seed_guard(args)


def cited_seed_guard(args):
    """A cited win rate must be measured on seeds the model was not trained on.

    runs/stage1-student-eval and runs/stage2-student-eval both played seeds
    501-525 while 13 of those 25 seeds sat in datasets/stage1-v1 — the student
    was graded on shuffles whose winning lines it had been fine-tuned on. No
    amount of confidence interval fixes that, so the check belongs here, before
    the games run, not in the analysis afterwards.

    Refuses on either failure mode: a seed declared as training/harness/
    unregistered in eval/seed_registry.json, or a seed that is in a training
    corpus right now regardless of what the registry says."""
    try:
        registry = seed_registry.load_registry()
    except seed_registry.RegistryError as e:
        _refuse("seed registry is unusable (%s). Cited mode needs it to tell a "
                "held-out seed from a trained-on one." % e)

    problems = seed_registry.cited_seed_violations(registry, args.seeds)
    if problems:
        banks = ", ".join("%s %s" % (b["name"], seed_registry.fmt_ranges(b["ranges"]))
                          for b in registry.eval_only_banks) or "(none declared)"
        _refuse("these seeds cannot carry a cited eval:\n           - "
                + "\n           - ".join(problems)
                + "\n           eval-only bands: " + banks
                + "\n           (inspect with: python3 eval/seed_registry.py classify "
                  "<spec>)")


def finalize_provenance_and_cap(args, cited):
    """Guards 4-5 (spec C): derive the per-tier cap when --cap-minutes was
    omitted, compute provenance env, and (cited only) refuse on spike drift.
    Requires args.forge_home already resolved."""
    forced = {}
    if cited:
        forced["MULLIGAN_HEURISTIC"] = "0"
        forced["SKIP_HEURISTICS"] = "0"
        forced["PLAN_A"] = ""
        forced["PLAN_B"] = ""
    if args.name_a:
        forced["DECK_A_NAME"] = args.name_a
    if args.name_b:
        forced["DECK_B_NAME"] = args.name_b

    # Drift check (cited refuses; exploratory only notes it).
    drift = _spike_drift(args.forge_home)
    if drift:
        if cited:
            _refuse("installed overlay file(s) differ from this checkout's "
                    "forge-overlay: %s — run `bash install.sh` first."
                    % ", ".join(drift))
        else:
            log("[warn] overlay drift (%s) — installed JVM code != this overlay."
                % ", ".join(drift))

    forced["RUN_DISCIPLINE"] = "cited" if cited else "exploratory"
    forced["ENGINE_SHA"] = _engine_sha(args.forge_home)
    forced["OVERLAY_SHA"] = _overlay_sha(args.forge_home)
    # A cross-panel cell stamps both decks, so its deck_sha names the whole matchup (panel_score.py
    # checks it per cell). Other runs keep the historical one-deck stamp: changing it would refuse
    # every resume into an existing asymmetric exploratory directory.
    forced["DECK_SHA"] = (panel_score.pair_deck_sha(_deck_sha(args.deck_a), _deck_sha(args.deck_b))
                          if getattr(args, "cross_pin", None) else _deck_sha(args.deck_a or args.deck_b))
    forced["CHECKPOINT_SHA"] = _checkpoint_sha(args.checkpoint)
    # Which declared band these seeds came from, stamped on every game line so
    # the artifact says whether it was held-out without re-deriving it later.
    try:
        forced["SEED_BANK"] = seed_registry.seed_bank_label(
            seed_registry.load_registry(), args.seeds)
    except seed_registry.RegistryError as e:
        # Exploratory runs must not be blocked by a registry problem; cited runs
        # already refused in cited_seed_guard.
        log("[warn] seed registry unusable (%s) -> seed_bank=unregistered" % e)
        forced["SEED_BANK"] = seed_registry.UNREGISTERED
    args.forced_env = forced

    # Guard 4: per-tier cap derivation (only when not explicitly given).
    if args.cap_minutes is None:
        args.cap_minutes = derive_cap_minutes(args)
        log("[cap] --cap-minutes not given; derived %g min from controllers/models."
            % args.cap_minutes)

    log("[provenance] engine=%s overlay=%s deck=%s discipline=%s"
        % (forced["ENGINE_SHA"], forced["OVERLAY_SHA"], forced["DECK_SHA"],
           forced["RUN_DISCIPLINE"]))
    log("[provenance] checkpoint=%s seed_bank=%s"
        % (forced["CHECKPOINT_SHA"], forced["SEED_BANK"]))
    log("[condition] context_profile=%s memory_tools=%s"
        % (args.context_profile or "(unset: default " + DEFAULT_CONTEXT_PROFILE + ")",
           "1" if args.memory_tools else "off"))
    if forced["CHECKPOINT_SHA"] == "unknown":
        log("[warn] no --checkpoint given: the game line will not say which weights "
            "played. The endpoint's served_* fields are then the only identity, and "
            "an alias spec (e.g. openrouter:default_model) may not resolve to one.")


def effective_llm_models(args):
    """Model specs actually in play for llm seats, with the in-JVM defaults
    substituted (unset SMART_MODEL_* -> opus, unset FAST_MODEL_* -> haiku) so
    an unset tier is never silently dropped. Empty list -> no llm seat."""
    ca = args.controller_a or "llm"   # Java defaults an unset controller to llm
    cb = args.controller_b or "llm"
    models = []
    if ca == "llm":
        models += [args.smart_a or "opus", args.fast_a or "haiku"]
    if cb == "llm":
        models += [args.smart_b or "opus", args.fast_b or "haiku"]
    return models


def unpinned_llm_tiers(args):
    """Flags an llm seat leaves unset (issue #234). A seat with no controller is an llm seat, and an unset
    tier falls through to the in-JVM default model (Opus for the smart tier), which no stamp flags: a Luna
    mirror launched with only seat A pinned played Luna against Opus 5.5."""
    missing = []
    for ident in ("a", "b"):
        if (getattr(args, "controller_" + ident, None) or "llm") != "llm":
            continue
        missing += ["--%s-%s" % (tier, ident) for tier in ("smart", "fast") if not getattr(args, "%s_%s" % (tier, ident))]
    return missing


def warn_parallel_jobs(args):
    """Spec-aware --jobs advice (measured basis: eval/CONCURRENCY.md §7).
    Bare (non-openrouter:) specs shell out to the local claude CLI and contend
    with the user's own interactive sessions -> jobs=1 territory. openrouter:
    specs are network-bound (~1GB RSS, near-zero CPU per JVM) -> RAM is the
    limit; floor/random-only batches are CPU-burst-bound instead."""
    if args.jobs <= 1:
        return
    models = effective_llm_models(args)
    bare = sorted(set(m for m in models if not m.startswith("openrouter:")))
    if bare:
        log("WARNING: --jobs=%d with bare (claude-CLI) model spec(s) %s — "
            "parallel games starve the local claude CLI (decisions time out "
            "and fall back to auto/ai, polluting the eval). Use --jobs 1 or "
            "openrouter:-prefixed specs." % (args.jobs, bare))
    elif not models and args.jobs > 8:
        log("WARNING: --jobs=%d for a no-LLM (floor/random/ai/ai-log) batch — these "
            "are CPU-bound ~25s bursts; >8 concurrent JVMs oversubscribe "
            "cores (see eval/CONCURRENCY.md §7)." % args.jobs)
    elif models and args.jobs > 10:
        log("WARNING: --jobs=%d — openrouter-routed games are RAM-bound "
            "(~1GB RSS/JVM on a 24GB machine); count JVMs across ALL "
            "concurrently running batches, not just this one (see "
            "eval/CONCURRENCY.md §7)." % args.jobs)


# ------------------------------------------------------- seed governance
#
# eval/EVAL-DESIGN.md §6.5 reserves seed blocks (frozen-legacy 501-525,
# puzzle-mining 20000-29999, certification-holdout 30000-30099) that must
# never feed SFT/training data — see eval/seed_policy.py, the shared guard.
# run_batch.py itself is NOT refused here: playing eval games on a reserved
# seed is exactly what those blocks are FOR (frozen anchors, mining runs,
# certification holdouts). This is a non-fatal heads-up only, so a batch
# aimed at a reserved block (deliberately) is not blocked, but a batch that
# reserved seeds slipped into BY ACCIDENT (e.g. a copy-pasted --seeds range)
# gets a loud reminder before the games it writes are later fed to
# make_sft_data.py / mine_puzzles.py (both of which DO refuse).

def warn_reserved_seeds(seeds):
    """Print a loud, non-fatal warning if any requested seed falls in a
    reserved eval block."""
    offenders = seed_policy.check_seeds(seeds)
    if not offenders:
        return
    bar = "=" * 72
    log(bar)
    log("  NOTE: some requested seed(s) are in RESERVED eval blocks (%s)."
        % seed_policy.DOC_POINTER)
    log("  That's fine if this run IS the frozen-anchor/mining/certification")
    log("  use those blocks exist for — but games on these seeds must NEVER")
    log("  be fed into make_sft_data.py / mine_puzzles.py as SFT/training data.")
    for block in sorted(offenders):
        log("  [%s] %s" % (block, ", ".join(str(s) for s in offenders[block])))
    log(bar)


def derive_cap_minutes(args):
    """Per-tier default cap (minutes): no llm seat -> 15; any opus -> 240;
    all-haiku llm -> 120; anything else llm -> 240 (conservative)."""
    models = effective_llm_models(args)
    if not models:
        return 15.0
    if any("opus" in m for m in models):
        return 240.0
    if all("haiku" in m for m in models):
        return 120.0
    # sonnet/other -> use the long cap.
    return 240.0


def default_out_label(args):
    """e.g. 'floor-vs-random' / 'llm-haiku-vs-random' from controllers/models,
    suffixed with the context condition when it departs from the baseline."""
    def side(ctrl, smart):
        ctrl = ctrl or "llm"
        if ctrl == "llm" and smart:
            return "llm-%s" % smart
        return ctrl
    label = "%s-vs-%s" % (side(args.controller_a, args.smart_a),
                          side(args.controller_b, args.smart_b))
    if getattr(args, "context_profile", None):
        label += "-ctx-%s" % re.sub(r"[^A-Za-z0-9._-]+", "_", args.context_profile)
    if getattr(args, "memory_tools", False):
        label += "-memtools"
    return label


# ------------------------------------------------------------------ main

def load_secrets():
    """Auto-load <repo>/secrets.env into the environment (setdefault — real env
    wins), same as model_sweep.py/tournament.py. run_batch.py historically
    lacked this, so a direct invocation from a fresh shell launched JVMs that
    all died at the egress-consent check (2026-07-21 stage0 relaunch: 7 games
    failed at startup before this fix)."""
    path = REPO_ROOT / "secrets.env"
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def preflight_openrouter(args):
    """Refuse up front, with one clear message, instead of spawning a batch of
    JVMs that each crash at LLMBridgeFactory.create()'s key/egress checks."""
    if not any(m.startswith("openrouter:") for m in effective_llm_models(args)):
        return
    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("OPENROUTER_API_KEY not set (secrets.env missing?) — an "
                 "openrouter: model spec is in play; refusing to launch JVMs "
                 "that would all crash at startup.")
    if os.environ.get("OPENROUTER_ALLOW_EGRESS") != "1":
        sys.exit("OPENROUTER_ALLOW_EGRESS=1 not set — egress consent required "
                 "for openrouter: specs (see secrets.env.example); refusing to "
                 "launch JVMs that would all crash at startup.")


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=parse_seeds, default=None,
                    help="'1..20' inclusive range, or '3,7,42' explicit list. Required, except "
                         "with --panel, where it defaults to the panel's published seeds and "
                         "must be a subset of them")
    ap.add_argument("--pairs", action="store_true",
                    help="play each seed twice with SWAP_SEATS flipped (paired design)")
    ap.add_argument("--exploratory", action="store_true",
                    help="opt out of cited-mode guards (mirror deck, forced "
                         "heuristics-off, jobs=1). Numbers become NON-citable.")
    ap.add_argument("--jobs", type=int, default=1,
                    help="parallel game processes (each a separate JVM). "
                         "cited mode requires 1. default 1")
    ap.add_argument("--out",
                    help="output dir for JSONL + stdout logs. Optional: defaults "
                         "to runs/<UTC-timestamp>-<label> under the repo root.")
    ap.add_argument("--forge-home", default=os.path.expanduser("~/code/forge"),
                    help="dir containing run-llm-headless.sh (default ~/code/forge)")
    ap.add_argument("--cap-minutes", type=float, default=None,
                    help="per-game wall-clock cap in minutes; sets GAME_CAP_MS. "
                         "0 disables. Omitted -> derived per-tier (no-llm 15, "
                         "all-haiku 120, opus 240).")
    # deck selection
    ap.add_argument("--deck",
                    help="mirror decklist for BOTH seats (sets --deck-a and "
                         "--deck-b). The cited-mode convenience flag.")
    ap.add_argument("--format", default=None,
                    help="GAME_FORMAT the decks are built for (legacy, modern, standard, pauper). Told to the seat "
                         "and recorded on the game line; a deck that is not legal in it by the engine's own format "
                         "data (eval/deck_legality.py) is refused.")
    ap.add_argument("--deck-a", help="DECK_A path")
    ap.add_argument("--deck-b", help="DECK_B path")
    ap.add_argument("--name-a", help="DECK_A_NAME (identity key; cited default Identity-A)")
    ap.add_argument("--name-b", help="DECK_B_NAME (identity key; cited default Identity-B)")
    # per-seat pass-through (only exported when given, so runner defaults apply)
    ap.add_argument("--smart-a", help="SMART_MODEL_A")
    ap.add_argument("--smart-b", help="SMART_MODEL_B")
    ap.add_argument("--fast-a", help="FAST_MODEL_A")
    ap.add_argument("--fast-b", help="FAST_MODEL_B")
    ap.add_argument("--controller-a", choices=["llm", "random", "floor", "ai", "ai-log"],
                    help="CONTROLLER_A")
    ap.add_argument("--controller-b", choices=["llm", "random", "floor", "ai", "ai-log"],
                    help="CONTROLLER_B")
    ap.add_argument("--plan-a", help="PLAN_A (forbidden in cited mode)")
    ap.add_argument("--plan-b", help="PLAN_B (forbidden in cited mode)")
    ap.add_argument("--shadow-ai", action="store_true",
                    help="set SHADOW_AI=1: overlay the Forge-AI DAgger labeler "
                         "(D4). Additive shadow_choice field only — does NOT "
                         "change the game trajectory (bit-reproducible either "
                         "way). Use with --controller-a llm: floor games label "
                         "almost nothing (they short-circuit before the play "
                         "menu the shadow needs), so DAgger data costs student "
                         "inference.")
    ap.add_argument("--checkpoint",
                    help="identity of the weights under test: a path (adapter dir "
                         "or file, hashed) or an opaque tag. Stamped as "
                         "CHECKPOINT_SHA on every game line. Strongly recommended "
                         "whenever the model spec is an alias the endpoint resolves.")
    ap.add_argument("--context-profile", metavar="NAME",
                    help="CONTEXT_PROFILE for every game (a registered name such "
                         "as v4-faithful, v1-compat, v3-compact; the JVM refuses an "
                         "unknown one at launch). Unset = v4-faithful, the faithful "
                         "seat; v1-compat is the historical baseline and only a named "
                         "arm. Stamped as context_profile/"
                         "context_sha on each game line and in the batch manifest.")
    ap.add_argument("--memory-tools", action="store_true",
                    help="MEMORY_TOOLS=1: give LLM seats the evidence-archive tools "
                         "(MEMORY SEARCH/READ/NOTE). Default off. Agent-plus-tools "
                         "results are a separate condition from unassisted play and "
                         "must live in their own --out.")
    ap.add_argument("--budget-usd", type=float, default=None, metavar="USD",
                    help="stop starting games once this directory's estimated spend (eval/cost_report.py) "
                         "reaches USD. Games in flight finish, so the overshoot is at most --jobs games. "
                         "Rerun with a larger value to resume. Refuses a model with no price on file.")
    ap.add_argument("--retries", type=int, default=1, metavar="N",
                    help="play a game again up to N times when its JVM crashes, writes no game line or hangs "
                         "(default 1). The failed attempt's files move to <out>/crashed/ (still priced, never "
                         "aggregated) and the batch log says [retry]. 0 turns it off.")
    ap.add_argument("--text-tools", action="store_true",
                    help="NATIVE_TOOLS=0: with --memory-tools, use the text tool grammar instead "
                         "of native function calling. Native calling is the standard and is what "
                         "--memory-tools means on openrouter: seats; this flag is the opt-out, for "
                         "reproducing pre-2026-10-13 runs or measuring the protocol. A separate "
                         "condition: its own --out.")
    ap.add_argument("--text-answers", action="store_true",
                    help="ANSWER_TOOL=0: LLM seats answer each decision with a text tag line (CHOICE:, PICKS:, "
                         "BLOCKS: ...) instead of calling the `answer` function. The answer tool is the standard "
                         "wherever a seat is on openrouter:; this flag is the opt-out, for reproducing older runs "
                         "or measuring the protocol. A separate condition (stamped answer_tool); its own --out.")
    ap.add_argument("--answer-tool-strict", action="store_true",
                    help="ANSWER_TOOL_STRICT=1: send `strict: true` with a strict-mode answer schema (support "
                         "varies per OpenRouter endpoint). A separate condition (answer_tool_version ends "
                         "+strict); its own --out.")
    ap.add_argument("--one-tier", action="store_true",
                    help="the default since 2026-10-15 (kept so older command lines still run): every decision "
                         "goes to the smart model with its reasoning and system prompt; no fast tier.")
    ap.add_argument("--two-tier", action="store_true",
                    help="ONE_TIER=0: route 'minor' decisions to the fast tier (no reasoning), as before "
                         "2026-10-15. The harness deciding which questions deserve thought is a shortcut; this "
                         "is for measuring it. A separate condition (stamped one_tier=false); its own --out.")
    ap.add_argument("--no-coaching", action="store_true",
                    help="NO_COACHING=1: the system prompt drops PRINCIPLES and the static reference "
                         "(snapshot legend, worked examples, glossary). A separate condition (stamped "
                         "no_coaching); its own --out.")
    ap.add_argument("--one-chat", action="store_true",
                    help="ONE_CHAT=1: one conversation per seat (OpenRouter lane): each ask follows the seat's "
                         "earlier decisions, shortened to question and answer. Use with --one-tier for one "
                         "mind. A separate condition (stamped one_chat); its own --out.")
    ap.add_argument("--turn-chat", action="store_true",
                    help="TURN_CHAT=1: the seat's conversation holds this game turn's decisions (each kept "
                         "as its calls, results and reasoning) and starts over each turn, so a multi-step "
                         "line stays in one train of thought. A separate condition (stamped turn_chat); "
                         "its own --out.")
    ap.add_argument("--lookahead", action="store_true",
                    help="LOOKAHEAD=1: LLM seats may preview a play option (`simulate`) before "
                         "choosing. Default off; a separate condition from unassisted play and must "
                         "live in its own --out.")
    ap.add_argument("--plan-execute", action="store_true",
                    help="PLAN_EXECUTE=1: the harness itself plays steps of the model's own stated "
                         "turn plan (one smart call per turn rather than one per priority window). "
                         "Default off; a separate condition from unassisted play and must live in "
                         "its own --out.")
    ap.add_argument("--tier-fast-types", metavar="TYPES", default=None,
                    help="TIER_FAST_TYPES=<comma list>: route those decision types to the fast "
                         "(non-reasoning) tier instead of the smart one. Default empty (all smart); "
                         "a separate condition from the default routing and must live in its own "
                         "--out.")
    ap.add_argument("--rules-tool", action="store_true",
                    help="RULES_TOOL=1: give LLM seats the Comprehensive Rules lookup "
                         "(RULES SEARCH/READ/SECTION/TERM/TOC). Default off; a separate "
                         "condition from unassisted play and must live in its own --out.")
    ap.add_argument("--env", action="append", metavar="KEY=VALUE",
                    help="extra env var for the runner (repeatable)")
    ap.add_argument("--panel", choices=["mirror", "cross"],
                    help="launch every cell of an STR-AI panel from --panel-file: identity A is the "
                         "pilot given by --controller-a/--smart-a/--fast-a, identity B the panel's "
                         "opponent (the Forge AI), one directory per cell under --out. The cross "
                         "panel also plays its AI-vs-AI control per cell. Needs --budget-usd, which "
                         "then caps the whole panel. Score with eval/panel_score.py")
    ap.add_argument("--panel-file", default=str(PANEL_FILE),
                    help="panels definition (default eval/suites/panels-v1.json)")
    ap.add_argument("--panel-cells", metavar="ID,ID",
                    help="with --panel: only these cell ids (a partial panel is not scored as valid)")
    ap.add_argument("--no-control", action="store_true",
                    help="with --panel cross: skip the AI-vs-AI control cells")
    ap.add_argument("--sanity-arms", action="store_true",
                    help="after the scored block (each panel cell), play the zero-cost sanity arms on "
                         "the same seeds and decks: identity A as floor, random, and the LLM seat on "
                         "the loopback fake model with --policy empty and --policy garbage. Each arm "
                         "is a sibling directory <out>__sanity-<arm>; eval/sanity_arms.py checks that "
                         "none of them beats the floor. Needs --budget-usd")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.rules_tool and not args.memory_tools:
        raise SystemExit("run_batch: --rules-tool needs --memory-tools: the seat offers the rules lookup only with "
                         "the lookup tools on, so --rules-tool alone records rules_tool=1 for games that never had it")
    for kv in args.env or []:
        k = kv.split("=", 1)[0]
        if k in CONDITION_ENV_KEYS:
            raise SystemExit("run_batch: --env %s is not allowed in any mode; use "
                             "--context-profile / --memory-tools / --rules-tool / --text-tools / --text-answers / --answer-tool-strict / "
                             "--lookahead / --one-tier / --two-tier / --no-coaching / --one-chat / --turn-chat / --plan-execute / "
                             "--tier-fast-types / --format so the condition is recorded and guarded." % k)
    if args.seeds is None and not args.panel:
        ap.error("--seeds is required (except with --panel)")
    if (args.panel or args.sanity_arms) and args.budget_usd is None:
        # A panel is 16 cells: the cost rule (CLAUDE.md, docs/STANDARDS.md section 6) is a budget on
        # the whole thing, never an unbounded launch.
        print("run_batch: REFUSING: --panel and --sanity-arms launch many games; pass --budget-usd "
              "(it caps every paid game they start).", file=sys.stderr)
        sys.exit(2)
    load_secrets()
    if args.panel:
        return run_panel(args)
    rc, out_dir = run_block(args)
    if args.sanity_arms:
        rc = max(rc, run_sanity_arms(args, out_dir))
    return rc


def run_block(args):
    """Launch one block (one directory, one condition). Returns (exit code, out_dir)."""
    missing = unpinned_llm_tiers(args)
    if missing:
        # Every mode: an exploratory run on an unintended model is as wrong as a cited one.
        print("run_batch: REFUSING: an llm seat must name both of its models; unset: %s. A seat with no "
              "--controller-x is an llm seat, and an unset model falls through to the harness default "
              "(issue #234). Pass the models, or --controller-x ai|random|floor." % ", ".join(missing),
              file=sys.stderr)
        sys.exit(2)
    warn_reserved_seeds(args.seeds)
    if not getattr(args, "fake_policy", None):
        preflight_openrouter(args)

    cited = not args.exploratory

    # --deck is convenience for a mirror: it sets both seats.
    if args.deck:
        if args.deck_a or args.deck_b:
            _refuse("--deck sets both seats; do not also pass --deck-a/--deck-b.")
        args.deck_a = args.deck
        args.deck_b = args.deck

    args.native_tools = native_tools_requested(args)
    args.answer_tool = answer_tool_requested(args)
    # One tier is the default (owner, 2026-10-15: no harness shortcuts); --two-tier is the measured opt-out.
    if args.one_tier and args.two_tier:
        ap.error("--one-tier and --two-tier contradict each other")
    args.one_tier = not args.two_tier
    if args.answer_tool_strict and not args.answer_tool:
        _refuse("--answer-tool-strict needs the answer tool (an openrouter: LLM seat, without --text-answers).")

    # Deck paths are made ABSOLUTE here. A game's working directory is the Forge tree
    # (run-llm-headless.sh cd's into forge-gui-desktop), so a relative path never resolves — and
    # because Forge's desktop code starts an AWT thread, the JVM does NOT exit when buildPlayers
    # throws: the game lingers and burns the entire --cap-minutes before timing out. Refuse a
    # missing file now instead of losing the cap (seen 2026-10-13).
    for _k in ("deck_a", "deck_b"):
        _v = getattr(args, _k, None)
        if not _v:
            continue
        _ap = os.path.abspath(os.path.expanduser(_v))
        if not os.path.isfile(_ap):
            _refuse("--%s %s does not exist (paths must resolve; the game runs from the Forge tree)"
                    % (_k.replace("_", "-"), _v))
        setattr(args, _k, _ap)

    # Cited guards 1-3 run FIRST: before forge-home is touched and before any
    # directory is created, so a mirror-guard violation refuses cleanly.
    format_guard(args)
    if cited:
        cited_pre_guards(args)
    else:
        bar = "=" * 72
        log(bar)
        log("  EXPLORATORY MODE — numbers from this run are NOT citable.")
        log("  Mirror-deck / forced-heuristics-off / jobs=1 guards are DISABLED.")
        log("  Re-run without --exploratory (and a mirror deck) for a citable batch.")
        log(bar)
        # Default identity names so exploratory still keys cleanly, if unset.
        args.name_a = args.name_a or "Identity-A"
        args.name_b = args.name_b or "Identity-B"

    # forge-home + runner check
    args.forge_home = Path(args.forge_home).expanduser().resolve()
    runner = args.forge_home / "run-llm-headless.sh"
    if not runner.is_file():
        raise SystemExit("run-llm-headless.sh not found in --forge-home: %s"
                         % args.forge_home)

    # Guards 4-5: provenance env + drift refusal + cap derivation.
    finalize_provenance_and_cap(args, cited)

    # Guard 6: resolve the output dir (default runs/<ts>-<label>) and print it
    # first thing, before creating it or launching anything.
    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_dir = REPO_ROOT / "runs" / ("%s-%s" % (ts, default_out_label(args)))
    log("[out] %s" % out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Guard 7: one directory, one condition. Refuse to resume into a dir whose
    # manifest (or, for a pre-manifest dir, whose game lines) records a
    # different condition; then record this invocation.
    condition = batch_condition(args, cited)
    try:
        manifest = load_manifest(out_dir)
        args.unstamped_lookahead = unstamped_lookahead(manifest)
        args.unstamped_plan_execute = unstamped_play_switch(manifest, "plan_execute", "PLAN_EXECUTE")
        args.unstamped_tier_fast_types = unstamped_play_switch(manifest, "tier_fast_types",
                                                               "TIER_FAST_TYPES")
        problem = check_resume_condition(out_dir, condition, cited)
    except ManifestError as e:
        problem = str(e)
    if problem:
        print("run_batch: REFUSING: " + problem, file=sys.stderr)
        sys.exit(2)
    write_manifest(out_dir, condition, seeds=list(args.seeds), pairs=args.pairs,
                   jobs=args.jobs, argv=sys.argv[1:])

    warn_parallel_jobs(args)

    swaps = [False, True] if args.pairs else [False]
    combos = [(seed, swap) for seed in args.seeds for swap in swaps]

    todo, skipped, foreign = plan_combos(out_dir, combos, resume_expectation(args))
    if skipped:
        log("Resuming: skipping %d/%d combos already complete: %s"
            % (len(skipped), len(combos),
               ", ".join("%d/swap%d" % (s, w) for s, w in skipped)))
    if foreign:
        log("[warn] %d combo(s) have a game line recorded under a DIFFERENT condition "
            "and will be replayed (aggregate.py keeps the two apart): %s"
            % (len(foreign), ", ".join("%d/swap%d" % (s, w) for s, w in foreign)))
    if not todo:
        log("Nothing to do.")
        return 0, out_dir

    log("Running %d games (jobs=%d, out=%s, forge_home=%s)"
        % (len(todo), args.jobs, out_dir, args.forge_home))

    budget = getattr(args, "budget_usd", None)
    # A panel's budget covers every cell under its root, not one cell's directory.
    budget_root = getattr(args, "budget_root", None)
    spent = (lambda: spent_usd_tree(budget_root)) if budget_root else (lambda: spent_usd(out_dir))
    if budget is not None:
        unpriced = [s for s in (args.smart_a, args.fast_a, args.smart_b, args.fast_b)
                    if cost_report.model_id(s) and cost_report.price_of(cost_report.load_prices(), s) is None]
        if unpriced:
            _refuse("--budget-usd needs a price for %s in eval/pricing/prices.json" % ", ".join(sorted(set(unpriced))))
        log("[budget] %s may spend up to $%.2f (already $%.2f); a game is not started past that, "
            "so the overshoot is at most the %d games in flight"
            % ("this panel" if budget_root else "this directory", budget, spent(), args.jobs))

    def over_budget(seed, swap):
        # Checked when a worker picks the game up, not when it is queued, and before every retry.
        if budget is not None and spent() >= budget:
            log("[budget] not started: seed=%d swap=%d ($%.2f spent of $%.2f)" % (seed, swap, spent(), budget))
            return True
        return False

    def guarded(seed, swap):
        return play_with_retries(lambda: run_one(args, seed, swap, out_dir), seed, swap, out_dir,
                                 max(0, getattr(args, "retries", 1)), lambda: over_budget(seed, swap))

    results = []
    if args.jobs <= 1:
        for seed, swap in todo:
            results.append(guarded(seed, swap))
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = [pool.submit(guarded, seed, swap)
                    for seed, swap in todo]
            for fut in as_completed(futs):
                results.append(fut.result())

    ok = [r for r in results if r[2] == "ok"]
    unstarted = [r for r in results if r[2] == "budget"]
    bad = [r for r in results if r[2] not in ("ok", "budget")]
    log("")
    log("=== batch summary ===")
    log("completed: %d  skipped(resume): %d  problems: %d"
        % (len(ok), len(skipped), len(bad)))
    for seed, swap, status, code in sorted(bad):
        log("  seed=%d swap=%d status=%s exit=%d" % (seed, swap, status, code))
    if unstarted:
        log("not started (budget reached): %d; rerun with a larger --budget-usd to resume them" % len(unstarted))
    log("[cost] " + cost_report.fmt(cost_report.report_dir(str(out_dir), cost_report.load_prices())))
    crashed = out_dir / CRASHED_DIR
    if crashed.is_dir():
        log("[cost] crashed attempts (set aside, re-played): "
            + cost_report.fmt(cost_report.report_dir(str(crashed), cost_report.load_prices())))
    log("Aggregate with: python3 %s %s"
        % (Path(__file__).with_name("aggregate.py"), out_dir))
    return (1 if bad else 0), out_dir


# ----------------------------------------------------------- sanity arms

def sanity_arm_args(scored, arm, scored_out):
    """The namespace for one sanity arm of a scored block: identity A replaced, everything else
    (decks, seeds, opponent, cap, context flags) kept, so the arm differs only in who plays A."""
    a = copy.copy(scored)
    for k, v in sanity_arms.arm_overrides(arm).items():
        setattr(a, k, v)
    a.out = str(sanity_arms.arm_dir(scored_out, arm))
    a.checkpoint = None
    a.native_tools = native_tools_requested(a)
    a.loopback_env = None
    # No paid model plays identity A here (floor, random or the loopback stub), so the arm cannot
    # spend the budget; it still runs when the scored block used the budget up, which is exactly
    # when its check is needed. Identity B is the scored block's opponent, never a paid model in a
    # panel.
    a.budget_usd = None if not paid_identity_b(a) else scored.budget_usd
    return a


def paid_identity_b(a):
    """True when identity B is a model seat with a paid price (a PvP block's sanity arms then still
    spend on B, so they keep the budget)."""
    if (a.controller_b or "llm") != "llm":
        return False
    prices = cost_report.load_prices()
    return any((cost_report.price_of(prices, s) or {}).get("prompt", 1) > 0 or not cost_report.model_id(s)
               for s in (a.smart_b, a.fast_b))


def run_sanity_arms(scored, scored_out):
    """Play every sanity arm next to a finished scored block, then print the floor check."""
    rc = 0
    for arm in sanity_arms.ARMS:
        a = sanity_arm_args(scored, arm, scored_out)
        log("[sanity] arm %s -> %s" % (arm, a.out))
        server = None
        try:
            if a.fake_policy:
                # The request log lives outside the game directory, as in harness_acceptance.sh.
                server, port = sanity_arms.start_fake_server(
                    a.fake_policy, Path(a.out + ".server") / "fake_requests.jsonl")
                a.loopback_env = sanity_arms.loopback_env(port)
            rc = max(rc, run_block(a)[0])
        finally:
            if server is not None:
                sanity_arms.stop_fake_server(server)
    verdict = sanity_arms.check(scored_out, float(panel_anchor("random_max_margin_over_floor",
                                                                sanity_arms.DEFAULT_RANDOM_MARGIN,
                                                                getattr(scored, "panel_file", None))))
    sanity_arms.print_human(verdict)
    return max(rc, 1 if verdict["violations"] else 0)


# ----------------------------------------------------------------- panels

def panel_anchor(key, default, panel_file=None):
    try:
        doc = json.loads(Path(panel_file or PANEL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return (doc.get("anchors") or {}).get(key, default)


def spent_usd_tree(root):
    """Estimated spend of every run directory under root (a panel's cells, controls and arms)."""
    root = Path(root)
    if not root.is_dir():
        return 0.0
    # A crashed/ directory is counted by spent_usd of the run directory that holds it, even when every
    # game file of that run directory was set aside.
    dirs = sorted({p.parent.parent if p.parent.name == CRASHED_DIR else p.parent for p in root.rglob("*.jsonl")})
    return sum(spent_usd(d) for d in dirs)


def _panel_refuse(msg):
    """A panel that does not match its frozen definition: no mode bypasses this."""
    print("run_batch: REFUSING (--panel): " + msg, file=sys.stderr)
    sys.exit(2)


def plan_panel(args):
    """[(kind, cell id, namespace)] for every block a panel launch plays, in order, or a refusal.
    kind is 'pilot' or 'control'. Pure: nothing is launched or created."""
    try:
        panel, pool, doc = panel_score.load_panel(args.panel_file, args.panel)
    except panel_score.PanelError as e:
        _panel_refuse(str(e))
    for flag in ("deck", "deck_a", "deck_b", "controller_b", "smart_b", "fast_b"):
        if getattr(args, flag, None):
            _panel_refuse("--panel fixes the decks and identity B; do not pass --%s" % flag.replace("_", "-"))
    drift = []
    for d in pool.values():
        actual = _deck_sha(REPO_ROOT / d["path"])
        if actual != d["sha12"]:
            drift.append("%s (file %s, pinned %s)" % (d["name"], actual, d["sha12"]))
    if drift:
        _panel_refuse("deck file(s) changed since %s was frozen: %s. A new deck pool is a new panels "
                "version." % (args.panel_file, ", ".join(drift)))
    if (args.controller_a or "llm") == "llm":
        # The pilot condition the panels file pins (review E2): the flags must produce exactly it.
        want = (doc.get("pilot_condition") or {}).get("fields") or {}
        r = copy.copy(args)
        r.native_tools, r.answer_tool = native_tools_requested(r), answer_tool_requested(r)
        r.one_tier = not getattr(r, "two_tier", False)
        expect = resume_expectation(r)
        off = ["%s=%s" % (f, json.dumps(v)) for f, v in want.items()
               if [x for x in expect.get(f, [v]) if x is not ABSENT] != [v]]
        if off:
            _panel_refuse("the pilot plays under the panel's pilot_condition (%s); these flags do not give %s"
                          % (args.panel_file, ", ".join(off)))
    published = panel_score.parse_seed_spec(panel["seeds"])
    seeds = args.seeds if args.seeds is not None else published
    stray = sorted(set(seeds) - set(published))
    if stray:
        _panel_refuse("--seeds must be a subset of the panel's published seeds %s (every pilot plays the "
                "same list, so deltas stay seed-paired); not in it: %s" % (panel["seeds"], stray[:10]))
    cells = panel["cells"]
    if args.panel_cells:
        wanted = [c.strip() for c in args.panel_cells.split(",") if c.strip()]
        unknown = sorted(set(wanted) - {c["id"] for c in cells})
        if unknown:
            _panel_refuse("unknown --panel-cells: %s" % ", ".join(unknown))
        cells = [c for c in cells if c["id"] in wanted]
    control = panel.get("control") if not getattr(args, "no_control", False) else None
    if control:
        cseeds = panel_score.parse_seed_spec(control["seeds"])
        # The control plays the same seeds as the pilot and more: a prefix of its list, four times
        # as long as the pilot's block (it is free, and its variance enters every margin).
        n = min(len(cseeds), 4 * len(seeds)) if args.seeds is not None else len(cseeds)
        cseeds = cseeds[:n]
    root = Path(args.out).expanduser().resolve() if args.out else (
        REPO_ROOT / "runs" / ("%s-panel-%s-%s" % (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
                                                   args.panel, default_out_label(args))))
    plan = []
    for c in cells:
        a = copy.copy(args)
        a.deck = None
        a.deck_a = str((REPO_ROOT / pool[c["deck_a"]]["path"]).resolve())
        a.deck_b = str((REPO_ROOT / pool[c["deck_b"]]["path"]).resolve())
        a.controller_b = panel.get("opponent", "ai")
        a.smart_b = a.fast_b = None
        a.seeds = list(seeds)
        a.pairs = bool(panel.get("pairs", True))
        a.out = str(root / c["id"])
        a.budget_root = str(root)
        a.cross_pin = ((pool[c["deck_a"]]["sha12"], pool[c["deck_b"]]["sha12"])
                       if c["deck_a"] != c["deck_b"] else None)
        plan.append(("pilot", c["id"], a))
        if control:
            k = copy.copy(a)
            k.controller_a = control.get("controller_a", "ai")
            k.controller_b = control.get("controller_b", "ai")
            k.smart_a = k.fast_a = None
            k.checkpoint = None
            k.seeds = list(cseeds)
            k.out = str(root / (c["id"] + panel_score.CONTROL_SUFFIX))
            plan.append(("control", c["id"], k))
    return root, plan


def run_panel(args):
    root, plan = plan_panel(args)
    log("[panel] %s: %d block(s) under %s, budget $%.2f for the whole panel"
        % (args.panel, len(plan), root, args.budget_usd))
    rc = 0
    for kind, cid, a in plan:
        log("[panel] %s %s -> %s" % (kind, cid, a.out))
        code, out_dir = run_block(a)
        rc = max(rc, code)
        if kind == "pilot" and args.sanity_arms:
            rc = max(rc, run_sanity_arms(a, out_dir))
    log("[cost] panel total $%.2f" % spent_usd_tree(root))
    log("Score with: python3 %s %s --panel %s" % (Path(__file__).with_name("panel_score.py"), root, args.panel))
    return rc


if __name__ == "__main__":
    sys.exit(main())
