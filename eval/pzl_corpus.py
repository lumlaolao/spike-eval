#!/usr/bin/env python3
"""Read Forge's bundled .pzl puzzle corpus and turn it into eval positions.

WHY THIS EXISTS. The PUZ axis mines positions out of played-out Forge games,
so an answer key has to be MANUFACTURED for every position -- either by the
free exhaustive lane (which `verify_keys.adjudicate_exhaustive` documents as
"PURE rule adjudication for lethal-now @ play", i.e. single-spell face lethal
and nothing else) or by the expensive rollout lane. Measured 2026-08-12: the
free lane minted lethal-now keys and EXACTLY ZERO of anything else across
three rounds and ~60k candidates, and lethal-now discriminates at 6%.

Forge ships 367 hand-authored Possibility Storm puzzles in
$FORGE_HOME/forge-gui/res/puzzle/*.pzl, and this repo vendors 4 more that
exist upstream but postdate the pinned checkout (eval/puzzles/upstream --
they go there, NOT into $FORGE_HOME, because install.sh overwrites the Forge
tree). They dissolve the key problem
outright: `Goal:Win` + `Turns:N` is enforced by the ENGINE
(gamemodes/puzzle/Puzzle.java addGoalEnforcement installs a hidden card that
makes you LOSE at your cleanup step on the turn limit unless you have already
won). There is no key to mine and no rollout to price -- the rules ARE the
answer key.

They are also hard on purpose. Possibility Storm authors a puzzle so that
exactly one line wins against every block and every opponent response, and
ships a difficulty rating with it (Mythic/Rare/Uncommon/Common).

TWO AXES THAT MUST NOT BE CONFLATED, which is why `select` stratifies on both:

  DIFFICULTY is intrinsic -- the author's rating of how hard the line is.
  RECENCY governs CONTAMINATION -- Possibility Storm publishes solutions, and
  they are republished on TCGplayer/StarCityGames/MTGGoldfish. A 2017 Mythic
  is hard AND likely memorized; a 2026 Uncommon is easier but unseen. Neither
  rating substitutes for the other. Note the .pzl file itself carries no
  solution, so the Forge repo being in a training corpus leaks the POSITION
  but not the ANSWER -- it is the puzzle SITE that leaks the answer.

WHAT THIS MODULE DOES NOT DO. It does not play the puzzles. Scoring needs the
Forge side (puzzle-mode game construction on the headless path); this module
is the corpus reader, the validator, and the suite builder, so that side has
something frozen and checked to run against.

    python3 eval/pzl_corpus.py report
    python3 eval/pzl_corpus.py select --n 100 --out eval/suites/pzl-v1
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys
import unicodedata

SCHEMA = "pzl-position.v1"
SUITE_SCHEMA = "pzl-suite.v1"

# Zone keys GameState.java accepts (see its ZONES map + the endswith() chain
# around line 524). Prefixed by a player token: p0/p1, or human/ai.
ZONE_SUFFIXES = ("battlefield", "play", "hand", "graveyard", "library",
                 "exile", "command", "sideboard", "commander")

# Card-spec attributes GameState.processCardsForZone strips off the name.
# Kept as a prefix tuple rather than an exact set: unknown attributes should
# degrade to "ignore the attribute", never to "corrupt the card name".
_ATTR_RE = re.compile(r"^(Tapped|SummonSick|Renowned|Monstrous|PhasedOut|"
                      r"FaceDown|Flipped|Transformed|Counters:|Id:|Set:|Art:|"
                      r"Attaching:|AttachedTo:|Enchanting:|Owner:|"
                      r"ExiledWith:|Imprinting:|Named:|Chosen|Damage:|"
                      r"IsCommander|OnAdventure|Foretold|Suspended|"
                      r"Class:|Level:|Dungeon:|Ring)")


def fold(name):
    """Lookup key: casefolded and stripped of diacritics.

    Two puzzles reference "Lim-Dul's Cohort" and "Bartolome del Presidio"
    while the database spells them Lim-Dûl and Bartolomé. Folding to ASCII
    resolves both. It does NOT paper over real typos -- 'Hematatite Talisman'
    (database: 'Hematite Talisman') still fails to resolve, which is correct:
    Forge cannot load that card either, so the puzzle is genuinely broken.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed
                   if not unicodedata.combining(c)).casefold()


def forge_home():
    """Same resolution order as the Makefile (FORGE_HOME ?= $(HOME)/code/forge)."""
    return os.environ.get("FORGE_HOME") or os.path.expanduser("~/code/forge")


def puzzle_dir(home=None):
    return os.path.join(home or forge_home(), "forge-gui", "res", "puzzle")


# Puzzles that exist upstream but not in the PINNED Forge checkout, vendored
# into this repo. They live HERE and not in $FORGE_HOME because
# `forge-overlay/` is the source of truth and install.sh overwrites the Forge
# tree -- anything dropped in there is lost on the next install.
EXTRA_PUZZLE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "puzzles", "upstream")


def puzzle_dirs(home=None):
    """-> [(directory, provenance label)], pinned checkout first.

    First-wins on basename, so a puzzle shipped by the pinned engine always
    beats a vendored copy of the same name. Provenance is carried onto every
    row: a `upstream-master` puzzle is NOT in the pinned engine's puzzle
    folder, so Forge at FORGE_COMMIT will not list it in puzzle mode even
    though it can load every card the puzzle references.
    """
    dirs = [(puzzle_dir(home), "forge-pinned")]
    if os.path.isdir(EXTRA_PUZZLE_DIR):
        dirs.append((EXTRA_PUZZLE_DIR, "upstream-master"))
    return dirs


# --------------------------------------------------------------- .pzl reader

def parse_pzl(path):
    """-> dict with metadata, state, and the raw section text.

    The format is Forge's FileSection: `[metadata]` / `[state]` headers and
    `Key:Value` (metadata) or `key=value` (state) lines under them.
    """
    meta, state = {}, {}
    section = None
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            low = line.lower()
            if low.startswith("[") and low.endswith("]"):
                section = low[1:-1]
                continue
            if section == "metadata":
                # Metadata is Key:Value, but values (URL, Description) contain
                # colons -- split ONCE only.
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            elif section == "state":
                if "=" in line:
                    k, v = line.split("=", 1)
                    state[k.strip().lower()] = v.strip()
    return {"file": os.path.basename(path), "metadata": meta, "state": state}


def _strip_attrs(spec):
    """'Ornery Tumblewagg|Counters:P1P1=2' -> 'Ornery Tumblewagg'.

    Returns None for token specs ('t:goblin'), which name a TOKEN and not a
    card in the database -- validating them against cardsfolder would report
    false gaps.
    """
    spec = spec.strip()
    if not spec:
        return None
    if spec.lower().startswith("t:"):
        return None
    parts = spec.split("|")
    name = parts[0].strip()
    return name or None


def card_refs(puz):
    """-> {zone_key: [card name, ...]} over every zone-bearing state key."""
    out = collections.OrderedDict()
    for key, value in puz["state"].items():
        if not any(key.endswith(sfx) for sfx in ZONE_SUFFIXES):
            continue
        names = []
        for spec in value.split(";"):
            name = _strip_attrs(spec)
            if name:
                names.append(name)
        if names:
            out[key] = names
    return out


def all_card_names(puz):
    seen = []
    for names in card_refs(puz).values():
        seen.extend(names)
    return seen


# --------------------------------------------------- Forge card-name index

def scripted_card_index(home=None):
    """-> {folded name: canonical name} over $FORGE_HOME/.../cardsfolder.

    EVERY `Name:` line in a script counts, not just the first. A Forge script
    holds all faces of a card in one file, so `dusk_dawn.txt` declares
    `Name:Dusk` and later `Name:Dawn`, and `delver_of_secrets.txt` declares
    both Delver and `Name:Insectile Aberration`. Reading only line 1 rejected
    35 puzzles that reference a split half or a transformed back face -- all
    of them perfectly loadable.

    Split cards are ALSO referenced by their joined name ('Dusk // Dawn'),
    which appears in no Name: line, so joined forms are synthesized from the
    AlternateMode:Split pairing.

    Keyed casefolded because .pzl files are hand-written and at least one
    ('Thraben foulbloods') disagrees with the database on case. The project
    has been bitten by exactly this before -- a deck-name case typo cost
    30/320 positions in overnight-3 -- so matching is case-insensitive and
    `validate` reports the canonical spelling it resolved to.
    """
    root = os.path.join(home or forge_home(), "forge-gui", "res", "cardsfolder")
    index = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith(".txt"):
                continue
            try:
                with open(os.path.join(dirpath, fn), "r",
                          encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            faces = re.findall(r"^Name:(.+)$", text, re.M)
            for name in faces:
                index.setdefault(fold(name.strip()), name.strip())
            # 'A // B' joined form for split/room/adventure scripts.
            if len(faces) >= 2 and "AlternateMode:" in text:
                joined = " // ".join(f.strip() for f in faces[:2])
                index.setdefault(fold(joined), joined)
    return index


# -------------------------------------------------------- mechanic coverage

# Two independent senses of "mechanic", kept separate because they answer
# different questions and one does not imply the other:
#   kw:  a named keyword ability/cost on the card (K: lines) -- Flying, Ward,
#        Teamwork, Cascade. This is "mechanic" in the players' sense.
#   eff: the effect primitive Forge resolves (DB$/SP$/AB$/ST$ tokens) --
#        Scry, Surveil, Proliferate, Fight, Manifest. Keyword ACTIONS live
#        here, not in K:, so a keywords-only view silently reports zero
#        coverage of scry/surveil/explore and similar.
_KW_RE = re.compile(r"^K:([^:\n|]+)", re.M)
_EFF_RE = re.compile(r"\b(?:DB|SP|AB|ST)\$\s*(\w+)")
_NAME_RE = re.compile(r"^Name:(.+)$", re.M)


def mechanic_index(home=None):
    """-> ({folded card name: frozenset(mechanics)}, Counter of all mechanics).

    The Counter spans the WHOLE card database, not just puzzle cards, so the
    suite can report what it does NOT reach. That denominator is the honest
    part: 'covers every mechanic' is only meaningful against a stated universe.
    """
    root = os.path.join(home or forge_home(), "forge-gui", "res", "cardsfolder")
    per_card, universe = {}, collections.Counter()
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith(".txt"):
                continue
            try:
                with open(os.path.join(dirpath, fn), "r",
                          encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            mechs = set()
            for kw in _KW_RE.findall(text):
                kw = kw.strip()
                if kw:
                    mechs.add("kw:" + kw)
            for eff in _EFF_RE.findall(text):
                mechs.add("eff:" + eff)
            mechs = frozenset(mechs)
            for name in _NAME_RE.findall(text):
                per_card[fold(name.strip())] = mechs
            for m in mechs:
                universe[m] += 1
    return per_card, universe


def puzzle_mechanics(puz, per_card):
    """Union of mechanics over every card the puzzle places in any zone."""
    out = set()
    for name in set(all_card_names(puz)):
        out |= per_card.get(fold(name), frozenset())
    return out


def quota_pick(pool, n, seed, per_card, quota=5):
    """Pick n puzzles maximizing how many mechanics reach `quota` examples.

    WHY A QUOTA AND NOT PLAIN COVERAGE. One puzzle containing Ninjutsu shows
    only that a model handled Ninjutsu *once, in one board state*. Judging
    whether it UNDERSTANDS the mechanic needs several independent instances,
    so the objective is max-min-ish: fill deficits toward `quota` rather than
    stopping at first touch.

    THE CORPUS CAPS THIS HARD AND THE CAP IS NOT FIXABLE BY SELECTION.
    Measured 2026-08-12 over all 366 usable puzzles: of 280 reachable
    mechanics, 146 appear in fewer than 5 puzzles and 62 appear in exactly
    ONE. Those cannot reach quota at any n. `quota_shortfall` reports each
    one with the maximum the corpus can actually supply, so the gap is a
    published number rather than a silent weakness.

    Greedy on deficit-slots-filled: a puzzle is worth the number of its
    mechanics still under quota. Ties break on (recency, difficulty, hash),
    same key as everywhere else.
    """
    rank = {d: i for i, d in enumerate(DIFF_ORDER)}

    def tiebreak(p):
        band, _ = recency_band(p)
        return (band, rank.get(p["difficulty"], 99),
                hashlib.sha256(("%s|%s" % (seed, p["file"])).encode()).hexdigest())

    mechs = {p["file"]: puzzle_mechanics(p, per_card) for p in pool}
    remaining = sorted(pool, key=tiebreak)
    picked, have = [], collections.Counter()
    while remaining and len(picked) < n:
        best, best_gain = None, 0
        for p in remaining:
            gain = sum(1 for m in mechs[p["file"]] if have[m] < quota)
            if gain > best_gain:
                best, best_gain = p, gain
        if best is None:
            break                      # every mechanic at quota (or capped)
        picked.append(best)
        for m in mechs[best["file"]]:
            have[m] += 1
        remaining.remove(best)
    for p in remaining:                # fill remaining slots, stratified
        if len(picked) >= n:
            break
        picked.append(p)
        for m in mechs[p["file"]]:
            have[m] += 1
    return picked, have


def cover_pick(pool, n, seed, per_card):
    """Pick n puzzles that cover as many distinct mechanics as possible.

    Greedy maximum-coverage first, then the plain stratified order for the
    slots left over. Greedy is not optimal for set cover, but it is within a
    (1 - 1/e) factor and -- the property that actually matters here -- it is
    deterministic and explainable, which a frozen suite needs more than it
    needs the last few percent.

    Ties are broken by the SAME (recency, difficulty, hash) key the plain
    selection uses, so among equally-informative puzzles the fresher and
    harder one still wins. 51 mechanics are carried by exactly one card in
    the whole corpus, so those puzzles are effectively forced -- which is the
    point of running coverage first instead of hoping difficulty order
    stumbles onto them.
    """
    rank = {d: i for i, d in enumerate(DIFF_ORDER)}

    def tiebreak(p):
        band, _ = recency_band(p)
        return (band, rank.get(p["difficulty"], 99),
                hashlib.sha256(("%s|%s" % (seed, p["file"])).encode()).hexdigest())

    mechs = {p["file"]: puzzle_mechanics(p, per_card) for p in pool}
    remaining = sorted(pool, key=tiebreak)
    picked, covered = [], set()
    while remaining and len(picked) < n:
        best, best_gain = None, -1
        for p in remaining:
            gain = len(mechs[p["file"]] - covered)
            if gain > best_gain:
                best, best_gain = p, gain
        if best_gain <= 0:
            break                      # nothing new to cover; fill below
        picked.append(best)
        covered |= mechs[best["file"]]
        remaining.remove(best)
    for p in remaining:                # fill by plain stratified order
        if len(picked) >= n:
            break
        picked.append(p)
    return picked, covered


# ------------------------------------------------------------- validation

_URL_DATE_RE = re.compile(r"/(20\d\d)/(\d\d)/")


def published(puz):
    """-> 'YYYY-MM' parsed from the wp-content upload path in URL, else None.

    Possibility Storm's image URLs embed the upload month, which is the only
    date any .pzl carries. It is the PUBLICATION date, so it bounds when a
    solution could have entered a training corpus.
    """
    m = _URL_DATE_RE.search(puz["metadata"].get("URL", ""))
    return "%s-%s" % (m.group(1), m.group(2)) if m else None


RECENT_FROM = "2025-06"


def recency_band(puz):
    """-> (0 recent | 1 dated-but-older | 2 undated, basis string).

    Contamination risk, not age for its own sake. Two independent signals:

      url            -- the upload month in the puzzle's image URL.
      upstream-only  -- the puzzle exists upstream but NOT in the pinned Forge
                        checkout, so it was ADDED to Forge after the pin. That
                        is a lower bound on its age and it beats having no
                        signal at all. PS_MSH1 is exactly this case: its URL is
                        a Patreon permalink with no date, yet it is a Marvel
                        Super Heroes puzzle (set released 2026-06-26) and is
                        the freshest item in the corpus. Ranking it 'undated',
                        i.e. last, would bury the single least-contaminated
                        puzzle available.

    It is a BAND and not a synthesized date on purpose -- inventing a specific
    month we did not read would be worse than admitting we only know an order.
    """
    # Prefer the value load_corpus already computed; fall back to re-reading
    # the URL only for dicts that never went through it. Recomputing
    # unconditionally made this disagree with row["published"] for any puzzle
    # built by hand, which is exactly how a suite ends up sorted by one rule
    # and labelled by another.
    pub = puz["published"] if "published" in puz else published(puz)
    if pub:
        return (0 if pub >= RECENT_FROM else 1), "url"
    if puz.get("provenance") == "upstream-master":
        return 0, "upstream-only"
    return 2, "none"


# Puzzles whose REAL objective is "maximize damage", encoded as Goal:Win
# because Forge has no maximization goal. The engine then scores them as
# "did you win", which a single point of damage can satisfy while missing the
# entire intended challenge -- so they are unscoreable, not merely odd.
# Found via a reviewer flagging PS_GRND; 4 of the 5 Difficulty:Special
# puzzles turned out to be this. Matching on the OBJECTIVE phrasing rather
# than on Difficulty:Special, because Special also covers PS_GRNS, a
# legitimate Goal:Survive puzzle that must be kept.
_MAXIMIZE_RE = re.compile(
    r"as much damage as possible|how much damage can you deal|"
    r"absolute maximum|as much damage to your opponent as possible|"
    r"how much \w+ can you channel|inflict as much damage", re.I)


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate(puz, index):
    """-> list of problem strings; empty means loadable and scoreable.

    Checks only what would make a puzzle unusable as an eval item. A puzzle
    that Forge can load but whose goal we cannot machine-check is still a
    problem HERE, because an unscoreable position in a frozen suite is a
    silent hole in the denominator.
    """
    problems = []
    meta, state = puz["metadata"], puz["state"]

    goal = meta.get("Goal", "")
    if not goal:
        problems.append("no Goal")
    turns = _int_or_none(meta.get("Turns"))
    if turns is None:
        problems.append("no integer Turns")
    elif turns > 3:
        # 9999/100/20 are open-ended sandbox puzzles, not a bounded decision.
        problems.append("unbounded Turns=%d" % turns)

    blurb = "%s %s" % (meta.get("Name", ""), meta.get("Description", ""))
    if _MAXIMIZE_RE.search(blurb):
        problems.append("unscoreable objective: asks to MAXIMIZE damage but "
                        "Goal is '%s', which the engine scores as a plain "
                        "win" % goal)

    if not state:
        problems.append("no [state] section")
    if _int_or_none(state.get("p1life")) is None and \
       _int_or_none(state.get("ailife")) is None:
        problems.append("no opponent life")

    refs = card_refs(puz)
    if not refs:
        problems.append("no cards in any zone")
    missing = sorted({n for names in refs.values() for n in names
                      if fold(n) not in index})
    if missing:
        problems.append("unscripted cards: %s" % ", ".join(missing[:4]))
    return problems


def miscased(puz, index):
    """-> [(as written, canonical)] for names that only matched casefolded.

    Not a validation failure -- Forge's own loader is case-insensitive here --
    but worth surfacing, because a case drift is how a silent 30/320 position
    loss happened once already.
    """
    out = []
    for name in all_card_names(puz):
        canon = index.get(fold(name))
        if canon and canon != name:
            out.append((name, canon))
    return out


# ------------------------------------------------------------ corpus load

def load_corpus(home=None, validate_cards=True):
    """-> (list of enriched puzzle dicts, index). Every dict gets .problems."""
    dirs = puzzle_dirs(home)
    if not os.path.isdir(dirs[0][0]):
        raise SystemExit("no puzzle dir at %s (set FORGE_HOME)" % dirs[0][0])
    index = scripted_card_index(home) if validate_cards else {}
    out, seen = [], set()
    for pdir, provenance in dirs:
        for fn in sorted(os.listdir(pdir)):
            if not fn.endswith(".pzl") or fn in seen:
                continue
            seen.add(fn)
            puz = parse_pzl(os.path.join(pdir, fn))
            puz["provenance"] = provenance
            puz["difficulty"] = puz["metadata"].get("Difficulty") or "(unrated)"
            puz["goal"] = puz["metadata"].get("Goal") or ""
            puz["turns"] = _int_or_none(puz["metadata"].get("Turns"))
            puz["published"] = published(puz)
            puz["n_cards"] = len(all_card_names(puz))
            puz["problems"] = validate(puz, index) if validate_cards else []
            out.append(puz)
    return out, index


# ---------------------------------------------------------------- reporting

DIFF_ORDER = ["Mythic", "Rare", "Uncommon", "Common", "Special", "(unrated)"]


def cmd_report(args):
    corpus, _ = load_corpus(args.forge_home)
    ok = [p for p in corpus if not p["problems"]]
    srcs = collections.Counter(p["provenance"] for p in corpus)
    print("corpus: %d .pzl files from %d source(s): %s"
          % (len(corpus), len(srcs),
             ", ".join("%s=%d" % kv for kv in sorted(srcs.items()))))
    print("usable: %d   rejected: %d" % (len(ok), len(corpus) - len(ok)))

    reasons = collections.Counter()
    for p in corpus:
        for prob in p["problems"]:
            reasons[prob.split(":")[0]] += 1
    if reasons:
        print("\nrejection reasons:")
        for r, c in reasons.most_common():
            print("  %-28s %d" % (r, c))

    print("\ndifficulty x recency (usable only):")
    era = lambda p: ["recent", "older", "unknown"][recency_band(p)[0]]
    grid = collections.defaultdict(collections.Counter)
    for p in ok:
        grid[p["difficulty"]][era(p)] += 1
    print("  %-12s %-10s %-8s %-9s %s" % ("difficulty", "recent", "older", "unknown", "total"))
    for d in DIFF_ORDER:
        if d not in grid:
            continue
        c = grid[d]
        print("  %-12s %-10d %-8d %-9d %d"
              % (d, c["recent"], c["older"], c["unknown"], sum(c.values())))

    print("\ngoal types (usable only):")
    for g, c in collections.Counter(p["goal"] for p in ok).most_common():
        print("  %-34s %d" % (g, c))
    return 0


# ----------------------------------------------------------- suite building

def to_position(puz, per_card=None):
    """One frozen eval row. `id` is content-derived so the same puzzle always
    gets the same id and a re-freeze is diffable."""
    body = json.dumps({"metadata": puz["metadata"], "state": puz["state"]},
                      sort_keys=True, separators=(",", ":"))
    pid = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return collections.OrderedDict([
        ("schema", SCHEMA),
        ("id", pid),
        ("source_file", puz["file"]),
        ("provenance", puz.get("provenance", "forge-pinned")),
        ("name", puz["metadata"].get("Name", "")),
        ("difficulty", puz["difficulty"]),
        ("published", puz["published"]),
        ("recency_band", ["recent", "older", "unknown"][recency_band(puz)[0]]),
        ("recency_basis", recency_band(puz)[1]),
        ("goal", puz["goal"]),
        ("turns", puz["turns"]),
        ("description", puz["metadata"].get("Description", "")),
        ("url", puz["metadata"].get("URL", "")),
        ("n_cards", puz["n_cards"]),
        # Carried on the row so a consumer can read what a puzzle exercises
        # without needing a Forge checkout to re-derive it.
        ("tagged_mechanics",
         sorted(puzzle_mechanics(puz, per_card)) if per_card else []),
        ("state", collections.OrderedDict(sorted(puz["state"].items()))),
    ])


def stratified_pick(pool, n, seed):
    """Pick n, hardest-first within a recency-balanced frame.

    Order is (a) recent before old, because contamination is the threat this
    corpus is most exposed to, then (b) Mythic before Rare before the rest,
    then (c) a stable content hash so the choice is reproducible and not
    alphabetical (alphabetical would over-pick whole SETS, since the filenames
    are PS_<SET><n>).
    """
    rank = {d: i for i, d in enumerate(DIFF_ORDER)}

    def key(p):
        band, _basis = recency_band(p)
        tie = hashlib.sha256(("%s|%s" % (seed, p["file"])).encode()).hexdigest()
        return (band, rank.get(p["difficulty"], 99), tie)

    return sorted(pool, key=key)[:n]


def cmd_select(args):
    corpus, _ = load_corpus(args.forge_home)
    ok = [p for p in corpus if not p["problems"]]
    if args.goal:
        ok = [p for p in ok if p["goal"].lower() == args.goal.lower()]
    if args.turns:
        ok = [p for p in ok if p["turns"] == args.turns]
    if len(ok) < args.n:
        print("WARNING: only %d usable puzzles match, wanted %d"
              % (len(ok), args.n), file=sys.stderr)

    per_card, universe = mechanic_index(args.forge_home)
    reachable = set()
    for p in ok:
        reachable |= puzzle_mechanics(p, per_card)
    # What the CORPUS can supply per mechanic -- the ceiling selection cannot
    # raise. Reported next to what the suite achieved so a shortfall reads as
    # "no more exist", not "we chose badly".
    corpus_cap = collections.Counter()
    for p in ok:
        for m in puzzle_mechanics(p, per_card):
            corpus_cap[m] += 1
    if args.cover:
        picked, have = quota_pick(ok, args.n, args.seed, per_card, args.quota)
    else:
        picked = stratified_pick(ok, args.n, args.seed)
        have = collections.Counter()
        for p in picked:
            for m in puzzle_mechanics(p, per_card):
                have[m] += 1
    covered = {m for m in have if have[m] > 0}
    at_quota = {m for m in reachable if have[m] >= args.quota}
    capped = {m for m in reachable if corpus_cap[m] < args.quota}
    shortfall = collections.OrderedDict(
        (m, {"in_suite": have[m], "max_in_corpus": corpus_cap[m]})
        for m in sorted(reachable - at_quota))
    rows = [to_position(p, per_card) for p in picked]

    jsonl = args.out + ".jsonl"
    with open(jsonl, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    with open(jsonl, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()

    diffs = collections.Counter(r["difficulty"] for r in rows)
    recent = sum(1 for r in rows if r["recency_band"] == "recent")
    usable_corpus = [p for p in corpus if not p["problems"]]
    n_usable = len(usable_corpus)
    n_undated_corpus = sum(1 for p in usable_corpus if recency_band(p)[0] == 2)
    n_undated_suite = sum(1 for r in rows if r["recency_band"] == "unknown")
    n_upstream = sum(1 for r in rows if r["provenance"] == "upstream-master")
    manifest = collections.OrderedDict([
        ("schema", SUITE_SCHEMA),
        ("name", os.path.basename(args.out)),
        ("positions_file", jsonl),
        ("n_positions", len(rows)),
        ("sha256", sha),
        ("headline_metric", "pzl.solve_1"),
        ("categories", dict(diffs)),
        ("goals", dict(collections.Counter(r["goal"] for r in rows))),
        ("turns", dict(collections.Counter(str(r["turns"]) for r in rows))),
        ("n_recent", recent),
        ("n_from_upstream", n_upstream),
        ("source", collections.OrderedDict([
            ("corpus", "Forge bundled puzzle mode ($FORGE_HOME/forge-gui/res/puzzle)"),
            ("author", "Possibility Storm (and Forge tutorial/community puzzles)"),
            ("n_corpus_files", len(corpus)),
            ("n_usable", n_usable),
            ("selection", "stratified_pick: recency band, then difficulty, "
                          "then stable content hash; seed=%d" % args.seed),
        ])),
        ("mechanic_coverage", collections.OrderedDict([
            ("definition", "kw:* = named keyword abilities/costs (Forge K: "
                           "lines). eff:* = effect primitives Forge resolves "
                           "(DB$/SP$/AB$/ST$), which is where keyword ACTIONS "
                           "like Scry/Surveil/Proliferate live. A "
                           "keywords-only count would report zero coverage of "
                           "those, so both are tracked."),
            ("n_in_forge_card_database", len(universe)),
            ("n_reachable_in_puzzle_corpus", len(reachable)),
            ("n_covered_by_this_suite", len(covered)),
            ("pct_of_reachable_covered",
             round(100.0 * len(covered) / len(reachable), 1) if reachable else 0),
            ("n_unreachable_at_any_suite_size", len(set(universe) - reachable)),
            ("selection_mode", ("greedy-quota-%d" % args.quota) if args.cover
                               else "stratified-only"),
            ("quota_per_mechanic", args.quota),
            ("n_at_quota", len(at_quota)),
            ("n_below_quota", len(reachable - at_quota)),
            ("n_capped_by_corpus",
             "%d of the %d below quota simply do not have %d puzzles anywhere "
             "in the corpus" % (len(capped), len(reachable - at_quota), args.quota)),
            ("uncovered_but_reachable", sorted(reachable - covered)),
            ("quota_shortfall", shortfall),
        ])),
        ("adjudication", collections.OrderedDict([
            ("method", "engine"),
            ("detail", "Puzzle.addGoalEnforcement installs a hidden goal card "
                       "that makes the solver LOSE at their cleanup step on "
                       "the turn limit unless the Goal is met. No mined "
                       "answer key and no rollout: the rules decide."),
        ])),
        ("floors", collections.OrderedDict([
            ("status", "NOT MEASURED -- do not quote a score off this suite yet"),
            ("note", "Per 'a score without its floor is not a score', this "
                     "manifest ships no floor numbers rather than guessed "
                     "ones. The floor here is an EXECUTED baseline (random "
                     "and Forge-AI solvers over the same puzzles), not an "
                     "analytic menu baseline as on PUZ -- there is no fixed "
                     "option menu to enumerate. Expected near zero, since a "
                     "Possibility Storm puzzle is authored so exactly one "
                     "line wins, but expected is not measured."),
        ])),
        ("known_limits", [
            "NOT YET SCOREABLE. Scoring needs puzzle-mode game construction on "
            "the headless path; this suite is the frozen corpus side only. "
            "n_positions is a count of puzzles, not of measured positions.",
            "CONTAMINATION IS THE MAIN THREAT AND IT IS UNEVEN. Possibility "
            "Storm publishes solutions and they are republished on TCGplayer / "
            "StarCityGames / MTGGoldfish, so older puzzles may be recalled "
            "rather than solved. The .pzl file itself carries NO solution, so "
            "the Forge repo sitting in a training corpus leaks the position "
            "but not the answer. %d of %d rows are published 2025-06 or later; "
            "report that subset separately before pooling."
            % (recent, len(rows)),
            "Difficulty is the AUTHOR's rating, not a measured discrimination "
            "rate. It has not been validated against model performance and "
            "must not be quoted as one until it has.",
            "Publication date is parsed from the month in the puzzle's image "
            "URL, so it is the date the PUZZLE was published, not the date its "
            "solution spread. Puzzles carrying no URL date are binned "
            "'undated' and ranked last rather than assumed old; %d of the %d "
            "usable corpus puzzles are undated, and %d of the %d rows SELECTED "
            "here are (the recency ordering filled the suite from dated "
            "puzzles first). In-suite recency: %d published 2025-06 or later, "
            "%d older but dated."
            % (n_undated_corpus, n_usable, n_undated_suite, len(rows),
               recent, len(rows) - recent - n_undated_suite),
            "%d rows come from eval/puzzles/upstream, i.e. they exist in Forge "
            "master but NOT in the pinned checkout (FORGE_COMMIT). Every card "
            "they reference IS scripted at the pin, so they load, but Forge at "
            "the pin will not list them in its own puzzle mode. Their "
            "recency_basis is 'upstream-only': being absent from the pin dates "
            "them after it, which is a LOWER BOUND, not a read date -- one "
            "(PS_MSH1) has a Patreon permalink with no month in it."
            % n_upstream,
            "MECHANIC COVERAGE IS BOUNDED BY THE CORPUS, NOT BY SUITE SIZE. "
            "%d of the %d mechanics in the Forge card database appear in NO "
            "puzzle at all, so no selection of any size can reach them; this "
            "suite covers %d of the %d that ARE reachable. 'Covers every "
            "mechanic' is therefore false as stated and true only against the "
            "reachable set -- quote it that way or not at all."
            % (len(set(universe) - reachable), len(universe),
               len(covered), len(reachable)),
            "Goal/Turns filtering keeps only bounded puzzles (Turns<=3). "
            "Open-ended sandbox entries (Turns 20/100/9999) are dropped "
            "because 'did you win' stops being a bounded decision.",
        ]),
    ])
    with open(args.out + ".json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write("\n")

    print("wrote %s (%d positions)" % (jsonl, len(rows)))
    print("      %s.json  sha256=%s" % (args.out, sha[:16]))
    print("difficulty: %s" % ", ".join("%s=%d" % (d, diffs[d])
                                       for d in DIFF_ORDER if d in diffs))
    print("recent band: %d/%d   from upstream: %d" % (recent, len(rows), n_upstream))
    print("mechanics at quota %d: %d/%d  (%d capped by corpus, cannot reach it)"
          % (args.quota, len(at_quota), len(reachable), len(capped)))
    print("mechanics: %d/%d reachable covered (%.1f%%); %d unreachable at any size"
          % (len(covered), len(reachable),
             100.0 * len(covered) / len(reachable) if reachable else 0,
             len(set(universe) - reachable)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--forge-home", default=None)
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("report", help="inventory the corpus")
    r.set_defaults(func=cmd_report)

    s = sub.add_parser("select", help="freeze a suite")
    s.add_argument("--n", type=int, default=100)
    s.add_argument("--out", default="eval/suites/pzl-v1")
    # No goal/turn filter by DEFAULT. Every goal type is enforced by the same
    # addGoalEnforcement machinery, and `validate` already rejects unbounded
    # puzzles (Turns>3), so filtering further only costs mechanic coverage:
    # Win/T1 alone reaches 273 mechanics, the full usable pool reaches 280
    # (Turns<=3 adds Miracle / Reinforce / RearrangeTopOfLibrary; non-Win
    # goals add Cumulative upkeep / Entwine / Phasing / Rampage).
    s.add_argument("--goal", default=None,
                   help="restrict to one Goal (default: any)")
    s.add_argument("--turns", type=int, default=None,
                   help="restrict to an exact Turns value (default: any bounded)")
    s.add_argument("--seed", type=int, default=20260812)
    s.add_argument("--quota", type=int, default=5,
                   help="target examples per mechanic (default 5)")
    s.add_argument("--cover", action="store_true", default=True,
                   help="maximize mechanic coverage (default)")
    s.add_argument("--no-cover", dest="cover", action="store_false",
                   help="plain difficulty/recency order, no coverage pass")
    s.set_defaults(func=cmd_select)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
