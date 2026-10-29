#!/usr/bin/env python3
"""Action-coverage audit over ALREADY-LOGGED MTG Forge decision JSONL.

Tabulates the distribution of decision `type` (play / target / attackers /
blockers / order / confirm / surveil / mulligan / damage-assign / number /
modes / discard / reveal / entity / zone-change / ...) across one or more run
dirs, so we can see which decision FAMILIES the corpus actually exercises and
which are underrepresented. No new games, no model calls: read-only over disk.

Three breakdowns:
  overall  — every decision type, split total / real / forced.
  per tier — smart / fast / auto / ai / ai-teacher, real decisions only.
  per deck — resolved deck ARCHETYPE, real decisions only.

"real" vs "forced": a row with n_options > 1 is a genuine choice; a row with
n_options <= 1 was forced (a single legal action / auto-confirm) and the model
had nothing to decide. Both are reported, but the coverage story is about REAL
decisions — a family that only ever shows up with n_options==1 (e.g. surveil of
a single card) has no real coverage no matter how many rows it has.

DECK IDENTITY. A decision row's `seat` is an identity/display name, which in
cited mirrors is a generic label ("Identity-A") and in tournaments is a model
name — NOT the archetype. The archetype is recovered, in priority order:
  1. the game line's `deck_sha` matched against sha256[:12] of eval/decks/*.txt
     (mirror runs stamp one sha for both seats -> both seats resolve);
  2. else the seat/deck display name slugged to a deck-file stem
     ("UR Delver" -> ur-delver, "Grixis Reanimator" -> grixis-reanimator);
  3. else "unresolved:<name>" (tournament model seats, unknown shas).

Only decisions from a seat that ever made a real (n_options>1) decision are
attributed to a deck; Forge-AI opponent seats emit no decision rows at all, so
a vs-AI run only ever logs the LLM archetype.

DEDUP. game-*.jsonl files are globbed recursively under each input dir. Reruns
of the same (seed, swap) — which live in the same directory — are deduped
per (containing-dir, seed, swap), keeping the lexicographically-last file
(filenames carry a trailing millis, so "last" is the latest rerun), matching
aggregate.py semantics. The same seed in two DIFFERENT run dirs is kept (they
are different runs), so pointing at several corpora never collapses them.

Usage:
  python3 eval/coverage_audit.py <run-dir> [<run-dir> ...] [--json [PATH]]
                                 [--decks-dir eval/decks] [--min-share PCT]

Exit codes: 0 success; 1 usage/input error (no dirs, none readable).
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

# Repo root = parent of eval/. Default decks dir lives here.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DECKS_DIR = REPO_ROOT / "eval" / "decks"

# Canonical decision FAMILIES we expect a well-rounded corpus to exercise.
# A family listed here that never appears (or only appears forced) is called
# out as an absent/underrepresented gap even if the corpus is otherwise large.
EXPECTED_FAMILIES = (
    "play", "target", "targets", "attackers", "blockers", "order",
    "confirm", "surveil", "mulligan", "mulligan-bottom", "damage-assign",
    "number", "modes", "discard", "reveal", "entity", "choose-entity",
    "card-name", "zone-change", "sacrifice", "cards-for-effect",
)

# Below this share of all REAL decisions a present family is flagged thin.
DEFAULT_MIN_SHARE = 1.0  # percent


# ---------------------------------------------------------------- deck ids

def slugify(name):
    """'UR Delver' -> 'ur-delver'; collapse non-alnum runs to single '-'."""
    if not isinstance(name, str):
        return ""
    out, prev_dash = [], False
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        else:
            if not prev_dash:
                out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def load_deck_sha_map(decks_dir):
    """{sha256[:12]: stem} over decks_dir/*.txt (same hash as run_batch.py).
    Also returns the set of known deck stems for the name-slug fallback."""
    sha_map, stems = {}, set()
    p = Path(decks_dir).expanduser()
    if not p.is_dir():
        return sha_map, stems
    for f in sorted(p.glob("*.txt")):
        try:
            sha = hashlib.sha256(f.read_bytes()).hexdigest()[:12]
        except OSError:
            continue
        sha_map[sha] = f.stem
        stems.add(f.stem)
    return sha_map, stems


def resolve_deck(seat, deck_sha, sha_map, stems):
    """Archetype for a decision's seat: name-slug first (per-seat precision),
    then deck_sha (whole-game, works for mirror identity seats), else
    'unresolved:<seat>'."""
    slug = slugify(seat)
    if slug in stems:
        return slug
    if isinstance(deck_sha, str) and deck_sha in sha_map:
        return sha_map[deck_sha]
    return "unresolved:%s" % (seat if seat else "?")


# ---------------------------------------------------------------- loading

def _iter_game_files(run_dir):
    return sorted(Path(run_dir).expanduser().rglob("game-*.jsonl"))


def load_files(run_dirs):
    """Parse every game-*.jsonl under each run dir.

    Returns (records, warnings). `records` is a list of per-file dicts
    {meta, decisions} where meta carries the game line's seed/swap/deck_sha/
    deck names, and decisions is the list of decision rows in that file.
    Reruns of one (dir, seed, swap) are deduped, latest file kept.
    """
    warnings = []
    # (containing_dir, seed, swap) -> (filename_sort_key, record)
    by_key = {}
    loose = []  # files with no game line: keep, can't dedup on seed/swap
    n_files = 0
    for rd in run_dirs:
        rp = Path(rd).expanduser()
        if not rp.is_dir():
            warnings.append("not a directory (skipped): %s" % rd)
            continue
        files = _iter_game_files(rp)
        if not files:
            warnings.append("no game-*.jsonl under %s" % rd)
        for fp in files:
            n_files += 1
            game_line, decisions, n_bad = None, [], 0
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
                            decisions.append(rec)
                        elif t == "game":
                            game_line = rec  # last game line wins as meta
            except OSError as e:
                warnings.append("unreadable %s: %s" % (fp.name, e))
                continue
            if n_bad:
                warnings.append("%s: skipped %d malformed line(s)"
                                % (fp.name, n_bad))
            meta = {}
            if game_line is not None:
                meta = {
                    "seed": game_line.get("seed"),
                    "swap": bool(game_line.get("swap", False)),
                    "deck_sha": game_line.get("deck_sha"),
                    "deck_a": game_line.get("deck_a"),
                    "deck_b": game_line.get("deck_b"),
                }
            rec = {"file": fp.name, "meta": meta, "decisions": decisions}
            if game_line is None or meta.get("seed") is None:
                loose.append(rec)
                continue
            key = (str(fp.parent), meta["seed"], meta["swap"])
            prev = by_key.get(key)
            if prev is None or fp.name > prev[0]:
                if prev is not None:
                    warnings.append("rerun for %s seed=%s swap=%s: kept %s, "
                                    "dropped %s" % (fp.parent.name, meta["seed"],
                                                    meta["swap"], fp.name, prev[1]["file"]))
                by_key[key] = (fp.name, rec)
            else:
                warnings.append("rerun for %s seed=%s swap=%s: kept %s, "
                                "dropped %s" % (fp.parent.name, meta["seed"],
                                                meta["swap"], prev[1]["file"], fp.name))
    records = [r for _, r in by_key.values()] + loose
    return records, warnings, n_files


# --------------------------------------------------------------- tabulate

def is_real(d):
    n = d.get("n_options")
    return isinstance(n, int) and not isinstance(n, bool) and n > 1


def tabulate(records, sha_map, stems):
    """Build the overall / per-tier / per-deck distributions."""
    overall = defaultdict(lambda: {"total": 0, "real": 0, "forced": 0})
    by_tier = defaultdict(lambda: defaultdict(lambda: {"total": 0, "real": 0}))
    by_deck = defaultdict(lambda: defaultdict(lambda: {"total": 0, "real": 0}))
    # A seat only counts as a "deck" if it ever made a real decision (so we do
    # not attribute a lone forced auto-row from an un-overridden AI seat).
    real_seats = set()
    n_decisions = 0

    for r in records:
        deck_sha = r["meta"].get("deck_sha")
        for d in r["decisions"]:
            n_decisions += 1
            typ = d.get("type") or "unknown"
            tier = d.get("tier") or "unknown"
            seat = d.get("seat")
            real = is_real(d)
            o = overall[typ]
            o["total"] += 1
            o["real" if real else "forced"] += 1
            bt = by_tier[tier][typ]
            bt["total"] += 1
            if real:
                bt["real"] += 1
            deck = resolve_deck(seat, deck_sha, sha_map, stems)
            bd = by_deck[deck][typ]
            bd["total"] += 1
            if real:
                bd["real"] += 1
                real_seats.add(deck)

    # Drop decks that never made a real decision (forced-only opponent noise).
    by_deck = {k: v for k, v in by_deck.items() if k in real_seats}
    return {
        "n_decisions": n_decisions,
        "overall": {k: dict(v) for k, v in overall.items()},
        "by_tier": {t: {k: dict(v) for k, v in d.items()}
                    for t, d in by_tier.items()},
        "by_deck": {t: {k: dict(v) for k, v in d.items()}
                    for t, d in by_deck.items()},
    }


def find_gaps(overall, min_share_pct):
    """Absent / forced-only / thin families vs EXPECTED_FAMILIES + present."""
    total_real = sum(v["real"] for v in overall.values()) or 1
    absent, forced_only, thin = [], [], []
    present = set(overall)
    for fam in EXPECTED_FAMILIES:
        v = overall.get(fam)
        if v is None or v["total"] == 0:
            absent.append(fam)
        elif v["real"] == 0:
            forced_only.append(fam)
    # thin = present WITH real coverage but under the share floor.
    for fam in sorted(present):
        v = overall[fam]
        if v["real"] == 0:
            continue
        share = 100.0 * v["real"] / total_real
        if share < min_share_pct:
            thin.append((fam, v["real"], share))
    thin.sort(key=lambda x: x[1])
    return {"total_real": total_real, "absent": absent,
            "forced_only": forced_only, "thin": thin}


def build_report(run_dirs, decks_dir, min_share_pct):
    sha_map, stems = load_deck_sha_map(decks_dir)
    records, warnings, n_files = load_files(run_dirs)
    tab = tabulate(records, sha_map, stems)
    gaps = find_gaps(tab["overall"], min_share_pct)
    return {
        "run_dirs": [str(d) for d in run_dirs],
        "decks_dir": str(decks_dir),
        "n_files": n_files,
        "n_games_used": len(records),
        "n_decisions": tab["n_decisions"],
        "known_decks": sorted(stems),
        "min_share_pct": min_share_pct,
        "overall": tab["overall"],
        "by_tier": tab["by_tier"],
        "by_deck": tab["by_deck"],
        "gaps": gaps,
        "warnings": warnings,
    }


# --------------------------------------------------------------- rendering

def _bar(frac, width=24):
    n = int(round(frac * width))
    return "#" * n + "." * (width - n)


def render_text(r):
    L = []
    L.append("=" * 74)
    L.append("ACTION-COVERAGE AUDIT — decision-type distribution over logged games")
    L.append("=" * 74)
    L.append("run dirs: %s" % ", ".join(r["run_dirs"]))
    L.append("files scanned: %d   games used (deduped): %d   decisions: %d"
             % (r["n_files"], r["n_games_used"], r["n_decisions"]))

    ov = r["overall"]
    total_real = r["gaps"]["total_real"]
    L.append("")
    L.append("### OVERALL decision-type distribution")
    L.append("  (real = n_options>1 genuine choices; forced = n_options<=1)")
    L.append("  %-16s %8s %8s %8s  %6s  %s"
             % ("type", "total", "real", "forced", "%real", "real share"))
    for typ, v in sorted(ov.items(), key=lambda kv: -kv[1]["real"]):
        share = v["real"] / total_real if total_real else 0.0
        L.append("  %-16s %8d %8d %8d  %5.1f%%  %s"
                 % (typ, v["total"], v["real"], v["forced"],
                    100.0 * share, _bar(share)))
    L.append("  %-16s %8d %8d %8d"
             % ("TOTAL", sum(v["total"] for v in ov.values()),
                total_real, sum(v["forced"] for v in ov.values())))

    L.append("")
    L.append("### BY TIER (real decisions only)")
    tiers = r["by_tier"]
    # column set = types that ever appear real, ordered by overall real volume.
    typ_order = [t for t, _ in sorted(ov.items(), key=lambda kv: -kv[1]["real"])
                 if ov[t]["real"] > 0]
    for tier in sorted(tiers):
        row = tiers[tier]
        tot = sum(v["real"] for v in row.values())
        parts = ["%s=%d" % (t, row[t]["real"]) for t in typ_order
                 if t in row and row[t]["real"] > 0]
        L.append("  %-8s real=%-6d  %s" % (tier, tot, "  ".join(parts)))

    L.append("")
    L.append("### BY DECK ARCHETYPE (real decisions only)")
    L.append("  resolved via deck_sha / seat-name slug; unresolved: = "
             "tournament model seats / unknown sha")
    decks = r["by_deck"]
    for deck in sorted(decks, key=lambda k: -sum(v["real"] for v in decks[k].values())):
        row = decks[deck]
        tot = sum(v["real"] for v in row.values())
        parts = ["%s=%d" % (t, row[t]["real"]) for t in typ_order
                 if t in row and row[t]["real"] > 0]
        L.append("  %-22s real=%-6d  %s" % (deck, tot, "  ".join(parts)))

    g = r["gaps"]
    L.append("")
    L.append("### UNDERREPRESENTED / MISSING decision families")
    L.append("  ABSENT (expected family, zero rows on disk): %s"
             % (", ".join(g["absent"]) or "(none)"))
    L.append("  FORCED-ONLY (present but never a real n>1 choice): %s"
             % (", ".join(g["forced_only"]) or "(none)"))
    L.append("  THIN (real, but < %.1f%% of real decisions):" % r["min_share_pct"])
    if g["thin"]:
        for fam, n, share in g["thin"]:
            L.append("     %-16s real=%-5d  %.2f%%" % (fam, n, share))
    else:
        L.append("     (none)")

    if r["warnings"]:
        L.append("")
        L.append("### warnings (%d)" % len(r["warnings"]))
        for w in r["warnings"][:30]:
            L.append("  - " + w)
        if len(r["warnings"]) > 30:
            L.append("  ... %d more" % (len(r["warnings"]) - 30))
    return "\n".join(L)


# --------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dirs", nargs="+", help="one or more run dirs of game-*.jsonl")
    ap.add_argument("--decks-dir", default=str(DEFAULT_DECKS_DIR),
                    help="deck files for sha/name resolution (default eval/decks)")
    ap.add_argument("--min-share", type=float, default=DEFAULT_MIN_SHARE,
                    help="flag a present family as THIN below this %% of real "
                         "decisions (default %(default).1f)")
    ap.add_argument("--json", metavar="PATH", nargs="?", const="-",
                    help="emit machine-readable JSON (to PATH, or stdout if bare)")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    if not any(Path(d).expanduser().is_dir() for d in args.run_dirs):
        print("no readable run dir among: %s" % ", ".join(args.run_dirs),
              file=sys.stderr)
        sys.exit(1)

    report = build_report(args.run_dirs, args.decks_dir, args.min_share)

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
