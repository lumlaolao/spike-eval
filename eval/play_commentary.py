#!/usr/bin/env python3
"""A live line per decision of the model's seat, for someone playing it or watching (play-vs-llm.sh --commentary).

    python3 eval/play_commentary.py runs/human/<time>             # what is there now, then exit
    python3 eval/play_commentary.py runs/human/<time> --follow    # keep printing as the game goes on
    python3 eval/play_commentary.py runs/human/<time> --why       # and the reason the model gave for each

It reads the game JSONL the GUI writes under EVAL_OUT: a decision row of the seat answered by the model (tier smart or
fast) becomes one line, "T3 MAIN1  Seat: CAST Goblin Guide 2/2 {R}", naming the move the row records (what the game
did; demo_events.chosen_indexes), never a reply that was not used. What the model chose in its hidden zones (a scry,
a discard, a search) is shown too, and --why shows its reasoning, which names its hand: it is for watching, not for a
fair game. While a call of the seat's is unanswered (TRANSCRIPTS=1, as play-vs-llm.sh sets) it says the seat is
thinking. A match's games share one file; a turn count that starts again marks the next game. It reads only, and
calls no network.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo_events  # noqa: E402

MODEL_TIERS = ("smart", "fast")
REASON_KEYS = ("WHY", "PROFILE", "LINES", "PLAN")


def _fields(raw):
    """The KEY: value lines of a reply, the last of each kept (the answer used is the reply's last)."""
    out = {}
    for ln in (raw or "").splitlines():
        m = re.match(r"\s*([A-Z_]+):\s*(.*)", ln)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def answer(row):
    """What the seat chose, in words: the options the row records, else (a number, a keep) its answer line."""
    options = row.get("options") or []
    picked = [demo_events.norm(options[i]) for i in demo_events.chosen_indexes(row) if i < len(options)]
    if picked:
        return " + ".join(picked)
    if row.get("choice") == -1 and not row.get("picks") and (row.get("n_options") or 0) >= 0 and options:
        return "(declined)"
    for k, v in _fields(row.get("raw_reply")).items():
        if k not in REASON_KEYS:
            return "%s: %s" % (k, v)
    return "(no answer read)"


def line(row, why=False, seat="Seat"):
    """One decision row as a commentary line, or None when it is not a decision the model made at `seat`."""
    if row.get("t") != "decision" or row.get("tier") not in MODEL_TIERS or row.get("seat") != seat:
        return None
    kind = row.get("type")
    who = seat if kind in (None, "play") else "%s %s" % (seat, kind)
    when = " ".join(str(x) for x in ("T%s" % row.get("turn", "?"), row.get("phase")) if x not in (None, "", "null"))
    text = "%s  %s: %s" % (when, who, answer(row))
    reason = _fields(row.get("raw_reply")).get("WHY")
    if why and reason:
        text += "\n          why: " + reason
    return text


class _Rows:
    """The JSON rows appended to one file since the last read (demo_events.read_lines: bytes, a torn row waits)."""

    def __init__(self, path):
        self.path, self.offset = path, 0

    def read(self):
        for raw, _ in demo_events.read_lines(self.path, self):
            try:
                yield json.loads(raw)
            except ValueError:
                continue


def _turn(row):
    try:
        return int(row.get("turn"))
    except (TypeError, ValueError):
        return None


class Commentary:
    """The commentary of one play-vs-llm.sh directory; each poll() returns the lines that are new since the last."""

    def __init__(self, directory, seat, why):
        self.directory, self.seat, self.why = directory, seat, why
        self.game, self.http, self.announced, self.last_turn = None, {}, False, None
        self.sent, self.answered = set(), set()     # (jvm, seq): files are read in any order, so kept apart

    def _thinking(self):
        for path in glob.glob(os.path.join(self.directory, "transcripts", "http-*.jsonl")):
            jvm = os.path.basename(path).rsplit("-", 1)[0]     # a JVM's files share one seq: a retry file continues it
            for row in self.http.setdefault(path, _Rows(path)).read():
                if str(row.get("label", "")).split("/")[0] == self.seat:
                    (self.sent if row.get("phase") == "sent" else self.answered).add((jvm, row.get("seq")))

    def poll(self):
        out = []
        self._thinking()
        if self.game is None:
            path = demo_events.find_game(self.directory)[0]
            self.game = _Rows(path) if path else None
        for row in self.game.read() if self.game else ():
            text = line(row, self.why, self.seat)
            if not text:
                continue
            turn = _turn(row)
            # A match's games share one file; each game's first rows (play/draw, mulligan) are at T0.
            if turn is not None and self.last_turn is not None and turn < self.last_turn:
                out.append("-- next game --")
            if turn is not None:
                self.last_turn = turn
            out.append(text)
            self.announced = False
        if self.sent - self.answered and not self.announced:
            out.append("          ... %s is thinking" % self.seat)
            self.announced = True
        return out


def feed(directory, seat, why, follow, out=sys.stdout, poll=1.0):
    """Print the commentary; with follow, keep polling until interrupted."""
    c = Commentary(directory, seat, why)
    while True:
        for text in c.poll():
            out.write(text + "\n")
        out.flush()
        if not follow:
            return
        time.sleep(poll)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="the game's EVAL_OUT directory (play-vs-llm.sh prints it)")
    ap.add_argument("--seat", default="Seat", help="the model seat's name (default Seat, as play-vs-llm.sh names it)")
    ap.add_argument("--why", action="store_true", help="also print the reason the model gave (names its hand)")
    ap.add_argument("--follow", action="store_true", help="keep printing until interrupted")
    a = ap.parse_args(argv)
    try:
        feed(a.dir, a.seat, a.why, a.follow)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
