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
- target asks: a battlefield permanent the target list left out is not a legal target of that spell.

The state given to a model is the recorded prompt up to its DECISION line, with the hand's legality tags removed
("(castable)", "(need 1 more R)", ...): those tags are the engine's answer. eval/research/2026-11-05-rules-probe.md says
why and what the set measures.

Usage:
  python3 eval/rules_probe.py mine <run dir> [...] --out <items>.jsonl [--forge-home DIR]
  python3 eval/rules_probe.py stats <items>.jsonl [--json]
  python3 eval/rules_probe.py balance <items>.jsonl --per-cell N --seed S --out <set>.jsonl
  python3 eval/rules_probe.py ask <set>.jsonl --model haiku --out <answers>.jsonl   (CLAUDE_CLI_SEAT=1; development)
  python3 eval/rules_probe.py score <set>.jsonl <answers>.jsonl [--json]

Stdlib only. `ask` is the Claude CLI development lane only (CLAUDE_CLI_SEAT=1, never Opus, never a benchmark score).
"""

import argparse
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
from aggregate import wilson_ci

SCHEMA = "rules-probe-item-v1"
# The engine's rejection categories (LlmSpikeGui.proposalRejection), the answer vocabulary of `ask`.
CATEGORIES = ("timing", "cost", "zone", "target", "land drop", "control", "type", "restriction")
REPO = Path(__file__).resolve().parents[1]

HAND_TAG = re.compile(r" \((castable[^)]*|payable, not castable now|need [^)]*|not castable now)\)")
HAND_ENTRY = re.compile(r"(.*?) ?@(\d+)(?: \(([^)]*)\))?(?:, |$)")
NAME_COST_PT = re.compile(r"^(?P<name>.+?)(?: (?P<cost>[0-9X]*(?:\{[^}]+\})+|\d+))?(?: (?P<pt>[-*+\d]+/[-*+\d]+))?$")
HEADER = re.compile(r"^== Turn (\d+) (\S+) \(active: (.*?) \| priority: (\w+)\) ==", re.M)
OPTION = re.compile(r"^(CAST|PLAY LAND|ACTIVATE|PLAY)\b.*?@(\d+)")
BOARD_ENTRY = re.compile(r"(.*?) @(\d+)(?:, |$)")
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
    lines = []
    for ln in p.split("\n"):
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
    """name -> {types, keywords, oracle} read from Forge's card scripts on demand; None without a checkout."""
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


def items_from_play_row(row, prov, idx, cards=None):
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
        (cast_ok if verb == "cast" else land_ok if verb == "play_land" else set()).add(h)
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
                         category=p.get("category") or _category_of(p.get("error")), category_source="engine",
                         reason=p.get("error"), difficulty=None))
    look = cards or (lambda n: None)
    lands_ok_by_name = {names.get(h) for h in land_ok}       # the menu lists one PLAY LAND a land name
    for c in hand:
        h = c["handle"]
        info = look(c["name"])
        # A land shows no cost in the hand line; so does a zero-cost spell (Mishra's Bauble), told apart by its script.
        if c["cost"] is None and not (info and "Land" not in info["types"].split()):
            if h in land_ok or ("play_land", h) in seen or c["name"] in lands_ok_by_name:
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
        if h in cast_ok or ("cast", h) in seen or (c.get("tag") or "").startswith("castable"):
            continue
        cat, diff = _infer_cast(c, w, info)
        out.append(_item("play", state, {"verb": "cast", "card": h, "name": c["name"]}, "illegal", prov, idx,
                         label_source="menu-absence", category=cat, category_source="inferred", difficulty=diff))
    return out


def _option_name(o):
    m = re.match(r"^(?:CAST|PLAY LAND|ACTIVATE|PLAY) (.*?)(?: [-*+\d]+/[-*+\d]+)? @\d+", o)
    return m.group(1) if m else None


def _category_of(err):
    m = re.match(r"ILLEGAL \(([^)]+)\)", err or "")
    return m.group(1) if m else None


def items_from_target_row(row, prov, idx):
    prompt = row.get("prompt") or ""
    m = re.search(r"DECISION: Choose the TARGET for (.*?) @(\d+)(.*?)(?: \(the spell/ability resolves|\n|$)", prompt)
    if not m:
        return []
    source = f"{m.group(1)} @{m.group(2)}"
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


def items_from_row(row, prov, idx, cards=None):
    if not isinstance(row, dict) or row.get("t") != "decision":
        return []
    if row.get("type") == "play":
        return items_from_play_row(row, prov, idx, cards)
    if row.get("type") == "target":
        return items_from_target_row(row, prov, idx)
    return []


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
            for k, r in enumerate(rows):
                out.extend(items_from_row(r, prov, k, cards))
    return dedupe(out)


def balance(items, per_cell, seed, keep_mana_abilities=False):
    """One item per position (`position_key`), then at most `per_cell` per (kind, illegal category, difficulty), and
    as many legal items as illegal ones."""
    rng = random.Random(seed)
    pool, seen = [], set()
    for i in sorted(items, key=lambda i: i["id"]):
        k = position_key(i)
        if (keep_mana_abilities or not i.get("mana_ability")) and k not in seen:
            seen.add(k)
            pool.append(i)
    cells = {}
    for i in pool:
        if i["label"] == "illegal":
            cells.setdefault((i.get("kind"), i.get("category"), i.get("difficulty")), []).append(i)
    chosen = []
    for key in sorted(cells, key=lambda k: tuple(str(x) for x in k)):
        c = sorted(cells[key], key=lambda i: i["id"])
        rng.shuffle(c)
        chosen.extend(c[:per_cell])
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
    if p.get("source"):
        p["source"] = re.sub(r" @\d+", "", p["source"])
    return hashlib.sha256(json.dumps([lines, p], sort_keys=True).encode()).hexdigest()


def stats(items):
    def count(f, sub):
        return dict(Counter(f(i) for i in sub).most_common())
    illegal = [i for i in items if i["label"] == "illegal"]
    states = {hashlib.sha256(i["state"].encode()).hexdigest() for i in items}
    return {"items": len(items), "states": len(states),
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
            "by_engine_pin_source": count(lambda i: i["provenance"].get("engine_pin_source"), items)}


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
    if p.get("verb") == "target":
        play = f"choose {nm} {p['card']} as the target of {p.get('source')} {p.get('source_text') or ''}".rstrip()
    else:
        play = {"cast": "cast", "play_land": "play the land", "activate": "activate an ability of",
                "play": "play"}.get(p.get("verb"), p.get("verb")) + f" {nm} {p.get('card')}".replace("  ", " ")
        if p.get("cost"):
            play += f" (cast way: {p['cost']})"
    card = ""
    info = cards(nm) if cards and nm else None
    if info:
        card = f"CARD: {nm} - {info['types']}. {info['oracle']}\n\n"
    return QUESTION.format(card=card, state=item["state"], play=play, cats=", ".join(CATEGORIES))


def check_cli_lane(model, env=None):
    env = os.environ if env is None else env
    if env.get("CLAUDE_CLI_SEAT") != "1":
        sys.exit("rules_probe ask: the Claude CLI is a development lane; it needs CLAUDE_CLI_SEAT=1 (never a score)")
    if "opus" in (model or "").lower():
        sys.exit("rules_probe ask: never Opus")


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


def ask(items, model, out, cards=None, timeout=180):
    check_cli_lane(model)
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
                r = subprocess.run(argv, input=render(it, cards), capture_output=True, text=True, timeout=timeout)
                env = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
                text = env.get("result", r.stdout)
                cost = env.get("total_cost_usd")
            except (subprocess.TimeoutExpired, ValueError) as e:
                text, cost = f"ERROR {e}", None
            fh.write(json.dumps({"id": it["id"], "model": model, "reply": text, "answer": parse_reply(text),
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
    legal_share = per["legal"][1] / n if n else 0.0
    return {"n": n, "missing": sum(1 for i in items if answers.get(i["id"]) is None),
            "accuracy": round(right / n, 4) if n else None, "accuracy_ci": [round(x, 4) for x in wilson_ci(right, n)],
            "balanced_accuracy": round(bal, 4) if bal is not None else None,
            "recall_legal": round(per["legal"][0] / per["legal"][1], 4) if per["legal"][1] else None,
            "recall_illegal": round(per["illegal"][0] / per["illegal"][1], 4) if per["illegal"][1] else None,
            "floors": {"always_legal_accuracy": round(legal_share, 4), "always_legal_balanced": 0.5,
                       "majority_accuracy": round(max(legal_share, 1 - legal_share), 4),
                       "state_reading_balanced": round(hbal, 4) if hbal is not None else None,
                       "state_reading_accuracy": round((hper["legal"][0] + hper["illegal"][0]) / n, 4) if n else None},
            "category_n": len(cat), "category_accuracy": round(sum(cat) / len(cat), 4) if cat else None}


# ---------------------------------------------------------------------------------------------------- CLI

def _read(path):
    """Items from a file `_write` wrote: each state is stored once, as a {"t": "state"} row the items name."""
    states, items = {}, []
    with open(path, encoding="utf-8") as fh:
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
    with open(out, "w", encoding="utf-8") as fh:
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
    b = sub.add_parser("balance")
    b.add_argument("items")
    b.add_argument("--per-cell", type=int, required=True)
    b.add_argument("--seed", type=int, required=True)
    b.add_argument("--out", required=True)
    a = sub.add_parser("ask")
    a.add_argument("items")
    a.add_argument("--model", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--forge-home", default=os.environ.get("FORGE_HOME", "/root/forge"))
    c = sub.add_parser("score")
    c.add_argument("items")
    c.add_argument("answers")
    c.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "mine":
        items = mine(args.runs, cards=card_index(args.forge_home))
        _write(items, args.out)
        print(f"{len(items)} items -> {args.out}")
    elif args.cmd == "stats":
        print(json.dumps(stats(_read(args.items)), indent=None if args.json else 1))
    elif args.cmd == "balance":
        items = balance(_read(args.items), args.per_cell, args.seed)
        _write(items, args.out)
        print(f"{len(items)} items -> {args.out}")
    elif args.cmd == "ask":
        ask(_read(args.items), args.model, args.out, card_index(args.forge_home))
    elif args.cmd == "score":
        ans = {r["id"]: r.get("answer") for r in _read(args.answers)}
        ans = {k: v for k, v in ans.items() if v is not None}
        print(json.dumps(score(_read(args.items), ans), indent=None if args.json else 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
