#!/usr/bin/env python3
"""Which visible game objects the model was "thinking about" at one decision, and the evidence for each.

Kinds, strongest first (a stronger kind wins on an object; `engine` is a separate frame):
  chosen     handles in the option(s) the answer selected, its blocks, or (for labels without a handle, such as an
             attacker "Bilbo, Thief in the Night (2/2)") the seat's own object of that name
  looked     `inspect` targets, the options a `simulate` previewed (`option`, `target`, `then`), `memory_search` names
  planned    names in the seat's latest `set_plan` and in the YOUR PLAN line of its prompt
  mentioned  names and @handles in the provider's reasoning and in the answer's `why`
  engine     attackers and blockers in COMBAT, the stack's sources

What it measures: string matches of the model's own words against the objects its prompt showed. It cannot tell
what the model "meant": a name that matches is evidence, shown on hover, not a claim. Only objects visible in that
decision's state are ever highlighted, so a guess in the reasoning about a card in the opponent's hidden hand stays
text in the trace and never lights up a card.
"""
import re

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import demo_state

HANDLE = re.compile(r"@(\d+)")
RANK = {"chosen": 4, "looked": 3, "planned": 2, "mentioned": 1}
NUMBERED = re.compile(r"(\d+) = [^@\n]*?@(\d+)")


def _names(objs):
    """name (and the part before a comma, for legendary names) -> handles, longest first so 'Bilbo, Thief in the
    Night' is found before 'Bilbo'."""
    out = {}
    for o in objs:
        full = o.get("name") or ""
        for n in {full, full.split(",")[0].strip()}:
            if len(n) >= 3:
                out.setdefault(n, set()).add(o["handle"])
    return sorted(out.items(), key=lambda kv: -len(kv[0]))


def _find_names(text, names):
    """Handles of every visible name in text (case-insensitive, whole words); a longer name hides its parts."""
    found = {}
    text = text or ""
    taken = []
    for name, handles in names:
        for m in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I):
            if any(a <= m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            for h in handles:
                found.setdefault(h, m.group(0))
    return found


def _snippet(text, needle, width=90):
    i = (text or "").lower().find((needle or "").lower())
    if i < 0:
        return (text or "")[:width]
    a = max(0, i - width // 3)
    return ("..." if a else "") + text[a:a + width].replace("\n", " ") + ("..." if a + width < len(text) else "")


class Marks:
    def __init__(self, visible):
        self.visible = {o["handle"] for o in visible}
        self.out = {}

    def add(self, handle, kind, evidence):
        if handle not in self.visible:
            return
        cur = self.out.get(handle)
        if kind == "engine":
            if cur is None:
                self.out[handle] = {"kind": None, "evidence": None, "engine": evidence}
            elif not cur.get("engine"):
                cur["engine"] = evidence
            return
        if cur is None or cur["kind"] is None or RANK[kind] > RANK[cur["kind"]]:
            engine = cur.get("engine") if cur else None
            self.out[handle] = {"kind": kind, "evidence": evidence, "engine": engine}

    def result(self):
        out = {}
        for h, v in self.out.items():
            if v["kind"] is None:
                v = {"kind": "engine", "evidence": v["engine"], "engine": v["engine"]}
            out[str(h)] = v
        return out


def _option_handles(label, objs, own_only=False):
    hs = [int(h) for h in HANDLE.findall(label or "")]
    if hs:
        return hs
    # Attacker labels carry no handle: "Bilbo, Thief in the Night (2/2)". Match the seat's own object by name.
    name = re.sub(r"\s*\(\d+/\d+\)\s*$", "", label or "").strip()
    return [o["handle"] for o in objs if o.get("name") == name and (not own_only or o.get("owner") == "YOU")]


def compute(ev, state, plan=None):
    """Highlights for one decision event (demo_events), given the state its board shows and the seat's latest plan."""
    objs = [o for o in demo_state.visible_objects(state or {}) if o.get("handle") is not None]
    if not objs:
        return {}
    marks = Marks(objs)
    names = _names(objs)
    options = ev.get("options") or []
    answer = ev.get("answer") or {}

    # engine: what the engine itself put in front of the seat.
    for item in state.get("stack", []):
        for h in item.get("handles", []):
            marks.add(h, "engine", "on the stack: " + (item.get("source") or item.get("text") or ""))
    combat = state.get("combat") or ""
    for h in HANDLE.findall(combat):
        marks.add(int(h), "engine", "in combat: " + combat[:120])

    # mentioned: the model's own words.
    texts = [("reasoning", c.get("reasoning") or "") for c in ev.get("calls", [])] + [("why", answer.get("why") or "")]
    for src, text in texts:
        for h in HANDLE.findall(text):
            marks.add(int(h), "mentioned", "%s: %s" % (src, _snippet(text, "@" + h)))
        for h, word in _find_names(text, names).items():
            marks.add(h, "mentioned", "%s: %s" % (src, _snippet(text, word)))

    # planned: the seat's plan, set at this decision or earlier, and the plan line of its prompt.
    plan_texts = []
    if plan:
        plan_texts.append("set_plan: GOAL %s NEXT %s" % (plan.get("goal") or "", plan.get("next") or ""))
    sp = state.get("plan") or {}
    if sp.get("goal") or sp.get("next"):
        plan_texts.append("YOUR PLAN: GOAL %s NEXT %s" % (sp.get("goal") or "", sp.get("next") or ""))
    for text in plan_texts:
        for h, word in _find_names(text, names).items():
            marks.add(h, "planned", _snippet(text, word))

    # looked: lookups.
    for c in ev.get("calls", []):
        for t in c.get("tools", []):
            args = t.get("args") or {}
            if t.get("name") == "inspect":
                target = str(args.get("target") or "")
                hs = [int(h) for h in HANDLE.findall(target)] or list(_find_names(target, names))
                for h in hs:
                    marks.add(h, "looked", "inspect %s" % target)
            elif t.get("name") == "simulate":
                nums = [args.get("option"), args.get("target")] + list(args.get("then") or [])
                for n in nums:
                    if isinstance(n, int) and 1 <= n <= len(options):
                        for h in _option_handles(options[n - 1], objs):
                            marks.add(h, "looked", "simulate previewed option %d: %s" % (n, options[n - 1]))
            elif t.get("name") == "memory_search":
                q = str(args.get("query") or "")
                for h in _find_names(q, names):
                    marks.add(h, "looked", "memory_search %s" % q)

    # chosen: the answer the engine applied.
    for i in ev.get("chosen") or []:
        if 0 <= i < len(options):
            for h in _option_handles(options[i], objs, own_only=ev.get("type") == "attackers"):
                marks.add(h, "chosen", "chosen option %d: %s" % (i + 1, options[i]))
    blocks = answer.get("blocks") or []
    if blocks and state.get("decision"):
        text = state["decision"]
        att = dict((int(n), int(h)) for n, h in NUMBERED.findall(_line(text, "ATTACKERS:")))
        blk = dict((int(n), int(h)) for n, h in NUMBERED.findall(_line(text, "YOUR BLOCKERS:")))
        for b in blocks:
            if isinstance(b, dict):
                for key, table in (("blocker", blk), ("attacker", att)):
                    h = table.get(b.get(key))
                    if h is not None:
                        marks.add(h, "chosen", "block: blocker %s on attacker %s" % (b.get("blocker"), b.get("attacker")))
    return marks.result()


def _line(text, label):
    for line in (text or "").split("\n"):
        if line.strip().startswith(label):
            return line
    return ""
