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
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_batch import (  # noqa: E402  provenance helpers, reused verbatim
    REPO_ROOT, SPIKE_REL, _engine_sha, _overlay_sha, _deck_sha, _spike_drift,
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
        plan = txt.with_suffix(".plan")
        blurb = plan.read_text(encoding="utf-8").strip() if plan.is_file() else ""
        # first clause of the blurb keeps the draft menu compact
        short = re.split(r"[.:]", blurb, 1)[0].strip() if blurb else txt.stem
        pool.append({"key": txt.stem, "path": str(txt), "blurb": short})
    return pool


# ------------------------------------------------------------- openrouter

def openrouter_chat(model, system, user, timeout=90, retries=2):
    """One OpenRouter chat completion. Proper JSON parsing (not the Java bridge's
    naive scan). Returns assistant text, or None on failure after retries."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        die("OPENROUTER_API_KEY is required for the draft/games.")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 512,
    }).encode("utf-8")
    for attempt in range(retries + 1):
        req = urllib.request.Request(OPENROUTER_URL, data=body, method="POST")
        req.add_header("Authorization", "Bearer " + key)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except (urllib.error.URLError, KeyError, ValueError, IndexError) as e:
            log("[draft] %s call failed (attempt %d/%d): %s"
                % (model, attempt + 1, retries + 1, e))
    return None


# ---------------------------------------------------------------- drafting

def draft(models, pool, rng, mock=False):
    """Snake-order draft: each model picks a UNIQUE deck from the remaining pool.
    Returns (assignments: list of {model,label,deck,path,blurb}, transcript)."""
    remaining = {d["key"]: d for d in pool}
    order = list(range(len(models)))          # seed order = listed order
    # snake so a late seed sometimes picks earlier across (hypothetical) rounds;
    # with one pick each it's a single pass, but keep the structure explicit.
    picks, transcript = [], []
    for rnd, idx in enumerate(_snake(order, rounds=1)):
        model = models[idx]
        opts = list(remaining.values())
        if mock:
            choice = rng.choice(opts)
        else:
            choice = _ask_pick(model, opts, rng)
        del remaining[choice["key"]]
        picks.append({"seed": idx + 1, "model": model, "label": _label(model),
                      "deck": choice["key"], "path": choice["path"],
                      "blurb": choice["blurb"]})
        transcript.append("seed %d %s -> %s" % (idx + 1, _label(model), choice["key"]))
        log("[draft] " + transcript[-1])
    # restore listed/seed order
    picks.sort(key=lambda p: p["seed"])
    return picks, transcript


def _snake(order, rounds):
    seq = []
    for r in range(rounds):
        seq.extend(order if r % 2 == 0 else list(reversed(order)))
    return seq


def _ask_pick(model, opts, rng):
    menu = "\n".join("%d. %s — %s" % (i + 1, o["key"], o["blurb"])
                     for i, o in enumerate(opts))
    system = ("You are a competitive Magic: The Gathering (Legacy) player choosing "
              "which deck to bring to a tournament. Pick the deck you would play to "
              "win. Reply with ONLY the number of your choice on the final line, "
              "e.g. `PICK: 3`.")
    user = ("Available decks (each already unavailable to later drafters once "
            "taken):\n%s\n\nWhich deck do you choose? Reply `PICK: <number>`." % menu)
    reply = openrouter_chat(model, system, user)
    if reply:
        nums = re.findall(r"\b(\d+)\b", reply)
        if nums:
            n = int(nums[-1])
            if 1 <= n <= len(opts):
                return opts[n - 1]
    # unparseable / failed -> deterministic fallback so the draft never stalls
    log("[draft] %s gave no usable pick; assigning random remaining deck" % model)
    return rng.choice(opts)


def _label(model):
    return model.split("/")[-1].split(":")[-1]


# ----------------------------------------------------------------- bracket

def seed_bracket(picks):
    """Standard 8-seed single-elim QF pairings (1v8,4v5,2v7,3v6) so seeds 1 and 2
    can only meet in the final. picks are seed-ordered (index 0 = seed 1)."""
    if len(picks) != 8:
        die("bracket needs exactly 8 models (got %d)." % len(picks))
    s = picks
    return [(s[0], s[7]), (s[3], s[4]), (s[1], s[6]), (s[2], s[5])]


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
    installed = args.forge_home / SPIKE_REL
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
        "OVERLAY_SHA": _overlay_sha(installed),
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
    backstop = args.cap_minutes * 60 * 2 + 300
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            subprocess.run(["bash", "run-llm-spike.sh"], cwd=str(args.forge_home),
                           env=env, stdout=lf, stderr=subprocess.STDOUT,
                           timeout=backstop)
    except subprocess.TimeoutExpired:
        log("[match] backstop timeout — killing any stray JVM")
        subprocess.run(["pkill", "-INT", "-f", "LlmSpikeGui"], check=False)
        return "timeout"
    return _read_winner(jdir, seed, swap)


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

def run_bracket(picks, out_dir, args, rng):
    state_path = out_dir / "bracket.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"rounds": {}}

    def play(pairs, round_name):
        results = state["rounds"].get(round_name, {})
        winners = []
        for i, (a, b) in enumerate(pairs):
            mid = "%s-%d" % (round_name, i + 1)
            if mid in results and not args.dry_run:
                w = results[mid]["winner_label"]
                log("[resume] %s already decided -> %s" % (mid, w))
                winners.append(_by_label(pairs, w))
                continue
            w, l, info = run_match(a, b, mid, out_dir, args, rng)
            results[mid] = {"a": a["label"], "b": b["label"],
                            "a_deck": a["deck"], "b_deck": b["deck"],
                            "winner_label": w["label"], **info}
            winners.append(w)
            state["rounds"][round_name] = results
            if not args.dry_run:
                state_path.write_text(json.dumps(state, indent=2))
        return winners

    qf = seed_bracket(picks)
    sf_w = play(qf, "QF")
    sf = [(sf_w[0], sf_w[1]), (sf_w[2], sf_w[3])]
    f_w = play(sf, "SF")
    final = [(f_w[0], f_w[1])]
    champ = play(final, "F")[0]
    state["champion"] = champ["label"]
    state["champion_deck"] = champ["deck"]
    if not args.dry_run:
        state_path.write_text(json.dumps(state, indent=2))
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
    lines.append("\n## Results\n")
    for rnd in ("QF", "SF", "F"):
        r = state["rounds"].get(rnd, {})
        if not r:
            continue
        lines.append("### %s" % {"QF": "Quarterfinals", "SF": "Semifinals",
                                 "F": "Final"}[rnd])
        for mid in sorted(r):
            m = r[mid]
            lines.append("- **%s** (%s) def. %s (%s)%s" % (
                m["winner_label"],
                m["a_deck"] if m["winner_label"] == m["a"] else m["b_deck"],
                m["b"] if m["winner_label"] == m["a"] else m["a"],
                m["b_deck"] if m["winner_label"] == m["a"] else m["a_deck"],
                "" if "attempt" not in m else " · seed %s" % m.get("seed", "?")))
    lines.append("\n## Champion\n")
    lines.append("🏆 **%s** with %s" % (state.get("champion", "?"),
                                        state.get("champion_deck", "?")))
    (out_dir / "REPORT.md").write_text("\n".join(lines) + "\n")
    log("[report] wrote %s" % (out_dir / "REPORT.md"))


def main():
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
    ap.add_argument("--forge-home", default=os.path.expanduser("~/code/forge"))
    ap.add_argument("--record", dest="record", action="store_true", default=True)
    ap.add_argument("--no-record", dest="record", action="store_false")
    ap.add_argument("--record-fps", type=int, default=8)
    ap.add_argument("--draft-only", action="store_true", help="draft, then stop")
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

    # spike-drift guard: the installed JVM code must match this checkout (only when
    # games will actually launch — a full --dry-run doesn't need a built overlay).
    drift = _spike_drift(args.forge_home / SPIKE_REL, REPO_ROOT / "forge-overlay" / SPIKE_REL)
    if drift and launches_games:
        die("installed spike file(s) differ from this checkout: %s — run "
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

    # --dry-run implies an offline draft too (no OpenRouter calls), same as --mock-draft.
    picks, transcript = draft(args.models, pool, rng, mock=args.mock_draft or args.dry_run)
    (out_dir / "draft.json").write_text(json.dumps(picks, indent=2))
    log("[draft] complete: " + " | ".join(transcript))
    if args.draft_only:
        return 0

    state, champ = run_bracket(picks, out_dir, args, rng)
    write_report(state, picks, out_dir, args)
    log("\n=== CHAMPION: %s (%s) ===" % (champ["label"], champ["deck"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
