#!/usr/bin/env python3
"""Card images for the demo viewer: Scryfall's official image of a card by its exact English name, cached on disk.

  python3 eval/demo_cards.py prefetch runs/loop/g33     # cache every card the game's prompts show, then run offline
  python3 eval/demo_cards.py get "Black Lotus"          # one card; prints the cached path

The viewer asks the server for /card/<name>; the server answers from the cache only, unless it runs with
--fetch-images, when it fetches a missing name once. A name Scryfall does not know (a token such as "Construct Token", a face-down card) is
remembered as a miss and the viewer draws its text tile. Requests follow Scryfall's API guidance: a User-Agent and
Accept header, and at least 100 ms between requests. Delete misses.json in the cache to ask for misses again.
Stdlib only.

What it cannot do: pick the printing the deck used (Scryfall's default printing is shown), or show anything for a
card the seat could not see: the viewer only asks for names that are in a prompt.
"""
import argparse
import hashlib
import json
import os
import ssl
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
TIMEOUT_S = 5
RETRY_AFTER_S = 300   # a network failure is not a miss, but the name is not tried again for five minutes
# python.org's macOS installer ships without root certificates until its "Install Certificates.command" is run, so
# every fetch fails verification and the viewer silently shows text tiles; say so once.
SSL_HINT = ("[demo_cards] Scryfall's certificate could not be verified, so no card images can be fetched. On macOS "
            "with python.org's Python, run '/Applications/Python 3.x/Install Certificates.command' once (or use "
            "Homebrew's python3); then fetch again.")


def is_cert_error(e):
    return isinstance(e, ssl.SSLCertVerificationError) or isinstance(getattr(e, "reason", None),
                                                                     ssl.SSLCertVerificationError)


def http_fetch(name):
    """The image bytes for a name, or None when Scryfall has no such card (404). Other errors raise."""
    req = urllib.request.Request(API % urllib.parse.quote(name), headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
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
        self.lock = threading.Lock()   # guards the rate gap and the bookkeeping, never held across a request
        self.last = 0.0
        self.failed = {}               # name -> when the network last failed for it
        self.inflight = set()
        self.hinted = False            # the macOS certificate hint, printed once
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

    def is_miss(self, name):
        """True for a name Scryfall answered 404 (a token, an effect): it will never have an image."""
        return (name or "").strip().lower() in self.misses

    def get(self, name):
        """The cached file for a name, fetching it once if allowed; None for a miss, when offline, or on a network
        failure (retried after RETRY_AFTER_S). A slow request holds up only its own name."""
        name = (name or "").strip()
        if not name:
            return None
        p = self.path(name)
        if os.path.isfile(p):
            return p
        key = name.lower()
        with self.lock:
            if (self.offline or key in self.misses or key in self.inflight
                    or time.time() - self.failed.get(key, -RETRY_AFTER_S) < RETRY_AFTER_S):
                return None
            self.inflight.add(key)
            wait = MIN_GAP_S - (time.time() - self.last)
            self.last = max(time.time(), self.last + MIN_GAP_S)
        try:
            if wait > 0:
                time.sleep(wait)
            try:
                data = self.fetch(name)
            except Exception as e:  # the network is optional: no image, the text tile stays
                with self.lock:
                    self.failed[key] = time.time()
                    hint = is_cert_error(e) and not self.hinted
                    self.hinted = self.hinted or hint
                if hint:
                    print(SSL_HINT, file=sys.stderr, flush=True)
                return None
            if data is None:
                with self.lock:
                    self.misses.add(key)
                    try:
                        with open(self.misses_path, "w") as f:
                            json.dump(sorted(self.misses), f)
                    except OSError:
                        pass
                return None
            if not data.startswith(b"\xff\xd8"):  # a proxy's HTML page is not a card image
                with self.lock:
                    self.failed[key] = time.time()
                return None
            tmp = p + ".part"
            try:
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, p)
            except OSError:
                return None
            return p
        finally:
            with self.lock:
                self.inflight.discard(key)


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
    cache = Cache(a.cache, fetch=http_fetch)
    if a.cmd == "get":
        p = cache.get(a.target)
        print(p or "no image for %r" % a.target)
        return 0 if p else 1
    jsonl, _ = demo_events.find_game(a.target)
    if not jsonl:
        ap.error("no game-*.jsonl in %s" % a.target)
    names = names_in_game(jsonl)
    got = [n for n in names if cache.get(n)]
    unfetched = [n for n in names if n not in got and n.strip().lower() in cache.failed]
    missing = [n for n in names if n not in got and n not in unfetched]
    print("cached %d of %d card names in %s%s%s" % (
        len(got), len(names), cache.root,
        ("; not fetched (network, retry later): %d" % len(unfetched)) if unfetched else "",
        ("; no image (tokens, unknown names): " + ", ".join(missing)) if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
