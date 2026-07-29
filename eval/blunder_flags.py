#!/usr/bin/env python3
"""Blunder flagging (EVAL-DESIGN.md S4: axes 2 & 5) — PURE rules, no I/O in the core.

Hybrid flagging per S4.1: three lanes over scoreable decisions of already-played
games:
  text-flag       11 cheap text red-flag rules over the prompt snapshot +
                  option menu (this module; each rule is a pure function of
                  (decision_row, game_line, ctx)).
  shadow-disagree d["shadow_choice"] present and != d["choice"]. MEASURED FACT:
                  ZERO rows in the current runs/ corpus carry shadow_choice
                  (emitted only under SHADOW_AI=1, which no recorded batch
                  used), so this lane is VACUOUS today. scan() reports
                  n_shadow_rows_available and a shadow_lane_note instead of a
                  fake 0% disagreement rate.
  audit-sample    a deterministic uniform 5% sample of all scoreable decisions
                  — measures what the flaggers miss (leakage), per S4.1(c).

Flags are TRIGGERS for adjudication (eval/blunder_report.py), never verdicts:
a flag says "worth adjudicating", not "blunder". Rules in LOW_PRECISION_FLAGS
are known-low-precision (holding up mana / a counter is often correct) and are
reported split by precision class.

No network, no engine, no file reads inside the rules. The only I/O in this
module is the lazy deck-plan index for resolve_deck_slug (reads eval/decks/*.plan
+ eval/holdouts.json once, outside the rules, and only when scan() runs over
real games; a failure degrades to deck_slug=None, never raises).

Usage: library only — imported by eval/blunder_report.py. Run the self-test:
  python3 eval/test_blunder_flags.py

WHAT WOULD MAKE THIS FAIL
  * The prompt text format drifting (board:/hand(/Incoming:/RECENT EVENTS
    lines renamed) — every rule silently stops firing; the corpus-level flag
    rate in blunder_report's summary dropping to ~0 on a corpus that
    previously flagged is the tripwire.
  * choice encoding drifting (play stops using -1 as the PASS sentinel, or
    count types start logging subsets) — pass/decline detection breaks.
  * A rule firing on 90%+ of scoreable decisions — that flagger is useless
    noise; the per-rule fire counts in scan() stats exist to catch it.
  * audit_sample_ids losing determinism (different set across processes) —
    the leakage measurement becomes irreproducible.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent

from mine_puzzles import (  # noqa: E402
    check_lethal_now, check_save_vs_lethal, check_forced_block,
    split_you_opp, _HAND_RE, _PT_RE, _INCOMING_RE, _STACK_RE,
    _OPP_LIFE_RE, _first_int, eligible, DAMAGE_SPELLS, ANSWER_CARDS,
)
from process_metrics import chosen_option_text  # noqa: E402

# Optional canonical resolver (t4 owns plan_adherence.py; inline fallback below
# per the shared contract S4 — byte-equivalent resolution order).
try:
    from plan_adherence import resolve_deck_slug as _t4_resolve_deck_slug
except ImportError:
    _t4_resolve_deck_slug = None


# ------------------------------------------------------------- scoreability

SCOREABLE_TYPES = frozenset((
    "play", "attackers", "blockers", "targets", "target", "discard",
    "sacrifice", "modes", "entity", "zone-change", "mulligan"))


def scoreable(d):
    """-> (bool, reason). EVAL-DESIGN S4.3: a decision is scoreable iff it had
    a real menu (n_options >= 2), was actually decided by a strategy (no
    fallback), is of a scoreable type, and was not auto-resolved (tier auto).
    reason tokens: '', 'degenerate_n_options', 'fallback_row',
    'type_not_scoreable', 'auto_tier'."""
    n = d.get("n_options")
    if not isinstance(n, int) or isinstance(n, bool) or n < 2:
        return False, "degenerate_n_options"
    if d.get("fallback") is not None:
        return False, "fallback_row"
    if d.get("type") not in SCOREABLE_TYPES:
        return False, "type_not_scoreable"
    if d.get("tier") == "auto":
        return False, "auto_tier"
    return True, ""


# --------------------------------------------------------------- text rules

FLAG_RULES = (
    "lethal_available_passed",
    "attack_declined_with_lethal",
    "forced_block_declined",
    "save_vs_lethal_passed",
    "durdle_pass_with_castable",
    "land_drop_skipped",
    "discard_only_land",
    "discard_only_threat",
    "burn_to_creature_when_face_lethal",
    "counter_held_while_dying",
    "chump_free_lethal_block_skipped",
)

# Known-low-precision rules: holding up mana / a counter / a land is often
# CORRECT play. These flag for adjudication, never for direct verdicts, and
# the summary reports counts split by precision class.
LOW_PRECISION_FLAGS = frozenset((
    "durdle_pass_with_castable", "land_drop_skipped",
    "counter_held_while_dying"))

# Rules whose firing condition is engine-verifiable from the prompt snapshot
# alone in most cases (EVAL-DESIGN S4.3): the ONLY rules the textlane-verified
# adjudication method in blunder_report.py accepts.
TEXTLANE_VERIFIED_FLAGS = frozenset((
    "lethal_available_passed", "attack_declined_with_lethal",
    "forced_block_declined", "chump_free_lethal_block_skipped"))

_DURDLE_CAVEAT = ("WEAK: holding up mana for Daze/Spell Pierce/Force or "
                  "sandbagging a threat is often CORRECT tempo play, so a "
                  "nonzero durdle_rate is not a blunder count.")
_COUNT_ENCODED_CAVEAT = ("[COUNT-ENCODED CAVEAT: the harness logs `choice` as "
                         "the SIZE of the chosen subset for discard rows, not "
                         "an index; options[choice] is a positional proxy "
                         "only]")


def _chosen(d):
    """Normalized chosen-option text via process_metrics.chosen_option_text:
    options[choice] normalized, or '\\x00PASS' for the -1 sentinel, or None."""
    return chosen_option_text(d)


def _chosen_is_pass(d):
    c = _chosen(d)
    return isinstance(c, str) and (c == "\x00PASS" or c.startswith("PASS"))


def _opts(d):
    o = d.get("options")
    return o if isinstance(o, list) else []


def _header_active_is_seat(d):
    """True when the prompt's '== Turn N PHASE (active: <seat>) ==' header says
    the decision seat is the active player (own turn)."""
    prompt = d.get("prompt") or ""
    first = prompt.split("\n", 1)[0]
    return ("(active: %s)" % (d.get("seat") or "")) in first


def _event_lines(prompt):
    idx = prompt.find("RECENT EVENTS")
    if idx == -1:
        return []
    out = []
    for line in prompt[idx:].splitlines():
        if line.startswith("  - "):
            out.append(line[4:])
    return out


def _hand_entry_shape(hand_line, name):
    """Classify a card name against the YOU hand line: 'land' (present, no
    '{'-cost and no P/T after the name), 'threat' (P/T present), 'other'
    (cost but no P/T), or None (name not found). Precision limitation: card
    names containing ', ' make the hand line ambiguous to split, so we match
    the name and inspect only the text immediately following it."""
    i = hand_line.find(name)
    if i == -1:
        return None
    tail = hand_line[i + len(name):]
    m = re.match(r"\s*((?:\d+)?(?:\{[^{}]+\})*)\s*(\d+/\d+)?", tail)
    cost = (m.group(1) or "").strip() if m else ""
    pt = m.group(2) if m else None
    if pt:
        return "threat"
    if cost:
        return "other"
    return "land"


def _rule_lethal_available_passed(d, game, ctx):
    """Rule 1: check_lethal_now fires and the chosen action is PASS.
    Precision limitation: castable-burn totals are color-blind and blockers
    are not modeled, so 'lethal' is a heuristic projection (mine_puzzles
    documents the same limit) — adjudication, not this rule, is the verdict."""
    if d.get("type") not in ("play", "attackers"):
        return None
    if not _chosen_is_pass(d):
        return None
    ev = check_lethal_now(d)
    if ev is None:
        return None
    return ev


def _rule_attack_declined_with_lethal(d, game, ctx):
    """Rule 2: attackers decision with lethal on the table, zero attackers
    declared (count-encoded choice == 0). Precision limitation: lethality
    ignores blockers the opponent could still assign."""
    if d.get("type") != "attackers" or d.get("choice") != 0:
        return None
    ev = check_lethal_now(d)
    if ev is None:
        return None
    return ev + " [declared 0 attackers]"


def _rule_forced_block_declined(d, game, ctx):
    """Rule 3: blockers decision where unblocked incoming power >= own life,
    zero blocks assigned (choice == 0). Precision limitation: prevention/fog
    effects or damage-order tricks invisible to the text are not modeled."""
    if d.get("type") != "blockers" or d.get("choice") != 0:
        return None
    ev = check_forced_block(d)
    if ev is None:
        return None
    return ev + " [assigned 0 blocks]"


def _rule_save_vs_lethal_passed(d, game, ctx):
    """Rule 4: play decision while opp board power >= own life, a CAST option
    existed, chose PASS. Precision limitation: the castable spell may not
    actually save (it could be a cantrip); low-but-nonzero precision by
    design — the rollout adjudicates."""
    if d.get("type") != "play" or not _chosen_is_pass(d):
        return None
    if not any(isinstance(o, str) and o.startswith("CAST") for o in _opts(d)):
        return None
    ev = check_save_vs_lethal(d)
    if ev is None:
        return None
    return ev + " [passed with a castable spell]"


def _rule_durdle_pass_with_castable(d, game, ctx):
    """Rule 5: process_metrics' durdle counter promoted to a flag — main-phase
    play with >= 1 castable spell that chose a non-CAST action. LOW PRECISION;
    the process_metrics caveat is carried verbatim in the evidence string
    (holding up mana / sandbagging a threat is often CORRECT tempo play)."""
    if d.get("type") != "play" or d.get("phase") not in ("MAIN1", "MAIN2"):
        return None
    if not any(isinstance(o, str) and o.startswith("CAST") for o in _opts(d)):
        return None
    c = _chosen(d)
    if isinstance(c, str) and c.startswith("CAST"):
        return None
    return "castable spell available, chose %r. %s" % (
        (c if c != "\x00PASS" else "PASS"), _DURDLE_CAVEAT)


def _rule_land_drop_skipped(d, game, ctx):
    """Rule 6: own main phase, a PLAY LAND option offered, chose PASS, and no
    '<seat> played <Land>' RECENT EVENTS line since the last
    'Turn <N> (<seat>)' marker (i.e. the land drop was still available and
    was skipped). LOW PRECISION: skipping a land drop is occasionally correct
    (Daze/Exploration lines, hiding information)."""
    if d.get("type") != "play" or d.get("phase") not in ("MAIN1", "MAIN2"):
        return None
    if not _header_active_is_seat(d):
        return None
    if not any(isinstance(o, str) and o.startswith("PLAY LAND")
               for o in _opts(d)):
        return None
    if not _chosen_is_pass(d):
        return None
    if ctx.get("lands_played_this_turn", 0) > 0:
        return None
    seat = str(d.get("seat") or "")
    events = _event_lines(d.get("prompt") or "")
    turn_marker = re.compile(r"^Turn \d+ \(%s\)$" % re.escape(seat))
    played_re = re.compile(r"^%s played " % re.escape(seat))
    last_marker = -1
    for i, line in enumerate(events):
        if turn_marker.match(line):
            last_marker = i
    for line in events[last_marker + 1:]:
        if played_re.match(line):
            return None
    return ("PLAY LAND offered, chose PASS, no land played since last turn "
            "marker (events scanned: %d)" % len(events))


def _rule_discard_only_land(d, game, ctx):
    """Rule 7: discard where exactly one candidate is land-shaped in the YOU
    hand line (no '{' cost, no P/T) and options[choice] is it. Precision
    limitation: discard `choice` is COUNT-ENCODED by the harness (subset size,
    not an index — the subset itself is unlogged), so options[choice] is a
    positional proxy only; and hand-line shape classification is
    name-substring based, so split cards / adventure faces can misclassify."""
    if d.get("type") != "discard":
        return None
    opts = _opts(d)
    ch = d.get("choice")
    if not isinstance(ch, int) or isinstance(ch, bool) or not (0 <= ch < len(opts)):
        return None
    prompt = d.get("prompt") or ""
    you_part, _ = split_you_opp(prompt)
    m = _HAND_RE.search(you_part)
    if not m:
        return None
    hand_line = m.group(1)
    shapes = [_hand_entry_shape(hand_line, o) if isinstance(o, str) else None
              for o in opts]
    land_idxs = [i for i, s in enumerate(shapes) if s == "land"]
    if len(land_idxs) != 1 or ch != land_idxs[0]:
        return None
    return "discarded the only land-shaped card %r %s" % (
        opts[ch], _COUNT_ENCODED_CAVEAT)


def _rule_discard_only_threat(d, game, ctx):
    """Rule 8: discard where exactly one candidate carries a P/T in the YOU
    hand line and options[choice] is it. Same count-encoding and name-match
    precision limitations as rule 7."""
    if d.get("type") != "discard":
        return None
    opts = _opts(d)
    ch = d.get("choice")
    if not isinstance(ch, int) or isinstance(ch, bool) or not (0 <= ch < len(opts)):
        return None
    prompt = d.get("prompt") or ""
    you_part, _ = split_you_opp(prompt)
    m = _HAND_RE.search(you_part)
    if not m:
        return None
    hand_line = m.group(1)
    shapes = [_hand_entry_shape(hand_line, o) if isinstance(o, str) else None
              for o in opts]
    threat_idxs = [i for i, s in enumerate(shapes) if s == "threat"]
    if len(threat_idxs) != 1 or ch != threat_idxs[0]:
        return None
    return "discarded the only creature-shaped card %r %s" % (
        opts[ch], _COUNT_ENCODED_CAVEAT)


def _rule_burn_to_creature_when_face_lethal(d, game, ctx):
    """Rule 9: target/targets decision for a known DAMAGE_SPELLS burn spell
    where targeting the opponent's face would be lethal (opp_life <= damage),
    a bare player-name option existed, and a creature (P/T-bearing option)
    was chosen instead. Precision limitation: DAMAGE_SPELLS is a tiny curated
    map (unknown burn spells are invisible), and lifelink/prevention are not
    modeled."""
    if d.get("type") not in ("target", "targets"):
        return None
    opts = _opts(d)
    names = ctx.get("player_names") or set()
    if not any(isinstance(o, str) and o.strip() in names for o in opts):
        return None
    prompt = d.get("prompt") or ""
    stack_m = _STACK_RE.search(prompt)
    dec_idx = prompt.find("DECISION:")
    searchable = ((stack_m.group(1) if stack_m else "")
                  + "\x00" + (prompt[dec_idx:] if dec_idx != -1 else "")
                  + "\x00" + " ".join(o for o in opts if isinstance(o, str)))
    dmg = 0
    matched = []
    for name, spell_dmg in DAMAGE_SPELLS.items():
        if name in searchable:
            matched.append(name)
            dmg = max(dmg, spell_dmg)
    if not matched:
        return None
    opp_life = _first_int(_OPP_LIFE_RE, prompt)
    if opp_life is None or opp_life > dmg:
        return None
    c = _chosen(d)
    if not isinstance(c, str) or not _PT_RE.search(c):
        return None
    return ("burn spell %s (dmg=%d) >= opp_life=%d but targeted creature %r "
            "instead of the player" % (",".join(matched), dmg, opp_life, c))


def _rule_counter_held_while_dying(d, game, ctx):
    """Rule 10: play decision while check_save_vs_lethal fires, a castable
    ANSWER_CARDS interaction option existed, chose PASS. LOW PRECISION:
    holding a counter for the actual kill spell is often correct — this only
    nominates for adjudication."""
    if d.get("type") != "play" or not _chosen_is_pass(d):
        return None
    ev = check_save_vs_lethal(d)
    if ev is None:
        return None
    matched = []
    for o in _opts(d):
        if not (isinstance(o, str) and o.startswith("CAST")):
            continue
        for card in ANSWER_CARDS:
            if card in o:
                matched.append(card)
    if not matched:
        return None
    return ("%s; castable answer(s) %s held while dying [LOW-PRECISION: "
            "holding interaction is often correct]" % (ev, ",".join(matched)))


def _rule_chump_free_lethal_block_skipped(d, game, ctx):
    """Rule 11: forced-block situation (incoming >= own life) where at least
    one own blocker's toughness exceeds the largest incoming power (it blocks
    and SURVIVES — not even a chump), yet zero blocks were assigned.
    Precision limitation: deathtouch/trample/first-strike are not visible in
    the P/T text, so 'survives' is a P/T-only projection."""
    if d.get("type") != "blockers" or d.get("choice") != 0:
        return None
    ev = check_forced_block(d)
    if ev is None:
        return None
    prompt = d.get("prompt") or ""
    m = _INCOMING_RE.search(prompt)
    if not m:
        return None
    incoming_powers = [int(p) for p, _t, _tags in _PT_RE.findall(m.group(1))]
    if not incoming_powers:
        return None
    max_in = max(incoming_powers)
    survivors = []
    for o in _opts(d):
        if not isinstance(o, str):
            continue
        pm = _PT_RE.search(o)
        if pm and int(pm.group(2)) > max_in:
            survivors.append(o)
    if not survivors:
        return None
    return ("%s; blocker(s) %s would survive the biggest attacker (power %d) "
            "yet 0 blocks assigned" % (ev, survivors, max_in))


_RULE_FNS = {
    "lethal_available_passed": _rule_lethal_available_passed,
    "attack_declined_with_lethal": _rule_attack_declined_with_lethal,
    "forced_block_declined": _rule_forced_block_declined,
    "save_vs_lethal_passed": _rule_save_vs_lethal_passed,
    "durdle_pass_with_castable": _rule_durdle_pass_with_castable,
    "land_drop_skipped": _rule_land_drop_skipped,
    "discard_only_land": _rule_discard_only_land,
    "discard_only_threat": _rule_discard_only_threat,
    "burn_to_creature_when_face_lethal": _rule_burn_to_creature_when_face_lethal,
    "counter_held_while_dying": _rule_counter_held_while_dying,
    "chump_free_lethal_block_skipped": _rule_chump_free_lethal_block_skipped,
}


def flag_decision(d, game, ctx):
    """Run every text rule against one decision. -> [{'flag': id,
    'evidence': str}], possibly empty, in FLAG_RULES order. Pure: no I/O.
    Rows without a prompt (mine_puzzles.eligible False) never text-flag —
    there is no state snapshot to reason over."""
    if not eligible(d):
        return []
    out = []
    for rid in FLAG_RULES:
        ev = _RULE_FNS[rid](d, game, ctx)
        if ev is not None:
            out.append({"flag": rid, "evidence": ev})
    return out


# ------------------------------------------------------------------- lanes

LANE_PRIORITY = ("shadow-disagree", "text-flag", "audit-sample")


def lanes_for(flags, shadow_choice, choice, in_audit):
    """Full lane membership, in priority order."""
    lanes = []
    if shadow_choice is not None and shadow_choice != choice:
        lanes.append("shadow-disagree")
    if flags:
        lanes.append("text-flag")
    if in_audit:
        lanes.append("audit-sample")
    return lanes


def lane_for(flags, shadow_choice, choice, in_audit):
    """Highest-priority lane ('shadow-disagree' > 'text-flag' >
    'audit-sample') or None. The emitted row carries this as flag_lane and
    the FULL membership as the additive 'lanes' field, so per-lane summary
    rates are computed over membership, never double-counting flag_lane."""
    lanes = lanes_for(flags, shadow_choice, choice, in_audit)
    return lanes[0] if lanes else None


def audit_sample_ids(row_ids, rate=0.05, sample_seed=20260728):
    """Deterministic uniform sample of row ids (mine_puzzles._sample_hash
    style): include rid iff the first 8 hex chars of
    sha256('%d:%s' % (sample_seed, rid)) interpret below rate * 2**32.
    Sorted, reproducible, independent of input order and of process."""
    threshold = rate * 4294967296.0  # 2**32
    picked = []
    for rid in row_ids:
        h = hashlib.sha256(("%d:%s" % (sample_seed, rid)).encode("utf-8"))
        if int(h.hexdigest()[:8], 16) < threshold:
            picked.append(rid)
    return sorted(set(picked))


# ------------------------------------------------- deck-archetype resolution

_PLAN_INDEX = None
_NAME_INDEX = None


def load_plan_index():
    """{plan_text.strip(): slug} over eval/decks/*.plan (lazy, cached).
    Degrades to {} if the files are unreadable — deck_slug becomes None."""
    global _PLAN_INDEX
    if _PLAN_INDEX is None:
        idx = {}
        try:
            for fp in sorted((REPO / "eval" / "decks").glob("*.plan")):
                try:
                    idx[fp.read_text(encoding="utf-8").strip()] = fp.stem
                except OSError:
                    continue
        except OSError:
            pass
        _PLAN_INDEX = idx
    return _PLAN_INDEX


def _load_name_index():
    """{display name: slug} from eval/holdouts.json decks (lazy, cached)."""
    global _NAME_INDEX
    if _NAME_INDEX is None:
        idx = {}
        try:
            h = json.loads((REPO / "eval" / "holdouts.json")
                           .read_text(encoding="utf-8"))
            for slug, info in (h.get("decks") or {}).items():
                name = (info or {}).get("name")
                if name:
                    idx[name] = slug
        except (OSError, ValueError):
            pass
        _NAME_INDEX = idx
    return _NAME_INDEX


def resolve_deck_slug(game, ident):
    """Contract S4 resolution order: 1. exact plan-text match (plan_<ident> is
    reliable; deck_<ident> is NOT — it holds model names in tournament-1),
    2. display-name match via holdouts.json, 3. None. Delegates to
    plan_adherence.resolve_deck_slug when that module exists."""
    if _t4_resolve_deck_slug is not None:
        return _t4_resolve_deck_slug(game, ident)
    slug = load_plan_index().get((game.get("plan_%s" % ident) or "").strip())
    if slug:
        return slug
    return _load_name_index().get(game.get("deck_%s" % ident))


def seat_to_ident(game, seat_name):
    """'a'/'b'/None for a seat display name; 'a' wins a mirror-name tie
    (replay_match.seat_plan()'s documented convention)."""
    if seat_name == game.get("deck_a"):
        return "a"
    if seat_name == game.get("deck_b"):
        return "b"
    return None


# ---------------------------------------------------------------- statistics
# Private copy per the shared contract S5 (byte-identical in t2/t4/t5;
# integration hoists it into aggregate.py). Pinned reference test in
# eval/test_blunder_flags.py detects drift.

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


# --------------------------------------------------------------------- scan

def _game_key(run_dir, game_file):
    return "%s|%s" % (run_dir, game_file)


def scan(entries, model=None, audit_rate=0.05, sample_seed=20260728):
    """entries: load_complete_games() dicts ({"file","game","decisions"}),
    each optionally carrying a caller-injected "run_dir" key (blunder_report
    injects it; absent -> ""). -> (flagged_rows, stats).

    flagged_rows: one dict per scoreable decision that is in ANY lane,
    carrying everything blunder_report needs (see keys below). stats carries
    corpus-wide counters INCLUDING the per-game scoreable/flagged counts the
    summary's clustered denominators need, and the vacuous-shadow-lane note
    (a lane with zero shadow rows is REPORTED as unmeasured, never as a
    measured 0% disagreement rate)."""
    flagged_rows = []
    skip_reasons = Counter()
    flag_counts = Counter()
    per_game_scoreable = {}
    per_game_flagged = {}
    prec_class = Counter()
    n_games = n_decisions = n_scoreable = 0
    n_shadow_available = n_shadow_disagree = n_audit = n_text_flagged = 0

    # Pass 1: enumerate scoreable rids for the deterministic audit sample.
    rids = []
    for entry in entries:
        run_dir = entry.get("run_dir") or ""
        for di, d in enumerate(entry.get("decisions") or []):
            ok, _reason = scoreable(d)
            if ok:
                rids.append("%s|%s|%d" % (run_dir, entry.get("file"), di))
    audit_ids = set(audit_sample_ids(rids, rate=audit_rate,
                                     sample_seed=sample_seed))

    # Pass 2: flag.
    for entry in entries:
        g = entry.get("game") or {}
        run_dir = entry.get("run_dir") or ""
        game_file = entry.get("file")
        n_games += 1
        gkey = _game_key(run_dir, game_file)
        per_game_scoreable.setdefault(gkey, 0)
        per_game_flagged.setdefault(gkey, 0)
        player_names = set()
        for ident in ("a", "b"):
            name = g.get("deck_%s" % ident)
            if name is not None:
                player_names.add(str(name))
        ctx_by_seat = {}
        for di, d in enumerate(entry.get("decisions") or []):
            n_decisions += 1
            seat = str(d.get("seat") or "")
            ctx = ctx_by_seat.setdefault(seat, {
                "lands_played_this_turn": 0, "turn": None,
                "player_names": player_names})
            turn = d.get("turn")
            if turn != ctx["turn"]:
                ctx["turn"] = turn
                ctx["lands_played_this_turn"] = 0
            ok, reason = scoreable(d)
            if not ok:
                skip_reasons[reason] += 1
                # ctx upkeep even on non-scoreable rows (fallback land drops).
                c = chosen_option_text(d)
                if isinstance(c, str) and c.startswith("PLAY LAND"):
                    ctx["lands_played_this_turn"] += 1
                continue
            n_scoreable += 1
            per_game_scoreable[gkey] += 1

            flags = flag_decision(d, g, ctx)
            shadow = d.get("shadow_choice")
            if shadow is not None:
                n_shadow_available += 1
                if shadow != d.get("choice"):
                    n_shadow_disagree += 1
            rid = "%s|%s|%d" % (run_dir, game_file, di)
            in_audit = rid in audit_ids
            if in_audit:
                n_audit += 1
            lanes = lanes_for(flags, shadow, d.get("choice"), in_audit)
            flag_lane = lanes[0] if lanes else None

            if flags:
                n_text_flagged += 1
                per_game_flagged[gkey] += 1
                for f in flags:
                    flag_counts[f["flag"]] += 1
                if any(f["flag"] not in LOW_PRECISION_FLAGS for f in flags):
                    prec_class["high"] += 1
                else:
                    prec_class["low"] += 1
            elif "shadow-disagree" in lanes:
                per_game_flagged[gkey] += 1

            # ctx upkeep AFTER flagging (rule 6 must see the pre-choice state).
            c = chosen_option_text(d)
            if isinstance(c, str) and c.startswith("PLAY LAND"):
                ctx["lands_played_this_turn"] += 1

            if flag_lane is None:
                continue

            ident = seat_to_ident(g, seat)
            if model is not None:
                row_model = model
            elif ident is not None:
                row_model = (g.get("served_smart_%s" % ident)
                             or g.get("smart_%s" % ident) or "unknown")
            else:
                row_model = "unknown"
            address = {
                "engine_sha": g.get("engine_sha"),
                "overlay_sha": g.get("overlay_sha"),
                "deck_a": g.get("deck_a"), "deck_b": g.get("deck_b"),
                "controllers": "%s/%s" % (g.get("controller_a"),
                                          g.get("controller_b")),
                "seed": g.get("seed"), "swap": bool(g.get("swap", False)),
                "decision_index": di, "decision_type": d.get("type"),
                "seat": seat, "turn": d.get("turn"), "game_file": game_file,
            }
            for pk in ("plan_a", "plan_b"):
                if g.get(pk) is not None:
                    address[pk] = g.get(pk)
            row = {
                "rid": rid,
                "run_dir": run_dir,
                "game_file": game_file,
                "decision_index": di,
                "seat": seat,
                "model": row_model,
                "turn": d.get("turn") if isinstance(d.get("turn"), int)
                        and not isinstance(d.get("turn"), bool) else 0,
                "decision_type": d.get("type"),
                "n_options": d.get("n_options"),
                "choice": d.get("choice"),
                "options": d.get("options"),
                "prompt": d.get("prompt"),
                "flags": [f["flag"] for f in flags],
                "evidence": [f["evidence"] for f in flags],
                "flag_lane": flag_lane,
                "lanes": lanes,
                "shadow_choice": shadow,
                "address": address,
                "deck_slug": (resolve_deck_slug(g, ident)
                              if ident is not None else None),
            }
            if flags:
                row["precision_class"] = (
                    "high" if any(f not in LOW_PRECISION_FLAGS
                                  for f in row["flags"]) else "low")
            flagged_rows.append(row)

    stats = {
        "n_games": n_games,
        "n_decisions": n_decisions,
        "n_scoreable": n_scoreable,
        "n_text_flagged": n_text_flagged,
        "n_audit": n_audit,
        "n_rows_emitted": len(flagged_rows),
        "n_shadow_rows_available": n_shadow_available,
        "n_shadow_disagree": n_shadow_disagree,
        "shadow_lane_note": (
            "shadow lane vacuous (no SHADOW_AI=1 corpus)"
            if n_shadow_available == 0 else None),
        "skip_reasons": dict(skip_reasons),
        "flag_counts": dict(flag_counts),
        "flagged_by_precision_class": {
            "high": prec_class.get("high", 0), "low": prec_class.get("low", 0)},
        "per_game_scoreable": per_game_scoreable,
        "per_game_flagged": per_game_flagged,
        "audit_rate": audit_rate,
        "sample_seed": sample_seed,
        "warnings": [],
    }
    return flagged_rows, stats
