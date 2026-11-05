#!/usr/bin/env python3
"""Run one model on one split of a frozen PUZ suite, k attempts per puzzle (docs/eval/PUZZLES.md).

    python3 eval/puzzles/puzzle_solve.py --suite eval/suites/puzzles-forge-builtin-v1.json --split open \\
        --model openrouter:openai/gpt-6-luna --budget-usd 1.00 --out runs/puzzles/luna-open

Each active puzzle of the split gets one JVM with PZL_SEEDS = the k attempt seeds, CONTROLLER_A=llm and
the model on both tiers; every result row is stamped with the suite's sha256, so puzzle_score.py
refuses to pool it with another suite version. The run stops starting puzzles once the directory's
estimated spend (eval/cost_report.py) reaches --budget-usd, which is required, and prints the cost so
far after each puzzle. Then score it:

    python3 eval/puzzles/puzzle_score.py --suite <suite> --split open runs/puzzles/luna-open

--resume continues a stopped run (docs/eval/PUZZLES.md, "Running a model").

Every run keeps its full record (owner, 2026-10-25): each JVM runs with TRANSCRIPTS=1, so every HTTP round-trip to the
model is kept byte for byte and each game's whole Forge log is kept, under <puzzle>/transcripts/ (Transcript.java);
the .pzl the JVM read and the suite are copied into the run. eval/archive_run.py packs and verifies a finished run.

An openrouter: model sends each puzzle's position to openrouter.ai: the egress opt-in
(OPENROUTER_ALLOW_EGRESS=1, normally in secrets.env) is required and never set here. So does a
jev: decision model (OpenRouter's Decisions API, DecisionsBridge), which also cannot take --memory-tools.

--max-price-at-list (an openrouter: model only) caps every request at the model's list price (OPENROUTER_MAX_PRICE);
--quantizations fp8,bf16 serves it only at those precisions (OPENROUTER_QUANTIZATIONS). docs/eval/PUZZLES.md says how and
what the manifest records.

A claude-cli: model (the local Claude Code CLI, ClaudeCliBridge) is a development lane on the operator's own Claude
subscription (owner, 2026-10-24): it runs only with CLAUDE_CLI_SEAT=1, and its rows are stamped dev_lane, which
puzzle_score.py labels as not a benchmark score. It sends each position to Anthropic under the CLI's own login, not
to openrouter.ai, so it needs no OPENROUTER_ALLOW_EGRESS; it cannot take --memory-tools, runs with -j 1 (each JVM
shells out to the one local CLI), and its spend is the CLI's own reported cost. --claude-cli-effort and
--claude-cli-thinking-tokens pin its CLI effort and thinking budget (unset: the CLI's defaults), never the shell's.
"""

import argparse
import decimal
import functools
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import cost_report  # noqa: E402
import puzzle_baselines as pb  # noqa: E402
import puzzle_format as pf  # noqa: E402
import puzzle_score as ps  # noqa: E402
import versions  # noqa: E402

# The attempt seeds: k distinct seeds per puzzle, the same for every solver, outside every reserved band.
ATTEMPT_BASE = 1
RUN_FILE = "puzzle-run.json"
# What makes two builds one harness for --resume: the overlay compiled, and whether a stale build was let run.
BUILD_KEYS = ("build_overlay_sha", "stale_build_allowed")
SUITE_COPY = "suite.json"
TRANSCRIPT_CLASS = "forge/spike/Transcript.class"


def transcript_version():
    """Transcript.VERSION, as eval/versions.py reads it from the overlay."""
    return dict((name, value) for name, value, _, _ in versions.read_stamps())["Transcript.VERSION"]


def records(build):
    """True when the build has Transcript compiled in: a build from before it writes no record."""
    classes = getattr(build, "classes", None)
    return isinstance(classes, (str, Path)) and (Path(classes) / TRANSCRIPT_CLASS).is_file()


def keep_pzl(rec, out):
    """A byte copy of the position the JVM is about to read, so a run directory reconstructs its games without the
    checkout (a forge: puzzle lives in the Forge checkout, which install.sh overwrites). Returns its name."""
    name = Path(rec["pzl"].split(":", 1)[-1]).name
    shutil.copyfile(str(pf.pzl_path(rec)), str(Path(out) / name))
    return name


def load_secrets():
    path = pf.REPO / "secrets.env"
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def is_loopback(url):
    """True when url names this machine (LlmSpikeGui.LLMBridgeFactory.isLoopback)."""
    from urllib.parse import urlparse
    try:
        return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")
    except ValueError:
        return False


def spent(out_root, bound=False):
    """What the run's directories cost at the list price; `bound`: at each model's dearest endpoint, as the budget
    is checked."""
    prices = cost_report.load_prices()
    prices = cost_report.budget_prices(prices) if bound else prices
    return sum(cost_report.report_dir(str(d), prices)["usd"] for d in Path(out_root).glob("*") if d.is_dir())


def over_budget(out_root, budget_usd):
    """The budget is checked at each model's dearest endpoint, never at the list price."""
    return spent(out_root, bound=True) >= budget_usd


def condition(a):
    """Every setting that changes what the seat sees, pinned explicitly (never inherited from the shell),
    as run_batch.py pins a game's: the same seat as the games it is compared with. A claude-cli: model's effort is
    pinned too (both tiers, so a shell's CLAUDE_BRIDGE_EFFORT_SMART cannot leak in); no other model's condition has it."""
    cond = {"CONTEXT_PROFILE": a.context_profile, "MEMORY_TOOLS": "1" if a.memory_tools else "0",
            "RULES_TOOL": "1" if a.rules_tool else "0", "NATIVE_TOOLS": "1", "ANSWER_TOOL": "1",
            "LOOKAHEAD": "0", "MULLIGAN_HEURISTIC": "0", "SKIP_HEURISTICS": "0"}
    # The hidden-menu arm only when asked for, so a directory from before it resumes unchanged.
    if getattr(a, "propose_actions", False):
        cond["PROPOSE_ACTIONS"] = "1"
    if getattr(a, "propose_terse", False):
        cond["PROPOSE_TERSE"] = "1"
    if cost_report.is_cli_lane(getattr(a, "model", None)):
        cond.update(cost_report.cli_env(getattr(a, "claude_cli_effort", None), getattr(a, "claude_cli_thinking_tokens", None)))
    return cond


# OpenRouter's precision names (provider routing docs, 2026-10-26); OpenRouterBridge.QUANTIZATIONS in Java.
QUANTIZATIONS = ("int4", "int8", "fp4", "mxfp4", "nvfp4", "fp6", "fp8", "mxfp8", "fp16", "bf16", "fp32", "unknown")


def max_price_env(prices, spec):
    """OPENROUTER_MAX_PRICE for `spec`: {"<model>": {"prompt": P, "completion": C}}, its list price in USD per million
    tokens. Decimal, from the price as written: a float product (2e-06 * 1e6 = 1.9999999999999998) can fall just
    under list and rule out the list-price endpoint itself."""
    p = cost_report.price_of(prices, spec)
    per_m = {k: decimal.Decimal(repr(p[k])).scaleb(6).normalize() for k in ("prompt", "completion")}
    return "{%s: {%s}}" % (json.dumps(cost_report.model_id(spec)),
                           ", ".join('"%s": %s' % (k, format(v, "f")) for k, v in per_m.items()))


def already_done(out, k, suite_sha, cap_ms=None):
    """True when `out` holds a result row on this suite for each of the k attempt seeds: a resumed run skips it. A
    puzzle played under a game cap below `cap_ms` is not done: the cap is a condition, so all its attempts are played
    again (replaying only the games it cut off would redraw only slow games, a bias toward fast ones)."""
    if cap_ms is not None and ((ps.record_of(Path(out)) or {}).get("game_cap_ms") or ps.LEGACY_GAME_CAP_MS) < cap_ms:
        return False
    seeds = {r.get("seed") for r in pb.read_results(out)
             if r.get("suite_sha256") == suite_sha and not r.get("routing_refusals")}
    return all(s in seeds for s in range(ATTEMPT_BASE, ATTEMPT_BASE + k))


def set_aside_partial(out):
    """A puzzle cut off part way is played again from its first seed; its rows so far and its JVM log are renamed,
    so a scorer never counts them and the replay does not overwrite them, and its game rows stay, so cost_report
    still prices what was spent. The manifest is copied beside them: the replay rewrites it, and the set-aside rows
    keep the condition they were played under (a lower game cap)."""
    out = Path(out)
    n = max(len(list(out.glob("puzzle-results.partial*.jsonl"))), len(list(out.glob("jvm.partial*.log"))))
    for name, aside in (("puzzle-results.jsonl", "puzzle-results.partial%d.jsonl"), ("jvm.log", "jvm.partial%d.log")):
        if (out / name).is_file():
            (out / name).rename(out / (aside % n))
    if (out / "batch-manifest.json").is_file():
        shutil.copyfile(str(out / "batch-manifest.json"), str(out / ("batch-manifest.partial%d.json" % n)))


def prepare(out, k, suite_sha, resume, cap_ms=None, can_run=lambda: True):
    """"done" when a resumed run already has this puzzle's k attempts; "skip" when it would run and `can_run()` says
    no (the budget), with nothing moved; otherwise "run", a partial set aside."""
    if resume and already_done(out, k, suite_sha, cap_ms):
        return "done"
    if not can_run():
        return "skip"
    if resume:
        set_aside_partial(out)
    return "run"


def write_json(path, obj):
    """Written whole, then moved, so a restart (or a reader, eval/puzzles/puzzle_watch.py) never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def write_manifest(out, model, suite_sha, puzzle_id, cond, build=None, record=None):
    """The condition a puzzle's directory was played under; cost_report prices its calls from it when there is no
    game row (a puzzle has none). `build`: puzzle_baselines.Build.provenance(), the build it ran on. `record`: what
    the directory keeps beyond the rows (the transcript version, the .pzl copy) and the run conditions puzzle_score
    reads from it (game cap, call deadline, token cap; docs/STANDARDS.md section 4)."""
    m = {"condition": {"smart_a": model, "fast_a": model, "puzzle": puzzle_id, "suite_sha256": suite_sha, "env": cond},
         "build": build}
    if record is not None:
        m["record"] = record
    write_json(Path(out) / "batch-manifest.json", m)


def write_run(out_root, ids, k, budget_usd, finished, stopped=None, build=None, suite_file=None):
    """RUN_FILE: what this invocation will play, so eval/puzzles/puzzle_watch.py can show the puzzles not started yet, tell
    when each is done (k result rows) and when the run is over (`finished`, written after the last puzzle) or was
    stopped part way (`stopped`: the exception or interrupt that ended it). `build`: the build the whole run uses
    (Build.provenance(), with stale_build_allowed when --allow-stale-build let an old one run)."""
    run = {"ids": ids, "k": k, "budget_usd": budget_usd, "finished": finished}
    if stopped is not None:
        run["stopped"] = stopped
    if build is not None:
        run["build"] = build
    if suite_file is not None:
        run["suite_file"] = suite_file
    write_json(Path(out_root) / RUN_FILE, run)


def resume_conflicts(out_root, ids, model, suite_sha, cond, build, max_price=None, quantizations=None, cap_ms=None,
                     call_ms=None, max_tokens=None):
    """The puzzle directories under out_root made under another model, suite or seat condition, or on another
    build (`build`: Build.provenance(); its compiled overlay and whether it was let run stale), or price ceiling
    (`max_price`, max_price_env's JSON or None: a ceiling can move a request to another provider): a resume into them
    would pool two conditions in one score (STANDARDS section 4). A directory from before builds were recorded is
    another build; one from before the ceiling existed was uncapped. `quantizations`: the precisions allowed (a
    list or None), compared the same way: it is what the model ran at. `cap_ms`: the game cap may rise on a resume
    (already_done replays every puzzle played under the lower one), never fall below what a puzzle was played under.
    `call_ms`, `max_tokens`: the call deadline and token cap, refused under any other value."""
    bad, now = [], {"smart_a": model, "suite_sha256": suite_sha, "env": cond}
    ceiling = json.loads(max_price) if max_price else None
    for pid in ids:
        f = Path(out_root) / pid / "batch-manifest.json"
        if not f.is_file():
            continue
        try:
            manifest = json.loads(f.read_text())
        except ValueError:
            bad.append("%s: unreadable batch-manifest.json" % pid)
            continue
        was, was_build, was_record = (manifest.get(k) or {} for k in ("condition", "build", "record"))
        diff = [key for key in now if was.get(key) != now[key]]
        diff += ["build." + key for key in BUILD_KEYS if was_build.get(key) != build.get(key)]
        diff += [key for key, v in (("openrouter_max_price", ceiling), ("openrouter_quantizations", quantizations),
                                    ("call_timeout_ms", call_ms), ("max_tokens", max_tokens))
                 if was_record.get(key) != v]
        if cap_ms is not None and (was_record.get("game_cap_ms") or ps.LEGACY_GAME_CAP_MS) > cap_ms:
            diff.append("game_cap_ms (lower than played)")
        if diff:
            bad.append("%s: %s differ" % (pid, ", ".join(diff)))
    return bad


# The post-cap grace, as run_batch.py's GRACE_S (pinned by test_the_grace_is_run_batchs): a capped game finishes
# the decision in flight (maxDecisionMs() + 10 s).
GRACE_S = 1000
# ModelCfg's table keys (LlmSpikeGui.ModelCfg.buildTable): OPENROUTER_MODEL_CFG overrides a key it names, and a
# model takes the first key its id contains, else "default", so --max-tokens names every one.
MODEL_CFG_KEYS = ("opus", "sonnet", "haiku", "deepseek", "gpt", "kimi", "default")


def solve_puzzle(build, rec, model, k, out_root, suite_sha, cond, recorded=True, max_price=None, quantizations=None,
                 cap_ms=ps.LEGACY_GAME_CAP_MS, call_ms=None, max_tokens=None):
    out = Path(out_root) / rec["id"]
    out.mkdir(parents=True, exist_ok=True)
    record = {"transcript_version": transcript_version() if recorded else None, "pzl_file": keep_pzl(rec, out),
              "pzl_sha256": rec.get("pzl_sha256"),
              "openrouter_max_price": json.loads(max_price) if max_price else None,
              "openrouter_quantizations": quantizations, "game_cap_ms": cap_ms,
              "call_timeout_ms": call_ms, "max_tokens": max_tokens}
    write_manifest(out, model, suite_sha, rec["id"], cond, build.provenance(), record)
    # TRANSCRIPTS is a recording switch, not a condition: it changes no byte the model is sent (TranscriptTest).
    env = dict(cond, PUZZLE=str(pf.pzl_path(rec)), CONTROLLER_A="llm", CONTROLLER_B="ai",
               PZL_SEEDS="%d..%d" % (ATTEMPT_BASE, ATTEMPT_BASE + k - 1), PZL_SUITE_SHA256=suite_sha,
               SMART_MODEL_A=model, FAST_MODEL_A=model, GAME_CAP_MS=str(cap_ms), TRANSCRIPTS="1")
    if call_ms:
        env["OPENROUTER_TIMEOUT_MS"] = str(call_ms)   # every tier's deadline; Build.run strips it from the shell
    if max_tokens:
        env["OPENROUTER_MODEL_CFG"] = json.dumps({k: {"maxTokens": max_tokens} for k in MODEL_CFG_KEYS})
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_ALLOW_EGRESS", "OPENROUTER_BASE_URL"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    if max_price:
        env["OPENROUTER_MAX_PRICE"] = max_price
    if quantizations:
        env["OPENROUTER_QUANTIZATIONS"] = json.dumps({cost_report.model_id(model): quantizations})
    # Past every attempt's game cap and the grace after it, so the JVM is never killed while a game may still end.
    rc = build.run(env, out, timeout_s=max(4 * 3600, k * (cap_ms // 1000 + GRACE_S) + 3600), keep_model_env=True)
    rows = pb.read_results(out)
    return rec["id"], rc, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--split", required=True, choices=pf.SPLITS)
    ap.add_argument("--model", required=True)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--budget-usd", type=float, required=True)
    ap.add_argument("--ids", default=None, help="only these puzzle ids (comma-separated): a pilot, not a score")
    ap.add_argument("--out", required=True)
    ap.add_argument("-j", "--jobs", type=int, default=2)
    ap.add_argument("--context-profile", default="v4-faithful")
    ap.add_argument("--memory-tools", action="store_true")
    ap.add_argument("--rules-tool", action="store_true")
    ap.add_argument("--propose-actions", action="store_true",
                    help="PROPOSE_ACTIONS=1: the hidden-menu arm; the seat proposes each play and the engine checks it")
    ap.add_argument("--propose-terse", action="store_true",
                    help="PROPOSE_TERSE=1, with --propose-actions: a refused proposal is told only that it is illegal")
    ap.add_argument("--build", default=None)
    ap.add_argument("--allow-no-record", action="store_true",
                    help="run on a build without Transcript (no http or engine record; the manifest says so)")
    ap.add_argument("--allow-stale-build", action="store_true",
                    help="run --build although it was not compiled from this checkout's overlay (recorded)")
    ap.add_argument("--forge", default=None)
    ap.add_argument("--claude-cli-effort", choices=("low", "medium", "high", "xhigh", "max"), default=None,
                    help="a claude-cli: model only: the CLI's --effort (CLAUDE_BRIDGE_EFFORT); unset is the CLI's default")
    ap.add_argument("--claude-cli-thinking-tokens", type=int, default=None, metavar="N",
                    help="a claude-cli: model only: the CLI's MAX_THINKING_TOKENS; unset is the CLI's default")
    ap.add_argument("--max-price-at-list", action="store_true",
                    help="an openrouter: model only: send a price ceiling at its list price (no priority tier)")
    ap.add_argument("--quantizations", default=None, metavar="LIST",
                    help="an openrouter: model only: the precisions it may be served at, comma-separated (fp8,bf16)")
    ap.add_argument("--game-cap-minutes", type=int, default=ps.LEGACY_GAME_CAP_MS // 60000,
                    help="wall-clock cap on each attempt's game, whole minutes (default %(default)s); a capped game counts "
                         "as unsolved")
    ap.add_argument("--call-timeout-minutes", type=int, default=None,
                    help="deadline on each model call, whole minutes (default: each model's own); a call past it is "
                         "retried, then its decision goes to a fixed legal option")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="max_tokens on each smart-tier call, its thinking budget too (default: each model's own, "
                         "32000); the fast tier keeps 8192 with reasoning off")
    ap.add_argument("--resume", action="store_true",
                    help="skip puzzles whose k attempts on this suite are already in --out (a run stopped part way)")
    a = ap.parse_args(argv)
    if a.propose_terse and not a.propose_actions:
        raise SystemExit("--propose-terse without --propose-actions: it words the arm's refusals, and a menu game has none")
    if cost_report.lane_of(a.model) and a.memory_tools:
        raise SystemExit("--memory-tools with a %s: model: such a seat cannot call a lookup, so the run would record "
                         "MEMORY_TOOLS=1 for a seat that never used it (the JVM refuses it too)" % a.model.split(":", 1)[0])
    refusal = cost_report.cli_opt_in_refusal([a.model], os.environ)
    if refusal:
        raise SystemExit(refusal)
    refusal = cost_report.cli_flag_refusal([a.model], a.claude_cli_effort, a.claude_cli_thinking_tokens)
    if refusal:
        raise SystemExit(refusal)
    if cost_report.is_cli_lane(a.model) and a.jobs != 1:
        raise SystemExit("-j 1 with a %s model: each puzzle's JVM shells out to the one local Claude CLI, and parallel ones "
                         "contend for it, so decisions time out and are lost (run_batch.py's cited rule)" % cost_report.CLI_PREFIX)
    for flag, on in (("--max-price-at-list", a.max_price_at_list), ("--quantizations", a.quantizations is not None)):
        if on and not a.model.startswith("openrouter:"):
            raise SystemExit("%s is for an openrouter: chat model; %s sends no provider preferences" % (flag, a.model))
    # A condition the seat's bridge never reads would split a pair or a resume over a difference that is not there:
    # ClaudeCliBridge reads neither, DecisionsBridge (jev:) reads only OPENROUTER_TIMEOUT_MS.
    for flag, on, prefixes in (("--max-tokens", a.max_tokens is not None, ("openrouter:",)),
                               ("--call-timeout-minutes", a.call_timeout_minutes is not None, ("openrouter:", "jev:"))):
        if on and not a.model.startswith(prefixes):
            raise SystemExit("%s is for a %s model; %s never reads it" % (flag, " or ".join(prefixes), a.model))
    quantizations = None
    if a.quantizations is not None:
        # Sorted and deduplicated: a set of precisions, so the order typed never splits a resume or a condition.
        quantizations = sorted({q.strip() for q in a.quantizations.split(",") if q.strip()})
        unknown = [q for q in quantizations if q not in QUANTIZATIONS]
        if unknown or not quantizations:
            raise SystemExit("--quantizations takes precisions from %s (OpenRouter's names), got %r"
                             % (", ".join(QUANTIZATIONS), a.quantizations))
    if a.game_cap_minutes < 1:
        raise SystemExit("--game-cap-minutes must be at least 1")
    cap_ms = a.game_cap_minutes * 60000
    if a.call_timeout_minutes is not None and a.call_timeout_minutes < 1:
        raise SystemExit("--call-timeout-minutes must be at least 1")
    call_ms = a.call_timeout_minutes * 60000 if a.call_timeout_minutes else None
    # A resume that played the seeds a capped game skipped would redraw only slow puzzles (docs/eval/PUZZLES.md).
    if call_ms and call_ms > 5 * 60000 and a.k > 1:
        raise SystemExit("--call-timeout-minutes above 5 needs --k 1: the post-cap grace is sized from ModelCfg, so a "
                         "capped game could skip its puzzle's later seeds (docs/eval/PUZZLES.md)")
    if a.max_tokens is not None and not 1 <= a.max_tokens < 2 ** 31:
        raise SystemExit("--max-tokens must be from 1 to 2^31-1 (the JVM reads it as an int)")
    if a.rules_tool and not a.memory_tools:
        raise SystemExit("--rules-tool needs --memory-tools: the seat offers the rules lookup only with the lookup "
                         "tools on, so --rules-tool alone records RULES_TOOL=1 for puzzles that never had it")
    # cost_report prices an unpriced model at $0, so --budget-usd would never stop it (run_batch.py refuses too).
    if cost_report.model_id(a.model) and cost_report.price_of(cost_report.load_prices(), a.model) is None:
        raise SystemExit("--budget-usd needs a price for %s in eval/pricing/prices.json"
                         % cost_report.model_id(a.model))

    manifest = pf.load_suite(a.suite)
    problems = pf.validate_suite(manifest)
    if problems or not manifest.get("frozen"):
        raise SystemExit("suite does not validate or is not frozen: %s" % "; ".join(problems[:5]))
    load_secrets()
    # A loopback endpoint (the acceptance fake model) sends nothing off the machine, as the JVM's own check says.
    if (a.model.startswith(cost_report.API_PREFIXES) and os.environ.get("OPENROUTER_ALLOW_EGRESS") != "1"
            and not is_loopback(os.environ.get("OPENROUTER_BASE_URL", ""))):
        raise SystemExit("OPENROUTER_ALLOW_EGRESS=1 is not set: an openrouter: or jev: model sends each position to "
                         "openrouter.ai, and that needs the owner's opt-in (secrets.env)")
    max_price = None
    if a.max_price_at_list:
        prices = cost_report.load_prices()
        listed = cost_report.price_of(prices, a.model)
        if not all(listed[k] > 0 for k in ("prompt", "completion")):
            raise SystemExit("--max-price-at-list needs a list price above 0 for %s (eval/pricing/prices.json): "
                             "every request would be refused" % a.model)
        max_price = max_price_env(prices, a.model)
        print("[puzzles] price ceiling %s (list price checked %s; a puzzle that loses decisions to it on every resume "
              "means the list moved)" % (max_price, listed.get("checked", "never")), flush=True)
    ids = set(a.ids.split(",")) if a.ids else None
    todo = [r for r in manifest["puzzles"] if r["split"] == a.split and r["status"] == "active"
            and (ids is None or r["id"] in ids)]
    build = pb.Build(a.build, a.forge, allow_stale=a.allow_stale_build)
    prov = build.provenance()
    recorded = records(build)
    if not recorded and not a.allow_no_record:
        raise SystemExit("build %s has no %s: it would run without the full record (the http and engine transcripts) "
                         "the owner asked for. Build this overlay, or pass --allow-no-record"
                         % (getattr(build, "dir", a.build), TRANSCRIPT_CLASS))
    if a.resume:
        bad = resume_conflicts(a.out, [r["id"] for r in todo], a.model, manifest["suite_sha256"], condition(a), prov,
                               max_price, quantizations, cap_ms, call_ms, a.max_tokens)
        if bad:
            raise SystemExit("--resume into a directory made under another condition (%d puzzles): %s"
                             % (len(bad), "; ".join(bad[:5])))
    out_root = Path(a.out)
    out_root.mkdir(parents=True, exist_ok=True)
    print("[puzzles] %d active %s puzzles, k=%d, model %s, budget $%.2f (spent $%.2f; $%.2f at the dearest "
          "endpoint, which the budget is checked at)"
          % (len(todo), a.split, a.k, a.model, a.budget_usd, spent(out_root), spent(out_root, bound=True)), flush=True)

    ids = [r["id"] for r in todo]
    suite_copy = out_root / SUITE_COPY
    if not (suite_copy.exists() and os.path.samefile(a.suite, str(suite_copy))):
        shutil.copyfile(a.suite, str(suite_copy))
    run_file = functools.partial(write_run, out_root, ids, a.k, a.budget_usd, build=prov, suite_file=SUITE_COPY)
    run_file(finished=False)

    def work(rec):
        out = out_root / rec["id"]
        state = prepare(out, a.k, manifest["suite_sha256"], a.resume, cap_ms,
                        can_run=lambda: not over_budget(out_root, a.budget_usd))
        if state == "done":
            return rec["id"], "done", pb.read_results(out)
        if state == "skip":
            return rec["id"], None, []
        return solve_puzzle(build, rec, a.model, a.k, out_root, manifest["suite_sha256"], condition(a), recorded,
                            max_price, quantizations, cap_ms, call_ms, a.max_tokens)

    skipped = 0
    try:
        with ThreadPoolExecutor(max(1, a.jobs)) as ex:
            for pid, rc, rows in ex.map(work, todo):
                if rc is None:
                    skipped += 1
                    print("[budget] not started: %s ($%.2f at the dearest endpoint)"
                          % (pid, spent(out_root, bound=True)), flush=True)
                    continue
                # A row with a decision lost to a routing filter is no result (puzzle_score refuses it): say so, go on.
                refused = [r for r in rows if r.get("routing_refusals")]
                solved = sum(1 for r in rows if not r.get("routing_refusals") and ps.attempt_success(r)[0])
                print("%-28s rc=%s attempts=%d solved=%d  [cost] $%.3f%s"
                      % (pid, rc, len(rows), solved, spent(out_root),
                         "  [ROUTING-FILTER] %d attempt(s) lost decisions to the routing filter: replay with --resume"
                         % len(refused) if refused else ""), flush=True)
    except BaseException as e:  # an interrupt too: a watcher must not wait --stall minutes to learn the run ended
        run_file(finished=False, stopped=repr(e))
        raise
    run_file(finished=True)
    print("[cost] $%.3f over %d puzzles (%d not started for budget)" % (spent(out_root), len(todo) - skipped, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
