#!/usr/bin/env python3
"""LLM tournament driver for the MTG Forge eval.

Runs an 8-model single-elimination bracket of best-of-1 Legacy games. Each model
first DRAFTS its own deck from the pool in eval/decks/ (snake order, unique decks),
then plays the bracket; every match runs in the GUI runner with in-JVM recording so
each game is captured to its own mp4.

Transport: every model is an OpenRouter spec (openrouter:<id>). Because each game
ships the full board/hand snapshot to openrouter.ai, this driver refuses to run real
games unless OPENROUTER_ALLOW_EGRESS=1 is set explicitly (mirrors the Java factory's
egress gate) and OPENROUTER_API_KEY is present. Draft calls hit OpenRouter too and
obey the same gate. Only a full --dry-run is offline and skips the gate; --mock-draft
alone skips just the DRAFT calls (the games are still real, so the gate still applies).

The winner line each match emits is IDENTITY-keyed (seat A = the first model in the
pairing, regardless of who is on the play), so seat randomization never corrupts
who-advanced. State is checkpointed to <out>/bracket.json and the run is resumable:
a match whose result is already recorded is skipped.

Examples:
  # cheap end-to-end smoke test (ONE recorded game, cheap model both seats):
  OPENROUTER_ALLOW_EGRESS=1 python3 eval/tournament.py --smoke qwen/qwen3-8b

  # the real thing (user supplies the 8 model ids; needs OPENROUTER_API_KEY):
  OPENROUTER_ALLOW_EGRESS=1 python3 eval/tournament.py --out runs/tournament-1 \
      --models anthropic/claude-opus-4.8 anthropic/claude-fable-5 openai/gpt-5 \
               x-ai/grok-4.5 google/gemini-3.5-flash qwen/qwen3.7-max \
               moonshotai/kimi-k3 deepseek/deepseek-v4-pro

  # validate bracket + draft plumbing with zero cost:
  python3 eval/tournament.py --models a b c d e f g h --mock-draft --dry-run
"""

import argparse
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import deck_legality  # noqa: E402
import puzzle_format  # noqa: E402  forge_checkout: the Forge checkout, $FORGE_HOME as given
from run_batch import (  # noqa: E402  provenance helpers, reused verbatim
    PINNED_ENV_KEYS, REPO_ROOT, _engine_sha, _overlay_sha, _spike_drift,
)

DECKS_DIR = REPO_ROOT / "eval" / "decks"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def load_secrets_file():
    """Load KEY=VALUE lines from <repo>/secrets.env into the environment (git-ignored;
    never overrides a value already in the env, so inline `VAR=... python3 ...` wins).
    This is where the user drops their OPENROUTER_API_KEY. Consent (OPENROUTER_ALLOW_EGRESS)
    is a deliberate uncomment in that file — the driver still refuses without it."""
    path = REPO_ROOT / "secrets.env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:   # non-empty, don't clobber inline env
            os.environ[k] = v


def log(msg):
    print(msg, flush=True)


def die(msg, code=2):
    print("tournament: " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


# --------------------------------------------------------------- deck pool

def load_pool():
    """Every eval/decks/*.txt with its one-line .plan blurb (blurb '' if absent)."""
    pool = []
    for txt in sorted(DECKS_DIR.glob("*.txt")):
        if deck_legality.is_format_deck(txt.stem):
            continue   # the top-8 format lists (eval/decks/SOURCES.md); the tournament is the Legacy eval pool
        plan = txt.with_suffix(".plan")
        blurb = plan.read_text(encoding="utf-8").strip() if plan.is_file() else ""
        # first clause of the blurb keeps the draft menu compact
        short = re.split(r"[.:]", blurb, maxsplit=1)[0].strip() if blurb else txt.stem
        pool.append({"key": txt.stem, "path": str(txt), "blurb": short})
    return pool


# ------------------------------------------------------------- openrouter

def _atomic_write(path, text):
    """Write via a temp file + os.replace so an interrupt can never leave a half-written
    (corrupt) bracket.json/draft.json — the whole bracket's recorded results depend on it."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path, rec):
    if path is None:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        log("[log] failed to append %s: %s" % (path, e))


def openrouter_chat(model, system, user, timeout=180, retries=2, log_path=None, tag=None):
    """One OpenRouter chat completion. Proper JSON parsing (not the Java bridge's naive
    scan). Returns assistant text, or None on failure after retries. When log_path is
    given, appends one JSONL record per attempt capturing the full request AND the full
    raw response (content + any separate `reasoning` field + usage) so nothing the model
    said is lost."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        die("OPENROUTER_API_KEY is required for the draft/games.")
    body_obj = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 2048,
    }
    body = json.dumps(body_obj).encode("utf-8")
    for attempt in range(retries + 1):
        rec = {"tag": tag, "model": model, "attempt": attempt + 1, "request": body_obj}
        req = urllib.request.Request(OPENROUTER_URL, data=body, method="POST")
        req.add_header("Authorization", "Bearer " + key)
        req.add_header("Content-Type", "application/json")
        raw = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                status = resp.status
            data = json.loads(raw)
            msg = data["choices"][0]["message"]
            content = msg["content"]
            rec.update({"status": status, "response": data, "content": content,
                        "reasoning": msg.get("reasoning"), "usage": data.get("usage")})
            _append_jsonl(log_path, rec)
            return content, msg.get("reasoning")
        except (urllib.error.URLError, KeyError, ValueError, IndexError) as e:
            rec.update({"error": repr(e), "raw": raw})
            _append_jsonl(log_path, rec)
            log("[draft] %s call failed (attempt %d/%d): %s"
                % (model, attempt + 1, retries + 1, e))
    return None, None


# ---------------------------------------------------------------- drafting

def draft(models, pool, rng, mock=False, log_dir=None):
    """Snake-order draft: each model picks a UNIQUE deck from the remaining pool.
    Returns (assignments: list of {model,label,deck,path,blurb,reasoning}, transcript).
    When log_dir is given, every model's full request/response goes to raw.jsonl and a
    readable <seed>-<label>.md per model."""
    remaining = {d["key"]: d for d in pool}
    order = list(range(len(models)))          # seed order = listed order
    # snake so a late seed sometimes picks earlier across (hypothetical) rounds;
    # with one pick each it's a single pass, but keep the structure explicit.
    raw_log = (log_dir / "raw.jsonl") if log_dir else None
    picks, transcript = [], []
    for rnd, idx in enumerate(_snake(order, rounds=1)):
        model = models[idx]
        opts = list(remaining.values())
        if mock:
            choice, reasoning = rng.choice(opts), "(mock draft — no model call)"
        else:
            choice, reasoning = _ask_pick(model, opts, rng, idx + 1, raw_log)
            if log_dir:
                _write_pick_md(log_dir, idx + 1, model, opts, choice, reasoning)
        del remaining[choice["key"]]
        picks.append({"seed": idx + 1, "model": model, "label": _label(model),
                      "deck": choice["key"], "path": choice["path"],
                      "blurb": choice["blurb"], "reasoning": reasoning})
        transcript.append("seed %d %s -> %s" % (idx + 1, _label(model), choice["key"]))
        log("[draft] " + transcript[-1])
    # restore listed/seed order
    picks.sort(key=lambda p: p["seed"])
    return picks, transcript


def _write_pick_md(log_dir, seed, model, opts, choice, reasoning):
    menu = "\n".join("%d. %s — %s" % (i + 1, o["key"], o["blurb"]) for i, o in enumerate(opts))
    md = ("# Draft pick — seed %d: `%s`\n\n**Chose:** %s\n\n## Menu shown\n%s\n\n"
          "## Model's full response / reasoning\n\n%s\n"
          % (seed, model, choice["key"], menu, reasoning or "(no reasoning returned)"))
    try:
        (log_dir / ("%d-%s.md" % (seed, _label(model)))).write_text(md, encoding="utf-8")
    except OSError as e:
        log("[log] failed to write pick md: %s" % e)


def _snake(order, rounds):
    seq = []
    for r in range(rounds):
        seq.extend(order if r % 2 == 0 else list(reversed(order)))
    return seq


# Archetype nicknames -> deck key, so a pick stated by name (not menu number) still
# resolves. Matching is on normalized substrings; the LAST-occurring alias in the text
# wins (models typically name their final choice last).
_DECK_ALIASES = {
    "ur-delver": ["ur-delver", "izzet delver", "izzet tempo", "delver"],
    "grixis-reanimator": ["grixis-reanimator", "grixis reanimator", "reanimator", "reanimate"],
    "mardu-energy": ["mardu-energy", "mardu energy", "mardu", "energy"],
    "death-and-taxes": ["death-and-taxes", "death and taxes", "death & taxes", "d&t", "taxes"],
    "sneak-and-show": ["sneak-and-show", "sneak and show", "sneak & show", "sneak show", "sneak attack"],
    "lands": ["lands", "aggro loam", "loam"],
    "elves": ["elves", "elfball"],
    "ant-storm": ["ant-storm", "ad nauseam tendrils", "tendrils", " ant ", "storm"],
    "miracles": ["miracles", "miracle", "counterbalance"],
    "goblins": ["goblins", "goblin"],
    "burn": ["burn", "rdw"],
    "infect": ["infect"],
}


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower().replace("&", "and"))


def _parse_number_pick(text, opts):
    """Explicit menu-number pick only (`PICK: N`, then 'option/deck/number N').
    This is the authoritative signal — the number indexes the menu as shown."""
    if not text:
        return None
    # Prefer the prompted format: `PICK: N` alone on a line. Only if that's absent fall
    # back to a looser scan (last "pick/option/deck/number N" anywhere), which can be
    # tripped by unrelated numbered prose.
    anchored = re.findall(r"(?im)^\s*pick[:\s#]*(\d+)\s*$", text)
    for cand in (anchored,
                 re.findall(r"pick[:\s#]*(\d+)", text, re.I),
                 re.findall(r"(?:option|deck|number|choice)\s*#?\s*(\d+)", text, re.I)):
        if cand:
            n = int(cand[-1])
            if 1 <= n <= len(opts):
                return opts[n - 1]
    return None


def _alias_pick(text, opts):
    """Last-resort: the last-mentioned deck name/alias in the text. Used only when no
    explicit number was given anywhere; run on content+reasoning combined so the
    concluding choice (usually stated last) beats decks merely discussed earlier."""
    nt = " " + _norm(text) + " "
    best, best_pos = None, -1
    for o in opts:
        for alias in [o["key"]] + _DECK_ALIASES.get(o["key"], []):
            pos = nt.rfind(_norm(alias))
            if pos > best_pos:
                best, best_pos = o, pos
    return best


def _resolve_pick(content, reasoning, opts):
    """Explicit number in content wins; then explicit number in reasoning; then, only if
    neither gave a number, alias-match the combined text (concluding choice last)."""
    return (_parse_number_pick(content, opts)
            or _parse_number_pick(reasoning, opts)
            or _alias_pick((content or "") + "\n" + (reasoning or ""), opts))


def _ask_pick(model, opts, rng, seed, raw_log):
    """Ask one model to pick a deck. Returns (choice_dict, reasoning_text)."""
    menu = "\n".join("%d. %s — %s" % (i + 1, o["key"], o["blurb"])
                     for i, o in enumerate(opts))
    system = ("You are a competitive Magic: The Gathering (Legacy) player about to enter "
              "an 8-player single-elimination tournament against seven other AI models. "
              "You must choose ONE deck to pilot for the whole event. Think it through: "
              "weigh the archetypes' power, the likely metagame, and how each deck suits "
              "aggressive vs. controlling play. Explain your reasoning in a few sentences, "
              "then end your reply with your choice on its own final line as `PICK: <number>`.")
    user = ("Available decks (each becomes unavailable once an earlier drafter takes it):\n"
            "%s\n\nWhich deck do you choose, and why? End with `PICK: <number>`." % menu)
    content, reasoning = openrouter_chat(model, system, user, log_path=raw_log,
                                         tag="draft-seed-%d-%s" % (seed, _label(model)))
    # explicit number wins (content, then reasoning); alias-match combined text only if
    # neither gave a number — recovers content-filtered models that state the choice only
    # in their reasoning field before truncation.
    choice = _resolve_pick(content, reasoning, opts)
    saved = (content or "").strip()
    if reasoning:
        saved += ("\n\n---\n[reasoning field]\n" + reasoning.strip())
    if choice:
        return choice, (saved or "(empty response)")
    # nothing parseable anywhere -> deterministic fallback so the draft never stalls
    log("[draft] %s gave no usable pick; assigning random remaining deck" % model)
    return rng.choice(opts), (saved or "(no response from model — random fallback)")


def _label(model):
    return model.split("/")[-1].split(":")[-1]


# ----------------------------------------------------------------- bracket

def draw_bracket(picks, rng):
    """Random draw: shuffle the 8 players into bracket slots 1-8. QF pairs are adjacent
    slots (1v2, 3v4, 5v6, 7v8); winners flow QF1/QF2 -> SF1, QF3/QF4 -> SF2, SF1/SF2 -> F.
    Returns the 8 picks in slot order (the drawn bracket)."""
    if len(picks) != 8:
        die("bracket needs exactly 8 models (got %d)." % len(picks))
    slots = list(picks)
    rng.shuffle(slots)
    return slots


def qf_pairs_from_draw(draw):
    return [(draw[0], draw[1]), (draw[2], draw[3]), (draw[4], draw[5]), (draw[6], draw[7])]


def _print_bracket(draw):
    qf = qf_pairs_from_draw(draw)
    log("\n=== BRACKET DRAW ===")
    for i, (a, b) in enumerate(qf):
        log("  QF-%d:  %-16s (%s)  vs  %-16s (%s)"
            % (i + 1, a["label"], a["deck"], b["label"], b["deck"]))
    log("  SF-1:  winner QF-1  vs  winner QF-2")
    log("  SF-2:  winner QF-3  vs  winner QF-4")
    log("  F-1 :  winner SF-1  vs  winner SF-2")
    log("====================\n")


# ------------------------------------------------------------- match runner

def run_match(a, b, match_id, out_dir, args, rng):
    """Play one recorded bo1 game between models a and b (seat A = a). Returns
    (winner_pick, loser_pick, info dict). Replays on draw/invalid up to args.retries."""
    mp4 = out_dir / ("%s__%s_vs_%s.mp4" % (match_id, a["label"], b["label"]))
    jdir = out_dir / ("match-" + match_id)
    jdir.mkdir(parents=True, exist_ok=True)
    for attempt in range(args.retries + 1):
        seed = args.seed + match_id_hash(match_id) + attempt * 101
        swap = rng.random() < 0.5          # randomize who is on the play
        log("[match %s] %s(A) vs %s(B) seed=%d swap=%d attempt=%d"
            % (match_id, a["label"], b["label"], seed, swap, attempt + 1))
        if args.dry_run:
            # inert: build no env (avoids the git subprocess in _match_env), launch nothing
            log("       DRY-RUN: bash run-llm-spike.sh  (DECK_A=%s DECK_B=%s "
                "SMART_MODEL_A=openrouter:%s SMART_MODEL_B=openrouter:%s RECORD_OUT=%s)"
                % (a["deck"], b["deck"], a["model"], b["model"], mp4.name))
            return a, b, {"winner": "A", "seed": seed, "swap": swap, "dry_run": True,
                          "mp4": str(mp4)}
        env = _match_env(a, b, seed, swap, mp4, jdir, args)
        winner = _launch_and_read(env, jdir, seed, swap, args)
        if winner in ("A", "B"):
            w, l = (a, b) if winner == "A" else (b, a)
            log("[match %s] winner: %s" % (match_id, w["label"]))
            return w, l, {"winner": winner, "seed": seed, "swap": swap,
                          "mp4": str(mp4), "attempt": attempt + 1}
        log("[match %s] no decisive result (%s); replaying" % (match_id, winner))
    # exhausted retries: pick deterministically so the bracket still completes
    log("[match %s] no decisive game after %d attempts; coin-flipping (seeded)"
        % (match_id, args.retries + 1))
    w, l = (a, b) if rng.random() < 0.5 else (b, a)
    return w, l, {"winner": "coinflip:" + w["label"], "mp4": str(mp4)}


def _match_env(a, b, seed, swap, mp4, jdir, args):
    env = os.environ.copy()
    # Every condition run_batch.py pins, and the shadow labeller: a shell value never reaches a bracket game
    # unrecorded (pr-review of #347); the games run at the JVM's defaults, as the tournament always meant.
    for name in PINNED_ENV_KEYS:
        env.pop(name, None)
    env.update({
        "GAME_SEED": str(seed),
        "CONTROLLER_A": "llm", "CONTROLLER_B": "llm",
        "DECK_A": a["path"], "DECK_B": b["path"],
        "DECK_A_NAME": a["label"], "DECK_B_NAME": b["label"],
        "SMART_MODEL_A": "openrouter:" + a["model"], "FAST_MODEL_A": "openrouter:" + a["model"],
        "SMART_MODEL_B": "openrouter:" + b["model"], "FAST_MODEL_B": "openrouter:" + b["model"],
        "MATCH_GAMES": "1", "AUTO_EXIT": "1",
        "EVAL_OUT": str(jdir),
        "GAME_CAP_MS": str(int(args.cap_minutes * 60_000)),
        "ENGINE_SHA": _engine_sha(args.forge_home),
        "OVERLAY_SHA": _overlay_sha(args.forge_home),
        "DECK_SHA": "tournament-mixed",
        "RUN_DISCIPLINE": "tournament",
    })
    if swap:
        env["SWAP_SEATS"] = "1"
    else:
        env.pop("SWAP_SEATS", None)
    if args.record:
        env["RECORD"] = "1"
        env["RECORD_OUT"] = str(mp4)
        env["RECORD_FPS"] = str(args.record_fps)
    else:
        env.pop("RECORD", None)
    # egress consent must come from the PARENT env (user), never injected here.
    return env


def _launch_and_read(env, jdir, seed, swap, args):
    log_path = jdir / ("game-%d-%d.log" % (seed, 1 if swap else 0))
    # Clear any stale game-summary JSONL for THIS seed from an abandoned prior attempt
    # (crash before bracket.json was persisted): the JVM writes a fresh unique file, so a
    # leftover could otherwise be returned by _read_winner and misattribute the winner.
    for stale in jdir.glob("game-%d-*.jsonl" % seed):
        try:
            stale.unlink()
        except OSError:
            pass
    backstop = args.cap_minutes * 60 * 2 + 300
    with open(log_path, "w", encoding="utf-8") as lf:
        # Its own process group, so a backstop stops this match's launcher and JVM, never another game on the machine.
        proc = subprocess.Popen(["bash", "run-llm-spike.sh"], cwd=str(args.forge_home), env=env, stdout=lf,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            proc.wait(timeout=backstop)
        except subprocess.TimeoutExpired:
            log("[match] backstop timeout — stopping this match's JVM")
            return "timeout"
        finally:
            # A new session takes the match out of the terminal's process group, so a Ctrl-C (or any exception)
            # reaches only this process: the launcher and its JVM, which may be calling a paid model, are stopped here.
            if proc.poll() is None:
                stop_group(proc)
    return _read_winner(jdir, seed, swap)


def exit_on_hangup():
    """SIGTERM and SIGHUP (a kill, a closed terminal) raise SystemExit, so _launch_and_read's finally stops the match's
    own process group: its new session takes it out of the terminal's group, and a default handler would end this
    process without unwinding, leaving the launcher and JVM playing."""
    def _exit(signum, frame):
        # A second signal during the stop's grace must not cut short its SIGKILL of what ignored the SIGINT.
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _exit)


def stop_group(proc, grace_s=30):
    """SIGINT to the process group of `proc` (the launcher and the JVM it started); whatever of the group is still
    alive after grace_s gets SIGKILL. Returns when the launcher has exited."""
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except ProcessLookupError:
        proc.wait()
        return
    deadline = time.time() + grace_s
    while time.time() < deadline:
        proc.poll()                              # reap the launcher, so a finished group reads as gone
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()


def _read_winner(jdir, seed, swap):
    for path in Path(jdir).glob("game-%d-*.jsonl" % seed):
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("t") == "game" \
                        and rec.get("seed") == seed and bool(rec.get("swap", False)) == swap:
                    return rec.get("winner", "invalid")
        except OSError:
            continue
    return "no-line"


def match_id_hash(mid):
    return sum(ord(c) for c in mid) % 997


# -------------------------------------------------------------------- main

def _ensure_draw(picks, state, state_path, rng, args):
    """Load the persisted random draw, or make (and save) one. Returns picks in slot
    order. --redraw forces a new draw."""
    by_label = {p["label"]: p for p in picks}
    if state.get("draw") and not args.redraw:
        try:
            return [by_label[lbl] for lbl in state["draw"]]
        except KeyError:
            log("[draw] saved draw doesn't match --models; re-drawing")
    draw = draw_bracket(picks, rng)
    state["draw"] = [p["label"] for p in draw]
    if not args.dry_run:
        _atomic_write(state_path, json.dumps(state, indent=2))
    log("[draw] new random bracket draw saved")
    return draw


def run_bracket(picks, out_dir, args, rng):
    """Play the bracket from the persisted random draw. Plays at most args.max_matches
    NEW matches this invocation (0 = all remaining), so callers can go one game at a
    time. Returns (state, champion-or-None). champion is None until the Final is done."""
    state_path = out_dir / "bracket.json"
    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text())
        except (ValueError, OSError) as e:
            die("bracket.json is unreadable (%s). It holds every recorded match result; "
                "fix or remove it before continuing rather than risk overwriting results." % e)
    state.setdefault("rounds", {})
    draw = _ensure_draw(picks, state, state_path, rng, args)

    if args.draw_only:
        _print_bracket(draw)
        return state, None

    if args.max_matches < 0:
        die("--max-matches must be >= 0 (0 = all remaining).")
    budget = [args.max_matches if args.max_matches > 0 else 10 ** 9]

    def play_round(name, pairs):
        """Play this round's not-yet-decided matches, up to the remaining budget.
        Returns True iff every match in the round is now recorded."""
        results = state["rounds"].setdefault(name, {})
        for i, (a, b) in enumerate(pairs):
            mid = "%s-%d" % (name, i + 1)
            if mid in results:
                continue
            if budget[0] <= 0:
                continue
            w, l, info = run_match(a, b, mid, out_dir, args, rng)
            results[mid] = {"a": a["label"], "b": b["label"],
                            "a_deck": a["deck"], "b_deck": b["deck"],
                            "winner_label": w["label"], **info}
            budget[0] -= 1
            if not args.dry_run:
                _atomic_write(state_path, json.dumps(state, indent=2))
        return all(("%s-%d" % (name, i + 1)) in results for i in range(len(pairs)))

    def winners(name, pairs):
        r = state["rounds"][name]
        return [_by_label(pairs, r["%s-%d" % (name, i + 1)]["winner_label"])
                for i in range(len(pairs))]

    qf = qf_pairs_from_draw(draw)
    if not play_round("QF", qf):
        return state, None
    qf_w = winners("QF", qf)
    sf = [(qf_w[0], qf_w[1]), (qf_w[2], qf_w[3])]
    if not play_round("SF", sf):
        return state, None
    sf_w = winners("SF", sf)
    final = [(sf_w[0], sf_w[1])]
    if not play_round("F", final):
        return state, None
    champ = winners("F", final)[0]
    state["champion"] = champ["label"]
    state["champion_deck"] = champ["deck"]
    if not args.dry_run:
        _atomic_write(state_path, json.dumps(state, indent=2))
    return state, champ


def _by_label(pairs, label):
    for a, b in pairs:
        if a["label"] == label:
            return a
        if b["label"] == label:
            return b
    return None


def write_report(state, picks, out_dir, args):
    lines = ["# LLM Legacy Tournament\n",
             "Single-elimination, best-of-1, %s. Each model drafted its own deck "
             "from a %d-deck pool.\n" % (
                 "recorded" if args.record else "not recorded", len(load_pool())),
             "## Draft\n", "| seed | model | deck |", "|---|---|---|"]
    for p in picks:
        lines.append("| %d | `%s` | %s |" % (p["seed"], p["model"], p["deck"]))
    if state.get("draw"):
        by_label = {p["label"]: p for p in picks}
        slots = [by_label[l] for l in state["draw"] if l in by_label]
        if len(slots) == 8:
            lines.append("\n## Bracket draw (random)\n")
            for i, (a, b) in enumerate(qf_pairs_from_draw(slots)):
                lines.append("- QF-%d: %s (%s) vs %s (%s)"
                             % (i + 1, a["label"], a["deck"], b["label"], b["deck"]))
    lines.append("\n## Results\n")
    total = 0
    for rnd in ("QF", "SF", "F"):
        r = state.get("rounds", {}).get(rnd, {})
        if not r:
            continue
        lines.append("### %s" % {"QF": "Quarterfinals", "SF": "Semifinals",
                                 "F": "Final"}[rnd])
        for mid in sorted(r):
            m = r[mid]
            total += 1
            lines.append("- **%s** (%s) def. %s (%s)" % (
                m["winner_label"],
                m["a_deck"] if m["winner_label"] == m["a"] else m["b_deck"],
                m["b"] if m["winner_label"] == m["a"] else m["a"],
                m["b_deck"] if m["winner_label"] == m["a"] else m["a_deck"]))
    lines.append("\n## Champion\n")
    if state.get("champion"):
        lines.append("🏆 **%s** with %s" % (state["champion"], state.get("champion_deck", "?")))
    else:
        lines.append("_Tournament in progress — %d/7 matches played._" % total)
    (out_dir / "REPORT.md").write_text("\n".join(lines) + "\n")
    log("[report] wrote %s" % (out_dir / "REPORT.md"))


def main():
    exit_on_hangup()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+",
                    help="8 OpenRouter model ids (bare, no 'openrouter:' prefix)")
    ap.add_argument("--out", help="output dir (default runs/tournament-<ts>)")
    ap.add_argument("--seed", type=int, default=100, help="base RNG/shuffle seed")
    ap.add_argument("--cap-minutes", type=float, default=60.0,
                    help="per-game wall-clock cap (default 60)")
    ap.add_argument("--retries", type=int, default=2,
                    help="replays on a drawn/invalid game before a seeded coin-flip")
    ap.add_argument("--forge-home", default=puzzle_format.forge_checkout(),
                    help="the Forge checkout holding run-llm-spike.sh (default: $FORGE_HOME as given if set, else "
                         "~/code/forge or /root/forge; puzzle_format.forge_checkout)")
    ap.add_argument("--record", dest="record", action="store_true", default=True)
    ap.add_argument("--no-record", dest="record", action="store_false")
    ap.add_argument("--record-fps", type=int, default=8)
    ap.add_argument("--draft-only", action="store_true", help="draft, then stop")
    ap.add_argument("--redraft", action="store_true",
                    help="force a fresh draft even if --out already has a draft.json")
    ap.add_argument("--draw-only", action="store_true",
                    help="draft + make the random bracket draw, print it, then stop")
    ap.add_argument("--redraw", action="store_true",
                    help="force a fresh random draw even if bracket.json already has one")
    ap.add_argument("--max-matches", type=int, default=0,
                    help="play at most N NEW matches this invocation (0 = all remaining). "
                         "Use 1 to go one game at a time.")
    ap.add_argument("--mock-draft", action="store_true",
                    help="assign decks with the seeded RNG (no OpenRouter calls)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print bracket + would-run commands; launch no games")
    ap.add_argument("--smoke", metavar="MODEL",
                    help="run ONE recorded game of MODEL vs itself (two decks) to "
                         "validate the OpenRouter+GUI+recording path end to end")
    args = ap.parse_args()

    load_secrets_file()   # pull OPENROUTER_* from <repo>/secrets.env if present

    args.forge_home = Path(args.forge_home).expanduser().resolve()
    if not (args.forge_home / "run-llm-spike.sh").is_file():
        die("run-llm-spike.sh not found in --forge-home: %s" % args.forge_home)

    # Egress gate: required whenever ANYTHING will hit openrouter.ai. Games launch
    # unless --dry-run, so the gate is required unless --dry-run — NOTE --mock-draft
    # alone only skips the DRAFT calls, the games are still real, so it does NOT
    # exempt the gate. Only a full --dry-run is truly offline.
    launches_games = not args.dry_run
    if launches_games and os.environ.get("OPENROUTER_ALLOW_EGRESS") != "1":
        die("real games/draft ship the full game state to openrouter.ai. Consent by "
            "uncommenting OPENROUTER_ALLOW_EGRESS=1 in secrets.env (or exporting it) — "
            "this driver will NOT set it for you. Use --dry-run for a fully offline check.")
    if launches_games and not os.environ.get("OPENROUTER_API_KEY"):
        die("OPENROUTER_API_KEY not found. Put your key in secrets.env "
            "(from https://openrouter.ai/keys) or export it, then re-run.")

    rng = random.Random(args.seed)
    pool = load_pool()
    if len(pool) < 8:
        die("need >=8 decks in eval/decks (found %d)." % len(pool))

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out).expanduser().resolve() if args.out \
        else REPO_ROOT / "runs" / ("tournament-" + ts)
    out_dir.mkdir(parents=True, exist_ok=True)
    log("[out] %s" % out_dir)

    # overlay-drift guard: the installed JVM code must match this checkout (only when
    # games will actually launch — a full --dry-run doesn't need a built overlay).
    drift = _spike_drift(args.forge_home)
    if drift and launches_games:
        die("installed overlay file(s) differ from this checkout: %s — run "
            "`bash install.sh` && recompile first." % ", ".join(drift))

    # --- smoke: single recorded game, model vs itself on two random decks ---
    if args.smoke:
        two = rng.sample(pool, 2)
        a = {"model": args.smoke, "label": _label(args.smoke) + "-A",
             "deck": two[0]["key"], "path": two[0]["path"]}
        b = {"model": args.smoke, "label": _label(args.smoke) + "-B",
             "deck": two[1]["key"], "path": two[1]["path"]}
        w, l, info = run_match(a, b, "smoke", out_dir, args, rng)
        log("[smoke] winner=%s info=%s" % (w["label"], info))
        return 0

    if not args.models:
        die("--models is required (or use --smoke).")
    if len(args.models) != 8:
        die("need exactly 8 --models (got %d)." % len(args.models))

    # Draft reuse: if this --out already has a draft.json for the SAME models, reuse it
    # (don't re-call OpenRouter) — so `--draft-only` now and the bracket later use the
    # SAME picks. --redraft forces a fresh draft.
    draft_path = out_dir / "draft.json"
    picks = None
    if draft_path.is_file() and not args.redraft:
        prior = json.loads(draft_path.read_text())
        if [p["model"] for p in prior] == list(args.models):
            picks = prior
            log("[draft] reusing existing draft.json (%s)" % draft_path)
        else:
            log("[draft] draft.json models differ from --models; re-drafting")
    if picks is None:
        # --dry-run implies an offline draft too (no OpenRouter calls), same as --mock-draft.
        draft_logs = out_dir / "draft-logs"
        draft_logs.mkdir(parents=True, exist_ok=True)
        picks, transcript = draft(args.models, pool, rng,
                                  mock=args.mock_draft or args.dry_run, log_dir=draft_logs)
        _atomic_write(draft_path, json.dumps(picks, indent=2))
        log("[draft] complete: " + " | ".join(transcript))
        log("[draft] per-model reasoning saved to %s" % draft_logs)
    if args.draft_only:
        return 0

    state, champ = run_bracket(picks, out_dir, args, rng)
    if args.draw_only:
        return 0
    write_report(state, picks, out_dir, args)
    if champ:
        log("\n=== CHAMPION: %s (%s) ===" % (champ["label"], champ["deck"]))
    else:
        done = sum(len(state.get("rounds", {}).get(r, {})) for r in ("QF", "SF", "F"))
        log("\n=== %d/7 matches played — run again to continue (bracket.json saved) ==="
            % done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
