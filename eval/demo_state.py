#!/usr/bin/env python3
"""Parse the state block of one recorded decision prompt into a structure the demo viewer draws.

Input is the `prompt` field of a `t: "decision"` row (the whole user message the seat was shown). Output is a dict:
the header (turn, phase, active player, priority), one entry per player (counters and zones, each visible object as
{name, handle, was, pt, cost, tags, notes}), the stack, combat, the seat's plan, the decision text, and every other
top-level block kept as raw lines.

What it measures: nothing. It only re-reads what the seat was shown, so the viewer cannot draw anything the seat did
not read. What it cannot do: rebuild hidden zones (the opponent's hand is a count; library order is never shown), and
it parses the observation grammar of observation-faithful-v20 onward best; older rows (July 2026 "yard(5):",
"Name(2/2)[T]") parse into the same shape with fewer fields, and a block it does not know is kept raw, never dropped.
"""
import re

HEADER = re.compile(r"^== Turn (\d+) (\S+) \((.*)\) ==\s*$")
PLAYER = re.compile(r"^(YOU|OPP) \[(.*?)\]\s*(.*)$")
SUBLINE = re.compile(r"^  (\w[\w ]*?)(?:\((\d+)\))?: ?(.*)$")
TOP = re.compile(r"^([A-Z][A-Z' ]*[A-Z])(?: \((.*?)\))?:(?: (.*))?$")
HANDLE_ITEM = re.compile(r"\s*(.+?) @(\d+)((?: \([^()]*\))*)(?:, |$)")
LAND_GROUP = re.compile(r"(.+?) x(\d+) \[(untapped|tapped) (.*?)\](?:, |$)")
LAND_HANDLE = re.compile(r"@(\d+)(?:\(was @(\d+)\))?(?: \(([^()]*)\))?")
TAG = re.compile(r"\[([^\[\]]*)\]")
PT = re.compile(r"(?<![\w/{])(-?\d+|\*)/(-?\d+|\*)(?=$|\s|\)|\[)")
COST = re.compile(r"\s(\d*(?:\{[^}]+\})+)(?=\s|$)")
OPTION = re.compile(r"^\s+(\d+)\) (.*)$")
HANDLE = re.compile(r"@(\d+)")

ZONES = ("hand", "board", "graveyard", "yard", "exile", "command", "sideboard")


def _parse_object(text, zone):
    """One object of a zone list, without its handle: 'Bilbo, Thief in the Night 2/2 [untapped] [haste]'."""
    tags = TAG.findall(text)
    rest = TAG.sub(" ", text)
    pt = None
    m = PT.search(rest)
    if m:
        pt = "%s/%s" % (m.group(1), m.group(2))
        rest = rest[:m.start()] + " " + rest[m.end():]
    cost = None
    m = COST.search(" " + rest)
    if m:
        cost = m.group(1)
        rest = (" " + rest)[:m.start()] + " " + (" " + rest)[m.end():]
    name = re.sub(r"\s+", " ", re.sub(r"\(\s*\)", " ", rest)).strip().rstrip("(").strip()
    flat = []
    for t in tags:
        # "[T]" is the July 2026 grammar's tapped tag.
        flat.extend("tapped" if x.strip() == "T" else x.strip() for x in t.split(",") if x.strip())
    return {"name": name, "pt": pt, "cost": cost, "tags": flat, "zone": zone}


def parse_zone_list(text, zone):
    """'A @1, B 2/2 [tapped] @2 (was @1) (castable)' -> objects. A list without handles (old rows) splits on ', '."""
    text = (text or "").strip()
    # "(none)", "(no nonland permanents)": an empty zone said in words.
    if not text or (text.startswith("(") and text.endswith(")") and not HANDLE.search(text)):
        return []
    out = []
    if HANDLE.search(text):
        pos = 0
        for m in HANDLE_ITEM.finditer(text):
            if m.start() != pos:
                break
            pos = m.end()
            obj = _parse_object(m.group(1), zone)
            obj["handle"] = int(m.group(2))
            notes = re.findall(r"\(([^()]*)\)", m.group(3) or "")
            was = [n for n in notes if n.startswith("was @")]
            obj["was"] = int(was[0][5:]) if was else None
            obj["notes"] = [n for n in notes if not n.startswith("was @")]
            out.append(obj)
        if pos < len(text):
            out.append({"name": text[pos:].strip(), "raw": True, "zone": zone, "handle": None, "tags": []})
        return out
    for part in re.split(r", (?=[A-Z0-9])", text):
        obj = _parse_object(part, zone)
        obj["handle"] = None
        obj["was"] = None
        obj["notes"] = []
        out.append(obj)
    return out


def parse_lands(text):
    """'1 untapped / 2 total (Forest x1 [tapped @8(was @4)], Gaea's Cradle x1 [untapped @13])' -> counts and lands."""
    m = re.match(r"(\d+) untapped / (\d+) total(?: \((.*)\))?\s*$", (text or "").strip())
    if not m:
        return {"untapped": None, "total": None, "lands": [], "raw": text}
    lands = []
    groups = m.group(3) or ""
    if HANDLE.search(groups):
        for g in LAND_GROUP.finditer(groups):
            name, status = g.group(1).strip(), g.group(3)
            for h in LAND_HANDLE.finditer(g.group(4)):
                extra = h.group(3) or ""
                obj = _parse_object(name + " " + extra, "lands") if extra else {
                    "name": name, "pt": None, "cost": None, "tags": [], "zone": "lands"}
                obj["name"] = name
                obj["tags"] = [status] + obj["tags"]
                obj["handle"] = int(h.group(1))
                obj["was"] = int(h.group(2)) if h.group(2) else None
                obj["notes"] = []
                lands.append(obj)
    elif groups:
        for part in re.split(r", (?=[A-Z])", groups):
            lands.append({"name": part.strip(), "pt": None, "cost": None, "tags": [], "zone": "lands",
                          "handle": None, "was": None, "notes": []})
    return {"untapped": int(m.group(1)), "total": int(m.group(2)), "lands": lands}


def _counters(text):
    """'life 18 | poison 0/10 | hand 2 | library 52 (hidden, order unknown) | mana pool empty' -> dict."""
    out = {}
    for part in (text or "").split(" | "):
        part = part.strip()
        m = re.match(r"(lands played this turn|spells cast this turn|mana pool|life|poison|hand|library|graveyard|"
                     r"exile|yard)\s+(.*)$", part)
        if not m:
            if part:
                out.setdefault("other", []).append(part)
            continue
        key, val = m.group(1).replace(" ", "_"), m.group(2)
        n = re.match(r"(-?\d+)", val)
        out[key] = int(n.group(1)) if n and key in ("life", "hand", "library", "graveyard", "exile", "yard") else val
    return out


def _player(kind, name, counters):
    return {"kind": kind, "name": name, "counters": _counters(counters),
            "zones": {"hand": [], "board": [], "lands": [], "graveyard": [], "exile": []},
            "hand_hidden": None, "lands_summary": None, "lines": {}}


def _plan(text):
    m = re.search(r"GOAL:\s*(.*?)\s*(?:NEXT:\s*(.*))?$", text or "", re.S)
    if not m:
        return {"goal": None, "next": None, "raw": text}
    return {"goal": m.group(1).strip(), "next": (m.group(2) or "").strip() or None}


def parse_prompt(prompt):
    """The whole state of one decision prompt. Never raises on an unknown shape: it keeps the text raw."""
    st = {"header": None, "players": [], "stack": [], "stack_raw": None, "combat": None, "blocks": {},
          "decision": None, "options": [], "answer_line": None, "archive": None, "unparsed": []}
    if not prompt:
        return st
    text = prompt
    m = re.search(r"<archive>\s*(.*?)\s*</archive>", text, re.S)
    if m:
        st["archive"] = m.group(1)
        text = text[:m.start()] + text[m.end():]
    lines = text.split("\n")
    player = None
    block = None
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip():
            continue
        h = HEADER.match(line)
        if h:
            meta = dict(kv.split(": ", 1) for kv in h.group(3).split(" | ") if ": " in kv)
            st["header"] = {"turn": int(h.group(1)), "phase": h.group(2), "active": meta.get("active"),
                            "priority": meta.get("priority")}
            continue
        p = PLAYER.match(line)
        if p:
            player = _player(p.group(1), p.group(2), p.group(3))
            st["players"].append(player)
            block = None
            continue
        if line.startswith("  ") and player is not None and block is None:
            s = SUBLINE.match(line)
            if s:
                key, n, val = s.group(1).strip(), s.group(2), s.group(3)
                zone = "graveyard" if key == "yard" else key
                if key == "hand" and n is None:
                    player["hand_hidden"] = val
                elif key == "lands":
                    player["lands_summary"] = parse_lands(val)
                    player["zones"]["lands"] = player["lands_summary"]["lands"]
                elif key in ZONES:
                    player["zones"][zone] = parse_zone_list(val, zone)
                else:
                    player["lines"][key] = val
                continue
            player["lines"].setdefault("raw", []).append(line.strip())
            continue
        if line.startswith("DECISION:"):
            body = [line[len("DECISION:"):].strip()]
            while i < len(lines) and not lines[i].startswith("ANSWER:"):
                body.append(lines[i])
                i += 1
            st["decision"] = "\n".join(body).strip()
            st["options"] = [{"n": int(o.group(1)), "label": o.group(2)} for o in map(OPTION.match, body) if o]
            if i < len(lines):
                st["answer_line"] = "\n".join(lines[i:]).strip()
            break
        t = TOP.match(line)
        if t:
            player = None
            label, note, val = t.group(1), t.group(2), t.group(3) or ""
            block = label
            if label.startswith("STACK"):
                st["stack_raw"] = val
                st["stack"] = parse_stack(val)
            elif label == "FACTS":
                st["facts"] = val
            elif label == "COMBAT":
                st["combat"] = val
            elif label == "YOUR PLAN":
                st["plan"] = _plan(val)
                st["plan"]["stated"] = note
            st["blocks"][label] = {"note": note, "text": val, "lines": []}
            continue
        if block is not None and line.startswith("  "):
            st["blocks"][block]["lines"].append(line.strip())
            continue
        if line.startswith("stack: "):
            # July 2026 grammar: "stack: 1 pending; top=Emrakul, the Aeons Torn (<player>)".
            st["stack_raw"] = line[len("stack: "):]
            st["stack"] = [{"text": st["stack_raw"], "handles": []}]
            continue
        st["unparsed"].append(line)
    st["blocks"].pop("YOUR PLAN", None)
    return st


def parse_stack(val):
    """'1 not yet resolved (...): #1 spell Mox Ruby @27 (you): Mox Ruby | #2 ...' -> items, top first."""
    if not val or val.strip() in ("(empty)", "empty"):
        return []
    body = val.split("): ", 1)[1] if re.match(r"\d+ not yet resolved \(", val) and "): " in val else val
    items = []
    for part in re.split(r" \| (?=#\d+ )", body):
        m = re.match(r"#(\d+) (spell|trigger of|ability of) (.*?)(?: \(([^()]*)\))?(?:: (.*))?$", part.strip())
        if not m:
            items.append({"text": part.strip(), "handles": [int(x) for x in HANDLE.findall(part)]})
            continue
        items.append({"index": int(m.group(1)), "kind": m.group(2), "source": m.group(3), "who": m.group(4),
                      "text": m.group(5), "handles": [int(x) for x in HANDLE.findall(part)]})
    return items


def visible_objects(state):
    """Every object the seat can see in this state, each with its owner's kind ('YOU'/'OPP')."""
    out = []
    for p in state.get("players", []):
        for zone, objs in p["zones"].items():
            for o in objs:
                out.append(dict(o, owner=p["kind"]))
    return out
