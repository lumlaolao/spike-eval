#!/usr/bin/env python3
"""Turn one game's EVAL_OUT JSONL into the event list the demo viewer plays: one event per decision with the model
calls, tool requests, provider reasoning and answer that produced it, one per priority window passed without asking,
and the game summary.

Joining: decision rows carry no call id. The harness writes every `context_call` / `context_tool` row of an ask
before that ask's `decision` row, by the same seat, through one autoflushing writer, so the rows of a seat since its
previous decision row belong to its next decision row (pinned on g33, one llm seat, and on a two-llm-seat mirror in
eval/test_demo_events.py).

What it measures: nothing; every field on screen is a field of the file, or a count of them. What it cannot do: show
the system prompt (not stored per row), the opponent's hidden hand, or a row the JVM had not flushed yet (`Reader`
parses complete lines only, so a half-written last line waits for its newline).
"""
import json
import os
import re
import sys

import demo_highlights
import demo_state
import match_review

TOOL_CALL = re.compile(r"^TOOL CALL (\w+) (.*)$", re.S)


def parse_tool_request(request):
    """'TOOL CALL inspect {"target": "X"}' -> ('inspect', {...}); a text-protocol 'MEMORY SEARCH 0 turn' -> name only."""
    request = request or ""
    m = TOOL_CALL.match(request.strip())
    if m:
        try:
            return m.group(1), json.loads(m.group(2))
        except ValueError:
            return m.group(1), {"_raw": m.group(2)}
    words = request.split()
    return ("_".join(words[:2]).lower() if words else "?"), {"_raw": request}


def chosen_indexes(row):
    """The 0-based options the answer selected, as the row records them; [] for a decline or no blocks, and for a
    number ask (`n_options` -1), whose `choice` is the number chosen, not an option."""
    if (row.get("n_options") or 0) < 0:
        return []
    picks = row.get("picks")
    if isinstance(picks, list):
        return [p for p in picks if isinstance(p, int)]
    if row.get("type") == "blockers":
        return []
    choice = row.get("choice")
    options = row.get("options") or []
    if isinstance(choice, int) and choice >= 0:
        return [choice] if choice < len(options) else []
    for i, label in enumerate(options):
        if str(label).startswith("PASS"):
            return [i]
    return []


class Builder:
    """Accumulates rows in file order and emits events. One instance per game file."""

    def __init__(self):
        self.events = []
        self.pending = {}       # seat -> list of calls (each with its tools) since that seat's last decision
        self.last_state = {}    # seat -> index of the last decision event with a parsed state
        self.plans = {}         # seat -> its latest set_plan arguments
        self.start = None       # byte offset of the row being added (Reader sets it)
        self.feed_at = None     # offset -> the audience feed's snapshot written before it (Reader sets it)
        self.handles_at = None  # (offset, seat) -> engine id -> handle, from the feed (Reader sets it)
        self.game = None
        self.decisions = 0

    def _calls(self, seat):
        return self.pending.setdefault(seat, [])

    def add(self, row, start=None):
        self.start = start
        t = row.get("t")
        seat = row.get("seat")
        if t == "context_call":
            self._calls(seat).append({
                "call": row.get("call"), "round": row.get("round"), "tier": row.get("tier"),
                "elapsed_ms": row.get("elapsed_ms"), "provider": row.get("provider"),
                "reasoning": row.get("reasoning") or "", "reply": row.get("raw_reply") or "",
                "prompt_tokens": row.get("prompt_tokens"), "completion_tokens": row.get("completion_tokens"),
                "cached_tokens": row.get("cached_tokens"), "tools": []})
        elif t == "context_tool":
            calls = self._calls(seat)
            target = next((c for c in reversed(calls) if c["call"] == row.get("call")), None)
            if target is None:
                target = {"call": row.get("call"), "round": None, "tier": None, "elapsed_ms": None,
                          "provider": None, "reasoning": "", "reply": "", "tools": [], "synthetic": True}
                calls.append(target)
            name, args = parse_tool_request(row.get("request"))
            target["tools"].append({"name": name, "args": args, "result": row.get("result") or "",
                                    "request": row.get("request") or ""})
        elif t == "decision":
            self._decision(row)
        elif t == "window":
            # One event per row, never changed after it is sent: a live client fetches only new events, so a merged
            # marker would keep its first count. The viewer collapses runs of them for display.
            self.events.append({"kind": "window", "i": len(self.events), "seat": seat, "turn": row.get("turn"),
                                "phase": row.get("phase"), "reason": row.get("reason")})
        elif t == "game":
            self.game = summarize_game(row)
            self.events.append(dict(self.game, kind="game", i=len(self.events)))

    def _decision(self, row):
        seat = row.get("seat")
        calls = self.pending.pop(seat, [])
        tools = [tool for c in calls for tool in c["tools"]]
        answers = [tool for tool in tools if tool["name"] == "answer"]
        accepted = [a for a in answers if a["result"].startswith("ANSWER ACCEPTED")]
        answer = (accepted or answers or [None])[-1]
        plans = [tool["args"] for tool in tools if tool["name"] == "set_plan"]
        prompt = row.get("prompt")
        state = demo_state.parse_prompt(prompt) if prompt else None
        ev = {
            "kind": "decision", "i": len(self.events), "n": self.decisions, "seat": seat,
            "turn": row.get("turn"), "phase": row.get("phase"), "type": row.get("type"), "tier": row.get("tier"),
            "choice": row.get("choice"), "picks": row.get("picks"), "options": row.get("options") or [],
            "chosen": chosen_indexes(row), "latency_ms": row.get("latency_ms"),
            # forced / TAKEN / LOST / other: an auto row is not always forced (bridge-down, unparsed, no budget).
            "auto_class": match_review.classify_auto(row) if row.get("tier") == "auto" else None,
            "prompt_tokens": row.get("prompt_tokens"), "completion_tokens": row.get("completion_tokens"),
            "cached_tokens": row.get("cached_tokens"), "fallback": row.get("fallback"),
            "attempts": row.get("attempts"), "answer_invalid": row.get("answer_invalid"),
            "first_failed_reply": row.get("first_failed_reply"), "answer_via": row.get("answer_via"),
            "raw_reply": row.get("raw_reply"), "calls": calls,
            "answer": answer["args"] if answer else None, "plan": plans[-1] if plans else None,
            "prompt": prompt, "state": state,
            "state_from": len(self.events) if state else self.last_state.get(seat),
        }
        # The engine-truth snapshot (DemoFeed) written before this row: what was really in both hands when the seat
        # was asked. Audience only: nothing of it reaches the seat or the highlights.
        ev["feed"] = self.feed_at(self.start) if self.feed_at and self.start is not None else None
        # engine id -> this seat's @handle at that moment, so the seat's highlights land on the true cards.
        ev["feed_handles"] = self.handles_at(self.start, seat) if self.handles_at and self.start is not None else None
        if state:
            self.last_state[seat] = len(self.events)
        if ev["plan"]:
            self.plans[seat] = ev["plan"]
        shown = state if state else (self.events[ev["state_from"]]["state"] if ev["state_from"] is not None else None)
        ev["highlights"] = demo_highlights.compute(ev, shown, self.plans.get(seat))
        self.events.append(ev)
        self.decisions += 1


GAME_FIELDS = ("seed", "swap", "deck_a", "deck_b", "controller_a", "controller_b", "smart_a", "smart_b", "fast_a",
               "fast_b", "winner", "turns", "win_condition", "duration_ms", "harness_epoch", "observation_version",
               "answer_tool", "answer_tool_version", "native_tools", "native_tools_version", "lookahead",
               "lookahead_version", "memory_tools", "rules_tool", "context_profile", "context_sha", "overlay_sha",
               "deck_sha", "engine_sha", "one_tier", "turn_chat", "one_chat", "no_coaching")


def summarize_game(row):
    """The game row's fields the summary strip shows; absent fields (older rows) stay absent."""
    return {k: row[k] for k in GAME_FIELDS if k in row}


class Reader:
    """Reads a JSONL from a byte offset and feeds complete lines to a Builder: the live tail and the replay share it."""

    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.builder = Builder()
        self.bad_lines = 0
        # DemoFeed writes feed-<seed>-<ms>.jsonl beside game-<seed>-<ms>.jsonl when DEMO_FEED=1.
        d, name = os.path.split(path)
        self.feed = Lines(os.path.join(d, "feed-" + name[len("game-"):])) if name.startswith("game-") else None
        self.builder.feed_at = self.feed_at
        self.builder.handles_at = self.handles_at

    def _index_at(self, offset):
        rows = self.feed.rows if self.feed else []
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if (rows[mid].get("jsonl_offset") or 0) <= offset:
                lo = mid + 1
            else:
                hi = mid
        return lo - 1

    def feed_at(self, offset):
        """The last feed snapshot written while the game JSONL was at most `offset` bytes long."""
        i = self._index_at(offset)
        return self.feed.rows[i] if i >= 0 else None

    def handles_at(self, offset, seat):
        """engine id -> @handle for `seat` as of that snapshot. The feed writes a seat's map only when it grows."""
        for i in range(self._index_at(offset), -1, -1):
            m = (self.feed.rows[i].get("handles") or {}).get(seat)
            if m is not None:
                return {str(eid): int(h) for h, eid in m.items()}
        return None

    def poll(self):
        """Parse every complete line appended since the last poll; return the number of new events. The feed is read
        first: a decision row is written after every feed row that precedes it."""
        if self.feed:
            self.feed.poll()
        before = len(self.builder.events)
        for line, at in read_lines(self.path, self):
            try:
                self.builder.add(json.loads(line.decode("utf-8")), at)
            except ValueError:
                self.bad_lines += 1
        return len(self.builder.events) - before

    @property
    def events(self):
        return self.builder.events


def read_lines(path, cursor):
    """Complete lines appended to `path` since `cursor.offset`, each with its start offset; advances the cursor. A
    half-written last line waits for its newline."""
    try:
        with open(path, "rb") as f:
            f.seek(cursor.offset)
            data = f.read()
    except OSError:
        return []
    end = data.rfind(b"\n")
    if end < 0:
        return []
    out, at = [], cursor.offset
    for line in data[:end + 1].split(b"\n")[:-1]:
        if line.strip():
            out.append((line, at))
        at += len(line) + 1
    cursor.offset += end + 1
    return out


class Lines:
    """A JSONL of plain rows read incrementally (the audience feed)."""

    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.rows = []

    def poll(self):
        for line, _ in read_lines(self.path, self):
            try:
                self.rows.append(json.loads(line.decode("utf-8")))
            except ValueError:
                pass


def load(path):
    r = Reader(path)
    r.poll()
    return r


def tally(log_path):
    """The last [TALLY] line of each seat in a console log (the integrity counters: ai=0, textParses=0)."""
    out = {}
    try:
        with open(log_path, errors="replace") as f:
            for line in f:
                m = re.match(r"^\[(.*?)\] \[TALLY\] (.*)$", line.strip())
                if m:
                    out[m.group(1)] = dict(kv.split("=", 1) for kv in m.group(2).split() if "=" in kv)
    except OSError:
        pass
    return out


def _last_row(path):
    """The last complete JSON line of a file (the game row of a finished game), or None."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            lines = [x for x in f.read().split(b"\n") if x.strip()]
        return json.loads(lines[-1].decode("utf-8")) if lines else None
    except (OSError, ValueError):
        return None


HANDLE = re.compile(r"\s*@\d+")


def _norm(option):
    """An option without its viewer-local @handles, which two harness versions number differently."""
    return HANDLE.sub("", str(option)).strip()


def divergence(a, b):
    """Where two games of the same seed and decks part: the first decision, in order, where the menus differ or the
    same menu got a different answer. {"a": i, "b": i, "reason": ...} (decision indexes into each game's decisions),
    or None when every decision both games reached matched."""
    ga = next((e for e in a if e.get("kind") == "game"), None)
    gb = next((e for e in b if e.get("kind") == "game"), None)
    cond = lambda g: tuple(g.get(k) for k in ("seed", "deck_a", "deck_b"))
    if ga and gb and cond(ga) != cond(gb):
        return {"a": 0, "b": 0, "reason": "not a side by side: the seeds or decks differ", "unpaired": True}
    da = [e for e in a if e.get("kind") == "decision"]
    db = [e for e in b if e.get("kind") == "decision"]
    for n, (x, y) in enumerate(zip(da, db)):
        spot = lambda d: (str(d.get("turn")), str(d.get("phase")), d.get("seat"), d.get("type"))
        if spot(x) != spot(y) or [_norm(o) for o in x.get("options") or []] != [_norm(o) for o in y.get("options") or []]:
            return {"a": n, "b": n, "reason": "the engine asked different questions (the games had already parted)"}
        pick = lambda d: sorted(_norm((d.get("options") or [])[c]) for c in d.get("chosen") or []
                                if 0 <= c < len(d.get("options") or []))
        if pick(x) != pick(y):
            return {"a": n, "b": n, "reason": "same question, different answer"}
    return None


GAME_FILE = re.compile(r"game-(-?\d+)-(\d+)\.jsonl$")


def _started(name):
    m = GAME_FILE.match(name)
    return int(m.group(2)) if m else 0


def find_game(path):
    """A run directory or a .jsonl -> (jsonl path, console log path or None). The newest game-*.jsonl in a directory.

    run_batch.py names the console log game-<seed>-<0|1>.log by seat swap: the game row's `swap` picks it (as
    match_review does); before the game row exists, the log written closest in time to the jsonl."""
    if os.path.isdir(path):
        # Newest by the start time in the name (game-<seed>-<epoch ms>.jsonl), not by the last write: two games
        # written at once would otherwise swap places on every write.
        files = sorted((f for f in os.listdir(path) if f.startswith("game-") and f.endswith(".jsonl")),
                       key=lambda f: (_started(f), os.path.getmtime(os.path.join(path, f))))
        if not files:
            return None, None
        jsonl = os.path.join(path, files[-1])
    else:
        jsonl = path
    d = os.path.dirname(jsonl) or "."
    m = GAME_FILE.match(os.path.basename(jsonl))
    seed = m.group(1) if m else None
    logs = [f for f in os.listdir(d) if f.endswith(".log") and seed and f.startswith("game-%s-" % seed)]
    if not logs:
        return jsonl, None
    game = _last_row(jsonl)
    if game and game.get("t") == "game" and "swap" in game:
        want = "game-%s-%d.log" % (seed, 1 if game.get("swap") else 0)
        return jsonl, (os.path.join(d, want) if want in logs else None)
    t = os.path.getmtime(jsonl)
    return jsonl, os.path.join(d, min(logs, key=lambda f: abs(os.path.getmtime(os.path.join(d, f)) - t)))


if __name__ == "__main__":
    jsonl, log = find_game(sys.argv[1])
    r = load(jsonl)
    kinds = {}
    for e in r.events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(json.dumps({"jsonl": jsonl, "log": log, "events": kinds, "tally": tally(log) if log else {}}, indent=1))
