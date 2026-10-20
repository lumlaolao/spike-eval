#!/usr/bin/env python3
"""Card images for the demo viewer: Scryfall's official image of a card by its exact English name, cached on disk.

  python3 eval/demo_cards.py prefetch runs/loop/g33     # cache every card the game's prompts show, then run offline
  python3 eval/demo_cards.py get "Black Lotus"          # one card; prints the cached path

The viewer asks the server for /card/<name>; the server answers from the cache and, unless it runs --offline,
fetches a miss once. A name Scryfall does not know (a token such as "Construct Token", a face-down card) is
remembered as a miss and the viewer draws its text tile. Requests follow Scryfall's API guidance: a User-Agent and
Accept header, and at least 100 ms between requests. Stdlib only.

What it cannot do: pick the printing the deck used (Scryfall's default printing is shown), or show anything for a
card the seat could not see: the viewer only asks for names that are in a prompt.
"""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import demo_events
import demo_state

DEFAULT_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "mtg-forge-demo", "cards")
API = "https://api.scryfall.com/cards/named?exact=%s&format=image&version=normal"
HEADERS = {"User-Agent": "mtg-forge-llm-agent-demo/1.0", "Accept": "image/jpeg,image/*;q=0.9,*/*;q=0.1"}
MIN_GAP_S = 0.1


def http_fetch(name):
    """The image bytes for a name, or None when Scryfall has no such card (404). Other errors raise."""
    req = urllib.request.Request(API % urllib.parse.quote(name), headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


class Cache:
    """name -> a .jpg file under `root`; misses recorded in misses.json so a token is asked for once."""

    def __init__(self, root=DEFAULT_CACHE, fetch=http_fetch, offline=False):
        self.root = root
        self.fetch = fetch
        self.offline = offline
        self.lock = threading.Lock()
        self.last = 0.0
        os.makedirs(root, exist_ok=True)
        self.misses_path = os.path.join(root, "misses.json")
        try:
            with open(self.misses_path) as f:
                self.misses = set(json.load(f))
        except (OSError, ValueError):
            self.misses = set()

    def path(self, name):
        key = hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.root, key + ".jpg")

    def get(self, name):
        """The cached file for a name, fetching it once if allowed; None for a miss or when offline."""
        name = (name or "").strip()
        if not name:
            return None
        p = self.path(name)
        if os.path.isfile(p):
            return p
        if self.offline or name.lower() in self.misses:
            return None
        with self.lock:
            if os.path.isfile(p):
                return p
            wait = MIN_GAP_S - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            try:
                data = self.fetch(name)
            except Exception:  # the network is optional: no image, the text tile stays
                return None
            finally:
                self.last = time.time()
            if data is None:
                self.misses.add(name.lower())
                with open(self.misses_path, "w") as f:
                    json.dump(sorted(self.misses), f)
                return None
            tmp = p + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
            return p


def names_in_game(jsonl):
    """Every card name the game's prompts show: visible objects of every parsed state."""
    names = set()
    for e in demo_events.load(jsonl).events:
        if e.get("kind") == "decision" and e.get("state"):
            for o in demo_state.visible_objects(e["state"]):
                if o.get("name") and not o.get("raw"):
                    names.add(o["name"])
    return sorted(names)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("prefetch", "get"))
    ap.add_argument("target", help="a run directory or game-*.jsonl (prefetch), or a card name (get)")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    a = ap.parse_args(argv)
    cache = Cache(a.cache)
    if a.cmd == "get":
        p = cache.get(a.target)
        print(p or "no image for %r" % a.target)
        return 0 if p else 1
    jsonl, _ = demo_events.find_game(a.target)
    if not jsonl:
        ap.error("no game-*.jsonl in %s" % a.target)
    names = names_in_game(jsonl)
    got = [n for n in names if cache.get(n)]
    missing = [n for n in names if n not in got]
    print("cached %d of %d card names in %s%s" % (len(got), len(names), cache.root,
                                                  ("; no image (tokens, unknown names): " + ", ".join(missing)) if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
