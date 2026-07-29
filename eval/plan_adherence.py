#!/usr/bin/env python3
"""Axis-4 PLAN adherence: milestone attainment per deck archetype (EVAL-DESIGN
section 10.3 / 5.2), generalising the UR-Delver-specific triple in
eval/process_metrics.py to all 16 archetypes via data-driven milestone specs
(eval/plans/<slug>.milestones.json, schema milestones.v1).

For every logged game seat, the seat's deck archetype is resolved via
resolve_deck_slug() (plan-text match first — game-line deck_a/deck_b are NOT
reliable deck names: tournament runs put MODEL names there). The matching
milestone spec is then evaluated over that seat's decision rows + prompt
snapshots. All extractors are text/regex over the logged prompt and option
menus — no judge model, no card database, no engine.

Metrics (each with n / se / 95% CI and explicit trivial baselines):
  PLAN-attain  per (deck, milestone): fraction of seat-games attained, Wilson
               CI plus a cluster bootstrap clustered by game_file. Reported as
               a PROFILE — a vector per deck — NEVER collapsed to one number
               (there is no exchange rate between "Thalia by 2" and "Tendrils
               at storm 8").
  PLAN-hold    combo_assembly_progress / protection_usage only: over turns t
               where the predicate held at t-1 (adjacent observed turns), the
               fraction where it still holds at t (cross-turn maintenance).
  Segments     among seat-games that reached milestone M: downstream win rate
               (identity-keyed `winner`) and mean turns to the next attainment
               milestone in spec order. Correlational, NOT causal.
  Partial credit  combo_assembly_progress attainment restricted to LOST games,
               labelled — a lost combo game that assembled the combo still
               scores on assembly.

Baselines (the 88% lesson — a score without its floor is not a score):
  never_attained floor 0.0 / always_attained ceiling 1.0 for every attainment
  milestone, with margin_over_never_attained. Rates carry the take-it-always
  (1.0) / take-it-never (0.0) endpoints plus n_opportunities. target_split
  carries the uniform-random face floor (player options / total options,
  averaged over the counted rows) because "always shoot face" is a strategy,
  not a floor.

Usage:
  python3 eval/plan_adherence.py DIR [DIR ...] --out RUNDIR \\
      [--decks slug,slug] [--specs eval/plans] [--min-games 1] [--json] \\
      [--strict-specs]

Outputs <out>/milestones.jsonl (one line per game_file x seat x milestone) and
<out>/summary.json; --json additionally dumps the summary object to stdout as
the only stdout content. Exit codes: 0 OK; 2 usage error / invalid specs under
--strict-specs / nothing usable to measure.

WHAT WOULD MAKE THIS FAIL
  * A typo'd card name in a spec (the dominant failure mode of text
    extractors): --strict-specs and test_plan_adherence.py cross-check every
    matched name against the real decklist eval/decks/<slug>.txt.
  * Trusting deck_a/deck_b as archetype names: tournament logs carry MODEL
    names there. resolve_deck_slug() matches plan_a/plan_b text against
    eval/decks/*.plan and refuses (None -> n_seats_unresolved) when neither
    plan nor display name matches; nothing is ever guessed.
  * The prompt format drifting (board:/hand()/yard() lines renamed): every
    extractor would silently return 0 — the per-milestone observed hit rates
    in the summary are the canary; a milestone that never fires on a corpus
    that plays the deck is a bug in the spec or in this file.
  * A storm count invented from thin air: THERE IS NO STORM COUNT IN THE
    PROMPT; casts_this_turn is derived only from RECENT EVENTS "<seat> cast"
    lines since the last "Turn N (<seat>)" marker, and says so in the schema.
"""

import argparse
import datetime
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent

from make_sft_data import iter_game_dirs, load_complete_games  # noqa: E402
from process_metrics import chosen_option_text, your_board, turn_bucket  # noqa: E402,F401
from mine_puzzles import (_OWN_LIFE_RE, _OPP_LIFE_RE, _HAND_RE, _PT_RE,  # noqa: E402
                          _board_power, split_you_opp)
from aggregate import wilson_ci, p90  # noqa: E402

SCHEMA_ROW = "plan-adherence.v1"
SCHEMA_SUMMARY = "plan-adherence-summary.v1"
SCHEMA_SPEC = "milestones.v1"

DECK_PLAN_DIR = REPO / "eval" / "decks"
DEFAULT_SPECS_DIR = REPO / "eval" / "plans"
HOLDOUTS_PATH = REPO / "eval" / "holdouts.json"

KINDS = ("deploy_by_turn", "sequence_order", "resource_discipline",
         "combo_assembly_progress", "payoff_condition", "protection_usage",
         "target_split")
ATTAINMENT_KINDS = frozenset(("deploy_by_turn", "sequence_order",
                              "combo_assembly_progress", "payoff_condition"))
RATE_KINDS = frozenset(("resource_discipline", "protection_usage"))
HOLD_KINDS = frozenset(("combo_assembly_progress", "protection_usage"))
ZONES = ("hand", "board", "yard")
PASS_TOKEN = "\x00PASS"   # chosen_option_text's stable pass sentinel

# ------------------------------------------------------------ deck resolution
# Contract section 4. MEASURED FACT: deck_a/deck_b are unreliable ("Identity-A",
# model names in runs/tournament-1); plan_a/plan_b stripped equal exactly one
# eval/decks/<slug>.plan stripped content for every game in runs/.

_PLAN_INDEX_CACHE = None
_NAME_INDEX_CACHE = None


def load_plan_index():
    """-> {plan_text_stripped: slug} built from eval/decks/*.plan"""
    global _PLAN_INDEX_CACHE
    if _PLAN_INDEX_CACHE is None:
        idx = {}
        for p in sorted(DECK_PLAN_DIR.glob("*.plan")):
            try:
                idx[p.read_text(encoding="utf-8").strip()] = p.stem
            except OSError:
                continue
        _PLAN_INDEX_CACHE = idx
    return _PLAN_INDEX_CACHE


def _name_index():
    """-> {display name: slug} from eval/holdouts.json decks[*].name"""
    global _NAME_INDEX_CACHE
    if _NAME_INDEX_CACHE is None:
        idx = {}
        try:
            data = json.loads(HOLDOUTS_PATH.read_text(encoding="utf-8"))
            for slug, info in (data.get("decks") or {}).items():
                name = (info or {}).get("name")
                if isinstance(name, str):
                    idx[name] = slug
        except (OSError, ValueError):
            pass
        _NAME_INDEX_CACHE = idx
    return _NAME_INDEX_CACHE


def resolve_deck_slug(game, ident):
    """ident in ("a","b"). Order: 1. exact plan-text match against
    eval/decks/*.plan; 2. display-name match against eval/holdouts.json
    decks[*].name == deck_<ident>; 3. None."""
    slug = load_plan_index().get((game.get("plan_%s" % ident) or "").strip())
    if slug is not None:
        return slug
    return _name_index().get(game.get("deck_%s" % ident))


def seat_to_ident(game, seat_name):
    """seat rows carry `seat` == deck_a or deck_b. Returns "a"/"b"/None.
    'a' wins on a name tie (mirror matchups), matching replay_match.seat_plan's
    documented convention."""
    if seat_name == game.get("deck_a"):
        return "a"
    if seat_name == game.get("deck_b"):
        return "b"
    return None


# ------------------------------------------------------------ shared stats
# cluster_bootstrap_ci is deliberately BYTE-IDENTICAL across t2/t4/t5 (shared
# interface contract section 5); the pinned reference test in each track's test
# file detects the copies drifting. Integration hoists it into aggregate.py.

def cluster_bootstrap_ci(clusters, resamples=2000, seed=20260728):
    """Percentile bootstrap CI for a pooled mean, resampling CLUSTERS with
    replacement (EVAL-DESIGN.md S6.1: decision/position metrics cluster by
    game; naive SEs understate up to 3x).

    clusters: iterable of iterables of floats; each inner iterable is one
    cluster's observations. Returns {"est","se","ci95","n","n_clusters",
    "resamples","bootstrap_seed"}. est is the POOLED mean over all
    observations (not the mean of cluster means). se is the sd of the
    bootstrap distribution. <2 clusters -> se/ci95 None (an interval a
    single cluster cannot earn)."""
    clusters = [list(c) for c in clusters if c]
    n_clusters = len(clusters)
    n = sum(len(c) for c in clusters)
    if n == 0:
        return {"est": None, "se": None, "ci95": [None, None], "n": 0,
                "n_clusters": 0, "resamples": resamples,
                "bootstrap_seed": seed}
    est = sum(sum(c) for c in clusters) / n
    if n_clusters < 2:
        return {"est": est, "se": None, "ci95": [None, None], "n": n,
                "n_clusters": n_clusters, "resamples": resamples,
                "bootstrap_seed": seed}
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        tot = 0.0
        cnt = 0
        for _ in range(n_clusters):
            c = clusters[rng.randrange(n_clusters)]
            tot += sum(c)
            cnt += len(c)
        means.append(tot / cnt if cnt else 0.0)
    means.sort()
    mu = sum(means) / len(means)
    var = sum((m - mu) ** 2 for m in means) / (len(means) - 1)
    lo = means[int(0.025 * resamples)]
    hi = means[min(resamples - 1, int(0.975 * resamples))]
    return {"est": est, "se": math.sqrt(var), "ci95": [lo, hi], "n": n,
            "n_clusters": n_clusters, "resamples": resamples,
            "bootstrap_seed": seed}


def metric_block(est, se, ci95, n, n_clusters, method):
    """The standard metric block every reported number is emitted as."""
    return {"est": est, "se": se, "ci95": list(ci95) if ci95 else [None, None],
            "n": n, "n_clusters": n_clusters, "method": method}


def wilson_block(wins, n):
    if n <= 0:
        return metric_block(None, None, [None, None], 0, None, "wilson")
    p = wins / n
    se = math.sqrt(p * (1.0 - p) / n)
    return metric_block(p, se, wilson_ci(wins, n), n, None, "wilson")


def cluster_block(clusters):
    r = cluster_bootstrap_ci(clusters)
    return metric_block(r["est"], r["se"], r["ci95"], r["n"],
                        r["n_clusters"], "cluster-bootstrap")


# ------------------------------------------------------------ prompt parsing
# Reuse mine_puzzles' pre-built regexes; only the yard line and RECENT EVENTS
# cast-counter are new here.

_YARD_RE = re.compile(r"^  yard\((\d+)\): (.*)$", re.M)
_TURN_MARKER_RE = re.compile(r"^  - Turn \d+ \(")
_CAST_EVENT_RE = re.compile(r"^  - .+? cast .+$")


def you_zone_text(prompt, zone):
    """Text of the named zone line inside the YOU section of the prompt.
    zone in ZONES. Empty string when absent/unparseable."""
    if not isinstance(prompt, str):
        return ""
    if zone == "board":
        return your_board(prompt)
    you_part, _opp = split_you_opp(prompt)
    if zone == "hand":
        m = _HAND_RE.search(you_part)
        return m.group(1) if m else ""
    if zone == "yard":
        m = _YARD_RE.search(you_part)
        return m.group(2) if m else ""
    return ""


def casts_this_turn(prompt):
    """Count '  - <seat> cast <Card>' RECENT EVENTS lines since the last
    '  - Turn <N> (<seat>)' marker. THERE IS NO STORM COUNT IN THE PROMPT
    (verified); this derived count is the only storm-like predicate available,
    counts BOTH players' casts (storm does too), and does NOT include the
    spell currently being decided on."""
    if not isinstance(prompt, str):
        return 0
    i = prompt.find("RECENT EVENTS")
    if i < 0:
        return 0
    lines = [ln for ln in prompt[i:].splitlines() if ln.startswith("  - ")]
    last_marker = -1
    for k, ln in enumerate(lines):
        if _TURN_MARKER_RE.match(ln):
            last_marker = k
    return sum(1 for ln in lines[last_marker + 1:] if _CAST_EVENT_RE.match(ln))


def state_holds(req, d, spec):
    """Evaluate a requires_state predicate against a decision row's prompt.
    req: None | "own_threat_on_battlefield" | dict of the documented predicate
    keys (multiple keys AND together). Unknown predicates never hold (and are
    rejected by spec validation)."""
    if req is None:
        return True
    prompt = d.get("prompt")
    if not isinstance(prompt, str):
        return False
    if isinstance(req, str):
        if req == "own_threat_on_battlefield":
            board = your_board(prompt)
            return any(t in board for t in (spec.get("threat_names") or ()))
        return False
    if not isinstance(req, dict):
        return False
    you_part, opp_part = split_you_opp(prompt)
    for key in sorted(req):
        val = req[key]
        if key == "own_board_contains":
            if not any(n in your_board(prompt) for n in val):
                return False
        elif key == "opp_life_at_most":
            m = _OPP_LIFE_RE.search(prompt)
            if m is None or int(m.group(1)) > val:
                return False
        elif key == "own_life_at_most":
            m = _OWN_LIFE_RE.search(prompt)
            if m is None or int(m.group(1)) > val:
                return False
        elif key == "own_yard_count_at_least":
            yard = you_zone_text(prompt, "yard")
            cnt = sum(yard.count(nm) for nm in val.get("names", ()))
            if cnt < val.get("n", 1):
                return False
        elif key == "casts_this_turn_at_least":
            if casts_this_turn(prompt) < val:
                return False
        elif key == "opp_board_power_at_least":
            if _board_power(opp_part) < val:
                return False
        else:
            return False
    return True


# ------------------------------------------------------------ matching helpers

def _matches(text, names):
    return isinstance(text, str) and any(n in text for n in names)


def _prefix_ok(text, prefix):
    if prefix is None:
        return True
    return isinstance(text, str) and text.startswith(prefix)


def _turn_of(d):
    t = d.get("turn")
    if isinstance(t, int) and not isinstance(t, bool):
        return t
    return None


def _clip(s, n=100):
    s = s if isinstance(s, str) else ""
    return s if len(s) <= n else s[:n - 3] + "..."


# ------------------------------------------------------------ milestone kinds
# Each extractor takes (ms, rows, spec, game) where rows is the seat's decision
# rows in file order, and returns a result dict with at least:
#   attained bool, attain_turn int|None, numerator, denominator,
#   hold_num, hold_den, evidence str
# Extra keys (per kind) travel into the summary via _extra.


def eval_deploy_by_turn(ms, rows, spec, game):
    names = ms.get("chosen_option_matches") or []
    prefix = ms.get("require_prefix")
    turn_max = ms.get("turn_max", 99)
    first_turn, first_text = None, None
    for d in rows:
        ch = chosen_option_text(d)
        if ch is None or ch == PASS_TOKEN:
            continue
        if not _prefix_ok(ch, prefix):
            continue
        if _matches(ch, names):
            first_turn, first_text = _turn_of(d), ch
            break
    attained = first_turn is not None and first_turn <= turn_max
    ev = ("first match '%s' @turn %s (turn_max %d)"
          % (_clip(first_text, 60), first_turn, turn_max)
          if first_turn is not None else "never matched (turn_max %d)" % turn_max)
    return {"attained": attained,
            "attain_turn": first_turn if attained else None,
            "numerator": None, "denominator": None,
            "hold_num": None, "hold_den": None, "evidence": ev}


def eval_sequence_order(ms, rows, spec, game):
    first_names = ms.get("first") or []
    then_names = ms.get("then") or []
    within = ms.get("within_turn", True)
    prefix = ms.get("require_prefix", "CAST")
    attain_turn = None
    ev = "sequence never completed"
    if within:
        seen_first_in_turn = {}
        for d in rows:
            ch = chosen_option_text(d)
            t = _turn_of(d)
            if ch is None or ch == PASS_TOKEN or t is None:
                continue
            if not _prefix_ok(ch, prefix):
                continue
            if _matches(ch, then_names) and seen_first_in_turn.get(t):
                attain_turn = t
                ev = "'%s' -> '%s' within turn %d" % (
                    _clip(seen_first_in_turn[t], 40), _clip(ch, 40), t)
                break
            if _matches(ch, first_names):
                seen_first_in_turn.setdefault(t, ch)
    else:
        first_seen = None
        for d in rows:
            ch = chosen_option_text(d)
            if ch is None or ch == PASS_TOKEN:
                continue
            if not _prefix_ok(ch, prefix):
                continue
            if first_seen is not None and _matches(ch, then_names):
                attain_turn = _turn_of(d)
                ev = "'%s' then '%s' @turn %s" % (
                    _clip(first_seen, 40), _clip(ch, 40), attain_turn)
                break
            if first_seen is None and _matches(ch, first_names):
                first_seen = ch
    return {"attained": attain_turn is not None, "attain_turn": attain_turn,
            "numerator": None, "denominator": None,
            "hold_num": None, "hold_den": None, "evidence": ev}


def eval_resource_discipline(ms, rows, spec, game):
    """rate = times chosen / times OFFERED, over type=='play' menus only (other
    row types list card names as targets, which are not 'offers to act')."""
    names = ms.get("chosen_option_matches") or []
    prefix = ms.get("require_prefix", "CAST")
    num = den = 0
    obs = []
    for d in rows:
        if d.get("type") != "play":
            continue
        opts = d.get("options") if isinstance(d.get("options"), list) else []
        offered = any(isinstance(o, str) and _prefix_ok(o, prefix)
                      and _matches(o, names) for o in opts)
        if not offered:
            continue
        den += 1
        ch = chosen_option_text(d)
        took = 1 if (_prefix_ok(ch, prefix) and _matches(ch, names)) else 0
        num += took
        obs.append(float(took))
    return {"attained": num > 0, "attain_turn": None,
            "numerator": num, "denominator": den,
            "hold_num": None, "hold_den": None,
            "evidence": "chosen %d / offered %d" % (num, den),
            "_obs": obs}


def _hold_from_curve(held_by_turn):
    """held_by_turn: {turn: bool}. Over adjacent observed turns (t-1, t) where
    the predicate held at t-1, fraction still holding at t. Returns
    (hold_num, hold_den, transitions[list of 0/1])."""
    num = den = 0
    trans = []
    for t in sorted(held_by_turn):
        if (t - 1) in held_by_turn and held_by_turn[t - 1]:
            den += 1
            ok = 1 if held_by_turn[t] else 0
            num += ok
            trans.append(float(ok))
    return num, den, trans


def eval_combo_assembly_progress(ms, rows, spec, game):
    pieces = ms.get("piece_names") or spec.get("combo_pieces") or []
    zones = [z for z in (ms.get("zones") or list(ZONES)) if z in ZONES]
    min_count = ms.get("min_count", len(pieces) or 1)
    count_by_turn = {}
    for d in rows:
        prompt = d.get("prompt")
        t = _turn_of(d)
        if not isinstance(prompt, str) or t is None:
            continue
        zone_text = "\x01".join(you_zone_text(prompt, z) for z in zones)
        cnt = sum(1 for p in pieces if p in zone_text)
        if cnt > count_by_turn.get(t, -1):
            count_by_turn[t] = cnt
    attain_turn = None
    for t in sorted(count_by_turn):
        if count_by_turn[t] >= min_count:
            attain_turn = t
            break
    held = {t: (c >= min_count) for t, c in count_by_turn.items()}
    hold_num, hold_den, trans = _hold_from_curve(held)
    curve = [[t, count_by_turn[t]] for t in sorted(count_by_turn)]
    return {"attained": attain_turn is not None, "attain_turn": attain_turn,
            "numerator": None, "denominator": None,
            "hold_num": hold_num, "hold_den": hold_den,
            "evidence": "max %s/%d pieces; curve %s" % (
                max(count_by_turn.values()) if count_by_turn else 0,
                min_count, _clip(json.dumps(curve), 80)),
            "_hold_obs": trans}


def eval_payoff_condition(ms, rows, spec, game):
    names = ms.get("chosen_option_matches") or []
    prefix = ms.get("require_prefix", "CAST")
    req = ms.get("requires_state")
    attain_turn, ev = None, "payoff never cast under required state"
    for d in rows:
        ch = chosen_option_text(d)
        if ch is None or ch == PASS_TOKEN:
            continue
        if not _prefix_ok(ch, prefix) or not _matches(ch, names):
            continue
        if state_holds(req, d, spec):
            attain_turn = _turn_of(d)
            ev = "'%s' @turn %s with state %s" % (
                _clip(ch, 50), attain_turn, json.dumps(req))
            break
    return {"attained": attain_turn is not None, "attain_turn": attain_turn,
            "numerator": None, "denominator": None,
            "hold_num": None, "hold_den": None, "evidence": ev}


def eval_protection_usage(ms, rows, spec, game):
    """Ported from process_metrics.plan_adherence (do not rewrite): opportunity
    = a play row where a protection spell is castable (CAST option matching)
    WHILE a spec threat is on our board; used = the chosen option is such a
    CAST. Additionally emits the cross-turn threat-hold curve for PLAN-hold."""
    names = ms.get("chosen_option_matches") or []
    threats = spec.get("threat_names") or []
    opps = used = 0
    obs = []
    held_by_turn = {}
    for d in rows:
        prompt = d.get("prompt")
        t = _turn_of(d)
        if isinstance(prompt, str) and t is not None:
            on_board = any(th in your_board(prompt) for th in threats)
            held_by_turn[t] = held_by_turn.get(t, False) or on_board
        if d.get("type") != "play":
            continue
        opts = d.get("options") if isinstance(d.get("options"), list) else []
        chosen = chosen_option_text(d)
        prot_castable = any(
            isinstance(o, str) and o.startswith("CAST")
            and any(p in o for p in names) for o in opts)
        threat_on_board = any(th in your_board(d.get("prompt"))
                              for th in threats)
        if prot_castable and threat_on_board:
            opps += 1
            took = 1 if (isinstance(chosen, str) and chosen.startswith("CAST")
                         and any(p in chosen for p in names)) else 0
            used += took
            obs.append(float(took))
    hold_num, hold_den, trans = _hold_from_curve(held_by_turn)
    return {"attained": used > 0, "attain_turn": None,
            "numerator": used, "denominator": opps,
            "hold_num": hold_num, "hold_den": hold_den,
            "evidence": "used %d / opportunities %d" % (used, opps),
            "_obs": obs, "_hold_obs": trans}


def eval_target_split(ms, rows, spec, game):
    """Ported from process_metrics burn_target_split: a `target` row whose
    options contain a bare player name; classify the chosen target as face
    (player) / creature (P/T present) / other. Optional `spells` filter keeps
    only rows whose prompt says 'TARGET for <spell>'."""
    spells = ms.get("spells") or []
    player_names = set()
    for ident in ("a", "b"):
        nm = game.get("deck_%s" % ident)
        if nm is not None:
            player_names.add(str(nm))
    face = creature = other = 0
    floor_terms = []
    obs = []
    for d in rows:
        if d.get("type") != "target":
            continue
        opts = d.get("options") if isinstance(d.get("options"), list) else []
        if not opts:
            continue
        n_players = sum(1 for o in opts
                        if isinstance(o, str) and o.strip() in player_names)
        if n_players == 0:
            continue
        if spells:
            prompt = d.get("prompt")
            if not (isinstance(prompt, str)
                    and any(("TARGET for %s" % s) in prompt for s in spells)):
                continue
        chosen = chosen_option_text(d)
        if not isinstance(chosen, str) or chosen == PASS_TOKEN:
            continue
        cs = chosen.strip()
        if cs in player_names:
            face += 1
            obs.append(1.0)
        elif _PT_RE.search(cs):
            creature += 1
            obs.append(0.0)
        else:
            other += 1
        floor_terms.append(n_players / float(len(opts)))
    return {"attained": (face + creature + other) > 0, "attain_turn": None,
            "numerator": face, "denominator": face + creature,
            "hold_num": None, "hold_den": None,
            "evidence": "face/creature/other %d/%d/%d" % (face, creature, other),
            "_obs": obs, "_floor_terms": floor_terms}


KIND_EVAL = {
    "deploy_by_turn": eval_deploy_by_turn,
    "sequence_order": eval_sequence_order,
    "resource_discipline": eval_resource_discipline,
    "combo_assembly_progress": eval_combo_assembly_progress,
    "payoff_condition": eval_payoff_condition,
    "protection_usage": eval_protection_usage,
    "target_split": eval_target_split,
}


# ------------------------------------------------------------ spec loading

def load_specs(specs_dir):
    """-> ({slug: spec}, [error strings]). A spec whose JSON does not parse is
    an error; schema/name validation errors are collected per spec."""
    specs, errors = {}, []
    d = Path(specs_dir)
    if not d.is_dir():
        return {}, ["specs dir not found: %s" % d]
    for p in sorted(d.glob("*.milestones.json")):
        try:
            spec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            errors.append("%s: unreadable/unparseable: %s" % (p.name, e))
            continue
        slug = p.name[:-len(".milestones.json")]
        errors.extend("%s: %s" % (p.name, e)
                      for e in validate_spec(spec, slug))
        specs[slug] = spec
    return specs, errors


def _spec_names(ms):
    """Every card name a milestone will substring-match."""
    names = []
    for key in ("chosen_option_matches", "piece_names", "first", "then",
                "spells"):
        v = ms.get(key)
        if isinstance(v, list):
            names.extend(x for x in v if isinstance(x, str))
    return names


def validate_spec(spec, slug):
    """Schema + decklist validation. Every matched card name must appear in
    eval/decks/<slug>.txt — a typo'd name is the dominant text-extractor
    failure mode and would silently zero a milestone."""
    errs = []
    if not isinstance(spec, dict):
        return ["spec is not an object"]
    if spec.get("schema") != SCHEMA_SPEC:
        errs.append("schema != %r" % SCHEMA_SPEC)
    if spec.get("deck") != slug:
        errs.append("deck %r != filename stem %r" % (spec.get("deck"), slug))
    plan_file = spec.get("plan_file")
    if not (isinstance(plan_file, str) and (REPO / plan_file).is_file()):
        errs.append("plan_file missing or not found: %r" % plan_file)
    deck_txt = ""
    deck_path = DECK_PLAN_DIR / ("%s.txt" % slug)
    if deck_path.is_file():
        deck_txt = deck_path.read_text(encoding="utf-8")
    else:
        errs.append("decklist not found: %s" % deck_path)
    mss = spec.get("milestones")
    if not (isinstance(mss, list) and mss):
        errs.append("milestones missing/empty")
        mss = []
    seen_ids = set()
    for ms in mss:
        if not isinstance(ms, dict):
            errs.append("milestone is not an object")
            continue
        mid = ms.get("id")
        if not (isinstance(mid, str) and re.match(r"^[a-z0-9_]+$", mid or "")):
            errs.append("milestone id not snake_case: %r" % mid)
        if mid in seen_ids:
            errs.append("duplicate milestone id: %r" % mid)
        seen_ids.add(mid)
        kind = ms.get("kind")
        if kind not in KINDS:
            errs.append("%s: unknown kind %r" % (mid, kind))
            continue
        if not isinstance(ms.get("description"), str):
            errs.append("%s: missing description" % mid)
        if kind == "deploy_by_turn" and not isinstance(ms.get("turn_max"), int):
            errs.append("%s: deploy_by_turn needs int turn_max" % mid)
        if kind == "sequence_order" and not (ms.get("first") and ms.get("then")):
            errs.append("%s: sequence_order needs first+then" % mid)
        if kind == "combo_assembly_progress":
            if not isinstance(ms.get("min_count"), int):
                errs.append("%s: combo_assembly_progress needs int min_count" % mid)
            for z in ms.get("zones") or []:
                if z not in ZONES:
                    errs.append("%s: unknown zone %r" % (mid, z))
        req = ms.get("requires_state")
        if req is not None and not isinstance(req, (str, dict)):
            errs.append("%s: requires_state must be string or object" % mid)
        if isinstance(req, str) and req != "own_threat_on_battlefield":
            errs.append("%s: unknown requires_state %r" % (mid, req))
        if isinstance(req, dict):
            known = ("own_board_contains", "opp_life_at_most",
                     "own_life_at_most", "own_yard_count_at_least",
                     "casts_this_turn_at_least", "opp_board_power_at_least")
            for k in req:
                if k not in known:
                    errs.append("%s: unknown requires_state key %r" % (mid, k))
        if deck_txt:
            for name in _spec_names(ms):
                if name not in deck_txt:
                    errs.append("%s: card %r not in %s" % (mid, name, deck_path.name))
            if isinstance(req, dict):
                for nm in (req.get("own_board_contains") or []):
                    if nm not in deck_txt:
                        errs.append("%s: requires_state card %r not in decklist" % (mid, nm))
                yv = req.get("own_yard_count_at_least") or {}
                for nm in (yv.get("names") or []):
                    if nm not in deck_txt:
                        errs.append("%s: requires_state card %r not in decklist" % (mid, nm))
    if deck_txt:
        for key in ("threat_names", "combo_pieces"):
            for nm in (spec.get(key) or []):
                if nm not in deck_txt:
                    errs.append("%s %r not in decklist" % (key, nm))
    return errs


# ------------------------------------------------------------ evaluation core

def evaluate_corpus(dirs, specs, deck_filter=None):
    """Scan run dirs, resolve every game seat to a deck slug, evaluate its
    milestone spec. Returns (rows, per_deck, counters, warnings) where rows is
    the flat milestones.jsonl payload and per_deck accumulates raw material
    for the summary."""
    rows = []
    per_deck = defaultdict(lambda: {
        # milestone_id -> lists parallel over seat-games
        "results": defaultdict(list),   # each: dict from KIND_EVAL + context
        "seat_games": [],               # (game_file, ident, won, model)
        "order": None,                  # milestone order from spec
        "game_files": set(),
    })
    counters = {"n_games": 0, "n_seats_resolved": 0, "n_seats_unresolved": 0,
                "n_seats_filtered": 0, "n_seats_no_decisions": 0}
    warnings = []
    seen_dirs = []
    for dir_arg in dirs:
        root = Path(dir_arg).expanduser()
        if not root.is_dir():
            warnings.append("not a directory (skipped): %s" % dir_arg)
            continue
        seen_dirs.append(str(dir_arg))
        for gd in iter_game_dirs(root):
            games, w = load_complete_games(gd)
            warnings.extend("%s: %s" % (gd, x) for x in w)
            for entry in games:
                game = entry["game"]
                counters["n_games"] += 1
                for ident in ("a", "b"):
                    slug = resolve_deck_slug(game, ident)
                    if slug is None:
                        counters["n_seats_unresolved"] += 1
                        continue
                    counters["n_seats_resolved"] += 1
                    if deck_filter and slug not in deck_filter:
                        counters["n_seats_filtered"] += 1
                        continue
                    spec = specs.get(slug)
                    if spec is None:
                        warnings.append("no milestone spec for deck %r" % slug)
                        continue
                    seat_rows = [d for d in entry["decisions"]
                                 if seat_to_ident(game, str(d.get("seat"))) == ident]
                    if not seat_rows:
                        # e.g. an 'ai'-controlled opponent seat whose decisions
                        # are never logged, or ident 'b' of a mirror matchup.
                        # Scoring it would report the UNLOGGED seat as
                        # attaining nothing — a fake zero, not a measurement.
                        counters["n_seats_no_decisions"] += 1
                        continue
                    winner = game.get("winner")
                    won = None
                    if winner == "A":
                        won = (ident == "a")
                    elif winner == "B":
                        won = (ident == "b")
                    model = (game.get("served_smart_%s" % ident)
                             or game.get("smart_%s" % ident) or "")
                    controller = game.get("controller_%s" % ident) or ""
                    acc = per_deck[slug]
                    acc["game_files"].add(entry["file"])
                    acc["seat_games"].append((entry["file"], ident, won, model))
                    if acc["order"] is None:
                        acc["order"] = [ms.get("id") for ms in spec["milestones"]]
                    for ms in spec["milestones"]:
                        res = KIND_EVAL[ms["kind"]](ms, seat_rows, spec, game)
                        res["_game_file"] = entry["file"]
                        res["_ident"] = ident
                        res["_won"] = won
                        res["_kind"] = ms["kind"]
                        res["_weight"] = ms.get("weight", 1.0)
                        acc["results"][ms["id"]].append(res)
                        rows.append({
                            "schema": SCHEMA_ROW,
                            "run_dir": str(dir_arg),
                            "game_file": entry["file"],
                            "seat": str(game.get("deck_%s" % ident)),
                            "ident": ident,
                            "deck": slug,
                            "milestone_id": ms["id"],
                            "kind": ms["kind"],
                            "attained": bool(res["attained"]),
                            "attain_turn": res["attain_turn"],
                            "numerator": res["numerator"],
                            "denominator": res["denominator"],
                            "hold_num": res["hold_num"],
                            "hold_den": res["hold_den"],
                            "won": won,
                            "model": model,
                            "controller": controller,
                            "evidence": res["evidence"],
                        })
    counters["dirs"] = seen_dirs
    return rows, per_deck, counters, warnings


def _turn_dist(turns):
    ts = sorted(turns)
    return {"n": len(ts),
            "mean": (sum(ts) / len(ts)) if ts else None,
            "median": ts[len(ts) // 2] if ts else None,
            "min": ts[0] if ts else None,
            "max": ts[-1] if ts else None,
            "p90": p90(ts)}


def summarize_deck(slug, acc, spec):
    """Build the by_deck[slug] summary object. PLAN-attain is a PROFILE — one
    block per milestone — never collapsed to a single deck score."""
    out = {"n_games": len(acc["game_files"]),
           "n_seats": len(acc["seat_games"]),
           "milestones": {}, "plan_hold": {}, "segments": {},
           "partial_credit_lost_games": {}}
    order = acc["order"] or []
    for mid in order:
        results = acc["results"].get(mid, [])
        if not results:
            continue
        kind = results[0]["_kind"]
        m = {"kind": kind, "weight": results[0]["_weight"],
             "n_seat_games": len(results)}
        by_file = defaultdict(list)
        if kind in ATTAINMENT_KINDS:
            att = [1.0 if r["attained"] else 0.0 for r in results]
            for r, a in zip(results, att):
                by_file[r["_game_file"]].append(a)
            wins = int(sum(att))
            wb = wilson_block(wins, len(att))
            m["attain_rate"] = wb
            m["attain_rate_clustered"] = cluster_block(by_file.values())
            m["attain_turns"] = _turn_dist(
                [r["attain_turn"] for r in results
                 if r["attained"] and r["attain_turn"] is not None])
            m["baselines"] = {"never_attained": 0.0, "always_attained": 1.0}
            m["margin_over_never_attained"] = wb["est"]
        elif kind in RATE_KINDS:
            num = sum(r["numerator"] or 0 for r in results)
            den = sum(r["denominator"] or 0 for r in results)
            for r in results:
                by_file[r["_game_file"]].extend(r.get("_obs") or [])
            cb = cluster_block(by_file.values())
            m["rate"] = cb
            m["rate_wilson"] = wilson_block(num, den)
            m["numerator"] = num
            m["n_opportunities"] = den
            m["baselines"] = {"always_take_rate": 1.0, "never_take_rate": 0.0,
                              "n_opportunities": den}
            m["margin_over_never_take"] = cb["est"]
        elif kind == "target_split":
            face = sum(r["numerator"] or 0 for r in results)
            fc = sum(r["denominator"] or 0 for r in results)
            floor_terms = []
            for r in results:
                by_file[r["_game_file"]].extend(r.get("_obs") or [])
                floor_terms.extend(r.get("_floor_terms") or [])
            cb = cluster_block(by_file.values())
            m["face_rate"] = cb
            m["face_rate_wilson"] = wilson_block(face, fc)
            m["face"] = face
            m["face_plus_creature"] = fc
            floor = (sum(floor_terms) / len(floor_terms)) if floor_terms else None
            m["baselines"] = {
                "uniform_random_face_rate": floor,
                "n_rows": len(floor_terms),
                "note": "'always shoot face' is a strategy, not a floor; the "
                        "floor is uniform-random over each row's menu."}
            m["margin_over_uniform_random"] = (
                (cb["est"] - floor) if (cb["est"] is not None
                                        and floor is not None) else None)
        if kind in HOLD_KINDS:
            hn = sum(r["hold_num"] or 0 for r in results)
            hd = sum(r["hold_den"] or 0 for r in results)
            hold_by_file = defaultdict(list)
            for r in results:
                hold_by_file[r["_game_file"]].extend(r.get("_hold_obs") or [])
            out["plan_hold"][mid] = {
                "hold_num": hn, "hold_den": hd,
                "rate": cluster_block(hold_by_file.values()),
                "note": "PLAN-hold: over adjacent observed turns where the "
                        "predicate held at t-1, fraction still holding at t."}
        if kind == "combo_assembly_progress":
            lost = [r for r in results if r["_won"] is False]
            latt = [1.0 if r["attained"] else 0.0 for r in lost]
            out["partial_credit_lost_games"][mid] = {
                "n_lost_seat_games": len(lost),
                "attain_rate_in_lost_games": wilson_block(int(sum(latt)), len(latt)),
                "label": "checkpoint partial credit: assembly attainment "
                         "counted EVEN IN LOST GAMES (a lost combo game that "
                         "assembled the combo scores on assembly).",
                "baselines": {"never_attained": 0.0, "always_attained": 1.0},
            }
        out["milestones"][mid] = m

    # segments: downstream win rate + turns-to-next among reachers
    attain_ids = [mid for mid in order
                  if acc["results"].get(mid)
                  and acc["results"][mid][0]["_kind"] in ATTAINMENT_KINDS]
    for i, mid in enumerate(attain_ids):
        results = acc["results"][mid]
        reached = [r for r in results if r["attained"]]
        next_id = attain_ids[i + 1] if i + 1 < len(attain_ids) else None
        wins = sum(1 for r in reached if r["_won"] is True)
        decisive = sum(1 for r in reached if r["_won"] is not None)
        ttn = []
        if next_id is not None:
            nxt = {(r["_game_file"], r["_ident"]): r
                   for r in acc["results"][next_id]}
            for r in reached:
                nr = nxt.get((r["_game_file"], r["_ident"]))
                if (nr is not None and nr["attained"]
                        and nr["attain_turn"] is not None
                        and r["attain_turn"] is not None
                        and nr["attain_turn"] >= r["attain_turn"]):
                    ttn.append(nr["attain_turn"] - r["attain_turn"])
        out["segments"][mid] = {
            "n_reached": len(reached),
            "downstream_win_rate": wilson_block(wins, decisive),
            "next_milestone_id": next_id,
            "mean_turns_to_next": (sum(ttn) / len(ttn)) if ttn else None,
            "n_turns_to_next": len(ttn),
        }
    return out


CAVEATS = [
    "Milestone attainment is correlational, not causal: attaining a milestone "
    "against a fast clock may itself be a punt, and reaching a milestone "
    "correlates with the game already going well.",
    "Extractors are text-brittle: they substring-match card names in logged "
    "option/prompt text; a prompt-format change or an unexpected rendering "
    "silently zeroes a milestone (watch the per-milestone n / hit counts).",
    "A deck with fewer than --min-games games contributes no CI worth "
    "reading; its rows are still emitted but the interval spans most of [0,1].",
    "Fallback rows (engine-substituted legal defaults) are NOT excluded: "
    "milestones measure what happened in the game, matching the ported "
    "process_metrics semantics.",
    "In a mirror matchup (deck_a == deck_b) seat rows cannot be attributed "
    "('a' wins the name tie), so ident 'b' contributes zero decision rows.",
    "A seat with ZERO logged decision rows (e.g. an 'ai'-controlled opponent "
    "whose decisions are not logged) is excluded (n_seats_no_decisions), not "
    "scored as attaining nothing — that would be a fake zero.",
    "by_deck profiles POOL every controller/model that piloted the deck "
    "(a 'floor' seat legitimately attains ~nothing); slice milestones.jsonl "
    "by its model/controller fields before comparing pilots.",
    "casts_this_turn is derived from RECENT EVENTS cast lines (both players) "
    "since the last turn marker; there is no storm count in the prompt.",
]


def build_summary(rows, per_deck, counters, specs, warnings, min_games):
    by_deck = {}
    for slug in sorted(per_deck):
        acc = per_deck[slug]
        if len(acc["game_files"]) < min_games:
            warnings = warnings + [
                "deck %s: %d game(s) < --min-games %d — excluded from by_deck"
                % (slug, len(acc["game_files"]), min_games)]
            continue
        by_deck[slug] = summarize_deck(slug, acc, specs.get(slug) or {})
    return {
        "schema": SCHEMA_SUMMARY,
        "generated_utc": datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
        "dirs": counters.get("dirs", []),
        "n_games": counters["n_games"],
        "n_seats_resolved": counters["n_seats_resolved"],
        "n_seats_unresolved": counters["n_seats_unresolved"],
        "n_seats_no_decisions": counters.get("n_seats_no_decisions", 0),
        "by_deck": by_deck,
        "baselines": {
            "never_attained": 0.0,
            "always_attained": 1.0,
            "note": "every attainment block repeats these plus "
                    "margin_over_never_attained; rate blocks carry "
                    "always/never-take endpoints and n_opportunities; "
                    "target_split carries the uniform-random face floor.",
        },
        "caveats": list(CAVEATS),
        "warnings": warnings,
    }


# ------------------------------------------------------------ rendering / CLI

def render_text(summary):
    L = []
    L.append("PLAN adherence (axis 4) — %d games, %d seats resolved, %d unresolved"
             % (summary["n_games"], summary["n_seats_resolved"],
                summary["n_seats_unresolved"]))
    for slug in sorted(summary["by_deck"]):
        dk = summary["by_deck"][slug]
        L.append("")
        L.append("[%s] %d game(s), %d seat-game(s)  — PROFILE (never collapse)"
                 % (slug, dk["n_games"], dk["n_seats"]))
        for mid, m in dk["milestones"].items():
            if "attain_rate" in m:
                b = m["attain_rate"]
                est = "-" if b["est"] is None else "%.2f" % b["est"]
                L.append("  %-28s attain=%s (n=%d, ci95=[%s,%s]) floor=0.0"
                         % (mid, est, b["n"],
                            "-" if b["ci95"][0] is None else "%.2f" % b["ci95"][0],
                            "-" if b["ci95"][1] is None else "%.2f" % b["ci95"][1]))
            elif "rate" in m:
                b = m["rate"]
                est = "-" if b["est"] is None else "%.2f" % b["est"]
                L.append("  %-28s rate=%s (%d/%d opportunities)"
                         % (mid, est, m["numerator"], m["n_opportunities"]))
            elif "face_rate" in m:
                b = m["face_rate"]
                est = "-" if b["est"] is None else "%.2f" % b["est"]
                fl = m["baselines"]["uniform_random_face_rate"]
                L.append("  %-28s face_rate=%s (floor=%s, n=%d)"
                         % (mid, est,
                            "-" if fl is None else "%.2f" % fl, b["n"]))
    for w in summary["warnings"][:20]:
        L.append("warning: %s" % w)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", metavar="DIR",
                    help="run dirs scanned recursively for game-*.jsonl")
    ap.add_argument("--out", default=None, metavar="RUNDIR",
                    help="output dir for milestones.jsonl + summary.json")
    ap.add_argument("--decks", default=None,
                    help="comma-separated deck slug filter")
    ap.add_argument("--specs", default=str(DEFAULT_SPECS_DIR),
                    help="milestone spec dir (default eval/plans)")
    ap.add_argument("--min-games", type=int, default=1,
                    help="decks with fewer games are excluded from by_deck")
    ap.add_argument("--json", action="store_true",
                    help="dump the summary object to stdout as the ONLY "
                         "stdout content")
    ap.add_argument("--strict-specs", action="store_true",
                    help="exit 2 if any spec fails schema/decklist validation "
                         "(default: warn)")
    args = ap.parse_args(argv)

    if not args.out and not args.json:
        print("need --out and/or --json", file=sys.stderr)
        return 2

    specs, spec_errors = load_specs(args.specs)
    if spec_errors:
        for e in spec_errors:
            print("SPEC %s: %s" % ("ERROR" if args.strict_specs else "WARNING", e),
                  file=sys.stderr)
        if args.strict_specs:
            print("REFUSING under --strict-specs: %d spec validation error(s)."
                  % len(spec_errors), file=sys.stderr)
            return 2
    if not specs:
        print("no milestone specs found in %s" % args.specs, file=sys.stderr)
        return 2

    deck_filter = None
    if args.decks:
        deck_filter = set(s.strip() for s in args.decks.split(",") if s.strip())
        unknown = deck_filter - set(specs)
        if unknown:
            print("unknown deck slug(s): %s" % ", ".join(sorted(unknown)),
                  file=sys.stderr)
            return 2

    rows, per_deck, counters, warnings = evaluate_corpus(
        args.dirs, specs, deck_filter)
    warnings = ["spec: %s" % e for e in spec_errors] + warnings

    if counters["n_games"] == 0:
        print("nothing usable to measure: no complete games found under %s"
              % ", ".join(args.dirs), file=sys.stderr)
        return 2
    if counters["n_seats_resolved"] == 0:
        print("nothing usable to measure: no seat resolved to a deck "
              "archetype (%d unresolved)" % counters["n_seats_unresolved"],
              file=sys.stderr)
        return 2

    summary = build_summary(rows, per_deck, counters, specs, warnings,
                            args.min_games)

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "milestones.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        (out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(render_text(summary))
        if args.out:
            print("\nwrote %d milestone rows -> %s" % (
                len(rows), Path(args.out) / "milestones.jsonl"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
