#!/usr/bin/env python3
"""Serve the demo viewer: a recorded game (replay) or a running one (live tail) as decision events for a browser page.

  python3 eval/demo_server.py --game runs/loop/g33                       # replay one game
  python3 eval/demo_server.py --game runs/demo/now --game runs/demo/then # two panes side by side
  python3 eval/demo_server.py --live runs/demo/live                      # tail the newest game-*.jsonl there

Then open http://127.0.0.1:8765/. Stdlib only, no engine: replay needs only the JSONL (and the console log beside it
for the [TALLY] line). Card images come from the demo_cards.py cache (filled by `python3 eval/demo_cards.py prefetch
<run>`); only with --fetch-images does the server send a missing card's name to api.scryfall.com and write the image
into that cache. A live pane re-reads its file on every request, so the
page follows a game a JVM is writing (EVAL_OUT rows are autoflushed per line). The server never writes into a run
and never talks to the JVM, so it cannot change what the seat sees or decides.

What it cannot show: what the JSONL does not hold (the system prompt, the opponent's hidden hand, library order).
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
import demo_events

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "web")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
WAIT_S = 20.0


class Pane:
    """One game: a run directory or .jsonl (replay), or a directory a JVM is writing into (live)."""

    def __init__(self, path, live=False):
        self.path = path
        self.live = live
        self.reader = None
        self.log = None
        self.gen = 0            # moves when a live pane switches to a newer game file: the client starts over
        self.lock = threading.Lock()
        self._summary = None
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
                if jsonl:
                    self.log = log
            if self.reader is not None:
                was_done = self.done
                self.reader.poll()
                if self.done and not was_done:
                    self._summary = None

    @property
    def done(self):
        return self.reader is not None and self.reader.builder.game is not None

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
        if self._summary is None:
            self._summary = build_summary(self)
        return self._summary


def build_summary(pane):
    """The strip under the board: the game row, the [TALLY] line, match_review counts, and the run's cost."""
    out = {"path": pane.path, "live": pane.live, "jsonl": pane.reader.path if pane.reader else None,
           "log": pane.log, "game": pane.reader.builder.game if pane.reader else None,
           "tally": demo_events.tally(pane.log) if pane.log else {}}
    if pane.reader and pane.done:
        try:
            import match_review
            r = match_review.review(pane.reader.path)
            out["review"] = {k: v for k, v in r["counts"].items()}
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


class Handler(BaseHTTPRequestHandler):
    panes = []
    cards = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json", cache=False):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        q = parse_qs(url.query)
        if not parts:
            return self._static("index.html")
        if parts[0] == "card" and len(parts) == 2:
            p = self.cards.get(unquote(parts[1])) if self.cards else None
            if not p:
                # Cacheable: the page re-renders on every live event and must not ask again for a token's image.
                return self._send(404, {"error": "no image"}, cache=True)
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


def make_server(panes, port=8765, host="127.0.0.1", cards=None):
    handler = type("DemoHandler", (Handler,), {"panes": panes, "cards": cards})
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", action="append", default=[], help="a run directory or game-*.jsonl to replay")
    ap.add_argument("--live", action="append", default=[], help="an EVAL_OUT directory a running game writes into")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--image-cache", default=demo_cards.DEFAULT_CACHE, help="card images (demo_cards.py prefetch fills it)")
    ap.add_argument("--fetch-images", action="store_true",
                    help="fetch a card image missing from the cache from Scryfall (default: serve the cache only)")
    a = ap.parse_args(argv)
    panes = [Pane(p) for p in a.game] + [Pane(p, live=True) for p in a.live]
    if not panes:
        ap.error("give at least one --game or --live")
    for p in panes:
        if not p.live and p.reader is None:
            ap.error("no game-*.jsonl in %s" % p.path)
    srv = make_server(panes, a.port, a.host, demo_cards.Cache(a.image_cache, offline=not a.fetch_images))
    print("DEMO http://%s:%d/  (%s)" % (a.host, srv.server_address[1], ", ".join(p.path for p in panes)), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
