#!/usr/bin/env python3
"""Tier-A process-level eval over ALREADY-LOGGED MTG Forge games.

Computes, from the decision/game JSONL corpora that already exist on disk (no
new games, no GPU, no model calls), the three highest-value process metrics
from the design doc "Process-Level Evaluation for the MTG Student":

  1. Teacher decision-match rate on PAIRED STATES. A student run dir is joined
     against the teacher run dir (deepseek, runs/stage0-calibration) on the SAME
     (seed, swap). CRITICAL: decisions are matched by a normalized STATE HASH
     (the state snapshot `prompt` + the option menu, with volatile Forge object
     ids collapsed by make_sft_data.normalize_state), NOT by blind turn
     alignment. Match rate is reported over MATCHED states only, alongside the
     matched-state count and the coverage fraction (matched / eligible student
     decisions), broken down by tier, decision type, and turn bucket. Because
     trajectories diverge after the shared opening, coverage concentrates in the
     early game — the per-turn-bucket breakdown makes that visible.

  2. Blunder / play-quality counters computable DETERMINISTICALLY from single
     rows: mulligan keep-rate (vs teacher), pass-rate on castable main phases
     (a "durdle" indicator), fetch-land crack-vs-hold rate, and reply
     length/format stats. Each carries an honesty caveat where the
     interpretation is weak — none of these is a clean "blunder" oracle.

  3. Plan-adherence lite for the UR Delver tempo plan (threat-then-protect):
     turn of first threat deployment, protection-spell usage rate while a threat
     is on board, and the face-vs-creature split of burn targeting.

Only COMPLETE games (a `t:"game"` summary line present) are read; in-flight
files appended by a live batch are skipped, matching make_sft_data /
aggregate.py semantics. Directories are opened read-only (safe to point at a
live batch dir).

Usage:
  python3 eval/process_metrics.py <student-run-dir> --teacher runs/stage0-calibration [--json out.json]

Exit codes match the sibling analyzers: 0 success; 1 usage/input error.
"""

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Reuse the SFT pipeline's completeness+dedup loader and its dedup state
# normalizer (the single source of truth for collapsing volatile object ids),
# per the design brief ("reuse/factor that code if possible").
from make_sft_data import load_complete_games, normalize_state  # noqa: E402

DEFAULT_TEACHER = "runs/stage0-calibration"

# ---- UR Delver plan vocabulary (plan-adherence lite is archetype-specific) ---
THREATS = ("Delver of Secrets", "Dragon's Rage Channeler", "Murktide Regent",
           "Ragavan")
PROTECTION = ("Daze", "Force of Will", "Spell Pierce", "Force of Negation")
BURN = ("Lightning Bolt", "Chain Lightning")

_PT = re.compile(r"\(\d+/\d+\)")          # a creature option carries P/T
_CHOICE_LINE = re.compile(r"CHOICE:\s*-?\d+")
_FETCH_CRACK = re.compile(r"Sacrifice CARDNAME: Search your library")


# ---------------------------------------------------------------- loading

def load_dir(dir_path):
    """Load complete games from a run dir.

    Returns (entries, llm_seats, player_names, warnings, n_complete). `entries`
    is make_sft_data's [{file, game, decisions}]; `llm_seats` is the set of seat
    names whose controller is 'llm' in any game line; `player_names` is every
    deck/seat name seen (used to tell a player-target from a creature-target).
    """
    p = Path(dir_path).expanduser()
    entries, warnings = load_complete_games(p)
    llm_seats, player_names = set(), set()
    for e in entries:
        g = e["game"]
        for ident in ("a", "b"):
            name = g.get("deck_%s" % ident)
            if name is not None:
                player_names.add(str(name))
            if g.get("controller_%s" % ident) == "llm" and name is not None:
                llm_seats.add(str(name))
    return entries, llm_seats, player_names, warnings, len(entries)


# ------------------------------------------------------- state / choice keys

def chosen_option_text(d):
    """Normalized text of the option the decision actually chose, or None.

    `choice` is a 0-based index into `options`. The play/pass menus reserve an
    explicit terminal PASS option, but a bare -1 sentinel is also emitted for a
    pass on some sites, so -1 maps to a stable PASS token. Object ids in the
    option text are collapsed so 'Mountain (28)' and 'Mountain (84)' compare
    equal, exactly as the teacher-join needs."""
    opts = d.get("options")
    ch = d.get("choice")
    if not isinstance(ch, int) or isinstance(ch, bool):
        return None
    if isinstance(opts, list) and 0 <= ch < len(opts) and isinstance(opts[ch], str):
        return normalize_state(opts[ch])
    if ch == -1:
        return "\x00PASS"
    return None


def state_key(d, seed, swap, seat):
    """Stable hash of a decision's STATE (prompt snapshot + option menu),
    namespaced by (seed, swap, seat) so a hash collision can never cross a
    seed/seat boundary. None when no state was shown (prompt null: forced
    auto/ai handoffs, which are not model choices and carry no comparable
    state). Volatile object ids are normalized so two structurally identical
    states hash identically."""
    prompt = d.get("prompt")
    if not isinstance(prompt, str):
        return None
    opts = d.get("options") or []
    norm_opts = "\x01".join(normalize_state(o) if isinstance(o, str) else ""
                            for o in opts)
    raw = normalize_state(prompt) + "\x02" + norm_opts
    h = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()
    return (seed, swap, seat, h)


def turn_bucket(turn):
    """Coarse turn buckets. Mulligan decisions log turn 0."""
    if not isinstance(turn, int) or isinstance(turn, bool):
        return "unknown"
    if turn <= 0:
        return "0-mull"
    if turn <= 3:
        return "1-3"
    if turn <= 6:
        return "4-6"
    return "7+"

BUCKET_ORDER = ("0-mull", "1-3", "4-6", "7+", "unknown")


# --------------------------------------------------- metric 1: decision match

def build_teacher_index(entries, llm_seats):
    """state_key -> Counter of chosen-option-text for the llm seat's decisions.

    A recurring identical state that the teacher answered differently keeps both
    answers; the join scores a hit against the teacher's MAJORITY option at that
    state (most_common(1))."""
    idx = defaultdict(Counter)
    for e in entries:
        g = e["game"]
        seed, swap = g.get("seed"), bool(g.get("swap", False))
        for d in e["decisions"]:
            if str(d.get("seat")) not in llm_seats:
                continue
            key = state_key(d, seed, swap, d.get("seat"))
            opt = chosen_option_text(d)
            if key is None or opt is None:
                continue
            idx[key][opt] += 1
    return idx


def decision_match(student_entries, student_seats, teacher_idx):
    """Join student decisions onto the teacher's paired-state index.

    Denominators: `total` = every decision of an llm seat; `eligible` = those
    with a comparable state and a determinable chosen option (the join domain);
    `matched` = eligible states the teacher also visited on the same (seed,
    swap). match_rate is hits / matched. coverage is matched / eligible."""
    total = eligible = matched = hits = 0
    by = {"tier": defaultdict(lambda: [0, 0]),   # dim -> [matched, hits]
          "type": defaultdict(lambda: [0, 0]),
          "bucket": defaultdict(lambda: [0, 0])}
    for e in student_entries:
        g = e["game"]
        seed, swap = g.get("seed"), bool(g.get("swap", False))
        for d in e["decisions"]:
            if str(d.get("seat")) not in student_seats:
                continue
            total += 1
            key = state_key(d, seed, swap, d.get("seat"))
            opt = chosen_option_text(d)
            if key is None or opt is None:
                continue
            eligible += 1
            if key not in teacher_idx:
                continue
            matched += 1
            t_choice = teacher_idx[key].most_common(1)[0][0]
            hit = 1 if opt == t_choice else 0
            hits += hit
            for dim, k in (("tier", d.get("tier") or "unknown"),
                           ("type", d.get("type") or "unknown"),
                           ("bucket", turn_bucket(d.get("turn")))):
                by[dim][k][0] += 1
                by[dim][k][1] += hit

    def rollup(dd):
        return {k: {"matched": m, "hits": h,
                    "match_rate": (h / m) if m else None}
                for k, (m, h) in sorted(dd.items())}

    return {
        "total_decisions": total,
        "eligible_decisions": eligible,
        "matched_states": matched,
        "hits": hits,
        "match_rate": (hits / matched) if matched else None,
        "coverage_vs_eligible": (matched / eligible) if eligible else None,
        "coverage_vs_total": (matched / total) if total else None,
        "by_tier": rollup(by["tier"]),
        "by_type": rollup(by["type"]),
        "by_bucket": rollup(by["bucket"]),
    }


# --------------------------------------------- metric 2: blunder/quality counters

def _pct_or_none(num, den):
    return (num / den) if den else None


def quality_counters(entries, llm_seats):
    """Deterministic single-row play-quality counters for the llm seat(s)."""
    mull_total = mull_keep = mull_model = 0
    durdle_den = durdle_num = 0
    fetch_avail = fetch_crack = 0
    reply_chars = []
    comp_tokens = []
    fmt_ok = fmt_total = 0
    plan_line = 0

    for e in entries:
        for d in e["decisions"]:
            if str(d.get("seat")) not in llm_seats:
                continue
            typ = d.get("type")
            opts = d.get("options") if isinstance(d.get("options"), list) else []
            chosen = chosen_option_text(d)

            # (a) mulligan keep/mull distribution
            if typ == "mulligan":
                mull_total += 1
                if d.get("choice") == 0:      # 0 == KEEP, 1 == MULLIGAN
                    mull_keep += 1
                if d.get("tier") in ("smart", "fast"):
                    mull_model += 1

            # (b) durdle: a main-phase play with >=1 castable spell that chose a
            #     land-only / pass action instead of casting.
            if typ == "play" and d.get("phase") in ("MAIN1", "MAIN2"):
                has_cast = any(isinstance(o, str) and o.startswith("CAST")
                               for o in opts)
                if has_cast:
                    durdle_den += 1
                    if not (isinstance(chosen, str) and chosen.startswith("CAST")):
                        durdle_num += 1

            # (c) fetch timing: crack vs hold when a fetch sac ability is offered
            if typ == "play":
                fetch_opt = any(isinstance(o, str) and _FETCH_CRACK.search(o)
                                for o in opts)
                if fetch_opt:
                    fetch_avail += 1
                    if isinstance(chosen, str) and _FETCH_CRACK.search(chosen):
                        fetch_crack += 1

            # (d) reply length / format stats (model-consulted rows only)
            if d.get("tier") in ("smart", "fast") and isinstance(d.get("raw_reply"), str):
                raw = d["raw_reply"]
                reply_chars.append(len(raw))
                ct = d.get("completion_tokens")
                if isinstance(ct, int) and not isinstance(ct, bool) and ct > 0:
                    comp_tokens.append(ct)
                fmt_total += 1
                if _CHOICE_LINE.search(raw):
                    fmt_ok += 1
                if "PLAN:" in raw:
                    plan_line += 1

    return {
        "mulligan": {
            "n": mull_total, "n_model_tier": mull_model,
            "keep_rate": _pct_or_none(mull_keep, mull_total),
            "caveat": "Most mulligan decisions run on the heuristic (tier auto), "
                      "not the model; keep_rate mainly reflects the mulligan "
                      "heuristic, not learned policy (n_model_tier shows the "
                      "model-driven subset).",
        },
        "durdle": {
            "castable_main_phases": durdle_den,
            "land_or_pass": durdle_num,
            "durdle_rate": _pct_or_none(durdle_num, durdle_den),
            "caveat": "WEAK: holding up mana for Daze/Spell Pierce/Force or "
                      "sandbagging a threat is often CORRECT tempo play, so a "
                      "nonzero durdle_rate is not a blunder count.",
        },
        "fetch": {
            "fetch_available": fetch_avail,
            "cracked": fetch_crack,
            "crack_rate": _pct_or_none(fetch_crack, fetch_avail),
            "caveat": "WEAK: holding a fetch to shuffle after Brainstorm or to "
                      "fix colors later is frequently correct; crack_rate is "
                      "descriptive, not a quality score.",
        },
        "reply": {
            "n_model_replies": fmt_total,
            "format_ok_rate": _pct_or_none(fmt_ok, fmt_total),
            "plan_line_rate": _pct_or_none(plan_line, fmt_total),
            "mean_reply_chars": (sum(reply_chars) / len(reply_chars))
                                if reply_chars else None,
            "p90_reply_chars": _p90(reply_chars),
            "mean_completion_tokens": (sum(comp_tokens) / len(comp_tokens))
                                      if comp_tokens else None,
            "p90_completion_tokens": _p90(comp_tokens),
            "caveat": "format_ok_rate = fraction whose reply carries the "
                      "required 'CHOICE: <n>' line; a low rate flags "
                      "format/parse trouble, not play quality.",
        },
    }


def _p90(values):
    if not values:
        return None
    vs = sorted(values)
    return vs[max(0, math.ceil(0.9 * len(vs)) - 1)]


# ------------------------------------------- metric 3: plan-adherence lite (UR)

def your_board(prompt):
    """The llm seat's own 'board:' line (creatures in play). The prompt renders
    'YOU [..] ... board: <...>' then 'OPP [..] ...'; we read the board line from
    the YOU section only."""
    if not isinstance(prompt, str):
        return ""
    i = prompt.find("\nOPP ")
    section = prompt[:i] if i >= 0 else prompt
    m = re.search(r"^\s*board:\s*(.*)$", section, re.M)
    return m.group(1) if m else ""


def plan_adherence(entries, llm_seats, player_names):
    """UR Delver threat-then-protect heuristics."""
    first_threat_turns = []       # one value per game that ever deployed a threat
    games_no_threat = 0
    prot_opportunities = prot_used = 0
    burn_face = burn_creature = burn_other = 0

    for e in entries:
        first_turn = None
        for d in e["decisions"]:
            if str(d.get("seat")) not in llm_seats:
                continue
            typ = d.get("type")
            opts = d.get("options") if isinstance(d.get("options"), list) else []
            chosen = chosen_option_text(d)
            turn = d.get("turn")

            # (a) first threat deployment (a CAST of a threat creature)
            if (typ == "play" and isinstance(chosen, str)
                    and chosen.startswith("CAST")
                    and any(t in chosen for t in THREATS)):
                if isinstance(turn, int) and not isinstance(turn, bool):
                    if first_turn is None or turn < first_turn:
                        first_turn = turn

            # (b) protection usage while a threat is on OUR board
            if typ == "play":
                prot_castable = any(
                    isinstance(o, str) and o.startswith("CAST")
                    and any(p in o for p in PROTECTION) for o in opts)
                threat_on_board = any(t in your_board(d.get("prompt"))
                                      for t in THREATS)
                if prot_castable and threat_on_board:
                    prot_opportunities += 1
                    if (isinstance(chosen, str) and chosen.startswith("CAST")
                            and any(p in chosen for p in PROTECTION)):
                        prot_used += 1

            # (c) burn face-vs-creature split (an 'any target' menu carries a
            #     bare player-name option; classify the chosen target)
            if typ == "target" and opts:
                has_player = any(isinstance(o, str) and o.strip() in player_names
                                 for o in opts)
                if has_player and isinstance(chosen, str):
                    cs = chosen.strip()
                    if cs in player_names:
                        burn_face += 1
                    elif _PT.search(cs):
                        burn_creature += 1
                    else:
                        burn_other += 1
        if first_turn is not None:
            first_threat_turns.append(first_turn)
        else:
            games_no_threat += 1

    ftt = sorted(first_threat_turns)
    return {
        "first_threat_turn": {
            "games_with_threat": len(ftt),
            "games_without_threat": games_no_threat,
            "mean": (sum(ftt) / len(ftt)) if ftt else None,
            "median": ftt[len(ftt) // 2] if ftt else None,
            "min": ftt[0] if ftt else None,
            "max": ftt[-1] if ftt else None,
            "caveat": "Earlier is on-plan for a tempo deck; but a late first "
                      "threat can reflect a threat-light draw, not a misplay.",
        },
        "protection_usage": {
            "opportunities": prot_opportunities,
            "used": prot_used,
            "usage_rate": _pct_or_none(prot_used, prot_opportunities),
            "caveat": "WEAK: opportunity = a protection spell castable WHILE a "
                      "threat is on our board; correct play often HOLDS the "
                      "counter for the opponent's key spell, so a low rate is "
                      "not necessarily wrong.",
        },
        "burn_target_split": {
            "face": burn_face, "creature": burn_creature, "other": burn_other,
            "face_rate": _pct_or_none(burn_face, burn_face + burn_creature),
            "caveat": "Proxy: any-target menus (a player option present) across "
                      "ALL targeted spells, dominated by Lightning Bolt / Chain "
                      "Lightning. face_rate excludes 'other' (non-player, "
                      "non-creature) targets from the denominator.",
        },
    }


# ------------------------------------------------------------- top-level build

def run_metrics(entries, llm_seats, player_names):
    return {
        "quality": quality_counters(entries, llm_seats),
        "plan": plan_adherence(entries, llm_seats, player_names),
    }


def build_report(student_dir, teacher_dir):
    s_entries, s_seats, s_players, s_warn, s_n = load_dir(student_dir)
    t_entries, t_seats, t_players, t_warn, t_n = load_dir(teacher_dir)
    players = s_players | t_players
    teacher_idx = build_teacher_index(t_entries, t_seats)
    return {
        "student_dir": str(student_dir),
        "teacher_dir": str(teacher_dir),
        "student_complete_games": s_n,
        "teacher_complete_games": t_n,
        "student_llm_seats": sorted(s_seats),
        "teacher_llm_seats": sorted(t_seats),
        "teacher_indexed_states": len(teacher_idx),
        "decision_match": decision_match(s_entries, s_seats, teacher_idx),
        "student": run_metrics(s_entries, s_seats, players),
        "teacher": run_metrics(t_entries, t_seats, players),
        "warnings": s_warn + t_warn,
    }


# --------------------------------------------------------------- rendering

def fmt_pct(x):
    return "-" if x is None else "%.1f%%" % (100.0 * x)


def fmt_num(x, spec="%.2f"):
    return "-" if x is None else spec % x


def render_text(r):
    L = []
    L.append("=" * 72)
    L.append("PROCESS-LEVEL EVAL (Tier A) — process metrics over logged games")
    L.append("=" * 72)
    L.append("student: %s  (%d complete games, llm seats: %s)"
             % (r["student_dir"], r["student_complete_games"],
                ", ".join(r["student_llm_seats"]) or "(none)"))
    L.append("teacher: %s  (%d complete games, llm seats: %s)"
             % (r["teacher_dir"], r["teacher_complete_games"],
                ", ".join(r["teacher_llm_seats"]) or "(none)"))
    L.append("teacher indexed paired-states: %d" % r["teacher_indexed_states"])

    # --- metric 1
    m = r["decision_match"]
    L.append("")
    L.append("### 1. TEACHER DECISION-MATCH ON PAIRED STATES")
    L.append("matched by normalized state hash (prompt+menu, object ids "
             "collapsed), NOT turn alignment.")
    L.append("student llm decisions: %d total | %d eligible (comparable state + "
             "chosen option)" % (m["total_decisions"], m["eligible_decisions"]))
    L.append("matched states: %d   coverage: %s of eligible / %s of total"
             % (m["matched_states"], fmt_pct(m["coverage_vs_eligible"]),
                fmt_pct(m["coverage_vs_total"])))
    L.append("MATCH RATE (over matched states): %s  (%d/%d)"
             % (fmt_pct(m["match_rate"]), m["hits"], m["matched_states"]))
    for dim, title in (("by_tier", "by tier"), ("by_type", "by decision type"),
                       ("by_bucket", "by turn bucket")):
        L.append("  -- %s --" % title)
        rows = m[dim]
        order = (BUCKET_ORDER if dim == "by_bucket"
                 else sorted(rows, key=lambda k: -rows[k]["matched"]))
        for k in order:
            if k not in rows:
                continue
            v = rows[k]
            L.append("     %-12s matched=%-5d match=%s (%d/%d)"
                     % (k, v["matched"], fmt_pct(v["match_rate"]),
                        v["hits"], v["matched"]))
    L.append("NOTE: coverage falls off fast after the shared opening — "
             "trajectories diverge, so the early buckets dominate matched "
             "states. Match rate is only meaningful where matched>0.")

    # --- metric 2 + 3 side by side (student vs teacher)
    L.append("")
    L.append("### 2. BLUNDER / PLAY-QUALITY COUNTERS  (student  |  teacher)")
    sq, tq = r["student"]["quality"], r["teacher"]["quality"]
    L.append("mulligan keep-rate:        %s | %s   (n=%d|%d; model-tier n=%d|%d)"
             % (fmt_pct(sq["mulligan"]["keep_rate"]),
                fmt_pct(tq["mulligan"]["keep_rate"]),
                sq["mulligan"]["n"], tq["mulligan"]["n"],
                sq["mulligan"]["n_model_tier"], tq["mulligan"]["n_model_tier"]))
    L.append("  caveat: %s" % sq["mulligan"]["caveat"])
    L.append("durdle-rate (pass/land on castable main): %s | %s   (den=%d|%d)"
             % (fmt_pct(sq["durdle"]["durdle_rate"]),
                fmt_pct(tq["durdle"]["durdle_rate"]),
                sq["durdle"]["castable_main_phases"],
                tq["durdle"]["castable_main_phases"]))
    L.append("  caveat: %s" % sq["durdle"]["caveat"])
    L.append("fetch crack-rate:          %s | %s   (available=%d|%d)"
             % (fmt_pct(sq["fetch"]["crack_rate"]),
                fmt_pct(tq["fetch"]["crack_rate"]),
                sq["fetch"]["fetch_available"], tq["fetch"]["fetch_available"]))
    L.append("  caveat: %s" % sq["fetch"]["caveat"])
    sr, tr = sq["reply"], tq["reply"]
    L.append("reply format-ok rate:      %s | %s   (model replies=%d|%d)"
             % (fmt_pct(sr["format_ok_rate"]), fmt_pct(tr["format_ok_rate"]),
                sr["n_model_replies"], tr["n_model_replies"]))
    L.append("reply plan-line rate:      %s | %s"
             % (fmt_pct(sr["plan_line_rate"]), fmt_pct(tr["plan_line_rate"])))
    L.append("mean reply chars:          %s | %s"
             % (fmt_num(sr["mean_reply_chars"], "%.0f"),
                fmt_num(tr["mean_reply_chars"], "%.0f")))
    L.append("mean completion tokens:    %s | %s"
             % (fmt_num(sr["mean_completion_tokens"], "%.0f"),
                fmt_num(tr["mean_completion_tokens"], "%.0f")))
    L.append("  caveat: %s" % sr["caveat"])

    L.append("")
    L.append("### 3. PLAN-ADHERENCE LITE (UR Delver: threat-then-protect)  "
             "(student | teacher)")
    sp, tp = r["student"]["plan"], r["teacher"]["plan"]
    sf, tf = sp["first_threat_turn"], tp["first_threat_turn"]
    L.append("first threat turn mean/median: %s/%s | %s/%s   "
             "(games w/ threat=%d|%d, none=%d|%d)"
             % (fmt_num(sf["mean"], "%.1f"), fmt_num(sf["median"], "%d") if sf["median"] is not None else "-",
                fmt_num(tf["mean"], "%.1f"), fmt_num(tf["median"], "%d") if tf["median"] is not None else "-",
                sf["games_with_threat"], tf["games_with_threat"],
                sf["games_without_threat"], tf["games_without_threat"]))
    L.append("  caveat: %s" % sf["caveat"])
    spu, tpu = sp["protection_usage"], tp["protection_usage"]
    L.append("protection usage (threat on board): %s | %s   (opps=%d|%d)"
             % (fmt_pct(spu["usage_rate"]), fmt_pct(tpu["usage_rate"]),
                spu["opportunities"], tpu["opportunities"]))
    L.append("  caveat: %s" % spu["caveat"])
    sb, tb = sp["burn_target_split"], tp["burn_target_split"]
    L.append("burn face-rate:            %s | %s   "
             "(face/creature/other: %d/%d/%d | %d/%d/%d)"
             % (fmt_pct(sb["face_rate"]), fmt_pct(tb["face_rate"]),
                sb["face"], sb["creature"], sb["other"],
                tb["face"], tb["creature"], tb["other"]))
    L.append("  caveat: %s" % sb["caveat"])

    if r["warnings"]:
        L.append("")
        L.append("### warnings (%d)" % len(r["warnings"]))
        for w in r["warnings"][:40]:
            L.append("  - " + w)
        if len(r["warnings"]) > 40:
            L.append("  ... %d more" % (len(r["warnings"]) - 40))
    return "\n".join(L)


# --------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("student_dir", help="student run dir of game-*.jsonl files")
    ap.add_argument("--teacher", default=DEFAULT_TEACHER,
                    help="teacher run dir (default %s)" % DEFAULT_TEACHER)
    ap.add_argument("--json", metavar="PATH", nargs="?", const="-",
                    help="emit machine-readable JSON (to PATH, or stdout if bare)")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    for d in (args.student_dir, args.teacher):
        if not Path(d).expanduser().is_dir():
            print("not a directory: %s" % d, file=sys.stderr)
            sys.exit(1)

    report = build_report(args.student_dir, args.teacher)

    if args.json is not None:
        blob = json.dumps(report, indent=2, ensure_ascii=False)
        if args.json == "-":
            print(blob)
        else:
            Path(args.json).write_text(blob + "\n", encoding="utf-8")
            print(render_text(report))
            print("\n(json written to %s)" % args.json)
    else:
        print(render_text(report))
    sys.exit(0)


if __name__ == "__main__":
    main()
