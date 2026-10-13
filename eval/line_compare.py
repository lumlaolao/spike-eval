#!/usr/bin/env python3
"""Compare arms on own-card line events, using only what every faithful-profile observation version
records (the prompt's `hand(...)` and `board:` lines and the chosen menu option), so a pre-fix arm
and a post-fix arm are counted by the SAME rule. Stdlib only.

Per arm it prints:
  games / wins for identity A, with a Wilson 95% interval (reported, never the decision variable
      at this n)
  non-hand plays: the chosen option names a card that is not in the seat's hand, i.e. an activated
      ability or a graveyard cast, by card
  Cabal Therapy flashbacks, and for each: was Ajani, Nacatl Pariah on the seat's board before it,
      and is Ajani (either face) still on the board at the seat's next decision
  Ajani, Nacatl Avenger: games in which the planeswalker was ever on the seat's board; activations
  model calls per turn and smart/fast split; plays the harness executed from a stated plan
and, for every two arms that played the same (seed, seat swap) games, the paired result: games only
one of them won, with an exact two-sided sign test.

Usage: python3 eval/line_compare.py NAME=run_dir [NAME=run_dir ...]
"""
import glob
import json
import math
import os
import re
import sys
from collections import Counter

HAND = re.compile(r"\n  hand\(\d+\): ([^\n]*)")
BOARD = re.compile(r"\nYOU \[[^\n]*\n(?:  [^\n]*\n)*?  board: ([^\n]*)")


def name_of(label):
    s = re.sub(r"^(CAST|ACTIVATE|PLAY LAND)\s+", "", label or "")
    return re.sub(r"\s+\d+/\d+$", "", re.split(r"\s+@\d+|\s+\[|\s+\d*\{|\s+from |\s+in your |\s+—", s, 1)[0]).strip()


def wilson(k, n):
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def sign_test(a, b):
    """Exact two-sided sign test on a vs b discordant pairs."""
    n, k = a + b, min(a, b)
    if n == 0:
        return 1.0
    return min(1.0, 2.0 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def arm(run_dir):
    out = {"games": 0, "wins": 0, "capped": 0, "turns": 0, "smart": 0, "fast": 0, "plan": 0,
           "nonhand": Counter(), "flash": [], "walker_games": 0, "walker_acts": 0, "versions": set(), "result": {}}
    for path in sorted(glob.glob(os.path.join(run_dir, "*.jsonl"))):
        rows = []
        for line in open(path, errors="replace"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        game = next((r for r in rows if r.get("t") == "game"), None)
        if not game:
            continue
        out["games"] += 1
        out["turns"] += int(game.get("turns") or 0)
        out["wins"] += 1 if game.get("winner") == "A" else 0
        out["capped"] += 1 if "cap" in str(game.get("win_condition", "")).lower() else 0
        out["versions"].add(str(game.get("context_sha")))
        out["result"][(game.get("seed"), bool(game.get("swap")))] = game.get("winner") == "A"
        out["plan"] += sum(1 for r in rows if r.get("t") == "decision" and r.get("tier") == "plan")
        dec = [r for r in rows if r.get("t") == "decision" and r.get("prompt")]
        walker = False
        for i, r in enumerate(dec):
            tier = r.get("tier")
            if tier in ("smart", "fast"):
                out[tier] += 1
            p = r["prompt"]
            b = BOARD.search(p)
            board = b.group(1) if b else ""
            if "Ajani, Nacatl Avenger" in board:
                walker = True
            if r.get("type") != "play":
                continue
            opts, c = r.get("options") or [], r.get("choice")
            if not (isinstance(c, int) and 0 <= c < len(opts)) or opts[c].startswith(("PASS", "PLAY LAND")):
                continue
            n = name_of(opts[c])
            h = HAND.search(p)
            hand = h.group(1) if h else ""
            if n and n not in hand:
                out["nonhand"][n] += 1
                if n == "Ajani, Nacatl Avenger":
                    out["walker_acts"] += 1
                if n == "Cabal Therapy":
                    before = "Ajani, Nacatl Pariah" in board
                    after = None
                    for nxt in dec[i + 1:i + 12]:
                        nb = BOARD.search(nxt["prompt"])
                        if nb and (nxt.get("type") != "sacrifice"):
                            after = "Ajani, Nacatl" in nb.group(1)
                            if "Ajani, Nacatl Avenger" in nb.group(1):
                                walker = True
                            if nxt.get("type") == "play":
                                break
                    out["flash"].append((os.path.basename(path), r.get("turn"), before, after))
        out["walker_games"] += 1 if walker else 0
    return out


def main(argv):
    if not argv or any("=" not in a for a in argv):
        print(__doc__)
        return 2
    arms = [(a.split("=", 1)[0], arm(a.split("=", 1)[1])) for a in argv]
    for name, a in arms:
        lo, hi = wilson(a["wins"], a["games"])
        calls = a["smart"] + a["fast"]
        print("\n== %s ==  context_sha=%s" % (name, ",".join(sorted(a["versions"]))))
        print("  games=%d capped=%d  won by the model=%d (%.0f%%, Wilson 95%% %.0f-%.0f%%)" % (
            a["games"], a["capped"], a["wins"], 100.0 * a["wins"] / max(1, a["games"]), 100 * lo, 100 * hi))
        print("  model calls=%d over %d turns (%.1f/turn); smart=%d fast=%d harness-executed plan steps=%d" % (
            calls, a["turns"], calls / max(1, a["turns"]), a["smart"], a["fast"], a["plan"]))
        print("  Ajani, Nacatl Avenger: on the seat's board in %d of %d games; %d loyalty activations" % (
            a["walker_games"], a["games"], a["walker_acts"]))
        fl = a["flash"]
        with_ajani = [f for f in fl if f[2]]
        kept = [f for f in with_ajani if f[3]]
        print("  Cabal Therapy flashbacks: %d; with Ajani, Nacatl Pariah on board: %d; Ajani still on board after: %d" % (
            len(fl), len(with_ajani), len(kept)))
        for f in fl:
            print("      %s T%s ajani_before=%s ajani_after=%s" % f)
        print("  non-hand plays (abilities / graveyard casts): " + ", ".join(
            "%s x%d" % kv for kv in a["nonhand"].most_common(8)))
    print("\npaired by (seed, seat swap) -- only games both arms played:")
    for i, (n1, a1) in enumerate(arms):
        for n2, a2 in arms[i + 1:]:
            both = sorted(set(a1["result"]) & set(a2["result"]), key=str)
            if not both:
                continue
            only1 = sum(1 for k in both if a1["result"][k] and not a2["result"][k])
            only2 = sum(1 for k in both if a2["result"][k] and not a1["result"][k])
            print("  %s vs %s: %d shared games; only %s won %d, only %s won %d, same result %d; sign test p=%.2f" % (
                n1, n2, len(both), n1, only1, n2, only2, len(both) - only1 - only2, sign_test(only1, only2)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
