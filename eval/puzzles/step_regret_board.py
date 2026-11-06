#!/usr/bin/env python3
"""Step regret on a splice board from the board's own model: a verdict at every position of a recorded attempt.

The exact search (step_regret.py) proves the late positions of a failed attempt lost but leaves the middle
`not_found`. On a splice board (puzzle_gen.splice_board) the board's model, puzzle_gen.splice_best, is an exhaustive
search over the same cards; this script reads the state the seat was shown at each decision (its prompt: hand, mana
pool, untapped lands, Thermo-Alchemist, the stack, the opponent's life), values it with that model, and so labels
every position winnable or not. It is a MODEL verdict, exact only when the model's assumptions hold:

  1. Every card is one of puzzle_gen.SPLICE_CARDS, all mana is red, the only mana is the pool, untapped Mountains
     and Desperate Ritual (the pool lasts the main phase), and nothing else deals damage.
  2. The opponent has no cards, mana or permanents (cannot interact), and the turn ending loses.
  3. Every spell can wait for the stack to resolve before the next is cast (no line gains by casting in response),
     and a splice is offered when the spell plus its splices are payable from the pool and untapped lands.
  4. Thermo-Alchemist pings once per untap; each instant or sorcery cast untaps it (a pending untap trigger over an
     untapped Alchemist is one more ping, taken in response).
  5. A spell being cast at a target or splice question is committed (CANCEL is not a recorded option).

Anything outside the model (another card, an unknown stack item) is refused (NotModeled), never guessed. Position K
is the game after the seat's first K decision rows, as in step_regret.py; the end is the last decision applied.

  python3 eval/puzzles/step_regret_board.py <attempt dir or game log>... --search runs/step-regret/x/step-regret.jsonl
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import puzzle_gen as pg  # noqa: E402
import step_regret as sr  # noqa: E402

VERSION = "step-regret-board-v1"
CARDS = pg.SPLICE_CARDS


class NotModeled(Exception):
    """The position holds something the splice model does not cover."""


# ------------------------------------------------------------------ reading the prompt

def _line(prompt, prefix, after=None):
    lines = prompt.splitlines()
    start = 0
    if after:
        start = next((i for i, ln in enumerate(lines) if ln.startswith(after)), None)
        if start is None:
            raise NotModeled("no %r line" % after)
    for ln in lines[start:]:
        if ln.startswith(prefix):
            return ln
    raise NotModeled("no %r line" % prefix)


def _stack_item(text):
    if re.match(r"#\d+ trigger of Thermo-Alchemist\b.*untap Thermo-Alchemist", text):
        return {"dmg": 0, "mana": 0, "untap": True}
    m = re.match(r"#\d+ (spell|ability of) (.+?) @\d+", text)
    if not m or not (m.group(2) in CARDS or m.group(2).startswith("Thermo-Alchemist")):
        raise NotModeled("stack item %r" % text[:80])
    if re.search(r"deals [^0-9]", text):
        raise NotModeled("stack item %r" % text[:80])
    dmg = sum(int(n) for n in re.findall(r"deals (\d+) damage to Opponent\b", text))
    return {"dmg": dmg, "mana": 3 * text.count("adds {R}{R}{R}"), "untap": False}


def parse_state(prompt):
    """The model state from a seat prompt: hand (sorted names), mana (pool + untapped lands), alch (Thermo-Alchemist
    on the battlefield), u (it is untapped), stack (top first: dmg to the opponent, mana added, untap trigger), opp
    (the opponent's life), pending (a spell being cast: set by the decision rows, not the prompt)."""
    you = _line(prompt, "YOU [")
    pool = re.search(r"mana pool ([^|]*)", you).group(1).strip()
    pool = 0 if pool.startswith("empty") else sum(int(n) for n in re.findall(r"\{R:(\d+)\}", pool))
    if pool == 0 and not you.count("mana pool empty"):
        raise NotModeled("mana pool %r" % you)
    hand_ln = _line(prompt, "  hand(", "YOU [")
    body = hand_ln.split("): ", 1)[1] if "): " in hand_ln else ""
    hand = [] if body.startswith("(none)") or not body.strip() else [
        re.match(r"\s*(.+?) (?:\S*\{|@)", c).group(1) for c in re.split(r", (?=[A-Z])", body)]
    for c in hand:
        if c not in CARDS:
            raise NotModeled("card %r" % c)
    board = _line(prompt, "  board:", "YOU [")
    alch = "Thermo-Alchemist" in board
    lands = int(re.search(r"lands: (\d+) untapped", _line(prompt, "  lands:", "YOU [")).group(1))
    opp_ln = _line(prompt, "OPP [")
    opp_board = _line(prompt, "  board:", "OPP [")
    if " hand 0 " not in opp_ln + " " or "(no nonland permanents)" not in opp_board:
        raise NotModeled("the opponent can act: %r" % opp_ln)
    st = _line(prompt, "STACK").split(": ", 1)[1]
    items = [] if st.startswith("(empty)") else [_stack_item(x) for x in re.split(r" \| (?=#\d+ )", st[st.index("#"):])]
    return {"hand": tuple(sorted(hand)), "mana": pool + lands, "alch": alch, "u": alch and "[untapped]" in board,
            "stack": items, "opp": int(re.search(r"OPP \[[^\]]*\] life (-?\d+)", opp_ln).group(1)), "pending": None,
            "ended": False}


# ------------------------------------------------------------------ the model's value

def _splice_options(state, card):
    """The splice sets `card` can carry, payable now (the pool and untapped lands; mana on the stack is not yet there)."""
    g, r, _, _, arcane, _, _, _, _ = CARDS[card]
    rest = list(state["hand"])
    rest.remove(card)
    sets = pg.splice_sets(rest, lambda x: CARDS[x][6] is not None) if arcane else [()]
    return [s for s in sets if g + r + sum(CARDS[x][6] for x in s) <= state["mana"]]


def cast(state, card, splices):
    """The state after casting `card` carrying `splices`: paid, on the stack under the Alchemist's untap trigger."""
    g, r, add, dmg, _, _, _, _, _ = CARDS[card]
    cost = g + r + sum(CARDS[x][6] for x in splices)
    if cost > state["mana"]:
        return None
    hand = list(state["hand"])
    hand.remove(card)
    item = {"dmg": dmg + sum(CARDS[x][8] for x in splices), "mana": add + sum(CARDS[x][7] for x in splices),
            "untap": False}
    top = [{"dmg": 0, "mana": 0, "untap": True}] if state["alch"] else []
    return dict(state, hand=tuple(hand), mana=state["mana"] - cost, stack=top + [item] + state["stack"], pending=None)


def _resolved(state):
    """The position once its stack has resolved, taking a ping in response to each untap trigger the Alchemist can
    use: (damage on the stack and from those pings, mana then, the Alchemist untapped then, the pings first)."""
    st = state["stack"]
    k = sum(1 for x in st if x["untap"])
    extra = k - (0 if state["u"] else 1) if state["alch"] and k else 0
    return (sum(x["dmg"] for x in st) + extra, state["mana"] + sum(x["mana"] for x in st),
            state["u"] or bool(state["alch"] and k), extra)


def value(state):
    """The most damage the position can still deal to the opponent: what is on the stack, the pings its untap
    triggers allow, then puzzle_gen.splice_best from the resolved position."""
    if state.get("ended"):
        return 0
    p = state.get("pending")
    if p:
        sets = [tuple(p["splices"])] if p["splices"] is not None else _splice_options(state, p["card"])
        after = [cast(state, p["card"], s) for s in sets]
        return max([value(a) for a in after if a is not None], default=0)
    dmg, mana, u, _ = _resolved(state)
    (best, _, _), _ = pg.splice_best(list(state["hand"]), mana, state["alch"], untapped=u)
    return dmg + best


def winnable(state):
    return state["opp"] <= 0 or value(state) >= state["opp"]


def after_option(state, label):
    """The state after the seat picks the option `label` at a priority, target or splice question."""
    if state.get("pending") and label.startswith("opponent"):
        return state                                   # the spell's damage goes where the model sends it
    if state.get("pending"):
        raise NotModeled("option %r while casting" % label)
    if label.startswith("PASS"):
        if not state["stack"]:
            return dict(state, ended=True)             # the main phase ends: the turn is over
        top, rest = state["stack"][0], state["stack"][1:]
        return dict(state, stack=rest, opp=state["opp"] - top["dmg"], mana=state["mana"] + top["mana"],
                    u=state["u"] or top["untap"])
    if label.startswith("ACTIVATE Thermo-Alchemist"):
        if not state["u"]:
            raise NotModeled("a tapped Alchemist activated")
        return dict(state, u=False, stack=[{"dmg": 1, "mana": 0, "untap": False}] + state["stack"])
    if label.startswith("ACTIVATE Mountain"):
        return state                                   # pool + 1, one untapped land fewer: the same mana
    m = re.match(r"CAST (.+?) @\d+", label)
    if m and m.group(1) in CARDS and m.group(1) in state["hand"]:
        return dict(state, pending={"card": m.group(1), "splices": None})
    raise NotModeled("option %r" % label)


# ------------------------------------------------------------------ an attempt

def _picks(row):
    """The splice cards a splice row picked (its PICKS, 1-based), () when it asked nothing."""
    opts = row.get("options") or []
    if not opts:
        return ()
    m = re.search(r"PICKS: ([\d, ]*)", row.get("raw_reply") or "")
    if not m:
        raise NotModeled("splice row without PICKS")
    idx = [int(x) for x in re.findall(r"\d+", m.group(1))]
    return tuple(re.match(r"(.+?) \S*\{", opts[i - 1]).group(1) for i in idx)


def chosen_label(row):
    opts = row.get("options") or []
    c = row.get("choice")
    if isinstance(c, int) and 0 <= c < len(opts):
        return opts[c]
    return next((o for o in opts if o.startswith("PASS")), None)   # -1 at a priority: the pass


def position_state(rows, k):
    """The model state before decision row k: its prompt, and the spell it is in the middle of casting."""
    row = rows[k]
    s = parse_state(row.get("prompt") or "")
    if row.get("type") in ("target", "splice"):
        j = k - 1
        while j >= 0 and rows[j].get("type") in ("target", "splice"):
            j -= 1
        m = re.match(r"CAST (.+?) @\d+", chosen_label(rows[j]) or "") if j >= 0 else None
        if not m:
            raise NotModeled("row %d: no cast before the %s question" % (k, row.get("type")))
        between = rows[j + 1:k]
        for b in between:
            if b.get("type") == "target" and not (chosen_label(b) or "").startswith("opponent"):
                raise NotModeled("row %d: an earlier target was not the opponent" % k)
        sp = [b for b in between if b.get("type") == "splice"]
        s["pending"] = {"card": m.group(1), "splices": _picks(sp[-1]) if sp else None}
        # The prompt is the state before the cast: the card is still in hand and nothing is paid.
        if s["pending"]["card"] not in s["hand"]:
            raise NotModeled("row %d: %s not in hand" % (k, s["pending"]["card"]))
    return s


def trajectory(rows):
    """Each position step_regret.py searches (before every model decision, then the end): its K, the decision taken
    there, the model's value and verdict."""
    out = []
    pos = sr.positions(rows)
    for i, k, row in pos:
        if row is None:
            last = pos[-2][1]
            s = after_option(position_state(rows, last), chosen_label(rows[last]))
            label = None
        else:
            s = position_state(rows, k)
            label = chosen_label(row) if row.get("type") != "splice" else "splice: %s" % (
                ", ".join(_picks(row)) or "none")
        v = value(s)
        out.append({"index": i, "k": k, "type": None if row is None else row.get("type"), "choice": label,
                    "opp": s["opp"], "value": v, "winnable": s["opp"] <= 0 or v >= s["opp"],
                    "pending_cast": s.get("pending")})
    return out


def regret(traj):
    """The regret step: the last winnable position before the first lost one. Every position is decided, so the
    step is exact under the model, unless the verdicts are not monotone (a model error: reported, not used)."""
    w = [p["winnable"] for p in traj]
    out = {"regret_index": None, "regret_k": None}
    if all(w):
        return dict(out, status="won_at_end")
    j = w.index(False)
    if any(w[j:]):
        return dict(out, status="non_monotone")
    if j == 0:
        return dict(out, status="lost_at_start")
    return dict(out, status="exact", regret_index=j - 1, regret_k=traj[j - 1]["k"])


def floor_at(rows, k):
    """At decision row k: every option's verdict after it is taken, and the share that keep the win."""
    row = rows[k]
    s = position_state(rows, k)
    opts = row.get("options") or []
    verdicts = {o: winnable(after_option(s, o)) for o in opts}
    kept = [o for o in opts if verdicts[o]]
    return {"k": k, "type": row.get("type"), "n_options": len(opts), "kept": kept,
            "floor": len(kept) / len(opts) if opts else None, "model_label": chosen_label(row),
            "options": {o: verdicts[o] for o in opts}}


# ------------------------------------------------------------------ an engine witness: the model's line, written

def script_line(row):
    """A recorded model decision as a ScriptBridge line: the words its option starts with."""
    if row.get("type") == "splice":
        return "; ".join(_picks(row)) or "none"
    label = chosen_label(row) or ""
    if label.startswith("PASS"):
        return "pass"
    if row.get("type") == "target":
        m = re.match(r"(opponent|you) \(", label)
        if m:
            return m.group(1)
    m = re.match(r"(CAST|ACTIVATE) (.+?)(?: \d+/\d+)? @\d+", label)
    if m:
        return "%s %s" % (m.group(1).lower(), m.group(2))
    raise NotModeled("no script line for %r" % label)


def prefix_lines(rows, k):
    """The seat's model decisions before row k, as script lines (an auto row asks the bridge nothing)."""
    return [script_line(r) for r in rows[:k] if r.get("tier") in sr.MODEL_TIERS]


def winning_line(state):
    """The model's best line from `state` written for the script arm (puzzle_gen.written_line, from the pool, the
    Alchemist and the stack as they are): (lines, damage dealt from here)."""
    if state.get("pending") or state.get("ended"):
        raise NotModeled("a line is written from a priority position only")
    st = state["stack"]
    _, mana, u, extra = _resolved(state)
    (_, _, line), _ = pg.splice_best(list(state["hand"]), mana, state["alch"], untapped=u)
    items = ["ping"] * extra + ["ping" if x is None else ("cast", x[0], x[1]) for x in line]
    stack = [("untap",) if x["untap"] else ("obj", x["mana"], 0, x["dmg"], 0) for x in reversed(st)]
    lines, dealt, _ = pg.written_line(items, list(state["hand"]), 0, state["alch"], False, False, pool=state["mana"],
                                      alch_untapped=state["u"], stack=stack)
    return lines, dealt


def script_for(rows, k):
    """The witness script for position k: the recorded prefix, then the model's line from k. Raises ValueError when
    the model's line does not reach the opponent's life (the model calls the position lost)."""
    state = position_state(rows, k)
    lines, dealt = winning_line(state)
    if dealt < state["opp"]:
        raise ValueError("K=%d: the model's line deals %d of %d" % (k, dealt, state["opp"]))
    first = lines[0].lower()
    opts = rows[k].get("options") or []
    option = next((o for o in opts if o.lower().startswith(first)), None)
    if option is None:   # the line does not fit this menu: the bridge passes (it waits for its window)
        option = next((o for o in opts if o.startswith("PASS")), None)
    pre = prefix_lines(rows, k)
    return {"k": k, "prefix": len(pre), "lines": pre + lines, "dealt": dealt, "opp": state["opp"], "option": option}


def witness_verdict(recorded, witness, k, result, option=None):
    """An engine witness for position k: "proven" when the witness game reached the recording's position k (every
    decision row before it the same question, row k's state the same), took `option` there (when given: the option
    the model says keeps the win) and the script arm played its line to a solve; "failed" when all that held and it
    lost (the model was wrong); else "drift: <why>"."""
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
    if option is not None and chosen_label(witness[k]) != option:
        return "drift: K=%d the witness took %r, not %r" % (k, chosen_label(witness[k]), option)
    if result.get("script_drift") or not result.get("script_played"):
        return "drift: the line did not play: %s" % (result.get("script_drift") or "lines unplayed")
    return "proven" if result.get("status") == "solved" else "failed"


def search_verdicts(files):
    """{source_game: {k: verdict}} from step-regret.jsonl rows; a decided verdict wins over not_found; two decided
    verdicts that disagree are refused."""
    out = {}
    for f in files:
        for r in sr.read_jsonl(f):
            if r.get("t") != "step_regret" or r.get("kind") != "position":
                continue
            d = out.setdefault(r.get("source_game"), {})
            k, v = r.get("k"), r.get("verdict")
            old = d.get(k)
            if old in sr.DECIDED and v in sr.DECIDED and old != v:
                raise SystemExit("%s K=%s: the search said both %s and %s" % (r.get("source_game"), k, old, v))
            if old not in sr.DECIDED:
                d[k] = v
    return out


def agreement(traj, verdicts):
    """The model against every position the search decided, and the trivial baseline (every position lost)."""
    decided = [(p, verdicts[p["k"]]) for p in traj if verdicts.get(p["k"]) in sr.DECIDED]
    dis = [{"k": p["k"], "search": v, "model": "proven" if p["winnable"] else "unwinnable"}
           for p, v in decided if (v == "proven") != p["winnable"]]
    return {"decided": len(decided), "agree": len(decided) - len(dis),
            "baseline_agree": sum(1 for _, v in decided if v == "unwinnable"), "disagreements": dis,
            "decided_proven": sum(1 for _, v in decided if v == "proven")}


def analyse(log, verdicts):
    rows = sr.decision_rows(log)
    traj = trajectory(rows)
    reg = regret(traj)
    out = {"t": "step_regret_board", "version": VERSION, "source_game": str(log), "seed": sr.seed_of(log),
           "positions": traj, "agreement": agreement(traj, verdicts), **reg}
    if reg["status"] == "exact":
        out["floor"] = floor_at(rows, reg["regret_k"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("attempts", nargs="+", help="attempt dirs (each game-<seed>-*.jsonl) or game logs")
    ap.add_argument("--search", nargs="*", default=[], help="step-regret.jsonl files of the exact search")
    ap.add_argument("--out", help="write one JSON row per attempt here")
    ap.add_argument("--scripts", help="write a witness script (the recorded prefix, then the model's line) for every "
                                      "position the model calls winnable into this dir")
    ap.add_argument("--witnesses", help="dir of witness runs (<game stem>-k<K>/, puzzle_baselines.py line --out-dir): "
                                        "each checked against its recording")
    a = ap.parse_args(argv)
    sv = search_verdicts(a.search)
    rows = []
    for att in a.attempts:
        for log in sr.game_logs(att):
            r = analyse(log, sv.get(str(log), {}))
            rows.append(r)
            if a.scripts:
                drows = sr.decision_rows(log)
                for p in r["positions"]:
                    if p["winnable"] and p["type"] == "play":
                        w = script_for(drows, p["k"])
                        f = Path(a.scripts) / ("%s-k%03d.txt" % (Path(log).stem, p["k"]))
                        f.parent.mkdir(parents=True, exist_ok=True)
                        f.write_text("# %s K=%d: %d recorded lines, then the model's line (%d of %d); option %r\n"
                                     "[solution]\n%s\n" % (Path(log).name, p["k"], w["prefix"], w["dealt"], w["opp"],
                                                            w["option"], "\n".join(w["lines"])))
            ag = r["agreement"]
            print("%s seed %s: %s regret K=%s | agree %d/%d (baseline %d/%d)" % (
                Path(log).parent.parent.name, r["seed"], r["status"], r["regret_k"], ag["agree"], ag["decided"],
                ag["baseline_agree"], ag["decided"]))
            for p in r["positions"]:
                print("  K=%-3s %-7s opp %-3s value %-3s %-10s search %-10s %s" % (
                    p["k"], p["type"] or "end", p["opp"], p["value"], "winnable" if p["winnable"] else "lost",
                    sv.get(str(log), {}).get(p["k"], "-"), (p["choice"] or "")[:40]))
            if r.get("floor"):
                f = r["floor"]
                print("  floor at K=%d: %d of %d keep the win %s; the model took %r" % (
                    f["k"], len(f["kept"]), f["n_options"], f["kept"], f["model_label"]))
        if a.witnesses:
            for log in sr.game_logs(att):
                for d in sorted(Path(a.witnesses).glob("%s-k*" % Path(log).stem)):
                    k = int(d.name.rsplit("-k", 1)[1])
                    games = sorted(d.rglob("game-*.jsonl"))
                    res = [r for f in sorted(d.rglob("puzzle-results.jsonl")) for r in sr.read_jsonl(f)]
                    drows = sr.decision_rows(log)
                    v = ("drift: no witness game or result row" if not games or not res else
                         witness_verdict(drows, sr.decision_rows(games[-1]), k, res[-1], script_for(drows, k)["option"]))
                    w = {"t": "step_regret_witness", "version": VERSION, "source_game": str(log), "k": k,
                         "verdict": v, "witness_dir": str(d)}
                    rows.append(w)
                    print("witness %s K=%d: %s" % (Path(log).stem, k, v))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text("".join(json.dumps(r) + "\n" for r in rows))


if __name__ == "__main__":
    main()
