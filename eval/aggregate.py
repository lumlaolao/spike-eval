#!/usr/bin/env python3
"""Aggregate MTG Forge LLM eval JSONL logs into win rates + decision stats.

Reads every game-*.jsonl in a directory. Prints:
  - per-arm win rates with Wilson 95% CIs (draws/invalids excluded from
    denominators, reported separately)
  - paired analysis when both swap variants of a seed exist (per-seed
    paired outcomes + paired-bootstrap CI on the win-rate delta)
  - decision-level stats: per-tier counts, mean/p90 latency, fallback
    rates, per-decision-type volume

Contract (final, documented in eval/README.md): the game line is
IDENTITY-KEYED, not seat-keyed. winner "A" means deck-identity A (the
DECK_A/CONTROLLER_A/SMART_A configuration) won, REGARDLESS of seat swap;
controller_a/smart_a/... always describe identity A's raw config. swap:true
is pairing/seat metadata only: it records that identity B sat in seat 1
(went first into the players list). Arm attribution therefore never flips
on swap; only the seat-bias diagnostic consults it.

Experiment grouping (2026-09-06): a directory may hold games recorded under
more than one CONDITION (different context profile, memory tools, model,
checkpoint, budgets, ...). Games are grouped by their full recorded condition
BEFORE any seed/swap deduplication, so a rerun under a different profile can
never silently replace the original. Win rates and paired statistics are
computed per experiment; the top-level arms/paired blocks are only filled when
the directory holds exactly one experiment, otherwise they are refused and the
per-experiment results live under "experiments". Legacy lines missing the
newer fields form their own explicitly labelled experiment.

Served identity (2026-09-07): the served_* fields (what the endpoint said
answered) are part of the condition exactly as each game recorded them. An
unresolved value is never filled from another game, never paired with a
resolved game of the same seed and never deduplicated against one; two games
that each resolved only smart or only fast are two experiments, not one joint
model condition. A cited game whose llm seats name nothing is ineligible
(exit 3) on its own, regardless of its neighbours; baseline seats (floor,
random, ai) have no model to name and are reported not-applicable.

Usage:
  python3 eval/aggregate.py <dir> [--json] [--bootstrap-seed N] [--resamples N]
                                   [--experiment ID] [--sanity-arms]
"""

import argparse
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_RESAMPLES = 10_000
DEFAULT_BOOTSTRAP_SEED = 20260715  # fixed for reproducible CIs
DEFAULT_MAX_FALLBACK_RATE = 0.10
# The harness-decided share: decisions the harness made when the model could have (match_review's
# TAKEN and LOST, and any Forge-AI row), over the llm identities' decisions. Forced picks (one legal
# option) are not counted; the fallback rate above counts them, which hides this number.
DEFAULT_MAX_HARNESS_DECIDED_RATE = 0.02

# Default for fields the harness did not yet emit when the line was written.
# Kept DISTINCT from "unknown": absent means this run predates the field and
# cannot be judged; "unknown" means the run could have resolved the value and
# didn't. The identity gate fails the second and excuses the first, so adding
# the gate does not retroactively condemn every older run dir.
MISSING = "(pre-provenance)"

# Self-describing provenance fields appended to the game line (see spec B).
# (game-line key, display default when the field is absent on legacy lines)
PROVENANCE_FIELDS = (
    ("discipline", "unspecified"),
    ("engine_sha", "unknown"),
    ("overlay_sha", "unknown"),
    ("deck_sha", "unknown"),
    ("mulligan_heuristic", "unknown"),
    ("skip_heuristics", "unknown"),
    ("plan_a", "unknown"),
    ("plan_b", "unknown"),
    ("smart_budget", "unknown"),
    ("fast_budget", "unknown"),
    ("claude_deadline_ms", "unknown"),
    ("game_cap_ms", "unknown"),
    # Resolved model identity (added 2026-07-26). The configured spec is not an
    # identity: SMART_MODEL_A="openrouter:default_model" against a local server
    # names no checkpoint, which is why stage1-student-eval and
    # stage2-student-eval are indistinguishable in provenance despite being
    # different adapters. served_* is what the endpoint said actually answered.
    ("served_smart_a", MISSING),
    ("served_smart_b", MISSING),
    ("served_fast_a", MISSING),
    ("served_fast_b", MISSING),
    ("openrouter_base_url", MISSING),
    # The precisions an openrouter: seat may be served at (OPENROUTER_QUANTIZATIONS): what the model ran at, so a
    # condition. Default "": a game from before the filter was unfiltered, as is one that stamps it empty.
    ("openrouter_quantizations", ""),
    # A price ceiling (OPENROUTER_MAX_PRICE) is a condition too: at list an open-weight model may find only a 4-bit
    # host (standards review of #327). Default "" as above.
    ("openrouter_max_price", ""),
    ("checkpoint_sha", MISSING),
    ("model_cfg_sha", MISSING),
    ("temperature_a", MISSING),
    ("temperature_b", MISSING),
    ("seed_bank", MISSING),
    ("context_profile", MISSING),
    ("context_sha", MISSING),
    # The faithful observation's version (SeatObservation.VERSION): what the seat reads is a condition.
    ("observation_version", MISSING),
    ("memory_tools", MISSING),
    ("memory_tools_version", MISSING),
    # Reasoning effort is part of the player's identity, like temperature.
    ("claude_effort_smart", MISSING),
    ("claude_effort_fast", MISSING),
    # What the seat can do beyond answering: the tool protocol (native vs text) and each opt-in
    # tool are conditions and never pool (CLAUDE.md). native_tools_downgraded is an observation
    # (a provider refused function calling mid-game): such a game is not a clean native-tools
    # game and stays apart, as served_* do.
    ("native_tools", MISSING),
    ("native_tools_seats", MISSING),
    ("native_tools_version", MISSING),
    ("native_tools_downgraded", MISSING),
    # How a decision is answered: a call to `answer` (AnswerTool) or a text tag line. Tool and text
    # answers are different conditions and never pool (CLAUDE.md, owner decision 2026-10-15).
    ("answer_tool", MISSING),
    ("answer_tool_seats", MISSING),
    ("answer_tool_version", MISSING),
    ("rules_tool", MISSING),
    ("rules_tool_version", MISSING),
    ("rules_effective", MISSING),
    ("lookahead", MISSING),
    ("lookahead_version", MISSING),
    # One bridge for every decision (ONE_TIER) vs the smart/fast split: how hard the model may
    # think per decision type is a condition.
    ("one_tier", MISSING),
    # PRINCIPLES and the static reference left out of the system prompt (NO_COACHING): an
    # assistance condition (docs/STANDARDS.md section 8).
    ("no_coaching", MISSING),
    # One conversation per seat (ONE_CHAT) vs a stateless ask per decision.
    ("one_chat", MISSING),
    ("one_chat_version", MISSING),
    # The conversation bounded by the game turn (TURN_CHAT).
    ("turn_chat", MISSING),
    ("turn_chat_version", MISSING),
    # The questions a jev: decision-model seat is asked (DecisionsBridge), per identity; stamped only for a
    # jev: seat, so a line without it had no such seat, before the stamp or after.
    ("decisions_bridge_version_a", "none"),
    ("decisions_bridge_version_b", "none"),
    # The Claude CLI answer bridge (ClaudeCliBridge) a claude-cli: seat plays through, per identity; stamped only for
    # such a seat.
    ("claude_cli_bridge_version_a", "none"),
    ("claude_cli_bridge_version_b", "none"),
    # Its thinking budget (CLAUDE_CLI_THINKING_TOKENS, "cli-default" when unset), per identity, only for such a seat.
    ("claude_cli_thinking_tokens_a", "none"),
    ("claude_cli_thinking_tokens_b", "none"),
    # A game with a development-lane seat (claude-cli:, owner 2026-10-24): never pooled with a benchmark game.
    ("dev_lane", "none"),
    # The interface the model plays through, as one number (docs/STANDARDS.md section 8).
    ("harness_epoch", MISSING),
    # Prompt caching (Anthropic models): changes what a run costs, not what the model answers.
    ("prompt_cache", MISSING),
    # The format the decks are built for (GAME_FORMAT). A line without it was Legacy: the only format then.
    ("game_format", "legacy"),
    # Two opt-in switches that change how the seat plays (issue #117): PLAN_EXECUTE makes the harness
    # play steps of the model's own stated turn plan, TIER_FAST_TYPES moves decision types off the
    # reasoning tier. Neither was stamped, keyed or pinned, so games played with them on and off pooled
    # into one experiment. A legacy line that carries neither reads MISSING, as a distinct condition.
    ("plan_execute", MISSING),
    ("tier_fast_types", MISSING),
    # The hidden-menu arm (PROPOSE_ACTIONS, harness-epoch-74): every line before it was a menu game.
    ("propose_actions", False),
    ("propose_audit", False),
    # PROPOSE_TERSE (harness-epoch-78): a rules refusal said only "ILLEGAL", not the rule; every line before it did.
    ("propose_terse", False),
    # SHADOW_AI asks Forge's AI on the side for a label (a DAgger corpus). Its targets are reset before the seat's cast
    # and the RNG restored, but an AI timeout weakens that isolation ([SHADOW] ... WEAKENED): a condition (#345).
    ("shadow_ai", False),
)

# Identity fields a cited run must resolve. A value in this set means the field
# is present but names nothing — an alias the endpoint never disambiguated.
UNATTRIBUTABLE_VALUES = ("unknown", "", "unregistered")

# ---------------------------------------------------------- experiment key
#
# An EXPERIMENT is the set of games that were played under one recorded
# condition. Its key is every behaviorally relevant field the game line
# carries: the arm configuration (who played, with which model spec and deck
# identity) plus every provenance field except the two that describe how the
# run was LABELLED rather than how it BEHAVED (discipline, seed_bank). Two
# games whose keys differ are different measurements and are never pooled
# into one win rate or one paired delta, whatever directory they share.
CONDITION_ARM_FIELDS = ("deck_a", "deck_b", "controller_a", "controller_b",
                        "smart_a", "smart_b", "fast_a", "fast_b")
# prompt_cache is recorded but does not split experiments: the provider returns the same output
# with or without its cache, so caching on and off are one measurement at two prices.
NON_BEHAVIORAL_PROVENANCE = ("discipline", "seed_bank", "prompt_cache", "openrouter_provider_sort")
# What the endpoint said answered, keyed by deck identity and tier. These are
# OBSERVATIONS and partition experiments exactly as recorded (2026-09-07): a
# game whose endpoint named nothing ("unknown") is a different measurement from
# one whose endpoint named a checkpoint, and two games that each named only
# half of a smart/fast pair are two measurements, not one joint condition.
# Nothing is ever filled in from another game. The absent-field state
# (MISSING, pre-provenance) stays distinct from the present-but-empty state
# ("unknown"); see served_identity_status() for the per-field classification.
SERVED_FIELDS = ("served_smart_a", "served_smart_b", "served_fast_a", "served_fast_b")
CONDITION_FIELDS = CONDITION_ARM_FIELDS + tuple(
    n for n, _ in PROVENANCE_FIELDS if n not in NON_BEHAVIORAL_PROVENANCE)

# Per-field evidential status of a served identity (served_identity_status).
SERVED_RESOLVED = "resolved"            # the endpoint named a model
SERVED_UNRESOLVED = "unresolved"        # field present, names nothing (UNATTRIBUTABLE_VALUES)
SERVED_MISSING = "missing"              # field absent: pre-provenance line
SERVED_NOT_APPLICABLE = "not-applicable"  # seat is not llm-controlled; no model to name


def _is_resolved(v):
    return v != MISSING and str(v).strip() not in UNATTRIBUTABLE_VALUES


def served_identity_status(condition):
    """Classify each served_* field of a condition without altering its value.

    Returns {field: {"value": recorded value, "status": SERVED_*}}. A seat whose
    controller is not "llm" has no model to name, so whatever the harness
    stamped there (the Java side writes "unknown" for every seat it never
    routed) is NOT-APPLICABLE rather than unresolved: a floor/random/ai
    baseline seat must never read as an attribution failure."""
    out = {}
    for f in SERVED_FIELDS:
        ident = f[-1]
        ctrl = condition.get("controller_%s" % ident)
        v = condition.get(f, MISSING)
        if ctrl != "llm":
            status = SERVED_NOT_APPLICABLE
        elif v == MISSING:
            status = SERVED_MISSING
        elif not _is_resolved(v):
            status = SERVED_UNRESOLVED
        else:
            status = SERVED_RESOLVED
        out[f] = {"value": v, "status": status}
    return out


def identity_status(condition):
    """One word for the whole condition: 'unresolved' if any applicable served
    field names nothing, else 'legacy' if any is absent, else 'resolved' when at
    least one llm seat exists, else 'not-applicable' (no model played)."""
    statuses = {x["status"] for x in served_identity_status(condition).values()}
    if SERVED_UNRESOLVED in statuses:
        return SERVED_UNRESOLVED
    if SERVED_MISSING in statuses:
        return "legacy"
    if SERVED_RESOLVED in statuses:
        return SERVED_RESOLVED
    return SERVED_NOT_APPLICABLE


def _canon(obj):
    """Stable serialization for grouping keys and ids."""
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def game_condition(game):
    """The recorded condition of one game line: every CONDITION_FIELDS entry,
    with the documented display default for fields the harness did not emit.
    Absent arm fields (a malformed or ancient line) read as MISSING."""
    cond = {}
    for f in CONDITION_ARM_FIELDS:
        v = game.get(f, MISSING)
        cond[f] = MISSING if v is None else v
    for name, default in PROVENANCE_FIELDS:
        if name in NON_BEHAVIORAL_PROVENANCE:
            continue
        v = game.get(name, default)
        cond[name] = default if v is None else v
    return cond


def experiment_id(condition):
    return hashlib.sha256(_canon(condition).encode("utf-8")).hexdigest()[:12]


def group_experiments(games):
    """Partition real game records into experiments by recorded condition.

    The partition key is the FULL recorded condition, served_* included, taken
    exactly as each game line observed it. Consequences that are the contract:
      - a game whose endpoint named nothing never joins a game whose endpoint
        named a model, however alike their configured specs are;
      - two games that each resolved only one of smart/fast do not combine
        into a joint condition that neither observed;
      - a same-seed rerun with a different served value is a second
        experiment, never a duplicate of (and never paired with) the first;
      - a pre-provenance line (MISSING) is distinct from a present-but-empty
        ("unknown") one.
    Every member therefore satisfies experiment_id(game_condition(g)) == id.

    Returns (experiments, crashed). Each experiment is a dict:
      id          sha256[:12] of the canonical condition (stable across dirs)
      condition   the full field -> value map, as recorded
      all_games   every record in file order, before dedupe
      games       after (seed, swap) dedupe within THIS experiment, latest kept
      duplicates  [{seed, swap, kept, dropped, conflicting, ...}]
      legacy_fields  condition fields that read MISSING (pre-provenance lines)
      served_identity  {served_field: {value, status}} (served_identity_status)
      identity_status  resolved | unresolved | legacy | not-applicable
    Crashed games (no game line, no seed) carry no condition and are returned
    separately; they are never merged into an experiment or deduped away.
    Sets game["_experiment"] = id on every grouped record."""
    crashed, groups, order = [], {}, []
    for g in games:
        if g.get("_no_game_line"):
            crashed.append(g)
            continue
        cond = game_condition(g)
        key = _canon(cond)
        if key not in groups:
            groups[key] = (cond, [])
            order.append(key)
        groups[key][1].append(g)
    return [_make_experiment(members, cond) for cond, members in
            (groups[k] for k in order)], crashed


def _make_experiment(members, cond):
    cond = dict(cond)
    eid = experiment_id(cond)
    all_games, kept, latest, duplicates = [], [], {}, []
    for g in members:
        g["_experiment"] = eid
        all_games.append(g)
        seed = g.get("seed")
        if seed is None:
            kept.append(g)   # unkeyed: keep, never merge
            continue
        key = (seed, bool(g.get("swap", False)))
        if key in latest:
            old = latest[key]
            same = (old.get("winner") == g.get("winner")
                    and old.get("turns") == g.get("turns"))
            duplicates.append({
                "seed": seed, "swap": key[1],
                "kept": g["_file"], "dropped": old["_file"],
                "kept_winner": g.get("winner"), "dropped_winner": old.get("winner"),
                "conflicting": old.get("winner") != g.get("winner"),
                "same_outcome": same,
            })
            kept.remove(old)
        latest[key] = g
        kept.append(g)
    return {
        "id": eid,
        "condition": cond,
        "all_games": all_games,
        "games": kept,
        "duplicates": duplicates,
        "legacy_fields": sorted(k for k, v in cond.items() if v == MISSING),
        "served_identity": served_identity_status(cond),
        "identity_status": identity_status(cond),
    }


def experiment_labels(experiments):
    """Human labels naming the fields that DIFFER between the experiments of
    one directory (dir-relative; the id is the stable identifier). Values are
    printed exactly as recorded: an experiment whose endpoint named nothing is
    labelled served_smart_a=unknown, never with a substitute."""
    if len(experiments) <= 1:
        return {e["id"]: "(single condition)" for e in experiments}
    differing = []
    for f in CONDITION_FIELDS:
        vals = {_canon(e["condition"][f]) for e in experiments}
        if len(vals) > 1:
            differing.append(f)
    labels = {}
    for e in experiments:
        labels[e["id"]] = " ".join("%s=%s" % (f, e["condition"][f]) for f in differing)
    return labels

# The ai tier only counts fallbacks INSIDE overridden methods; several decision
# points are un-overridden and always run on Forge AI invisibly. ai=0 therefore
# does NOT mean the model drove every decision.
AI_PARTIAL_CAVEAT = (
    "NOTE (ai tier is PARTIAL): the ai counter only sees fallbacks inside "
    "overridden methods. Combat damage order, X announcement, edict sacrifices, "
    "mana tapping and mulligan bottoming are un-overridden and always run on "
    "Forge AI invisibly, emitting no decision line. ai=0 does NOT mean the "
    "model drove every decision."
)


# ---------------------------------------------------------------- parsing

def parse_dir_full(path):
    """Parse all game-*.jsonl files into every row type the harness emits.

    Returns a dict:
      games        deduped game records (crashed synthetic games included)
      decisions    every decision row, in file order, with _file attached
      context_calls / context_tools   memory-tool rows (see PLAYER-HARNESS.md)
      rows_by_file {file: [decision|context_call|context_tool rows in order]}
      experiments  group_experiments() output (post-dedupe games per condition)
      crashed      synthetic invalid games for files with no game line
      warnings     human-readable anomalies

    Every game line becomes a game record (parallel same-millisecond launches
    can interleave two games into one file, so we don't assume one game per
    file). Games are grouped by recorded CONDITION first and only then deduped
    by (seed, swap) WITHIN a condition, keeping the last seen — files sort by
    trailing millis, so "last" is the most recent rerun. A rerun under a
    different profile/model/tool setting therefore never replaces the
    original; it becomes a second experiment. Files with no game line at all
    become synthetic invalid games (crashed before summarizing)."""
    games, decisions, calls, tools, warnings = [], [], [], [], []
    rows_by_file = {}
    files = sorted(Path(path).glob("game-*.jsonl"))
    if not files:
        warnings.append("no game-*.jsonl files found in %s" % path)
    for fp in files:
        found_game = False
        n_bad = 0
        rows = []
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        n_bad += 1
                        continue
                    if not isinstance(rec, dict):
                        n_bad += 1
                        continue
                    t = rec.get("t")
                    if t == "decision":
                        rec["_file"] = fp.name
                        decisions.append(rec)
                        rows.append(rec)
                    elif t == "game":
                        rec["_file"] = fp.name
                        games.append(rec)
                        found_game = True
                    elif t == "context_call":
                        rec["_file"] = fp.name
                        calls.append(rec)
                        rows.append(rec)
                    elif t == "context_tool":
                        rec["_file"] = fp.name
                        tools.append(rec)
                        rows.append(rec)
        except OSError as e:
            warnings.append("unreadable file %s: %s" % (fp.name, e))
            continue
        rows_by_file[fp.name] = rows
        if n_bad:
            warnings.append("%s: skipped %d malformed line(s)" % (fp.name, n_bad))
        if not found_game:
            # Crashed game: no summary line was ever written. Count as invalid.
            m = re.match(r"game-(-?\d+)-\d+\.jsonl$", fp.name)
            games.append({"t": "game",
                          "seed": int(m.group(1)) if m else None,
                          "winner": "invalid", "_no_game_line": True,
                          "_file": fp.name})
            warnings.append("%s: no game line -> counted as invalid" % fp.name)

    experiments, crashed = group_experiments(games)
    if len(experiments) > 1:
        labels = experiment_labels(experiments)
        warnings.append(
            "%d distinct experiment conditions in this directory; win rates are "
            "reported per experiment and NOT pooled: %s"
            % (len(experiments),
               "; ".join("%s (%d game lines) %s" % (e["id"], len(e["all_games"]),
                                                    labels[e["id"]])
                         for e in experiments)))
    for e in experiments:
        for d in e["duplicates"]:
            msg = ("duplicate game for seed=%s swap=%s in experiment %s: keeping %s, "
                   "dropping %s" % (d["seed"], d["swap"], e["id"], d["kept"], d["dropped"]))
            if d["conflicting"]:
                msg += (" — CONFLICTING outcome (%s vs %s); a retry artifact that "
                        "disagrees with the original is reported, not averaged"
                        % (d["dropped_winner"], d["kept_winner"]))
            elif not d["same_outcome"]:
                msg += " (same winner, different length)"
            else:
                msg += " (identical outcome: retry artifact)"
            warnings.append(msg)

    # Deduped list in original file order: crashed games plus every kept game.
    kept_ids = {id(g) for e in experiments for g in e["games"]}
    deduped = [g for g in games if g.get("_no_game_line") or id(g) in kept_ids]
    return {
        "games": deduped,
        "decisions": decisions,
        "context_calls": calls,
        "context_tools": tools,
        "rows_by_file": rows_by_file,
        "experiments": experiments,
        "crashed": crashed,
        "warnings": warnings,
    }


def parse_dir(path):
    """Compatibility wrapper: (games, decisions, warnings).

    games is deduped per EXPERIMENT (see parse_dir_full), so a caller that
    keys on (seed, swap) alone may now see two records for one combo when the
    directory mixes conditions; each carries `_experiment`."""
    p = parse_dir_full(path)
    return p["games"], p["decisions"], p["warnings"]


def arm_label(game, identity):
    """Stable label for a deck-identity's configuration ('a' or 'b').
    Identity-keyed: describes the raw CONTROLLER_x/SMART_x config, never a
    seat. Model names only matter for llm controllers."""
    ctrl = game.get("controller_%s" % identity) or "unknown"
    if ctrl == "llm":
        smart = game.get("smart_%s" % identity) or "?"
        fast = game.get("fast_%s" % identity)
        return "llm[%s]" % smart if not fast else "llm[%s/%s]" % (smart, fast)
    return ctrl


# ------------------------------------------------------------- statistics

def wilson_ci(wins, n, z=1.96):
    """Wilson score interval for a binomial proportion. Returns (lo, hi).
    n=0 gives the maximally uninformative (0, 1)."""
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def paired_bootstrap(pair_outcomes, resamples=DEFAULT_RESAMPLES, rng=None):
    """Bootstrap CI on the win-rate DELTA (focal arm minus opponent) for a
    paired-mirror design.

    pair_outcomes: list of (x1, x2) per seed — x1 = focal arm won the
    unswapped game, x2 = focal arm won the swapped game (both 0/1, both
    games decisive).

    The resampling unit is the SEED (the pair), not the individual game:
    the two games of a seed share deck shuffles and seat assignment, so
    they are correlated and must travel together in each resample —
    resampling single games would understate the variance the pairing
    removed. With two arms and draws excluded, opponent rate = 1 - p, so
    delta = 2p - 1.

    Returns (delta_point, lo, hi).
    """
    if not pair_outcomes:
        return (0.0, -1.0, 1.0)
    if rng is None:
        rng = random.Random(DEFAULT_BOOTSTRAP_SEED)
    k = len(pair_outcomes)
    point_p = sum(x1 + x2 for x1, x2 in pair_outcomes) / (2.0 * k)
    deltas = []
    for _ in range(resamples):
        wins = 0
        for _ in range(k):
            x1, x2 = pair_outcomes[rng.randrange(k)]
            wins += x1 + x2
        deltas.append(2.0 * (wins / (2.0 * k)) - 1.0)
    deltas.sort()
    lo = deltas[int(0.025 * resamples)]
    hi = deltas[min(resamples - 1, int(0.975 * resamples))]
    return (2.0 * point_p - 1.0, lo, hi)


def p90(values):
    """Nearest-rank 90th percentile."""
    if not values:
        return None
    vs = sorted(values)
    return vs[max(0, math.ceil(0.9 * len(vs)) - 1)]


# ------------------------------------------------------------- summaries

def summarize_games(games):
    counts = Counter()
    for g in games:
        w = g.get("winner")
        if w in ("A", "B"):
            counts["decisive"] += 1
            # Seat-bias diagnostic. winner is identity-keyed, so seat 1
            # (first player) won iff identity A won an unswapped game or
            # identity B won a swapped game (swap:true = B sat in seat 1).
            swap = bool(g.get("swap", False))
            if (w == "A") != swap:
                counts["seat1_wins"] += 1
        elif w == "draw":
            counts["draw"] += 1
        else:
            counts["invalid"] += 1  # includes crashed / missing winner
            if g.get("_no_game_line"):
                counts["no_game_line"] += 1
    counts["total"] = len(games)
    return dict(counts)


def summarize_arms(games):
    """Unpaired per-arm win rates over decisive games only.

    Identity-keyed: winner "A" is identity A's win and controller_a/smart_a
    are identity A's config, so attribution never flips on swap."""
    arms = defaultdict(lambda: {"wins": 0, "games": 0, "started": 0, "draws": 0})
    for g in games:
        w = g.get("winner")
        la, lb = arm_label(g, "a"), arm_label(g, "b")
        # Every started game, for the strict score: a game that ended in a draw or never finished (the
        # wall-clock cap, a crash) is not dropped there, so stalling a lost game into the cap cannot
        # erase the loss.
        arms[la]["started"] += 1
        arms[lb]["started"] += 1
        if w == "draw":
            arms[la]["draws"] += 1
            arms[lb]["draws"] += 1
        if w not in ("A", "B"):
            continue
        arms[la]["games"] += 1
        arms[lb]["games"] += 1
        arms[la if w == "A" else lb]["wins"] += 1
    out = {}
    for label, d in sorted(arms.items()):
        if not d["games"]:
            continue                        # an arm with no decisive game is not listed (as before)
        lo, hi = wilson_ci(d["wins"], d["games"])
        out[label] = {"wins": d["wins"], "games": d["games"],
                      "win_rate": d["wins"] / d["games"] if d["games"] else None,
                      "wilson95": [lo, hi],
                      "started": d["started"], "draws": d["draws"],
                      # Strict score: wins + half the draws over every started game (an unfinished
                      # game scores 0). Equal to win_rate when every game finished decisively.
                      "strict_score": (d["wins"] + 0.5 * d["draws"]) / d["started"] if d["started"] else None}
    return out


def summarize_paired(games, resamples, bootstrap_seed):
    """Pair games by seed (swap=False vs swap=True). Focal arm = identity A.

    Identity-keyed contract: winner "A" means identity A won in BOTH swap
    variants (the Java side maps by player name), so no flip on swap —
    swap only tells us which seat identity A occupied, which the paired
    delta deliberately averages out."""
    by_seed = defaultdict(dict)
    dup = 0
    for g in games:
        seed = g.get("seed")
        if seed is None:
            continue
        key = bool(g.get("swap", False))
        if key in by_seed[seed]:
            dup += 1  # rerun of same combo: keep the later file (sorted order)
        by_seed[seed][key] = g

    focal = None
    outcomes = []   # (x1, x2) per usable seed
    tallies = Counter()
    n_pairs = n_excluded_nondecisive = n_excluded_mismatch = 0
    for seed in sorted(by_seed):
        pair = by_seed[seed]
        if False not in pair or True not in pair:
            continue
        n_pairs += 1
        g1, g2 = pair[False], pair[True]
        if focal is None:
            focal = arm_label(g1, "a")
        # Identity A's config must be the same in both variants (the fields
        # are raw config, never post-swap). Drop pairs from a different mix.
        if arm_label(g1, "a") != focal or arm_label(g2, "a") != focal:
            n_excluded_mismatch += 1
            continue
        if g1.get("winner") not in ("A", "B") or g2.get("winner") not in ("A", "B"):
            n_excluded_nondecisive += 1
            continue
        # winner is identity-keyed: "A" = focal won, in either swap variant.
        x1 = 1 if g1["winner"] == "A" else 0
        x2 = 1 if g2["winner"] == "A" else 0
        outcomes.append((x1, x2))
        tallies["WW" if x1 + x2 == 2 else "LL" if x1 + x2 == 0 else "split"] += 1

    if not outcomes:
        return {"n_pairs_complete": n_pairs, "n_pairs_used": 0,
                "note": "no seed has both swap variants decisive"}

    rng = random.Random(bootstrap_seed)
    delta, lo, hi = paired_bootstrap(outcomes, resamples, rng)
    k = len(outcomes)
    focal_wins = sum(x1 + x2 for x1, x2 in outcomes)
    # Opponent label: identity B's config in any game whose identity A is focal.
    opponent = None
    for seed in sorted(by_seed):
        g = by_seed[seed].get(False)
        if g is not None and arm_label(g, "a") == focal:
            opponent = arm_label(g, "b")
            break
    result = {
        "focal_arm": focal,
        "opponent_arm": opponent,
        "n_pairs_complete": n_pairs,
        "n_pairs_used": k,
        "n_pairs_excluded_nondecisive": n_excluded_nondecisive,
        "n_pairs_excluded_config_mismatch": n_excluded_mismatch,
        "duplicate_combos": dup,
        "per_seed": {"focal_swept": tallies["WW"], "split": tallies["split"],
                     "focal_lost_both": tallies["LL"]},
        "focal_wins": focal_wins,
        "focal_win_rate": focal_wins / (2.0 * k),
        "delta": delta,
        "delta_ci95": [lo, hi],
        "resamples": resamples,
        "bootstrap_seed": bootstrap_seed,
    }
    return result


def summarize_decisions(decisions):
    tiers = defaultdict(lambda: {"n": 0, "latencies": [], "fallbacks": 0})
    types = Counter()
    for d in decisions:
        tier = d.get("tier") or "unknown"
        t = tiers[tier]
        t["n"] += 1
        lat = d.get("latency_ms")
        if isinstance(lat, (int, float)):
            t["latencies"].append(lat)
        if d.get("fallback"):
            t["fallbacks"] += 1
        types[d.get("type") or "unknown"] += 1
    tier_out = {}
    for tier, t in sorted(tiers.items()):
        lats = t["latencies"]
        tier_out[tier] = {
            "calls": t["n"],
            "mean_latency_ms": sum(lats) / len(lats) if lats else None,
            "p90_latency_ms": p90(lats),
            "fallback_rate": t["fallbacks"] / t["n"] if t["n"] else None,
            "fallbacks": t["fallbacks"],
        }
    return {"total": len(decisions), "by_tier": tier_out,
            "by_type": dict(types.most_common())}


def summarize_provenance(games):
    """Collect the self-describing provenance fields across real game lines.

    A field with more than one distinct value across the directory means the
    batch mixes runs that are not comparable (different engine, decklist,
    discipline, budgets, ...): we flag that as MIXED-PROVENANCE. Legacy game
    lines without the fields fall back to the documented default, so an old
    dir reports 'unknown' rather than crashing."""
    real = [g for g in games if not g.get("_no_game_line")]
    fields = {}
    mixed_fields = []
    for name, default in PROVENANCE_FIELDS:
        distinct, seen = [], set()
        for g in real:
            v = g.get(name, default)
            if v is None:
                v = default
            key = repr(v)
            if key not in seen:
                seen.add(key)
                distinct.append(v)
        fields[name] = distinct
        if len(distinct) > 1:
            mixed_fields.append(name)
    return {
        "n_games": len(real),
        "mixed": bool(mixed_fields),
        "mixed_fields": mixed_fields,
        "fields": fields,
    }


def summarize_model_identity(games, prov):
    """Can this directory's numbers be attributed to a specific model?

    Three states, and the difference between the last two is the whole point:

      legacy         the identity fields are absent -> the run predates them.
                     Unattributable, but not a harness failure: nothing was
                     asked of it. Reported, never gated.
      unattributable the fields are present and still name nothing ("unknown"),
                     e.g. an alias spec against a server that reports no model.
                     A cited run in this state FAILS -- it is exactly the
                     stage1-vs-stage2 situation, where two different adapters
                     produced two artifacts no one can tell apart afterwards.
      attributable   every llm identity resolves to a served model name.

    Only llm-controlled identities are checked: a floor/random/ai seat has no
    model to name, so demanding one would fail every baseline arm."""
    real = [g for g in games if not g.get("_no_game_line")]
    f = prov["fields"]

    def one(field):
        vals = f.get(field, [])
        return vals[0] if len(vals) == 1 else None

    llm_identities = sorted({ident for g in real for ident in ("a", "b")
                             if g.get("controller_%s" % ident) == "llm"})

    # Configured specs live on the game line but are not provenance fields (they
    # are part of the arm label), so read them off the records directly.
    def distinct(field):
        out, seen = [], set()
        for g in real:
            v = g.get(field, MISSING)
            if repr(v) not in seen:
                seen.add(repr(v))
                out.append(v)
        return out

    configured = {"%s_%s" % (tier, ident): distinct("%s_%s" % (tier, ident))
                  for ident in llm_identities for tier in ("smart", "fast")}

    # Judged per game and per seat: a field counts as unresolved or legacy only
    # in a game where THAT seat was llm-controlled. A floor/random/ai seat's
    # "unknown" is not-applicable and never names the directory unresolved.
    unresolved, legacy_fields = [], []
    for g in real:
        for field, x in served_identity_status(game_condition(g)).items():
            if x["status"] == SERVED_MISSING:
                legacy_fields.append(field)
            elif x["status"] == SERVED_UNRESOLVED:
                unresolved.append(field)

    ckpt = f.get("checkpoint_sha", [])
    ckpt_legacy = any(v == MISSING for v in ckpt)

    legacy = bool(legacy_fields) or (not llm_identities and ckpt_legacy)
    # A run with no llm arm has nothing to attribute -> vacuous, like the
    # fallback gate. Never fail it.
    vacuous = not llm_identities
    attributable = bool(llm_identities) and not unresolved and not legacy_fields
    discipline = one("discipline")
    disciplines = f.get("discipline", [])

    # The gate is decided per GAME, not per directory: a game stamped cited
    # whose own llm seats name nothing is ineligible, whatever the games next
    # to it are stamped, and whatever their endpoints happened to name. A
    # pre-provenance game (fields absent) is excused because nothing was asked
    # of it; a game that HAS the fields and left them empty is not.
    unresolved_cited = []
    for g in real:
        if g.get("discipline") != "cited":
            continue
        status = served_identity_status(game_condition(g))
        if any(x["status"] == SERVED_UNRESOLVED for x in status.values()):
            unresolved_cited.append(g.get("_file"))

    return {
        "llm_identities": llm_identities,
        "vacuous": vacuous,
        "legacy": legacy,
        "legacy_fields": sorted(set(legacy_fields)),
        "unresolved_fields": sorted(set(unresolved)),
        "attributable": attributable,
        "discipline": discipline,
        "disciplines": disciplines,
        "unresolved_cited_games": len(unresolved_cited),
        "configured": configured,
        "served": {k: f.get(k, []) for k in
                   ("served_smart_a", "served_smart_b", "served_fast_a", "served_fast_b")},
        "checkpoint_sha": ckpt,
        "base_url": f.get("openrouter_base_url", []),
        "model_cfg_sha": f.get("model_cfg_sha", []),
        "temperature": {k: f.get(k, []) for k in ("temperature_a", "temperature_b")},
        "seed_bank": f.get("seed_bank", []),
        # Cited numbers must be attributable; exploratory ones only warn. A game
        # that predates the fields is excused either way -- see MISSING.
        "failed": bool(unresolved_cited),
    }


def summarize_fallback_gate(games, decisions, max_rate, max_harness_rate=DEFAULT_MAX_HARNESS_DECIDED_RATE):
    """Fallback gate over decisions of LLM-controlled identities only.

    Decisions are attributed by seat: a decision's `seat` is the player/deck
    NAME, which we map to an identity via the game line's deck_a/deck_b names
    and then to controller_a/controller_b. Only decisions whose identity has
    controller == 'llm' count. A decision is a fallback if its tier is auto or
    ai, or if it is smart/fast with a non-null fallback reason.

    rate = fallbacks / all-decisions-of-llm-identities. If any llm arm exists
    and the rate exceeds the ceiling, the gate FAILS (caller exits 2). With no
    llm arm the gate is vacuous (never fails)."""
    # A name that is llm in any game is treated as llm (identity-keyed config
    # is stable within a batch; mixed config is surfaced by provenance).
    llm_names = set()
    any_llm_arm = False
    for g in games:
        for ident in ("a", "b"):
            ctrl = g.get("controller_%s" % ident)
            name = g.get("deck_%s" % ident)
            if ctrl == "llm":
                any_llm_arm = True
                if name is not None:
                    llm_names.add(name)

    denom = numer = harness = 0
    for d in decisions:
        seat = d.get("seat")
        if seat not in llm_names:
            continue
        denom += 1
        tier = d.get("tier")
        if tier in ("auto", "ai"):
            numer += 1
        elif tier in ("smart", "fast") and d.get("fallback") is not None:
            numer += 1
        if harness_decided(d):
            harness += 1
    rate = (numer / denom) if denom else None
    failed = bool(any_llm_arm and rate is not None and rate > max_rate)
    hrate = (harness / denom) if denom else None
    hfailed = bool(any_llm_arm and hrate is not None and hrate > max_harness_rate)
    return {
        "max_fallback_rate": max_rate,
        "any_llm_arm": any_llm_arm,
        "vacuous": not any_llm_arm,
        "llm_identities": sorted(llm_names),
        "n_llm_decisions": denom,
        "n_fallback": numer,
        "fallback_rate": rate,
        "failed": failed,
        "max_harness_decided_rate": max_harness_rate,
        "n_harness_decided": harness,
        "harness_decided_rate": hrate,
        "harness_failed": hfailed,
    }


def harness_decided(d):
    """True when the harness, not the model, made a choice the model could have made."""
    import match_review, proposal_audit     # lazy: aggregate stays importable on its own
    tier = d.get("tier")
    if tier == "ai":
        return True
    if tier == "auto":
        return match_review.classify_auto(d) in ("TAKEN", "LOST")
    # A propose decision whose four tries were all refused is the model's error (proposal_audit.lost_to_refusals).
    return (tier in ("smart", "fast") and bool(match_review.LOST.search(str(d.get("fallback") or "")))
            and not proposal_audit.lost_to_refusals(d))


# ------------------------------------------------------- tool accounting
#
# Memory-tool games (MEMORY_TOOLS=1) emit, per model round, a `context_call`
# row {seat, turn, call, tier, elapsed_ms, prompt_tokens, completion_tokens,
# prompt, raw_reply} and, per tool request, a `context_tool` row {seat, turn,
# call, request, result}. These rows are SUPPLEMENTARY to decision rows: the
# decision row's prompt_tokens/completion_tokens are the LAST attempt's, i.e.
# the same numbers as the final context_call row of that decision, so summing
# both double counts. Token fields describe the last transport attempt only;
# failed bridge attempts (decision-row `attempts` beyond the recorded rounds)
# have no token record at all. Everything here is therefore "reported", never
# "billed".

TOOL_DENIED_PREFIX = "ERROR: tool budget exhausted"
TOOL_ERROR_PREFIX = "ERROR:"
TOOL_ACCOUNTING_CAVEAT = (
    "Token counts are what the backend reported for the LAST transport attempt "
    "of each round; retried attempts, truncation retries and rounds that report "
    "no usage are not costed. Decision-row tokens overlap the final context_call "
    "row of the same decision and are counted only for decisions with no "
    "context_call rows. These are reported counts, not billing.")


def _tok(v):
    """A usable token count, or None. The Java side writes -1 into context_call
    rows and null into decision rows when the backend reported nothing."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def classify_tool_result(result):
    if not isinstance(result, str):
        return "unknown"
    if result.startswith(TOOL_DENIED_PREFIX):
        return "denied"
    if result.startswith(TOOL_ERROR_PREFIX):
        return "error"
    return "ok"


def tool_command(request):
    """'MEMORY SEARCH 0 x' -> 'SEARCH'; anything else -> 'other'."""
    if not isinstance(request, str):
        return "other"
    parts = request.strip().split(None, 2)
    if len(parts) >= 2 and parts[0] == "MEMORY" and parts[1] in ("SEARCH", "READ", "NOTE"):
        return parts[1]
    return "other"


def attach_context_calls(rows):
    """Attribute context_call rows to the decision row that follows them for
    the same seat, in file order (the Java side writes every round's row
    before the decision row). Returns (attached, unattached) where attached is
    {id(decision): [call rows]} and unattached lists rounds that no decision
    row ever claimed (crash before the decision was recorded)."""
    pending = defaultdict(list)
    attached = {}
    for r in rows:
        t = r.get("t")
        seat = r.get("seat")
        if t == "context_call":
            pending[seat].append(r)
        elif t == "decision":
            if pending[seat]:
                attached[id(r)] = pending.pop(seat)
    unattached = [r for lst in pending.values() for r in lst]
    return attached, unattached


def _seat_identity(games):
    """seat name -> (identity letter, controller) from the game lines."""
    out = {}
    for g in games:
        if g.get("_no_game_line"):
            continue
        for ident in ("a", "b"):
            name = g.get("deck_%s" % ident)
            if name is None:
                continue
            ctrl = g.get("controller_%s" % ident) or "unknown"
            prev = out.get(name)
            if prev and prev != (ident, ctrl):
                out[name] = (prev[0] if prev[0] == ident else "mixed",
                             prev[1] if prev[1] == ctrl else "mixed")
            else:
                out[name] = (ident, ctrl)
    return out


def summarize_tool_accounting(games, rows_by_file):
    """Per-seat model-round, tool and token accounting over the files of the
    given games (see the module comment above for what is and is not costed)."""
    files = []
    seen = set()
    for g in games:
        fn = g.get("_file")
        if fn is not None and fn not in seen and fn in rows_by_file:
            seen.add(fn)
            files.append(fn)
    n_games_per_file = Counter(g.get("_file") for g in games if not g.get("_no_game_line"))
    ident = _seat_identity(games)
    memory_games = sum(1 for g in games if g.get("memory_tools") is True)
    versions = set()

    seats = {}

    def seat_entry(seat):
        if seat not in seats:
            seats[seat] = {
                "files": set(),
                "rounds": 0, "rounds_by_tier": Counter(), "rounds_without_reply": 0,
                "rounds_unattached": 0,
                "elapsed": [],
                "tool_requests": 0, "tool_ok": 0, "tool_errors": 0, "tool_denied": 0,
                "tool_unknown": 0, "tool_by_command": Counter(),
                "tools_per_file": Counter(),
                "ctx_prompt": 0, "ctx_completion": 0, "ctx_known": 0, "ctx_unknown": 0,
                "dec_total": 0, "dec_with_model": 0, "dec_prompt": 0, "dec_completion": 0,
                "dec_known": 0, "dec_unknown": 0, "dec_overlap_skipped": 0,
                "attempts_sum": 0, "attempts_unrecorded": 0, "dec_with_retries": 0,
                "dec_legacy_no_attempts": 0,
            }
        return seats[seat]

    ambiguous_files = 0
    for fn in files:
        rows = rows_by_file.get(fn, [])
        if n_games_per_file.get(fn, 0) > 1:
            ambiguous_files += 1
        attached, unattached = attach_context_calls(rows)
        for r in unattached:
            seat_entry(r.get("seat"))["rounds_unattached"] += 1
        for r in rows:
            t = r.get("t")
            seat = r.get("seat")
            s = seat_entry(seat)
            s["files"].add(fn)
            if r.get("version") is not None:
                versions.add(str(r["version"]))
            if t == "context_call":
                s["rounds"] += 1
                s["rounds_by_tier"][r.get("tier") or "unknown"] += 1
                if r.get("raw_reply") is None:
                    s["rounds_without_reply"] += 1
                el = r.get("elapsed_ms")
                if isinstance(el, (int, float)) and not isinstance(el, bool):
                    s["elapsed"].append(el)
                pt, ct = _tok(r.get("prompt_tokens")), _tok(r.get("completion_tokens"))
                if pt is None or ct is None:
                    s["ctx_unknown"] += 1
                else:
                    s["ctx_known"] += 1
                    s["ctx_prompt"] += pt
                    s["ctx_completion"] += ct
            elif t == "context_tool":
                s["tool_requests"] += 1
                s["tools_per_file"][fn] += 1
                s["tool_by_command"][tool_command(r.get("request"))] += 1
                kind = classify_tool_result(r.get("result"))
                if kind == "ok":
                    s["tool_ok"] += 1
                elif kind == "denied":
                    s["tool_denied"] += 1
                elif kind == "error":
                    s["tool_errors"] += 1
                else:
                    s["tool_unknown"] += 1
            elif t == "decision":
                s["dec_total"] += 1
                calls = attached.get(id(r), [])
                tier = r.get("tier")
                attempts = r.get("attempts")
                has_attempts = isinstance(attempts, int) and not isinstance(attempts, bool)
                used_model = bool(calls) or tier in ("smart", "fast") or (has_attempts and attempts >= 1)
                if not used_model:
                    continue
                s["dec_with_model"] += 1
                if has_attempts:
                    s["attempts_sum"] += attempts
                    recorded = len(calls) if calls else 1
                    extra = attempts - recorded
                    if extra > 0:
                        s["attempts_unrecorded"] += extra
                        s["dec_with_retries"] += 1
                else:
                    s["dec_legacy_no_attempts"] += 1
                if calls:
                    # Same numbers as the final context_call row: never add twice.
                    s["dec_overlap_skipped"] += 1
                    continue
                pt, ct = _tok(r.get("prompt_tokens")), _tok(r.get("completion_tokens"))
                if pt is None or ct is None:
                    s["dec_unknown"] += 1
                else:
                    s["dec_known"] += 1
                    s["dec_prompt"] += pt
                    s["dec_completion"] += ct

    out_seats = {}
    for seat, s in sorted(seats.items(), key=lambda kv: str(kv[0])):
        letter, ctrl = ident.get(seat, (None, "unknown"))
        is_model_seat = ctrl in ("llm", "unknown", "mixed")
        reasons = []
        if not is_model_seat:
            reasons.append("no model on this seat (%s)" % ctrl)
        else:
            if s["ctx_unknown"]:
                reasons.append("%d context round(s) report no token counts" % s["ctx_unknown"])
            if s["dec_unknown"]:
                reasons.append("%d decision row(s) with a model call report no token counts"
                               % s["dec_unknown"])
            if s["attempts_unrecorded"]:
                reasons.append("%d backend attempt(s) across %d decision(s) were retried "
                               "and have no token record" % (s["attempts_unrecorded"],
                                                             s["dec_with_retries"]))
            if s["rounds_without_reply"]:
                reasons.append("%d round(s) ended without a reply" % s["rounds_without_reply"])
            if s["rounds_unattached"]:
                reasons.append("%d context round(s) were never followed by a decision row"
                               % s["rounds_unattached"])
            if s["dec_legacy_no_attempts"]:
                reasons.append("%d decision row(s) predate the attempts field"
                               % s["dec_legacy_no_attempts"])
        n_rounds_costed = s["ctx_known"] + s["dec_known"]
        out_seats[seat] = {
            "identity": letter,
            "controller": ctrl,
            "games": len(s["files"]),
            "model_rounds": {"total": s["rounds"], "by_tier": dict(s["rounds_by_tier"]),
                             "without_reply": s["rounds_without_reply"],
                             "unattached": s["rounds_unattached"]},
            "elapsed_ms": {"sum": sum(s["elapsed"]),
                           "mean": (sum(s["elapsed"]) / len(s["elapsed"])) if s["elapsed"] else None,
                           "p90": p90(s["elapsed"]), "n": len(s["elapsed"])},
            "tools": {"requests": s["tool_requests"], "ok": s["tool_ok"],
                      "errors": s["tool_errors"], "denied": s["tool_denied"],
                      "unknown": s["tool_unknown"],
                      "by_command": dict(s["tool_by_command"]),
                      "max_per_game": max(s["tools_per_file"].values()) if s["tools_per_file"] else 0},
            "tokens": {
                "context_call": {"prompt": s["ctx_prompt"], "completion": s["ctx_completion"],
                                 "rounds_known": s["ctx_known"], "rounds_unknown": s["ctx_unknown"]},
                "decision_rows": {"prompt": s["dec_prompt"], "completion": s["dec_completion"],
                                  "rows_known": s["dec_known"], "rows_unknown": s["dec_unknown"],
                                  "rows_overlapping_skipped": s["dec_overlap_skipped"]},
                "reported_prompt": s["ctx_prompt"] + s["dec_prompt"],
                "reported_completion": s["ctx_completion"] + s["dec_completion"],
                "rounds_costed": n_rounds_costed,
                "rounds_uncosted": s["ctx_unknown"] + s["dec_unknown"] + s["attempts_unrecorded"],
            },
            "decision_rows": {"total": s["dec_total"], "with_model_call": s["dec_with_model"],
                              "attempts_sum": s["attempts_sum"],
                              "with_retries": s["dec_with_retries"],
                              "attempts_without_token_record": s["attempts_unrecorded"],
                              "legacy_without_attempts": s["dec_legacy_no_attempts"]},
            "billing": {"complete": is_model_seat and not reasons,
                        "applicable": is_model_seat,
                        "incomplete_reasons": reasons},
        }
    return {
        "n_files": len(files),
        "files_with_multiple_games": ambiguous_files,
        "memory_tools_games": memory_games,
        "n_context_calls": sum(1 for fn in files for r in rows_by_file.get(fn, [])
                               if r.get("t") == "context_call"),
        "n_context_tools": sum(1 for fn in files for r in rows_by_file.get(fn, [])
                               if r.get("t") == "context_tool"),
        "versions": sorted(versions),
        "seats": out_seats,
        "caveat": TOOL_ACCOUNTING_CAVEAT,
    }


# ------------------------------------------------------------ experiments

REFUSED_NOTE = ("pooled statistics refused: this directory holds more than one "
                "experiment condition; see 'experiments'")


def _experiment_decisions(exp, decisions):
    """Decision rows attributable to this experiment: those in files whose
    kept game(s) belong to it. Rows in files whose game was dropped as a
    duplicate, or that hold no game line, are not attributed."""
    files = {g["_file"] for g in exp["games"]}
    return [d for d in decisions if d.get("_file") in files]


def summarize_experiment(exp, label, parsed, resamples, bootstrap_seed, max_fallback_rate):
    games = exp["games"]
    decisions = _experiment_decisions(exp, parsed["decisions"])
    prov = summarize_provenance(games)
    return {
        "id": exp["id"],
        "label": label,
        "condition": exp["condition"],
        "legacy": bool(exp["legacy_fields"]),
        "legacy_fields": exp["legacy_fields"],
        "served_identity": exp["served_identity"],
        "identity_status": exp["identity_status"],
        "n_game_lines": len(exp["all_games"]),
        "n_games": len(games),
        "duplicates": exp["duplicates"],
        "n_duplicates_conflicting": sum(1 for d in exp["duplicates"] if d["conflicting"]),
        "games": summarize_games(games),
        "arms": summarize_arms(games),
        "paired": summarize_paired(games, resamples, bootstrap_seed),
        "decisions": summarize_decisions(decisions),
        "model_identity": summarize_model_identity(games, prov),
        "fallback_gate": summarize_fallback_gate(games, decisions, max_fallback_rate),
        "tool_accounting": summarize_tool_accounting(games, parsed["rows_by_file"]),
    }


def select_experiment(experiments, wanted):
    """Resolve an --experiment id (or unique prefix) or raise ValueError."""
    hits = [e for e in experiments if e["id"] == wanted or e["id"].startswith(wanted)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ValueError("no experiment matches %r; known: %s"
                         % (wanted, ", ".join(e["id"] for e in experiments) or "(none)"))
    raise ValueError("%r is ambiguous: %s" % (wanted, ", ".join(e["id"] for e in hits)))


def build_summary(path, resamples, bootstrap_seed,
                  max_fallback_rate=DEFAULT_MAX_FALLBACK_RATE, experiment=None):
    parsed = parse_dir_full(path)
    experiments = parsed["experiments"]
    warnings = list(parsed["warnings"])
    selected = None
    if experiment is not None:
        selected = select_experiment(experiments, experiment)
        experiments = [selected]
        warnings.append("--experiment %s: only that condition's games are summarized"
                        % selected["id"])
    labels = experiment_labels(parsed["experiments"])
    exp_summaries = [summarize_experiment(e, labels[e["id"]], parsed, resamples,
                                          bootstrap_seed, max_fallback_rate)
                     for e in experiments]

    crashed = parsed["crashed"]
    games = [g for e in experiments for g in e["games"]] + crashed
    decisions = parsed["decisions"]
    if selected is not None:
        decisions = _experiment_decisions(selected, decisions)
    prov = summarize_provenance(games)
    single = len(experiments) <= 1

    # The directory-level gate is the union of the per-experiment gates: one
    # cited experiment that cannot name its model makes the directory
    # ineligible even when a neighbouring experiment (or a legacy one) is fine.
    model_identity = summarize_model_identity(games, prov)
    failed_experiments = [s["id"] for s in exp_summaries if s["model_identity"]["failed"]]
    model_identity["failed_experiments"] = failed_experiments
    model_identity["failed"] = bool(model_identity["failed"] or failed_experiments)
    model_identity["experiments"] = {
        s["id"]: {"identity_status": s["identity_status"],
                  "served_identity": s["served_identity"],
                  "failed": s["model_identity"]["failed"]}
        for s in exp_summaries}

    games_summary = summarize_games(games)
    if single:
        arms = summarize_arms(games)
        paired = summarize_paired(games, resamples, bootstrap_seed)
    else:
        # Counts may pool (they are totals); rates and deltas may not.
        games_summary["seat1_wins"] = None
        games_summary["pooled_refused"] = True
        arms = {}
        paired = {"n_pairs_complete": sum(s["paired"].get("n_pairs_complete", 0)
                                          for s in exp_summaries),
                  "n_pairs_used": 0, "pooled_refused": True, "note": REFUSED_NOTE}

    return {
        "dir": str(path),
        "games": games_summary,
        "arms": arms,
        "paired": paired,
        # ai tier counts are PARTIAL — carry the caveat in machine output too.
        "decisions": dict(summarize_decisions(decisions),
                          ai_partial_caveat=AI_PARTIAL_CAVEAT),
        "provenance": prov,
        "provenance_mixed": prov["mixed"],
        "model_identity": model_identity,
        "fallback_gate": summarize_fallback_gate(games, decisions, max_fallback_rate),
        "experiments": {
            "n": len(experiments),
            "n_in_dir": len(parsed["experiments"]),
            "selected": selected["id"] if selected else None,
            "pooled": single,
            "condition_fields": list(CONDITION_FIELDS),
            "by_id": {s["id"]: s for s in exp_summaries},
            "order": [s["id"] for s in exp_summaries],
        },
        "tool_accounting": summarize_tool_accounting(games, parsed["rows_by_file"]),
        "warnings": warnings,
    }


# --------------------------------------------------------------- output

def fmt_pct(x):
    return "-" if x is None else "%.1f%%" % (100.0 * x)


def _prov_val(distinct):
    """Render one provenance field's distinct value(s) for humans."""
    if not distinct:
        return "unknown"
    if len(distinct) == 1:
        return str(distinct[0])
    return "MIXED {%s}" % ", ".join(str(v) for v in distinct)


def print_provenance(s):
    p = s["provenance"]
    if p["mixed"]:
        print("!!! MIXED-PROVENANCE WARNING !!! these games do NOT share the same "
              "run configuration and are NOT comparable as one batch.")
        print("    differing fields: %s" % ", ".join(p["mixed_fields"]))
    print("=== provenance (%d game line(s)) ===" % p["n_games"])
    f = p["fields"]
    print("discipline:  %s" % _prov_val(f.get("discipline", [])))
    print("engine_sha:  %s   overlay_sha: %s   deck_sha: %s"
          % (_prov_val(f.get("engine_sha", [])), _prov_val(f.get("overlay_sha", [])),
             _prov_val(f.get("deck_sha", []))))
    print("heuristics:  mulligan_heuristic=%s  skip_heuristics=%s"
          % (_prov_val(f.get("mulligan_heuristic", [])),
             _prov_val(f.get("skip_heuristics", []))))
    print("plans:       plan_a=%s  plan_b=%s"
          % (_prov_val(f.get("plan_a", [])), _prov_val(f.get("plan_b", []))))
    print("budgets:     smart_budget=%s  fast_budget=%s  claude_deadline_ms=%s"
          % (_prov_val(f.get("smart_budget", [])), _prov_val(f.get("fast_budget", [])),
             _prov_val(f.get("claude_deadline_ms", []))))
    print("caps:        game_cap_ms=%s" % _prov_val(f.get("game_cap_ms", [])))
    print("seed bank:   %s" % _prov_val(f.get("seed_bank", [])))


def print_model_identity(s):
    """Who actually played. Printed next to the win rate because a win rate
    whose player cannot be named is not a result about a model."""
    m = s["model_identity"]
    print("\n=== model identity ===")
    if m["vacuous"]:
        print("no llm arm in this dir -> nothing to attribute (gate vacuous).")
        return
    print("endpoint:    %s   model_cfg=%s"
          % (_prov_val(m["base_url"]), _prov_val(m["model_cfg_sha"])))
    print("checkpoint:  %s" % _prov_val(m["checkpoint_sha"]))
    for ident in m["llm_identities"]:
        print("identity %s:  configured smart=%s fast=%s"
              % (ident.upper(), _prov_val(m["configured"].get("smart_%s" % ident, [])),
                 _prov_val(m["configured"].get("fast_%s" % ident, []))))
        print("             served     smart=%s fast=%s   temperature=%s"
              % (_prov_val(m["served"].get("served_smart_%s" % ident, [])),
                 _prov_val(m["served"].get("served_fast_%s" % ident, [])),
                 _prov_val(m["temperature"].get("temperature_%s" % ident, []))))
    if m["legacy_fields"]:
        print("verdict: LEGACY — this dir predates the identity fields, so which model "
              "played it cannot be recovered from the artifact. Not gated (nothing was "
              "asked of the run), but do not cite it as a per-checkpoint result.")
    elif m["unresolved_fields"]:
        print("verdict: UNATTRIBUTABLE — the endpoint never named what answered (%s). "
              "The configured spec is an alias, so this artifact cannot distinguish one "
              "checkpoint from another." % ", ".join(m["unresolved_fields"]))
    else:
        print("verdict: attributable — every llm identity resolves to a served model.")
    if m.get("failed_experiments"):
        print("ineligible experiment(s): %s — cited games whose endpoint never named "
              "the model; kept apart from every resolved experiment, never merged."
              % ", ".join(m["failed_experiments"]))


def print_fallback_gate(s):
    fg = s["fallback_gate"]
    print("\n=== fallback gate (ceiling %s) ===" % fmt_pct(fg["max_fallback_rate"]))
    if fg["vacuous"]:
        print("no llm arm in this dir -> gate vacuous (nothing to enforce).")
        return
    print("llm identities: %s" % (", ".join(fg["llm_identities"]) or "(none matched)"))
    if fg["fallback_rate"] is None:
        print("no decisions attributed to llm identities -> gate passes.")
    else:
        print("fallbacks: %d/%d = %s of llm-identity decisions (auto+ai plus "
              "smart/fast with a fallback reason)"
              % (fg["n_fallback"], fg["n_llm_decisions"], fmt_pct(fg["fallback_rate"])))
        print("harness-decided: %d/%d = %s (TAKEN + LOST + Forge AI; forced picks excluded; ceiling %s)"
              % (fg["n_harness_decided"], fg["n_llm_decisions"], fmt_pct(fg["harness_decided_rate"]),
                 fmt_pct(fg["max_harness_decided_rate"])))
    print("gate: %s" % ("FAILED" if fg["failed"] or fg.get("harness_failed") else "passed"))


def print_games_block(g, indent=""):
    print("%stotal: %d   decisive: %d   draws: %d   invalid: %d (of which %d crashed with no game line)"
          % (indent, g.get("total", 0), g.get("decisive", 0), g.get("draw", 0),
             g.get("invalid", 0), g.get("no_game_line", 0)))
    if g.get("decisive") and g.get("seat1_wins") is not None:
        # Seat 1 = first player. Computed identity-aware: wins-by-A among
        # swap=false games plus wins-by-B among swap=true games. In a
        # seat-swapped mirror this should hover near 50% even when one arm
        # dominates; a large skew means seat order matters (play/draw
        # advantage) or SWAP_SEATS broke.
        print("%sseat-bias check: seat 1 (first player) win rate %d/%d decisive (%s)"
              % (indent, g.get("seat1_wins", 0), g["decisive"],
                 fmt_pct(g.get("seat1_wins", 0) / g["decisive"])))


def print_arms_and_paired(arms, p, indent=""):
    print("%s--- per-arm win rates (unpaired, decisive games only) ---" % indent)
    if not arms:
        print("%s(no decisive games)" % indent)
    for label, a in arms.items():
        lo, hi = a["wilson95"]
        print("%s%-24s %3d/%3d  %s  Wilson95 [%.1f%%, %.1f%%]"
              % (indent, label, a["wins"], a["games"], fmt_pct(a["win_rate"]),
                 100 * lo, 100 * hi))
        if a.get("started", a["games"]) != a["games"]:
            print("%s%-24s strict score %s over all %d started games (draws half, unfinished 0)"
                  % (indent, "", fmt_pct(a["strict_score"]), a["started"]))
    print("%s--- paired analysis (seed x seat-swap) ---" % indent)
    if p.get("n_pairs_used", 0) == 0:
        print("%s%s" % (indent, p.get("note", "no usable pairs")))
        if p.get("n_pairs_complete"):
            print("%s(complete pairs found: %d)" % (indent, p["n_pairs_complete"]))
    else:
        print("%sfocal arm: %s   vs   %s" % (indent, p["focal_arm"], p["opponent_arm"]))
        print("%spairs used: %d  (complete: %d, excluded non-decisive: %d, config-mismatch: %d)"
              % (indent, p["n_pairs_used"], p["n_pairs_complete"],
                 p["n_pairs_excluded_nondecisive"],
                 p["n_pairs_excluded_config_mismatch"]))
        ps = p["per_seed"]
        print("%sper-seed: focal swept both %d | split %d | lost both %d"
              % (indent, ps["focal_swept"], ps["split"], ps["focal_lost_both"]))
        print("%sfocal win rate: %d/%d = %s"
              % (indent, p["focal_wins"], 2 * p["n_pairs_used"], fmt_pct(p["focal_win_rate"])))
        print("%swin-rate delta (focal - opponent): %+.1f pts  "
              "paired-bootstrap 95%% CI [%+.1f, %+.1f]  (%d resamples, seed %d)"
              % (indent, 100 * p["delta"], 100 * p["delta_ci95"][0],
                 100 * p["delta_ci95"][1], p["resamples"], p["bootstrap_seed"]))


def print_experiments(s):
    e = s["experiments"]
    print("\n=== experiments (%d condition(s) in dir%s) ==="
          % (e["n_in_dir"], ", showing --experiment %s" % e["selected"] if e["selected"] else ""))
    if e["n"] > 1:
        print("!!! %d DISTINCT CONDITIONS !!! win rates below are per experiment; the "
              "pooled arms/paired blocks are refused." % e["n"])
    for eid in e["order"]:
        x = e["by_id"][eid]
        flags = []
        if x["legacy"]:
            flags.append("LEGACY: %s missing" % ", ".join(x["legacy_fields"]))
        if x["identity_status"] == SERVED_UNRESOLVED:
            flags.append("served identity UNRESOLVED: %s" % ", ".join(
                "%s=%r" % (f, v["value"]) for f, v in sorted(x["served_identity"].items())
                if v["status"] == SERVED_UNRESOLVED))
        if x["n_duplicates_conflicting"]:
            flags.append("%d CONFLICTING duplicate(s)" % x["n_duplicates_conflicting"])
        elif x["duplicates"]:
            flags.append("%d duplicate retry artifact(s) dropped" % len(x["duplicates"]))
        print("experiment %s  games=%d (lines=%d)  %s%s"
              % (eid, x["n_games"], x["n_game_lines"], x["label"],
                 ("  [" + "; ".join(flags) + "]") if flags else ""))
        if e["n"] > 1:
            print_games_block(x["games"], indent="    ")
            print_arms_and_paired(x["arms"], x["paired"], indent="    ")


def _fmt_ms(v):
    return "-" if v is None else ("%.1fs" % (v / 1000.0))


def print_tool_accounting(s):
    ta = s["tool_accounting"]
    print("\n=== tool accounting (context_call/context_tool rows) ===")
    print("files: %d   memory-tool games: %d   context rounds: %d   tool requests: %d   versions: %s"
          % (ta["n_files"], ta["memory_tools_games"], ta["n_context_calls"],
             ta["n_context_tools"], ", ".join(ta["versions"]) or "-"))
    if ta["files_with_multiple_games"]:
        print("NOTE: %d file(s) hold more than one game line; per-decision attribution "
              "there is order-based and may cross games." % ta["files_with_multiple_games"])
    for seat, x in ta["seats"].items():
        who = ("identity %s, %s" % (x["identity"].upper(), x["controller"])
               if x["identity"] else x["controller"])
        mr = x["model_rounds"]
        print("seat %-16s (%s) games=%d  model rounds=%d %s no-reply=%d unattached=%d"
              % (seat, who, x["games"], mr["total"],
                 "(" + ", ".join("%s %d" % kv for kv in sorted(mr["by_tier"].items())) + ")"
                 if mr["by_tier"] else "", mr["without_reply"], mr["unattached"]))
        t = x["tools"]
        if t["requests"]:
            print("    tools: requests=%d ok=%d errors=%d denied=%d  [%s]  max/game=%d"
                  % (t["requests"], t["ok"], t["errors"], t["denied"],
                     ", ".join("%s %d" % kv for kv in sorted(t["by_command"].items())),
                     t["max_per_game"]))
        el = x["elapsed_ms"]
        if el["n"]:
            print("    elapsed: sum=%s mean=%s p90=%s over %d round(s)"
                  % (_fmt_ms(el["sum"]), _fmt_ms(el["mean"]), _fmt_ms(el["p90"]), el["n"]))
        tk = x["tokens"]
        dr = x["decision_rows"]
        if x["billing"]["applicable"]:
            print("    tokens (reported, last attempt only): prompt=%d completion=%d over %d "
                  "round(s); %d round/attempt(s) uncosted"
                  % (tk["reported_prompt"], tk["reported_completion"], tk["rounds_costed"],
                     tk["rounds_uncosted"]))
            print("    decision rows: %d (%d with a model call, %d overlapping context rows "
                  "skipped); attempts=%d, %d without token record"
                  % (dr["total"], dr["with_model_call"],
                     tk["decision_rows"]["rows_overlapping_skipped"], dr["attempts_sum"],
                     dr["attempts_without_token_record"]))
        b = x["billing"]
        print("    accounting: %s%s"
              % ("complete" if b["complete"] else ("n/a" if not b["applicable"] else "INCOMPLETE"),
                 (" — " + "; ".join(b["incomplete_reasons"])) if b["incomplete_reasons"] else ""))
    print(TOOL_ACCOUNTING_CAVEAT)


def print_human(s):
    print_provenance(s)
    print_experiments(s)
    print()
    g = s["games"]
    print("=== games (%s) ===" % s["dir"])
    print_games_block(g)
    print("draws and invalids are EXCLUDED from all win-rate denominators.")
    if s["experiments"]["n"] > 1:
        print("\n=== per-arm win rates / paired analysis ===")
        print(REFUSED_NOTE)
    else:
        print()
        print_arms_and_paired(s["arms"], s["paired"])

    d = s["decisions"]
    print("\n=== decisions (%d total) ===" % d["total"])
    for tier, t in d["by_tier"].items():
        mean = "-" if t["mean_latency_ms"] is None else "%.0fms" % t["mean_latency_ms"]
        pp = "-" if t["p90_latency_ms"] is None else "%.0fms" % t["p90_latency_ms"]
        print("tier %-6s calls=%5d  mean=%7s  p90=%7s  fallbacks=%d (%s)"
              % (tier, t["calls"], mean, pp, t["fallbacks"],
                 fmt_pct(t["fallback_rate"])))
    if d["by_type"]:
        print("by type: " + "  ".join("%s=%d" % (k, v)
                                      for k, v in d["by_type"].items()))
    print(AI_PARTIAL_CAVEAT)

    print_model_identity(s)
    print_fallback_gate(s)
    print_tool_accounting(s)

    if s["warnings"]:
        print("\n=== warnings ===")
        for w in s["warnings"]:
            print("- " + w)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="directory of game-*.jsonl files (EVAL_OUT)")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON summary instead of text")
    ap.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES,
                    help="bootstrap resamples (default %d)" % DEFAULT_RESAMPLES)
    ap.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED,
                    help="RNG seed for the bootstrap (default %d)" % DEFAULT_BOOTSTRAP_SEED)
    ap.add_argument("--max-fallback-rate", type=float, default=DEFAULT_MAX_FALLBACK_RATE,
                    help="fallback-gate ceiling over llm-identity decisions; exceeding "
                         "it exits 2 (default %.2f). A cited run whose serving endpoint "
                         "never named the model exits 3." % DEFAULT_MAX_FALLBACK_RATE)
    ap.add_argument("--experiment", metavar="ID",
                    help="summarize only the experiment with this id (or unique "
                         "prefix) when the dir holds several conditions. Without "
                         "it, a multi-condition dir reports per experiment and "
                         "refuses pooled win rates.")
    ap.add_argument("--sanity-arms", action="store_true",
                    help="also check this block's sanity arms (<dir>__sanity-<arm>, written by "
                         "run_batch.py --sanity-arms): none may beat the floor. A violation exits 5, "
                         "a missing arm 6 (eval/sanity_arms.py)")
    args = ap.parse_args()

    path = Path(args.dir).expanduser()
    if not path.is_dir():
        raise SystemExit("not a directory: %s" % path)

    try:
        summary = build_summary(path, args.resamples, args.bootstrap_seed,
                                args.max_fallback_rate, experiment=args.experiment)
    except ValueError as e:
        raise SystemExit("--experiment: %s" % e)
    sanity = None
    if args.sanity_arms:
        import sanity_arms                  # lazy: sanity_arms imports this module
        sanity = sanity_arms.check(path)
        summary["sanity_arms"] = sanity
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print_human(summary)
        if sanity is not None:
            print()
            sanity_arms.print_human(sanity)

    # Gates are enforced AFTER the full report is printed.
    mi = summary["model_identity"]
    if mi["failed"]:
        print("\n" + "!" * 60, file=sys.stderr)
        print("FAILED MODEL-IDENTITY GATE: this run is stamped discipline=cited but the "
              "serving endpoint never named what answered (%s). A citable number has to "
              "say which model produced it."
              % ", ".join(mi["unresolved_fields"]), file=sys.stderr)
        print("!" * 60, file=sys.stderr)

    fg = summary["fallback_gate"]
    if fg["failed"]:
        print("\n" + "!" * 60, file=sys.stderr)
        print("FAILED FALLBACK GATE: %s of llm-identity decisions were fallbacks "
              "(ceiling %s). These games are too degraded to cite."
              % (fmt_pct(fg["fallback_rate"]), fmt_pct(fg["max_fallback_rate"])),
              file=sys.stderr)
        print("!" * 60, file=sys.stderr)

    # Exit 2 stays the fallback gate's documented code; the identity gate gets
    # its own 3 so a caller can tell "degraded games" from "unidentifiable
    # player". Both messages print even when both fail.
    if fg.get("harness_failed"):
        print("\n" + "!" * 60, file=sys.stderr)
        print("FAILED HARNESS-DECIDED GATE: the harness made %s of the llm identities' choices "
              "(ceiling %s). The score measures the harness as much as the model."
              % (fmt_pct(fg["harness_decided_rate"]), fmt_pct(fg["max_harness_decided_rate"])), file=sys.stderr)
        print("!" * 60, file=sys.stderr)
    if fg["failed"]:
        sys.exit(2)
    if mi["failed"]:
        sys.exit(3)
    if fg.get("harness_failed"):
        sys.exit(4)
    if sanity is not None and sanity["violations"]:
        sys.exit(5)
    if sanity is not None and sanity["missing"]:
        sys.exit(6)


if __name__ == "__main__":
    main()
