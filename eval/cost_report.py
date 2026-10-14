#!/usr/bin/env python3
"""What recorded games cost: tokens the provider billed, priced from eval/pricing/prices.json.

Counts every model round in a run directory. A MEMORY_TOOLS=1 game writes one `context_call` row per
round (tool rounds included), and those are counted; a game without them is counted from its
`decision` rows. The two are never added together (they describe the same calls).

It is an ESTIMATE of spend, an upper bound on the input side: it prices every prompt token at the
full rate, and the provider bills cached prompt tokens at a lower one that the JSONL does not
record. It cannot see calls that left no row (a request that timed out is still billed).

  python3 eval/cost_report.py runs/lines/ctl2/native runs/lines/ctl2/native-lookahead
  python3 eval/cost_report.py --plan 16 runs/lines/ctl2/native     # what 16 more games like these would cost
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

PRICES = Path(__file__).resolve().parent / "pricing" / "prices.json"


def load_prices(path=PRICES):
    with open(path) as f:
        return json.load(f)["models"]


def model_id(spec):
    """`openrouter:deepseek/deepseek-v4.1-flash` -> `deepseek/deepseek-v4.1-flash`; None for a non-API spec."""
    if not spec or not str(spec).startswith("openrouter:"):
        return None
    return str(spec).split(":", 1)[1]


def game_usage(path):
    """{'game': the game row or None, 'tiers': {(seat, tier): [calls, prompt_tokens, completion_tokens]}} for one
    file. `seat` is the row's player name (None on a row that has none)."""
    ctx, dec, game = {}, {}, None

    def add(bucket, tier, r):
        b = bucket.setdefault((r.get("seat"), tier), [0, 0, 0])
        b[0] += 1
        b[1] += max(0, r.get("prompt_tokens") or 0)
        b[2] += max(0, r.get("completion_tokens") or 0)
    with open(path, errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = r.get("t")
            if t == "game":
                game = r
            elif t == "context_call":
                add(ctx, r.get("tier") or "?", r)
            elif t == "decision" and r.get("tier") in ("smart", "fast"):
                add(dec, r["tier"], r)
    return {"game": game, "tiers": ctx or dec}


def price_of(prices, spec):
    return prices.get(model_id(spec) or "")


def identities_of(g, seat):
    """The deck identities ('a', 'b') a row's seat (player name) may belong to. The game row names them
    deck_a/deck_b, a batch manifest name_a/name_b. Both, when both identities carry that name (an
    exploratory batch may give them one); only 'a' for a row with no seat or one matching neither."""
    a = seat is not None and seat in (g.get("deck_a"), g.get("name_a"))
    b = seat is not None and seat in (g.get("deck_b"), g.get("name_b"))
    return ("a", "b") if a and b else ("b",) if b else ("a",)


def report_dir(run_dir, prices):
    out = {"dir": run_dir, "games": 0, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0, "unfinished": 0,
           "unpriced": set(), "models": set(), "checked": set()}
    # A game that was killed or capped has no game row but was billed all the same: its tiers are
    # priced from the batch manifest, and it is counted as unfinished.
    manifest = {}
    try:
        with open(os.path.join(run_dir, "batch-manifest.json")) as f:
            manifest = json.load(f).get("condition") or {}
    except (OSError, ValueError):
        pass
    for path in sorted(glob.glob(os.path.join(run_dir, "*.jsonl"))):
        u = game_usage(path)
        g = u["game"] or manifest
        out["games"] += 1 if u["game"] else 0
        out["unfinished"] += 0 if u["game"] or not u["tiers"] else 1
        for (seat, tier), (calls, pin, pout) in u["tiers"].items():
            out["calls"] += calls
            out["prompt_tokens"] += pin
            out["completion_tokens"] += pout
            # Priced by the calling seat's own spec: under seat A's, a model-B-vs-ai batch reads as $0 and
            # --budget-usd never stops it. A name both seats share is priced at the dearer spec (an upper bound).
            specs = [g.get("%s_%s" % ("smart" if tier == "smart" else "fast", i)) for i in identities_of(g, seat)]
            priced = {s: p for s, p in ((s, price_of(prices, s)) for s in specs) if p is not None}
            out["unpriced"].update(model_id(s) for s in specs if s not in priced and model_id(s))
            if not priced:
                continue
            usd = {s: pin * p["prompt"] + pout * p["completion"] for s, p in priced.items()}
            spec = max(usd, key=usd.get)
            out["models"].add(model_id(spec))
            out["checked"].add(priced[spec].get("checked", "?"))
            out["usd"] += usd[spec]
    return out


def fmt(r):
    n = r["games"] + r["unfinished"]
    per = r["usd"] / n if n else 0.0
    line = "%s: %d games%s, %d model calls, %.2fM prompt + %.2fM completion tokens -> $%.2f ($%.3f per game started)" % (
        r["dir"].rstrip("/"), r["games"], (" + %d unfinished" % r["unfinished"]) if r["unfinished"] else "", r["calls"], r["prompt_tokens"] / 1e6, r["completion_tokens"] / 1e6, r["usd"], per)
    if r["unpriced"]:
        line += "  [NOT PRICED: %s -- add to eval/pricing/prices.json]" % ", ".join(sorted(r["unpriced"]))
    return line


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", help="run directories")
    ap.add_argument("--plan", type=int, default=0, metavar="N",
                    help="also print what N more games at each directory's per-game cost would be")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    prices = load_prices()
    rows = [report_dir(d, prices) for d in a.dirs]
    if a.json:
        print(json.dumps([dict(r, unpriced=sorted(r["unpriced"]), models=sorted(r["models"]), checked=sorted(r["checked"]))
                          for r in rows], indent=1))
        return 0
    for r in rows:
        print(fmt(r))
        if a.plan and r["games"] + r["unfinished"]:
            print("    %d more games like these: about $%.2f" % (a.plan, a.plan * r["usd"] / (r["games"] + r["unfinished"])))
    if len(rows) > 1:
        print("TOTAL: %d games + %d unfinished, $%.2f" % (sum(r["games"] for r in rows), sum(r["unfinished"] for r in rows),
                                                          sum(r["usd"] for r in rows)))
    checked = sorted(set(c for r in rows for c in r["checked"]))
    print("prices checked %s; an upper bound on input (cached prompt tokens are billed lower); "
          "calls with no row are not counted" % (", ".join(checked) or "n/a"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
