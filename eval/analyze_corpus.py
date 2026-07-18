#!/usr/bin/env python3
"""Slice the decision corpus (t:"decision"/t:"game" JSONL) to answer: where
should play-tightening aim?

Reads every *.jsonl in one or more run directories and reports, per dir AND
combined:
  - decision counts by type x tier
  - fallback lines grouped by normalized fallback reason (with an example)
  - ai-tier and auto-tier rates over llm-identity decisions (aggregate.py's
    notion: a seat whose game-line controller is 'llm')
  - latency p50/p90/max by tier over lines with latency_ms > 0
  - option-count (n_options) histogram summary by type
  - first-option-pick rate by type, model-consulted vs auto (bias detector)
  - per-game decision volume (mean/max)
  - prompt/raw_reply round-trip coverage
plus a play-tightening section ranking the un-routed surface: decision types
that only ever appear with tier auto/ai, and the structurally invisible
Forge-AI surfaces that emit no decision line at all.

Robustness contract: never crashes on a malformed/truncated line (skipped and
counted); a file with no game line counts as an invalid game but its decisions
are still used; legacy lines missing options/prompt/raw_reply are tolerated.
Directories are opened read-only (safe to point at a live batch).

Usage:
  python3 eval/analyze_corpus.py <run_dir> [<run_dir> ...] [--json] [--markdown PATH]

Exit codes match aggregate.py's contract: 0 success; 1 usage/input error
(e.g. not a directory); 2 data-quality failure (zero decision lines found —
the analogue of aggregate.py's fallback-gate exit 2).
"""

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Fallback copy for standalone use (aggregate.py is the single source of
# truth; test_analyze_corpus.py asserts the two strings stay identical).
_AI_PARTIAL_FALLBACK = (
    "NOTE (ai tier is PARTIAL): the ai counter only sees fallbacks inside "
    "overridden methods. Combat damage order, X announcement, edict "
    "sacrifices, mana tapping and mulligan bottoming are un-overridden and "
    "always run on Forge AI invisibly, emitting no decision line. ai=0 "
    "does NOT mean the model drove every decision."
)
try:
    from aggregate import AI_PARTIAL_CAVEAT
except ImportError:  # pragma: no cover
    AI_PARTIAL_CAVEAT = _AI_PARTIAL_FALLBACK

TIER_ORDER = ("smart", "fast", "auto", "ai")

PLAY_BASELINE_NOTE = (
    "(play baseline uses 1/(n-1): the play menu's last slot is PASS, logged "
    "as choice=-1, so choice==n_options-1 is structurally unreachable for the "
    "non-pass lines counted here)"
)

# Types whose `choice` is a COUNT of picks, not an index into options —
# excluded from first-option-bias math (choice==0 there means "picked none").
COUNT_CHOICE_TYPES = {"attackers", "blockers", "discard", "cards-for-effect",
                      "modes"}

# Decision points with NO override in PlayerControllerLLM: they resolve on
# Forge AI invisibly and emit no decision line at all. Their absence from the
# corpus is itself the finding.
INVISIBLE_SURFACES = (
    "combat damage assignment order",
    "X announcement (choosing X when casting)",
    "edict sacrifice choice (opponent-forced sacrifices)",
    "mana tapping / payment ordering",
    "mulligan bottoming (which cards go to the bottom after a keep)",
)

INVISIBLE_CAVEAT = (
    "These surfaces are STRUCTURALLY INVISIBLE: they produce no decision line "
    "at all, so their absence from this corpus is itself the point — zero "
    "lines is not zero decisions, it is zero routing. Additionally, "
    "chooseTargetsFor early-super returns (multi-target spells, sub-ability "
    "targeting) are silent Forge-AI targeting handoffs not counted as tier ai."
)


# ---------------------------------------------------------------- parsing

def parse_run_dir(path):
    """Parse one run directory (read-only). Returns a dict:
      dir, files (list of names), games (game lines), decisions (each
      annotated with _dir/_file/_llm), n_skipped_lines, n_legacy_lines,
      missing_game_line_files (list).
    Malformed / truncated / half-written lines are skipped and counted.
    """
    path = Path(path)
    res = {"dir": str(path), "files": [], "games": [], "decisions": [],
           "n_skipped_lines": 0, "n_legacy_lines": 0,
           "missing_game_line_files": [], "unreadable_files": []}
    for fp in sorted(path.glob("game-*.jsonl")):
        res["files"].append(fp.name)
        found_game = False
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        res["n_skipped_lines"] += 1  # truncated/malformed
                        continue
                    if not isinstance(rec, dict):
                        res["n_skipped_lines"] += 1
                        continue
                    t = rec.get("t")
                    if t == "decision":
                        rec["_dir"] = str(path)
                        rec["_file"] = fp.name
                        if not ("options" in rec and "prompt" in rec
                                and "raw_reply" in rec):
                            res["n_legacy_lines"] += 1
                        res["decisions"].append(rec)
                    elif t == "game":
                        rec["_file"] = fp.name
                        res["games"].append(rec)
                        found_game = True
                    # unknown t: tolerated, skipped silently
        except OSError as e:
            res["unreadable_files"].append("%s: %s" % (fp.name, e))
            continue
        if not found_game:
            res["missing_game_line_files"].append(fp.name)

    # llm-identity annotation (aggregate.py's notion): a seat name whose
    # controller is 'llm' in any game line of THIS dir. Annotating per
    # decision keeps combined stats correct when dirs disagree on which
    # identity is llm.
    llm_names = set()
    for g in res["games"]:
        for ident in ("a", "b"):
            if g.get("controller_%s" % ident) == "llm":
                name = g.get("deck_%s" % ident)
                if name is not None:
                    llm_names.add(str(name))  # str(): corrupt non-string deck
    res["llm_names"] = sorted(llm_names)      # names must not crash sorted/join
    for d in res["decisions"]:
        d["_llm"] = str(d.get("seat")) in llm_names
    return res


# ------------------------------------------------------------- helpers

def percentile(values, q):
    """Nearest-rank percentile (q in [0,1])."""
    if not values:
        return None
    vs = sorted(values)
    return vs[max(0, math.ceil(q * len(vs)) - 1)]


_FALLBACK_HOST = re.compile(r"\s*\[[^\]]*\]")


def normalize_fallback(reason):
    """Group key for a fallback reason: strip bracketed host card names, then
    everything after '->', ': ', or ' x<n>' (variable payloads like card
    names, counts, land names)."""
    s = _FALLBACK_HOST.sub("", str(reason))
    for sep in ("->", ": "):
        i = s.find(sep)
        if i >= 0:
            s = s[:i]
    s = re.sub(r"\s+x\d+.*$", "", s)
    return s.strip()


def model_consulted(d):
    """Ground truth for 'a model was consulted' is prompt != null — never the
    tier (forced single-candidate targets log tier smart with prompt null;
    rejected answers log tier auto/ai with prompt set)."""
    return d.get("prompt") is not None


def clean_key(v):
    """Coerce a grouping key (tier/type) to a non-empty str so a valid-JSON
    line with a wrong-shape field can never crash sorted()."""
    return v if isinstance(v, str) and v else "unknown"


def is_int(x):
    """int check that rejects bool (isinstance(True, int) is True)."""
    return isinstance(x, int) and not isinstance(x, bool)


def is_num(x):
    """int/float check that rejects bool."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ------------------------------------------------------------- summaries

def summarize(parse_results):
    """Build the full metrics dict over one or more parsed dirs."""
    decisions = [d for r in parse_results for d in r["decisions"]]
    games = [g for r in parse_results for g in r["games"]]

    # --- header counts
    n_files = sum(len(r["files"]) for r in parse_results)
    missing = [str(Path(r["dir"]) / f) for r in parse_results
               for f in r["missing_game_line_files"]]

    # --- type x tier matrix
    type_tier = defaultdict(Counter)
    tiers_seen = set()
    for d in decisions:
        ty = clean_key(d.get("type"))
        ti = clean_key(d.get("tier"))
        type_tier[ty][ti] += 1
        tiers_seen.add(ti)
    tier_cols = [t for t in TIER_ORDER if t in tiers_seen] + \
                sorted(tiers_seen - set(TIER_ORDER))
    type_tier_out = {ty: dict(c) for ty, c in type_tier.items()}

    # --- fallback grouping
    fb_groups = {}
    for d in decisions:
        fb = d.get("fallback")
        if fb is None:
            continue
        key = normalize_fallback(fb)
        g = fb_groups.setdefault(key, {"count": 0, "example": None})
        g["count"] += 1
        if g["example"] is None:
            g["example"] = {"seat": d.get("seat"), "turn": d.get("turn"),
                            "type": d.get("type"), "raw": fb,
                            "file": d.get("_file")}
    fb_out = dict(sorted(fb_groups.items(), key=lambda kv: -kv[1]["count"]))

    # --- llm-identity ai/auto rates (aggregate.py notion)
    llm_dec = [d for d in decisions if d.get("_llm")]
    n_llm = len(llm_dec)
    ai_n = sum(1 for d in llm_dec if d.get("tier") == "ai")
    auto_n = sum(1 for d in llm_dec if d.get("tier") == "auto")
    # aggregate.py's gate numerator additionally counts smart/fast lines that
    # carry a fallback reason — report it so this section can't under-read
    # the gate on the same dir.
    sf_fb_n = sum(1 for d in llm_dec
                  if d.get("tier") in ("smart", "fast")
                  and d.get("fallback") is not None)
    # dirs whose files carry smart/fast lines but no game line at all (crashed
    # games, live batch): their model decisions cannot be attributed to an
    # llm identity and are excluded from the denominator above.
    unattributed_dirs = sorted(
        r["dir"] for r in parse_results
        if not r["llm_names"]
        and any(d.get("tier") in ("smart", "fast") for d in r["decisions"]))
    llm_out = {
        "llm_identities": sorted({n for r in parse_results
                                  for n in r["llm_names"]}),
        "any_llm_arm": any(r["llm_names"] for r in parse_results),
        "n_llm_decisions": n_llm,
        "ai_count": ai_n,
        "auto_count": auto_n,
        "ai_rate": ai_n / n_llm if n_llm else None,
        "auto_rate": auto_n / n_llm if n_llm else None,
        "smart_fast_with_fallback_count": sf_fb_n,
        "dirs_with_unattributed_model_lines": unattributed_dirs,
        "ai_partial_caveat": AI_PARTIAL_CAVEAT,
    }

    # --- latency by tier (latency_ms > 0 only; many sites hardcode 0 even
    # when a model ran, so 0 means "not measured", not "instant")
    lat_by_tier = defaultdict(list)
    for d in decisions:
        lat = d.get("latency_ms")
        if is_num(lat) and lat > 0:
            lat_by_tier[clean_key(d.get("tier"))].append(lat)
    lat_out = {}
    for tier in sorted(lat_by_tier):
        vs = lat_by_tier[tier]
        lat_out[tier] = {"n": len(vs), "p50_ms": percentile(vs, 0.5),
                         "p90_ms": percentile(vs, 0.9), "max_ms": max(vs)}

    # --- n_options histogram summary by type
    nopt_out = {}
    by_type_nopt = defaultdict(list)
    no_menu = Counter()
    bad_nopt = Counter()  # wrong-shape field (string/bool/...), NOT the
    for d in decisions:   # legitimate -1 sentinel — kept apart so the
        n = d.get("n_options")  # no-menu column stays honest
        ty = clean_key(d.get("type"))
        if is_int(n) and n >= 0:
            by_type_nopt[ty].append(n)
        elif is_int(n):
            no_menu[ty] += 1  # -1 sentinel (auto/ai 6-arg lines, free-form)
        else:
            bad_nopt[ty] += 1
    for ty in sorted(set(by_type_nopt) | set(no_menu) | set(bad_nopt)):
        vs = by_type_nopt.get(ty, [])
        nopt_out[ty] = {
            "menus": len(vs), "no_menu": no_menu.get(ty, 0),
            "malformed_n_options": bad_nopt.get(ty, 0),
            "min": min(vs) if vs else None,
            "median": percentile(vs, 0.5),
            "p90": percentile(vs, 0.9),
            "max": max(vs) if vs else None,
        }

    # --- first-option bias (index-choice types only)
    bias_out = {}
    bias_acc = defaultdict(lambda: {"model": [0, 0], "auto": [0, 0],
                                    "inv_n": []})
    n_choice_oor = 0  # choice >= n_options: impossible index, data corruption
    for d in decisions:
        ty = clean_key(d.get("type"))
        if ty in COUNT_CHOICE_TYPES:
            continue
        n = d.get("n_options")
        ch = d.get("choice")
        if not (is_int(n) and n > 1 and is_int(ch) and ch >= 0):
            continue
        if ch >= n:
            n_choice_oor += 1  # excluded AND surfaced — never in denominators
            continue
        if d.get("fallback") is not None:
            # deterministic harness-forced pick (unparsed reply / dead bridge
            # always picks index 0): not a genuine choice by anyone; already
            # surfaced in the fallback-grouping section.
            continue
        a = bias_acc[ty]
        if model_consulted(d) and d.get("raw_reply") is not None:
            # model bucket = the model actually answered; prompt-without-reply
            # lines record the fallback's pick, not the model's
            a["model"][0] += 1
            a["model"][1] += 1 if ch == 0 else 0
            # play menus reserve the LAST slot for PASS (a pass logs
            # choice=-1), so for the non-pass lines counted here the
            # reachable menu is n-1 wide: uniform baseline is 1/(n-1)
            a["inv_n"].append(1.0 / (n - 1) if ty == "play" else 1.0 / n)
        elif d.get("tier") == "auto":
            a["auto"][0] += 1
            a["auto"][1] += 1 if ch == 0 else 0
    for ty in sorted(bias_acc):
        a = bias_acc[ty]
        mn, mf = a["model"]
        an, af = a["auto"]
        bias_out[ty] = {
            "model_n": mn,
            "model_first_rate": mf / mn if mn else None,
            "uniform_baseline": (sum(a["inv_n"]) / len(a["inv_n"])
                                 if a["inv_n"] else None),
            "auto_n": an,
            "auto_first_rate": af / an if an else None,
        }

    # --- per-game decision volume (per file)
    per_file = Counter()
    for d in decisions:
        per_file[(d.get("_dir"), d.get("_file"))] += 1
    vols = list(per_file.values())
    vol_out = {"games_with_decisions": len(vols),
               "mean": sum(vols) / len(vols) if vols else None,
               "max": max(vols) if vols else None}

    # --- prompt/raw_reply coverage
    n_model = sum(1 for d in decisions if model_consulted(d))
    n_full = sum(1 for d in decisions
                 if d.get("prompt") is not None
                 and d.get("raw_reply") is not None)
    n_dead = sum(1 for d in decisions
                 if d.get("prompt") is not None and d.get("raw_reply") is None)
    cov_out = {"model_consulted": n_model, "full_round_trip": n_full,
               "prompt_without_reply_bridge_death": n_dead}

    # --- un-routed surface: types never seen with a model tier
    any_model_tier = any(t in tiers_seen for t in ("smart", "fast"))
    unrouted = sorted(
        ty for ty, c in type_tier.items()
        if not (c.get("smart") or c.get("fast"))
    )
    # --- ai-handoff audit lines (invariant: zero)
    ai_lines = [{"dir": d.get("_dir"), "file": d.get("_file"),
                 "seat": d.get("seat"), "turn": d.get("turn"),
                 "type": d.get("type"), "fallback": d.get("fallback")}
                for d in decisions if d.get("tier") == "ai"]

    return {
        "n_files": n_files,
        "n_game_lines": len(games),
        "n_missing_game_line": len(missing),
        "missing_game_line_files": missing,
        "n_skipped_lines": sum(r["n_skipped_lines"] for r in parse_results),
        "n_legacy_lines": sum(r["n_legacy_lines"] for r in parse_results),
        "unreadable_files": [f for r in parse_results
                             for f in r["unreadable_files"]],
        "n_decisions": len(decisions),
        "tier_columns": tier_cols,
        "type_tier": type_tier_out,
        "fallbacks": fb_out,
        "llm": llm_out,
        "latency": lat_out,
        "n_options": nopt_out,
        "n_malformed_n_options": sum(bad_nopt.values()),
        "first_option_bias": bias_out,
        "n_choice_out_of_range": n_choice_oor,
        "volume": vol_out,
        "coverage": cov_out,
        "has_model_tier_lines": any_model_tier,
        "unrouted_types": unrouted,
        "ai_lines": ai_lines,
    }


def build_report(dirs):
    # dedupe repeated/path-aliased dirs so combined stats never double-count
    seen, uniq = set(), []
    for p in dirs:
        rp = Path(p).expanduser().resolve()
        if rp in seen:
            print("warning: duplicate run dir ignored: %s" % p,
                  file=sys.stderr)
            continue
        seen.add(rp)
        uniq.append(p)
    results = [parse_run_dir(p) for p in uniq]
    report = {
        "dirs": {r["dir"]: summarize([r]) for r in results},
        "combined": summarize(results),
        "invisible_surfaces": list(INVISIBLE_SURFACES),
        "invisible_caveat": INVISIBLE_CAVEAT,
    }
    return report


# --------------------------------------------------------------- output

def fmt_pct(x):
    return "-" if x is None else "%.1f%%" % (100.0 * x)


def fmt_ms(x):
    return "-" if x is None else "%.0f" % x


def _scope_lines(name, s):
    """Render one scope (a dir or 'combined') as a list of text lines."""
    L = []
    L.append("=== %s ===" % name)
    L.append("files=%d  game_lines=%d  missing_game_line->invalid=%d  "
             "skipped_malformed_lines=%d  legacy_short_lines=%d  decisions=%d"
             % (s["n_files"], s["n_game_lines"], s["n_missing_game_line"],
                s["n_skipped_lines"], s["n_legacy_lines"], s["n_decisions"]))
    for f in s["missing_game_line_files"]:
        L.append("  (no game line, decisions still counted: %s)" % f)
    for f in s["unreadable_files"]:
        L.append("  (unreadable: %s)" % f)
    if not s["n_decisions"]:
        L.append("(no decision lines)")
        return L

    # type x tier
    cols = s["tier_columns"]
    L.append("")
    L.append("--- decision counts by type x tier ---")
    hdr = "%-18s" % "type" + "".join("%8s" % c for c in cols) + "%8s" % "total"
    L.append(hdr)
    for ty, c in sorted(s["type_tier"].items(),
                        key=lambda kv: -sum(kv[1].values())):
        row = "%-18s" % ty + "".join("%8d" % c.get(col, 0) for col in cols)
        L.append(row + "%8d" % sum(c.values()))

    # fallbacks
    L.append("")
    L.append("--- fallbacks by normalized reason ---")
    if not s["fallbacks"]:
        L.append("(none)")
    for reason, g in s["fallbacks"].items():
        ex = g["example"] or {}
        L.append("%-34s %6d   e.g. %s t%s %s: %r [%s]"
                 % (reason, g["count"], ex.get("seat"), ex.get("turn"),
                    ex.get("type"), ex.get("raw"), ex.get("file")))

    # llm-identity tier rates
    L.append("")
    L.append("--- llm-identity fallback tiers (seats whose controller is llm) ---")
    llm = s["llm"]
    if not llm["any_llm_arm"]:
        if llm["dirs_with_unattributed_model_lines"]:
            L.append("WARNING: model arm present but unidentifiable — "
                     "smart/fast lines exist but no game line names an llm "
                     "identity (crashed games or a live batch before its "
                     "first game completes); ai/auto rates unavailable")
        else:
            L.append("no llm arm in this scope -> rates not applicable")
    else:
        L.append("llm identities: %s" % ", ".join(llm["llm_identities"]))
        L.append("llm decisions=%d   ai=%d (%s)   auto=%d (%s)   "
                 "smart/fast-with-fallback-reason=%d"
                 % (llm["n_llm_decisions"], llm["ai_count"],
                    fmt_pct(llm["ai_rate"]), llm["auto_count"],
                    fmt_pct(llm["auto_rate"]),
                    llm["smart_fast_with_fallback_count"]))
        L.append("(aggregate.py's gate numerator = ai + auto + "
                 "smart/fast-with-fallback-reason)")
        for ud in llm["dirs_with_unattributed_model_lines"]:
            L.append("WARNING: %s has smart/fast lines but no game lines — "
                     "its model decisions are EXCLUDED from the llm "
                     "denominator above" % ud)
    L.append(AI_PARTIAL_CAVEAT)

    # latency
    L.append("")
    L.append("--- latency by tier (ms; only lines with latency_ms>0 — several "
             "sites hardcode 0 even when a model ran) ---")
    if not s["latency"]:
        L.append("(no measured latencies)")
    else:
        L.append("%-8s%8s%9s%9s%9s" % ("tier", "n", "p50", "p90", "max"))
        for tier, t in s["latency"].items():
            L.append("%-8s%8d%9s%9s%9s" % (tier, t["n"], fmt_ms(t["p50_ms"]),
                                           fmt_ms(t["p90_ms"]),
                                           fmt_ms(t["max_ms"])))

    # n_options
    L.append("")
    L.append("--- option-count (n_options) by type ---")
    L.append("%-18s%8s%10s%6s%6s%6s%6s%6s"
             % ("type", "menus", "no-menu", "bad", "min", "med", "p90", "max"))
    for ty, o in sorted(s["n_options"].items(),
                        key=lambda kv: -(kv[1]["menus"] + kv[1]["no_menu"])):
        L.append("%-18s%8d%10d%6d%6s%6s%6s%6s"
                 % (ty, o["menus"], o["no_menu"], o["malformed_n_options"],
                    "-" if o["min"] is None else o["min"],
                    "-" if o["median"] is None else o["median"],
                    "-" if o["p90"] is None else o["p90"],
                    "-" if o["max"] is None else o["max"]))
    if s["n_malformed_n_options"]:
        L.append("('bad' = wrong-shape n_options field, e.g. a string — NOT "
                 "the legitimate -1 no-menu sentinel: %d line(s))"
                 % s["n_malformed_n_options"])

    # first-option bias
    L.append("")
    L.append("--- first-option pick rate (index-choice types, n_options>1, "
             "0<=choice<n; model = model answered, fallback-decided lines "
             "excluded) ---")
    L.append("%-18s%9s%10s%10s%9s%10s"
             % ("type", "model_n", "P(first)", "1/n base", "auto_n",
                "P(first)"))
    if not s["first_option_bias"]:
        L.append("(no eligible lines)")
    for ty, b in s["first_option_bias"].items():
        L.append("%-18s%9d%10s%10s%9d%10s"
                 % (ty, b["model_n"], fmt_pct(b["model_first_rate"]),
                    fmt_pct(b["uniform_baseline"]), b["auto_n"],
                    fmt_pct(b["auto_first_rate"])))
    if "play" in s["first_option_bias"]:
        L.append(PLAY_BASELINE_NOTE)
    if s["n_choice_out_of_range"]:
        L.append("WARNING: %d line(s) with choice >= n_options (impossible "
                 "index — emitter or data corruption) excluded from the "
                 "table above" % s["n_choice_out_of_range"])

    # volume
    L.append("")
    v = s["volume"]
    L.append("--- per-game decision volume ---")
    L.append("games_with_decisions=%d  mean=%s  max=%s"
             % (v["games_with_decisions"],
                "-" if v["mean"] is None else "%.1f" % v["mean"],
                v["max"] if v["max"] is not None else "-"))

    # coverage
    c = s["coverage"]
    L.append("")
    L.append("--- prompt/raw_reply coverage ---")
    L.append("model-consulted (prompt!=null): %d   full round-trip "
             "(prompt+raw_reply): %d   prompt-without-reply (bridge died "
             "mid-call): %d"
             % (c["model_consulted"], c["full_round_trip"],
                c["prompt_without_reply_bridge_death"]))

    # ai audit
    L.append("")
    L.append("--- ai-tier handoff audit (invariant: zero; each line is "
             "bug-adjacent) ---")
    if not s["ai_lines"]:
        L.append("(none)")
    for a in s["ai_lines"]:
        L.append("  %s/%s seat=%s turn=%s type=%s fallback=%r"
                 % (a["dir"], a["file"], a["seat"], a["turn"], a["type"],
                    a["fallback"]))
    L.append("(the ai counter is a LOWER BOUND — see the ai-PARTIAL note "
             "in the llm-identity section above)")
    return L


def _play_tightening_lines(report):
    s = report["combined"]
    L = []
    L.append("=== PLAY-TIGHTENING: the un-routed surface (combined) ===")
    if not s["has_model_tier_lines"]:
        L.append("NOTE: no smart/fast lines anywhere in this corpus (no model "
                 "arm ran) — the 'unrouted' list below is trivially every "
                 "type and not meaningful for routing gaps.")
    if s["unrouted_types"]:
        L.append("decision types seen ONLY with tier auto/ai (never routed to "
                 "a model):")
        for ty in s["unrouted_types"]:
            c = s["type_tier"].get(ty, {})
            L.append("  %-18s %d line(s): %s"
                     % (ty, sum(c.values()),
                        ", ".join("%s=%d" % (k, v)
                                  for k, v in sorted(c.items()))))
    else:
        L.append("every observed decision type has at least one model-routed "
                 "(smart/fast) line.")
    L.append("")
    L.append("structurally invisible surfaces (NO decision lines exist for "
             "these, by construction):")
    for surf in report["invisible_surfaces"]:
        L.append("  - %s" % surf)
    L.append(report["invisible_caveat"])
    return L


def render_text(report):
    lines = []
    dir_names = list(report["dirs"])
    for name in dir_names:
        lines.extend(_scope_lines(name, report["dirs"][name]))
        lines.append("")
    if len(dir_names) > 1:
        lines.extend(_scope_lines("COMBINED (%d dirs)" % len(dir_names),
                                  report["combined"]))
        lines.append("")
    lines.extend(_play_tightening_lines(report))
    return "\n".join(lines)


def render_markdown(report):
    """Markdown rendering: same content, headings + fenced tables."""
    out = ["# Decision-corpus analysis", ""]
    dir_names = list(report["dirs"])
    scopes = [(n, report["dirs"][n]) for n in dir_names]
    if len(dir_names) > 1:
        scopes.append(("COMBINED (%d dirs)" % len(dir_names),
                       report["combined"]))
    for name, s in scopes:
        body = "\n".join(_scope_lines(name, s)[1:])
        out.append("## %s" % name)
        out.append("")
        out.append("```")
        out.append(body)
        out.append("```")
        out.append("")
    out.append("## Play-tightening: the un-routed surface")
    out.append("")
    out.append("```")
    out.append("\n".join(_play_tightening_lines(report)[1:]))
    out.append("```")
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", metavar="run_dir",
                    help="run directories of *.jsonl game files")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of text")
    ap.add_argument("--markdown", metavar="PATH",
                    help="also write a markdown version of the report to PATH")
    args = ap.parse_args()

    # never let card-name unicode crash a report on a non-UTF-8 stdout
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    for d in args.dirs:
        if not Path(d).expanduser().is_dir():
            print("not a directory: %s" % d, file=sys.stderr)
            sys.exit(1)

    report = build_report([Path(d).expanduser() for d in args.dirs])

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        print(render_text(report))

    if args.markdown:
        Path(args.markdown).write_text(render_markdown(report),
                                       encoding="utf-8")
        if not args.json:
            print("\n(markdown written to %s)" % args.markdown)

    if report["combined"]["n_decisions"] == 0:
        print("no decision lines found in %s" % ", ".join(args.dirs),
              file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
