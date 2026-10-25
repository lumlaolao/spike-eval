#!/usr/bin/env python3
"""A live line per decision of the model's seat, for someone playing it or watching (play-vs-llm.sh --commentary).

    python3 eval/play_commentary.py runs/human/<time>             # what is there now, then exit
    python3 eval/play_commentary.py runs/human/<time> --follow    # keep printing as the game goes on
    python3 eval/play_commentary.py runs/human/<time> --why       # and the reason the model gave for each

It reads the game JSONL the GUI writes under EVAL_OUT: a decision row of the seat answered by the model (tier smart or
fast) becomes one line, "T3 MAIN1  Seat: CAST Goblin Guide 2/2 {R}". What the model chose in its hidden zones (a scry,
a discard, a search) is shown too, and --why shows its reasoning, which names its hand: it is for watching, not for a
fair game. While a call of the seat's is unanswered (TRANSCRIPTS=1, as play-vs-llm.sh sets) it says the seat is
thinking. It reads only, and calls no network.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

MODEL_TIERS = ("smart", "fast")
REASON_KEYS = ("WHY", "PROFILE", "LINES", "PLAN")


def clean(option):
    """An option without its handle (@7) or bracketed rules text and states."""
    text = re.sub(r"\s*\[[^\]]*\]", "", option)
    return re.sub(r"\s*@\d+\b", "", text).strip()


def _fields(raw):
    out = {}
    for ln in (raw or "").splitlines():
        m = re.match(r"\s*([A-Z_]+):\s*(.*)", ln)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip()
    return out


def _numbers(s):
    return [int(n) for n in re.findall(r"\d+", s or "")]


def answer(row):
    """What the seat chose, in words: the options it named, else its answer line."""
    options = row.get("options") or []
    f = _fields(row.get("raw_reply"))
    for key in ("CHOICE", "PICKS"):
        picked = [options[n - 1] for n in _numbers(f.get(key)) if 1 <= n <= len(options)]
        if picked:
            return " + ".join(clean(o) for o in picked)
    choice = row.get("choice")
    if isinstance(choice, int) and 0 <= choice < len(options):
        return clean(options[choice])
    if choice == -1 and options and options[-1].startswith("PASS"):
        return clean(options[-1])
    for k, v in f.items():
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


def _newest(directory):
    games = glob.glob(os.path.join(directory, "game-*.jsonl"))
    return max(games, key=os.path.getmtime) if games else None


class _Tail:
    """The complete JSON rows appended to one file since the last read; a torn last line waits for the rest."""

    def __init__(self, path):
        self.path, self.offset, self.buf = path, 0, ""

    def rows(self):
        with open(self.path, encoding="utf-8", errors="replace") as f:
            f.seek(self.offset)
            self.buf += f.read()
            self.offset = f.tell()
        *complete, self.buf = self.buf.split("\n")
        for raw in complete:
            try:
                yield json.loads(raw)
            except ValueError:
                continue


def feed(directory, seat, why, follow, out=sys.stdout, poll=1.0):
    """Print the newest game's model decisions for `seat`, and "... is thinking" while a call of its is unanswered
    (the transcript's write-ahead rows, TRANSCRIPTS=1). With follow, keep going, onto the match's next game too."""
    game, http, waiting, announced = None, {}, set(), False
    while True:
        newest = _newest(directory)
        if newest and (game is None or newest != game.path):
            if game:
                out.write("-- next game --\n")
            game = _Tail(newest)
        for path in glob.glob(os.path.join(directory, "transcripts", "http-*.jsonl")):
            for row in http.setdefault(path, _Tail(path)).rows():
                if str(row.get("label", "")).split("/")[0] != seat:
                    continue
                (waiting.add if row.get("phase") == "sent" else waiting.discard)((path, row.get("seq")))
        for row in game.rows() if game else ():
            text = line(row, why, seat)
            if text:
                out.write(text + "\n")
                announced = False
        if waiting and not announced:
            out.write("          ... %s is thinking\n" % seat)
            announced = True
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
