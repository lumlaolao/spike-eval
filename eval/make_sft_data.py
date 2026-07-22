#!/usr/bin/env python3
"""Build Stage-1 SFT data: winning MTG trajectories -> MLX chat-format JSONL.

Takes the eval JSONL game logs, keeps only COMPLETE games the LLM-piloted seat
WON, and emits one training example per model-answered decision from that
winning seat:

    system    = the game's EXACT reconstructed teacher preamble for its
                (build, tier, seat, plan) — see PreambleBuilder below
    user      = the decision row `prompt` field, byte-exact (the state snapshot
                + the specific question as the model saw it)
    assistant = the decision row `raw_reply` field, byte-exact (the accepted
                winning reply; NOT first_failed_reply)

Smart-tier rows are the primary set; fast-tier rows are included but carry
tier="fast" in the aligned meta so a downstream mix weight can be tuned (they
use the terse FORMAT line and never get the STATIC_REFERENCE appendix).

Nothing in the replies is rewritten — examples are verbatim.

WHY THE SYSTEM MESSAGE IS RECONSTRUCTED, NOT READ
-------------------------------------------------
No run artifact records the system message (the jsonl has no system field; the
.log files are Forge engine stdout only). The harness bridge is STATELESS: every
model call sends exactly [system=preamble, user=prompt] with no history, so
system+prompt fully reconstruct the request. The preamble is produced by
PlayerControllerLLM.framing(seat, plan, terse) in LlmSpikeGui.java. We extract
its string literals PROGRAMMATICALLY from the Java source (rather than
hand-retyping ~9 KB of text) to stay byte-exact, and self-check the result
against known character counts (verified against the logged prompt_tokens of the
cache-cold first smart call in both the deepseek and gemini corpora).

PER-BUILD PREAMBLE (authoritative source = the run DIRECTORY, per recon):
  STAGE0 (commit 339f457 == current working tree): smart tier gets
         base(seat,plan,verbose) + "\n\n" + STATIC_REFERENCE; fast gets
         base(seat,plan,terse).
  FIX    (commit c3ec3d2): smart tier gets base(seat,plan,verbose) only (no
         STATIC_REFERENCE); fast gets base(seat,plan,terse). The `base` text is
         byte-identical to STAGE0's (verified: the only diff between the two
         commits is wrapping the old return into `String base = ...` and adding
         the terse/STATIC_REFERENCE branch), so a single working-tree Java file
         reconstructs both builds.

Usage:
  python3 eval/make_sft_data.py \
      [runs/stage0-calibration:STAGE0 runs/ab-quality-1/fix:FIX] \
      [--out-dir datasets/stage1-v0] [--val-frac 0.1] [--split-seed 20260722] \
      [--java <path to LlmSpikeGui.java>]

Defaults scan the two usable sources (STAGE0 deepseek + FIX gemini). Pass
DIR:BUILD pairs to override; BUILD is STAGE0 or FIX.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JAVA = (REPO_ROOT / "forge-overlay" / "forge-gui-desktop" / "src"
                / "main" / "java" / "forge" / "spike" / "LlmSpikeGui.java")

# (relative dir, build tag). Build tag selects the preamble shape (see below).
DEFAULT_SOURCES = [
    ("runs/stage0-calibration", "STAGE0"),
    ("runs/ab-quality-1/fix", "FIX"),
]

# build tag -> does the SMART tier get the STATIC_REFERENCE appendix?  + commit
# for provenance. fast tier never gets STATIC_REFERENCE in any build.
BUILDS = {
    "STAGE0": {"static_reference_on_smart": True, "commit": "339f457"},
    "FIX":    {"static_reference_on_smart": False, "commit": "c3ec3d2"},
}

MODEL_TIERS = ("smart", "fast")  # only model-authored tiers become examples

# Expected character lengths — a guard against silent drift in the Java source.
EXPECT_STATIC_REFERENCE_LEN = 7492


# ------------------------------------------------------- Java literal parsing

def _unescape_java(s):
    """Decode a Java string-literal body (already stripped of the quotes)."""
    out, i, n = [], 0, len(s)
    simple = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\',
              "'": "'", 'b': '\b', 'f': '\f', '0': '\0'}
    while i < n:
        c = s[i]
        if c == '\\' and i + 1 < n:
            nxt = s[i + 1]
            if nxt in simple:
                out.append(simple[nxt]); i += 2; continue
            if nxt == 'u':
                out.append(chr(int(s[i + 2:i + 6], 16))); i += 6; continue
        out.append(c); i += 1
    return ''.join(out)


def _scan_stmt(text, start):
    """Tokenize a Java statement starting at `start` until the first top-level
    ';'. Returns a list of ('lit', value) and ('op', ch) tokens where op is one
    of ? : ; . Double-quoted string literals and // line comments are handled so
    that ';' or quotes inside them are not mistaken for structure."""
    toks, i, n = [], start, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j, buf = i + 1, []
            while j < n:
                cj = text[j]
                if cj == '\\':
                    buf.append(text[j:j + 2]); j += 2; continue
                if cj == '"':
                    break
                buf.append(cj); j += 1
            toks.append(('lit', _unescape_java(''.join(buf))))
            i = j + 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '/':
            i = text.index('\n', i) + 1
            continue
        if c in '?:;':
            toks.append(('op', c))
            if c == ';':
                break
        i += 1
    return toks


class PreambleBuilder:
    """Reconstructs the exact teacher system message from the Java source.

    Extracts three pieces from framing()/STATIC_REFERENCE and reassembles them
    the way framing() does, interpolating only the two dynamic parts (seat,
    plan). Self-checks the pieces against structural anchors + a length guard so
    a future edit to the Java source can never silently corrupt the preamble.
    """

    def __init__(self, java_path):
        src = Path(java_path).read_text(encoding="utf-8")

        # 1. FORMAT ternary:  terse ? <lits> : <lits> ;
        ftoks = _scan_stmt(src, src.index("String format = terse"))
        qi = next(k for k, t in enumerate(ftoks) if t == ('op', '?'))
        ci = next(k for k, t in enumerate(ftoks) if t == ('op', ':'))
        self.format_terse = ''.join(v for kind, v in ftoks[qi + 1:ci]
                                    if kind == 'lit')
        self.format_verbose = ''.join(v for kind, v in ftoks[ci + 1:]
                                      if kind == 'lit')

        # 2. base = "You are ...piloting \"" + seat + "\".\n"
        #           + (plan? "GAME PLAN: " + plan + "\n")
        #           + <PROTOCOL/PRINCIPLES literals> + format
        #           + " Play to WIN ...";
        # Drop empty literals (the ternary's "" branch and the //-comment's ""
        # tokens if the comment scanner ever misses one), then the layout is
        # deterministic: [seat_prefix, seat_suffix, "GAME PLAN: ", "\n",
        #                 <body...>, tail].
        blits = [v for kind, v in _scan_stmt(src, src.index("String base ="))
                 if kind == 'lit' and v != '']
        self.seat_prefix, self.seat_suffix = blits[0], blits[1]
        self.plan_prefix, self.plan_suffix = blits[2], blits[3]
        self.body = ''.join(blits[4:-1])
        self.tail = blits[-1]

        # 3. STATIC_REFERENCE: one pure constant, no interpolation.
        self.static_reference = ''.join(
            v for kind, v in _scan_stmt(src, src.index("String STATIC_REFERENCE ="))
            if kind == 'lit')

        # --- structural anchors: fail loudly if the source layout drifted ---
        assert self.seat_prefix.endswith('piloting "'), repr(self.seat_prefix)
        assert self.seat_suffix == '".\n', repr(self.seat_suffix)
        assert self.plan_prefix == 'GAME PLAN: ', repr(self.plan_prefix)
        assert self.plan_suffix == '\n', repr(self.plan_suffix)
        assert self.body.startswith('PROTOCOL:'), repr(self.body[:20])
        assert self.tail.startswith(' Play to WIN'), repr(self.tail)
        assert self.format_terse.startswith('FORMAT: reply with ONLY'), \
            repr(self.format_terse[:24])
        assert self.format_verbose.startswith('FORMAT: at most two'), \
            repr(self.format_verbose[:24])
        assert len(self.static_reference) == EXPECT_STATIC_REFERENCE_LEN, \
            "STATIC_REFERENCE is %d chars, expected %d" % (
                len(self.static_reference), EXPECT_STATIC_REFERENCE_LEN)

    def base(self, seat, plan, terse):
        fmt = self.format_terse if terse else self.format_verbose
        plan_block = "" if not plan else self.plan_prefix + plan + self.plan_suffix
        return (self.seat_prefix + seat + self.seat_suffix + plan_block
                + self.body + fmt + self.tail)

    def system(self, seat, plan, tier, build):
        """Exact system message for a (seat, plan, tier, build) decision."""
        terse = (tier == "fast")
        msg = self.base(seat, plan, terse)
        if (not terse) and BUILDS[build]["static_reference_on_smart"]:
            msg = msg + "\n\n" + self.static_reference
        return msg


# ------------------------------------------------------------ game parsing

def load_complete_games(dir_path):
    """Yield one dict per COMPLETE game in dir_path.

    A file is complete iff it contains a game-summary line (t=="game"); files
    without one are in-flight (live batches append these) and are skipped
    entirely. One game per file is assumed (the harness writes game-<seed>-<ms>);
    a file with several game lines is skipped with a warning because its
    decisions cannot be unambiguously attributed.

    Reruns of the same (seed, swap) are deduped keeping the newest file (glob
    sorts by the trailing epoch-millis), matching aggregate.py semantics.

    Returns (games, warnings) where each game is:
      {"file", "game": <game line>, "decisions": [<decision rows in file order>]}
    """
    warnings = []
    by_key = {}   # (seed, swap) -> game dict (last wins)
    for fp in sorted(Path(dir_path).glob("game-*.jsonl")):
        game_lines, decisions, n_bad = [], [], 0
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
                    if rec.get("t") == "decision":
                        decisions.append(rec)
                    elif rec.get("t") == "game":
                        game_lines.append(rec)
        except OSError as e:
            warnings.append("unreadable %s: %s" % (fp.name, e))
            continue
        if n_bad:
            warnings.append("%s: skipped %d malformed line(s)" % (fp.name, n_bad))
        if not game_lines:
            # In-flight file (no summary line yet) — skip silently; these are
            # expected while live batches run.
            continue
        if len(game_lines) > 1:
            warnings.append("%s: %d game lines in one file -> skipped (cannot "
                            "attribute decisions)" % (fp.name, len(game_lines)))
            continue
        g = game_lines[0]
        key = (g.get("seed"), bool(g.get("swap", False)))
        if key in by_key:
            warnings.append("dup (seed=%s swap=%s): keeping %s over %s"
                            % (key[0], key[1], fp.name, by_key[key]["file"]))
        by_key[key] = {"file": fp.name, "game": g, "decisions": decisions}
    return list(by_key.values()), warnings


def winning_llm_identities(game):
    """Return the identity letters ('A'/'B') whose controller is 'llm' AND who
    WON this game. winner is IDENTITY-keyed (never flipped by swap)."""
    winner = game.get("winner")
    out = []
    for ident, letter in (("a", "A"), ("b", "B")):
        if game.get("controller_%s" % ident) == "llm" and winner == letter:
            out.append(ident)
    return out


# ------------------------------------------------------------- dedup / split

_OBJ_ID = re.compile(r" \(\d+\)")


def normalize_state(prompt):
    """Normalize volatile Forge object ids for the dedup KEY only (the emitted
    example always keeps the raw prompt verbatim).

    Forge stamps engine object ids into the RECENT EVENTS log as a
    space-preceded parenthesized integer, e.g. `Burn played Mountain (28)` or
    `Monastery Swiftspear (4) deals ...`. These ids are assigned per game/session
    and carry no strategic meaning, so two structurally-identical states differ
    only by them. We collapse ` (<digits>)` -> ` (#)`. The pattern requires a
    leading space and pure digits, so it never touches semantic parentheticals
    like `hand(8)`, `(1/2)` (P/T), `(need 1 more U)`, or `(0 untapped sources)`.
    """
    return _OBJ_ID.sub(" (#)", prompt)


def game_key(source_tag, seed):
    """Split unit: BOTH swap variants of a seed travel together (they share deck
    shuffles), so the split is leakage-free. Namespaced by source so seed-number
    reuse across corpora cannot collide."""
    return "%s#%s" % (source_tag, seed)


def assign_splits(keys, val_frac, split_seed):
    """Deterministically send ~val_frac of GAME keys to 'valid'. Ordering is a
    stable hash of (split_seed, key), so the same inputs always split the same
    way and the choice is independent of scan order."""
    ordered = sorted(keys, key=lambda k: hashlib.sha256(
        ("%d:%s" % (split_seed, k)).encode("utf-8")).hexdigest())
    n_val = round(val_frac * len(ordered))
    val = set(ordered[:n_val])
    return {k: ("valid" if k in val else "train") for k in ordered}


# --------------------------------------------------------------- build set

def build_examples(sources, preamble):
    """Scan sources -> list of example dicts (pre-dedup, pre-split), plus
    warnings and simple hygiene counters."""
    examples, warnings = [], []
    hygiene = {"no_reply": 0, "fallback": 0, "truncated": 0}
    for dir_path, build in sources:
        if build not in BUILDS:
            raise SystemExit("unknown build %r for %s (want %s)"
                             % (build, dir_path, "/".join(BUILDS)))
        p = (REPO_ROOT / dir_path) if not Path(dir_path).is_absolute() else Path(dir_path)
        source_tag = str(dir_path)
        games, w = load_complete_games(p)
        warnings.extend("%s: %s" % (source_tag, x) for x in w)
        for entry in games:
            g = entry["game"]
            for ident in winning_llm_identities(g):
                letter = ident.upper()
                seat_name = g.get("deck_%s" % ident)
                plan = g.get("plan_%s" % ident) or ""
                for di, d in enumerate(entry["decisions"]):
                    if d.get("seat") != seat_name:
                        continue
                    tier = d.get("tier")
                    if tier not in MODEL_TIERS:
                        continue
                    reply = d.get("raw_reply")
                    # hygiene: only keep rows the model actually answered cleanly
                    if reply is None or not reply.strip():
                        hygiene["no_reply"] += 1
                        continue
                    if d.get("fallback") is not None:
                        hygiene["fallback"] += 1
                        continue
                    if d.get("finish_reason") == "length":
                        hygiene["truncated"] += 1
                        continue
                    prompt = d.get("prompt") or ""
                    system = preamble.system(seat_name, plan, tier, build)
                    model = g.get("%s_%s" % (tier, ident))  # smart_a / fast_a ...
                    examples.append({
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": reply},
                        ],
                        "meta": {
                            "source_dir": source_tag,
                            "source_file": entry["file"],
                            "seed": g.get("seed"),
                            "swap": bool(g.get("swap", False)),
                            "winner": g.get("winner"),
                            "llm_identity": letter,
                            "seat": seat_name,
                            "model": model,
                            "build": build,
                            "build_commit": BUILDS[build]["commit"],
                            "engine_sha": g.get("engine_sha"),
                            "overlay_sha": g.get("overlay_sha"),
                            "deck_sha": g.get("deck_sha"),
                            "tier": tier,
                            "decision_index": di,
                            "type": d.get("type"),
                            "choice": d.get("choice"),
                        },
                        # dedup key material (not emitted)
                        "_key": (tier, seat_name, normalize_state(prompt), reply),
                        "_gkey": game_key(source_tag, g.get("seed")),
                    })
    return examples, warnings, hygiene


def dedupe(examples):
    """Drop later examples whose (tier, seat, normalized-state, reply) already
    appeared. Stable: keeps the first occurrence in scan order."""
    seen, kept, dropped = set(), [], 0
    for ex in examples:
        if ex["_key"] in seen:
            dropped += 1
            continue
        seen.add(ex["_key"])
        kept.append(ex)
    return kept, dropped


# ------------------------------------------------------------------- output

def write_outputs(out_dir, examples, splits, argv, sources, hygiene,
                  dedup_dropped, warnings):
    out_dir.mkdir(parents=True, exist_ok=True)
    # Deterministic emission order: by source, seed, swap, then decision index.
    examples.sort(key=lambda e: (e["meta"]["source_dir"], e["meta"]["seed"],
                                 e["meta"]["swap"], e["meta"]["decision_index"]))
    for ex in examples:
        ex["_split"] = splits[ex["_gkey"]]

    train = [e for e in examples if e["_split"] == "train"]
    valid = [e for e in examples if e["_split"] == "valid"]

    def dump(rows, name):
        with open(out_dir / name, "w", encoding="utf-8") as f:
            for e in rows:
                f.write(json.dumps({"messages": e["messages"]},
                                   ensure_ascii=False) + "\n")

    dump(train, "train.jsonl")
    dump(valid, "valid.jsonl")

    # meta.jsonl: one line per example, ordered as [train block][valid block] so
    # each block is aligned BY LINE to the matching data file. `split` and
    # `line` (1-based within the block) make the alignment explicit.
    with open(out_dir / "meta.jsonl", "w", encoding="utf-8") as f:
        for block, rows in (("train", train), ("valid", valid)):
            for i, e in enumerate(rows, 1):
                m = dict(e["meta"]); m["split"] = block; m["line"] = i
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    counts = summarize(train, valid)
    write_readme(out_dir, counts, argv, sources, hygiene, dedup_dropped, warnings)
    return train, valid, counts


def summarize(train, valid):
    def tier_counts(rows):
        c = {"smart": 0, "fast": 0}
        for e in rows:
            c[e["meta"]["tier"]] = c.get(e["meta"]["tier"], 0) + 1
        return c

    def games(rows):
        return len({(e["meta"]["source_dir"], e["meta"]["seed"],
                     e["meta"]["swap"]) for e in rows})

    return {
        "train": len(train), "valid": len(valid),
        "train_by_tier": tier_counts(train), "valid_by_tier": tier_counts(valid),
        "train_games": games(train), "valid_games": games(valid),
        "smart_total": tier_counts(train)["smart"] + tier_counts(valid)["smart"],
        "fast_total": tier_counts(train)["fast"] + tier_counts(valid)["fast"],
    }


def write_readme(out_dir, counts, argv, sources, hygiene, dedup_dropped, warnings):
    src_lines = "\n".join("- `%s` (build %s)" % (d, b) for d, b in sources)
    lines = [
        "# stage1-v0 — winning-trajectory SFT data",
        "",
        "MLX chat-format SFT set built by `eval/make_sft_data.py` from winning",
        "MTG Forge harness trajectories. One example per model-answered decision",
        "from a seat the LLM piloted **and won**.",
        "",
        "## Exact command",
        "",
        "```",
        "python3 " + " ".join(argv),
        "```",
        "",
        "## Sources",
        "",
        src_lines,
        "",
        "## Counts",
        "",
        "| split | examples | smart | fast | games |",
        "|-------|---------:|------:|-----:|------:|",
        "| train | %d | %d | %d | %d |" % (
            counts["train"], counts["train_by_tier"]["smart"],
            counts["train_by_tier"]["fast"], counts["train_games"]),
        "| valid | %d | %d | %d | %d |" % (
            counts["valid"], counts["valid_by_tier"]["smart"],
            counts["valid_by_tier"]["fast"], counts["valid_games"]),
        "| **total** | **%d** | **%d** | **%d** | **%d** |" % (
            counts["train"] + counts["valid"], counts["smart_total"],
            counts["fast_total"], counts["train_games"] + counts["valid_games"]),
        "",
        "Smart-tier rows are the primary set. Fast-tier rows use the terse FORMAT",
        "line (no STATIC_REFERENCE) and are tagged `tier=\"fast\"` in `meta.jsonl`",
        "so a downstream mix weight can be tuned.",
        "",
        "## Files",
        "",
        "- `train.jsonl` / `valid.jsonl` — MLX chat format:",
        "  `{\"messages\":[{\"role\":\"system\",...},{\"role\":\"user\",...},"
        "{\"role\":\"assistant\",...}]}`.",
        "- `meta.jsonl` — per-example provenance aligned by line. Ordered as the",
        "  train block followed by the valid block; each carries `split` and a",
        "  1-based `line` within its block, so `meta` rows with `split=\"train\"`",
        "  align line-for-line to `train.jsonl` (likewise valid). Fields:",
        "  source_dir, source_file, seed, swap, winner, llm_identity, seat,",
        "  model, build, build_commit, engine_sha, overlay_sha, deck_sha, tier,",
        "  decision_index, type, choice.",
        "",
        "## Construction contract",
        "",
        "- **system** = the game's exact teacher preamble, reconstructed",
        "  programmatically from `framing()` / `STATIC_REFERENCE` in",
        "  `LlmSpikeGui.java` (byte-exact, self-checked against known char",
        "  counts). STAGE0 smart rows get the STATIC_REFERENCE appendix; FIX",
        "  smart rows and all fast rows do not.",
        "- **user** = the decision row `prompt` (verbatim state snapshot).",
        "- **assistant** = the decision row `raw_reply` (verbatim winning reply).",
        "  Replies are never rewritten.",
        "",
        "## Hygiene",
        "",
        "- Only COMPLETE games (a `t:game` summary line present) are read;",
        "  in-flight files appended by live batches are skipped.",
        "- Reruns of a (seed, swap) are deduped keeping the newest file.",
        "- Rows dropped for quality: %d no-reply, %d fallback, %d truncated."
        % (hygiene["no_reply"], hygiene["fallback"], hygiene["truncated"]),
        "- Near-duplicate `(tier, seat, normalized-state, reply)` examples",
        "  deduped: **%d dropped**. State is normalized for the dedup KEY only"
        % dedup_dropped,
        "  by collapsing space-preceded Forge object ids ` (<digits>)` -> ` (#)`;",
        "  emitted prompts stay verbatim.",
        "- Train/valid split is BY GAME (both swap variants of a seed together,",
        "  leakage-free), deterministic via a stable hash of the game key.",
    ]
    if warnings:
        lines += ["", "## Warnings", ""] + ["- " + w for w in warnings]
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------- driver

def parse_source_arg(s):
    if ":" not in s:
        raise argparse.ArgumentTypeError(
            "source must be DIR:BUILD (BUILD in %s), got %r"
            % ("/".join(BUILDS), s))
    d, b = s.rsplit(":", 1)
    return (d, b)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", type=parse_source_arg,
                    help="DIR:BUILD pairs (BUILD in %s). Default: the two usable "
                         "sources." % "/".join(BUILDS))
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "datasets" / "stage1-v0"),
                    help="output directory (default datasets/stage1-v0)")
    ap.add_argument("--java", default=str(DEFAULT_JAVA),
                    help="path to LlmSpikeGui.java for preamble reconstruction")
    ap.add_argument("--val-frac", type=float, default=0.1,
                    help="fraction of GAMES to hold out as valid (default 0.1)")
    ap.add_argument("--split-seed", type=int, default=20260722,
                    help="seed for the deterministic by-game split")
    args = ap.parse_args(argv)

    sources = args.sources or DEFAULT_SOURCES
    preamble = PreambleBuilder(args.java)

    examples, warnings, hygiene = build_examples(sources, preamble)
    examples, dedup_dropped = dedupe(examples)

    gkeys = {e["_gkey"] for e in examples}
    splits = assign_splits(gkeys, args.val_frac, args.split_seed)

    # Record the command as invoked (for the README), normalizing sources back
    # to DIR:BUILD tokens.
    recorded = ["eval/make_sft_data.py"] + ["%s:%s" % (d, b) for d, b in sources]
    if args.out_dir != str(REPO_ROOT / "datasets" / "stage1-v0"):
        recorded += ["--out-dir", args.out_dir]

    out_dir = Path(args.out_dir)
    train, valid, counts = write_outputs(
        out_dir, examples, splits, recorded, sources, hygiene,
        dedup_dropped, warnings)

    print("wrote %d train + %d valid examples to %s"
          % (len(train), len(valid), out_dir))
    print("  smart: %d train / %d valid   fast: %d train / %d valid"
          % (counts["train_by_tier"]["smart"], counts["valid_by_tier"]["smart"],
             counts["train_by_tier"]["fast"], counts["valid_by_tier"]["fast"]))
    print("  games: %d train / %d valid   dedup dropped: %d"
          % (counts["train_games"], counts["valid_games"], dedup_dropped))
    print("  hygiene drops: %d no-reply, %d fallback, %d truncated"
          % (hygiene["no_reply"], hygiene["fallback"], hygiene["truncated"]))
    if warnings:
        print("  warnings: %d (see README)" % len(warnings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
