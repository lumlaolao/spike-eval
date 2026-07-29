#!/usr/bin/env python3
"""prompt-contract.v1 — the frozen, versioned, content-hashed render+parse
contract every eval track stamps its artifacts with.

Usage:
  python3 eval/prompt_contract.py --manifest [--profile puz-v1]
  python3 eval/prompt_contract.py --render POSITIONS.jsonl [--index N] \
      [--strictness strict|lenient] [--profile puz-v1]

Both modes are fully offline: this module performs no network I/O and imports
no network libraries. Exit codes: 0 OK, 2 usage error / bad input.

WHAT THIS FREEZES
  The system-message template (the seven SYS_*_V1 literals below), the plan
  policy (deck-plan-file), the user policy (row.prompt served VERBATIM), and
  the parser spec (parser.v1). contract_sha() hashes exactly those, so it is
  stable against ANY future edit to LlmSpikeGui.java, any row content, the
  environment, and the clock. Editing a frozen literal, PARSER_SPEC, PROFILES,
  or STRICTNESSES mints a NEW contract_sha — that is the versioning mechanism:
  v1 never moves; adopt drift by minting v2.

  The literals were generated ONCE at freeze time from
  make_sft_data.PreambleBuilder(DEFAULT_JAVA) (seat_prefix, seat_suffix,
  plan_prefix, plan_suffix, body, format_verbose, tail) and pasted in.
  Assembly is byte-identical to PreambleBuilder.base(seat, plan, terse=False):
      seat_prefix + seat + seat_suffix
      + ("" if not plan else plan_prefix + plan + plan_suffix)
      + body + format_verbose + tail
  STATIC_REFERENCE is deliberately NOT appended: the current build
  (make_sft_data.BUILDS["FIX"]) has static_reference_on_smart=False — live
  games today do not send it — and PreambleBuilder.system(..., "STAGE0")
  currently raises SystemExit on its length pin (STAGE0 pinned 7492 chars vs
  the working tree's 8154), so appending it would emit a preamble no current
  game ever saw. FROZEN_AGAINST_JAVA_SHA records sha256 of the seven pieces
  concatenated in the order above, [:16], as observed at freeze time
  (2026-07-28, working tree at eval/make_sft_data.py's DEFAULT_JAVA).

  plan_sha (per-render) vs contract_sha (per-contract) is a deliberate split:
  the contract covers template+parser; plan_sha covers the .plan file CONTENT,
  so an edit to eval/decks/<slug>.plan is detectable on every rendered artifact
  WITHOUT moving contract_sha.

THE INTERFACE/PLAY SPLIT (the single most important semantic here)
  parse() outcome=="interface_failure" means the reply could not be turned
  into a legal action. It is NEVER scored as a wrong Magic play. Callers
  report interface-failure rate as its own number, and report the play score
  BOTH including interface failures as 0 and excluding them. A model that is
  bad at output format must be distinguishable from a model that is bad at
  Magic.

  strict mode is deliberately STRICTER than the live harness: LlmSpikeGui's
  chooseSpellAbilityToPlay silently coerces an out-of-range play number to
  PASS (`choice == passIdx || choice < 1 || choice > passIdx` -> PASS). The
  contract refuses to launder a formatting failure into a Magic decision:
  out-of-range is an interface_failure in BOTH strictnesses. lenient mode adds
  ONLY bare-number recovery when no tag is found at all, and ONLY for the
  `single` family — leniency is about FORMAT, never about LEGALITY, and never
  about inventing a wire format the seat was never offered (see below).

THE DECLINE SLOT IS A PROPERTY OF THE ROW, NOT OF THE DECISION TYPE
  decideOne() adds its extra "decline/none" menu entry at n_options+1 ONLY when
  its `noneLabel` argument is non-null — i.e. only for OPTIONAL sites
  (`isOptional ? "Decline / none" : null`, `min == 0 ? "No target (decline)"
  : null`). confirm / number / card-name / ability-to-play pass null outright,
  and the flag is not a field of the logged row. MEASURED over every
  mine_puzzles-eligible row in runs/ (2026-07-28): 942 non-play single rows
  have NO decline slot, 7 have one. Assuming the slot always exists would make
  `CHOICE: n_options+1` a legal "decline" on 99.3% of rows where the seat was
  never shown that number — laundering a formatting failure into a Magic
  decision, the exact failure this module exists to prevent.

  So the geometry is derived from the PROMPT the seat actually saw:
  prompt_menu_max() reads the highest "  <N>) <label>" line, and the decline
  slot exists iff that maximum is n_options+1. This is also build-independent:
  the corpus spans overlay builds whose optional-ness differed, and the prompt
  is the only per-row record of what was offered. Verified against the corpus:
  play menus are exactly n_options entries on 8718/8718 eligible rows, count
  menus exactly n_options on 529/529, and no non-menu prompt line ever matches
  the regex.

  Rows whose prompt carries NO numbered menu at all (393 eligible rows are the
  harness's second-attempt retry prompt, "That reply had no parseable
  choice...", which contains neither state nor menu) are rendered with warning
  "prompt_missing_menu": serving one guarantees an interface failure that is
  not the model's fault, so callers must drop them from scored sets.

PARSER DRIFT IS SELF-DETECTING
  contract_sha() folds in parser_behavior_sha() — a hash of parse()'s ACTUAL
  output over a frozen table of probe cases (_PARSER_PROBE_CASES), not of the
  PARSER_SPEC string. Editing an acceptance rule therefore mints a new
  contract_sha automatically; it is not possible to change what the parser
  accepts while keeping the id that says two runs are comparable. The pinned
  value is asserted in eval/test_prompt_contract.py.

  Harness `choice` encoding reminder (EVAL contract §1.3): index-encoded
  families (single/mulligan) put the 0-based option index in `choice` with -1
  the PASS/decline sentinel; count/blocks families put the SIZE of the chosen
  subset / number of block assignments in `choice`. parse() mirrors that
  encoding in its `choice` field so downstream comparison against logged rows
  is a plain equality.

WHAT WOULD MAKE THIS FAIL
  * contract_sha() or render() depending on os.environ, the clock, dict/set
    iteration order, or the Java working tree — caught by the subprocess
    determinism tests in eval/test_prompt_contract.py.
  * A typo in the pasted frozen literals — caught by
    test_frozen_template_matches_java_today, which recomputes the framing from
    PreambleBuilder and byte-compares every piece while the Java is unchanged,
    and by manifest["system_template_sha"] != FROZEN_AGAINST_JAVA_SHA.
  * parse() accepting an out-of-range number as PASS (the harness's coercion)
    or scoring an unparseable reply as a Magic mistake — caught by
    test_interface_vs_play_split.
  * parse() accepting n_options+1 as a "decline" on a row whose prompt never
    offered a decline entry — caught by test_decline_slot_is_per_row, which
    renders the SAME decision type with and without the extra menu line.
  * lenient mode recovering a bare number for mulligan/count/blocks, where the
    seat was never shown numbers at all — caught by test_strictness_gap.
  * ANY change to what parse() accepts, without a new contract_sha — caught by
    test_parser_behavior_pin (PARSER_BEHAVIOR_SHA) and by the contract_sha pin.
  * render() mutating/reflowing row["prompt"] — caught by
    test_user_message_verbatim (byte equality, unicode + trailing whitespace).
  * Anything in this module opening a socket — caught by test_no_egress_path,
    which runs the whole public API with socket.socket poisoned to raise.

NOT IN SCOPE (deliberately): this module reports NO metric, so it carries no
baseline/CI block. The floors ("the 88% lesson") belong to the tools that
score with it; what this module owes them is the geometry those floors are
computed FROM — menu_numbers (the random-choice denominator) and
decline_menu_number / pass_option_index (the always-pass numerator) — which is
precisely why the decline-slot bug above was a scoring bug, not a cosmetic one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent

# Reused harness-port parsers (EVAL contract §2: import, never reimplement).
from replay_match import (  # noqa: E402
    strip_markdown, last_segment_any, parse_tag_number, parse_picks,
    parse_mulligan, parse_blocks_count, decision_family,
    SINGLE_CHOICE_TYPES, COUNT_PICKS_TYPES, BLOCKS_TYPES,  # noqa: F401
)

# ---------------------------------------------------------------- constants

CONTRACT_VERSION = "prompt-contract.v1"
PROFILES = ("puz-v1",)            # adding a profile mints a new sha
STRICTNESSES = ("strict", "lenient")
# parser.v1.1 corrected two acceptance rules of the never-released parser.v1
# BEFORE any artifact was stamped: the decline slot is now read from the row's
# prompt instead of assumed for every non-play single type, and bare-number
# recovery is restricted to the `single` family. contract_sha moved with it
# (it folds in parser_behavior_sha(), so it could not have failed to).
PARSER_SPEC = "parser.v1.1"       # bump == new contract_sha
FAMILIES = ("single", "count", "blocks", "mulligan")
SCOREABLE_INDEX_TYPES = frozenset(("play", "mulligan"))   # v1 key-able types
# Families whose prompt carries a numbered menu; the other two (mulligan,
# blocks) answer in words, so a missing numbered menu is normal for them.
NUMBERED_MENU_FAMILIES = ("single", "count")
# Families where a lone number is a meaningful answer (lenient recovery).
BARE_NUMBER_FAMILIES = ("single",)

# ------------------------------------------------- frozen system template
# Generated once at freeze time from PreambleBuilder(DEFAULT_JAVA); see the
# module docstring. DO NOT regenerate in place — editing these mints v2.

SYS_SEAT_PREFIX_V1 = 'You are a world-class Magic: The Gathering (Legacy) player piloting "'
SYS_SEAT_SUFFIX_V1 = '".\n'
SYS_PLAN_PREFIX_V1 = 'GAME PLAN: '
SYS_PLAN_SUFFIX_V1 = '\n'
SYS_BODY_V1 = 'PROTOCOL: I am the Forge rules engine. Each message gives you the CURRENT game state and ONE decision with a numbered list of the ONLY legal options — every option is already engine-validated, so pick purely on strategy. The state snapshot in each message is authoritative: trust it over your memory of earlier turns. You have NO tools and cannot inspect anything yourself — everything you need is in the message; answer directly.\nPRINCIPLES: (1) Sequence toward your game plan, not the shiniest single play. (2) At instant speed with nothing to gain, PASS. (3) In combat, count damage and their untapped mana before committing. (4) Life, cards, and tempo are resources — spend them to win, not to feel safe. (5) Card selection is fuel: with spare mana, a draw/loot/dig spell, and no better line, CAST IT — repeatedly passing while holding cantrips is how combo decks lose. (6) If your key piece is gone (stripped/bounced), your plan is to FIND another (dig), not to accumulate mana with nothing to spend it on.\n'
SYS_FORMAT_V1 = 'FORMAT: at most two short sentences of reasoning, then a final line in EXACTLY the format I request (e.g. `CHOICE: 3` or `PICKS: 1,4`). I parse ONLY that final line — never write anything after it.'
SYS_TAIL_V1 = ' Play to WIN the game, not just the current decision.'

# sha256 of the seven pieces concatenated in the order above, [:16], observed
# at freeze time. If PreambleBuilder produces a different value tomorrow, the
# JAVA drifted — v1 stays frozen on purpose; mint v2 to adopt.
FROZEN_AGAINST_JAVA_SHA = '24fcfabcd2a71c25'

_SYSTEM_PIECES = (SYS_SEAT_PREFIX_V1, SYS_SEAT_SUFFIX_V1, SYS_PLAN_PREFIX_V1,
                  SYS_PLAN_SUFFIX_V1, SYS_BODY_V1, SYS_FORMAT_V1, SYS_TAIL_V1)

_DECK_DIR = Path(__file__).resolve().parent / "decks"
_HOLDOUTS = Path(__file__).resolve().parent / "holdouts.json"

_DIGIT_RUN = re.compile(r"\d+")
_LINE_SPLIT = re.compile(r"\r\n|\r|\n")

INTERFACE_ERRORS = ("empty_reply", "no_tag", "no_number", "out_of_range",
                    "count_out_of_range", "unsupported_family")


def _sha16(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------ contract ids

def contract_sha(profile="puz-v1"):
    """16-hex stable id of the (template, plan policy, user policy, parser)
    tuple. Depends ONLY on the module constants above — never on any row, on
    os.environ, on the clock, or on the Java working tree."""
    if profile not in PROFILES:
        raise ValueError("unknown profile %r (known: %s)"
                         % (profile, ", ".join(PROFILES)))
    parts = [CONTRACT_VERSION, profile, PARSER_SPEC,
             "plan_policy=deck-plan-file",
             "user_policy=row.prompt-verbatim",
             "static_reference=0",
             ",".join(STRICTNESSES)]
    parts.extend(_SYSTEM_PIECES)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def system_template_sha():
    """sha256 of the seven frozen pieces concatenated in assembly order, [:16].
    Recomputed from the literals at call time, so it equals
    FROZEN_AGAINST_JAVA_SHA iff nobody tampered with the pasted literals."""
    return _sha16("".join(_SYSTEM_PIECES))


def contract_manifest(profile="puz-v1"):
    """JSON-serializable manifest matching
    eval/schemas/prompt-contract.v1.schema.json#/$defs/manifest."""
    return {
        "contract": CONTRACT_VERSION,
        "profile": profile,
        "contract_sha": contract_sha(profile),
        "parser_spec": PARSER_SPEC,
        "strictnesses": list(STRICTNESSES),
        "system_template_sha": system_template_sha(),
        "plan_policy": "deck-plan-file",
        "user_policy": "row.prompt verbatim",
        "frozen_against_java_sha": FROZEN_AGAINST_JAVA_SHA,
        "static_reference_included": False,
    }


def stamp(obj, profile="puz-v1"):
    """Mutate and return obj with the contract identity fields set. Every
    artifact any track writes MUST be stamped."""
    obj["contract"] = CONTRACT_VERSION
    obj["contract_sha"] = contract_sha(profile)
    return obj


# ----------------------------------------------------------- plan resolution

_PLAN_CACHE = {}


def _load_plan_index():
    """{plan_text_stripped: slug} over eval/decks/*.plan. Cached per-process;
    the cache key is the directory path so tests could repoint it."""
    key = ("plan_index", str(_DECK_DIR))
    if key not in _PLAN_CACHE:
        idx = {}
        if _DECK_DIR.is_dir():
            for fp in sorted(_DECK_DIR.glob("*.plan")):
                try:
                    idx[fp.read_text(encoding="utf-8").strip()] = fp.stem
                except OSError:
                    continue
        _PLAN_CACHE[key] = idx
    return _PLAN_CACHE[key]


def _name_to_slug():
    """{display name: slug} from eval/holdouts.json decks. decks is a dict
    keyed by slug with a 'name' field per entry; missing/unreadable file ->
    empty map (resolution then falls through to 'unresolved')."""
    key = ("names", str(_HOLDOUTS))
    if key not in _PLAN_CACHE:
        names = {}
        try:
            decks = json.loads(_HOLDOUTS.read_text(encoding="utf-8")).get(
                "decks", {})
            if isinstance(decks, dict):
                for slug in sorted(decks):
                    ent = decks[slug]
                    if isinstance(ent, dict) and isinstance(
                            ent.get("name"), str):
                        names[ent["name"]] = slug
        except (OSError, ValueError):
            names = {}
        _PLAN_CACHE[key] = names
    return _PLAN_CACHE[key]


def resolve_plan(seat_or_deck_name, plan_text=None):
    """-> (plan_text, plan_slug, plan_sha, plan_source).

    plan_source in {"explicit","deck-plan-file","unresolved"}. Resolution
    order (EVAL contract §2):
      1. plan_text argument, if given -> "explicit"; the slug is recovered by
         matching the stripped text against the eval/decks/*.plan index (None
         when it matches no known plan). Text is .strip()ed for both matching
         and hashing so the sha of an explicit copy of a .plan file equals the
         deck-plan-file sha.
      2. seat/deck display name -> slug via eval/holdouts.json decks[*].name,
         then read eval/decks/<slug>.plan, .strip(). As a documented
         convenience the bare slug itself is also accepted (harmless
         extension: 'ur-delver' resolves like 'UR Delver').
      3. unresolved -> ("", None, sha16 of "", "unresolved").
    plan_sha = sha256(plan_text.encode('utf-8')).hexdigest()[:16]."""
    if plan_text is not None:
        text = plan_text.strip()
        return (text, _load_plan_index().get(text), _sha16(text), "explicit")
    name = seat_or_deck_name if isinstance(seat_or_deck_name, str) else ""
    slug = _name_to_slug().get(name)
    if slug is None and name and (_DECK_DIR / (name + ".plan")).is_file():
        slug = name
    if slug is not None:
        fp = _DECK_DIR / (slug + ".plan")
        try:
            text = fp.read_text(encoding="utf-8").strip()
            return (text, slug, _sha16(text), "deck-plan-file")
        except OSError:
            pass
    return ("", None, _sha16(""), "unresolved")


# ------------------------------------------------------------------- render

def _system_message(seat, plan):
    """Byte-identical to PreambleBuilder.base(seat, plan, terse=False)."""
    plan_block = "" if not plan else (SYS_PLAN_PREFIX_V1 + plan
                                      + SYS_PLAN_SUFFIX_V1)
    return (SYS_SEAT_PREFIX_V1 + seat + SYS_SEAT_SUFFIX_V1 + plan_block
            + SYS_BODY_V1 + SYS_FORMAT_V1 + SYS_TAIL_V1)


def render(row, profile="puz-v1", strictness="strict", plan_text=None,
           seat=None):
    """Render one decision into the frozen two-message prompt.

    Accepts BOTH a puzzle-position.v1 dict (fields under row["source"]) and a
    raw decision row (flat fields). The user message is row["prompt"]
    VERBATIM — no strip, no reflow, no normalization (it already contains the
    numbered menu and the trailing format instruction). Raises ValueError
    (never SystemExit) when prompt is missing/None/empty or n_options < 2.

    DETERMINISM: render(row) twice, in two processes, produces byte-identical
    "messages" and identical render_sha — nothing here reads env or clock."""
    if profile not in PROFILES:
        raise ValueError("unknown profile %r" % (profile,))
    if strictness not in STRICTNESSES:
        raise ValueError("unknown strictness %r" % (strictness,))
    if not isinstance(row, dict):
        raise ValueError("row must be a dict, got %s" % type(row).__name__)

    src = row.get("source") if isinstance(row.get("source"), dict) else {}
    if seat is None:
        seat = src.get("seat", row.get("seat"))
    dtype = src.get("decision_type") or row.get("type") or ""

    warnings = []
    options = row.get("options")
    if not isinstance(options, list):
        if options is not None:
            warnings.append("options_not_a_list")
        options = []
    if any(not isinstance(o, str) for o in options):
        warnings.append("non_string_options")

    n_options = row.get("n_options")
    if not isinstance(n_options, int) or isinstance(n_options, bool):
        n_options = len(options)
    if len(options) < n_options:
        # Trust n_options (the harness's count) but record the discrepancy.
        warnings.append("options_shorter_than_n_options")

    prompt = row.get("prompt")
    if not isinstance(prompt, str) or prompt == "":
        raise ValueError("row.prompt is missing/None/empty — cannot render a "
                         "decision the seat never actually saw")
    if n_options < 2:
        raise ValueError("n_options=%r < 2 — not a decision" % (n_options,))

    family = decision_family(dtype)
    if family == "mulligan":
        menu_numbers = [1, 2]           # KEEP=1, MULLIGAN=2 (wire format is
        decline_menu_number = None      # the WORDS, numbers only for lenient
        pass_option_index = None        # bare-number recovery)
    elif family == "single":
        menu_numbers = list(range(1, n_options + 1))
        if dtype == "play":
            # PASS IS the last menu option; there is no extra decline slot.
            decline_menu_number = None
            pass_option_index = n_options - 1
        else:
            # decideOne offers a decline slot at n_options+1 with NO entry in
            # `options`; picking it logs choice=-1.
            decline_menu_number = n_options + 1
            pass_option_index = None
    else:                               # count / blocks / unknown
        menu_numbers = list(range(1, n_options + 1))
        decline_menu_number = None
        pass_option_index = None

    seat_str = seat if isinstance(seat, str) else ""
    ptext, pslug, psha, psource = resolve_plan(seat_str, plan_text)
    system = _system_message(seat_str, ptext)

    return {
        "contract": CONTRACT_VERSION,
        "contract_sha": contract_sha(profile),
        "profile": profile,
        "strictness": strictness,
        "decision_type": dtype,
        "family": family,
        "n_options": n_options,
        "menu_numbers": menu_numbers,
        "decline_menu_number": decline_menu_number,
        "pass_option_index": pass_option_index,
        "plan_slug": pslug,
        "plan_sha": psha,
        "plan_source": psource,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "render_sha": _sha16(system + "\x00" + prompt),
        "warnings": warnings,
    }


# -------------------------------------------------------------------- parse

def _blank_parsed(reply):
    return {"outcome": "interface_failure", "interface_error": None,
            "recovered_by": None, "menu_number": None, "option_index": None,
            "choice": None, "picks": None, "picks_size": None,
            "blocks_count": None, "raw": reply, "is_pass": False}


def _bare_number_last_line(reply):
    """Lenient-only recovery input: the single digit run on the LAST non-empty
    line of the (markdown-stripped) reply, or None. Requires EXACTLY one digit
    run — 'attack with 2 then 3' is ambiguous and stays a failure."""
    lines = [ln for ln in _LINE_SPLIT.split(strip_markdown(reply))
             if ln.strip()]
    if not lines:
        return None
    runs = _DIGIT_RUN.findall(lines[-1])
    if len(runs) != 1:
        return None
    return int(runs[0])


def _apply_single_number(out, num, rendered):
    """Map a 1-based menu number onto the single-family outcome fields."""
    n = rendered["n_options"]
    dtype = rendered["decision_type"]
    decline = rendered["decline_menu_number"]
    out["menu_number"] = num
    if num in rendered["menu_numbers"]:
        if dtype == "play" and num == n:
            out["outcome"] = "ok"
            out["option_index"] = n - 1     # PASS is a real menu option...
            out["choice"] = -1              # ...but logs as -1 (harness enc.)
            out["is_pass"] = True
        else:
            out["outcome"] = "ok"
            out["option_index"] = num - 1
            out["choice"] = num - 1
    elif decline is not None and num == decline:
        out["outcome"] = "ok"
        out["option_index"] = None          # decline slot has no options entry
        out["choice"] = -1
    else:
        out["interface_error"] = "out_of_range"
    return out


def parse(reply, rendered, strictness=None):
    """Parse a model reply against a render() result.

    outcome=="interface_failure" means the reply could not be turned into a
    legal action; it is NEVER scored as a wrong Magic play (see module
    docstring). strict refuses the harness's out-of-range->PASS coercion;
    lenient adds ONLY bare-number recovery when no tag was found — leniency is
    about format, never about legality (out-of-range fails in BOTH modes).

    Bare-number recovery is defined only for the single and mulligan families:
    a lone number cannot express a subset (count) or block assignment
    (blocks), so recovering one there would be inventing semantics.

    parser.v1 refinement over the raw harness ports (stricter, never looser):
    PICKS/BLOCKS are flexible bare-word tags in the harness, so ordinary prose
    containing the word ("no blocks here") matches with a contentless segment
    the harness would score as a size-0 declaration. The contract requires an
    empty pick/block set to be DECLARED (the literal NONE, or an actual
    assignment/number); a contentless bare-word match is no_tag."""
    if strictness is None:
        strictness = rendered["strictness"]
    if strictness not in STRICTNESSES:
        raise ValueError("unknown strictness %r" % (strictness,))
    family = rendered["family"]
    out = _blank_parsed(reply)

    if reply is None or reply.strip() == "":
        out["interface_error"] = "empty_reply"
        return out

    if family == "single":
        num = parse_tag_number(reply, "CHOICE:")
        if num >= 0:
            return _apply_single_number(out, num, rendered)
        seg = last_segment_any(reply, ["CHOICE:", "ANSWER:"])
        if seg is not None:
            # Tag present, no digits after it (e.g. a reply truncated to
            # "CHOICE: " — observed in runs/improve/iter-2 game-112 idx 124).
            out["interface_error"] = "no_number"
            return out
        if strictness == "lenient":
            rec = _bare_number_last_line(reply)
            if rec is not None:
                out = _apply_single_number(out, rec, rendered)
                if out["outcome"] == "ok":
                    out["recovered_by"] = "bare_number"
                return out
        out["interface_error"] = "no_tag"
        return out

    if family == "mulligan":
        v = parse_mulligan(reply)
        if v is None and strictness == "lenient":
            rec = _bare_number_last_line(reply)
            if rec is not None:
                out["menu_number"] = rec
                if rec in rendered["menu_numbers"]:
                    v = rec - 1
                    out["recovered_by"] = "bare_number"
                else:
                    out["interface_error"] = "out_of_range"
                    return out
        if v is None:
            out["interface_error"] = "no_tag"
            return out
        out["outcome"] = "ok"
        out["option_index"] = v             # 0 == KEEP, 1 == MULLIGAN
        out["choice"] = v
        out["menu_number"] = v + 1
        return out

    if family == "count":
        picks_set, ok = parse_picks(reply, rendered["n_options"])
        seg = last_segment_any(reply, ["PICKS:"])
        if not ok or seg is None:
            out["interface_error"] = "no_tag"
            return out
        if not picks_set and "NONE" not in seg.upper():
            # PICKS is a flexible bare-word tag in the harness, so ordinary
            # prose ("no picks tag here") matches it with an empty segment.
            # parser.v1 refuses to launder that into a size-0 pick: an empty
            # pick must be DECLARED ("PICKS: NONE"). Stricter than the port,
            # never looser. Digits that were all out of menu range are a
            # range failure, not a declaration of "none".
            if _DIGIT_RUN.search(seg):
                out["interface_error"] = "out_of_range"
            else:
                out["interface_error"] = "no_tag"
            return out
        # parse_picks keeps only DISTINCT in-range numbers, so the size can
        # never exceed n_options; the menu's min/max bounds are not present in
        # the row, so count_out_of_range is unreachable for this family in v1.
        picks = sorted(p - 1 for p in picks_set)
        out["outcome"] = "ok"
        out["picks"] = picks
        out["picks_size"] = len(picks)
        out["choice"] = len(picks)          # harness count-encoding (§1.3)
        return out

    if family == "blocks":
        cnt, ok = parse_blocks_count(reply)
        seg = last_segment_any(reply, ["BLOCKS:"])
        if not ok or seg is None:
            out["interface_error"] = "no_tag"
            return out
        if cnt == 0 and "NONE" not in seg.upper():
            # Same bare-word-in-prose hazard as PICKS: "no blocks here"
            # matches the flexible BLOCKS tag with a contentless segment. A
            # zero-block declaration must be explicit ("BLOCKS: NONE");
            # anything else without an 'X->Y' assignment is unparsed prose.
            out["interface_error"] = "no_tag"
            return out
        if cnt > rendered["n_options"]:
            out["interface_error"] = "count_out_of_range"
            out["blocks_count"] = cnt
            return out
        out["outcome"] = "ok"
        out["blocks_count"] = cnt
        out["choice"] = cnt                 # harness blocks-encoding (§1.3)
        return out

    out["interface_error"] = "unsupported_family"
    return out


def parse_both(reply, rendered):
    """Both strictnesses at once, so callers can always report the formatting
    tax (lenient score minus strict score) instead of silently picking one."""
    return {"strict": parse(reply, rendered, "strict"),
            "lenient": parse(reply, rendered, "lenient")}


# ---------------------------------------------------------------------- CLI

def _read_jsonl(path):
    rows, warnings = [], []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                warnings.append("line %d unparseable" % (i + 1,))
    return rows, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", action="store_true",
                    help="print the contract manifest JSON and exit")
    ap.add_argument("--render", metavar="POSITIONS_JSONL",
                    help="render one row of a positions/decisions jsonl")
    ap.add_argument("--index", type=int, default=0,
                    help="0-based row index for --render (default 0)")
    ap.add_argument("--strictness", default="strict", choices=STRICTNESSES)
    ap.add_argument("--profile", default="puz-v1")
    args = ap.parse_args(argv)

    if args.manifest:
        try:
            print(json.dumps(contract_manifest(args.profile), sort_keys=True,
                             indent=2))
        except ValueError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        return 0

    if args.render:
        try:
            rows, warnings = _read_jsonl(args.render)
        except OSError as e:
            print("error: cannot read %s: %s" % (args.render, e),
                  file=sys.stderr)
            return 2
        for w in warnings:
            print("warning: %s" % w, file=sys.stderr)
        if not (0 <= args.index < len(rows)):
            print("error: --index %d out of range (%d usable rows)"
                  % (args.index, len(rows)), file=sys.stderr)
            return 2
        try:
            rendered = render(rows[args.index], profile=args.profile,
                              strictness=args.strictness)
        except ValueError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        print(json.dumps(rendered, sort_keys=True, indent=2))
        return 0

    ap.print_usage(sys.stderr)
    print("error: one of --manifest / --render is required", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
