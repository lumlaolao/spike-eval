#!/usr/bin/env python3
"""Offline teacher-decision-match by REPLAY over ALREADY-LOGGED MTG Forge games.

Given (a) one or more per-decision JSONL corpora of logged games and (b) an
OpenAI-compatible chat endpoint, re-asks the endpoint each logged decision and
scores how often the endpoint's reply selects the SAME choice the log recorded.
This is a fidelity probe for a fine-tuned student against its teacher labels: no
new games are played, no GPU game engine is spun up, nothing is rewritten — the
tool only reconstructs the exact request the seat received and compares the fresh
reply to the logged answer under the harness's own acceptance rules.

WHY THIS IS SOUND (state reconstruction, not re-simulation)
-----------------------------------------------------------
Each logged decision row carries the FULL user message the seat saw (`prompt`),
the option menu (`options` / `n_options`), and the answer the harness accepted
(`choice`, a 0-based index, with -1 the PASS/decline sentinel). The harness
bridge is STATELESS: every model call sends exactly [system, user] with no
history. The user message is the row `prompt` verbatim. The system message is
NOT stored, so we RECONSTRUCT it exactly the way eval/make_sft_data.py does —
reusing its PreambleBuilder and the per-seat game-line plan fields — see
build_system() below. We then query the endpoint at temperature 0 and parse the
reply with a Python PORT of the harness's own reply parsers (decideOne /
decideMany / mulligan / blockers routers in LlmSpikeGui.java): CHOICE: <n>
1-based, PICKS: <list> by cardinality, KEEP/MULLIGAN, BLOCKS: <pairs> by count.
A reply is a MATCH when it selects the same choice the log recorded, using the
same index/count semantics — including play PASS (logged choice -1) == the LAST
menu option (menu number == n_options).

FORCED / DEGENERATE ROWS ARE NOT DECISIONS
------------------------------------------
Rows with `fallback` set (the engine substituted its own pick — not a genuine
teacher choice) or `n_options <= 1` (no real alternative) are excluded by
default, mirroring make_sft_data's fallback drop. Pass --keep-degenerate to
score them anyway.

BLOCKERS ARE NONE-ONLY BY DEFAULT
---------------------------------
make_sft_data's contract is that blockers rows only reconstruct EXACTLY when
choice==0 (`BLOCKS: NONE`); choice>0 is underdetermined (the pairing is not
logged) and always DROPPED from the SFT data. To score the same population, by
default this tool scores ONLY choice==0 (BLOCKS: NONE) blockers rows and drops
choice>0 blockers rows. Pass --keep-approx-blockers to opt back in (the by-type
table is then no longer marked "(none-only)").

CAVEATS (why the reported match rate is CONSERVATIVE)
-----------------------------------------------------
  * Single-shot: each decision is asked ONCE. The live harness retries a
    malformed reply with a corrective second attempt; this replay does not, so
    parse-failures here deflate the match rate vs. true harness acceptance.
  * Bounded counts: count-type (PICKS) acceptance is single-shot CARDINALITY
    matching with NO min/max truncation. Java truncates over-picks for bounded
    types, but that bound is unreconstructable from the logs — so an over-pick
    that Java would have silently truncated to a match is scored as a miss here.

EGRESS BOUNDARY
---------------
A replay sends the full reconstructed game snapshot (board, hands, deck plan) to
--endpoint on every call. If --endpoint is not a localhost host, the run refuses
to start unless OPENROUTER_ALLOW_EGRESS=1 is set explicitly (mirroring
eval/model_sweep.py and the CLAUDE.md egress-boundary note). Never set that flag
on the user's behalf.

Usage:
  python3 eval/replay_match.py <corpus-dir-or-file> [more...] \
      [--endpoint http://localhost:8080/v1] [--model NAME] \
      [--types play,attackers] [--tiers ai-teacher] [--deck "Doomsday"] \
      [--max-rows 500] [--sample-seed 20260723] \
      [--split-file datasets/stage2-local-v0/meta.jsonl] [--split-exclude-train]\
      [--workers 4] [--mismatch-examples 20] [--json out.json] [--dry-run]

Exit codes match the sibling analyzers: 0 success; 1 usage/input/health error.
"""

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Single source of truth for game loading + the EXACT teacher system message.
from make_sft_data import (  # noqa: E402
    DEFAULT_JAVA, BUILDS, PreambleBuilder, canonical_target,
    iter_game_dirs, load_complete_games,
    COUNT_PICKS_TYPES, SINGLE_CHOICE_TYPES, BLOCKS_TYPES,
)

DEFAULT_ENDPOINT = "http://localhost:8080/v1"
DEFAULT_MODEL = "local-model"
DEFAULT_TIERS = "ai-teacher"
DEFAULT_SAMPLE_SEED = 20260723

# Hosts we treat as on-box (no game-snapshot egress off the machine).
LOCAL_HOSTS = frozenset(("localhost", "127.0.0.1", "::1", ""))
# A run aborts (exit 1) rather than report a match rate when transport errors
# exceed this fraction of attempted rows — the denominator would be unreliable.
TRANSPORT_ERROR_BUDGET = 0.20


# ===========================================================================
# EGRESS GATE — refuse to send game snapshots off-box without explicit consent.
# Mirrors eval/model_sweep.py's OPENROUTER_ALLOW_EGRESS check and the CLAUDE.md
# egress-boundary note. The check is PURE (no network) and runs before any call.
# ===========================================================================

def endpoint_host(endpoint):
    """The lowercased hostname of an endpoint URL, with any userinfo and port
    stripped and IPv6 brackets removed ('' if unparseable)."""
    host = urllib.parse.urlsplit(endpoint).netloc.rsplit("@", 1)[-1]
    if host.startswith("["):                       # [::1]:8080 -> ::1
        host = host[1:].split("]", 1)[0]
    elif ":" in host:                              # host:port -> host
        host = host.rsplit(":", 1)[0]
    return host.lower()


def is_local_endpoint(endpoint):
    return endpoint_host(endpoint) in LOCAL_HOSTS


def egress_allowed(endpoint, env=None):
    """True if replaying to `endpoint` is permitted. Local hosts are always
    allowed; any other host requires OPENROUTER_ALLOW_EGRESS=1 in `env`
    (defaults to os.environ)."""
    if is_local_endpoint(endpoint):
        return True
    env = os.environ if env is None else env
    return env.get("OPENROUTER_ALLOW_EGRESS") == "1"


def enforce_egress_gate(endpoint, env=None):
    """Exit 1 loudly if `endpoint` is a non-local host without egress consent.
    No-op (returns None) when the replay is allowed."""
    if egress_allowed(endpoint, env):
        return
    host = endpoint_host(endpoint)
    print("REFUSING replay to non-local endpoint host %r without egress consent."
          % host, file=sys.stderr)
    print("A replay sends the full game snapshot (board, hands, deck plan) to the "
          "endpoint on every call.", file=sys.stderr)
    print("Set OPENROUTER_ALLOW_EGRESS=1 to opt in (see the egress-boundary note "
          "in CLAUDE.md / secrets.env.example).", file=sys.stderr)
    sys.exit(1)


def transport_budget_exceeded(attempted, errors):
    """True when `errors`/`attempted` is STRICTLY above TRANSPORT_ERROR_BUDGET."""
    return attempted > 0 and (errors / attempted) > TRANSPORT_ERROR_BUDGET


# ===========================================================================
# REPLY PARSING — a faithful Python port of LlmSpikeGui.java's reply routers.
# (stripMarkdown -> lastSegmentAny -> parseTagNumber / count parsing.) The
# canonical_target() docstring in make_sft_data.py documents the acceptance
# rules these implement; this is the read side of the same contract.
# ===========================================================================

_DIGITS = re.compile(r"\d+")
# lastSegmentAny strips a leading ": - <space>" run after the bare tag.
_SEP = re.compile(r"^\s*[:\-]?\s*")
# CHOICE/PICKS/BLOCKS have optional colons (FLEXIBLE_SEPARATOR_KEYS); ANSWER is a
# CHOICE synonym that keeps requiring its literal colon.
FLEXIBLE_KEYS = {"CHOICE:", "PICKS:", "BLOCKS:"}


def strip_markdown(s):
    """Port of stripMarkdown: drop code fences/ticks and bold markers."""
    if s is None:
        return None
    return (s.replace("```", "").replace("`", "")
             .replace("**", "").replace("__", ""))


def last_segment_any(reply, keys):
    """Text after the LAST line matching ANY key (case-insensitive), separator
    stripped. Port of lastSegmentAny: markdown is stripped from the whole reply
    first; a key in FLEXIBLE_KEYS matches as a bare word (colon optional)."""
    if reply is None:
        return None
    reply = strip_markdown(reply)
    found = None
    for ln in re.split(r"\r\n|\r|\n", reply):
        upper = ln.upper()
        for key in keys:
            flexible = key in FLEXIBLE_KEYS
            bare = key[:-1] if (flexible and key.endswith(":")) else key
            i = upper.find(bare.upper())
            if i >= 0:
                found = _SEP.sub("", ln[i + len(bare):])
                break  # this line matched a key; don't double-match another
    return found


def parse_tag_number(reply, key):
    """Last CHOICE/ANSWER number (1-based menu number), or -1 if none. Port of
    parseTagNumber: the ANSWER: synonym is accepted alongside `key`, and the
    FIRST digit run in the matched segment wins (trailing prose is ignored)."""
    seg = last_segment_any(reply, [key, "ANSWER:"])
    if seg is None:
        return -1
    m = _DIGITS.search(seg)
    return int(m.group()) if m else -1


def parse_picks(reply, n_options):
    """Port of decideMany's PICKS parse: the set of DISTINCT in-range 1-based
    numbers on the last PICKS line. Returns (picks_set, parsed_ok). parsed_ok is
    False only when no PICKS segment exists at all (a parse failure); an empty
    in-range set with a present segment (e.g. `PICKS: NONE`) is a valid size-0
    pick."""
    seg = last_segment_any(reply, ["PICKS:"])
    if seg is None:
        return set(), False
    picks = set()
    for m in _DIGITS.finditer(seg):
        idx = int(m.group())
        if 1 <= idx <= n_options:
            picks.add(idx)
    return picks, True


def parse_mulligan(reply):
    """Port of the mulligan router: last KEEP/MULLIGAN token wins. Returns
    0 (KEEP), 1 (MULLIGAN), or None (neither token -> parse failure)."""
    if reply is None:
        return None
    r = reply.upper()
    k, mu = r.rfind("KEEP"), r.rfind("MULLIGAN")
    if k < 0 and mu < 0:
        return None
    keep = k >= 0 and (mu < 0 or k > mu)
    return 0 if keep else 1


def parse_blocks_count(reply):
    """Port of the blockers router's COUNT: `BLOCKS: NONE` (or a missing/empty
    segment) -> 0; else the number of `X->Y` assignment segments. Returns
    (count, parsed_ok); parsed_ok is False when no BLOCKS segment exists."""
    seg = last_segment_any(reply, ["BLOCKS:"])
    if seg is None:
        return 0, False
    if "NONE" in seg.upper():
        return 0, True
    count = 0
    for pair in seg.split(","):
        if "->" in pair:
            count += 1
    return count, True


# ---------------------------------------------------- expected logged answer

def expected_menu_number(d):
    """The 1-based CHOICE menu number that corresponds to this row's logged
    `choice`, for SINGLE_CHOICE_TYPES. Mirrors canonical_target():
      ch >= 0                 -> ch + 1
      ch == -1, type == play  -> n_options      (PASS is the LAST menu option)
      ch == -1, other         -> n_options + 1  (the 'decline/none' label)
    Returns None when the row is out of range / not reconstructable."""
    ch = d.get("choice")
    opts = d.get("options") if isinstance(d.get("options"), list) else []
    n = d.get("n_options")
    if not isinstance(n, int) or isinstance(n, bool):
        n = len(opts)
    if not isinstance(ch, int) or isinstance(ch, bool):
        return None
    if ch >= 0:
        return ch + 1
    if ch == -1:
        if d.get("type") == "play":
            return n if n >= 1 else None
        return n + 1 if n >= 0 else None
    return None


def decision_family(dtype):
    if dtype == "mulligan":
        return "mulligan"
    if dtype in COUNT_PICKS_TYPES:
        return "count"
    if dtype in BLOCKS_TYPES:
        return "blocks"
    if dtype in SINGLE_CHOICE_TYPES:
        return "single"
    return "unknown"


def score_reply(d, reply):
    """Compare a fresh `reply` to the logged decision `d`.

    Returns (match: bool, parse_ok: bool, detail: str). parse_ok=False means the
    reply had no line the harness would accept for this decision family (counted
    in the parse-failure bucket AND as a non-match). Match/count semantics are
    identical to the harness acceptance rules ported above."""
    fam = decision_family(d.get("type"))
    ch = d.get("choice")
    opts = d.get("options") if isinstance(d.get("options"), list) else []
    n = d.get("n_options")
    if not isinstance(n, int) or isinstance(n, bool):
        n = len(opts)

    if fam == "mulligan":
        got = parse_mulligan(reply)
        if got is None:
            return False, False, "no KEEP/MULLIGAN token"
        # logged choice: 0==KEEP, 1==MULLIGAN (options[choice] agrees).
        return (got == ch), True, "keep" if got == 0 else "mulligan"

    if fam == "count":
        picks, ok = parse_picks(reply, n)
        if not ok:
            return False, False, "no PICKS line"
        want = ch if (isinstance(ch, int) and not isinstance(ch, bool)) else None
        # count-style: cardinality must match the logged subset size.
        return (want is not None and len(picks) == want), True, \
            "picks=%d want=%s" % (len(picks), want)

    if fam == "blocks":
        cnt, ok = parse_blocks_count(reply)
        if not ok:
            return False, False, "no BLOCKS line"
        want = ch if (isinstance(ch, int) and not isinstance(ch, bool)) else None
        return (want is not None and cnt == want), True, \
            "blocks=%d want=%s" % (cnt, want)

    if fam == "single":
        got = parse_tag_number(reply, "CHOICE:")
        if got < 0:
            return False, False, "no CHOICE number"
        want = expected_menu_number(d)
        return (want is not None and got == want), True, \
            "choice=%d want=%s" % (got, want)

    return False, False, "unknown decision type %r" % d.get("type")


# ===========================================================================
# SYSTEM-MESSAGE RECONSTRUCTION (reuse make_sft_data's PreambleBuilder)
# ===========================================================================

def seat_plan(game, seat):
    """The GAME PLAN string for `seat` in `game`, from the plan_<ident> field of
    whichever identity that seat name maps to (deck_a/deck_b). '' when unknown
    or a mirror matchup collapses the map (same fallback as make_sft_data)."""
    ident_by_seat = {}
    for il in ("b", "a"):                      # 'a' wins on a name tie (mirror)
        sname = game.get("deck_%s" % il)
        if sname:
            ident_by_seat[sname] = il
    rid = ident_by_seat.get(seat)
    if rid is None:
        return ""
    return game.get("plan_%s" % rid) or ""


def build_system(preamble, game, d, build):
    """Reconstruct the exact system message the seat received for decision `d`.

    ai-teacher / fast tiers use the TERSE base framing (never STATIC_REFERENCE),
    exactly as make_sft_data's build_ai_teacher_examples does. smart tier uses
    the verbose framing, with STATIC_REFERENCE gated by `build` — see BUILDS."""
    seat = d.get("seat")
    plan = seat_plan(game, seat)
    tier = d.get("tier")
    if tier == "smart":
        return preamble.system(seat, plan, "smart", build)
    if tier == "fast":
        return preamble.system(seat, plan, "fast", build)
    # ai-teacher (and any other non-model tier): terse base, no STATIC_REFERENCE.
    return preamble.base(seat, plan, terse=True)


# ===========================================================================
# ROW COLLECTION + FILTERING
# ===========================================================================

class Row:
    """One replay unit: the decision `d`, its game line, and provenance."""
    __slots__ = ("d", "game", "source_file", "source_subdir", "corpus",
                 "decision_index")

    def __init__(self, d, game, source_file, source_subdir, corpus, di):
        self.d = d
        self.game = game
        self.source_file = source_file
        self.source_subdir = source_subdir
        self.corpus = corpus
        self.decision_index = di

    def rid(self):
        """Stable identity used for deterministic sampling and mismatch refs."""
        return "%s|%s|%s|%d" % (self.corpus, self.source_subdir,
                                self.source_file, self.decision_index)


class NoRowsFromFile(Exception):
    """An explicit FILE corpus argument contributed zero replayable rows —
    superseded by a newer rerun of the same (seed, swap), still in-flight (no
    game-summary line), or decision-free. A hard error (not a buried warning):
    the caller asked for that specific file and it cannot be replayed."""


def load_rows(corpora):
    """Load every decision Row from the given corpus paths (files or dir trees).

    Directories are walked for game-*.jsonl (per-matchup, so load_complete_games'
    rerun-dedup stays correct); a file path is loaded directly. Returns
    (rows, warnings). Raises NoRowsFromFile when an explicit FILE argument
    resolves to zero rows after rerun-dedup."""
    rows, warnings = [], []
    for cp in corpora:
        p = Path(cp).expanduser()
        corpus = str(cp)
        if p.is_dir():
            for gd in iter_game_dirs(p):
                games, w = load_complete_games(gd)
                rel = str(gd.relative_to(p)) if gd != p else "."
                warnings.extend("%s[%s]: %s" % (corpus, rel, x) for x in w)
                for e in games:
                    for di, d in enumerate(e["decisions"]):
                        rows.append(Row(d, e["game"], e["file"], rel, corpus, di))
        elif p.is_file():
            games, w = load_complete_games(p.parent)
            warnings.extend("%s: %s" % (corpus, x) for x in w)
            matched = 0
            for e in games:
                if e["file"] != p.name:
                    continue
                for di, d in enumerate(e["decisions"]):
                    rows.append(Row(d, e["game"], e["file"], ".", corpus, di))
                    matched += 1
            if matched == 0:
                raise NoRowsFromFile(
                    "explicit file %s resolved to 0 rows after rerun-dedup "
                    "(superseded by a newer rerun, still in-flight without a "
                    "game-summary line, or decision-free) — nothing to replay"
                    % cp)
        else:
            warnings.append("not a file or directory: %s" % cp)
    return rows, warnings


def read_split_files(meta_path, exclude_train):
    """Read a dataset meta.jsonl and return (keep_set, mode).

    Default (exclude_train=False): keep = source_file basenames whose split is
    'valid' — restrict the replay to the dataset's held-out games.
    exclude_train=True: keep = every source_file NOT in the train split — a
    superset that additionally admits games the dataset never saw.

    Matching is by source_file basename (make_sft_data records the game file
    name there for ai-teacher/win rows)."""
    train, valid = set(), set()
    with open(Path(meta_path).expanduser(), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            sf = m.get("source_file")
            if not isinstance(sf, str):
                continue
            (valid if m.get("split") == "valid" else train).add(sf)
    if exclude_train:
        return train, "exclude-train"   # rows in this set are DROPPED
    return valid, "valid-only"           # only rows in this set are KEPT


def _int(v):
    return v if (isinstance(v, int) and not isinstance(v, bool)) else None


def apply_filters(rows, args):
    """Filter rows by the CLI predicates, then deterministically sample.

    Returns (kept_rows, stats) where stats is an ordered list of
    (stage, count) tuples for the dry-run / report funnel."""
    types = set(t.strip() for t in args.types.split(",")) if args.types else None
    tiers = set(t.strip() for t in args.tiers.split(",")) if args.tiers else None
    decks = set(d.strip() for d in args.deck.split(",")) if args.deck else None

    split_keep = split_mode = None
    if args.split_file:
        split_keep, split_mode = read_split_files(
            args.split_file, args.split_exclude_train)

    stats = [("loaded", len(rows))]

    def keep(rows, pred):
        return [r for r in rows if pred(r)]

    # forced/degenerate exclusion (default on)
    if not args.keep_degenerate:
        rows = keep(rows, lambda r: r.d.get("fallback") is None)
        stats.append(("after drop-fallback", len(rows)))
        rows = keep(rows, lambda r: (_int(r.d.get("n_options")) or 0) > 1)
        stats.append(("after drop-n_options<=1", len(rows)))

    # Blockers are none-only by default: make_sft_data DROPS choice>0 blockers
    # (the pairing is underdetermined), so to score the same population we keep
    # only choice==0 (BLOCKS: NONE) blockers rows. --keep-approx-blockers opts in.
    if not args.keep_approx_blockers:
        rows = keep(rows, lambda r: not (
            decision_family(r.d.get("type")) == "blocks"
            and (_int(r.d.get("choice")) != 0)))
        stats.append(("after drop-approx-blockers", len(rows)))

    if tiers is not None:
        rows = keep(rows, lambda r: r.d.get("tier") in tiers)
        stats.append(("after --tiers", len(rows)))
    if types is not None:
        rows = keep(rows, lambda r: r.d.get("type") in types)
        stats.append(("after --types", len(rows)))
    if decks is not None:
        rows = keep(rows, lambda r: r.d.get("seat") in decks)
        stats.append(("after --deck", len(rows)))

    # Reconstructability: a row we cannot score is not a decision we can replay.
    def scoreable(r):
        fam = decision_family(r.d.get("type"))
        if fam == "unknown":
            return False
        if fam == "single":
            return expected_menu_number(r.d) is not None
        return _int(r.d.get("choice")) is not None
    rows = keep(rows, scoreable)
    stats.append(("after scoreable", len(rows)))

    if split_keep is not None:
        if split_mode == "valid-only":
            rows = keep(rows, lambda r: r.source_file in split_keep)
        else:  # exclude-train
            rows = keep(rows, lambda r: r.source_file not in split_keep)
        stats.append(("after --split-file (%s)" % split_mode, len(rows)))

    # Deterministic seeded sampling: stable hash order, take first N. Independent
    # of scan order and endpoint, so a rerun re-asks the identical row set.
    if args.max_rows is not None and len(rows) > args.max_rows:
        rows = sorted(rows, key=lambda r: hashlib.sha256(
            ("%d:%s" % (args.sample_seed, r.rid())).encode("utf-8")).hexdigest())
        rows = rows[:args.max_rows]
        stats.append(("after --max-rows sample", len(rows)))

    return rows, stats


# ===========================================================================
# ENDPOINT
# ===========================================================================

def _post_chat(endpoint, model, messages, max_tokens, timeout):
    """One OpenAI-compatible /chat/completions call. Returns the assistant text.
    Raises urllib.error.URLError / OSError / ValueError on transport/parse
    failure (the caller decides how loud to be)."""
    url = endpoint.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        obj = json.loads(resp.read().decode("utf-8", "replace"))
    # mlx_lm server omits the "content" key entirely when the token budget is
    # exhausted before any text token (e.g. a 1-token reply that spent its
    # budget on a leading newline). Treat as empty text, not a transport error
    # — an empty scoring reply lands in the parse-failure bucket, which is the
    # honest outcome.
    return obj["choices"][0]["message"].get("content", "")


def health_probe(endpoint, model, timeout):
    """Fail loudly unless the endpoint answers a 1-token request within
    `timeout` seconds. Returns the measured latency (s)."""
    t0 = time.time()
    _post_chat(endpoint, model,
               [{"role": "user", "content": "ping"}],
               max_tokens=4, timeout=timeout)
    return time.time() - t0


# ===========================================================================
# WILSON 95% CI
# ===========================================================================

def wilson_ci(hits, n, z=1.959963984540054):
    """Wilson score interval for a binomial proportion. Returns (lo, hi) in
    [0,1], or (None, None) for n == 0. z defaults to the 95% two-sided value."""
    if n <= 0:
        return None, None
    phat = hits / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    half = (z * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))) / denom
    return max(0.0, center - half), min(1.0, center + half)


# ===========================================================================
# REPLAY DRIVER
# ===========================================================================

def replay(rows, preamble, args):
    """Query the endpoint for every row (threaded) and score each reply.

    Returns a list of result dicts (one per row, in input order) with the row's
    provenance, the reconstructed messages' lengths, the reply, and the score.
    A transport error is a non-match with error!=None (not a parse failure)."""
    results = [None] * len(rows)
    lock = threading.Lock()
    done = [0]

    def work(i, r):
        d = r.d
        try:
            system = build_system(preamble, r.game, d, args.build)
        except Exception as e:                 # noqa: BLE001 (report, don't die)
            return {"i": i, "row": r, "error": "system-reconstruct: %s" % e,
                    "reply": None, "match": False, "parse_ok": False,
                    "detail": "reconstruct failed"}
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": d.get("prompt") or ""}]
        err = reply = None
        for attempt in range(args.retries + 1):
            try:
                reply = _post_chat(args.endpoint, args.model, messages,
                                   args.max_tokens, args.req_timeout)
                err = None
                break
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                err = "%s" % e
                if attempt < args.retries:
                    time.sleep(0.25 * (attempt + 1))
        if err is not None:
            return {"i": i, "row": r, "error": err, "reply": None,
                    "match": False, "parse_ok": False, "detail": "transport"}
        match, parse_ok, detail = score_reply(d, reply)
        return {"i": i, "row": r, "error": None, "reply": reply,
                "match": match, "parse_ok": parse_ok, "detail": detail}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, i, r): i for i, r in enumerate(rows)}
        for fut in concurrent.futures.as_completed(futs):
            res = fut.result()
            results[res["i"]] = res
            with lock:
                done[0] += 1
                if args.progress and (done[0] % args.progress == 0
                                      or done[0] == len(rows)):
                    print("  ... %d/%d replayed" % (done[0], len(rows)),
                          file=sys.stderr)
    return results


# ===========================================================================
# REPORT
# ===========================================================================

def _bucket(results, keyfn):
    """dim value -> [n, hits, parse_fail, transport_err]."""
    out = defaultdict(lambda: [0, 0, 0, 0])
    for res in results:
        k = keyfn(res)
        out[k][0] += 1
        out[k][1] += 1 if res["match"] else 0
        out[k][2] += 0 if (res["parse_ok"] or res["error"]) else 1
        out[k][3] += 1 if res["error"] else 0
    return out


def majority_class_baseline(rows):
    """The match rate a model scores by ALWAYS emitting the most common target.

    Decision-match is a classification accuracy over a wildly unbalanced label
    set: ~78% of scoreable rows in the d1-selfplay corpus have PASS as their
    target, because the Forge AI declines to act on most priority windows and
    canonical_target encodes that decline as the last menu option. An 88% match
    rate against a 78% floor is +10 points of real signal, not 88 -- and the
    difference is not cosmetic, because the minority class is exactly the set
    of decisions that decide games. Reporting the rate without this number
    invites reading a near-trivial score as near-perfect play.

    Returns {"target": str|None, "rate": float|None, "n": int,
             "distribution": {target: count}} over the rows actually replayed."""
    counts = Counter()
    for r in rows:
        # Collapse to the answer SHAPE, not the literal string: "CHOICE: 4" and
        # "CHOICE: 7" are both a pass when they name the last option, and a
        # baseline forced to guess the exact integer would understate the floor.
        # canonical_target's `kind` already encodes that distinction.
        tgt, kind = canonical_target(r.d)
        if tgt is None:
            continue
        counts[kind] += 1
    n = sum(counts.values())
    if not n:
        return {"target": None, "rate": None, "n": 0, "distribution": {}}
    top, top_n = counts.most_common(1)[0]
    return {"target": top, "rate": top_n / n, "n": n,
            "distribution": dict(counts.most_common())}


def build_report(results, rows, stats, args, health_s):
    # Transport-errored rows are NOT decisions we scored: they leave the match
    # denominator and the Wilson CI, reported separately as transport_errors.
    attempted = len(results)
    errors = sum(1 for r in results if r["error"])
    scored = attempted - errors
    hits = sum(1 for r in results if r["match"])
    parse_fail = sum(1 for r in results
                     if not r["parse_ok"] and not r["error"])
    lo, hi = wilson_ci(hits, scored)
    baseline = majority_class_baseline(rows)

    def roll(bucket):
        out = {}
        for k, (bn, bh, bpf, berr) in sorted(bucket.items(),
                                             key=lambda kv: str(kv[0])):
            bscored = bn - berr
            out[str(k)] = {"n": bn, "scored": bscored, "hits": bh,
                           "match_rate": (bh / bscored) if bscored else None,
                           "parse_fail": bpf, "transport_errors": berr}
        return out

    mismatches = []
    for res in results:
        if res["match"]:
            continue
        if len(mismatches) >= args.mismatch_examples:
            break
        r = res["row"]
        d = r.d
        mismatches.append({
            "rid": r.rid(),
            "type": d.get("type"),
            "seat": d.get("seat"),
            "tier": d.get("tier"),
            "turn": d.get("turn"),
            "phase": d.get("phase"),
            "n_options": d.get("n_options"),
            "logged_choice": d.get("choice"),
            "logged_target": canonical_target(d)[0],
            "reply": (res["reply"] or "")[-400:],
            "error": res["error"],
            "detail": res["detail"],
        })

    return {
        "endpoint": args.endpoint,
        "model": args.model,
        "health_latency_s": round(health_s, 3) if health_s is not None else None,
        "corpora": list(args.corpora),
        "filters": {
            "types": args.types, "tiers": args.tiers, "deck": args.deck,
            "max_rows": args.max_rows, "sample_seed": args.sample_seed,
            "split_file": args.split_file,
            "split_exclude_train": args.split_exclude_train,
            "keep_degenerate": args.keep_degenerate,
            "keep_approx_blockers": args.keep_approx_blockers,
        },
        "blocks_none_only": not args.keep_approx_blockers,
        "funnel": [{"stage": s, "n": c} for s, c in stats],
        "attempted": attempted,
        "scored": scored,
        "n_replayed": attempted,   # back-compat alias for `attempted`
        "hits": hits,
        "match_rate": (hits / scored) if scored else None,
        "wilson95": {"lo": lo, "hi": hi},
        # A match rate is meaningless without the floor it is beating.
        "majority_class_baseline": baseline,
        "margin_over_baseline": (
            (hits / scored) - baseline["rate"]
            if scored and baseline["rate"] is not None else None),
        "parse_failures": parse_fail,
        "transport_errors": errors,
        "transport_error_budget": TRANSPORT_ERROR_BUDGET,
        "transport_budget_exceeded": transport_budget_exceeded(attempted, errors),
        "by_type": roll(_bucket(results, lambda r: r["row"].d.get("type"))),
        "by_deck": roll(_bucket(results, lambda r: r["row"].d.get("seat"))),
        "by_tier": roll(_bucket(results, lambda r: r["row"].d.get("tier"))),
        "mismatch_examples": mismatches,
    }


def fmt_pct(x):
    return "-" if x is None else "%.1f%%" % (100.0 * x)


def render_text(r):
    L = []
    L.append("=" * 72)
    L.append("REPLAY DECISION-MATCH — logged teacher choices re-asked offline")
    L.append("=" * 72)
    L.append("endpoint: %s   model: %s   (health probe %ss)"
             % (r["endpoint"], r["model"], r["health_latency_s"]))
    L.append("corpora: %s" % ", ".join(r["corpora"]))
    L.append("")
    L.append("### FILTER FUNNEL")
    for f in r["funnel"]:
        L.append("  %-32s %d" % (f["stage"], f["n"]))
    L.append("")
    L.append("### OVERALL")
    w = r["wilson95"]
    ci = ("[%s, %s]" % (fmt_pct(w["lo"]), fmt_pct(w["hi"]))
          if w["lo"] is not None else "-")
    L.append("MATCH RATE: %s  (%d/%d scored)   Wilson 95%% CI %s"
             % (fmt_pct(r["match_rate"]), r["hits"], r["scored"], ci))
    b = r.get("majority_class_baseline") or {}
    if b.get("rate") is not None:
        L.append("MAJORITY-CLASS BASELINE: %s — a model that ONLY ever emits "
                 "'%s' scores this." % (fmt_pct(b["rate"]), b["target"]))
        L.append("MARGIN OVER BASELINE: %+.1f pts  <- this, not the match rate, "
                 "is the signal." % (100 * (r["margin_over_baseline"] or 0)))
        L.append("  Decision-match is accuracy over a very unbalanced label set;")
        L.append("  the minority class is where games are decided, so a high")
        L.append("  match rate is compatible with a low win rate.")
    L.append("attempted: %d   scored: %d   transport errors: %d (excluded from "
             "the rate)   parse failures: %d"
             % (r["attempted"], r["scored"], r["transport_errors"],
                r["parse_failures"]))
    if r.get("transport_budget_exceeded"):
        L.append("WARNING: transport errors exceed the %.0f%% budget — the match "
                 "rate is UNRELIABLE." % (100.0 * r["transport_error_budget"]))

    for dim, title in (("by_type", "BY DECISION TYPE"),
                       ("by_deck", "BY DECK / SEAT"),
                       ("by_tier", "BY TIER")):
        L.append("")
        L.append("### %s" % title)
        rows = r[dim]
        for k in sorted(rows, key=lambda k: -rows[k]["n"]):
            v = rows[k]
            label = k
            if (dim == "by_type" and r.get("blocks_none_only")
                    and decision_family(k) == "blocks"):
                label = "%s (none-only)" % k
            L.append("  %-24s n=%-5d match=%s (%d/%d)  parse-fail=%d  err=%d"
                     % (label, v["n"], fmt_pct(v["match_rate"]), v["hits"],
                        v["scored"], v["parse_fail"], v["transport_errors"]))

    if r["mismatch_examples"]:
        L.append("")
        L.append("### MISMATCH EXAMPLES (first %d)" % len(r["mismatch_examples"]))
        for m in r["mismatch_examples"]:
            L.append("  - [%s] %s seat=%s tier=%s turn=%s"
                     % (m["type"], m["rid"], m["seat"], m["tier"], m["turn"]))
            L.append("      logged choice=%s (canonical target=%r)  n_options=%s"
                     % (m["logged_choice"], m["logged_target"], m["n_options"]))
            if m["error"]:
                L.append("      TRANSPORT ERROR: %s" % m["error"])
            else:
                L.append("      score: %s" % m["detail"])
                L.append("      reply tail: %r" % m["reply"])

    L.append("")
    L.append("### CAVEATS (the reported match rate is CONSERVATIVE)")
    L.append("  - Single-shot: each decision is asked ONCE — no corrective second")
    L.append("    attempt like the live harness — so parse-failures deflate this")
    L.append("    match rate vs. true harness acceptance.")
    L.append("  - Bounded counts: count-type (PICKS) acceptance is single-shot")
    L.append("    cardinality matching with NO min/max truncation. Java truncates")
    L.append("    over-picks for bounded types, but that bound is unreconstructable")
    L.append("    from the logs, so an over-pick Java would truncate to a match is")
    L.append("    scored as a miss here.")
    return "\n".join(L)


def render_dry_run(rows, stats):
    L = ["=" * 72,
         "REPLAY DRY-RUN — filter funnel + reconstruction check (no queries)",
         "=" * 72, "### FILTER FUNNEL"]
    for s, c in stats:
        L.append("  %-32s %d" % (s, c))
    by_type = Counter(r.d.get("type") for r in rows)
    by_tier = Counter(r.d.get("tier") for r in rows)
    by_deck = Counter(r.d.get("seat") for r in rows)
    for title, ctr in (("BY TYPE", by_type), ("BY TIER", by_tier),
                       ("BY DECK/SEAT", by_deck)):
        L.append("")
        L.append("### %s" % title)
        for k, c in ctr.most_common():
            L.append("  %-24s %d" % (k, c))
    return "\n".join(L)


# ===========================================================================
# MAIN
# ===========================================================================

def build_arg_parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpora", nargs="+",
                    help="per-decision JSONL corpus dirs or files")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                    help="OpenAI-compatible base URL (default %s)" % DEFAULT_ENDPOINT)
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="model name to send (default %s)" % DEFAULT_MODEL)
    ap.add_argument("--java", default=str(DEFAULT_JAVA),
                    help="LlmSpikeGui.java for preamble reconstruction")
    ap.add_argument("--build", default="FIX", choices=sorted(BUILDS),
                    help="preamble build for smart-tier STATIC_REFERENCE gating "
                         "(default FIX; ignored for ai-teacher/fast/terse)")
    ap.add_argument("--types", help="comma list of decision types to keep")
    ap.add_argument("--tiers", default=DEFAULT_TIERS,
                    help="comma list of tiers to keep (default %s; '' = all)"
                         % DEFAULT_TIERS)
    ap.add_argument("--deck", help="comma list of seat/deck names to keep")
    ap.add_argument("--max-rows", type=int, help="cap rows (seeded sample)")
    ap.add_argument("--sample-seed", type=int, default=DEFAULT_SAMPLE_SEED,
                    help="deterministic sampling seed (default %d)" % DEFAULT_SAMPLE_SEED)
    ap.add_argument("--split-file", metavar="META",
                    help="a dataset meta.jsonl; keep only its valid-split game "
                         "files (or with --split-exclude-train, drop train files)")
    ap.add_argument("--split-exclude-train", action="store_true",
                    help="with --split-file: DROP train-split files (keep valid "
                         "+ unseen) instead of keeping only valid")
    ap.add_argument("--keep-degenerate", action="store_true",
                    help="do NOT drop fallback / n_options<=1 rows")
    ap.add_argument("--keep-approx-blockers", action="store_true",
                    help="score choice>0 blockers rows too (default: NONE-only, "
                         "matching make_sft_data's blocks-underdetermined drop)")
    ap.add_argument("--workers", type=int, default=4, help="concurrent threads")
    ap.add_argument("--retries", type=int, default=1,
                    help="transport retries per row (default 1)")
    ap.add_argument("--req-timeout", type=float, default=60.0,
                    help="per-request timeout seconds (default 60)")
    ap.add_argument("--health-timeout", type=float, default=15.0,
                    help="health-probe hard timeout seconds (default 15)")
    ap.add_argument("--max-tokens", type=int, default=512,
                    help="max completion tokens per reply (default 512)")
    ap.add_argument("--mismatch-examples", type=int, default=20,
                    help="how many mismatch examples to record (default 20)")
    ap.add_argument("--progress", type=int, default=0,
                    help="print progress every N rows to stderr (0=off)")
    ap.add_argument("--json", metavar="PATH", nargs="?", const="-",
                    help="emit JSON summary (to PATH, or stdout if bare)")
    ap.add_argument("--dry-run", action="store_true",
                    help="filter + reconstruct only; do not query the endpoint")
    return ap


def main(argv=None):
    ap = build_arg_parser()
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    if args.tiers == "":
        args.tiers = None

    # Egress gate (pure, no network) — refuse to send snapshots off-box unless
    # opted in. Skipped for --dry-run, which never queries the endpoint.
    if not args.dry_run:
        enforce_egress_gate(args.endpoint)

    try:
        rows, warnings = load_rows(args.corpora)
    except NoRowsFromFile as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(1)
    if not rows:
        print("no decision rows found in: %s" % ", ".join(args.corpora),
              file=sys.stderr)
        for w in warnings[:20]:
            print("  - " + w, file=sys.stderr)
        sys.exit(1)

    kept, stats = apply_filters(rows, args)

    # Build the preamble now so a Java-source drift fails before any query.
    try:
        preamble = PreambleBuilder(args.java)
    except (OSError, AssertionError, ValueError, StopIteration) as e:
        print("preamble reconstruction failed (%s): %s" % (args.java, e),
              file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        # Reconstruct every system message so a reconstruct error surfaces here.
        recon_fail = 0
        for r in kept:
            try:
                build_system(preamble, r.game, r.d, args.build)
            except Exception:                  # noqa: BLE001
                recon_fail += 1
        print(render_dry_run(kept, stats))
        print("\nsystem-message reconstruction: %d ok, %d failed"
              % (len(kept) - recon_fail, recon_fail))
        if warnings:
            print("\nwarnings (%d):" % len(warnings))
            for w in warnings[:20]:
                print("  - " + w)
        sys.exit(0)

    if not kept:
        print("no rows survived the filters (see --dry-run)", file=sys.stderr)
        sys.exit(1)

    # Health-probe: fail loudly if the endpoint is down or slow.
    try:
        health_s = health_probe(args.endpoint, args.model, args.health_timeout)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        print("HEALTH PROBE FAILED for %s (model=%s): %s"
              % (args.endpoint, args.model, e), file=sys.stderr)
        print("endpoint down, unreachable, or slower than --health-timeout=%ss"
              % args.health_timeout, file=sys.stderr)
        sys.exit(1)

    results = replay(kept, preamble, args)
    report = build_report(results, kept, stats, args, health_s)
    report["warnings"] = warnings

    # Hard-fail (no misleading report) when transport errors blow the budget:
    # the match denominator would be too eroded to trust.
    if transport_budget_exceeded(report["attempted"], report["transport_errors"]):
        print("TRANSPORT ERRORS %d/%d (%.1f%%) exceed the %.0f%% budget — "
              "aborting without a match-rate report (denominator unreliable)."
              % (report["transport_errors"], report["attempted"],
                 100.0 * report["transport_errors"] / report["attempted"],
                 100.0 * TRANSPORT_ERROR_BUDGET), file=sys.stderr)
        sys.exit(1)

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
