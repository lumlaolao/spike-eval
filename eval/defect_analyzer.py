#!/usr/bin/env python3
"""defect_analyzer.py — reads a directory (or directories) of (jsonl, log)
game-file pairs from an eval/improve-loop batch and reports harness-defect
metrics: fallback counts, the tier=ai hard invariant, silent decision-drop
reconciliation (log-side ask events vs jsonl rows), no-CHOICE/parse-fail
counts, latency percentiles, finish_reason=length counts, forced-pick
mislabels, per-seat mulligan coverage, and block declarations that stood
illegal (a `[BLOCK-INVALID] ... no legal set` log line; reported, not an
invariant).

Signatures are grounded in runs/tournament-1/HARNESS-ANALYSIS.md sections
2-4 (C1 tier=ai delegation, C5 silent reactive-window drops, C6 forced
single-legal mislabeling, C7 missing mulligan jsonl rows, D1/D3 latency).

Pairing: for each `game-<seed>-<ts>.jsonl` this reads the trailing `t:"game"`
line for `seed`/`swap` and matches it to `game-<seed>-<0|1>.log` (the naming
`run_batch.py`/`run-llm-headless.sh` use: swap=false -> suffix 0, swap=true
-> suffix 1). Crashed games with no game line fall back to the seed parsed
from the filename and claim whichever matching log is still unclaimed.
Files that can't be paired are still scored on their jsonl-only metrics
(fallbacks, tier=ai, latency, forced-pick, mulligan coverage); only the
silent-drop reconciliation requires a paired log.

Usage:
  python3 eval/defect_analyzer.py <dir> [<dir> ...] [--json PATH] [--summary PATH]

Exit codes: 0 clean; 1 a hard invariant failed (build_report's
hard_invariants, named in docs/STANDARDS.md section 4: tier=ai count > 0, a
silent-drop gap > 0 anywhere, a crashed game, or a decision lost to a dead
model bridge, aggregate.bridge_down_decisions, issue #369, or a puzzle result row's
bridge_down_decisions, issue #378); 2 usage/input error (not a directory, or no
game-*.jsonl file found in any given directory).
"""

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from aggregate import bridge_down_decisions, log_game_file

# ------------------------------------------------------------- log parsing
#
# The four decision types that ever print "-> asking" in LlmSpikeGui.java
# (mulligan L524, attackers L578, blockers L638, play L795) are exactly the
# jsonl `type` values we reconcile the ask-count against.
ASK_TYPES = ("mulligan", "attackers", "blockers", "play")

_ASK_RE = re.compile(r'^\[([^\]]+)\]\s*\[ROUTER\]\s*([A-Za-z][A-Za-z_-]*)\S*.*-> asking')
_NON_ANSWER_RE = re.compile(r'^\[OPENROUTER ([^/\]]+)/(\w+)\]\s*non-answer')
_BRIDGE_NO_REPLY_RE = re.compile(r'^\[BRIDGE ([^\]]+)\]\s*no reply')
_PASS_RE = re.compile(r'^\[([^\]]+)\]\s*\[ROUTER\]\s*play:\s*PASS\b')
_AUTO_LEGAL_RE = re.compile(r'^\[([^\]]+)\]\s*\[AUTO-LEGAL\]')
# A block declaration the engine objects to that stood: the seat's fallback found no legal set, or an arm's 64 draws
# did not (issue #311). A set the fallback took ("took the legal set") is legal and not counted.
_ILLEGAL_BLOCKS_RE = re.compile(
    r'^\[([^\]]+)\]\s*\[BLOCK-INVALID\].*(no legal set was found|no legal block set in 64 draws)')


# Decision types whose answer is a SUBSET of the options (zero to all of them), so one option still
# leaves a choice. Every other type picks exactly one, and one option there is a forced pick.
SUBSET_TYPES = frozenset(("attackers", "blockers", "scry", "surveil"))

def parse_log_text(text):
    """Return per-seat log-side event counts from one game's stdout log.

    Keys: asks, non_answers, passes, auto_legal — each a Counter keyed by
    seat name (asks additionally keeps a per-type breakdown for context).
    """
    asks = Counter()
    ask_types = defaultdict(Counter)
    non_answers = Counter()
    passes = Counter()
    auto_legal = Counter()
    illegal_blocks = Counter()
    for line in text.splitlines():
        m = _ILLEGAL_BLOCKS_RE.match(line)
        if m:
            illegal_blocks[m.group(1)] += 1
            continue
        m = _ASK_RE.match(line)
        if m:
            seat, ty = m.group(1), m.group(2)
            asks[seat] += 1
            ask_types[seat][ty] += 1
            continue
        m = _NON_ANSWER_RE.match(line)
        if m:
            non_answers[m.group(1)] += 1
            continue
        m = _BRIDGE_NO_REPLY_RE.match(line)
        if m:
            non_answers[m.group(1)] += 1
            continue
        m = _PASS_RE.match(line)
        if m:
            passes[m.group(1)] += 1
            continue
        m = _AUTO_LEGAL_RE.match(line)
        if m:
            auto_legal[m.group(1)] += 1
            continue
    return {"asks": asks, "ask_types": {k: dict(v) for k, v in ask_types.items()},
            "non_answers": non_answers, "passes": passes, "auto_legal": auto_legal,
            "illegal_blocks": illegal_blocks}


# ------------------------------------------------------------ jsonl parsing

def parse_jsonl_file(path):
    """Return (decisions, games, n_skipped_lines) for one game-*.jsonl file.
    Malformed/truncated lines are skipped and counted, never raise."""
    decisions, games = [], []
    n_skipped = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    n_skipped += 1
                    continue
                if not isinstance(rec, dict):
                    n_skipped += 1
                    continue
                t = rec.get("t")
                if t == "decision":
                    decisions.append(rec)
                elif t == "game":
                    games.append(rec)
    except OSError:
        pass
    return decisions, games, n_skipped


# ------------------------------------------------------------------ pairing

_JSONL_RE = re.compile(r'^game-(-?\d+)-\d+\.jsonl$')
_LOG_RE = re.compile(r'^game-(-?\d+)-([01])\.log$')


def pair_dir(dir_path):
    """Pair every game-*.jsonl in dir_path with its game-<seed>-<0|1>.log,
    keyed by the jsonl's own `swap` field (falls back to filename-derived
    seed + best-effort unclaimed-log matching for crashed/no-game-line
    files). Returns a list of dicts:
      {jsonl, log, seed, swap, decisions, games, n_skipped, log_text,
       log_events (or None if unpaired), crash_text}
    crash_text is the text of the logs this file OWNS. Every log of the directory has exactly one owner: the file its
    "[EVAL] jsonl" line names when that file is in the directory, else the file it was paired with above, else none.
    """
    dir_path = Path(dir_path)
    jsonls = sorted(dir_path.glob("game-*.jsonl"))
    logs = sorted(dir_path.glob("game-*.log"))
    log_by_key = {}
    for lp in logs:
        m = _LOG_RE.match(lp.name)
        if m:
            log_by_key[(int(m.group(1)), int(m.group(2)))] = lp

    used_logs = set()
    log_texts = {}
    pairs = []
    for jp in jsonls:
        decisions, games, n_skipped = parse_jsonl_file(jp)
        seed, swap = None, None
        if games:
            last = games[-1]
            seed = last.get("seed")
            if isinstance(last.get("swap"), bool):
                swap = last.get("swap")
        if seed is None:
            m = _JSONL_RE.match(jp.name)
            if m:
                seed = int(m.group(1))

        log_path = None
        if seed is not None:
            if swap is not None:
                cand = log_by_key.get((seed, 1 if swap else 0))
                if cand is not None and cand not in used_logs:
                    log_path = cand
            if log_path is None:
                for sw in (0, 1):
                    cand = log_by_key.get((seed, sw))
                    if cand is not None and cand not in used_logs:
                        log_path = cand
                        break
        log_text, log_events = None, None
        if log_path is not None:
            used_logs.add(log_path)
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                log_text = None
            if log_text is not None:
                log_events = parse_log_text(log_text)
                log_texts[log_path] = log_text

        pairs.append({
            "jsonl": str(jp), "log": str(log_path) if log_path else None,
            "seed": seed, "swap": swap, "decisions": decisions, "log_text": log_text,
            "games": games, "n_skipped": n_skipped, "log_events": log_events, "crash_text": "",
        })

    by_file = {Path(p["jsonl"]).name: p for p in pairs}
    paired_with = {p["log"]: p for p in pairs if p["log"]}
    for lp in logs:
        named = log_game_file(lp)
        owner = by_file.get(named) or paired_with.get(str(lp))
        if owner is None:
            continue
        if lp not in log_texts:
            try:
                log_texts[lp] = lp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        owner["crash_text"] += log_texts[lp] + "\n"
    return pairs


# ------------------------------------------------------------------ helpers

def percentile(values, q):
    if not values:
        return None
    vs = sorted(values)
    return vs[max(0, math.ceil(q * len(vs)) - 1)]


def is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


_FALLBACK_HOST = re.compile(r"\s*\[[^\]]*\]")


def normalize_fallback(reason):
    """Group key for a fallback reason string: strip bracketed host-card
    names, then everything after '->'/': '/' x<n>' (mirrors
    analyze_corpus.normalize_fallback so the two tools agree on grouping)."""
    s = _FALLBACK_HOST.sub("", str(reason))
    for sep in ("->", ": "):
        i = s.find(sep)
        if i >= 0:
            s = s[:i]
    s = re.sub(r"\s+x\d+.*$", "", s)
    return s.strip()


# Expected reply-tag pattern per decision type, for the no-CHOICE/parse-fail
# heuristic. Types not listed fall back to _DEFAULT_TAG_RE.
_TAG_PATTERNS = {
    "mulligan": re.compile(r'\b(KEEP|MULLIGAN)\b', re.IGNORECASE),
}
# NOTE: this must only require the tag PREFIX, not a digit right after the colon.
# It used to require `-?\d` immediately following (CHOICE|PICKS|BLOCKS): ­\d, which
# produced 14/14 false positives in iter-1 (100% of that run's parse_fail_by_model
# count) against two reply shapes LlmSpikeGui.java's own parser already accepts
# fine: (1) `BLOCKS: name->name, name->name` — declareBlockers's format is card
# NAME pairs, never digits; (2) an intentionally EMPTY `PICKS:` (decideMany's
# valid "decline everything" answer when min==0 — e.g. attackers/surveil passing
# on 0 picks) has no digits after the tag either. Every candidate this regex is
# even evaluated against already has fallback=None on a smart/fast tier row,
# i.e. the harness's own Java parser (decideOne/decideMany's parseTagNumber /
# PICKS-digit-scan / declareBlockers's "->"-split) already found and used real
# content there — a reply that never had the tag at all (e.g. "I will just cast
# the spell") still fails this looser check exactly as before, since there is
# nothing to search for.
_DEFAULT_TAG_RE = re.compile(r'\b(CHOICE|PICKS|BLOCKS)\s*:', re.IGNORECASE)


def has_choice_tag(d):
    """True if a decision's raw_reply carries its expected answer tag."""
    raw = d.get("raw_reply")
    if not isinstance(raw, str):
        return True  # nothing to check (no reply captured at all)
    pat = _TAG_PATTERNS.get(d.get("type"), _DEFAULT_TAG_RE)
    return bool(pat.search(raw))


def model_consulted(d):
    return d.get("prompt") is not None


# ------------------------------------------------------------- summarizing

def puzzle_rows(dir_path):
    """The puzzle result rows (LlmSpikePuzzle, puzzle-results.jsonl) in dir_path, each tagged with its file; a puzzle
    attempt has no game line, so its row carries what the game line would (bridge_down_decisions, issue #378)."""
    path = Path(dir_path) / "puzzle-results.jsonl"
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(dict(row, _file=str(path)))
    return rows


def summarize(pairs, label, puzzles=()):
    """Build the full metrics dict for one directory's (already-paired) list
    of game dicts, as returned by pair_dir(), and its puzzle result rows (puzzle_rows())."""
    decisions = [d for p in pairs for d in p["decisions"]]
    n_games = len(pairs)
    n_skipped = sum(p["n_skipped"] for p in pairs)

    # --- fallback count by kind
    fb_counts = Counter()
    fb_examples = {}
    for d in decisions:
        fb = d.get("fallback")
        if fb is None:
            continue
        key = normalize_fallback(fb)
        fb_counts[key] += 1
        fb_examples.setdefault(key, {"seat": d.get("seat"), "turn": d.get("turn"),
                                     "type": d.get("type"), "raw": fb})
    fallback_by_kind = {k: {"count": v, "example": fb_examples.get(k)}
                        for k, v in sorted(fb_counts.items(), key=lambda kv: -kv[1])}

    # --- tier=ai hard invariant
    ai_lines = [{"file": p["jsonl"], "seat": d.get("seat"), "turn": d.get("turn"),
                 "type": d.get("type"), "fallback": d.get("fallback")}
                for p in pairs for d in p["decisions"] if d.get("tier") == "ai"]
    ai_count = len(ai_lines)

    # --- silent-drop reconciliation (only over paired files: log_events
    # is not None). For each seat, log-side ask count over ASK_TYPES vs
    # jsonl rows with prompt!=null over the same types.
    silent_drop = {}
    unpaired_files = []
    for p in pairs:
        if p["log_events"] is None:
            unpaired_files.append(p["jsonl"])
            continue
        asks = p["log_events"]["asks"]
        jsonl_consulted = Counter()
        for d in p["decisions"]:
            if d.get("type") in ASK_TYPES and model_consulted(d):
                jsonl_consulted[d.get("seat")] += 1
        seats = set(asks) | set(jsonl_consulted)
        for seat in seats:
            entry = silent_drop.setdefault(seat, {"log_asks": 0,
                                                  "jsonl_model_consulted": 0,
                                                  "gap": 0, "files": []})
            a = asks.get(seat, 0)
            j = jsonl_consulted.get(seat, 0)
            entry["log_asks"] += a
            entry["jsonl_model_consulted"] += j
            gap = a - j
            entry["gap"] += gap
            if gap > 0:
                entry["files"].append({"file": p["jsonl"], "log": p["log"],
                                       "log_asks": a, "jsonl_rows": j, "gap": gap})
    silent_drop_seats = sorted(s for s, v in silent_drop.items() if v["gap"] > 0)
    total_silent_drops = sum(max(0, v["gap"]) for v in silent_drop.values())

    # --- block declarations that stood illegal (paired logs only)
    illegal_blocks = Counter()
    for p in pairs:
        if p["log_events"] is not None:
            illegal_blocks.update(p["log_events"].get("illegal_blocks") or {})

    # --- no-CHOICE / parse-fail counts by model (seat), over genuinely
    # model-answered lines only (prompt+raw_reply both present, tier
    # smart/fast — a fallback-decided row's raw_reply is the dead first
    # attempt, not a thing to score for tag presence).
    parse_fail = Counter()
    for d in decisions:
        if d.get("tier") not in ("smart", "fast"):
            continue
        if not model_consulted(d) or d.get("raw_reply") is None:
            continue
        if d.get("fallback") is not None:
            continue
        if not has_choice_tag(d):
            parse_fail[d.get("seat")] += 1
    parse_fail_by_model = dict(sorted(parse_fail.items(), key=lambda kv: -kv[1]))

    # --- latency percentiles by tier/model (seat)
    lat = defaultdict(list)
    for d in decisions:
        v = d.get("latency_ms")
        if is_num(v) and v > 0:
            lat[(d.get("tier"), d.get("seat"))].append(v)
    latency_by_tier_model = {}
    for (tier, seat), vs in lat.items():
        latency_by_tier_model.setdefault(tier or "unknown", {})[seat or "unknown"] = {
            "n": len(vs), "p50_ms": percentile(vs, 0.5),
            "p90_ms": percentile(vs, 0.9), "max_ms": max(vs),
        }

    # --- finish_reason=length count (additive field; 0 if absent everywhere)
    finish_reason_length = sum(1 for d in decisions if d.get("finish_reason") == "length")

    # --- forced-pick mislabels: n_options==1 but tier != auto (C6)
    # Only for pick-EXACTLY-one menus. A SUBSET decision over one candidate is not forced: attack
    # with the one creature or not, block or not, keep the one scried card or not. Counting those
    # as mislabels is what led the harness to take them from the model (fixed 2026-10-13).
    forced_mislabels = [
        {"file": d.get("_srcfile"), "seat": d.get("seat"), "turn": d.get("turn"),
         "type": d.get("type"), "tier": d.get("tier")}
        for d in decisions
        if d.get("n_options") == 1 and d.get("tier") != "auto" and d.get("type") not in SUBSET_TYPES
    ]

    # --- mulligan rows present per seat
    all_seats = sorted({d.get("seat") for d in decisions if d.get("seat") is not None})
    mulligan_counts = Counter(d.get("seat") for d in decisions if d.get("type") == "mulligan")
    mulligan_by_seat = {s: mulligan_counts.get(s, 0) for s in all_seats}
    mulligan_missing_seats = sorted(s for s in all_seats if mulligan_counts.get(s, 0) == 0)

    # --- games whose thread crashed (runs/luna-v8/g2: a StackOverflowError scored as a draw)
    crashed = [str(p["jsonl"]) for p in pairs
               if any(g.get("game_error") for g in p.get("games") or [])
               or "game thread threw" in (p.get("crash_text") or "")]

    # --- decisions lost to a dead model bridge (issue #369: a 402 game read overall=PASS while this report's own
    # fallback table listed play(bridge-down) 260): the game line's count, or on a line from before it the transport
    # failures in its paired log (aggregate.bridge_down_decisions, the one reader)
    bridge_down = {}
    for p in pairs:
        if p.get("games"):
            # The game's own file, so paired_log judges it by the log that names it, as aggregate.py does.
            n = bridge_down_decisions(dict(p["games"][-1], _file=Path(p["jsonl"]).name), Path(p["jsonl"]).parent)
            if n:
                bridge_down[str(p["jsonl"])] = n
    for r in puzzles:   # a puzzle attempt's own count (issue #378)
        if r.get("bridge_down_decisions"):
            bridge_down["%s %s seed %s" % (r["_file"], r.get("id"), r.get("seed"))] = r["bridge_down_decisions"]

    return {
        "label": label,
        "n_games": n_games,
        "bridge_down_total": sum(bridge_down.values()),
        "bridge_down_files": bridge_down,
        "game_crash_count": len(crashed),
        "game_crash_files": crashed,
        "n_decisions": len(decisions),
        "n_skipped_lines": n_skipped,
        "unpaired_files": unpaired_files,
        "fallback_by_kind": fallback_by_kind,
        "ai_tier_count": ai_count,
        "ai_tier_lines": ai_lines,
        "silent_drop_by_seat": silent_drop,
        "silent_drop_seats": silent_drop_seats,
        "silent_drop_total": total_silent_drops,
        "illegal_blocks_by_seat": dict(sorted(illegal_blocks.items())),
        "illegal_blocks_total": sum(illegal_blocks.values()),
        "parse_fail_by_model": parse_fail_by_model,
        "latency_by_tier_model": latency_by_tier_model,
        "finish_reason_length_count": finish_reason_length,
        "forced_pick_mislabels": {"count": len(forced_mislabels),
                                  "examples": forced_mislabels[:20]},
        "mulligan_by_seat": mulligan_by_seat,
        "mulligan_missing_seats": mulligan_missing_seats,
    }


def build_report(dirs):
    seen, uniq = set(), []
    for d in dirs:
        rp = Path(d).expanduser().resolve()
        if rp in seen:
            print("warning: duplicate dir ignored: %s" % d, file=sys.stderr)
            continue
        seen.add(rp)
        uniq.append(d)

    per_dir = {}
    all_pairs, all_puzzles = [], []
    for d in uniq:
        pairs = pair_dir(d)
        puzzles = puzzle_rows(d)
        all_puzzles.extend(puzzles)
        # tag each decision with its source jsonl for forced-pick examples
        for p in pairs:
            for dec in p["decisions"]:
                dec["_srcfile"] = p["jsonl"]
        per_dir[str(d)] = summarize(pairs, str(d), puzzles)
        all_pairs.extend(pairs)

    combined = summarize(all_pairs, "combined", all_puzzles)
    ai_ok = combined["ai_tier_count"] == 0
    drop_ok = combined["silent_drop_total"] == 0
    crash_ok = combined["game_crash_count"] == 0
    bridge_ok = combined["bridge_down_total"] == 0
    report = {
        "dirs": per_dir,
        "combined": combined,
        "hard_invariants": {
            "ai_tier_count": combined["ai_tier_count"],
            "ai_tier_ok": ai_ok,
            "silent_drop_total": combined["silent_drop_total"],
            "silent_drop_ok": drop_ok,
            "game_crash_count": combined["game_crash_count"],
            "game_crash_ok": crash_ok,
            "bridge_down_total": combined["bridge_down_total"],
            "bridge_down_ok": bridge_ok,
            "pass": ai_ok and drop_ok and crash_ok and bridge_ok,
        },
    }
    return report


# --------------------------------------------------------------- rendering

def fmt_ms(x):
    return "-" if x is None else "%.0f" % x


def _render_scope(name, s):
    L = ["=== %s ===" % name,
         "games=%d  decisions=%d  skipped_malformed_lines=%d"
         % (s["n_games"], s["n_decisions"], s["n_skipped_lines"])]
    if s["unpaired_files"]:
        L.append("unpaired (no matching .log, jsonl-only metrics used): %s"
                 % ", ".join(s["unpaired_files"]))

    L.append("")
    L.append("--- fallback count by kind ---")
    if not s["fallback_by_kind"]:
        L.append("(none)")
    for k, v in s["fallback_by_kind"].items():
        ex = v["example"] or {}
        L.append("  %-30s %6d   e.g. seat=%s turn=%s type=%s raw=%r"
                 % (k, v["count"], ex.get("seat"), ex.get("turn"),
                    ex.get("type"), ex.get("raw")))

    L.append("")
    L.append("--- decisions lost to a dead model bridge (MUST be 0) ---")
    L.append("count=%d" % s["bridge_down_total"])
    for f, n in sorted(s["bridge_down_files"].items()):
        L.append("  VIOLATION %s lost %d" % (f, n))

    L.append("")
    L.append("--- tier=ai hard invariant (MUST be 0) ---")
    L.append("count=%d" % s["ai_tier_count"])
    for a in s["ai_tier_lines"]:
        L.append("  VIOLATION %s seat=%s turn=%s type=%s fallback=%r"
                 % (a["file"], a["seat"], a["turn"], a["type"], a["fallback"]))

    L.append("")
    L.append("--- silent-drop reconciliation (log '-> asking' events vs "
             "jsonl rows, over mulligan/attackers/blockers/play) ---")
    if not s["silent_drop_by_seat"]:
        L.append("(no paired log/jsonl data)")
    for seat, v in sorted(s["silent_drop_by_seat"].items()):
        flag = "  <-- SILENT DROP" if v["gap"] > 0 else ""
        L.append("  %-20s log_asks=%-4d jsonl_rows=%-4d gap=%-4d%s"
                 % (seat, v["log_asks"], v["jsonl_model_consulted"], v["gap"], flag))
        for f in v["files"]:
            L.append("      %s (log=%s) asks=%d rows=%d gap=%d"
                     % (f["file"], f["log"], f["log_asks"], f["jsonl_rows"], f["gap"]))
    L.append("total silent drops=%d" % s["silent_drop_total"])

    L.append("")
    L.append("--- illegal block sets that stood: %d ([BLOCK-INVALID] no legal set found) ---"
             % s["illegal_blocks_total"])
    for seat, n in s["illegal_blocks_by_seat"].items():
        L.append("  %-20s %d" % (seat, n))

    L.append("")
    L.append("--- no-CHOICE / parse-fail counts by model ---")
    if not s["parse_fail_by_model"]:
        L.append("(none)")
    for seat, n in s["parse_fail_by_model"].items():
        L.append("  %-20s %d" % (seat, n))

    L.append("")
    L.append("--- latency percentiles by tier/model (ms; latency_ms>0 only) ---")
    if not s["latency_by_tier_model"]:
        L.append("(no measured latencies)")
    for tier in sorted(s["latency_by_tier_model"]):
        for seat, t in sorted(s["latency_by_tier_model"][tier].items()):
            L.append("  tier=%-6s seat=%-20s n=%-4d p50=%-8s p90=%-8s max=%-8s"
                     % (tier, seat, t["n"], fmt_ms(t["p50_ms"]),
                        fmt_ms(t["p90_ms"]), fmt_ms(t["max_ms"])))

    L.append("")
    L.append("--- finish_reason=length count ---")
    L.append("count=%d" % s["finish_reason_length_count"])

    L.append("")
    L.append("--- forced-pick mislabels (n_options==1 but tier!=auto) ---")
    fp = s["forced_pick_mislabels"]
    L.append("count=%d" % fp["count"])
    for ex in fp["examples"]:
        L.append("  %s seat=%s turn=%s type=%s tier=%s"
                 % (ex["file"], ex["seat"], ex["turn"], ex["type"], ex["tier"]))

    L.append("")
    L.append("--- mulligan rows present per seat ---")
    if not s["mulligan_by_seat"]:
        L.append("(no seats observed)")
    for seat, n in sorted(s["mulligan_by_seat"].items()):
        flag = "  <-- NO MULLIGAN ROWS (heuristic keep with no jsonl record?)" if n == 0 else ""
        L.append("  %-20s %d%s" % (seat, n, flag))

    return L


def render_text(report):
    lines = []
    for name, s in report["dirs"].items():
        lines.extend(_render_scope(name, s))
        lines.append("")
    if len(report["dirs"]) > 1:
        lines.extend(_render_scope("COMBINED (%d dirs)" % len(report["dirs"]),
                                   report["combined"]))
        lines.append("")
    hi = report["hard_invariants"]
    lines.append("=== hard invariants ===")
    lines.append("ai_tier_count=%d (ok=%s)  silent_drop_total=%d (ok=%s)  game_crash_count=%d (ok=%s)  "
                 "bridge_down_total=%d (ok=%s)  overall=%s"
                 % (hi["ai_tier_count"], hi["ai_tier_ok"], hi["silent_drop_total"],
                    hi["silent_drop_ok"], hi["game_crash_count"], hi["game_crash_ok"],
                    hi["bridge_down_total"], hi["bridge_down_ok"], "PASS" if hi["pass"] else "FAIL"))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", metavar="run_dir",
                    help="directory (or directories) of game-*.jsonl / game-*.log pairs")
    ap.add_argument("--json", metavar="PATH",
                    help="write metrics.json (machine-readable) to PATH")
    ap.add_argument("--summary", metavar="PATH",
                    help="also write the human-readable text summary to PATH")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    for d in args.dirs:
        if not Path(d).expanduser().is_dir():
            print("not a directory: %s" % d, file=sys.stderr)
            sys.exit(2)

    report = build_report([Path(d).expanduser() for d in args.dirs])

    if report["combined"]["n_games"] == 0:
        print("no game-*.jsonl files found in %s" % ", ".join(args.dirs), file=sys.stderr)
        sys.exit(2)

    text = render_text(report)
    print(text)

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("\n(metrics written to %s)" % args.json)
    if args.summary:
        Path(args.summary).write_text(text, encoding="utf-8")
        print("(summary written to %s)" % args.summary)

    hi = report["hard_invariants"]
    if not hi["pass"]:
        print("\nHARD INVARIANT FAILURE: ai_tier_count=%d silent_drop_total=%d game_crash_count=%d bridge_down_total=%d"
             % (hi["ai_tier_count"], hi["silent_drop_total"], hi["game_crash_count"], hi["bridge_down_total"]),
              file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
