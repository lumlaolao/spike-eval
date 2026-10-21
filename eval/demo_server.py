#!/usr/bin/env python3
"""Serve the demo viewer: a recorded game (replay) or a running one (live tail) as decision events for a browser page.

  python3 eval/demo_server.py --game runs/loop/g33                       # replay one game
  python3 eval/demo_server.py --game runs/demo/now --game runs/demo/then # two panes side by side
  python3 eval/demo_server.py --live runs/demo/live                      # tail the newest game-*.jsonl there

Then open http://127.0.0.1:8765/. Stdlib only, no engine: replay needs only the JSONL (and the console log beside it
for the [TALLY] line). Card images come from the demo_cards.py cache (filled by `python3 eval/demo_cards.py prefetch
<run>`); only with --fetch-images does the server send a missing card's name to api.scryfall.com and write the image
into that cache. A live pane re-reads its file on every request, so the page follows a game a JVM is writing
(EVAL_OUT rows are autoflushed per line). The server writes nothing the seat reads, so it cannot change what the seat
sees or decides; with --highlight-file it writes one display-only file that the Forge window's DemoHighlight polls.

What it cannot show: what the JSONL does not hold (the system prompt, library order, and the opponent's hidden hand
unless the game was played with the audience feed, DEMO_FEED=1).
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import demo_cards
import demo_eval
import demo_events
import demo_puzzle

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "web")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
WAIT_S = 20.0


class Pane:
    """One game: a run directory or .jsonl (replay), or a directory a JVM is writing into (live)."""

    def __init__(self, path, live=False, highlight_file=None):
        self.path = path
        self.live = live
        self.highlight_file = highlight_file  # live: where the Forge window's highlights are written
        self.reader = None
        self.log = None
        self.gen = 0            # moves when a live pane switches to a newer game file: the client starts over
        self.lock = threading.Lock()
        self._summary = None
        self._puzzle = None     # a puzzle run's result (demo_puzzle.info) once its row is written; False: not a puzzle
        self.refresh()

    def refresh(self):
        with self.lock:
            # A live directory is re-scanned even after a game ends: the rehearsal game and then the real one are
            # written into the same EVAL_OUT, and the newest file is the one being played.
            if self.reader is None or self.live:
                jsonl, log = demo_events.find_game(self.path) if os.path.exists(self.path) else (None, None)
                if jsonl and (self.reader is None or self.reader.path != jsonl):
                    self.reader = demo_events.Reader(jsonl)
                    self.gen += 1
                    self._summary = None
                    self._puzzle = None
                if jsonl:
                    self.log = log
            if self.reader is not None:
                was_done = self.done
                if self.reader.poll() and self.highlight_file:
                    write_highlights(self.highlight_file, self.reader.events)
                if self.done and not was_done:
                    self._summary = None

    @property
    def done(self):
        """The game row landed, or, for a puzzle, its result row (a puzzle run writes no game row)."""
        return self.reader is not None and (self.reader.builder.game is not None or self.puzzle() is not None)

    def puzzle(self):
        """demo_puzzle.info for this pane's game when it is a finished puzzle attempt, else None. Kept once found; a
        game with no puzzle-results.jsonl beside it is marked not a puzzle and never looked up again."""
        if self._puzzle is None and self.reader is not None:
            run = os.path.dirname(self.reader.path)
            if not os.path.isfile(os.path.join(run, "puzzle-results.jsonl")) and not self.live:
                self._puzzle = False
            else:
                m = demo_events.GAME_FILE.search(os.path.basename(self.reader.path))
                self._puzzle = demo_puzzle.info(run, m.group(1) if m else None)
        return self._puzzle or None

    @property
    def events(self):
        return self.reader.events if self.reader else []

    def age_ms(self):
        """Milliseconds since the JSONL was last written: in live mode, how long the model has been thinking."""
        try:
            return int((time.time() - os.path.getmtime(self.reader.path)) * 1000)
        except (AttributeError, OSError):
            return None

    def summary(self):
        with self.lock:
            cached, key = self._summary, (self.gen, self.done)
        if cached is not None:
            return cached
        built = dict(build_summary(self), gen=key[0])  # the page drops a summary of a game it no longer shows
        with self.lock:
            # Kept only if the game it describes is still this pane's, in the same state (no newer file, no game row
            # landed meanwhile); otherwise it is returned once and rebuilt on the next request.
            if (self.gen, self.done) == key:
                self._summary = built
        return built


def highlight_ids(ev):
    """Engine card ids of the objects a decision highlights (not the engine's own frame), through the feed's handle
    map. Without the audience feed there is no map, and no id."""
    by_handle = ev.get("feed_handles") or {}
    return sorted({by_handle[h] for h, v in (ev.get("highlights") or {}).items()
                   if v.get("kind") != "engine" and h in by_handle})


def write_highlights(path, events):
    """The latest decision's highlights for DemoHighlight (the Forge window). Written whole, then renamed."""
    ev = next((e for e in reversed(events) if e.get("kind") == "decision"), None)
    if ev is None:
        return
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump({"decision": ev["n"], "seat": ev["seat"], "turn": ev["turn"], "ids": highlight_ids(ev)}, f)
    os.replace(tmp, path)


def run_decks(run_dir):
    """The deck names a run_batch run played (its batch-manifest.json), e.g. ["vintage-esper-control", ...]; the game
    row names the seats only ("Identity-A"). None without a manifest."""
    try:
        with open(os.path.join(run_dir, "batch-manifest.json")) as f:
            cond = json.load(f).get("condition") or {}
    except (OSError, ValueError):
        return None
    names = [os.path.splitext(os.path.basename(cond.get(k) or ""))[0] for k in ("deck_a", "deck_b")]
    return names if all(names) else None


def run_arm(run_dir):
    """The side by side arm a run directory holds (demo_compare's arm.json: arm, commit, date), or None."""
    try:
        with open(os.path.join(run_dir, "arm.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_summary(pane):
    """The strip under the board: the game row, the [TALLY] line, match_review counts, and the run's cost."""
    out = {"path": pane.path, "live": pane.live, "jsonl": pane.reader.path if pane.reader else None,
           "log": pane.log, "game": pane.reader.builder.game if pane.reader else None,
           "tally": demo_events.tally(pane.log) if pane.log else {}, "puzzle": pane.puzzle(),
           "decks": run_decks(os.path.dirname(pane.reader.path)) if pane.reader else None,
           "arm": run_arm(os.path.dirname(pane.reader.path)) if pane.reader else None}
    if pane.reader and pane.done:
        try:
            import match_review
            r = match_review.review(pane.reader.path)
            out["review"] = dict(r["counts"])
        except Exception as e:  # the sheet is decoration; a reader error must not break the viewer
            out["review_error"] = repr(e)
        try:
            import cost_report
            c = cost_report.report_dir(os.path.dirname(pane.reader.path), cost_report.load_prices())
            out["cost"] = {"usd": round(c["usd"], 4), "games": c["games"], "calls": c["calls"],
                           "unpriced": sorted(c["unpriced"]), "prices_checked": sorted(c["checked"])}
        except Exception as e:
            out["cost_error"] = repr(e)
    return out


class EvalReport:
    """demo_eval.report for one acceptance dir, built once on first ask (the PUZ bootstraps take a few seconds)."""

    def __init__(self, acceptance_dir=None, hard_dir=None):
        self.acceptance_dir, self.hard_dir = acceptance_dir, hard_dir
        self.lock = threading.Lock()
        self.value = None

    def get(self):
        with self.lock:
            if self.value is None:
                self.value = demo_eval.report(self.acceptance_dir, self.hard_dir)
            return self.value


class Handler(BaseHTTPRequestHandler):
    panes = []
    cards = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json", cache=False, headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        q = parse_qs(url.query)
        if not parts:
            return self._static("index.html")
        if parts[0] == "card" and len(parts) == 2:
            name = unquote(parts[1])
            p = self.cards.get(name) if self.cards else None
            if not p:
                # Only a name Scryfall lacks is cacheable, and says so (X-Image-Miss: the page stops asking for it);
                # offline, a failed or an in-flight fetch may succeed later.
                miss = bool(self.cards and self.cards.is_miss(name))
                return self._send(404, {"error": "no image"}, cache=miss, headers=[("X-Image-Miss", "1")] if miss else [])
            with open(p, "rb") as f:
                return self._send(200, f.read(), "image/jpeg", cache=True)
        if parts[0] != "api":
            return self._static("/".join(parts))
        if parts[1:] == ["games"]:
            out = []
            for i, p in enumerate(self.panes):
                p.refresh()
                out.append({"i": i, "path": p.path, "live": p.live, "events": len(p.events), "done": p.done,
                            "summary": p.summary()})
            return self._send(200, out)
        if parts[1:] == ["eval"]:
            return self._send(200, self.eval_report.get())
        if parts[1:] == ["divergence"]:
            # Two panes of one seed: where the games part (demo_events.divergence). None for one pane.
            if len(self.panes) != 2:
                return self._send(200, None)
            for p in self.panes:
                p.refresh()
            with self.panes[0].lock, self.panes[1].lock:
                a, b = list(self.panes[0].events), list(self.panes[1].events)
            return self._send(200, demo_events.divergence(a, b))
        if len(parts) == 4 and parts[1] == "game" and parts[3] in ("events", "summary"):
            try:
                pane = self.panes[int(parts[2])]
            except (ValueError, IndexError):
                return self._send(404, {"error": "no such game"})
            if parts[3] == "summary":
                pane.refresh()
                return self._send(200, pane.summary())
            try:
                start = max(0, int(q.get("from", ["0"])[0]))
            except ValueError:
                return self._send(400, {"error": "from must be an integer"})
            wait = q.get("wait", ["0"])[0] == "1"
            deadline = time.time() + WAIT_S
            pane.refresh()
            gen = pane.gen
            # A long poll: a live pane holds the request until a new event, the game row, or a newer game file.
            while (wait and pane.live and not pane.done and pane.gen == gen and len(pane.events) <= start
                   and time.time() < deadline):
                time.sleep(0.25)
                pane.refresh()
            with pane.lock:  # one generation's events: a file switch between reads would mix two games
                body = {"from": start, "gen": pane.gen, "jsonl": pane.reader.path if pane.reader else None,
                        "events": pane.events[start:], "total": len(pane.events), "done": pane.done,
                        "live": pane.live, "age_ms": pane.age_ms(), "waiting_for_file": pane.reader is None}
            return self._send(200, body)
        return self._send(404, {"error": "unknown endpoint"})

    def _static(self, rel):
        path = os.path.normpath(os.path.join(WEB, rel))
        if not path.startswith(WEB + os.sep) or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        with open(path, "rb") as f:
            self._send(200, f.read(), TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))


def make_server(panes, port=8765, host="127.0.0.1", cards=None, eval_report=None):
    handler = type("DemoHandler", (Handler,), {"panes": panes, "cards": cards,
                                               "eval_report": eval_report or EvalReport()})
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", action="append", default=[], help="a run directory or game-*.jsonl to replay")
    ap.add_argument("--live", action="append", default=[], help="an EVAL_OUT directory a running game writes into")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--image-cache", default=demo_cards.DEFAULT_CACHE, help="card images (demo_cards.py prefetch fills it)")
    ap.add_argument("--highlight-file", help="live: write the latest decision's highlighted card ids here for the "
                    "Forge window (the JVM reads it with DEMO_HIGHLIGHT_FILE set to the same path)")
    ap.add_argument("--fetch-images", action="store_true",
                    help="fetch a card image missing from the cache from Scryfall (default: serve the cache only)")
    ap.add_argument("--acceptance", help="the eval page's gate: an OUT dir of eval/harness_acceptance.sh (/eval.html)")
    ap.add_argument("--hard-results", help="the eval page's hard-suite pilot: a PUZ check dir (demo-recordings branch: "
                    "puzzles/hard-gen-luna-check)")
    a = ap.parse_args(argv)
    panes = [Pane(p) for p in a.game] + [Pane(p, live=True, highlight_file=a.highlight_file) for p in a.live]
    if not panes and not a.acceptance:
        ap.error("give at least one --game or --live (or --acceptance for the eval page alone)")
    for p in panes:
        if not p.live and p.reader is None:
            ap.error("no game-*.jsonl in %s" % p.path)
    srv = make_server(panes, a.port, a.host, demo_cards.Cache(a.image_cache, offline=not a.fetch_images),
                      EvalReport(a.acceptance, a.hard_results))
    if a.highlight_file:
        # The Forge window's highlights follow the game whether or not a browser is polling.
        def follow():
            while True:
                for p in panes:
                    if p.highlight_file:
                        p.refresh()
                time.sleep(0.25)
        threading.Thread(target=follow, daemon=True).start()
    print("DEMO http://%s:%d/  (%s)" % (a.host, srv.server_address[1], ", ".join(p.path for p in panes)), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
