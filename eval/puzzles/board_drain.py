#!/usr/bin/env python3
"""The drain board's model (puzzle_gen.drain_board and drain2_board): a verdict at every position of a recorded attempt.

step_regret_board.py dispatches here for a board whose id names the drain or drain2 family. The state is read off the
seat's own prompt (SeatObservation: hand, battlefield, graveyard, untapped Swamps, mana pool, the land drop, every
stack item, the opponent's life, the step), then valued by an exhaustive search over every legal sequence of casts,
sacrifices, Skeleton returns, the land drop and passes, with the stack and the end step modelled. It is a MODEL
verdict, exact only when its assumptions hold:

  1. Every card is one of puzzle_gen.DRAIN_COSTS (the drainers Blood Artist, Zulaport Cutthroat, Cruel Celebrant;
     the outlets Carrion Feeder and Viscera Seer, Ashnod's Altar; Reassembling Skeleton, Bloodghast, Gravecrawler),
     every land a Swamp, the only mana Swamps, a floating {B} or {C} and the Altar, and the only life loss a death
     trigger (each drainer on the battlefield as a creature dies, the dying one included, drains 1).
  2. The opponent has no cards, mana or permanents, and the turn ending loses (Turns:1).
  3. Sorcery-speed casts and the land drop need an empty stack in the main phase; a sacrifice and a Skeleton return
     can be made at any priority, the end step included (the seat is asked there when one is playable). A pass on an
     empty stack in the main phase goes to the end step (the pool empties); one in the end step ends the turn. A
     pass that yields (any stack) leaves the seat asked again this turn only in its main phase on an empty stack
     (LlmSpikeGui's window skip): the end step is then never asked.
  4. Each Skeleton return on the stack brings it back if it is in the graveyard then; Bloodghast's landfall trigger
     (only when it is in the graveyard as the land enters) returns it when accepted; Gravecrawler is cast from the
     graveyard only while Carrion Feeder (the only other Zombie) is on the battlefield.
  5. At a sacrifice question the cost is the only thing undecided (CANCEL goes back); at a trigger-order or scry
     question nothing that matters is (any order drains the same); at a target question Blood Artist's trigger
     drains only if it targets the opponent.

Anything outside the model (another card, a second copy, an unknown stack item, another step) is refused
(NotModeled), never guessed. A question row's state is the activation's prompt with the recorded sacrifice applied,
checked against the row's own prompt.
"""
import os
import re
import sys
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import puzzle_gen as pg  # noqa: E402
import step_regret as sr  # noqa: E402
from step_regret_board import NotModeled  # noqa: E402

VERSION = "board-drain-v1"
COSTS = pg.DRAIN_COSTS
DRAINERS = pg.DRAINERS
ALTAR = pg.ALTAR
SEER, FEEDER = "Viscera Seer", "Carrion Feeder"
OUTLETS = pg.COUNTED_OUTLETS
SKELETON, GHAST, CRAWLER, ARTIST = "Reassembling Skeleton", "Bloodghast", "Gravecrawler", "Blood Artist"
MEANINGFUL = ("creature", "return", "ghast")   # stack items that change the board; drains and no-ops do not
NAME = re.compile(r"\s*(.+?)(?= \S*\{| \d+/\d+| @| \[|$)")


def name_of(label):
    return NAME.match(label).group(1)


# ------------------------------------------------------------------ reading the prompt

def _line(prompt, prefix, after):
    lines = prompt.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(after)), None)
    if start is None:
        raise NotModeled("no %r line" % after)
    for ln in lines[start:]:
        if ln.startswith(prefix):
            return ln
    raise NotModeled("no %r line" % prefix)


def _cards(body):
    return [] if not body.strip() or body.strip().startswith(("(none)", "(empty)", "(no nonland")) else [
        name_of(c) for c in re.split(r", (?=[A-Z])", body)]


def _unique(names, where):
    if len(set(names)) != len(names):
        raise NotModeled("two copies in the %s: %s" % (where, names))
    return tuple(sorted(names))


def _stack_item(text):
    m = re.match(r"#\d+ (spell|ability of|trigger of) (.+?)(?: \d+/\d+)? @\d+", text)
    kind, card = (m.group(1), m.group(2)) if m else (None, None)
    if kind == "spell" and card in COSTS:
        return ("creature", card)
    if kind == "ability of" and card == SKELETON and "Return Reassembling Skeleton" in text:
        return ("return",)
    if kind == "ability of" and card in OUTLETS:
        return ("noop", "scry" if card == SEER else "counter")
    if kind == "trigger of" and card == ARTIST:
        t = re.search(r"-> targets: (\w+)", text)
        if not t:
            raise NotModeled("an untargeted Blood Artist trigger: %r" % text[:80])
        return ("drain", 1 if t.group(1) == "Opponent" else 0)
    if kind == "trigger of" and card in DRAINERS:
        return ("drain", 1)
    if kind == "trigger of" and card == GHAST:
        return ("ghast",)
    raise NotModeled("stack item %r" % text[:80])


def parse_state(prompt):
    """The model state from a seat prompt: pc / pb (floating {C} / {B}), sw (untapped Swamps), land (a Swamp in hand
    and the land drop unused), bf / gy / hand (sorted names), altar, stack (top first), opp (the opponent's life), eot
    (the end step), ended (the turn is over), pending (an outlet whose sacrifice is being chosen: set by the rows)."""
    head = re.match(r"== Turn (\d+) (\w+)", prompt)
    if not head or head.group(1) != "1" or head.group(2) not in ("MAIN2", "END_OF_TURN"):
        raise NotModeled("step %s (the model is turn 1's second main phase and end step)" % (
            head.groups() if head else None,))
    eot = head.group(2) == "END_OF_TURN"
    you = _line(prompt, "YOU [", "YOU [")
    pool = re.search(r"mana pool ([^|]*)", you).group(1).strip()
    pc = pb = 0
    if not pool.startswith("empty"):
        for col, n in re.findall(r"\{(\w):(\d+)\}", pool):
            if col == "B":
                pb += int(n)
            elif col == "C":
                pc += int(n)
            else:
                raise NotModeled("mana pool %r" % pool)
        if not pb + pc:
            raise NotModeled("mana pool %r" % pool)
    hand_ln = _line(prompt, "  hand(", "YOU [")
    hand = _cards(hand_ln.split("): ", 1)[1] if "): " in hand_ln else "")
    swamps_in_hand = hand.count("Swamp")
    hand = [c for c in hand if c != "Swamp"]
    for c in hand:
        if c not in COSTS:
            raise NotModeled("card in hand %r" % c)
    if swamps_in_hand > 1:
        raise NotModeled("two Swamps in hand")
    played = re.search(r"lands played this turn (\d+)/(\d+)", you)
    land = bool(swamps_in_hand) and not eot and played is not None and int(played.group(1)) < int(played.group(2))
    bf = _cards(_line(prompt, "  board:", "YOU [").split(": ", 1)[1])
    altar = ALTAR in bf
    bf = [c for c in bf if c != ALTAR]
    for c in bf:
        if c not in COSTS:
            raise NotModeled("permanent %r" % c)
    lands = _line(prompt, "  lands:", "YOU [")
    if any(t != "Swamp" for t in re.findall(r"([A-Z][\w' -]*?) x\d+", lands)):
        raise NotModeled("a land other than a Swamp: %r" % lands)
    sw = int(re.search(r"lands: (\d+) untapped", lands).group(1))
    gy = _cards(_line(prompt, "  graveyard(", "YOU [").split("): ", 1)[1])
    for c in gy:
        if c not in COSTS:
            raise NotModeled("card in the graveyard %r" % c)
    opp_ln = _line(prompt, "OPP [", "OPP [")
    if " hand 0 " not in opp_ln + " " or "(no nonland permanents)" not in _line(prompt, "  board:", "OPP ["):
        raise NotModeled("the opponent can act: %r" % opp_ln)
    st = _line(prompt, "STACK", "STACK").split(": ", 1)[1]
    items = [] if st.startswith("(empty)") else [_stack_item(x)
                                                 for x in re.split(r" \| (?=#\d+ )", st[st.index("#"):])]
    return {"pc": pc, "pb": pb, "sw": sw, "land": land, "bf": _unique(bf, "battlefield"),
            "gy": _unique(gy, "graveyard"), "hand": _unique(hand, "hand"), "altar": altar, "stack": items,
            "opp": int(re.search(r"OPP \[[^\]]*\] life (-?\d+)", opp_ln).group(1)), "eot": eot, "ended": False,
            "pending": None, "yld": False}


# ------------------------------------------------------------------ moves

def _pay(s, generic, black):
    """The state with {generic}{black...} paid: floating {C} for generic first, then floating {B}, then Swamps."""
    use_c = min(s["pc"], generic)
    need = generic - use_c + black
    use_b = min(s["pb"], need)
    if need - use_b > s["sw"]:
        return None
    return dict(s, pc=s["pc"] - use_c, pb=s["pb"] - use_b, sw=s["sw"] - (need - use_b))


def _add(names, c):
    return tuple(sorted(names + (c,)))


def _drop(names, c):
    out = list(names)
    out.remove(c)
    return tuple(out)


def stack_drain(s):
    return sum(x[1] for x in s["stack"] if x[0] == "drain")


def outlets(s):
    return ([ALTAR] if s["altar"] and s["bf"] else []) + [o for o in OUTLETS if o in s["bf"]]


def sacrifice(s, outlet, victim):
    """The state after `victim` is sacrificed to `outlet`: a drain trigger per drainer on the battlefield (the victim
    included; Blood Artist's untargeted yet), over the outlet's ability (none for the Altar's mana ability)."""
    if victim not in s["bf"]:
        raise NotModeled("sacrifice of %r, not on the battlefield" % victim)
    trig = [("drain", 1, "target") if d == ARTIST else ("drain", 1) for d in DRAINERS if d in s["bf"]]
    ability = [] if outlet == ALTAR else [("noop", "scry" if outlet == SEER else "counter")]
    return dict(s, bf=_drop(s["bf"], victim), gy=_add(s["gy"], victim), pc=s["pc"] + (2 if outlet == ALTAR else 0),
                stack=trig + ability + s["stack"], pending=None)


def resolve(s, accept=True):
    """The state once the top stack item resolves (an optional Bloodghast trigger accepted unless `accept` is off)."""
    top, rest = s["stack"][0], s["stack"][1:]
    t = dict(s, stack=rest)
    if top[0] == "drain":
        t["opp"] = s["opp"] - top[1]
    elif top[0] == "creature":
        t["bf"] = _add(s["bf"], top[1])
    elif top[0] == "return" and SKELETON in s["gy"]:
        t.update(bf=_add(s["bf"], SKELETON), gy=_drop(s["gy"], SKELETON))
    elif top[0] == "ghast" and accept and GHAST in s["gy"]:
        t.update(bf=_add(s["bf"], GHAST), gy=_drop(s["gy"], GHAST))
    return t


def successors(s):
    """Every legal move from a priority position: [(move, next state)]. After a yield the seat is asked only in its
    main phase on an empty stack (LlmSpikeGui's yield window skip), and its main-phase pass ends the turn."""
    out = []
    if s.get("yld") and (s["stack"] or s["eot"]):
        return [(("resolve",), resolve(s))] if s["stack"] else []
    if s["stack"]:
        out.append((("resolve",), resolve(s)))
    elif not s["eot"] and not s.get("yld"):
        out.append((("phase",), dict(s, eot=True, pc=0, pb=0, land=False)))
    for o in outlets(s):
        for v in s["bf"]:
            out.append((("sac", o, v), sacrifice(s, o, v)))
    if SKELETON in s["gy"]:
        t = _pay(s, 1, 1)
        if t:
            out.append((("return",), dict(t, stack=[("return",)] + s["stack"])))
    if not s["stack"] and not s["eot"]:
        for c in s["hand"]:
            t = _pay(s, *COSTS[c]) if COSTS[c] else None
            if t:
                out.append((("cast", c), dict(t, hand=_drop(s["hand"], c), stack=[("creature", c)])))
        if CRAWLER in s["gy"] and FEEDER in s["bf"]:
            t = _pay(s, 0, 1)
            if t:
                out.append((("cast", CRAWLER), dict(t, gy=_drop(s["gy"], CRAWLER), stack=[("creature", CRAWLER)])))
        if s["land"]:
            out.append((("land",), dict(s, sw=s["sw"] + 1, land=False,
                                        stack=[("ghast",)] if GHAST in s["gy"] else [])))
    return out


# ------------------------------------------------------------------ the model's value

def _key(s):
    return (s["pc"], s["pb"], s["sw"], s["land"], s["bf"], s["gy"], s["hand"], s["altar"],
            tuple(tuple(x) for x in s["stack"] if x[0] in MEANINGFUL), s["eot"], bool(s.get("yld")))


@lru_cache(maxsize=None)
def _best(key):
    pc, pb, sw, land, bf, gy, hand, altar, stack, eot, yld = key
    s = {"pc": pc, "pb": pb, "sw": sw, "land": land, "bf": bf, "gy": gy, "hand": hand, "altar": altar,
         "stack": list(stack), "opp": 0, "eot": eot, "ended": False, "pending": None, "yld": yld}
    return max([stack_drain(t) + _best(_key(t)) for _, t in successors(s)], default=0)


def value(s):
    """The most life the position can still drain: the drain triggers on the stack, then the best line."""
    if s.get("ended"):
        return 0
    if s.get("pending"):
        return max([value(sacrifice(s, s["pending"], v)) for v in s["bf"]] + [value(dict(s, pending=None))])
    return stack_drain(s) + _best(_key(s))


def winnable(s):
    return s["opp"] <= 0 or value(s) >= s["opp"]


def shortfall(s):
    return s["opp"] - value(s)


def after_option(s, label, yielded=False):
    """The state after the seat picks the option `label` (a PASS that yields ends the turn on an empty stack)."""
    if s.get("pending"):
        if label.startswith("CANCEL"):
            return dict(s, pending=None)
        return sacrifice(s, s["pending"], name_of(label))
    if label.startswith(("you (", "opponent (")):
        i = next((i for i, x in enumerate(s["stack"]) if x == ("drain", 1, "target")), None)
        if i is None:
            raise NotModeled("a target question with no Blood Artist trigger pending")
        st = list(s["stack"])
        st[i] = ("drain", 1 if label.startswith("opponent") else 0)
        return dict(s, stack=st)
    if label.startswith(("YES", "NO")):
        if not s["stack"] or s["stack"][0] != ("ghast",):
            raise NotModeled("an optional trigger other than Bloodghast's")
        return resolve(s, accept=label.startswith("YES"))
    if label.startswith("PASS"):
        s = dict(s, yld=bool(s.get("yld") or yielded))
        if s["stack"]:
            return resolve(s)
        if s["eot"] or s["yld"]:
            return dict(s, ended=True)
        return dict(s, eot=True, pc=0, pb=0, land=False)
    if label.startswith("PLAY LAND Swamp") and s["land"]:
        return dict(s, sw=s["sw"] + 1, land=False, stack=([("ghast",)] if GHAST in s["gy"] else []) + s["stack"])
    if label.startswith("ACTIVATE Swamp"):
        if not s["sw"]:
            raise NotModeled("a tapped Swamp activated")
        return dict(s, sw=s["sw"] - 1, pb=s["pb"] + 1)
    m = re.match(r"(CAST|ACTIVATE) (.+?)(?: \d+/\d+)? @\d+", label)
    card = m.group(2) if m else None
    if m and m.group(1) == "ACTIVATE" and card in (ALTAR,) + OUTLETS:
        return dict(s, pending=card)
    if m and m.group(1) == "ACTIVATE" and card == SKELETON and SKELETON in s["gy"]:
        t = _pay(s, 1, 1)
        if t:
            return dict(t, stack=[("return",)] + s["stack"])
    if m and m.group(1) == "CAST" and card in COSTS:
        from_gy = card not in s["hand"] and card == CRAWLER and CRAWLER in s["gy"]
        t = _pay(s, *(COSTS[card] or (99, 0))) if (card in s["hand"] or from_gy) else None
        if t:
            zone = dict(gy=_drop(s["gy"], card)) if from_gy else dict(hand=_drop(s["hand"], card))
            return dict(t, stack=[("creature", card)] + s["stack"], **zone)
    raise NotModeled("option %r" % label)


# ------------------------------------------------------------------ an attempt

def picked(row):
    """The labels a row picked: its picks (a pick list), else its one choice (-1 at a priority: the pass)."""
    opts = row.get("options") or []
    if row.get("picks") is not None and row.get("type") in ("sacrifice", "trigger-order", "scry"):
        return [opts[i] for i in row["picks"]]
    c = row.get("choice")
    if isinstance(c, int) and 0 <= c < len(opts):
        return [opts[c]]
    return [o for o in opts if o.startswith("PASS")][:1]


def chosen_label(row):
    p = picked(row)
    return "; ".join(p) if row.get("type") in ("trigger-order", "scry") else (p[0] if p else None)


def yielded(row):
    m = re.search(r"(?im)^YIELD:\s*(\S+)", row.get("raw_reply") or "")
    return bool(m and m.group(1).lower() not in ("none", "no", "-"))


def apply_row(s, row):
    """The state after the row's recorded answer."""
    if row.get("type") in ("trigger-order", "scry"):
        return s
    return after_option(s, chosen_label(row) or "", yielded=yielded(row))


def _activation(rows, k):
    """The play row that activated the outlet whose sacrifice row is k, and the outlet."""
    j = k - 1
    while j >= 0 and rows[j].get("tier") not in sr.MODEL_TIERS and rows[j].get("type") != "play":
        j -= 1
    m = re.match(r"ACTIVATE (Viscera Seer|Carrion Feeder|Ashnod's Altar)\b", (chosen_label(rows[j]) or "")
                 if j >= 0 and rows[j].get("type") == "play" else "")
    if not m:
        raise NotModeled("row %d: a sacrifice with no outlet activated before it" % k)
    return j, m.group(1)


def position_state(rows, k):
    """The model state before decision row k, with any yield the seat gave earlier this turn (it is not in the prompt). A sacrifice row is its prompt with the outlet pending; a trigger-order
    or target row is the activation's prompt with the recorded sacrifice (and any earlier target) applied, checked
    against the row's own prompt (the battlefield, the graveyard and the opponent's life must agree)."""
    yld = any(r.get("type") == "play" and yielded(r) and (chosen_label(r) or "").startswith("PASS") for r in rows[:k])
    return dict(_position_state(rows, k), yld=yld)


def _position_state(rows, k):
    row = rows[k]
    t = row.get("type")
    if t in ("play", "scry", "confirm-trigger"):
        return parse_state(row.get("prompt") or "")
    if t == "sacrifice":
        _, outlet = _activation(rows, k)
        return dict(parse_state(row.get("prompt") or ""), pending=outlet)
    if t in ("trigger-order", "target"):
        j = max((i for i in range(k) if rows[i].get("type") == "sacrifice"), default=None)
        if j is None or any(rows[i].get("type") not in ("trigger-order", "target", "counter-type")
                            for i in range(j + 1, k)):
            raise NotModeled("row %d: a %s question not after a sacrifice" % (k, t))
        a, outlet = _activation(rows, j)
        s = dict(parse_state(rows[j].get("prompt") or rows[a].get("prompt") or ""), pending=outlet)
        s = after_option(s, chosen_label(rows[j]))
        for i in range(j + 1, k):
            if rows[i].get("type") == "target":
                s = apply_row(s, rows[i])
        own = parse_state(row.get("prompt") or "")
        if (own["bf"], own["gy"], own["opp"]) != (s["bf"], s["gy"], s["opp"]):
            raise NotModeled("row %d: the derived state %s does not match the prompt's %s" % (
                k, (s["bf"], s["gy"], s["opp"]), (own["bf"], own["gy"], own["opp"])))
        return s
    raise NotModeled("row %d: a %s question" % (k, t))


def trajectory(rows):
    """Each position step_regret.py searches (before every model decision, then the end): its K, the decision taken
    there, the model's value and verdict."""
    out = []
    pos = sr.positions(rows)
    if len(pos) == 1:
        return out
    for i, k, row in pos:
        if row is None:
            last = pos[-2][1]
            s = apply_row(position_state(rows, last), rows[last])
            label = None
        else:
            s = position_state(rows, k)
            label = chosen_label(row)
        v = value(s)
        out.append({"index": i, "k": k, "type": None if row is None else row.get("type"), "choice": label,
                    "opp": s["opp"], "value": v, "winnable": s["opp"] <= 0 or v >= s["opp"],
                    "yielded": bool(row is not None and yielded(row)), "pending_sac": s.get("pending")})
    return out


def floor_at(rows, k):
    """At decision row k: every option's verdict after it is taken (a PASS read without a yield), and the share that
    keep the win."""
    row = rows[k]
    s = position_state(rows, k)
    opts = row.get("options") or []
    if row.get("type") in ("trigger-order", "scry"):
        answers = [(o, winnable(s)) for o in opts]
    else:
        answers = [(o, winnable(after_option(s, o))) for o in opts]
    kept = [o for o, w in answers if w]
    return {"k": k, "type": row.get("type"), "n_options": len(answers), "kept": kept,
            "floor": len(kept) / len(answers) if answers else None, "model_label": chosen_label(row),
            "model_yielded": yielded(row), "options": dict(answers)}


# ------------------------------------------------------------------ an engine witness: the model's line, written

def script_line(row):
    """A recorded model decision as a ScriptBridge line: the words its option starts with."""
    t = row.get("type")
    labels = picked(row)
    if t in ("trigger-order", "scry"):
        return "; ".join(name_of(x) for x in labels) or "none"
    label = labels[0] if labels else ""
    if t == "sacrifice":
        return "cancel" if label.startswith("CANCEL") else name_of(label)
    if t == "target":
        return label.split(" (")[0]
    if t == "confirm-trigger":
        return "yes" if label.startswith("YES") else "no"
    if label.startswith("PASS"):
        if yielded(row):
            raise NotModeled("a yielding pass has no script line")
        return "pass"
    if label.startswith("PLAY LAND Swamp"):
        return "play land Swamp"
    m = re.match(r"(CAST|ACTIVATE) (.+?)(?: \d+/\d+)? @\d+", label)
    if m:
        return "%s %s" % (m.group(1).lower(), m.group(2))
    raise NotModeled("no script line for %r" % label)


def prefix_lines(rows, k):
    return [script_line(r) for r in rows[:k] if r.get("tier") in sr.MODEL_TIERS]


def asked(s):
    """The seat is offered a priority window when anything but a Swamp's mana is playable there: an outlet with a
    creature to give it, or the Skeleton's return it can pay (LlmSpikeGui.floatChangesNothing; drain_lines)."""
    return bool(outlets(s)) or (SKELETON in s["gy"] and _pay(s, 1, 1) is not None)


def _trigger_lines(before):
    drainers = [d for d in DRAINERS if d in before["bf"]]
    return (["; ".join(drainers)] if len(drainers) > 1 else []) + (["opponent"] if ARTIST in drainers else [])


def _targeted(s):
    return dict(s, stack=[("drain", 1) if x == ("drain", 1, "target") else x for x in s["stack"]])


ORDER = {"resolve": 0, "sac": 1, "return": 2, "cast": 3, "land": 4, "phase": 5}


def walk(s, first_asked=True):
    """The model's line from priority position `s`, written for the script arm: each move keeps the shortfall (a
    resolve first wherever it does), a "pass" in each window the seat is asked, the questions each move raises
    answered, until the drain that kills resolves. Returns (lines, the state at the end)."""
    lines = []
    need = shortfall(s)
    if need > 0:
        raise ValueError("the position is lost: %d short" % need)
    window = first_asked
    while s["opp"] > 0:
        moves = sorted(successors(s), key=lambda mt: ORDER[mt[0][0]])
        move, t = next((m, t) for m, t in moves if shortfall(t) <= need)
        kind = move[0]
        if kind == "resolve":
            if window or asked(s):
                lines.append("pass")
            top = s["stack"][0]
            if top == ("ghast",):
                lines.append("yes")
            elif top == ("noop", "scry"):
                lines.append("Colossal Dreadmaw")
        elif kind == "phase":
            lines.append("pass")
        elif kind == "sac":
            lines.append("activate " + move[1])
            if len(s["bf"]) > 1:
                lines.append(move[2])
            lines += _trigger_lines(s)
            t = _targeted(t)
        elif kind == "return":
            lines.append("activate " + SKELETON)
        elif kind == "cast":
            lines.append("cast " + move[1])
        else:
            lines.append("play land Swamp")
        s, window = t, False
    while lines and lines[-1] == "pass":
        lines.pop()
    return lines, s


def script_for(rows, k):
    """The witness script for position k: the recorded prefix, then the model's line from k (at a question, the
    first answer that keeps the win). Raises ValueError when the model calls the position lost."""
    row = rows[k]
    s = position_state(rows, k)
    t = row.get("type")
    opts = row.get("options") or []
    if t == "sacrifice":
        option = next((o for o in opts if not o.startswith("CANCEL") and winnable(after_option(s, o))), None)
        if option is not None:
            lines, end = walk(_targeted(after_option(s, option)), first_asked=False)
            lines = [name_of(option)] + _trigger_lines(s) + lines
        else:   # only going back keeps the win: cancel, then the line from the menu it returns to
            option = next((o for o in opts if o.startswith("CANCEL")), None)
            if option is None:
                raise ValueError("K=%d: no sacrifice keeps the win" % k)
            lines, end = walk(after_option(s, option))
            lines = ["cancel"] + lines
    elif t == "target":
        option = next(o for o in opts if o.startswith("opponent"))
        lines, end = walk(after_option(s, option), first_asked=False)
        lines = ["opponent"] + lines
    elif t == "confirm-trigger":
        option = next(o for o in opts if o.startswith("YES"))
        lines, end = walk(after_option(s, option), first_asked=False)
        lines = ["yes"] + lines
    elif t == "play":
        lines, end = walk(s)
        first = lines[0].lower() if lines else "pass"
        word = {"pass": "PASS", "play land swamp": "PLAY LAND Swamp"}.get(first, first)
        option = next((o for o in opts if o.lower().startswith(word.lower())), None)
    else:
        raise NotModeled("K=%d: no witness at a %s question" % (k, t))
    dealt = s["opp"] - end["opp"]
    if dealt < s["opp"]:
        raise ValueError("K=%d: the model's line drains %d of %d" % (k, dealt, s["opp"]))
    pre = prefix_lines(rows, k)
    return {"k": k, "prefix": len(pre), "lines": pre + lines, "dealt": dealt, "opp": s["opp"], "option": option}


def witness_verdict(recorded, witness, k, result, option=None):
    """As step_regret_board.witness_verdict, read with this family's prompt and pick lists."""
    if len(witness) <= k:
        return "drift: the witness game has %d decision rows, not %d" % (len(witness), k + 1)
    for i in range(k + 1):
        a, b = recorded[i], witness[i]
        if (a.get("type"), a.get("options")) != (b.get("type"), b.get("options")):
            return "drift: row %d is %s %s, the recording's %s %s" % (i, b.get("type"), b.get("options"),
                                                                       a.get("type"), a.get("options"))
    sa, sb_ = parse_state(recorded[k].get("prompt") or ""), parse_state(witness[k].get("prompt") or "")
    if sa != sb_:
        return "drift: K=%d state %s, the recording's %s" % (k, sb_, sa)
    took = chosen_label(witness[k])
    if option is not None and took != option:
        return "drift: K=%d the witness took %r, not %r" % (k, took, option)
    if result.get("script_drift") or not result.get("script_played"):
        return "drift: the line did not play: %s" % (result.get("script_drift") or "lines unplayed")
    return "proven" if result.get("status") == "solved" else "failed"


# ------------------------------------------------------------------ what kind of decision a regret step is

def regret_kind(rows, k):
    """The kind of the decision at row k (the taxonomy of 2026-11-06-where-models-lose-drain.md): "land" (the land
    drop with Bloodghast still on the battlefield, its landfall return wasted), "skeleton" (Reassembling Skeleton cast
    or returned for {1}{B}), "yield" (a pass that yields), "response" (a sacrifice while a creature spell or a Skeleton
    return is still on the stack), "feeder" (Carrion Feeder sacrificed: the Zombie Gravecrawler is recast with),
    "drainer" (a drainer sacrificed), else "other"."""
    row = rows[k]
    s = position_state(rows, k)
    label = chosen_label(row) or ""
    if row.get("type") == "play":
        if label.startswith("PASS") and yielded(row):
            return "yield"
        if label.startswith("PLAY LAND") and GHAST in s["bf"]:
            return "land"
        if re.match(r"(CAST|ACTIVATE) Reassembling Skeleton\b", label):
            return "skeleton"
    if row.get("type") == "sacrifice" and not label.startswith("CANCEL"):
        victim = name_of(label)
        if any(x[0] in ("creature", "return") for x in s["stack"]):
            return "response"
        if victim == FEEDER:
            return "feeder"
        if victim in DRAINERS:
            return "drainer"
    return "other"
