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
against structural anchors and a framing hash (the lengths were verified against
the logged prompt_tokens of the cache-cold first smart call in both the deepseek
and gemini corpora).

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
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JAVA = (REPO_ROOT / "forge-overlay" / "forge-gui-desktop" / "src"
                / "main" / "java" / "forge" / "spike" / "LlmSpikeGui.java")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_policy  # noqa: E402  seed governance (EVAL-DESIGN.md §6.5)

# (relative dir, build tag). Build tag selects the preamble shape (see below).
DEFAULT_SOURCES = [
    ("runs/stage0-calibration", "STAGE0"),
    ("runs/ab-quality-1/fix", "FIX"),
]

# build tag -> does the SMART tier get the STATIC_REFERENCE appendix?  + commit
# for provenance. fast tier never gets STATIC_REFERENCE in any build.
# static_reference_len: the length STATIC_REFERENCE had WHEN THAT BUILD'S RUNS
# WERE GENERATED. Only builds with static_reference_on_smart can be
# mis-reconstructed by a drifted working tree, so only they pin it; system()
# refuses rather than appending text those runs never saw.
BUILDS = {
    "STAGE0": {"static_reference_on_smart": True, "commit": "339f457",
               "static_reference_len": 7492},
    "FIX":    {"static_reference_on_smart": False, "commit": "c3ec3d2"},
}

MODEL_TIERS = ("smart", "fast")  # only model-authored tiers become examples

# Pin on the framing EVERY build reconstructs: the seat and plan lines, base
# body, tail and both FORMAT branches (sha256 prefix of their concatenation).
# Terse sources (fast tier, ai-teacher, dagger) are only exact while these bytes
# match what their games saw. STATIC_REFERENCE is deliberately NOT pinned here:
# only builds with static_reference_on_smart append it, and system() checks it
# per build against BUILDS[...]["static_reference_len"]; a global pin on it
# would refuse terse sources too whenever that appendix grows. Pinned at
# 0bfdf91bdd367c92, identical at 339f457 (STAGE0), eef8c9d, 38217eb and
# 0b90848. A stale pin is a pin that can't fail: re-pin deliberately, never
# delete.
EXPECT_FRAMING_SHA256 = "0bfdf91bdd367c92"


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
    plan). Self-checks the pieces against structural anchors + a framing hash so
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
        framing = hashlib.sha256((self.seat_prefix + self.seat_suffix + self.plan_prefix
                                  + self.plan_suffix + self.body + self.tail
                                  + self.format_terse + self.format_verbose).encode("utf-8"))
        # Not an assert: `python3 -O` would skip it and write preambles no game saw.
        if framing.hexdigest()[:16] != EXPECT_FRAMING_SHA256:
            raise SystemExit("framing() drifted: sha256 %s, expected %s"
                             % (framing.hexdigest()[:16], EXPECT_FRAMING_SHA256))

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
            want = BUILDS[build].get("static_reference_len")
            if want is None:
                raise SystemExit(
                    "build %s sets static_reference_on_smart but no "
                    "static_reference_len; add the length STATIC_REFERENCE had "
                    "when %s runs were generated so drift can be detected."
                    % (build, build))
            if len(self.static_reference) != want:
                raise SystemExit(
                    "refusing to reconstruct %s smart-tier system messages: "
                    "the working-tree STATIC_REFERENCE is %d chars, but %s runs "
                    "saw %d. Appending it anyway would emit a preamble those "
                    "games never used (silent training-data corruption). "
                    "Reconstruct from a checkout at commit %s, or restrict this "
                    "run to terse sources (fast tier / ai-teacher / dagger), "
                    "which never carry STATIC_REFERENCE."
                    % (build, len(self.static_reference), build, want,
                       BUILDS[build]["commit"]))
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


# ===========================================================================
# STAGE-2 (stage2-local-v0) MULTI-SOURCE BUILDER
# ===========================================================================
#
# Everything below is additive: the Stage-1 winning-trajectory path above is
# untouched. The Stage-2 builder mixes three source kinds under CLI-declared
# weights + a stratified global cap:
#
#   ai-teacher  — ai-log SELF-PLAY corpora (a runs dir TREE). Win-filter keeps
#                 only decisions from the seat that WON that game. These rows
#                 carry tier="ai-teacher" and NO model reply, so we synthesize
#                 the assistant target as the CANONICAL TERSE answer the harness
#                 would parse for that decision (see canonical_target()).
#   prebuilt    — an already-built chat-format slice (dir with train/valid.jsonl
#                 or a single .jsonl). Re-emitted VERBATIM with provenance,
#                 keeping its original split (used for the deepseek smart-tier
#                 CoT slice and the 61 puzzle-CoT rows x2).
#   win         — the Stage-1 winning-trajectory path (DIR:BUILD), reused as-is.
#
# ------------------------------------------------ canonical ai-teacher targets
#
# ai-log rows record only the DECISION, not any prose. We emit exactly the final
# line a perfectly-behaved model would write for that decision — derived from
# how LlmSpikeGui.java PARSES replies (the "ANSWER CONTRACT" in the preamble and
# the decideOne/decideMany/play/mulligan/blockers routers):
#
#   * single numbered pick (play, zone-change, targets, entity, confirm, number,
#     card-name, ability-to-play): choice is a 0-BASED index into the logged
#     `options`; the harness parses `CHOICE: <1-based number>`  ->  emit
#     "CHOICE: <choice+1>".
#   * PASS (play, choice == -1): the PASS option is the LAST entry of the logged
#     `options` (menu number == n_options; evalDec logged n_options == passIdx).
#     Worked example C in the preamble: "CHOICE: 4 (the PASS / decline option)".
#     ->  emit "CHOICE: <n_options>".
#   * explicit decline on a decideOne site (choice == -1, non-play): the
#     "Decline / none" label is NOT in the logged `options`, so its menu number
#     is n_options+1 (verified against real zone-change rows).  ->  emit
#     "CHOICE: <n_options+1>".
#   * subset / count sites (attackers, discard, modes, sacrifice, reveal,
#     cards-for-effect, order, mulligan-bottom, damage-assign): evalDec logs
#     choice = the SUBSET SIZE k (never which elements). The harness parses
#     `PICKS: <comma-separated numbers>`.  k==0 -> "PICKS: NONE"; else emit the
#     cardinality-faithful canonical "PICKS: 1,2,...,k". This is EXACT when
#     k==0 (none) or k==n_options (all); for 0<k<n_options the SPECIFIC subset
#     is not recoverable from the log, so first-k is a valid, correctly-sized
#     REPRESENTATIVE (see --count-style to drop these instead).
#   * blockers: choice = number of blocker->attacker assignments, but the
#     PAIRING is not logged. k==0 -> "BLOCKS: NONE" (exact); k>0 is
#     underdetermined and always DROPPED.
#   * mulligan: options are ["KEEP","MULLIGAN"], reply is one word.  ->  emit
#     options[choice] (KEEP/MULLIGAN).
#
# Unknown decision types are dropped (never guessed) so no wrong-format target
# is ever emitted.

# subset/count sites whose reply is `PICKS: ...` (choice == subset size)
COUNT_PICKS_TYPES = frozenset((
    "attackers", "discard", "modes", "sacrifice", "reveal",
    "cards-for-effect", "order", "mulligan-bottom", "damage-assign"))
# NOTE: reveal / cards-for-effect / order / damage-assign never appear in
# ai-teacher corpora today (the ai-log seat logs combat damage as
# "damage-order", which is deliberately unhandled -> dropped as unknown-type).
# Kept for forward-compat with the model seat's row types; an emitted-but-
# unhandled type always lands in hygiene as drop_unknown_type, never a guess.
# single numbered pick sites whose reply is `CHOICE: <n>` (choice == 0-based idx)
SINGLE_CHOICE_TYPES = frozenset((
    "play", "zone-change", "targets", "target", "entity",
    "ability-to-play", "confirm", "number", "card-name"))
# blockers: reply is `BLOCKS: ...` (choice == # assignments; pairing not logged)
BLOCKS_TYPES = frozenset(("blockers",))


def canonical_target(d):
    """Canonical terse assistant target for an ai-teacher decision row.

    Returns (target_str, kind). target_str is None when the row cannot be
    faithfully reconstructed (kind then names the reason). kind is one of:
    choice / pass / decline / count-none / count-all / count-first-k /
    blocks-none / mulligan-word  (emitted), or a drop reason (dropped)."""
    typ = d.get("type")
    ch = d.get("choice")
    opts = d.get("options") if isinstance(d.get("options"), list) else []
    n = d.get("n_options")
    if not isinstance(n, int) or isinstance(n, bool):
        n = len(opts)
    if not isinstance(ch, int) or isinstance(ch, bool):
        return None, "no-choice"

    if typ == "mulligan":
        if 0 <= ch < len(opts) and isinstance(opts[ch], str):
            return opts[ch], "mulligan-word"
        if ch in (0, 1):                      # 0==KEEP, 1==MULLIGAN by convention
            return ("KEEP" if ch == 0 else "MULLIGAN"), "mulligan-word"
        return None, "mulligan-oob"

    if typ in COUNT_PICKS_TYPES:
        if ch == 0:
            return "PICKS: NONE", "count-none"
        if 1 <= ch <= n:
            picks = "PICKS: " + ",".join(str(i) for i in range(1, ch + 1))
            return picks, ("count-all" if ch == n else "count-first-k")
        return None, "count-oob"

    if typ in BLOCKS_TYPES:
        if ch == 0:
            return "BLOCKS: NONE", "blocks-none"
        return None, "blocks-underdetermined"

    if typ in SINGLE_CHOICE_TYPES:
        if ch == -1:
            # play: PASS is the last logged option (menu # == n_options).
            # other decideOne sites: the decline label sits at n_options+1.
            # A malformed/engine-fallback row (e.g. n_options == -1, as ai-log
            # stamps for "ai-choice-not-in-menu") would produce a non-positive
            # CHOICE number the harness (regex \d+) cannot parse, silently
            # picking option 0. Guard it and drop rather than emit a bad target.
            if typ == "play":
                return ("CHOICE: %d" % n, "pass") if n >= 1 else (None, "pass-oob")
            return (("CHOICE: %d" % (n + 1), "decline") if n >= 0
                    else (None, "decline-oob"))
        if ch >= 0:
            return "CHOICE: %d" % (ch + 1), "choice"
        return None, "choice-oob"

    return None, "unknown-type"


def winning_ai_seat(game):
    """(seat name, identity letter) of the ai-log identity that WON, or
    (None, reason) for a draw / missing / non-ai-log winner. winner is
    IDENTITY-keyed (never flipped by swap), so the winning seat is
    deck_<winning identity> regardless of seat swap."""
    winner = game.get("winner")
    if winner not in ("A", "B"):
        return None
    ident = "a" if winner == "A" else "b"
    if game.get("controller_%s" % ident) not in ("ai-log", "ai", "llm"):
        return None
    return game.get("deck_%s" % ident), ident


def iter_game_dirs(root):
    """Directories under `root` (inclusive) that contain game-*.jsonl files.

    ai-teacher corpora are dir TREES (runs/d1-selfplay/<matchup>/game-*.jsonl);
    d0-validate keeps files at the top level. We group by containing dir so the
    (seed, swap) rerun-dedup in load_complete_games stays per-matchup."""
    root = Path(root)
    dirs = set()
    if root.is_dir():
        if any(root.glob("game-*.jsonl")):
            dirs.add(root)
        for fp in root.rglob("game-*.jsonl"):
            dirs.add(fp.parent)
    return sorted(dirs, key=str)


def _resolve(path):
    return (REPO_ROOT / path) if not Path(path).is_absolute() else Path(path)


# --------------------------------------------------------- seed governance
#
# eval/EVAL-DESIGN.md §6.5 reserves seed blocks (frozen-legacy 501-525,
# puzzle-mining 20000-29999, certification-holdout 30000-30099) that must
# NEVER feed SFT/training data — see eval/seed_policy.py for the shared
# guard. This scan is deliberately a lightweight PRE-PASS over game(s)
# seeds only, run once per source before any builder does real work, so a
# violation refuses before ANY output is written and the real
# build_*_examples() functions stay untouched (existing call sites/tests
# unaffected). ai-teacher and dagger are self-play corpus TREES (scan via
# iter_game_dirs, matching build_ai_teacher_examples/build_dagger_examples);
# win is a single directory (matching build_examples). prebuilt sources are
# EXEMPT — they carry no game-*.jsonl files, only an already-built dataset.

def _scan_source_seeds(spec_type, path):
    """Seeds appearing in COMPLETE games under `path` for a source of this
    type. Returns a plain list (may contain duplicates/None; the caller
    filters)."""
    p = _resolve(path)
    seeds = []
    if spec_type in ("ai-teacher", "dagger"):
        for gd in iter_game_dirs(p):
            games, _ = load_complete_games(gd)
            seeds.extend(e["game"].get("seed") for e in games)
    else:  # "win"
        games, _ = load_complete_games(p)
        seeds.extend(e["game"].get("seed") for e in games)
    return seeds


def enforce_seed_policy(labeled_seeds, allow_reserved_seeds):
    """labeled_seeds: iterable of (source_label, seed) pairs collected across
    every non-exempt source about to be built. Refuses (prints a clear
    multi-line message + sys.exit(2)) if any seed lands in a reserved eval
    block, UNLESS allow_reserved_seeds overrides it — in which case a loud
    banner is printed instead and the build proceeds.

    Returns True iff reserved seeds were present AND allowed through (an
    actual override fired); False iff every seed was clean. Callers stamp
    the return value into the run's README/mix-report as
    reserved_seed_override so a contaminated build is traceable after the
    fact (additive field only)."""
    offenders = {}
    for label, seed in labeled_seeds:
        if not isinstance(seed, int) or isinstance(seed, bool):
            continue
        block = seed_policy.reserved_block(seed)
        if block:
            offenders.setdefault(block, set()).add("%s#%d" % (label, seed))
    if not offenders:
        return False

    bar = "=" * 72
    lines = [bar,
             "  reserved eval seed(s) detected in source game data.",
             "  %s reserves these seed blocks for held-out measurement;"
             % seed_policy.DOC_POINTER,
             "  games played on them must NEVER feed SFT/training data.", ""]
    for block in sorted(offenders):
        lines.append("  [%s] %s" % (block, ", ".join(sorted(offenders[block]))))
    lines.append(bar)

    if not allow_reserved_seeds:
        print("\nREFUSING:\n" + "\n".join(lines), file=sys.stderr)
        print("\npass --allow-reserved-seeds to override (the resulting "
              "dataset becomes CONTAMINATED for held-out measurement and "
              "must be labeled as such).", file=sys.stderr)
        sys.exit(2)

    print("\nOVERRIDDEN (--allow-reserved-seeds):\n" + "\n".join(lines),
          file=sys.stderr)
    print("\nproceeding anyway — this dataset is CONTAMINATED for held-out "
          "measurement purposes; reserved_seed_override=true is stamped "
          "into this run's output.", file=sys.stderr)
    return True


# ---------------------------------------------------- held-out deck guard
#
# The deck-side twin of enforce_seed_policy above. eval/holdouts.json declares
# decks whose games must never reach a training corpus, so an eval on them
# measures generalization rather than recall. Nothing enforced that at BUILD
# time, which is how datasets/stage2-r2-teacher-v0 acquired 1148 painter /
# sneak-and-show rows (2.3%) from runs/r2-breadth-teacher and silently turned
# the suite's generalization arm into an in-distribution one.
#
# Unlike seeds, a held-out deck is a per-ROW property, so this guard can offer
# something the seed guard cannot: drop exactly the offending rows and keep the
# rest of the corpus. That is the intended remedy, but it is NOT the default --
# silently shrinking a corpus is its own kind of surprise. Default is refuse;
# the operator picks --drop-heldout-decks (clean) or --allow-heldout-decks
# (contaminated, stamped).

def load_heldout_deck_slugs(registry_path=None):
    """Slugs of decks declared held-out in eval/holdouts.json.

    Returns (slugs, note). A missing/unreadable registry yields an empty set
    and a note the caller surfaces -- this guard must never be the reason a
    build dies, but it must also never pass quietly when it cannot see."""
    p = (Path(registry_path) if registry_path
         else Path(__file__).resolve().parent / "holdouts.json")
    try:
        reg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        return set(), ("held-out deck registry unreadable (%s: %s) -- this "
                       "build was NOT checked for held-out deck contamination"
                       % (p.name, e))
    slugs = {slug for slug, d in (reg.get("decks") or {}).items()
             if d.get("status") == "held-out"}
    if not slugs:
        return set(), ("held-out deck registry declares no held-out decks -- "
                       "this build's deck check is vacuous")
    return slugs, None


def _row_deck_slug(row):
    """The deck slug for an in-flight example row, or None if it carries none.

    Builders nest identity under `meta` (meta.deck == meta.seat == slug); the
    top level is checked as a fallback."""
    meta = row.get("meta") or {}
    return (meta.get("deck") or meta.get("seat")
            or row.get("deck") or row.get("seat"))


def enforce_heldout_decks(by_source, heldout_slugs, drop, allow):
    """Refuse, drop, or allow rows whose deck is declared held-out.

    by_source: {source_name: [row, ...]} mutated in place when dropping.
    Returns (dropped_count, override_bool). The deck slug lives in each row's
    nested `meta` (every builder writes meta.deck == meta.seat == the slug);
    the top level is checked too so a future row shape that hoists it still
    matches. A row carrying neither cannot be judged and is left alone --
    which is why unjudgeable rows are counted and reported below rather than
    passing silently."""
    if not heldout_slugs:
        return 0, False
    offenders = {}
    unjudgeable = 0
    for name, rows in by_source.items():
        for r in rows:
            slug = _row_deck_slug(r)
            if slug is None:
                unjudgeable += 1
            elif slug in heldout_slugs:
                offenders.setdefault(slug, {}).setdefault(name, 0)
                offenders[slug][name] += 1
    if unjudgeable:
        print("[heldout-decks] NOTE: %d row(s) carry no deck field and could "
              "not be checked against eval/holdouts.json" % unjudgeable,
              file=sys.stderr)
    if not offenders:
        return 0, False

    total = sum(n for per in offenders.values() for n in per.values())
    bar = "=" * 72
    lines = [bar,
             "  held-out deck row(s) detected in source game data.",
             "  eval/holdouts.json declares these decks held-out; games on",
             "  them must NEVER feed a training corpus, or every eval that",
             "  uses them measures recall instead of generalization.", ""]
    for slug in sorted(offenders):
        per = ", ".join("%s=%d" % (k, v) for k, v in sorted(offenders[slug].items()))
        lines.append("  [%s] %d row(s): %s"
                     % (slug, sum(offenders[slug].values()), per))
    lines.append(bar)

    if drop:
        for name, rows in list(by_source.items()):
            by_source[name] = [r for r in rows
                               if _row_deck_slug(r) not in heldout_slugs]
        print("\nDROPPED (--drop-heldout-decks):\n" + "\n".join(lines),
              file=sys.stderr)
        print("\n%d row(s) removed; the rest of the corpus is unaffected and "
              "stays clean for held-out measurement." % total, file=sys.stderr)
        return total, False

    if allow:
        print("\nOVERRIDDEN (--allow-heldout-decks):\n" + "\n".join(lines),
              file=sys.stderr)
        print("\nproceeding anyway — this dataset CONTAMINATES the held-out "
              "decks above; heldout_deck_override=true is stamped into this "
              "run's output, and eval/check_holdouts.py will fail until "
              "eval/holdouts.json grandfathers this dataset.", file=sys.stderr)
        return 0, True

    print("\nREFUSING:\n" + "\n".join(lines), file=sys.stderr)
    print("\npass --drop-heldout-decks to exclude just those rows (recommended), "
          "or --allow-heldout-decks to keep them (the resulting dataset "
          "CONTAMINATES those decks and must be labeled as such).",
          file=sys.stderr)
    sys.exit(2)


# Decision families the Forge AI emits in bulk and pilots competently (its
# aggro/combat strength). Everything else is "rare" for ai_filter purposes:
# menu-format coverage of the long tail is worth keeping even from a LOSING
# seat, while the bulk families stay winner-only (v1-mixed lesson: weak-teacher
# volume on the dominant types measurably hurts).
AI_COMMON_TYPES = frozenset(("play", "attackers", "mulligan", "confirm"))


def build_ai_teacher_examples(dir_path, preamble, count_style="first-k",
                              ai_filter="win"):
    """Scan an ai-log self-play corpus TREE -> Stage-2 example dicts.

    ai_filter: "win" keeps only the winning seat's decisions; "all" keeps both
    seats; "win+rare" keeps the winning seat in full plus the LOSING seat's
    rows for rare decision types only (not in AI_COMMON_TYPES) — coverage of
    the long-tail menus without importing the AI's losing-line play/combat
    volume. Assistant target is the canonical terse answer. System message is
    the TERSE framing for that seat+plan (terse never carries STATIC_REFERENCE
    in any build) so the system's requested format agrees with the choice-only
    answer we emit."""
    examples, warnings = [], []
    hyg = Counter()
    root = _resolve(dir_path)
    source_tag = str(dir_path)
    for gd in iter_game_dirs(root):
        games, w = load_complete_games(gd)
        rel = str(gd.relative_to(root)) if gd != root else "."
        warnings.extend("%s[%s]: %s" % (source_tag, rel, x) for x in w)
        for entry in games:
            g = entry["game"]
            won = winning_ai_seat(g)
            if won is None:
                hyg["skip_game_draw_or_invalid"] += 1
                continue
            seat, ident = won
            # seat name -> identity letter for BOTH seats (loser rows are
            # eligible under ai_filter "all"/"win+rare"). Seat names equal the
            # deck_<ident> display names; a mirror matchup (deck_a == deck_b)
            # collapses the map, so loser rows are skipped there (win-only
            # behavior — same limitation as the winner attribution itself).
            ident_by_seat = {}
            for il in ("b", "a"):
                sname = g.get("deck_%s" % il)
                if sname:
                    ident_by_seat[sname] = il
            system_by_seat = {}
            gkey = "ai-teacher#%s#%s#%s" % (source_tag, rel, g.get("seed"))
            for di, d in enumerate(entry["decisions"]):
                rseat = d.get("seat")
                seat_won = rseat == seat
                if not seat_won:
                    if ai_filter == "win":
                        continue
                    if (ai_filter == "win+rare"
                            and d.get("type") in AI_COMMON_TYPES):
                        hyg["skip_loser_common_type"] += 1
                        continue
                rid = ident_by_seat.get(rseat)
                if rid is None:
                    if rseat is not None and d.get("tier") == "ai-teacher":
                        hyg["skip_unmapped_seat"] += 1
                    continue
                if not seat_won and rid == ident:
                    # mirror-collapsed map pointed the loser seat at the
                    # winner's identity; attribution is unsafe -> skip.
                    hyg["skip_unmapped_seat"] += 1
                    continue
                if d.get("tier") != "ai-teacher":
                    hyg["skip_nonteacher_tier"] += 1
                    continue
                prompt = d.get("prompt") or ""
                if not prompt.strip():
                    hyg["drop_no_prompt"] += 1
                    continue
                # Engine-fallback rows are NOT genuine teacher choices: the
                # ai-log stamps fallback="ai-choice-not-in-menu" (with
                # n_options=-1, choice=-1) when the AI's pick wasn't in the menu
                # and the engine substituted its own. Mirror Stage-1's fallback
                # drop so we never mint a target from a degenerate fallback.
                if d.get("fallback") is not None:
                    hyg["drop_fallback"] += 1
                    continue
                target, kind = canonical_target(d)
                if target is None:
                    hyg["drop_%s" % kind] += 1
                    continue
                if count_style == "exact-only" and kind == "count-first-k":
                    hyg["drop_exact_only_partial"] += 1
                    continue
                hyg["emit_%s" % kind] += 1
                if not seat_won:
                    hyg["emit_from_losing_seat"] += 1
                dtype = d.get("type")
                if rseat not in system_by_seat:
                    rplan = g.get("plan_%s" % rid) or ""
                    system_by_seat[rseat] = preamble.base(
                        rseat, rplan, terse=True)
                examples.append({
                    "messages": [
                        {"role": "system", "content": system_by_seat[rseat]},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": target},
                    ],
                    "meta": {
                        "source": "ai-teacher",
                        "source_dir": source_tag,
                        "source_subdir": rel,
                        "source_file": entry["file"],
                        "seed": g.get("seed"),
                        "swap": bool(g.get("swap", False)),
                        "winner": g.get("winner"),
                        "seat_won": seat_won,
                        "llm_identity": rid.upper(),
                        "seat": rseat,
                        "deck": rseat,
                        "model": g.get("smart_%s" % rid),
                        "teacher_controller": g.get("controller_%s" % rid),
                        "engine_sha": g.get("engine_sha"),
                        "overlay_sha": g.get("overlay_sha"),
                        "deck_sha": g.get("deck_sha"),
                        "tier": "ai-teacher",
                        "decision_index": di,
                        "type": dtype,
                        "choice": d.get("choice"),
                        "n_options": d.get("n_options"),
                        "target_synth": kind,
                    },
                    "_split": None,
                    "_gkey": gkey,
                    "_cell": (rseat, dtype),
                    "_rid": "%s|%s|%s|%d" % (source_tag, rel, entry["file"], di),
                    "_source": "ai-teacher",
                    # dedup key material (not emitted): identical
                    # (seat, normalized-state, target) rows from DIFFERENT games
                    # would otherwise both land in the by-game split and can leak
                    # a byte-identical exemplar into both train and valid.
                    "_key": (seat, normalize_state(prompt), target),
                })
    return examples, warnings, hyg


def build_dagger_examples(dir_path, preamble, count_style="first-k"):
    """Scan a student self-play corpus TREE -> Stage-2 DAgger example dicts.

    A clone of build_ai_teacher_examples with three differences that make this
    the DAgger (on-trajectory expert-labeling) source:

      * NO win-filter. DAgger targets the compounding-error failure mode, so we
        want the AI's label on EVERY state the student actually reached — both
        seats, winning and losing games alike. (This is why DAgger yields ~2x the
        ai-teacher per-game volume: no winning_ai_seat gate.)
      * Row selection by `shadow_choice`, not tier. Keep a row iff it carries a
        non-null `shadow_choice` (the Forge-AI's DAgger label, written by the
        student seat's four shadow-covered overrides). Only genuine (n_options>1)
        student decisions on the four shadow families ever carry it.
      * Target synthesized from the SHADOW label: canonical_target({**d,
        "choice": shadow_choice}) reuses every per-type encoding unchanged, so
        the assistant answer is what the AI would have done in the student's
        state — while the user message stays the student's real prompt.

    Same blockers caveat as ai-teacher: canonical_target drops blockers with k>0
    (pairing not logged); a dagger blocker row yields an SFT target only when the
    AI blocks none (k=0). The k>0 shadow_choice still lives on the jsonl row for
    the process_metrics divergence report.

    RECOMMENDED: build dagger sources with count_style="exact-only". The count
    encoding records only HOW MANY attackers/blockers the expert chose, not WHICH
    ones, so a partial choice (0 < k < n) renders as "PICKS: 1..k" — the FIRST k
    menu entries, which is almost never the set the AI actually picked. Those rows
    are kind="count-first-k" and teach a wrong answer confidently. "exact-only"
    drops them and keeps the two unambiguous cases (count-none / count-all, i.e.
    "attack with nothing" / "attack with everything"). The default stays
    "first-k" for continuity with the ai-teacher corpus that trained
    stage2-local-v0, so this is a deliberate per-build choice, not a silent one."""
    examples, warnings = [], []
    hyg = Counter()
    root = _resolve(dir_path)
    source_tag = str(dir_path)
    for gd in iter_game_dirs(root):
        games, w = load_complete_games(gd)
        rel = str(gd.relative_to(root)) if gd != root else "."
        warnings.extend("%s[%s]: %s" % (source_tag, rel, x) for x in w)
        for entry in games:
            g = entry["game"]
            # No win-filter: keep every game (incl. draws) and BOTH seats.
            ident_by_seat = {}
            for il in ("b", "a"):
                sname = g.get("deck_%s" % il)
                if sname:
                    ident_by_seat[sname] = il
            system_by_seat = {}
            gkey = "dagger#%s#%s#%s" % (source_tag, rel, g.get("seed"))
            for di, d in enumerate(entry["decisions"]):
                sc = d.get("shadow_choice")
                if sc is None:
                    continue  # not a shadow-labeled row (the sole DAgger selector)
                if not isinstance(sc, int) or isinstance(sc, bool):
                    hyg["skip_bad_shadow_choice"] += 1
                    continue
                rseat = d.get("seat")
                rid = ident_by_seat.get(rseat)
                if rid is None:
                    hyg["skip_unmapped_seat"] += 1
                    continue
                prompt = d.get("prompt") or ""
                if not prompt.strip():
                    # Deterministic (floor/auto/budget) student rows carry no
                    # prompt -> no SFT user message; drop. The usable DAgger rows
                    # are the student's genuine model-consulted decisions.
                    hyg["drop_no_prompt"] += 1
                    continue
                # Target from the AI's shadow label over the SAME menu.
                target, kind = canonical_target({**d, "choice": sc})
                if target is None:
                    hyg["drop_%s" % kind] += 1
                    continue
                if count_style == "exact-only" and kind == "count-first-k":
                    hyg["drop_exact_only_partial"] += 1
                    continue
                hyg["emit_%s" % kind] += 1
                dtype = d.get("type")
                if rseat not in system_by_seat:
                    rplan = g.get("plan_%s" % rid) or ""
                    system_by_seat[rseat] = preamble.base(
                        rseat, rplan, terse=True)
                examples.append({
                    "messages": [
                        {"role": "system", "content": system_by_seat[rseat]},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": target},
                    ],
                    "meta": {
                        "source": "dagger",
                        "source_dir": source_tag,
                        "source_subdir": rel,
                        "source_file": entry["file"],
                        "seed": g.get("seed"),
                        "swap": bool(g.get("swap", False)),
                        "winner": g.get("winner"),
                        "llm_identity": rid.upper(),
                        "seat": rseat,
                        "deck": rseat,
                        "model": g.get("smart_%s" % rid),
                        "student_controller": g.get("controller_%s" % rid),
                        "engine_sha": g.get("engine_sha"),
                        "overlay_sha": g.get("overlay_sha"),
                        "deck_sha": g.get("deck_sha"),
                        "tier": "ai-shadow",
                        "decision_index": di,
                        "type": dtype,
                        "choice": sc,               # the SHADOW label (what we teach)
                        "student_choice": d.get("choice"),  # what the student did
                        "n_options": d.get("n_options"),
                        "target_synth": kind,
                    },
                    "_split": None,
                    "_gkey": gkey,
                    "_cell": (rseat, dtype),
                    "_rid": "%s|%s|%s|%d" % (source_tag, rel, entry["file"], di),
                    "_source": "dagger",
                    # dedup key: (type, seat, normalized-state, shadow-target) so a
                    # state the student revisits identically collapses to one row.
                    "_key": (dtype, rseat, normalize_state(prompt), target),
                })
    return examples, warnings, hyg


def dedupe_keyed(by_source):
    """Drop rows whose `_key` already appeared, across every source that carries
    one (ai-teacher + win). Prebuilt rows have no `_key` and are NEVER deduped
    (curated slices, e.g. the 61 puzzle rows x2, may repeat rows on purpose).

    This runs BEFORE the stratified cap and the by-game split, so byte-identical
    (seat, normalized-state, target) exemplars cannot (a) leak into both
    train.jsonl and valid.jsonl nor (b) over-represent the ~identical PASS play
    rows that dominate the corpus. Keeps the first occurrence in
    (source-insertion, build) order; deterministic. Returns (by_source, dropped).
    """
    seen, dropped = set(), 0
    out = {}
    for name, rows in by_source.items():
        kept = []
        for r in rows:
            k = r.get("_key")
            if k is None:
                kept.append(r)
                continue
            if k in seen:
                dropped += 1
                continue
            seen.add(k)
            kept.append(r)
        out[name] = kept
    return out, dropped


def build_prebuilt_examples(path, kind_label=None):
    """Re-emit an already-built chat-format slice VERBATIM with provenance.

    `path` is a dir (train.jsonl [+ valid.jsonl], split preserved) or a single
    .jsonl (all rows -> train). Rows must be MLX chat format
    {"messages":[...]}. Any row whose assistant text contains a <think> block is
    DROPPED (Stage-2 targets carry no <think> anywhere)."""
    examples, warnings = [], []
    hyg = Counter()
    p = _resolve(path)
    if p.is_dir():
        files = [(p / "train.jsonl", "train"), (p / "valid.jsonl", "valid")]
        files = [(f, s) for f, s in files if f.exists()]
        if not files:
            warnings.append("prebuilt %s: no train.jsonl/valid.jsonl" % path)
    elif p.is_file():
        files = [(p, "train")]
    else:
        warnings.append("prebuilt %s: not found" % path)
        files = []
    label = kind_label or (p.stem if p.is_file() else p.name)
    for fp, split in files:
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            warnings.append("prebuilt %s: unreadable (%s)" % (fp, e))
            continue
        for idx, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                hyg["drop_malformed"] += 1
                continue
            msgs = rec.get("messages") if isinstance(rec, dict) else None
            if not isinstance(msgs, list) or not msgs:
                hyg["drop_no_messages"] += 1
                continue
            asst = next((m for m in reversed(msgs)
                         if isinstance(m, dict) and m.get("role") == "assistant"),
                        None)
            if asst is not None and "<think>" in (asst.get("content") or ""):
                hyg["drop_think_block"] += 1
                continue
            hyg["emit"] += 1
            examples.append({
                "messages": msgs,
                "meta": {
                    "source": "prebuilt",
                    "source_dir": str(path),
                    "source_file": fp.name,
                    "prebuilt_kind": label,
                    "orig_index": idx,
                    "tier": "prebuilt",
                    "deck": label,
                    "type": "prebuilt",
                    "split": split,
                },
                "_split": split,
                "_gkey": None,
                "_cell": (label, "prebuilt"),
                "_rid": "prebuilt|%s|%s|%d" % (path, split, idx),
                "_source": "prebuilt",
            })
    return examples, warnings, hyg


def build_win_examples_v2(dir_path, build, preamble):
    """Adapt the Stage-1 winning-trajectory builder output to Stage-2 shape."""
    raw, warnings, hygiene = build_examples([(dir_path, build)], preamble)
    out = []
    for e in raw:
        m = dict(e["meta"])
        m["source"] = "win"
        m["deck"] = m.get("seat")
        out.append({
            "messages": e["messages"],
            "meta": m,
            "_split": None,
            "_gkey": e["_gkey"],
            "_cell": (m.get("seat"), m.get("type")),
            "_rid": "win|%s|%s|%s|%s" % (dir_path, m.get("seed"),
                                         m.get("swap"), m.get("decision_index")),
            "_source": "win",
            "_key": e["_key"],
        })
    return out, warnings, hygiene


# ------------------------------------------------- deterministic sampling / cap

def _sample_hash(seed, rid):
    return hashlib.sha256(("%d:%s" % (seed, rid)).encode("utf-8")).hexdigest()


def allocate_weighted(budget, items):
    """Distribute `budget` integer units across items proportional to weight,
    clamped to each item's availability (water-filling with largest-remainder).

    items: dict key -> (weight, avail). Returns dict key -> allocation. Fully
    deterministic (ties broken by sorted key). Saturated items overflow is
    redistributed to unsaturated ones until budget or availability is exhausted."""
    result = {k: 0 for k in items}
    budget = min(budget, sum(a for _, a in items.values()))
    if budget <= 0:
        return result
    unsat = {k for k, (w, a) in items.items() if a > 0 and w > 0}
    while budget > 0 and unsat:
        tw = sum(items[k][0] for k in unsat)
        quotas = {k: budget * items[k][0] / tw for k in unsat}
        assigned = 0
        for k in unsat:
            give = min(int(quotas[k]), items[k][1] - result[k])
            if give > 0:
                result[k] += give
                assigned += give
        budget -= assigned
        if assigned == 0:
            # hand out the remaining <len(unsat) units by largest fractional part
            order = sorted((k for k in unsat if result[k] < items[k][1]),
                           key=lambda k: (-(quotas[k] - int(quotas[k])), k))
            for k in order:
                if budget <= 0:
                    break
                result[k] += 1
                budget -= 1
        unsat = {k for k in unsat if result[k] < items[k][1]}
    return result


def stratified_sample(rows, target, seed):
    """Downsample ai-teacher rows to `target`, balancing (deck, decision-type)
    cells: each cell gets an equal share (clamped to its size), so play-type
    rows from two decks can't dominate and rare types are kept up to
    availability. Within a cell, deterministic by seeded row-hash."""
    if target >= len(rows):
        return list(rows)
    cells = defaultdict(list)
    for r in rows:
        cells[r["_cell"]].append(r)
    alloc = allocate_weighted(target, {c: (1.0, len(v)) for c, v in cells.items()})
    out = []
    for c in sorted(cells, key=lambda x: (str(x[0]), str(x[1]))):
        rws = sorted(cells[c], key=lambda r: _sample_hash(seed, r["_rid"]))
        out.extend(rws[:alloc[c]])
    return out


def hash_sample(rows, target, seed):
    """Deterministic uniform downsample to `target` by seeded row-hash order."""
    if target >= len(rows):
        return list(rows)
    return sorted(rows, key=lambda r: _sample_hash(seed, r["_rid"]))[:target]


def apply_mix(by_source, weights, per_source_caps, global_cap, sample_seed):
    """Apply per-source caps, then a global stratified cap, to the per-source
    row lists. Returns (kept_by_source, alloc_report).

    Contract (per the D2 brief): curated sources (prebuilt / win) are kept in
    full under the global cap; the AI-TEACHER budget = cap - reserved and is
    STRATIFIED across (deck, type). If the curated rows alone exceed the cap,
    they too are downsampled (weighted). Weights bias the split among sources of
    the same kind. All sampling is deterministic under `sample_seed`."""
    # dagger (D4) shares the ai-teacher reserved-budget family: both are
    # keyed, synthesized-target sources that get stratified across (deck, type)
    # rather than kept-in-full like the curated win/prebuilt sources.
    def _is_synth_bucket(name):
        return name.startswith("ai-teacher") or name.startswith("dagger")

    # 1. per-source hard caps
    capped = {}
    for name, rows in by_source.items():
        cap = per_source_caps.get(name)
        if cap is not None and cap < len(rows):
            if _is_synth_bucket(name):
                capped[name] = stratified_sample(rows, cap, sample_seed)
            else:
                capped[name] = hash_sample(rows, cap, sample_seed)
        else:
            capped[name] = list(rows)

    report = {"per_source_caps": dict(per_source_caps), "global_cap": global_cap,
              "raw": {n: len(r) for n, r in by_source.items()},
              "after_source_cap": {n: len(r) for n, r in capped.items()}}

    if global_cap is None:
        report["final"] = {n: len(r) for n, r in capped.items()}
        return capped, report

    ai = {n: r for n, r in capped.items() if _is_synth_bucket(n)}
    other = {n: r for n, r in capped.items() if not _is_synth_bucket(n)}
    reserved = sum(len(r) for r in other.values())

    final = {}
    if reserved >= global_cap:
        # curated rows already exceed the cap: weighted downsample of curated,
        # zero ai-teacher budget.
        alloc = allocate_weighted(
            global_cap, {n: (weights.get(n, 1.0), len(r)) for n, r in other.items()})
        for n, r in other.items():
            final[n] = hash_sample(r, alloc[n], sample_seed)
        for n in ai:
            final[n] = []
    else:
        for n, r in other.items():
            final[n] = r
        ai_budget = global_cap - reserved
        alloc = allocate_weighted(
            ai_budget, {n: (weights.get(n, 1.0), len(r)) for n, r in ai.items()})
        for n, r in ai.items():
            final[n] = stratified_sample(r, alloc[n], sample_seed)

    report["final"] = {n: len(r) for n, r in final.items()}
    return final, report


# --------------------------------------------------------- Stage-2 output

def stage2_counts(examples):
    """Nested counts: source -> deck -> type -> n, plus per-source totals."""
    tree = defaultdict(lambda: defaultdict(Counter))
    per_source = Counter()
    for e in examples:
        m = e["meta"]
        src = m.get("source", "?")
        tree[src][m.get("deck")][m.get("type")] += 1
        per_source[src] += 1
    return tree, per_source


def write_outputs_v2(out_dir, examples, argv, val_frac, split_seed,
                     mix_report, warnings, hygienes, count_style):
    """Write train/valid/meta/README for the Stage-2 multi-source set.

    Split: game-derived rows (ai-teacher, win) split BY GAME via assign_splits
    (both swaps together, leakage-free). Prebuilt rows keep their original
    split. Deterministic emission order (source, rid)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    game_keys = {e["_gkey"] for e in examples if e["_gkey"] is not None}
    splits = assign_splits(game_keys, val_frac, split_seed)
    for e in examples:
        if e["_split"] is None:
            e["_split"] = splits.get(e["_gkey"], "train")

    examples.sort(key=lambda e: (e["_source"], e["_rid"]))
    train = [e for e in examples if e["_split"] == "train"]
    valid = [e for e in examples if e["_split"] == "valid"]

    def dump(rows, name):
        with open(out_dir / name, "w", encoding="utf-8") as f:
            for e in rows:
                f.write(json.dumps({"messages": e["messages"]},
                                   ensure_ascii=False) + "\n")

    dump(train, "train.jsonl")
    dump(valid, "valid.jsonl")

    with open(out_dir / "meta.jsonl", "w", encoding="utf-8") as f:
        for block, rows in (("train", train), ("valid", valid)):
            for i, e in enumerate(rows, 1):
                m = dict(e["meta"])
                m["split"] = block
                m["line"] = i
                m["split_seed"] = split_seed
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    tree, per_source = stage2_counts(examples)
    write_readme_v2(out_dir, argv, examples, train, valid, tree, per_source,
                    mix_report, warnings, hygienes, count_style, split_seed,
                    val_frac)
    return train, valid, tree, per_source


def _counts_table(tree):
    lines = ["| source | deck | type | n |", "|---|---|---|---:|"]
    for src in sorted(tree):
        for deck in sorted(tree[src], key=lambda x: str(x)):
            for typ in sorted(tree[src][deck], key=lambda x: str(x)):
                lines.append("| %s | %s | %s | %d |"
                             % (src, deck, typ, tree[src][deck][typ]))
    return lines


def write_readme_v2(out_dir, argv, examples, train, valid, tree, per_source,
                    mix_report, warnings, hygienes, count_style, split_seed,
                    val_frac):
    lines = [
        "# stage2-local-v0 — multi-source SFT mix",
        "",
        "Built by `eval/make_sft_data.py` (Stage-2 multi-source mode). Mixes",
        "ai-log self-play teacher rows (win-filtered, canonical terse targets),",
        "prebuilt CoT/puzzle slices, and (optionally) Stage-1 winning",
        "trajectories, under CLI weights + a stratified global cap.",
        "",
        "## Exact command", "", "```", "python3 " + " ".join(argv), "```", "",
        "## Totals", "",
        "- examples: **%d** (train %d / valid %d)"
        % (len(examples), len(train), len(valid)),
        "- by source: " + ", ".join("%s=%d" % (s, per_source[s])
                                     for s in sorted(per_source)),
        "- split: BY GAME for ai-teacher/win (both swaps together); prebuilt",
        "  slices keep their original split. split_seed=%d val_frac=%s."
        % (split_seed, val_frac),
        "- ai-teacher targets are choice-only terse (no reasoning, no <think>);",
        "  count-style policy = `%s`." % count_style,
        "",
        "## Counts by (source, deck, type)", "",
    ]
    lines += _counts_table(tree)
    if mix_report.get("reserved_seed_override"):
        lines += [
            "",
            "## Seed governance",
            "",
            "**`--allow-reserved-seeds` was passed and reserved eval seed(s) "
            "(see `%s`) were" % seed_policy.DOC_POINTER,
            "found among the source games.** This dataset is **CONTAMINATED "
            "for held-out measurement** —",
            "do not use it (or anything trained on it) as an unbiased eval "
            "anchor.",
        ]
    lines += ["", "## Mix / cap", "", "```",
              json.dumps(mix_report, indent=2, ensure_ascii=False), "```", ""]
    lines += ["## Hygiene (per source)", ""]
    for name, hyg in hygienes:
        if hyg:
            lines.append("- **%s**: %s" % (
                name, ", ".join("%s=%d" % (k, v) for k, v in sorted(hyg.items()))))
    lines += ["", "## Canonical ai-teacher targets", "",
              "Assistant text is the exact final line the harness would parse",
              "(`CHOICE:`/`PICKS:`/`BLOCKS:`/`KEEP`/`MULLIGAN`), derived from",
              "`LlmSpikeGui.java`. PASS (choice=-1, play) -> `CHOICE: <n_options>`;",
              "decideOne decline -> `CHOICE: <n_options+1>`; subset size k ->",
              "`PICKS: 1..k` (exact at k in {0,n}; representative first-k",
              "otherwise); blockers k>0 dropped (pairing not logged); unknown",
              "types dropped."]
    if warnings:
        lines += ["", "## Warnings (%d)" % len(warnings), ""]
        lines += ["- " + w for w in warnings[:60]]
        if len(warnings) > 60:
            lines.append("- ... %d more" % (len(warnings) - 60))
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------- output

def write_outputs(out_dir, examples, splits, argv, sources, hygiene,
                  dedup_dropped, warnings, reserved_seed_override=False):
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
    write_readme(out_dir, counts, argv, sources, hygiene, dedup_dropped, warnings,
                 reserved_seed_override)
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


def write_readme(out_dir, counts, argv, sources, hygiene, dedup_dropped, warnings,
                 reserved_seed_override=False):
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
    if reserved_seed_override:
        lines += [
            "",
            "## Seed governance",
            "",
            "**`--allow-reserved-seeds` was passed and reserved eval seed(s) "
            "(see `%s`) were" % seed_policy.DOC_POINTER,
            "found among the source games.** This dataset is **CONTAMINATED "
            "for held-out measurement** —",
            "do not use it (or anything trained on it) as an unbiased eval "
            "anchor.",
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


def _is_float(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def parse_typed_source(s):
    """--source TYPE:SPEC[:WEIGHT].

    TYPE in {ai-teacher, dagger, prebuilt, win}. A trailing float token is the
    weight (default 1.0). SPEC is a PATH for ai-teacher/dagger/prebuilt, or
    DIR:BUILD for win. Returns a dict describing the source.

    dagger (D4 DAgger shadow-labeling): a student self-play corpus in which the
    student rows carry an additive `shadow_choice` field = the Forge-AI's label
    over the SAME menu the student faced on the student's own trajectory. Same
    SPEC shape as ai-teacher (a PATH); differs only in the builder (no win-filter,
    row selection by shadow_choice, target synthesized from the shadow label)."""
    parts = s.split(":")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            "source must be TYPE:SPEC[:WEIGHT], got %r" % s)
    typ, rest = parts[0], parts[1:]
    weight = 1.0
    if len(rest) > 1 and _is_float(rest[-1]):
        weight = float(rest[-1])
        rest = rest[:-1]
    if typ in ("ai-teacher", "dagger", "prebuilt"):
        return {"type": typ, "path": ":".join(rest), "weight": weight}
    if typ == "win":
        if len(rest) < 2:
            raise argparse.ArgumentTypeError(
                "win source needs DIR:BUILD, got %r" % s)
        return {"type": "win", "path": ":".join(rest[:-1]),
                "build": rest[-1], "weight": weight}
    raise argparse.ArgumentTypeError(
        "unknown source type %r (want ai-teacher/dagger/prebuilt/win)" % typ)


def parse_cap_per_source(s):
    """--cap-per-source MATCH:N  (MATCH = a source name substring, N = int)."""
    if ":" not in s:
        raise argparse.ArgumentTypeError("cap-per-source must be MATCH:N")
    m, n = s.rsplit(":", 1)
    try:
        return (m, int(n))
    except ValueError:
        raise argparse.ArgumentTypeError("cap-per-source N must be an int: %r" % s)


def source_name(spec):
    """Stable per-source key used for weights, caps, and reporting."""
    if spec["type"] == "win":
        return "win:%s:%s" % (spec["path"], spec.get("build"))
    return "%s:%s" % (spec["type"], spec["path"])


def run_stage2(args, preamble):
    """Build the stage2-local-v0 multi-source mix. Returns 0."""
    specs = args.source

    # Seed governance (EVAL-DESIGN.md §6.5): scan every non-exempt source
    # (ai-teacher/dagger/win are real game-dir trees; prebuilt is an
    # already-built dataset and carries no game-*.jsonl, so it's exempt) and
    # refuse before building anything if a reserved seed shows up, unless
    # overridden. See enforce_seed_policy()'s docstring. getattr() defaults
    # so lightweight args stand-ins (e.g. in tests) that predate this flag
    # don't need updating.
    allow_reserved_seeds = getattr(args, "allow_reserved_seeds", False)
    labeled_seeds = []
    for spec in specs:
        if spec["type"] in ("ai-teacher", "dagger", "win"):
            labeled_seeds += [(source_name(spec), s)
                              for s in _scan_source_seeds(spec["type"], spec["path"])]
    reserved_seed_override = enforce_seed_policy(labeled_seeds, allow_reserved_seeds)

    by_source, weights, warnings, hygienes = {}, {}, [], []
    for spec in specs:
        name = source_name(spec)
        weights[name] = spec["weight"]
        if spec["type"] == "ai-teacher":
            rows, w, hyg = build_ai_teacher_examples(
                spec["path"], preamble, args.count_style, args.ai_filter)
        elif spec["type"] == "dagger":
            rows, w, hyg = build_dagger_examples(
                spec["path"], preamble, args.count_style)
        elif spec["type"] == "prebuilt":
            rows, w, hyg = build_prebuilt_examples(spec["path"])
        elif spec["type"] == "win":
            rows, w, hyg = build_win_examples_v2(
                spec["path"], spec["build"], preamble)
        else:  # unreachable (validated at parse time)
            continue
        # namespace each row's source key so multiple sources of a type coexist
        for r in rows:
            r["_srcname"] = name
        by_source[name] = by_source.get(name, []) + rows
        warnings.extend(w)
        hygienes.append((name, hyg))

    # Held-out deck guard: runs AFTER building (a deck is a per-row property,
    # unlike a seed) but BEFORE dedup/cap/split, so dropped rows never reach the
    # split and the per-source counts below reflect what actually ships.
    heldout_slugs, heldout_note = load_heldout_deck_slugs()
    if heldout_note:
        warnings.append(heldout_note)
    heldout_dropped, heldout_deck_override = enforce_heldout_decks(
        by_source, heldout_slugs,
        getattr(args, "drop_heldout_decks", False),
        getattr(args, "allow_heldout_decks", False))
    if heldout_dropped:
        warnings.append("dropped %d held-out deck row(s) (--drop-heldout-decks)"
                        % heldout_dropped)

    # Raw per-source contribution BEFORE any dedup/cap. A declared source that
    # yields 0 rows is almost always a mistyped or cross-checkout path (relative
    # paths resolve against the SCRIPT's repo root, not cwd), so hard-fail rather
    # than let a whole curated source vanish behind a buried README warning.
    raw_counts = {name: len(rows) for name, rows in by_source.items()}
    for name in sorted(raw_counts):
        sys.stderr.write("[stage2] source %s: %d raw rows\n"
                         % (name, raw_counts[name]))
    empty = sorted(n for n, c in raw_counts.items() if c == 0)
    if empty:
        raise SystemExit(
            "[stage2] ERROR: source(s) contributed 0 rows: %s. Relative paths "
            "resolve against the script repo root %s (not cwd); check the path "
            "exists there and is non-empty." % (", ".join(empty), REPO_ROOT))

    # Cross-source dedup of keyed rows (ai-teacher/win) before capping + split.
    by_source, dedup_dropped = dedupe_keyed(by_source)
    if dedup_dropped:
        sys.stderr.write("[stage2] deduped %d near-identical keyed row(s)\n"
                         % dedup_dropped)

    # per-source caps: MATCH substring against source name
    per_source_caps = {}
    for match, n in (args.cap_per_source or []):
        for name in by_source:
            if match in name:
                per_source_caps[name] = n

    kept_by_source, mix_report = apply_mix(
        by_source, weights, per_source_caps, args.cap, args.sample_seed)
    mix_report["raw_pre_dedup"] = raw_counts
    mix_report["dedup_dropped"] = dedup_dropped
    mix_report["reserved_seed_override"] = reserved_seed_override

    examples = [r for rows in kept_by_source.values() for r in rows]

    recorded = ["eval/make_sft_data.py"]
    for spec in specs:
        tok = ("%s:%s:%s" % (spec["type"], spec["path"], spec.get("build"))
               if spec["type"] == "win"
               else "%s:%s" % (spec["type"], spec["path"]))
        if spec["weight"] != 1.0:
            tok += ":%g" % spec["weight"]
        recorded += ["--source", tok]
    recorded += ["--out-dir", args.out_dir]
    if args.cap is not None:
        recorded += ["--cap", str(args.cap)]
    for match, n in (args.cap_per_source or []):
        recorded += ["--cap-per-source", "%s:%d" % (match, n)]
    if args.count_style != "first-k":
        recorded += ["--count-style", args.count_style]
    if args.ai_filter != "win":
        recorded += ["--ai-filter", args.ai_filter]
    if allow_reserved_seeds:
        recorded += ["--allow-reserved-seeds"]
    if getattr(args, "drop_heldout_decks", False):
        recorded += ["--drop-heldout-decks"]
    if heldout_deck_override:
        recorded += ["--allow-heldout-decks"]

    train, valid, tree, per_source = write_outputs_v2(
        args.out_dir, examples, recorded, args.val_frac, args.split_seed,
        mix_report, warnings, hygienes, args.count_style)

    print("[stage2] wrote %d train + %d valid to %s"
          % (len(train), len(valid), args.out_dir))
    print("  by source: " + ", ".join("%s=%d" % (s, per_source[s])
                                       for s in sorted(per_source)))
    print("  mix: raw=%s -> final=%s (cap=%s)"
          % (mix_report["raw"], mix_report["final"], args.cap))
    if warnings:
        print("  warnings: %d (see README)" % len(warnings))
    return 0


def run_stage1(args):
    """Original Stage-1 winning-trajectory build (unchanged behavior aside
    from the seed-governance guard below)."""
    sources = args.sources or DEFAULT_SOURCES
    preamble = PreambleBuilder(args.java)

    # Seed governance (EVAL-DESIGN.md §6.5): every "win" source is a real
    # game-dir tree; refuse before building anything if a reserved seed
    # shows up, unless overridden. See enforce_seed_policy()'s docstring.
    # getattr() defaults so lightweight args stand-ins that predate this
    # flag don't need updating.
    allow_reserved_seeds = getattr(args, "allow_reserved_seeds", False)
    labeled_seeds = [(str(d), s) for d, _b in sources
                      for s in _scan_source_seeds("win", d)]
    reserved_seed_override = enforce_seed_policy(labeled_seeds, allow_reserved_seeds)

    examples, warnings, hygiene = build_examples(sources, preamble)
    examples, dedup_dropped = dedupe(examples)

    gkeys = {e["_gkey"] for e in examples}
    splits = assign_splits(gkeys, args.val_frac, args.split_seed)

    recorded = ["eval/make_sft_data.py"] + ["%s:%s" % (d, b) for d, b in sources]
    if args.out_dir != str(REPO_ROOT / "datasets" / "stage1-v0"):
        recorded += ["--out-dir", args.out_dir]
    if allow_reserved_seeds:
        recorded += ["--allow-reserved-seeds"]

    out_dir = Path(args.out_dir)
    train, valid, counts = write_outputs(
        out_dir, examples, splits, recorded, sources, hygiene,
        dedup_dropped, warnings, reserved_seed_override)

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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", type=parse_source_arg,
                    help="Stage-1 DIR:BUILD pairs (BUILD in %s). Default: the two "
                         "usable sources." % "/".join(BUILDS))
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "datasets" / "stage1-v0"),
                    help="output directory (default datasets/stage1-v0)")
    ap.add_argument("--java", default=str(DEFAULT_JAVA),
                    help="path to LlmSpikeGui.java for preamble reconstruction")
    ap.add_argument("--val-frac", type=float, default=0.1,
                    help="fraction of GAMES to hold out as valid (default 0.1)")
    ap.add_argument("--split-seed", type=int, default=20260722,
                    help="seed for the deterministic by-game split")
    ap.add_argument("--allow-reserved-seeds", action="store_true",
                    help="override the reserved-eval-seed-block refusal (see "
                         "eval/seed_policy.py / EVAL-DESIGN.md §6.5). Applies "
                         "to both Stage-1 and Stage-2 mode. Prints a loud "
                         "banner and stamps reserved_seed_override=true into "
                         "the output README/mix-report; the resulting "
                         "dataset is CONTAMINATED for held-out measurement.")
    ap.add_argument("--drop-heldout-decks", action="store_true",
                    help="exclude rows whose deck is declared held-out in "
                         "eval/holdouts.json, keeping the rest of the corpus. "
                         "The recommended remedy when a source tree happens to "
                         "contain a held-out matchup.")
    ap.add_argument("--allow-heldout-decks", action="store_true",
                    help="keep held-out deck rows instead of refusing. Stamps "
                         "heldout_deck_override=true; the dataset CONTAMINATES "
                         "those decks and eval/check_holdouts.py will fail "
                         "until eval/holdouts.json grandfathers it.")
    # ---- Stage-2 multi-source mode (activated by any --source) ----
    ap.add_argument("--source", action="append", type=parse_typed_source,
                    default=None, metavar="TYPE:SPEC[:WEIGHT]",
                    help="Stage-2 typed source. TYPE in ai-teacher/prebuilt/win. "
                         "SPEC is a PATH (ai-teacher/prebuilt) or DIR:BUILD (win). "
                         "Trailing float is the mix weight. Repeatable.")
    ap.add_argument("--cap", type=int, default=None,
                    help="Stage-2 global example cap; ai-teacher rows are "
                         "STRATIFIED-downsampled across (deck, type) to fit.")
    ap.add_argument("--cap-per-source", action="append", type=parse_cap_per_source,
                    default=None, metavar="MATCH:N",
                    help="cap sources whose name contains MATCH to N rows.")
    ap.add_argument("--sample-seed", type=int, default=20260723,
                    help="seed for deterministic downsampling (default 20260723)")
    ap.add_argument("--count-style", choices=("first-k", "exact-only"),
                    default="first-k",
                    help="count-style ai-teacher targets: 'first-k' emits "
                         "PICKS: 1..k (representative); 'exact-only' drops "
                         "partial-subset rows (keeps only k in {0,n}).")
    ap.add_argument("--ai-filter", choices=("win", "all", "win+rare"),
                    default="win",
                    help="ai-teacher seat filter: 'win' keeps only the winning "
                         "seat's rows; 'all' keeps both seats; 'win+rare' adds "
                         "the losing seat's rows for rare decision types only "
                         "(types outside %s) — long-tail menu coverage without "
                         "the AI's losing-line play/combat volume."
                         % "/".join(sorted(AI_COMMON_TYPES)))
    args = ap.parse_args(argv)

    if args.source:
        preamble = PreambleBuilder(args.java)
        return run_stage2(args, preamble)
    return run_stage1(args)


if __name__ == "__main__":
    raise SystemExit(main())
