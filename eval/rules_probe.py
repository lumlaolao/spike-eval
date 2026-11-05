#!/usr/bin/env python3
"""Rules probe: a static, engine-labelled rules-knowledge item set mined from recorded games (no game is played).

An item is (state, proposed play) -> legal? and, if not, which rule. The labels come from the engine, never from a
person or a model:
- `menu`: a play on a recorded play menu is legal (the menu is the engine's enumeration; a legal action is never
  hidden, and ProposeActionsTest.everyMenuPlayIsReachableByAProposal checks a proposal lands on it).
- `menu-absence`: a card in hand with no CAST (or no PLAY LAND) entry on that menu is not playable now. The label is
  the engine's; the CATEGORY of such an item is inferred from the state, in the engine's order of checks
  (`LlmSpikeGui.rejection`), and marked `category_source: "inferred"`.
- `engine-verdict`: a proposal the PROPOSE_ACTIONS arm refused, with the engine's category and sentence.
- `engine-label`: a proposal on a built board, labelled by the PROPOSE_ACTIONS checker itself with its category and
  sentence (`engine`, reading RulesProbeLabelTest's rows): no game, every category the engine names, and near-miss
  variants of each board. Its negatives' difficulty is the zone-reading floor's (`zone_floor`): subtle when it misses.
- target asks: a battlefield permanent the target list left out is not a legal target of that spell. An ask whose
  list an earlier choice of the same play narrowed (X announced, a second or "another" target, a sacrifice in the
  cost) is not mined: what it left out may be a legal target of the card.

Two harness behaviours make a menu absence the harness's, not the engine's, and are not mined: the menu lists one PLAY
LAND a land name, and a play that failed to pay is not offered again in its window (`failedInWindow`), so a card
chosen earlier in the same window is not an item there.

The state given to a model is the recorded prompt up to its DECISION line, with the hand's legality tags removed
("(castable)", "(need 1 more R)", ...): those tags are the engine's answer; the seat's own plan and its last play's
note are removed too (they can name the answer). eval/research/2026-11-05-rules-probe.md says
why and what the set measures.

Usage:
  python3 eval/rules_probe.py mine <run dir> [...] --out <items>.jsonl [--forge-home DIR]
  python3 eval/rules_probe.py engine <labeller rows>.jsonl[.gz] [...] --out <items>.jsonl[.gz]
  python3 eval/rules_probe.py stats <items>.jsonl [--floor] [--json]
  python3 eval/rules_probe.py balance <items>.jsonl --per-cell N --seed S [--contrast] --out <set>.jsonl
  python3 eval/rules_probe.py ask <set>.jsonl --model claude-cli:<model> --out <answers>.jsonl   (CLAUDE_CLI_SEAT=1)
  python3 eval/rules_probe.py score <set>.jsonl <answers>.jsonl [--reproduced-by <items>.jsonl] [--json]

Stdlib only. `ask` is the Claude CLI development lane only (`claude-cli:` spec of a listed model, CLAUDE_CLI_SEAT=1,
never a benchmark score); its child gets an allow-listed environment, so no API key bills a paid API.
"""

import argparse
import gzip
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import evalpath  # noqa: F401
import cost_report
from aggregate import wilson_ci

SCHEMA = "rules-probe-item-v1"
# The engine labeller's rows (RulesProbeLabelTest.SCHEMA; a test keeps the two equal).
ENGINE_RAW_SCHEMA = "rules-probe-engine-raw-v1"
# The engine's rejection categories (LlmSpikeGui.proposalRejection), the answer vocabulary of `ask`.
CATEGORIES = ("timing", "cost", "zone", "target", "land drop", "control", "type", "restriction")
REPO = Path(__file__).resolve().parents[1]

HAND_TAG = re.compile(r" \((castable[^)]*|payable, not castable now|need [^)]*|not castable now)\)")
HAND_ENTRY = re.compile(r"(.*?) ?@(\d+)(?: \(([^)]*)\))?(?:, |$)")
NAME_COST_PT = re.compile(r"^(?P<name>.+?)(?: (?P<cost>[0-9X]*(?:\{[^}]+\})+|\d+))?(?: (?P<pt>[-*+\d]+/[-*+\d]+))?$")
HEADER = re.compile(r"^== Turn (\d+) (\S+) \(active: (.*?) \| priority: (\w+)\) ==", re.M)
OPTION = re.compile(r"^(CAST|PLAY LAND|ACTIVATE|PLAY)\b.*?@(\d+)")
BOARD_ENTRY = re.compile(r"(.*?) @(\d+)(?:, |$)")
# An earlier choice of the same play narrows the list: X announced, a second or "another" target, a sacrifice.
DEPENDENT_TARGETS = re.compile(r"you announced|another target|target\b.*\btarget\b|[Ss]acrifice", re.S)
KEYWORDS_THAT_STOP_TARGETING = ("hexproof", "shroud", "protection", "ward")


# ---------------------------------------------------------------------------------------------------- parsing

def parse_hand(prompt):
    """The seat's hand line: [{name, cost, pt, handle, tag}] in order; a land has no cost."""
    m = re.search(r"^  hand\(\d+\): (.*)$", prompt or "", re.M)
    if not m or m.group(1).startswith("(empty)"):
        return []
    out = []
    for e in HAND_ENTRY.finditer(re.sub(r" \(was @\d+\)", "", m.group(1))):   # an earlier handle, not a card
        if not e.group(1) and not e.group(2):
            continue
        nm = NAME_COST_PT.match(e.group(1).strip())
        if not nm:
            continue
        out.append({"name": nm.group("name"), "cost": nm.group("cost"), "pt": nm.group("pt"),
                    "handle": "@" + e.group(2), "tag": e.group(3)})
    return out


def mana_value(cost):
    """Mana value of a printed cost such as 1{G}{G/W/P}{W} (X counts 0); None for no cost."""
    if cost is None:
        return None
    mv = 0
    lead = re.match(r"^(\d+)", cost)
    if lead:
        mv += int(lead.group(1))
    for sym in re.findall(r"\{([^}]+)\}", cost):
        if sym.isdigit():
            mv += int(sym)
        elif sym != "X":
            mv += 1
    return mv


def _you_block(prompt):
    i = (prompt or "").find("\nYOU [")
    j = (prompt or "").find("\nOPP [", i + 1)
    return prompt[i:j if j > 0 else len(prompt)] if i >= 0 else ""


def parse_window(prompt):
    """Whose turn, the phase, the stack, the land drop and the seat's mana, as the state shows them."""
    h = HEADER.search(prompt or "")
    you = re.search(r"^YOU \[(.*?)\]", prompt or "", re.M)
    own = bool(h and you and h.group(3) == you.group(1))
    phase = h.group(2) if h else None
    stack = re.search(r"^STACK \(engine, top first\): (.*)$", prompt or "", re.M)
    stack_empty = bool(stack and stack.group(1).strip() == "(empty)")
    yb = _you_block(prompt)
    lp = re.search(r"lands played this turn (\d+)/(\d+)", yb)
    mana = re.search(r"up to (\d+) mana", yb)        # counts the floating pool too ({B:1} up to 10 with {B:9} floating)
    return {"own_turn": own, "phase": phase, "stack_empty": stack_empty,
            "lands_played": int(lp.group(1)) if lp else None, "land_max": int(lp.group(2)) if lp else None,
            "mana": int(mana.group(1)) if mana else 0,
            "sorcery_speed": own and phase in ("MAIN1", "MAIN2") and stack_empty}


def state_text(prompt):
    """What a model is given: the prompt up to its DECISION line, with the hand's legality tags removed."""
    p = prompt or ""
    cut = p.find("\nDECISION:")
    p = p[:cut] if cut >= 0 else p
    lines, skipping = [], False
    for ln in p.split("\n"):
        if ln.startswith("THE PLAY YOU CHOSE MOST RECENTLY"):
            skipping = True                      # its note runs to the blank line
        if skipping:
            skipping = ln.strip() != ""
            continue
        if ln.startswith("YOUR PLAN"):
            continue
        if ln.startswith("  hand("):
            ln = HAND_TAG.sub("", ln)
        lines.append(ln)
    return "\n".join(lines).rstrip() + "\n"


def board_permanents(state):
    """Every battlefield permanent with a handle on the board and lands lines: [{handle, name, keywords, side}]."""
    out, side = [], None
    for ln in state.split("\n"):
        ln = re.sub(r" ?\(was @\d+\)", "", ln)          # an earlier handle of the same object, not an object
        if ln.startswith("YOU ["):
            side = "yours"
        elif ln.startswith("OPP ["):
            side = "opponent's"
        elif ln.startswith("  board: ") and side:
            body = re.sub(r" \[(has attached|attached to)[^\]]*\]", "", ln[len("  board: "):])   # handles of others
            for e in BOARD_ENTRY.finditer(body):          # entries end at their handle; a name may hold a comma
                core = re.sub(r" \[[^\]]+\]", "", e.group(1)).strip()
                pt = re.search(r" [-*+\d]+/[-*+\d]+$", core)
                out.append({"handle": "@" + e.group(2), "name": core[:pt.start()] if pt else core,
                            "keywords": re.findall(r"\[([^\]]+)\]", e.group(1)), "side": side,
                            "kind": "creature" if pt else "other"})
        elif ln.startswith("  lands: ") and side:
            for grp in re.finditer(r"([^(),]+?) x\d+ \[[^\]]*?((?:@\d+[^@\]]*)+)\]", ln):
                for hh in re.findall(r"@(\d+)", grp.group(2)):
                    out.append({"handle": "@" + hh, "name": grp.group(1).strip(), "keywords": ["land"], "kind": "land",
                                "side": side})
    return out


def card_index(forge_home):
    """name -> {types, keywords, oracle} read from Forge's card scripts on demand; None without a checkout. The
    script is found by a file name guessed from the card name (lower case, punctuation dropped, spaces to "_"); a card
    whose script is named otherwise (a split or double-faced card) reads as unknown."""
    forge_home = os.path.expanduser(os.path.expandvars(forge_home or ""))   # FORGE_HOME may hold "$HOME/forge"
    root = Path(forge_home) / "forge-gui" / "res" / "cardsfolder"
    if not forge_home or not root.is_dir():
        return None
    cache = {}

    def look(name):
        if name in cache:
            return cache[name]
        fn = re.sub(r"[^a-z0-9 _-]", "", name.lower()).replace(" ", "_").replace("-", "_") + ".txt"
        f = root / fn[0] / fn
        info = None
        if f.is_file():
            info = {"types": "", "keywords": [], "oracle": ""}
            for ln in f.read_text(encoding="utf-8", errors="replace").split("\n"):
                if ln.startswith("Types:") and not info["types"]:
                    info["types"] = ln[6:].strip()
                elif ln.startswith("K:"):
                    info["keywords"].append(ln[2:].split(":")[0].strip())
                elif ln.startswith("Oracle:") and not info["oracle"]:
                    info["oracle"] = ln[7:].strip().replace("\\n", "\n")
                elif ln.startswith("ALTERNATE"):
                    break
        cache[name] = info
        return info
    return look


# ---------------------------------------------------------------------------------------------------- items

def _item(kind, state, proposal, label, prov, row_idx, **kw):
    key = json.dumps([state, proposal], sort_keys=True)
    it = {"schema": SCHEMA, "kind": kind, "state": state, "proposal": proposal, "label": label,
          "label_source": kw.pop("label_source"), "category": kw.pop("category", None),
          "category_source": kw.pop("category_source", None), "reason": kw.pop("reason", None),
          "difficulty": kw.pop("difficulty", None),
          "id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
          "provenance": dict(prov, row=row_idx)}
    it.update(kw)
    return it


def _infer_cast(card, w, info):
    """(category, difficulty) of a hand card the menu could not cast, in the engine's order of checks: timing (a
    non-instant outside a sorcery window), then cost, then the rest. `info` is the card script, when known."""
    tag = card.get("tag") or ""
    mv = mana_value(card.get("cost"))
    instant = None
    if info:
        instant = "Instant" in info["types"].split() or "Flash" in info["keywords"]
    if not w["sorcery_speed"] and instant is False:
        return "timing", "trivial"
    total_short = mv is not None and mv > w["mana"]
    if tag.startswith("need"):
        cat = "cost" if (w["sorcery_speed"] or instant) else "timing-or-cost"
        return cat, "trivial" if total_short else "subtle"
    if tag == "payable, not castable now":
        return ("restriction-or-target", "subtle") if (w["sorcery_speed"] or instant) else ("timing-or-restriction",
                                                                                              "subtle")
    return "unknown", "trivial" if total_short else None


def window_key(prompt):
    """The priority window a play row is in: its turn, phase and stack line (LlmSpikeGui's winKey, from the text)."""
    h = HEADER.search(prompt or "")
    stack = re.search(r"^STACK \(engine, top first\): (.*)$", prompt or "", re.M)
    return (h.group(1), h.group(2)) if h else None, stack.group(1) if stack else None


def chosen_handle(row):
    """The handle of the play a row chose, or None (a pass, or no choice recorded)."""
    opts, c = row.get("options") or [], row.get("choice")
    if not isinstance(c, int) or not 0 <= c < len(opts):
        return None
    m = OPTION.match(opts[c])
    return "@" + m.group(2) if m else None


def items_from_play_row(row, prov, idx, cards=None, chosen_in_window=()):
    prompt = row.get("prompt") or ""
    state = state_text(prompt)
    w = parse_window(prompt)
    hand = parse_hand(prompt)
    names = {h["handle"]: h["name"] for h in hand}
    out, seen = [], set()
    cast_ok, land_ok = set(), set()
    for o in row.get("options") or []:
        m = OPTION.match(o)
        if not m:
            continue
        verb = {"CAST": "cast", "PLAY LAND": "play_land", "ACTIVATE": "activate", "PLAY": "play"}[m.group(1)]
        h = "@" + m.group(2)
        if verb == "cast":
            cast_ok.add(h)
        elif verb == "play_land":
            land_ok.add(h)
        prop = {"verb": verb, "card": h, "name": names.get(h) or _option_name(o)}
        if (verb, h) in seen:
            continue
        seen.add((verb, h))
        out.append(_item("play", state, prop, "legal", prov, idx, label_source="menu", option=o,
                         mana_ability=verb == "activate" and bool(re.search(r"\[\{T\}: Add \{", o))))
    refused = set()
    for p in row.get("proposals") or []:
        a = p.get("action") or {}
        if p.get("verdict") != "illegal" or not a.get("card"):
            continue
        prop = {k: a[k] for k in ("verb", "card", "ability", "face", "cost", "targets") if a.get(k) is not None}
        prop["name"] = names.get(a["card"])
        key = json.dumps(prop, sort_keys=True)
        if key in refused:
            continue
        refused.add(key)
        seen.add((a.get("verb"), a["card"]))
        out.append(_item("play", state, prop, "illegal", prov, idx, label_source="engine-verdict",
                         category=p.get("category"), category_source="engine",
                         reason=p.get("error"), difficulty=None))
    look = cards or (lambda n: None)
    lands_ok_by_name = {names.get(h) for h in land_ok}       # the menu lists one PLAY LAND a land name
    for c in hand:
        h = c["handle"]
        info = look(c["name"])
        # A land shows no cost in the hand line; so does a zero-cost spell (Mishra's Bauble), told apart by its script.
        if c["cost"] is None and not (info and "Land" not in info["types"].split()):
            if h in land_ok or ("play_land", h) in seen or c["name"] in lands_ok_by_name or h in chosen_in_window:
                continue
            if not w["sorcery_speed"]:
                cat, diff = "timing", "trivial"
            elif w["lands_played"] is not None and w["land_max"] is not None and w["lands_played"] >= w["land_max"]:
                cat, diff = "land drop", "trivial"
            else:
                cat, diff = "restriction", "subtle"
            out.append(_item("play", state, {"verb": "play_land", "card": h, "name": c["name"]}, "illegal", prov, idx,
                             label_source="menu-absence", category=cat, category_source="inferred", difficulty=diff))
            continue
        if h in cast_ok or ("cast", h) in seen or (c.get("tag") or "").startswith("castable") or h in chosen_in_window:
            continue
        cat, diff = _infer_cast(c, w, info)
        out.append(_item("play", state, {"verb": "cast", "card": h, "name": c["name"]}, "illegal", prov, idx,
                         label_source="menu-absence", category=cat, category_source="inferred", difficulty=diff))
    return out


def _option_name(o):
    m = re.match(r"^(?:CAST|PLAY LAND|ACTIVATE|PLAY) (.*?)(?: [-*+\d]+/[-*+\d]+)? @\d+", o)
    return m.group(1) if m else None


def items_from_target_row(row, prov, idx):
    prompt = row.get("prompt") or ""
    m = re.search(r"DECISION: Choose the TARGET for (.*?) @(\d+)(.*?)(?: \(the spell/ability resolves|\n|$)", prompt)
    if not m:
        return []
    source = f"{m.group(1)} @{m.group(2)}"
    if DEPENDENT_TARGETS.search(m.group(3)):
        return []
    state = state_text(prompt)
    # An option's own handle: not an earlier one "(was @7)", nor one inside its brackets ("[has attached: X @22]").
    offered = {h for o in row.get("options") or []
               for h in re.findall(r"@\d+", re.sub(r"\(was @\d+\)|\[[^\]]*\]", "", o))}
    out = []
    perms = board_permanents(state)
    kinds_offered = {p["kind"] for p in perms if p["handle"] in offered}
    for p in perms:
        legal = p["handle"] in offered
        kw = [k for k in p["keywords"] if any(w in k.lower() for w in KEYWORDS_THAT_STOP_TARGETING)]
        # Trivial: nothing of its kind (land, creature, other) was a legal target (a "target player" trigger, an
        # Equipment for "target creature"): the text's type rules it out. Subtle: another permanent of its kind was
        # legal and this one was not (hexproof, "you don't control", "nonblack", a power limit).
        diff = None if legal else "subtle" if p["kind"] in kinds_offered else "trivial"
        prop = {"verb": "target", "source": source, "source_text": m.group(3).strip(), "card": p["handle"],
                "name": p["name"]}
        out.append(_item("target", state, prop,
                         "legal" if legal else "illegal", prov, idx, label_source="menu" if legal else "menu-absence",
                         category=None if legal else "target", category_source=None if legal else "engine",
                         difficulty=diff, keyword_visible=bool(kw)))
    return out


def items_from_row(row, prov, idx, cards=None, chosen_in_window=()):
    if not isinstance(row, dict) or row.get("t") != "decision":
        return []
    if row.get("type") == "play":
        return items_from_play_row(row, prov, idx, cards, chosen_in_window)
    if row.get("type") == "target":
        return items_from_target_row(row, prov, idx)
    return []


def mine_rows(rows, prov, cards=None):
    """The items of one game's rows [(index, row)], in order: a card chosen earlier in a window is not an absence item
    later in that window (a play that failed to pay is not offered again there)."""
    out, window, chosen = [], None, set()
    for k, r in rows:
        if isinstance(r, dict) and r.get("t") == "decision" and r.get("type") == "play":
            w = window_key(r.get("prompt"))
            if w != window:
                window, chosen = w, set()
            out.extend(items_from_row(r, prov, k, cards, frozenset(chosen)))
            h = chosen_handle(r)
            if h:
                chosen.add(h)
        else:
            out.extend(items_from_row(r, prov, k, cards))
    return out


# ---------------------------------------------------------------------------------------------------- engine labels

ZONE_LINES = (("  hand(", "hand"), ("  board:", "battlefield"), ("  lands:", "battlefield"),
              ("  graveyard(", "graveyard"), ("  exile(", "exile"), ("  command:", "command"))


def zones(state):
    """handle -> (side, zone) for every object the state shows, and handle -> the number of abilities YOUR ACTIVATED
    ABILITIES lists for it. Side is "you", "opp" or None (the stack)."""
    where, abilities, side, in_abilities = {}, {}, None, False
    for ln in state.split("\n"):
        ln = re.sub(r" ?\(was @\d+\)", "", ln)
        if ln.startswith("YOU ["):
            side, in_abilities = "you", False
            continue
        if ln.startswith("OPP ["):
            side, in_abilities = "opp", False
            continue
        if ln.startswith("STACK"):
            for h in re.findall(r"@(\d+)(?!\d)", ln):
                where.setdefault("@" + h, (None, "stack"))
            side = None
            continue
        if ln.startswith("  YOUR ACTIVATED ABILITIES"):
            in_abilities = True
            continue
        if in_abilities and ln.startswith("    "):
            m = re.search(r"@(\d+): (.*)$", ln)
            if m:
                abilities["@" + m.group(1)] = len(re.findall(r"(?:^| )\d+\) ", m.group(2)))
            continue
        in_abilities = False
        zone = next((z for prefix, z in ZONE_LINES if ln.startswith(prefix)), None)
        if zone and side:
            for h in re.findall(r"@(\d+)(?!\d)", ln):
                where.setdefault("@" + h, (side, zone))
    return where, abilities


def zone_floor(item):
    """A second floor for the labeller's items, which name every visible card with every verb: the state-reading floor
    (`heuristic`) plus where the card is. A land is played from your hand (a hand card with no cost); a card on the
    battlefield or the stack, an opponent's card, or a card outside your hand with no way to cast named is not cast;
    only a card YOUR ACTIVATED ABILITIES lists, or your land, is activated. It knows no card text."""
    p, st = item["proposal"], item["state"]
    if p.get("verb") == "target":
        return heuristic(item)
    lands = {b["handle"] for b in board_permanents(st) if b["kind"] == "land"}
    if any(t in lands for t in p.get("targets") or []):
        return {"legal": False}
    where, abilities = zones(st)
    side, zone = where.get(p.get("card"), (None, None))
    hand = next((h for h in parse_hand(st) if h["handle"] == p.get("card")), None)
    if p.get("verb") == "play_land":
        if side != "you" or zone != "hand" or (hand and hand.get("cost")):
            return {"legal": False}
        return heuristic(item)
    if p.get("verb") == "cast":
        if side != "you" or zone in ("battlefield", "stack"):
            return {"legal": False}
        if zone != "hand":
            return {"legal": bool(p.get("cost"))}
        if hand and hand.get("cost") is None and not hand.get("pt"):
            return {"legal": False}
        return heuristic(item)
    if p.get("verb") == "activate":
        if side != "you":
            return {"legal": False}
        return {"legal": p.get("card") in abilities or p.get("card") in lands}
    return heuristic(item)


def _open(path, mode="r"):
    return gzip.open(path, mode + "t", encoding="utf-8") if str(path).endswith(".gz") else \
        open(path, mode, encoding="utf-8")


def read_raw(path):
    with _open(path) as fh:
        return [json.loads(ln) for ln in fh if ln.startswith("{")]


def engine_items(rows):
    """Probe items from the engine labeller's rows (RulesProbeLabelTest): the state row each names, the proposal, the
    checker's label, category and sentence. A FORMAT verdict (how a proposal is written, not a rule) or a row whose
    state is missing is no item. A negative is "trivial" when the zone-reading floor calls it illegal and "subtle"
    when it does not: the difficulty is defined by that floor, so the floor is right on no subtle negative by
    construction."""
    states = {r["id"]: r for r in rows if r.get("t") == "state"}
    out = []
    for r in rows:
        if r.get("t") == "state" or r.get("label") not in ("legal", "illegal"):
            continue
        st = states.get(r.get("state_id"))
        if st is None:
            continue
        p = r["proposal"]
        prov = {"board": r.get("board"), "variant": r.get("variant"), "harness_epoch": st.get("harness_epoch"),
                "observation_version": st.get("observation_version"), "engine_pin": st.get("engine_pin"),
                "engine_pin_source": "labeller", "source_run": "rules-probe-label", "puzzle": r.get("board")}
        illegal = r["label"] == "illegal"
        it = _item("target" if p.get("targets") else "play", st["text"], p, r["label"], prov, None,
                   label_source="engine-label", category=r.get("category") if illegal else None,
                   category_source="engine" if illegal else None, reason=r.get("reason") if illegal else None,
                   mana_ability=bool(r.get("mana_ability")), signature=r.get("signature"))
        if illegal:
            it["difficulty"] = "subtle" if zone_floor(it)["legal"] else "trivial"
        out.append(it)
    return dedupe(out)


def contrast_pairs(items):
    """(legal, illegal) pairs of one play (`signature`) on a board and one of its variants, with the two labels: the
    edit that made the variant changed the engine's answer. Either side may be the base board's."""
    base = {}
    for i in items:
        if i["provenance"].get("variant") == "base" and i.get("signature"):
            base.setdefault((i["provenance"].get("board"), i["signature"]), i)
    pairs = []
    for i in sorted(items, key=lambda x: x["id"]):
        v = i["provenance"].get("variant")
        b = base.get((i["provenance"].get("board"), i.get("signature")))
        if v in (None, "base") or b is None or b["label"] == i["label"]:
            continue
        pairs.append((b, i) if b["label"] == "legal" else (i, b))
    return pairs


def dedupe(items):
    out, seen = [], set()
    for i in items:
        if i["id"] not in seen:
            seen.add(i["id"])
            out.append(i)
    return out


def _repo_pin():
    try:
        return (REPO / "FORGE_COMMIT").read_text().strip()
    except OSError:
        return "unknown"


def _game_files(paths):
    for p in paths:
        p = Path(p)
        yield from (sorted(p.rglob("game-*.jsonl")) if p.is_dir() else [p])


def _provenance(f, rows, root, repo_pin):
    game = next((r for r in rows if r.get("t") == "game"), {})
    puzzle = {}
    pr = f.parent / "puzzle-results.jsonl"
    if pr.is_file():
        for ln in pr.read_text(encoding="utf-8", errors="replace").split("\n"):
            if ln.startswith("{"):
                puzzle = json.loads(ln)
                break
    build = {}
    if (f.parent / "build.json").is_file():
        build = json.loads((f.parent / "build.json").read_text())
    sha = game.get("engine_sha")
    known = sha and sha != "unknown"
    return {"source_run": str(root), "source_file": str(f.relative_to(root)) if root in f.parents else str(f),
            "harness_epoch": game.get("harness_epoch") or puzzle.get("harness_epoch") or "unknown",
            "observation_version": game.get("observation_version") or "unknown",
            "overlay_sha": (game.get("overlay_sha") if game.get("overlay_sha") not in (None, "unknown")
                            else build.get("build_overlay_sha", "unknown")),
            "engine_pin": sha if known else repo_pin,
            "engine_pin_source": "game-line" if known else "repo-FORGE_COMMIT-at-mining",
            "propose_actions": bool(game.get("propose_actions") or puzzle.get("propose_actions")),
            "puzzle": puzzle.get("id")}


def mine(paths, repo_pin=None, cards=None):
    """Every item of every play and target row under the given run directories, deduplicated."""
    pin = repo_pin or _repo_pin()
    out = []
    for p in paths:
        root = Path(p).resolve()
        for f in _game_files([root]):
            rows = []
            with open(f, encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    if ln.startswith("{"):
                        try:
                            rows.append(json.loads(ln))
                        except ValueError:
                            pass
            prov = _provenance(f.resolve(), rows, root, pin)
            out.extend(mine_rows(list(enumerate(rows)), prov, cards))
    return dedupe(out)


def position_pool(items, keep_mana_abilities=False):
    """One item per position (`position_key`), the first by id, mana abilities left out unless kept: the pool `balance`
    draws from and the floor is measured on."""
    pool, seen = [], set()
    for i in sorted(items, key=lambda i: i["id"]):
        k = position_key(i)
        if (keep_mana_abilities or not i.get("mana_ability")) and k not in seen:
            seen.add(k)
            pool.append(i)
    return pool


def reproduced(pilot, items):
    """The pilot items `items` (a later mine) reproduce: the same file, row, verb and handle with the same label, and a
    name without a handle in it (the first miner glued a hand entry's "(was @N)" tail into a name). The pilot's own
    item (its state as the pilot asked it) is returned."""
    def key(i):
        p = i["provenance"]
        return p.get("source_file"), p.get("row"), i["proposal"].get("verb"), i["proposal"].get("card")
    now = {key(i): i for i in items}
    return [i for i in pilot if key(i) in now and now[key(i)]["label"] == i["label"]
            and "@" not in (i["proposal"].get("name") or "")]


def balance(items, per_cell, seed, keep_mana_abilities=False, contrast=False):
    """One item per position (`position_key`), then at most `per_cell` per (kind, illegal category, difficulty), and
    as many legal items as illegal ones. With `contrast` (engine-labelled items), only negatives with a contrast
    partner (`contrast_pairs`) are drawn, and each brings its partner as its legal item."""
    rng = random.Random(seed)
    pool = position_pool(items, keep_mana_abilities)
    partner = {}
    if contrast:
        in_pool = {i["id"] for i in pool}
        for legal, illegal in contrast_pairs(items):
            if legal["id"] in in_pool and illegal["id"] in in_pool:
                partner.setdefault(illegal["id"], legal)
        pool = [i for i in pool if i["label"] == "legal" or i["id"] in partner]
    cells = {}
    for i in pool:
        if i["label"] == "illegal":
            cells.setdefault((i.get("kind"), i.get("category"), i.get("difficulty")), []).append(i)
    chosen = []
    for key in sorted(cells, key=lambda k: tuple(str(x) for x in k)):
        c = sorted(cells[key], key=lambda i: i["id"])
        rng.shuffle(c)
        chosen.extend(c[:per_cell])
    if contrast:
        legal = {partner[i["id"]]["id"]: partner[i["id"]] for i in chosen}
        return sorted(chosen + list(legal.values()), key=lambda i: i["id"])
    legal = sorted((i for i in pool if i["label"] == "legal"), key=lambda i: i["id"])
    rng.shuffle(legal)
    chosen.extend(legal[:len(chosen)])
    return sorted(chosen, key=lambda i: i["id"])


# ---------------------------------------------------------------------------------------------------- stats

POSITION_LINES = re.compile(r"^(== Turn|YOU \[|OPP \[|  hand\(|  board:|  lands:|  graveyard|  exile|STACK|COMBAT)")


def position_key(item):
    """The item without what does not change its rules question: handles, the plan, the history and the game log.
    Two attempts at one puzzle reach the same position with other handles and other notes."""
    lines = [re.sub(r" ?@\d+(\(was @\d+\))?", "", ln) for ln in item["state"].split("\n") if POSITION_LINES.match(ln)]
    p = dict(item["proposal"])
    p.pop("card", None)
    if "target_names" in p:            # an engine item's target by name: its handle differs between variants
        p["targets"] = p.pop("target_names")
    if p.get("source"):
        p["source"] = re.sub(r" @\d+", "", p["source"])
    return hashlib.sha256(json.dumps([lines, p], sort_keys=True).encode()).hexdigest()


def floor_report(items):
    """The state-reading floor over `position_pool` (balance's positions): its balanced accuracy, and per stratum
    (label, kind, difficulty) how many it gets right; the zone-reading floor (`zone_floor`) beside it."""
    pos = position_pool(items)
    strata = {}
    for i in pos:
        key = "%s %s %s" % (i["label"], i.get("kind"), i.get("difficulty") if i["label"] == "illegal" else "-")
        cell = strata.setdefault(key, {"n": 0, "floor_correct": 0, "zone_floor_correct": 0})
        cell["n"] += 1
        cell["floor_correct"] += heuristic(i)["legal"] is (i["label"] == "legal")
        cell["zone_floor_correct"] += zone_floor(i)["legal"] is (i["label"] == "legal")
    per, bal = _tally(pos, {i["id"]: heuristic(i) for i in pos})
    _, zbal = _tally(pos, {i["id"]: zone_floor(i) for i in pos})
    return {"positions": len(pos), "balanced_accuracy": round(bal, 4) if bal is not None else None,
            "recall_legal": round(per["legal"][0] / per["legal"][1], 4) if per["legal"][1] else None,
            "recall_illegal": round(per["illegal"][0] / per["illegal"][1], 4) if per["illegal"][1] else None,
            "zone_reading_balanced": round(zbal, 4) if zbal is not None else None}, \
        dict(sorted(strata.items()))


def stats(items, floor=False):
    def count(f, sub):
        return dict(Counter(f(i) for i in sub).most_common())
    illegal = [i for i in items if i["label"] == "illegal"]
    illegal_pos = [i for i in position_pool(items, keep_mana_abilities=True) if i["label"] == "illegal"]
    states = {hashlib.sha256(i["state"].encode()).hexdigest() for i in items}
    out = {"items": len(items), "states": len(states),
            "positions": len({position_key(i) for i in items}),
            "illegal_positions": len({position_key(i) for i in illegal}),
            "illegal_card_names": len({i["proposal"].get("name") for i in illegal}),
            "by_label": count(lambda i: i["label"], items),
            "by_kind_label": count(lambda i: f"{i.get('kind')}/{i['label']}", items),
            "by_label_source": count(lambda i: f"{i['label']}/{i['label_source']}", items),
            "legal_mana_abilities": sum(1 for i in items if i.get("mana_ability")),
            "illegal_by_category": count(lambda i: f"{i.get('category')} ({i.get('category_source')})", illegal),
            "illegal_by_difficulty": count(lambda i: str(i.get("difficulty")), illegal),
            "targets_keyword_visible": sum(1 for i in illegal
                                           if i.get("kind") == "target" and i.get("keyword_visible")),
            "by_epoch": count(lambda i: i["provenance"].get("harness_epoch"), items),
            "by_engine_pin_source": count(lambda i: i["provenance"].get("engine_pin_source"), items),
            "illegal_positions_by_cell": count(
                lambda i: "%s / %s (%s) / %s" % (i.get("kind"), i.get("category"), i.get("category_source"),
                                                 i.get("difficulty")),
                illegal_pos),
            "by_variant_label": count(lambda i: "%s/%s" % (i["provenance"].get("variant"), i["label"]),
                                      [i for i in items if i.get("label_source") == "engine-label"]),
            "contrast_pairs_by_variant": count(
                lambda pr: next(x["provenance"].get("variant") for x in pr if x["provenance"].get("variant") != "base"),
                contrast_pairs([i for i in items if i.get("label_source") == "engine-label"])),
            "subtle_positions_by_source": count(
                lambda i: i["provenance"].get("puzzle") or Path(i["provenance"].get("source_run", "")).name,
                [i for i in illegal_pos if i.get("difficulty") == "subtle"])}
    if floor:
        out["floor"], out["floor_by_stratum"] = floor_report(items)
    return out


# ---------------------------------------------------------------------------------------------------- asking

QUESTION = """You are checking a play in a game of Magic: The Gathering against the Comprehensive Rules.

{card}STATE (the player's view; @N are handles):
{state}
PROPOSED PLAY: {play}

Is this play legal right now? If it is not, which kind of rule does it break: one of {cats}.
Answer with one JSON object and nothing else: {{"legal": true}} or {{"legal": false, "category": "<one of the kinds>"}}.
"""


def render(item, cards=None):
    p = item["proposal"]
    nm = p.get("name") or ""
    if p.get("verb") == "activate" and p.get("ability") is not None:
        play = f"activate ability {p['ability']} (as numbered in YOUR ACTIVATED ABILITIES) of {nm} {p.get('card')}"
    elif p.get("verb") == "target":
        play = f"choose {nm} {p['card']} as the target of {p.get('source')} {p.get('source_text') or ''}".rstrip()
    else:
        play = {"cast": "cast", "play_land": "play the land", "activate": "activate an ability of",
                "play": "play"}.get(p.get("verb"), p.get("verb")) + f" {nm} {p.get('card')}".replace("  ", " ")
        if p.get("face"):
            play += f" (face: {p['face']})"
        if p.get("cost"):
            play += f" (cast way: {p['cost']})"
    if p.get("targets"):
        named = p.get("target_names") or [None] * len(p["targets"])
        play += " targeting " + ", ".join(t if t in ("you", "opponent") or not n else f"{n} {t}"
                                          for t, n in zip(p["targets"], named))
    card = ""
    info = cards(nm) if cards and nm else None
    if info:
        card = f"CARD: {nm} - {info['types']}. {info['oracle']}\n\n"
    return QUESTION.format(card=card, state=item["state"], play=play, cats=", ".join(CATEGORIES))


# The models `ask` may run (CLAUDE.md: never Opus; the CLI lane is development only).
CLI_MODELS = ("claude-haiku-5-5", "haiku", "claude-sonnet-5-5", "sonnet")
# What the CLI child inherits, by name (ClaudeCliBridge.KEEP_ENV): how it runs, reaches the network and logs in to the
# subscription. An API key, a cloud provider's switch and its credentials only with CLAUDE_CLI_ALLOW_API_KEY=1.
KEEP_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "TZ", "TMPDIR",
            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "no_proxy", "all_proxy",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "NODE_OPTIONS",
            "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR")
API_KEY_OPT_IN = "CLAUDE_CLI_ALLOW_API_KEY"
API_KEY_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
PROVIDER_ENV = {
    "CLAUDE_CODE_USE_BEDROCK": ("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_CONFIG_FILE",
                                "AWS_SHARED_CREDENTIALS_FILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                                "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"),
    "CLAUDE_CODE_USE_VERTEX": ("GOOGLE_APPLICATION_CREDENTIALS", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID",
                               "VERTEX_REGION_"),
    "CLAUDE_CODE_USE_FOUNDRY": ("ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE",
                                "ANTHROPIC_FOUNDRY_BASE_URL"),
}


def child_env(parent):
    """The CLI child's environment: the allow-listed part of `parent` and its LC_* locale; API keys and a provider's
    switch with its credentials only when the operator sets CLAUDE_CLI_ALLOW_API_KEY=1. Everything else, among it
    MAX_THINKING_TOKENS, ANTHROPIC_MODEL, the launching session's CLAUDE* and the OPENROUTER* variables, is dropped.
    The lists are ClaudeCliBridge's (a test keeps them equal); the probe is deliberately stricter on a provider's
    switch, which the bridge keeps whenever it is set and this keeps only with CLAUDE_CLI_ALLOW_API_KEY=1."""
    keep, prefixes = set(KEEP_ENV), ["LC_"]
    if parent.get(API_KEY_OPT_IN) == "1":
        keep.update(API_KEY_ENV)
        for switch, creds in PROVIDER_ENV.items():
            if parent.get(switch, "").strip().lower() in ("1", "true"):
                keep.add(switch)
                keep.update(c for c in creds if not c.endswith("_"))
                prefixes.extend(c for c in creds if c.endswith("_"))
    return {k: v for k, v in parent.items() if k in keep or any(k.startswith(x) for x in prefixes)}


def check_cli_lane(spec, env=None):
    """The model of a `claude-cli:<model>` spec `ask` may run, or exit saying why not: the operator's opt-in
    (cost_report.CLI_OPT_IN), the spec's form and a listed model."""
    env = os.environ if env is None else env
    why = cost_report.cli_opt_in_refusal([spec], env)
    if why is None and not cost_report.is_cli(spec):
        why = "%s is not the Claude CLI lane: give %s<model>" % (spec, cost_report.CLI_PREFIX)
    model = str(spec)[len(cost_report.CLI_PREFIX):]
    if why is None and model not in CLI_MODELS:
        why = "%s is not a model rules_probe asks (%s)" % (model, ", ".join(CLI_MODELS))
    if why:
        sys.exit("rules_probe ask: " + why)
    return model


def parse_reply(text):
    """The answer object from a reply: {"legal": bool, "category"?: str}, or None."""
    if not isinstance(text, str):
        return None
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            o = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(o, dict) and isinstance(o.get("legal"), bool):
            out = {"legal": o["legal"]}
            if isinstance(o.get("category"), str):
                out["category"] = o["category"].strip().lower()
            return out
    return None


def ask(items, spec, out, cards=None, timeout=180, environ=None):
    environ = os.environ if environ is None else environ
    model = check_cli_lane(spec, environ)
    child = child_env(environ)
    done = set()
    if Path(out).is_file():
        done = {json.loads(ln)["id"] for ln in open(out) if ln.startswith("{")}
    with open(out, "a", encoding="utf-8") as fh:
        for it in items:
            if it["id"] in done:
                continue
            argv = ["claude", "-p", "--model", model, "--output-format", "json", "--tools", "",
                    "--strict-mcp-config", "--no-session-persistence", "--setting-sources", "",
                    "--disable-slash-commands", "--system-prompt", "You judge plays in Magic: The Gathering."]
            try:
                r = subprocess.run(argv, input=render(it, cards), capture_output=True, text=True, timeout=timeout,
                                   env=child)
                envelope = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
                text = envelope.get("result", r.stdout)
                cost = envelope.get("total_cost_usd")
            except (subprocess.TimeoutExpired, ValueError) as e:
                text, cost = f"ERROR {e}", None
            fh.write(json.dumps({"id": it["id"], "model": spec, "reply": text, "answer": parse_reply(text),
                                 "cost_usd": cost}) + "\n")
            fh.flush()


# ---------------------------------------------------------------------------------------------------- scoring

def heuristic(item):
    """The state-reading floor: what one line of the state and the card's printed cost say, with no rule beyond
    "a land a turn, in your main phase", "mana value at most the mana you have" and "a creature at sorcery speed"
    (a P/T in the hand line; flash unseen). A target is called illegal only when it is a land."""
    p, st = item["proposal"], item["state"]
    if p.get("verb") == "target":
        lands = {b["handle"] for b in board_permanents(st) if b["kind"] == "land"}
        return {"legal": p.get("card") not in lands}
    w = parse_window(st)
    card = next((h for h in parse_hand(st) if h["handle"] == p.get("card")), None)
    if p.get("verb") == "play_land":
        used = w["lands_played"] is not None and w["land_max"] is not None and w["lands_played"] >= w["land_max"]
        return {"legal": w["sorcery_speed"] and not used}
    if p.get("verb") == "cast" and card:
        mv = mana_value(card.get("cost"))
        if mv is not None and mv > w["mana"]:
            return {"legal": False}
        if card.get("pt") and not w["sorcery_speed"]:
            return {"legal": False}
    return {"legal": True}


def _tally(items, answers):
    per = {"legal": [0, 0], "illegal": [0, 0]}
    for i in items:
        a = answers.get(i["id"])
        per[i["label"]][0] += a is not None and a.get("legal") is (i["label"] == "legal")
        per[i["label"]][1] += 1
    recalls = [r / t for r, t in per.values() if t]
    return per, (sum(recalls) / len(recalls) if recalls else None)


def score(items, answers):
    """Accuracy and balanced accuracy of `answers` (id -> {legal, category?}); a missing answer is wrong. Floors:
    always-legal (balanced accuracy 0.5) and the state-reading heuristic on the same items. The category is scored on
    illegal items with an engine category (never an inferred one) that the answer called illegal."""
    n = len(items)
    per, bal = _tally(items, answers)
    right = per["legal"][0] + per["illegal"][0]
    cat = [a.get("category") == i.get("category") for i in items
           for a in [answers.get(i["id"])]
           if i["label"] == "illegal" and i.get("category_source") == "engine" and a and a.get("legal") is False]
    hper, hbal = _tally(items, {i["id"]: heuristic(i) for i in items})
    zper, zbal = _tally(items, {i["id"]: zone_floor(i) for i in items})
    legal_share = per["legal"][1] / n if n else 0.0
    return {"n": n, "missing": sum(1 for i in items if answers.get(i["id"]) is None),
            "accuracy": round(right / n, 4) if n else None, "accuracy_ci": [round(x, 4) for x in wilson_ci(right, n)],
            "balanced_accuracy": round(bal, 4) if bal is not None else None,
            "recall_legal": round(per["legal"][0] / per["legal"][1], 4) if per["legal"][1] else None,
            "recall_illegal": round(per["illegal"][0] / per["illegal"][1], 4) if per["illegal"][1] else None,
            "floors": {"always_legal_accuracy": round(legal_share, 4), "always_legal_balanced": 0.5,
                       "majority_accuracy": round(max(legal_share, 1 - legal_share), 4),
                       "state_reading_balanced": round(hbal, 4) if hbal is not None else None,
                       "state_reading_accuracy": round((hper["legal"][0] + hper["illegal"][0]) / n, 4) if n else None,
                       "zone_reading_balanced": round(zbal, 4) if zbal is not None else None,
                       "zone_reading_accuracy": round((zper["legal"][0] + zper["illegal"][0]) / n, 4) if n else None},
            "category_n": len(cat), "category_accuracy": round(sum(cat) / len(cat), 4) if cat else None}


# ---------------------------------------------------------------------------------------------------- CLI

def _read(path):
    """Items from a file `_write` wrote: each state is stored once, as a {"t": "state"} row the items name."""
    states, items = {}, []
    with _open(path) as fh:
        for ln in fh:
            if not ln.startswith("{"):
                continue
            r = json.loads(ln)
            if r.get("t") == "state":
                states[r["id"]] = r["text"]
            elif "state_id" in r:
                r["state"] = states[r.pop("state_id")]
                items.append(r)
            else:
                items.append(r)
    return items


def _write(items, out):
    written = set()
    with _open(out, "w") as fh:
        for i in items:
            sid = hashlib.sha256(i["state"].encode("utf-8")).hexdigest()[:16]
            if sid not in written:
                written.add(sid)
                fh.write(json.dumps({"t": "state", "id": sid, "text": i["state"]}) + "\n")
            row = {k: v for k, v in i.items() if k != "state"}
            row["state_id"] = sid
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mine")
    m.add_argument("runs", nargs="+")
    m.add_argument("--out", required=True)
    m.add_argument("--forge-home", default=os.environ.get("FORGE_HOME", "/root/forge"))
    s = sub.add_parser("stats")
    s.add_argument("items")
    s.add_argument("--json", action="store_true")
    s.add_argument("--floor", action="store_true", help="the state-reading floor per stratum, over positions")
    b = sub.add_parser("balance")
    b.add_argument("items")
    b.add_argument("--per-cell", type=int, required=True)
    b.add_argument("--seed", type=int, required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--contrast", action="store_true", help="engine items: negatives with a contrast partner, each "
                   "with its partner as the legal item")
    e = sub.add_parser("engine", help="items from the engine labeller's rows (RulesProbeLabelTest)")
    e.add_argument("raw", nargs="+")
    e.add_argument("--out", required=True)
    a = sub.add_parser("ask")
    a.add_argument("items")
    a.add_argument("--model", required=True, help="claude-cli:<model>, a model of CLI_MODELS")
    a.add_argument("--out", required=True)
    a.add_argument("--forge-home", default=os.environ.get("FORGE_HOME", "/root/forge"))
    c = sub.add_parser("score")
    c.add_argument("items")
    c.add_argument("answers")
    c.add_argument("--json", action="store_true")
    c.add_argument("--reproduced-by", metavar="ITEMS", help="score only the items a later mine reproduces")
    args = ap.parse_args(argv)
    if args.cmd == "mine":
        items = mine(args.runs, cards=card_index(args.forge_home))
        _write(items, args.out)
        print(f"{len(items)} items -> {args.out}")
    elif args.cmd == "stats":
        print(json.dumps(stats(_read(args.items), floor=args.floor), indent=None if args.json else 1))
    elif args.cmd == "balance":
        items = balance(_read(args.items), args.per_cell, args.seed, contrast=args.contrast)
        _write(items, args.out)
        print(f"{len(items)} items -> {args.out}")
    elif args.cmd == "engine":
        items = dedupe([i for f in args.raw for i in engine_items(read_raw(f))])
        _write(items, args.out)
        print(f"{len(items)} items -> {args.out}")
    elif args.cmd == "ask":
        ask(_read(args.items), args.model, args.out, card_index(args.forge_home))
    elif args.cmd == "score":
        ans = {r["id"]: r["answer"] for r in _read(args.answers) if r.get("answer") is not None}
        items = _read(args.items)
        if args.reproduced_by:
            items = reproduced(items, _read(args.reproduced_by))
        print(json.dumps(score(items, ans), indent=None if args.json else 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
