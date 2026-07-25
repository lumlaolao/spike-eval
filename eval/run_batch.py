#!/usr/bin/env python3
"""Batch driver for the MTG Forge LLM eval.

Orchestrates N headless games by invoking `bash run-llm-headless.sh` from
FORGE_HOME with per-game env vars (GAME_SEED, SWAP_SEATS, EVAL_OUT, ...).
Each game is a separate JVM process. Resumable: seed/swap combos whose JSONL
in --out already contains a final game line are skipped.

Two modes:
  CITED (default) — the only mode whose numbers may be quoted. Enforces a
    mirror-deck setup (same decklist both seats, distinct identity names),
    forces the eval-confound switches off (MULLIGAN_HEURISTIC=0,
    SKIP_HEURISTICS=0, no plans), pins --jobs 1, and stamps full provenance
    (engine/overlay/deck shas + discipline). Refuses to run on any violation
    or on spike-file drift.
  --exploratory — skips the mirror/heuristic/jobs guards for quick pokes.
    Still stamps provenance (discipline=exploratory) but prints a loud banner
    that the numbers are NOT citable.

Examples:
  # cited mirror run (numbers citable)
  python3 eval/run_batch.py --seeds 1..20 --pairs --deck eval/decks/mardu-energy.txt \
      --controller-a llm --controller-b llm --smart-a haiku --smart-b haiku
  # exploratory poke (not citable)
  python3 eval/run_batch.py --seeds 3,7,42 --exploratory --jobs 2 \
      --deck-a eval/decks/grixis-reanimator.txt --deck-b eval/decks/mardu-energy.txt
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_policy  # noqa: E402  seed governance (EVAL-DESIGN.md §6.5)

PRINT_LOCK = threading.Lock()

# Repo root = parent of eval/. runs/ and forge-overlay/ live here.
REPO_ROOT = Path(__file__).resolve().parent.parent
SPIKE_REL = "forge-gui-desktop/src/main/java/forge/spike"
# Concatenated in THIS order for the OVERLAY_SHA (Gui then Headless).
SPIKE_FILES = ("LlmSpikeGui.java", "LlmSpikeHeadless.java")
# Env keys cited mode owns; a user --env override of these is refused.
CITED_FORCED_KEYS = ("MULLIGAN_HEURISTIC", "SKIP_HEURISTICS", "PLAN_A", "PLAN_B")
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
)


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


def _overlay_sha(installed_dir):
    """sha256[:12] over the two INSTALLED spike files (Gui then Headless,
    concatenated). 'unknown' if either is unreadable."""
    blobs = []
    for name in SPIKE_FILES:
        b = _read_bytes(Path(installed_dir) / name)
        if b is None:
            return "unknown"
        blobs.append(b)
    return _sha12_bytes(b"".join(blobs))


def _deck_sha(path):
    """sha256[:12] of the decklist file, or 'unknown'."""
    if not path:
        return "unknown"
    b = _read_bytes(path)
    return _sha12_bytes(b) if b is not None else "unknown"


def _spike_drift(installed_dir, overlay_dir):
    """Filenames whose INSTALLED bytes differ from this checkout's overlay
    copy (or that are missing on either side). Empty list == no drift."""
    drift = []
    for name in SPIKE_FILES:
        inst = _read_bytes(Path(installed_dir) / name)
        over = _read_bytes(Path(overlay_dir) / name)
        if inst is None or over is None or inst != over:
            drift.append(name)
    return drift


# ------------------------------------------------------------- env build

def build_env(args, seed, swap, out_dir):
    env = os.environ.copy()
    # Scrub every run-defining / heuristic key from the ambient environment:
    # only flags, --env (re-applied below), and forced_env may set them.
    for k in RUN_DEFINING_KEYS + CITED_FORCED_KEYS:
        env.pop(k, None)
    env["GAME_SEED"] = str(seed)
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
    # was widened from a flat 15s to LlmSpikeGui.ModelCfg.maxConfiguredTimeoutMs()
    # + 10s (~160s; the built-in ModelCfg table's max is kimi's 150s smart-tier
    # timeout) in the 4cfdaec cap-shutdown-race fix. So a healthy game's total
    # wall time ~= [JVM boot + card-script load + deck load, BEFORE the cap timer]
    # + GAME_CAP_MS + ~160s in-JVM post-cap grace + writeGameSummary/shutdown
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
    # (ModelCfg.maxConfiguredTimeoutMs) is Java-side and not cheaply callable from
    # Python. 200 = ~160s built-in grace + margin for a moderately raised
    # OPENROUTER_MODEL_CFG timeoutMs override.
    GRACE_S = 200
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
    log("[done] seed=%d swap=%d" % (seed, swap))
    return (seed, swap, "ok", code)


def has_game_line(out_dir, seed, swap):
    """True if some game-<seed>-*.jsonl in out_dir has a final game line
    matching this seed/swap combo. Defensive: skip unparseable lines."""
    for path in Path(out_dir).glob("game-%d-*.jsonl" % seed):
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
                    if not isinstance(rec, dict) or rec.get("t") != "game":
                        continue
                    if rec.get("seed") == seed and bool(rec.get("swap", False)) == swap:
                        return True
        except OSError:
            continue
    return False


# ----------------------------------------------------------- cited guards

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
    if pa != pb:
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
    if args.plan_a or args.plan_b:
        _refuse("plans (PLAN_A/PLAN_B) are an expert-knowledge confound and are "
                "forbidden in cited mode.")

    # --- guard 3: single JVM ---
    if args.jobs != 1:
        _refuse("cited mode requires --jobs 1 (got %d); parallel JVMs starve the "
                "claude CLI and inflate fallbacks." % args.jobs)


def finalize_provenance_and_cap(args, cited):
    """Guards 4-5 (spec C): derive the per-tier cap when --cap-minutes was
    omitted, compute provenance env, and (cited only) refuse on spike drift.
    Requires args.forge_home already resolved."""
    installed_dir = args.forge_home / SPIKE_REL
    overlay_dir = REPO_ROOT / "forge-overlay" / SPIKE_REL

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
    drift = _spike_drift(installed_dir, overlay_dir)
    if drift:
        if cited:
            _refuse("installed spike file(s) differ from this checkout's "
                    "forge-overlay: %s — run `bash install.sh` first."
                    % ", ".join(drift))
        else:
            log("[warn] spike drift (%s) — installed JVM code != this overlay."
                % ", ".join(drift))

    forced["RUN_DISCIPLINE"] = "cited" if cited else "exploratory"
    forced["ENGINE_SHA"] = _engine_sha(args.forge_home)
    forced["OVERLAY_SHA"] = _overlay_sha(installed_dir)
    forced["DECK_SHA"] = _deck_sha(args.deck_a or args.deck_b)
    args.forced_env = forced

    # Guard 4: per-tier cap derivation (only when not explicitly given).
    if args.cap_minutes is None:
        args.cap_minutes = derive_cap_minutes(args)
        log("[cap] --cap-minutes not given; derived %g min from controllers/models."
            % args.cap_minutes)

    log("[provenance] engine=%s overlay=%s deck=%s discipline=%s"
        % (forced["ENGINE_SHA"], forced["OVERLAY_SHA"], forced["DECK_SHA"],
           forced["RUN_DISCIPLINE"]))


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
    """e.g. 'floor-vs-random' / 'llm-haiku-vs-random' from controllers/models."""
    def side(ctrl, smart):
        ctrl = ctrl or "llm"
        if ctrl == "llm" and smart:
            return "llm-%s" % smart
        return ctrl
    return "%s-vs-%s" % (side(args.controller_a, args.smart_a),
                         side(args.controller_b, args.smart_b))


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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", required=True, type=parse_seeds,
                    help="'1..20' inclusive range, or '3,7,42' explicit list")
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
    ap.add_argument("--env", action="append", metavar="KEY=VALUE",
                    help="extra env var for the runner (repeatable)")
    args = ap.parse_args()

    warn_reserved_seeds(args.seeds)
    load_secrets()
    preflight_openrouter(args)

    cited = not args.exploratory

    # --deck is convenience for a mirror: it sets both seats.
    if args.deck:
        if args.deck_a or args.deck_b:
            _refuse("--deck sets both seats; do not also pass --deck-a/--deck-b.")
        args.deck_a = args.deck
        args.deck_b = args.deck

    # Cited guards 1-3 run FIRST: before forge-home is touched and before any
    # directory is created, so a mirror-guard violation refuses cleanly.
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

    warn_parallel_jobs(args)

    swaps = [False, True] if args.pairs else [False]
    combos = [(seed, swap) for seed in args.seeds for swap in swaps]

    todo, skipped = [], []
    for seed, swap in combos:
        if has_game_line(out_dir, seed, swap):
            skipped.append((seed, swap))
        else:
            todo.append((seed, swap))
    if skipped:
        log("Resuming: skipping %d/%d combos already complete: %s"
            % (len(skipped), len(combos),
               ", ".join("%d/swap%d" % (s, w) for s, w in skipped)))
    if not todo:
        log("Nothing to do.")
        return 0

    log("Running %d games (jobs=%d, out=%s, forge_home=%s)"
        % (len(todo), args.jobs, out_dir, args.forge_home))

    results = []
    if args.jobs <= 1:
        for seed, swap in todo:
            results.append(run_one(args, seed, swap, out_dir))
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = [pool.submit(run_one, args, seed, swap, out_dir)
                    for seed, swap in todo]
            for fut in as_completed(futs):
                results.append(fut.result())

    ok = [r for r in results if r[2] == "ok"]
    bad = [r for r in results if r[2] != "ok"]
    log("")
    log("=== batch summary ===")
    log("completed: %d  skipped(resume): %d  problems: %d"
        % (len(ok), len(skipped), len(bad)))
    for seed, swap, status, code in sorted(bad):
        log("  seed=%d swap=%d status=%s exit=%d" % (seed, swap, status, code))
    log("Aggregate with: python3 %s %s"
        % (Path(__file__).with_name("aggregate.py"), out_dir))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
