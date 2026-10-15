#!/usr/bin/env python3
"""Phase-0 puzzle-candidate miner (EVAL-DESIGN.md S3.2) — CPU/text-only, stdlib.

Scans per-decision jsonl corpora of ALREADY-PLAYED games (game-*.jsonl, one
game per file, written by the harness) and emits CANDIDATE puzzle positions:
cheap text-regex hypotheses over the `prompt` state snapshot + `options`
menu. Candidates carry no `answer_key` — they are unverified. Verification
(exhaustive enumeration or N-rollout win-probability) is a separate later
stage (LlmSpikeFork.java, Phase 1) that re-reaches the position by
deterministic replay and adjudicates it. Miner precision only affects YIELD
(how many candidates survive verification), never correctness — a wrong
guess here just gets rejected downstream, so heuristics here are allowed to
be broad/approximate as long as that's stated.

Eligibility (every category): t=="decision", prompt non-null, n_options>=2,
no `fallback` (a fallback row was never actually decided by a strategy —
deterministic legal fallback, not a real choice point).

Categories (EVAL-DESIGN.md S3.2 table):
  lethal-now       own attacking power (+ castable burn) >= opponent life,
                   at a play or attackers decision.
  save-vs-lethal   opponent's board power >= own life, at a play decision
                   (own turn) — "if that swings in next turn, I'm dead."
  forced-block     blockers decision where total incoming (unblocked)
                   power >= own life.
  free-win         opponent has a spell on the stack and a curated
                   "interaction" card sits in own hand. LOW PRECISION BY
                   DESIGN: whether the stacked spell is actually
                   game-winning is NOT recoverable from the prompt text (no
                   card database, no effect resolution) — this is a broad
                   proxy, not a lethality detector. See category_evidence
                   for exactly what matched; every candidate says so.
  mulligan         every eligible mulligan decision. No filtering heuristic
                   (rollout WP-gap filtering happens at verification) — do
                   NOT cap this category by default.
  graded-midgame   no heuristic; a seeded, turn-stratified random sample of
                   eligible play/attackers decisions, turns 3-8.

Text-format ground truth (verified against runs/stage0-calibration and
runs/tournament-1 before writing any regex — see the report accompanying
this file for the full note):
  "YOU [<seat>] life <N> | poison <N>/10" / same for "OPP [...]"
  "  board: Name(P/T)[tag][tag], Name(P/T), NonCreaturePermanent, ..."
    tags include [T] (tapped), [sick] (summoning sick), [+1/+1 xN] (counters)
  "  hand(<N>): Card {cost} (need ...), Land, ..."
  "  mana available: {W:1 R:5} (<N> untapped sources)"
  "stack (top first): <K> pending: #<i> CardName (Controller) | #<i> ..."
    or "stack (top first): (empty)"
  attackers/blockers menu options: "Card Name (P/T)" (space before paren,
    vs. no space on the board line — both matched by the same P/T regex).
  decision row already carries `turn`/`phase`/`seat`/`type` as JSON fields
    (no need to regex the "== Turn N ..." header for those).

Usage:
  python3 eval/mine_puzzles.py DIR [DIR ...] --out candidates.jsonl \\
      [--categories lethal-now,mulligan] [--max-per-category N] \\
      [--sample-seed 20260725] [--exclude-train-meta datasets/*/meta.jsonl]

DIR is scanned recursively for game-*.jsonl (so both a flat corpus like
runs/stage0-calibration and a tree like runs/tournament-1/match-*/ work).
Deterministic given --sample-seed: re-running with the same inputs and seed
reproduces byte-identical output.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_sft_data import iter_game_dirs, load_complete_games  # noqa: E402

try:
    import seed_policy
except ImportError:
    seed_policy = None
    print("NOTE: eval/seed_policy.py not found — seed_block tagging disabled "
          "(candidates still emitted, just without the reserved-block tag).",
          file=sys.stderr)


CATEGORIES = ("lethal-now", "save-vs-lethal", "forced-block", "free-win",
              "mulligan", "graded-midgame")

# Decision `type`s eligible per category (base eligibility filter still
# applies on top of this: prompt non-null, n_options>=2, no fallback).
CATEGORY_TYPES = {
    "lethal-now": frozenset(("play", "attackers")),
    "save-vs-lethal": frozenset(("play",)),
    "forced-block": frozenset(("blockers",)),
    "free-win": frozenset(("play", "target", "targets")),
    "mulligan": frozenset(("mulligan",)),
    "graded-midgame": frozenset(("play", "attackers")),
}

# Default per-category candidate cap when --max-per-category is not given.
# Mirrors the EVAL-DESIGN.md S3.2 "Target n (v1)" column: lethal-now 300,
# save-vs-lethal 150, forced-block 150, free-win 100, mulligan 150,
# graded-midgame 150. Mining emits ALL matching mulligan decisions up to the
# cap (the S3.2 WP-gap filter for mulligans is applied later, at engine
# verification, not here).
DEFAULT_TARGETS = {
    "lethal-now": 300, "save-vs-lethal": 150, "forced-block": 150,
    "free-win": 100, "mulligan": 150, "graded-midgame": 150,
}

# Controllers under which a game is bit-deterministically replayable by the
# fork harness (README/CLAUDE.md: floor/random/ai/ai-log are seed-derived;
# llm is not — an LLM call can never be re-reached).
DETERMINISTIC_CONTROLLERS = frozenset(("floor", "random", "ai", "ai-log"))

# Curated, deliberately small: burn spells actually seen in these corpora's
# Burn-deck hands, with their face damage. NOT a general card database —
# unknown burn spells are simply invisible to this heuristic (documented
# limitation, not a bug: it only affects yield).
DAMAGE_SPELLS = {
    "Lightning Bolt": 3, "Chain Lightning": 3, "Lava Spike": 3,
    "Rift Bolt": 3, "Fireblast": 4,
}

# Curated instant-speed "answer" cards (counterspells + cheap interaction)
# used only by the free-win low-precision proxy below.
ANSWER_CARDS = (
    "Force of Will", "Daze", "Spell Pierce", "Flusterstorm", "Swan Song",
    "Mental Misstep", "Counterspell", "Mindbreak Trap", "Pyroblast",
    "Red Elemental Blast", "Fatal Push", "Swords to Plowshares",
)

# ------------------------------------------------------------- text regexes

_OWN_LIFE_RE = re.compile(r"YOU \[[^\]]*\] life (\d+)")
_OPP_LIFE_RE = re.compile(r"OPP \[[^\]]*\] life (\d+)")
_BOARD_RE = re.compile(r"^  board: (.*)$", re.M)
_HAND_RE = re.compile(r"^  hand\(\d+\): (.*)$", re.M)
_MANA_SOURCES_RE = re.compile(r"mana available: \{[^}]*\} \((\d+) untapped sources[;)]")  # v23 adds "; each is counted ..."
_PT_RE = re.compile(r"\((\d+)/(\d+)\)((?:\[[^\]]*\])*)")
_INCOMING_RE = re.compile(r"Incoming: (.*?)\.\s*Your blockers:")
_STACK_RE = re.compile(r"stack \(top first\): (.*)")
_STACK_ENTRY_RE = re.compile(r"#\d+ (.+?) \(([^()]+)\)")


def _first_int(rx, text):
    m = rx.search(text)
    return int(m.group(1)) if m else None


def split_you_opp(prompt):
    """Split a decision prompt into (own-section, opp-section) substrings so
    P/T and hand regexes can be scoped to the right side — the board/hand
    line format is IDENTICAL on both sides, so an unscoped regex would sum
    both players' creatures together."""
    opp_idx = prompt.find("\nOPP [")
    if opp_idx == -1:
        return prompt, ""
    you_part = prompt[:opp_idx]
    stack_idx = prompt.find("\nstack (top first)", opp_idx)
    opp_part = prompt[opp_idx:stack_idx] if stack_idx != -1 else prompt[opp_idx:]
    return you_part, opp_part


def _board_power(section_text, skip_tapped_or_sick=False):
    m = _BOARD_RE.search(section_text)
    if not m:
        return 0
    total = 0
    for power, _tough, tags in _PT_RE.findall(m.group(1)):
        if skip_tapped_or_sick and ("[T]" in tags or "[sick]" in tags):
            continue
        total += int(power)
    return total


def _options_power(options):
    total = 0
    for opt in options or ():
        for power, _tough, _tags in _PT_RE.findall(opt):
            total += int(power)
    return total


def _castable_burn(you_part):
    """Approximate (color-blind) castable-burn total from the own hand line.
    Returns (total_damage, [matched card names]). Castability is checked
    only against total untapped mana SOURCES vs. the card's mana VALUE
    (numeral prefix + curly-brace symbol count) — colors are not matched, so
    this can over-count when the deck lacks a color. Documented limitation,
    not a correctness issue for a candidate miner (engine verification is
    the real check)."""
    m = _HAND_RE.search(you_part)
    if not m:
        return 0, []
    hand_line = m.group(1)
    avail = _first_int(_MANA_SOURCES_RE, you_part)
    if avail is None:
        avail = 0
    total, matched = 0, []
    for name, dmg in DAMAGE_SPELLS.items():
        if name not in hand_line:
            continue
        cm = re.search(re.escape(name) + r"\s*(\d*)((?:\{[^{}]+\})*)", hand_line)
        mv = 0
        if cm:
            digits, syms = cm.group(1), cm.group(2)
            mv = (int(digits) if digits else 0) + len(re.findall(r"\{[^{}]+\}", syms))
        if mv <= avail:
            total += dmg
            matched.append(name)
    return total, matched


# ------------------------------------------------------------- eligibility

def eligible(d):
    return (bool(d.get("prompt"))
            and isinstance(d.get("n_options"), int)
            and not isinstance(d.get("n_options"), bool)
            and d.get("n_options") >= 2
            and d.get("fallback") is None)


# ------------------------------------------------------------- categories

def check_lethal_now(d):
    prompt = d["prompt"]
    opp_life = _first_int(_OPP_LIFE_RE, prompt)
    if not opp_life:
        return None
    you_part, _opp_part = split_you_opp(prompt)
    if d.get("type") == "attackers":
        power = _options_power(d.get("options"))
        power_desc = "options_power"
    else:
        power = _board_power(you_part, skip_tapped_or_sick=True)
        power_desc = "own_untapped_power"
    burn, burn_names = _castable_burn(you_part)
    total = power + burn
    if total <= 0 or total < opp_life:
        return None
    ev = "%s=%d castable_burn=%d(%s) total=%d >= opp_life=%d" % (
        power_desc, power, burn, ",".join(burn_names) or "-", total, opp_life)
    return ev


def check_save_vs_lethal(d):
    prompt = d["prompt"]
    own_life = _first_int(_OWN_LIFE_RE, prompt)
    if not own_life:
        return None
    _you_part, opp_part = split_you_opp(prompt)
    power = _board_power(opp_part, skip_tapped_or_sick=False)
    if power <= 0 or power < own_life:
        return None
    return "opp_board_power=%d >= own_life=%d (next-attack projection)" % (power, own_life)


def check_forced_block(d):
    prompt = d["prompt"]
    own_life = _first_int(_OWN_LIFE_RE, prompt)
    m = _INCOMING_RE.search(prompt)
    if not m or not own_life:
        return None
    power = sum(int(p) for p, _t, _tags in _PT_RE.findall(m.group(1)))
    if power <= 0 or power < own_life:
        return None
    return "incoming_power=%d >= own_life=%d" % (power, own_life)


def check_free_win(d):
    prompt = d["prompt"]
    m = _STACK_RE.search(prompt)
    if not m or "empty" in m.group(1):
        return None
    seat = d.get("seat") or ""
    entries = _STACK_ENTRY_RE.findall(m.group(1))
    opp_cards = [name for name, ctrl in entries if ctrl.strip() != seat]
    if not opp_cards:
        return None
    you_part, _opp_part = split_you_opp(prompt)
    hand_m = _HAND_RE.search(you_part)
    hand_line = hand_m.group(1) if hand_m else ""
    matched = [c for c in ANSWER_CARDS if c in hand_line]
    if not matched:
        return None
    return ("opp_stack=%s answer_in_hand=%s "
            "[LOW-PRECISION: game-winning-ness of the stacked spell is not "
            "verifiable from prompt text alone]" % (opp_cards, matched))


def check_mulligan(_d):
    return "mulligan decision (unfiltered — verification does the WP-gap filtering)"


CHECKS = {
    "lethal-now": check_lethal_now,
    "save-vs-lethal": check_save_vs_lethal,
    "forced-block": check_forced_block,
    "free-win": check_free_win,
    "mulligan": check_mulligan,
}


# ---------------------------------------------------------------- sampling

def _sample_hash(seed, rid):
    return hashlib.sha256(("%d:%s" % (seed, rid)).encode("utf-8")).hexdigest()


def cap_rows(rows, cap, sample_seed):
    if cap is None or len(rows) <= cap:
        return sorted(rows, key=lambda r: r["_rid"])
    return sorted(rows, key=lambda r: _sample_hash(sample_seed, r["_rid"]))[:cap]


def stratified_by_turn(rows, cap, sample_seed):
    """Deterministic turn-balanced sample (round-robin over hash-sorted
    per-turn buckets) — EVAL-DESIGN.md's "seeded stratified random sample"
    for graded-midgame."""
    if cap is None or len(rows) <= cap:
        return sorted(rows, key=lambda r: r["_rid"])
    buckets = defaultdict(list)
    for r in rows:
        buckets[r["turn"]].append(r)
    for t in buckets:
        buckets[t].sort(key=lambda r: _sample_hash(sample_seed, r["_rid"]))
    out = []
    keys = sorted(buckets)
    i = 0
    while len(out) < cap and any(buckets[k] for k in keys):
        k = keys[i % len(keys)]
        if buckets[k]:
            out.append(buckets[k].pop(0))
        i += 1
    return sorted(out, key=lambda r: r["_rid"])


# ------------------------------------------------------- --exclude-train-meta

def load_exclude_meta(paths):
    """Build the (seed, swap) set of games that fed any SFT dataset, from a
    list of datasets/*/meta.jsonl files. Returns (exclude_set,
    overlap_reliable, any_given, warnings).

    overlap_reliable is False if ANY given file could not yield a single
    recoverable (seed, swap) identity — in that case we refuse to claim
    clean-ness for candidates (excluded_from_sft_overlap is set to False,
    conservatively, rather than guessed)."""
    exclude_set = set()
    warnings = []
    any_given = bool(paths)
    reliable = True
    for raw in paths:
        p = Path(raw)
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            warnings.append("cannot read %s: %s" % (p, e))
            reliable = False
            continue
        n_total = n_seed = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            n_total += 1
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            seed = rec.get("seed")
            if isinstance(seed, int) and not isinstance(seed, bool):
                n_seed += 1
                exclude_set.add((seed, bool(rec.get("swap", False))))
        if n_total > 0 and n_seed == 0:
            warnings.append(
                "%s: no row carries a recoverable (seed,swap) identity — "
                "overlap exclusion UNAVAILABLE from this file. Candidates "
                "will be marked excluded_from_sft_overlap=False (not "
                "guessed clean)." % p)
            reliable = False
    return exclude_set, reliable, any_given, warnings


# ----------------------------------------------------------------- output

def make_candidate(category, d, g, corpus, game_file, decision_index,
                    evidence, seed_block, excluded_from_sft_overlap):
    engine_sha = g.get("engine_sha")
    overlay_sha = g.get("overlay_sha")
    seed = g.get("seed")
    swap = bool(g.get("swap", False))
    # id is unique PER LINE. Two salts beyond the replay address are required:
    #   * category — one decision can match several categories (lethal-now AND
    #     save-vs-lethal), each match is its own candidate line;
    #   * game_file — (engine_sha, seed, swap) does NOT identify a game
    #     instance: tournament matches replayed the same seed/swap with
    #     different pilots, and for non-deterministic (llm) games the same
    #     decision_index is a genuinely different state, so deduping on the
    #     tuple would silently drop distinct positions (found live: 420
    #     lines / 417 ids over stage0-calibration + tournament-1).
    # Downstream stores may key on id alone. The replay address for the fork
    # harness is the `source` block, shared across categories of one decision.
    cid = hashlib.sha256(
        ("%s|%s|%s|%s|%d|%s" % (engine_sha, seed, swap, game_file,
                                decision_index, category)).encode("utf-8")
    ).hexdigest()[:16]
    controller_a = g.get("controller_a")
    controller_b = g.get("controller_b")
    replayable = (controller_a in DETERMINISTIC_CONTROLLERS
                  and controller_b in DETERMINISTIC_CONTROLLERS)
    out = {
        "schema": "puzzle-position.v1",
        "id": cid,
        "category": category,
        "source": {
            "engine_sha": engine_sha, "overlay_sha": overlay_sha,
            "deck_a": g.get("deck_a"), "deck_b": g.get("deck_b"),
            "controllers": "%s/%s" % (controller_a, controller_b),
            "seed": seed, "swap": swap,
            "decision_index": decision_index, "game_file": game_file,
            "turn": d.get("turn"), "decision_type": d.get("type"),
            "seat": d.get("seat"),
        },
        "prompt": d.get("prompt"),
        "options": d.get("options"),
        "n_options": d.get("n_options"),
        "status": "candidate",
        "replayable": replayable,
        "split": "open",
        "category_evidence": evidence,
    }
    if seed_block is not None:
        out["seed_block"] = seed_block
    if excluded_from_sft_overlap is not None:
        out["excluded_from_sft_overlap"] = excluded_from_sft_overlap
    return out


# -------------------------------------------------------------------- scan

def scan(dirs, categories, exclude_set, overlap_reliable, any_meta_given):
    """Returns (raw_rows, stats, corpus_totals, scan_warnings).

    raw_rows: category -> list of dict (pre-cap), each with the fields
      needed to build a candidate line plus "_rid" for deterministic
      sampling.
    stats: category -> corpus -> {"eligible": n, "matched": n} (matched =
      raw candidates found for that category before any cap is applied).
    corpus_totals: corpus -> {"games": n, "skipped_overlap": n}.
    """
    raw_rows = defaultdict(list)
    stats = defaultdict(lambda: defaultdict(lambda: {"eligible": 0, "matched": 0}))
    corpus_totals = defaultdict(lambda: {"games": 0, "skipped_overlap": 0})
    warnings = []

    for dir_arg in dirs:
        root = Path(dir_arg)
        for gd in iter_game_dirs(root):
            games, w = load_complete_games(gd)
            warnings.extend("%s: %s" % (dir_arg, x) for x in w)
            for entry in games:
                g = entry["game"]
                seed = g.get("seed")
                swap = bool(g.get("swap", False))
                corpus_totals[dir_arg]["games"] += 1
                if any_meta_given and (seed, swap) in exclude_set:
                    corpus_totals[dir_arg]["skipped_overlap"] += 1
                    continue
                excluded_flag = None
                if any_meta_given:
                    excluded_flag = True if overlap_reliable else False
                seed_block = None
                if seed_policy is not None and isinstance(seed, int) and not isinstance(seed, bool):
                    seed_block = seed_policy.reserved_block(seed)

                for di, d in enumerate(entry["decisions"]):
                    dtype = d.get("type")
                    is_elig = eligible(d)
                    for cat in categories:
                        if dtype not in CATEGORY_TYPES[cat]:
                            continue
                        if cat == "graded-midgame":
                            turn = d.get("turn")
                            if not (isinstance(turn, int) and not isinstance(turn, bool)
                                    and 3 <= turn <= 8):
                                continue
                            if not is_elig:
                                continue
                            stats[cat][dir_arg]["eligible"] += 1
                            stats[cat][dir_arg]["matched"] += 1
                            raw_rows[cat].append({
                                "d": d, "g": g, "corpus": dir_arg,
                                "game_file": entry["file"], "decision_index": di,
                                "evidence": "stratified sample (turn=%d)" % turn,
                                "seed_block": seed_block, "excluded": excluded_flag,
                                "turn": turn,
                                "_rid": "%s|%s|%d" % (dir_arg, entry["file"], di),
                            })
                            continue
                        if not is_elig:
                            continue
                        stats[cat][dir_arg]["eligible"] += 1
                        evidence = CHECKS[cat](d)
                        if evidence is None:
                            continue
                        stats[cat][dir_arg]["matched"] += 1
                        raw_rows[cat].append({
                            "d": d, "g": g, "corpus": dir_arg,
                            "game_file": entry["file"], "decision_index": di,
                            "evidence": evidence, "seed_block": seed_block,
                            "excluded": excluded_flag, "turn": d.get("turn"),
                            "_rid": "%s|%s|%d" % (dir_arg, entry["file"], di),
                        })
    return raw_rows, stats, corpus_totals, warnings


# ---------------------------------------------------------------- reporting

def print_summary(categories, stats, final_rows, corpus_totals, any_meta_given, out):
    dirs = sorted(corpus_totals)
    if any_meta_given:
        print("\nSFT-overlap exclusion (--exclude-train-meta):", file=out)
        for d in dirs:
            t = corpus_totals[d]
            print("  %s: %d games scanned, %d skipped (SFT overlap)"
                  % (d, t["games"], t["skipped_overlap"]), file=out)

    print("\n%-16s %10s %10s %10s   per-corpus (eligible/matched)"
          % ("category", "eligible", "matched", "emitted"), file=out)
    print("-" * 90, file=out)
    for cat in categories:
        eligible_n = sum(stats[cat][d]["eligible"] for d in dirs)
        matched_n = sum(stats[cat][d]["matched"] for d in dirs)
        emitted_n = len(final_rows.get(cat, []))
        per_corpus = ", ".join(
            "%s=%d/%d" % (d, stats[cat][d]["eligible"], stats[cat][d]["matched"])
            for d in dirs if stats[cat][d]["eligible"] or stats[cat][d]["matched"])
        print("%-16s %10d %10d %10d   %s"
              % (cat, eligible_n, matched_n, emitted_n, per_corpus or "-"), file=out)


# --------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Mine candidate puzzle positions from decision jsonl corpora.")
    ap.add_argument("dirs", nargs="+", metavar="DIR",
                     help="run directories to scan recursively for game-*.jsonl")
    ap.add_argument("--out", required=True, help="output candidates jsonl path")
    ap.add_argument("--categories", default=None,
                     help="comma-separated subset of: " + ",".join(CATEGORIES))
    ap.add_argument("--max-per-category", type=int, default=None,
                     help="cap applied to every selected category (overrides "
                          "the per-category EVAL-DESIGN targets, including "
                          "mulligan's default of no cap)")
    ap.add_argument("--sample-seed", type=int, default=20260725,
                     help="determinism seed for cap/stratified sampling")
    ap.add_argument("--exclude-train-meta", nargs="*", default=[],
                     metavar="PATH", help="datasets/*/meta.jsonl file(s); "
                     "games that fed an SFT dataset are skipped")
    args = ap.parse_args(argv)

    if args.categories:
        categories = tuple(c.strip() for c in args.categories.split(",") if c.strip())
        for c in categories:
            if c not in CATEGORIES:
                ap.error("unknown category %r (choices: %s)" % (c, ", ".join(CATEGORIES)))
    else:
        categories = CATEGORIES

    exclude_set, overlap_reliable, any_meta_given, meta_warnings = \
        load_exclude_meta(args.exclude_train_meta)
    for w in meta_warnings:
        print("WARNING: %s" % w, file=sys.stderr)

    raw_rows, stats, corpus_totals, scan_warnings = scan(
        args.dirs, categories, exclude_set, overlap_reliable, any_meta_given)
    for w in scan_warnings:
        print("WARNING: %s" % w, file=sys.stderr)

    final_rows = {}
    for cat in categories:
        cap = args.max_per_category if args.max_per_category is not None else DEFAULT_TARGETS[cat]
        rows = raw_rows.get(cat, [])
        if cat == "graded-midgame":
            final_rows[cat] = stratified_by_turn(rows, cap, args.sample_seed)
        else:
            final_rows[cat] = cap_rows(rows, cap, args.sample_seed)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for cat in categories:
            for r in final_rows[cat]:
                cand = make_candidate(
                    cat, r["d"], r["g"], r["corpus"], r["game_file"],
                    r["decision_index"], r["evidence"], r["seed_block"],
                    r["excluded"])
                f.write(json.dumps(cand, ensure_ascii=False) + "\n")
                n_written += 1

    print("\nwrote %d candidates -> %s" % (n_written, out_path))
    print_summary(categories, stats, final_rows, corpus_totals, any_meta_given, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
