#!/usr/bin/env python3
"""verify_keys.py — turn mined puzzle CANDIDATES into engine-adjudicated answer keys.

Reads puzzle-position.v1 candidate rows (eval/mine_puzzles.py output) and, per
row, either mints an answer_key (status="verified") or records exactly why it
could not (status stays "candidate" + additive `unverifiable_reason`). Two
lanes:

  LANE A — EXHAUSTIVE (pure, no engine, no I/O beyond the candidates file):
    lethal-now @ play only, single-spell face lethal: an option that CASTs a
    mine_puzzles.DAMAGE_SPELLS burn spell whose damage >= the prompt's OPP
    life, with no explicit creature target in the option text, scores 10; all
    others 0. Verified iff >=1 option scores 10 AND >=1 scores 0. When an
    engine driver is available, a single FORK_MODE=enumerate probe first
    re-reaches the position and the live type/seat/n_options/options must be
    byte-identical to the candidate's (else `replay_drift`); with --driver
    none the key is still minted but carries the caveat
    "unprobed_replay_address" — a key that was never re-reached is weaker
    evidence and says so.

  LANE B — ROLLOUT (engine or fixture): play/mulligan only (V1_FORCEABLE_TYPES
    — the only index-encoded types the fork forcer can steer; count-encoded
    types log a subset SIZE, not an index, so a per-option key is UNDEFINED
    for them). Preconditions, each with its own unverifiable_reason, then
    driver.rollout() and EVAL-DESIGN §3.3 scoring:
      score(o) = round_half_up(10 * (wp[o]-wp_min) / (wp_max-wp_min)),
      any option with wp[o] + 1.96*se[o] >= wp_max is forced to 10,
      gap gate: wp_max - wp_min >= max(threshold, Z*max(se)) where threshold
      is 0.15 for mulligan (EVAL-DESIGN §3.2's mulligan-specific bar) and
      0.20 otherwise and Z is --min-separation-z (default 2.0), else
      `rollout_noise_below_threshold`.

ROLLOUT EVIDENCE (additive, both outcomes). Whenever the driver actually
returns a measurement (ok=True), the row carries `rollout_evidence` — the
per-option WPs, their SEs, the raw win/loss/draw/invalid counts, the spread
(wp_max - wp_min) with a propagated SE, best / runner-up indices, a
`separation_z` diagnostic, and the exact gate numbers the decision used. It is
emitted for VERIFIED rows too, so spread is comparable across decided and
undecided positions, and it is written at the TOP LEVEL of the row — never
inside answer_key, which stays the verified key and only exists on
status="verified" rows. Without it, "the options genuinely tie" and "the
rollout returned garbage" (every replay invalid -> wp all None) are
indistinguishable from the artifact, and that is the only decision-relevance
diagnostic available before committing to a large generation block.
`rollout_evidence` is ABSENT when no measurement happened (driver missing,
precondition refused, probe drift): absent = never measured, present with
option_wp all-None = measured and useless, present with a small wp_spread =
measured and genuinely tied.

separation_z is a DIAGNOSTIC, not the gate: it is
(wp_best - wp_runner_up) / sqrt(se_best^2 + se_runner_up^2), while the gate
compares the full spread (max - min) against max(threshold, Z*max(se)). A
position can have a large separation_z and still be rejected (and vice versa);
`rollout_evidence.gate` records exactly what was compared.

v1 COVERAGE (the scope decision, do not widen — reported in the summary):
  lethal-now @ play          -> exhaustive lane
  lethal-now @ attackers     -> unverifiable: count_encoded_no_v1_rollout
  save-vs-lethal @ play      -> rollout lane
  forced-block @ blockers    -> unverifiable: count_encoded_no_v1_rollout
  free-win (any type)        -> unverifiable: non_empty_stack_v1_restriction
                                (by construction the stack is non-empty;
                                EVAL-DESIGN §3.1 forbids mid-stack forks in v1)
  mulligan                   -> rollout lane
  graded-midgame @ play      -> rollout lane
  graded-midgame @ attackers -> unverifiable: count_encoded_no_v1_rollout
A narrow honest key set beats a wide fake one.

unverifiable_reason tokens written to rows (documented superset of the scope
list — the extra tokens come from the exhaustive-lane adjudication itself):
  count_encoded_no_v1_rollout, non_empty_stack_v1_restriction,
  non_deterministic_source_game, replay_drift, engine_sha_drift,
  overlay_sha_drift, rollout_noise_below_threshold, no_engine_available,
  degenerate_all_correct, exhaustive_no_lethal_option, missing_prompt.
Driver-internal reasons that do not fit that enum (fixture_miss,
rollout_timeout, engine_error, dry_run, missing_deck_path, probe_failed,
force_not_honoured) are mapped to `no_engine_available` on the row and
preserved verbatim in the additive `driver_reason` field.

Usage:
  python3 eval/verify_keys.py CANDIDATES.jsonl --out POSITIONS.jsonl \\
      [--lane exhaustive|rollout|both] [--n-per-option 16] \\
      [--forge-home ~/code/forge] [--driver engine|fixture|none] \\
      [--fixture FILE.jsonl] [--max-positions N] [--jobs 1] \\
      [--gap-threshold 0.20] [--mulligan-gap-threshold 0.15] \\
      [--min-separation-z 2.0] \\
      [--summary summary.json] [--json] [--dry-run]

Gate knobs (all three were hard-coded constants before; the defaults below
reproduce the previous behaviour byte-for-byte, and the summary echoes the
values actually used in `thresholds`):
  --gap-threshold           0.20  absolute win-probability units [0,1]; the
                                  floor on wp_max - wp_min for non-mulligan.
  --mulligan-gap-threshold  0.15  same units, mulligan-only bar (EVAL-DESIGN
                                  §3.2).
  --min-separation-z        2.0   dimensionless multiple of the LARGEST
                                  per-option SE; the noise floor is
                                  Z*max(se) and the gate requires
                                  wp_max - wp_min >= max(threshold, Z*max(se)).
Raising any of them makes the lane stricter; lowering them makes it looser —
a lowered bar is a real weakening and the summary records it so an artifact
can never hide which bar it was verified under.

Default --driver is `none`: pure exhaustive lane, zero engine, zero egress —
runnable and testable with no Forge checkout. Nothing in this tool ever
touches the network.

Exit codes: 0 OK; 1 (unused — no declared gate here yet); 2 usage error /
nothing usable to measure; 3 INCOMPLETE (an engine driver was requested but
the engine could not be reached for at least one row that needed it).

WHAT WOULD MAKE THIS FAIL:
  * the exhaustive lane minting a key for a position where the burn spell is
    smaller than OPP life, where every option is lethal (degenerate), or
    where the stack is not empty — test_verify_keys.py exercises all three;
  * the rollout lane minting a key from a rollout whose WP gap the rollout
    cannot statistically distinguish (gap < max(threshold, Z*SE));
  * a REJECTED rollout row losing the measured evidence — rollout_evidence
    missing, or its option_wp emptied — which would make "genuine tie" and
    "rollout returned garbage" indistinguishable again;
  * rollout_evidence appearing INSIDE answer_key, or answer_key appearing on
    a status="candidate" row (a half-filled answer_key later misread as a key);
  * wp_spread / wp_spread_se / separation_z drifting from
    max-min / sqrt(se_hi^2+se_lo^2) / (best-runner_up)/sqrt(se^2+se^2) — the
    tests pin hand-worked numbers for all three;
  * a count-encoded row (attackers/blockers/discard/...) receiving a
    per-option key — undefined by construction, asserted never to happen;
  * dropping or repurposing any field the miner emitted (schemas are
    additive-only — the echo test asserts every input key survives);
  * rollout_key drifting from the canonical 9-field definition t5's copy
    pins byte-identically.
"""

import argparse
import datetime
import hashlib
import itertools
import json
import math
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent

import mine_puzzles as mp  # noqa: E402  (regex helpers + DAMAGE_SPELLS; import, don't re-derive)
from replay_match import decision_family  # noqa: E402
from aggregate import wilson_ci  # noqa: E402

# t1's prompt_contract.py may not have landed yet (parallel tracks). Guarded
# import: when present, every artifact is stamped with the real contract_sha;
# when absent, contract_sha is the literal "unknown" and the summary says so.
try:
    import prompt_contract
    HAVE_PROMPT_CONTRACT = True
except ImportError:
    prompt_contract = None
    HAVE_PROMPT_CONTRACT = False

# --------------------------------------------------------------- constants

EMPTY_STACK_TYPES = frozenset(("play", "attackers", "blockers", "mulligan"))
V1_FORCEABLE_TYPES = frozenset(("play", "mulligan"))
DETERMINISTIC_CONTROLLERS = frozenset(("floor", "random", "ai", "ai-log"))
ROLLOUT_CONTINUATION = "ai-log/ai-log"
DEFAULT_N_PER_OPTION = 16

# Gate knobs — formerly literals inside score_from_wp, now the CLI defaults.
# Units: the two gap thresholds are absolute win-probability units in [0,1];
# MIN_SEPARATION_Z is a dimensionless multiple of the largest per-option SE.
DEFAULT_GAP_THRESHOLD = 0.20
DEFAULT_MULLIGAN_GAP_THRESHOLD = 0.15
DEFAULT_MIN_SEPARATION_Z = 2.0

# wp_spread histogram: 20 fixed bins of width 0.05 over [0, 1]. Bin i covers
# [i*0.05, (i+1)*0.05); the last bin is closed so spread == 1.0 lands in it.
SPREAD_BIN_WIDTH = 0.05
SPREAD_N_BINS = 20

SEED_FN = "mix64(base_seed ^ (i*C1) ^ (j*C2))"
EMPTY_STACK_MARK = "stack (top first): (empty)"

# How the engine re-randomizes hidden information at the fork point
# (LlmSpikeFork.determinizeHiddenZones / FORK_ROLLOUT_DETERMINIZE).
#
#   "library" -- re-deal every player's library order from the rollout seed.
#                The WP is then the win probability conditional on the
#                OBSERVABLE state, marginalized over library orders.
#   "none"    -- the pre-fix behavior, kept ONLY so the defect below stays
#                reproducible as evidence. Measured across 6 positions and 18
#                option-arms at every n tried, every arm returned exactly 0/n
#                or n/n: reseeding MyRandom mid-game cannot re-deal a library
#                whose order was fixed at game start, so all n replays played
#                the identical line and n=16 bought n=1 of information.
#
# This is provenance, not decoration: a key minted under "none" is a
# single-deal number and is NOT comparable to one minted under "library".
# It is recorded on every result and on every rollout_evidence block.
DETERMINIZE_MODES = ("library", "none")
DEFAULT_DETERMINIZE = "library"

#: Caveat slug stamped on every key whose WP came from a determinized rollout.
#: It REPLACES single_deal_hidden_info rather than joining it -- the two are
#: mutually exclusive claims about the same number, and emitting both would
#: leave the reader unable to tell which one is true. Meaning: library order
#: was re-dealt at the fork point, so the WP marginalizes over library orders
#: instead of measuring one deal. Residual approximation: a player who
#: legitimately knew its top card (post-Brainstorm/scry) loses that knowledge,
#: and opponents' HANDS are not re-dealt -- partial determinization, not full
#: ISMCTS.
DETERMINIZED_CAVEAT = "determinized_library_hidden_info"

#: The pre-fix claim, still correct for determinize="none" rollouts.
SINGLE_DEAL_CAVEAT = "single_deal_hidden_info"

# Combo archetypes whose graded/rollout keys carry rollout_policy_caveat
# (EVAL-DESIGN §3.5: the Forge-AI continuation policy is weak at combo, so
# combo-line WPs are underestimated).
COMBO_SLUGS = frozenset((
    "ant-storm", "cephalid-breakfast", "doomsday", "food-chain", "painter",
    "sneak-and-show", "grixis-reanimator", "infect", "elves",
))

VERIFIED_BY_TOOL = "verify_keys.v1"

COVERAGE_NOTE = (
    "v1 scope: per-option answer keys are DEFINED only for index-encoded "
    "decision types; verify_keys mints keys for lethal-now@play (exhaustive) "
    "and save-vs-lethal@play / mulligan / graded-midgame@play (rollout). "
    "attackers/blockers and every count-encoded type log a subset SIZE, not "
    "an index (no per-option object exists), and free-win positions have a "
    "non-empty stack (EVAL-DESIGN 3.1 forbids mid-stack forking in v1) — all "
    "such rows stay status=candidate with an explicit unverifiable_reason. "
    "A narrow honest key set beats a wide fake one."
)


def _utcnow():
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _fmt3(x):
    return "n/a" if x is None else "%.3f" % x


def rollout_key(address):
    """Stable 16-hex id of a replay address. CANONICAL DEFINITION — t5 keeps a
    byte-identical private copy; test_verify_keys.py and t5's
    test_blunder_report.py pin the two against each other."""
    payload = "|".join(str(address.get(k)) for k in
                       ("engine_sha", "overlay_sha", "deck_a", "deck_b",
                        "controllers", "seed", "swap", "decision_index",
                        "game_file"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def metric_block(wins, n, method="wilson"):
    est = (wins / n) if n else None
    lo, hi = wilson_ci(wins, n)
    se = math.sqrt(est * (1.0 - est) / n) if n and est is not None else None
    return {"est": est, "se": se, "ci95": [lo, hi], "n": n,
            "n_clusters": None, "method": method}


def contract_sha_or_unknown():
    if HAVE_PROMPT_CONTRACT:
        try:
            return prompt_contract.contract_sha()
        except Exception:  # a half-landed module must not kill verification
            return "unknown"
    return "unknown"


# ------------------------------------------------- deck-slug / path resolution

def _load_holdouts():
    try:
        with open(str(REPO / "eval" / "holdouts.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def deck_name_index():
    """display name -> (slug, decklist path str). From eval/holdouts.json."""
    idx = {}
    data = _load_holdouts()
    for slug, meta in sorted((data.get("decks") or {}).items()):
        name = meta.get("name")
        if not name:
            continue
        fpath = meta.get("file")
        idx[name] = (slug, str(REPO / fpath) if fpath else None)
    return idx


def slug_for_deck_name(name, name_idx=None):
    idx = deck_name_index() if name_idx is None else name_idx
    ent = idx.get(name)
    return ent[0] if ent else None


# ----------------------------------------------------------- exhaustive lane

def adjudicate_exhaustive(cand):
    """PURE rule adjudication for lethal-now @ play from prompt text + options
    only. -> (option_scores or None, reason or None, evidence str).

    Conservative by design: an option scores 10 only when it CASTs a known
    DAMAGE_SPELLS burn spell whose face damage >= OPP life AND the option text
    carries no explicit creature reference (no P/T annotation, no '->' target
    arrow) — i.e. the burn can plausibly go to the face. Everything else
    (including PASS) scores 0."""
    prompt = cand.get("prompt") or ""
    options = cand.get("options") or []
    if not prompt or len(options) < 2:
        return None, "missing_prompt", ""
    if EMPTY_STACK_MARK not in prompt:
        return None, "non_empty_stack_v1_restriction", "stack not empty in prompt"
    opp_life = mp._first_int(mp._OPP_LIFE_RE, prompt)
    if not opp_life:
        return None, "exhaustive_no_lethal_option", "no OPP life parsed from prompt"
    scores = []
    lethal_names = []
    for opt in options:
        s = 0
        if opt.startswith("CAST "):
            for name in sorted(mp.DAMAGE_SPELLS):
                if name in opt:
                    dmg = mp.DAMAGE_SPELLS[name]
                    face_ok = (mp._PT_RE.search(opt) is None) and ("->" not in opt)
                    if dmg >= opp_life and face_ok:
                        s = 10
                        lethal_names.append(name)
                    break
        scores.append(s)
    n_ten = sum(1 for s in scores if s == 10)
    if n_ten == 0:
        return None, "exhaustive_no_lethal_option", (
            "no castable single burn spell covers opp_life=%d" % opp_life)
    if n_ten == len(scores):
        return None, "degenerate_all_correct", (
            "every option is face-lethal — not a puzzle")
    ev = "single-spell face lethal: %s >= opp_life=%d" % (
        ",".join(sorted(set(lethal_names))), opp_life)
    return scores, None, ev


# ------------------------------------------------------------ rollout scoring

def base_threshold_for(category, gap_threshold=DEFAULT_GAP_THRESHOLD,
                       mulligan_gap_threshold=DEFAULT_MULLIGAN_GAP_THRESHOLD):
    """The category-specific absolute WP bar (EVAL-DESIGN 3.2/3.3)."""
    return mulligan_gap_threshold if category == "mulligan" else gap_threshold


def required_gap(ses, category, gap_threshold=DEFAULT_GAP_THRESHOLD,
                 mulligan_gap_threshold=DEFAULT_MULLIGAN_GAP_THRESHOLD,
                 min_separation_z=DEFAULT_MIN_SEPARATION_Z):
    """max(category bar, Z*max(se)) — the spread a rollout must show to decide.
    -> (required, base_threshold, noise_floor); noise_floor None when no SE."""
    base = base_threshold_for(category, gap_threshold, mulligan_gap_threshold)
    usable = [s for s in (ses or []) if s is not None]
    if not usable:
        return base, base, None
    floor = min_separation_z * max(usable)
    return max(base, floor), base, floor


def score_from_wp(option_wp, option_wp_se, category,
                  gap_threshold=DEFAULT_GAP_THRESHOLD,
                  mulligan_gap_threshold=DEFAULT_MULLIGAN_GAP_THRESHOLD,
                  min_separation_z=DEFAULT_MIN_SEPARATION_Z):
    """EVAL-DESIGN 3.3 mapping. -> (option_scores or None, reason or None).

    Gap gate: wp_max - wp_min >= max(threshold, Z*max(se)); threshold 0.15
    for mulligan (3.2's category-specific bar), 0.20 otherwise, Z 2.0 —
    all three are the CLI defaults, and calling this with three positional
    args reproduces the pre-flag behaviour exactly. round is HALF-UP
    (floor(x+0.5)) so scores don't depend on Python's banker's rounding.
    Options whose 95% CI overlaps wp_max are forced to 10 (never punish ties
    the rollout cannot distinguish)."""
    if not option_wp or any(w is None for w in option_wp):
        return None, "rollout_noise_below_threshold"
    ses = [s for s in (option_wp_se or []) if s is not None]
    if not ses:
        return None, "rollout_noise_below_threshold"
    wp_min = min(option_wp)
    wp_max = max(option_wp)
    gap = wp_max - wp_min
    req, _base, _floor = required_gap(ses, category, gap_threshold,
                                      mulligan_gap_threshold, min_separation_z)
    # gap <= 0 is a total tie: never divisible, never decision-relevant, and
    # the only way a zeroed-out threshold could reach the division below.
    if gap <= 0.0 or gap < req:
        return None, "rollout_noise_below_threshold"
    scores = []
    for i, w in enumerate(option_wp):
        scores.append(int(math.floor(10.0 * (w - wp_min) / gap + 0.5)))
    for i, w in enumerate(option_wp):
        se = option_wp_se[i] if i < len(option_wp_se or []) else None
        if se is not None and w + 1.96 * se >= wp_max:
            scores[i] = 10
    return scores, None


# ------------------------------------------------------------ rollout evidence

def _rank_indices(option_wp):
    """Indices sorted by WP descending, ties broken by lowest index (stable
    and deterministic — two options at the same WP always rank the same way)."""
    return sorted(range(len(option_wp)), key=lambda i: (-option_wp[i], i))


def _se_of_difference(se_a, se_b):
    """SE of (wp_a - wp_b) treating the two rollout batches as independent
    (they are: disjoint seed streams). None if either SE is missing."""
    if se_a is None or se_b is None:
        return None
    return math.sqrt(se_a * se_a + se_b * se_b)


def build_rollout_evidence(result, decided, reason, category,
                           gap_threshold=DEFAULT_GAP_THRESHOLD,
                           mulligan_gap_threshold=DEFAULT_MULLIGAN_GAP_THRESHOLD,
                           min_separation_z=DEFAULT_MIN_SEPARATION_Z):
    """The measured rollout, retained on the row WHATEVER the verdict.

    Emitted for verified and unverifiable rows alike so wp_spread is
    comparable across them. Every derived number degrades to None rather than
    raising: an all-invalid rollout (option_wp all None) must still produce a
    row that SAYS SO instead of vanishing. `separation_z` is a diagnostic on
    best-vs-runner-up; the gate block records the spread comparison that
    actually decided the row."""
    wp = list(result.get("option_wp") or [])
    se = list(result.get("option_wp_se") or [])
    n_opt = result.get("n_options")
    ev = {
        "schema": "rollout-evidence.v1",
        "option_wp": wp,
        "option_wp_se": se,
        "option_wins": list(result.get("option_wins") or []),
        "option_losses": list(result.get("option_losses") or []),
        "option_draws": list(result.get("option_draws") or []),
        "option_invalid": list(result.get("option_invalid") or []),
        "n_per_option": result.get("n_per_option"),
        "n_options": n_opt if n_opt is not None else len(wp),
        "wp_spread": None,
        "wp_spread_se": None,
        "best_index": None,
        "runner_up_index": None,
        "separation_z": None,
        "continuation": result.get("continuation"),
        "seed_fn": result.get("seed_fn"),
        # Provenance, not decoration: a "none" WP is a single-deal number and
        # is not comparable to a "library" one. See DETERMINIZE_MODES.
        "determinize": result.get("determinize"),
        "engine_sha": result.get("engine_sha"),
        "overlay_sha": result.get("overlay_sha"),
        "decided": bool(decided),
        "reason": reason,
    }
    req, base, floor = required_gap(se, category, gap_threshold,
                                    mulligan_gap_threshold, min_separation_z)
    ev["gate"] = {"base_threshold": base, "min_separation_z": min_separation_z,
                  "noise_floor": floor, "required_gap": req,
                  "measurable": floor is not None}
    if not wp or any(w is None for w in wp):
        # measured, but unusable — kept deliberately: option_invalid above is
        # what tells the reader the forcer/replay failed rather than tied.
        return ev
    order = _rank_indices(wp)
    best = order[0]
    worst = order[-1]
    ev["best_index"] = best
    ev["runner_up_index"] = order[1] if len(order) > 1 else None
    ev["wp_spread"] = wp[best] - wp[worst]
    ev["wp_spread_se"] = _se_of_difference(
        se[best] if best < len(se) else None,
        se[worst] if worst < len(se) else None)
    ru = ev["runner_up_index"]
    if ru is not None:
        denom = _se_of_difference(se[best] if best < len(se) else None,
                                  se[ru] if ru < len(se) else None)
        if denom:  # None or exactly 0.0 -> undefined, not infinite
            ev["separation_z"] = (wp[best] - wp[ru]) / denom
    return ev


# --------------------------------------------------------------- the drivers

def _fail_result(key, reason):
    return {"schema": "rollout-result.v1", "ok": False, "reason": reason,
            "rollout_key": key, "decision_type": None, "n_options": 0,
            "options": [], "option_wp": [], "option_wp_se": [],
            "option_wins": [], "option_losses": [], "option_draws": [],
            "option_invalid": [], "n_per_option": 0,
            "continuation": None, "seed_fn": SEED_FN, "rollout_seeds": [],
            "determinize": None,
            "caveats": [], "engine_sha": None, "overlay_sha": None,
            "wall_ms": 0}


class RolloutDriver(object):
    """Engine-backed replay/rollout driver. One JVM subprocess per probe and
    one per (position, option) rollout batch — fully local, zero egress (the
    engine never talks to a network; fork modes refuse llm controllers)."""

    _checks_deck_paths = True

    def __init__(self, forge_home=None, n_per_option=DEFAULT_N_PER_OPTION,
                 continuation=ROLLOUT_CONTINUATION, timeout_s=900,
                 work_dir=None, dry_run=False, engine_sha=None,
                 overlay_sha=None, determinize=DEFAULT_DETERMINIZE):
        self.forge_home = Path(forge_home).expanduser() if forge_home else None
        self.n_per_option = n_per_option
        self.continuation = continuation
        if determinize not in DETERMINIZE_MODES:
            raise ValueError("determinize must be one of %s (got %r)"
                             % (sorted(DETERMINIZE_MODES), determinize))
        self.determinize = determinize
        self.timeout_s = timeout_s
        self.work_dir = Path(work_dir) if work_dir else None
        self.dry_run = dry_run
        self.engine_sha = engine_sha
        self.overlay_sha = overlay_sha
        self._probe_cache = {}
        # Unique per driver instance; see _work_subdir.
        self._run_nonce = next(RolloutDriver._RUN_NONCE_COUNTER)

    # -------------------------------------------------- support verdicts

    def supports(self, address):
        """-> (bool, reason). reason is a stable snake_case token, "" when
        supported. Order: controllers, decision type, sha drift, deck paths,
        dry_run — shape problems surface before mode problems."""
        ctl = address.get("controllers") or ""
        parts = ctl.split("/")
        if len(parts) != 2 or any(p not in DETERMINISTIC_CONTROLLERS for p in parts):
            return False, "non_deterministic_controllers"
        dtype = address.get("decision_type")
        if dtype not in V1_FORCEABLE_TYPES:
            fam = decision_family(dtype)
            if fam in ("count", "blocks"):
                return False, "count_encoded_no_v1_rollout"
            return False, "unsupported_decision_type"
        if (self.engine_sha and address.get("engine_sha")
                and address.get("engine_sha") not in ("unknown", "")
                and self.engine_sha != address.get("engine_sha")):
            return False, "engine_sha_drift"
        if (self.overlay_sha and address.get("overlay_sha")
                and address.get("overlay_sha") not in ("unknown", "")
                and self.overlay_sha != address.get("overlay_sha")):
            return False, "overlay_sha_drift"
        if self._checks_deck_paths and not (address.get("deck_a_path")
                                            and address.get("deck_b_path")):
            return False, "missing_deck_path"
        if self.dry_run:
            return False, "dry_run"
        return True, ""

    # ------------------------------------------------------- subprocess

    def _launcher(self):
        if self.forge_home is None:
            return None
        script = self.forge_home / "run-llm-headless.sh"
        return script if script.is_file() else None

    #: Inherited variables that are STRIPPED from every JVM child. Rollouts run
    #: only floor|random|ai|ai-log arms (LlmSpikeFork.requireDeterministicControllers
    #: refuses anything else, exit 2), so the engine has no legitimate use for a
    #: model key or an egress opt-in — and inheriting them would mean a
    #: verification pass silently gains the ability to egress board state the
    #: moment someone sources secrets.env in the same shell. Strip, don't trust.
    CHILD_ENV_DENYLIST = ("OPENROUTER_ALLOW_EGRESS", "OPENROUTER_API_KEY",
                          "OPENAI_API_KEY", "ANTHROPIC_API_KEY")

    def _base_env(self, address, eval_out):
        env = dict(os.environ)  # PATH etc.; never adds any egress opt-in
        for k in self.CHILD_ENV_DENYLIST:
            env.pop(k, None)    # ...and removes the ones it inherited
        parts = (address.get("controllers") or "/").split("/")
        env["FORK_DECISION_INDEX"] = str(address.get("decision_index"))
        env["GAME_SEED"] = str(address.get("seed"))
        env["SWAP_SEATS"] = "1" if address.get("swap") else "0"
        env["CONTROLLER_A"] = parts[0]
        env["CONTROLLER_B"] = parts[1] if len(parts) > 1 else ""
        env["DECK_A_NAME"] = str(address.get("deck_a") or "")
        env["DECK_B_NAME"] = str(address.get("deck_b") or "")
        if address.get("deck_a_path"):
            env["DECK_A"] = str(address["deck_a_path"])
        if address.get("deck_b_path"):
            env["DECK_B"] = str(address["deck_b_path"])
        if self.engine_sha:
            env["ENGINE_SHA"] = self.engine_sha
        if self.overlay_sha:
            env["OVERLAY_SHA"] = self.overlay_sha
        env["EVAL_OUT"] = str(eval_out)
        return env

    def _run(self, env, extra):
        script = self._launcher()
        env = dict(env)
        env.update(extra)
        proc = subprocess.run(
            ["bash", str(script)], cwd=str(self.forge_home), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=self.timeout_s)
        return proc.returncode, proc.stdout.decode("utf-8", "replace")

    #: Per-instance nonce so two verify_keys invocations never share a work
    #: directory. See _work_subdir for why this is load-bearing.
    _RUN_NONCE_COUNTER = itertools.count()

    def _work_subdir(self, address, label):
        """Scratch dir for one (position, label), UNIQUE PER DRIVER INSTANCE.

        The nonce is not cosmetic. rollout_key(address) is deterministic per
        position, so before this the directory -- and therefore the engine's
        output jsonl -- was reused across invocations. The engine appends, and
        _collect_rows counts EVERY fork_rollout row it finds for an option, so
        a second run over the same position tallied its own rollouts PLUS
        every earlier run's.

        Measured on real data: the same position reported 16 wins/option at
        --n-per-option 16, then 18 at n=2, then 23 at n=5 -- i.e. 16, +2, +5.
        Win counts exceeded n_per_option, which is impossible for a single
        batch and is the tell. Every derived number (option_wp, wp_spread,
        separation_z) was computed over that polluted pool, so answer keys
        minted from a repeated run were silently wrong rather than merely
        noisy.
        """
        base = self.work_dir or (REPO / "runs" / "verify-keys-work")
        d = (Path(base) /
             ("%s-%s-run%s-%d" % (rollout_key(address), label,
                                  self._run_nonce, os.getpid())))
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---------------------------------------------------------- enumerate

    def enumerate(self, address):
        """One FORK_MODE=enumerate probe at source.decision_index."""
        ok, reason = self.supports(address)
        if not ok:
            return {"ok": False, "reason": reason}
        if self._launcher() is None:
            return {"ok": False, "reason": "no_engine_available"}
        key = rollout_key(address)
        if key in self._probe_cache:
            return self._probe_cache[key]
        out_dir = self._work_subdir(address, "enum")
        env = self._base_env(address, out_dir)
        try:
            rc, _log = self._run(env, {"FORK_MODE": "enumerate"})
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "probe_timeout"}
        except OSError:
            return {"ok": False, "reason": "engine_launch_failed"}
        if rc != 0:
            return {"ok": False,
                    "reason": "replay_drift" if rc == 4 else "probe_failed"}
        row = self._read_row(out_dir, "fork_enumerate")
        if row is None:
            return {"ok": False, "reason": "probe_failed"}
        res = {"ok": True, "reason": None, "type": row.get("type"),
               "seat": row.get("seat"), "turn": row.get("turn"),
               "phase": row.get("phase"), "n_options": row.get("n_options"),
               "choice": row.get("choice"), "options": row.get("options"),
               "snapshot": row.get("snapshot")}
        self._probe_cache[key] = res
        return res

    @staticmethod
    def _read_row(out_dir, t_value, path_glob="*.jsonl"):
        newest = None
        for p in sorted(Path(out_dir).glob(path_glob)):
            newest = p if newest is None or p.stat().st_mtime >= newest.stat().st_mtime else newest
        if newest is None:
            return None
        try:
            for line in newest.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("t") == t_value:
                    return rec
        except OSError:
            return None
        return None

    # ------------------------------------------------------------ rollout

    def rollout(self, address, n_per_option=None):
        """-> RolloutResult dict. MUST NOT raise on engine failure."""
        n = n_per_option or self.n_per_option
        key = rollout_key(address)
        t0 = datetime.datetime.utcnow()
        ok, reason = self.supports(address)
        if not ok:
            return _fail_result(key, reason)
        if self._launcher() is None:
            return _fail_result(key, "no_engine_available")
        probe = self.enumerate(address)
        if not probe.get("ok"):
            return _fail_result(key, probe.get("reason") or "probe_failed")
        dtype = probe.get("type")
        n_options = probe.get("n_options") or 0
        if dtype not in V1_FORCEABLE_TYPES or n_options < 2:
            return _fail_result(key, "count_encoded_no_v1_rollout")

        rows = []
        for o in range(n_options):
            out_dir = self._work_subdir(address, "roll%d" % o)
            roll_file = out_dir / ("rollout-opt%d.jsonl" % o)
            env = self._base_env(address, out_dir)
            extra = {"FORK_MODE": "rollout",
                     "FORK_ROLLOUT_OPTION": str(o),
                     "FORK_ROLLOUT_N": str(n),
                     "FORK_ROLLOUT_SEED_BASE": str(address.get("seed")),
                     "FORK_ROLLOUT_DETERMINIZE": self.determinize,
                     "FORK_ROLLOUT_OUT": str(roll_file)}
            try:
                rc, _log = self._run(env, extra)
            except subprocess.TimeoutExpired:
                return _fail_result(key, "rollout_timeout")
            except OSError:
                return _fail_result(key, "engine_launch_failed")
            if rc == 4:
                return _fail_result(key, "replay_drift")
            if rc != 0:
                return _fail_result(key, "engine_error")
            got = self._read_rollout_rows(roll_file, o)
            if not got:
                return _fail_result(key, "engine_error")
            rows.append(got)

        return self._aggregate(address, key, dtype, n_options,
                               probe.get("options") or [], rows, n, t0)

    @staticmethod
    def _read_rollout_rows(roll_file, option):
        out = []
        try:
            text = Path(roll_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("t") == "fork_rollout" and rec.get("option") == option:
                out.append(rec)
        return out

    def _aggregate(self, address, key, dtype, n_options, options, per_option_rows, n, t0):
        seat = address.get("seat")
        seat_is_a = seat == address.get("deck_a")  # 'a' wins a mirror name tie
        wins, losses, draws, invalid, wp, se, seeds = [], [], [], [], [], [], []
        for rows in per_option_rows:
            w = l = d = x = 0
            sds = []
            for r in rows:
                sds.append(r.get("rollout_seed"))
                winner = r.get("winner")
                if not r.get("forced_ok") or winner == "invalid" or winner is None:
                    x += 1
                elif winner == "draw":
                    d += 1
                elif (winner == "A") == seat_is_a:
                    w += 1
                else:
                    l += 1
            wins.append(w)
            losses.append(l)
            draws.append(d)
            invalid.append(x)
            seeds.append(sds)
            dec = w + l
            p = (w / dec) if dec else None
            wp.append(p)
            se.append(math.sqrt(p * (1.0 - p) / dec) if dec else None)
        wall_ms = int((datetime.datetime.utcnow() - t0).total_seconds() * 1000)
        return {"schema": "rollout-result.v1", "ok": True, "reason": None,
                "rollout_key": key, "decision_type": dtype,
                "n_options": n_options, "options": options,
                "option_wp": wp, "option_wp_se": se,
                "option_wins": wins, "option_losses": losses,
                "option_draws": draws, "option_invalid": invalid,
                "n_per_option": n,
                "continuation": address.get("controllers"),
                "seed_fn": SEED_FN, "rollout_seeds": seeds,
                "determinize": self.determinize,
                "caveats": [],
                "engine_sha": self.engine_sha,
                "overlay_sha": self.overlay_sha, "wall_ms": wall_ms}


class FixtureRolloutDriver(RolloutDriver):
    """Offline, zero-engine driver: a jsonl of RolloutResult dicts keyed by
    rollout_key. rollout() returns the matching record verbatim or ok=False
    reason='fixture_miss'. supports() gives the same verdicts as RolloutDriver
    except it never checks forge_home or deck paths. This is what the tests
    use — zero engine, zero egress, fully deterministic."""

    _checks_deck_paths = False

    def __init__(self, fixture_path):
        RolloutDriver.__init__(self, forge_home=None)
        self.fixture = {}
        self.warnings = []
        try:
            text = Path(fixture_path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            text = ""
            self.warnings.append("cannot read fixture %s: %s" % (fixture_path, e))
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                self.warnings.append("unparseable fixture line skipped")
                continue
            k = rec.get("rollout_key")
            if k:
                self.fixture[k] = rec

    def enumerate(self, address):
        ok, reason = self.supports(address)
        if not ok:
            return {"ok": False, "reason": reason}
        rec = self.fixture.get(rollout_key(address))
        if rec is None:
            return {"ok": False, "reason": "fixture_miss"}
        return {"ok": True, "reason": None, "type": rec.get("decision_type"),
                "seat": address.get("seat"), "turn": address.get("turn"),
                "phase": None, "n_options": rec.get("n_options"),
                "choice": None, "options": rec.get("options"),
                "snapshot": None}

    def rollout(self, address, n_per_option=None):
        key = rollout_key(address)
        ok, reason = self.supports(address)
        if not ok:
            return _fail_result(key, reason)
        rec = self.fixture.get(key)
        if rec is None:
            return _fail_result(key, "fixture_miss")
        return rec


# ------------------------------------------------------------- verification

def route_lane(cand):
    """The v1 scope decision. -> (lane or None, reason_if_none)."""
    cat = cand.get("category")
    src = cand.get("source") or {}
    dtype = src.get("decision_type")
    if cat == "lethal-now":
        if dtype == "play":
            return "exhaustive", None
        return None, "count_encoded_no_v1_rollout"
    if cat == "forced-block":
        return None, "count_encoded_no_v1_rollout"
    if cat == "free-win":
        return None, "non_empty_stack_v1_restriction"
    if cat == "mulligan":
        return "rollout", None
    if cat in ("save-vs-lethal", "graded-midgame"):
        if dtype == "play":
            return "rollout", None
        return None, "count_encoded_no_v1_rollout"
    return None, "unsupported_decision_type"


def address_of(cand, name_idx):
    src = cand.get("source") or {}
    addr = {}
    for k in ("engine_sha", "overlay_sha", "deck_a", "deck_b", "controllers",
              "seed", "swap", "decision_index", "decision_type", "seat",
              "turn", "game_file"):
        addr[k] = src.get(k)
    ent_a = name_idx.get(src.get("deck_a"))
    ent_b = name_idx.get(src.get("deck_b"))
    if ent_a and ent_a[1]:
        addr["deck_a_path"] = ent_a[1]
    if ent_b and ent_b[1]:
        addr["deck_b_path"] = ent_b[1]
    return addr


# Driver-internal reasons that already ARE row-enum tokens pass through; the
# rest collapse to no_engine_available (preserved verbatim in driver_reason).
_ROW_REASON_PASSTHROUGH = frozenset((
    "count_encoded_no_v1_rollout", "non_empty_stack_v1_restriction",
    "replay_drift", "engine_sha_drift", "overlay_sha_drift",
    "rollout_noise_below_threshold", "no_engine_available",
))


def map_driver_reason(reason):
    if reason in _ROW_REASON_PASSTHROUGH:
        return reason
    if reason == "non_deterministic_controllers":
        return "non_deterministic_source_game"
    return "no_engine_available"


def probe_matches(cand, probe):
    """Byte-identical type/seat/n_options/options between candidate and the
    re-reached live decision."""
    src = cand.get("source") or {}
    return (probe.get("type") == src.get("decision_type")
            and probe.get("seat") == src.get("seat")
            and probe.get("n_options") == cand.get("n_options")
            and list(probe.get("options") or []) == list(cand.get("options") or []))


def verify_one(cand, driver, lane_filter, n_per_option, name_idx, sha16, now,
               gap_threshold=DEFAULT_GAP_THRESHOLD,
               mulligan_gap_threshold=DEFAULT_MULLIGAN_GAP_THRESHOLD,
               min_separation_z=DEFAULT_MIN_SEPARATION_Z):
    """-> (out_row, lane_used, verified_bool, reason_or_None,
    engine_needed_but_unavailable_bool).

    The three gate knobs are trailing optional args defaulting to the pre-flag
    constants: a 7-positional-arg call behaves exactly as it did before."""
    out = dict(cand)  # additive echo: never drop a miner field
    out["status"] = "candidate"
    lane, reason = route_lane(cand)
    if lane is not None and lane_filter != "both" and lane != lane_filter:
        lane, reason = None, "lane_filtered_out"
    if lane is None:
        out["unverifiable_reason"] = reason
        return out, "none", False, reason, False

    src = cand.get("source") or {}
    address = address_of(cand, name_idx)
    engine_missing = False

    probe = None
    probe_evidence = None
    if driver is not None:
        probe = driver.enumerate(address)
        if probe.get("ok"):
            matched = probe_matches(cand, probe)
            probe_evidence = {"ok": True,
                              "n_options": probe.get("n_options"),
                              "matched": matched}
            if not matched:
                out["unverifiable_reason"] = "replay_drift"
                out["probe"] = probe_evidence
                return out, lane, False, "replay_drift", False
        else:
            preason = probe.get("reason") or "probe_failed"
            probe_evidence = {"ok": False, "n_options": None, "matched": False}
            if preason in ("no_engine_available", "engine_launch_failed",
                           "probe_timeout"):
                engine_missing = True
            if lane == "rollout":
                out["unverifiable_reason"] = map_driver_reason(preason)
                out["driver_reason"] = preason
                out["probe"] = probe_evidence
                return out, lane, False, out["unverifiable_reason"], engine_missing
            # exhaustive lane can still adjudicate purely; the missing probe
            # becomes the unprobed_replay_address caveat below.
            probe = None

    if lane == "exhaustive":
        scores, xreason, evidence = adjudicate_exhaustive(cand)
        if scores is None:
            out["unverifiable_reason"] = xreason
            if probe_evidence is not None:
                out["probe"] = probe_evidence
            return out, lane, False, xreason, engine_missing
        caveats = []
        if probe is None or not probe.get("ok"):
            caveats.append("unprobed_replay_address")
        out["status"] = "verified"
        out["answer_key"] = {"verified_by": "exhaustive",
                             "option_scores": scores, "caveats": caveats}
        out["verified_by_tool"] = VERIFIED_BY_TOOL
        out["verified_utc"] = now
        out["contract_sha"] = sha16
        out["adjudication_evidence"] = evidence
        if probe_evidence is not None:
            out["probe"] = probe_evidence
        return out, lane, True, None, engine_missing

    # ---- rollout lane preconditions, in contract order ----
    parts = (src.get("controllers") or "").split("/")
    if len(parts) != 2 or any(p not in DETERMINISTIC_CONTROLLERS for p in parts):
        out["unverifiable_reason"] = "non_deterministic_source_game"
        return out, lane, False, "non_deterministic_source_game", False
    dtype = src.get("decision_type")
    if dtype not in V1_FORCEABLE_TYPES:
        out["unverifiable_reason"] = "count_encoded_no_v1_rollout"
        return out, lane, False, "count_encoded_no_v1_rollout", False
    if dtype != "mulligan" and EMPTY_STACK_MARK not in (cand.get("prompt") or ""):
        out["unverifiable_reason"] = "non_empty_stack_v1_restriction"
        return out, lane, False, "non_empty_stack_v1_restriction", False
    if driver is None:
        out["unverifiable_reason"] = "no_engine_available"
        return out, lane, False, "no_engine_available", True

    result = driver.rollout(address, n_per_option)
    if not result.get("ok"):
        dreason = result.get("reason") or "engine_error"
        out["unverifiable_reason"] = map_driver_reason(dreason)
        out["driver_reason"] = dreason
        if probe_evidence is not None:
            out["probe"] = probe_evidence
        if out["unverifiable_reason"] == "no_engine_available":
            engine_missing = True
        return out, lane, False, out["unverifiable_reason"], engine_missing

    scores, sreason = score_from_wp(result.get("option_wp"),
                                    result.get("option_wp_se"),
                                    cand.get("category"),
                                    gap_threshold, mulligan_gap_threshold,
                                    min_separation_z)
    # RETAIN THE MEASUREMENT on both paths. The engine already spent the
    # compute; a rejected row that drops its WPs cannot tell a genuine tie
    # from a broken rollout. Top level, never inside answer_key.
    out["rollout_evidence"] = build_rollout_evidence(
        result, scores is not None, sreason, cand.get("category"),
        gap_threshold, mulligan_gap_threshold, min_separation_z)
    if scores is None:
        out["unverifiable_reason"] = sreason
        if probe_evidence is not None:
            out["probe"] = probe_evidence
        return out, lane, False, sreason, engine_missing

    # Mutually exclusive by construction: a determinized WP is NOT single-deal.
    # Emitting the stale slug on a determinized key would tell the reader the
    # opposite of what was measured, so it is chosen, never appended.
    caveats = [DETERMINIZED_CAVEAT
               if result.get("determinize") == "library"
               else SINGLE_DEAL_CAVEAT]
    slug = slug_for_deck_name(src.get("seat"), name_idx)
    if slug in COMBO_SLUGS:
        caveats.append("rollout_policy_caveat")
    for c in result.get("caveats") or []:
        if c not in caveats:
            caveats.append(c)
    out["status"] = "verified"
    out["answer_key"] = {
        "verified_by": "rollout",
        "rollout": {"n_per_option": result.get("n_per_option"),
                    "continuation": result.get("continuation"),
                    "seed_fn": result.get("seed_fn")},
        "option_scores": scores,
        "option_wp": result.get("option_wp"),
        "option_wp_se": result.get("option_wp_se"),
        "caveats": caveats,
    }
    out["verified_by_tool"] = VERIFIED_BY_TOOL
    out["verified_utc"] = now
    out["contract_sha"] = sha16
    if probe_evidence is not None:
        out["probe"] = probe_evidence
    return out, lane, True, None, engine_missing


# --------------------------------------------------------------------- main

def read_candidates(path, warnings):
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        warnings.append("cannot read %s: %s" % (path, e))
        return rows
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            warnings.append("%s:%d unparseable line skipped" % (path, i + 1))
    return rows


def quantile(sorted_vals, q):
    """Linear-interpolation (R type 7 / numpy default) quantile of an already
    sorted list. Pinned method, not an implementation detail: the summary's
    p25/median/p75 are compared across corpora."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _spread_bin(value):
    """Bin index for a wp_spread in [0,1]. The 1e-9 nudge keeps 0.30/0.05 from
    landing in bin 5 through binary-float underestimation; the top bin is
    closed so spread == 1.0 counts."""
    idx = int(math.floor(value / SPREAD_BIN_WIDTH + 1e-9))
    return max(0, min(SPREAD_N_BINS - 1, idx))


def spread_distribution(evidences):
    """Distribution of measured wp_spread over rollout-lane positions.

    THE ONE-COMMAND ANSWER to "is this corpus decision-relevant at all?" — a
    corpus whose median spread sits under the 0.20 bar cannot verify no matter
    how many rollouts are thrown at it, and that is visible here without
    re-parsing every row."""
    evs = [e for e in evidences if e]
    spreads = sorted(e["wp_spread"] for e in evs
                     if e.get("wp_spread") is not None)
    zs = sorted(e["separation_z"] for e in evs
                if e.get("separation_z") is not None)
    counts = [0] * SPREAD_N_BINS
    for s in spreads:
        counts[_spread_bin(s)] += 1
    def q5(vals):
        if not vals:
            return {"min": None, "p25": None, "median": None, "p75": None,
                    "max": None, "n": 0}
        return {"min": vals[0], "p25": quantile(vals, 0.25),
                "median": quantile(vals, 0.5), "p75": quantile(vals, 0.75),
                "max": vals[-1], "n": len(vals)}
    return {
        "n_positions_measured": len(evs),
        "n_decided": sum(1 for e in evs if e.get("decided")),
        "n_undecided": sum(1 for e in evs if not e.get("decided")),
        "n_no_wp": len(evs) - len(spreads),
        "wp_spread": q5(spreads),
        "separation_z": q5(zs),
        "wp_spread_hist": {"bin_width": SPREAD_BIN_WIDTH,
                           "n_bins": SPREAD_N_BINS,
                           "counts": counts,
                           "bins_note": "bin i covers [i*0.05, (i+1)*0.05); "
                                        "last bin closed at 1.0"},
        "quantile_method": "linear interpolation (R type 7)",
        "note": "measured over rollout-lane positions where the driver "
                "returned a measurement, DECIDED AND UNDECIDED ALIKE. "
                "n_no_wp counts measured positions whose option_wp was all "
                "None (rollout produced no decided game) — those are broken, "
                "not tied. wp_spread is a measured quantity, not a claim of "
                "decision-relevance for any single position.",
    }


def build_summary(rows_meta, driver_name, n_per_option, warnings,
                  thresholds=None):
    n_in = len(rows_meta)
    n_verified = sum(1 for m in rows_meta if m["verified"])
    n_unver = n_in - n_verified
    by_category = {}
    by_lane = {}
    for m in rows_meta:
        c = by_category.setdefault(m["category"] or "unknown",
                                   {"in": 0, "verified": 0, "unverifiable": 0,
                                    "by_reason": {}})
        c["in"] += 1
        if m["verified"]:
            c["verified"] += 1
        else:
            c["unverifiable"] += 1
            r = m["reason"] or "unknown"
            c["by_reason"][r] = c["by_reason"].get(r, 0) + 1
        ln = by_lane.setdefault(m["lane"], {"in": 0, "verified": 0,
                                            "unverifiable": 0})
        ln["in"] += 1
        if m["verified"]:
            ln["verified"] += 1
        else:
            ln["unverifiable"] += 1
    sha16 = contract_sha_or_unknown()
    summary = {
        "schema": "verify-keys-summary.v1",
        "generated_utc": _utcnow(),
        "n_in": n_in,
        "n_verified": n_verified,
        "n_unverifiable": n_unver,
        "by_category": by_category,
        "by_lane": by_lane,
        "yield_rate": metric_block(n_verified, n_in),
        "driver": driver_name,
        "n_per_option": n_per_option,
        "rollout_spread": spread_distribution(
            [m.get("rollout_evidence") for m in rows_meta]),
        "thresholds": thresholds if thresholds is not None else {
            "gap_threshold": DEFAULT_GAP_THRESHOLD,
            "mulligan_gap_threshold": DEFAULT_MULLIGAN_GAP_THRESHOLD,
            "min_separation_z": DEFAULT_MIN_SEPARATION_Z,
        },
        "engine_sha": os.environ.get("ENGINE_SHA", "unknown"),
        "overlay_sha": os.environ.get("OVERLAY_SHA", "unknown"),
        "contract_sha": sha16,
        "caveats": (["prompt_contract.py not present at verification time; "
                     "contract_sha is 'unknown'"] if not HAVE_PROMPT_CONTRACT
                    else []),
        "coverage_note": COVERAGE_NOTE,
        "warnings": warnings,
    }
    if HAVE_PROMPT_CONTRACT:
        try:
            prompt_contract.stamp(summary)
        except Exception:
            pass
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Verify mined puzzle candidates into answer keys "
                    "(exhaustive + rollout lanes).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates", help="puzzle-position.v1 candidates jsonl")
    ap.add_argument("--out", required=True, help="output positions jsonl")
    ap.add_argument("--lane", choices=("exhaustive", "rollout", "both"),
                    default="both")
    ap.add_argument("--n-per-option", type=int, default=DEFAULT_N_PER_OPTION)
    ap.add_argument("--determinize", choices=DETERMINIZE_MODES,
                    default=DEFAULT_DETERMINIZE,
                    help="re-randomize hidden info at the fork point. "
                         "'library' (default) re-deals every library so the "
                         "rollout actually diverges; 'none' reproduces the "
                         "measured zero-variance defect (every arm 0/n or n/n) "
                         "and is kept only as evidence -- do not mint keys with it.")
    ap.add_argument("--forge-home", default=os.path.expanduser("~/code/forge"))
    ap.add_argument("--driver", choices=("engine", "fixture", "none"),
                    default="none",
                    help="default none: pure exhaustive lane, zero engine")
    ap.add_argument("--fixture", default=None,
                    help="RolloutResult jsonl for --driver fixture")
    ap.add_argument("--gap-threshold", type=float,
                    default=DEFAULT_GAP_THRESHOLD,
                    help="absolute win-probability units [0,1]: floor on "
                         "wp_max-wp_min for non-mulligan categories "
                         "(default %(default)s)")
    ap.add_argument("--mulligan-gap-threshold", type=float,
                    default=DEFAULT_MULLIGAN_GAP_THRESHOLD,
                    help="same units, mulligan-only bar (default %(default)s)")
    ap.add_argument("--min-separation-z", type=float,
                    default=DEFAULT_MIN_SEPARATION_Z,
                    help="dimensionless multiple of the largest per-option "
                         "SE; the gate requires wp_max-wp_min >= "
                         "max(threshold, Z*max(se)) (default %(default)s)")
    ap.add_argument("--max-positions", type=int, default=None)
    ap.add_argument("--jobs", type=int, default=1,
                    help="reserved; v1 verifies sequentially regardless")
    ap.add_argument("--summary", default=None,
                    help="summary json path (default: <out dir>/summary.json)")
    ap.add_argument("--json", action="store_true",
                    help="dump the summary object to stdout as the ONLY "
                         "stdout content")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    warnings = []
    cands = read_candidates(args.candidates, warnings)
    if not cands:
        print("no usable candidate rows in %s" % args.candidates,
              file=sys.stderr)
        return 2
    if args.max_positions is not None:
        cands = cands[:args.max_positions]

    driver = None
    if args.driver == "fixture":
        if not args.fixture:
            print("--driver fixture requires --fixture FILE.jsonl",
                  file=sys.stderr)
            return 2
        driver = FixtureRolloutDriver(args.fixture)
        warnings.extend(driver.warnings)
    elif args.driver == "engine":
        driver = RolloutDriver(
            forge_home=args.forge_home, n_per_option=args.n_per_option,
            work_dir=str(Path(args.out).resolve().parent / "verify-keys-work"),
            dry_run=args.dry_run, determinize=args.determinize,
            engine_sha=os.environ.get("ENGINE_SHA") or None,
            overlay_sha=os.environ.get("OVERLAY_SHA") or None)

    name_idx = deck_name_index()
    sha16 = contract_sha_or_unknown()
    now = _utcnow()

    out_rows = []
    rows_meta = []
    any_engine_missing = False
    for cand in cands:
        row, lane, verified, reason, engine_missing = verify_one(
            cand, driver, args.lane, args.n_per_option, name_idx, sha16, now,
            args.gap_threshold, args.mulligan_gap_threshold,
            args.min_separation_z)
        if HAVE_PROMPT_CONTRACT:
            try:
                prompt_contract.stamp(row)
            except Exception:
                pass
        out_rows.append(row)
        rows_meta.append({"category": cand.get("category"), "lane": lane,
                          "verified": verified, "reason": reason,
                          "rollout_evidence": row.get("rollout_evidence")})
        any_engine_missing = any_engine_missing or engine_missing

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out_path), "w", encoding="utf-8") as f:
        for row in out_rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    summary = build_summary(
        rows_meta, args.driver, args.n_per_option, warnings,
        thresholds={"gap_threshold": args.gap_threshold,
                    "mulligan_gap_threshold": args.mulligan_gap_threshold,
                    "min_separation_z": args.min_separation_z})
    summary_path = Path(args.summary) if args.summary else out_path.parent / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(summary_path), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, sort_keys=True, indent=2) + "\n")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("verified %d / %d (yield %.3f) -> %s"
              % (summary["n_verified"], summary["n_in"],
                 summary["yield_rate"]["est"] or 0.0, out_path),
              file=sys.stderr)
        rs = summary["rollout_spread"]
        if rs["n_positions_measured"]:
            q = rs["wp_spread"]
            print("rollout wp_spread over %d measured positions: "
                  "min %s p25 %s median %s p75 %s max %s (%d with no wp)"
                  % (rs["n_positions_measured"],
                     _fmt3(q["min"]), _fmt3(q["p25"]), _fmt3(q["median"]),
                     _fmt3(q["p75"]), _fmt3(q["max"]), rs["n_no_wp"]),
                  file=sys.stderr)
        print("summary -> %s" % summary_path, file=sys.stderr)

    if args.driver == "engine" and any_engine_missing:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
