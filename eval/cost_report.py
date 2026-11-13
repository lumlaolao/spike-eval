#!/usr/bin/env python3
"""What recorded games cost: tokens the provider billed, priced from eval/pricing/prices.json.

Counts every model round in a run directory. A MEMORY_TOOLS=1 game writes one `context_call` row per
round (tool rounds included), and those are counted; a game without them is counted from its
`decision` rows. The two are never added together (they describe the same calls).

It is an ESTIMATE of spend. Prompt tokens a row reports as read from the provider's cache
(`cached_tokens`) are priced at the model's `input_cache_read` rate, and tokens written to it
(`cache_write_tokens`) at `input_cache_write`; both default to the full prompt rate when the price file
has no such rate. Rows from before 2026-10-15 carry neither, so their input is an upper bound. It
cannot see calls that left no row (a request that timed out is still billed).

A `claude-cli:` seat (the Claude Code CLI, ClaudeCliBridge) is not priced from tokens: each of its rows carries the
CLI's own `total_cost_usd` as `cost_usd` (its side calls to another model included), and that sum is its spend at
list price and at the budget alike. A CLI process killed at its deadline reports nothing, so it is not counted.

  python3 eval/cost_report.py runs/lines/ctl2/native runs/lines/ctl2/native-lookahead
  python3 eval/cost_report.py --plan 16 runs/lines/ctl2/native     # what 16 more games like these would cost
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
from pathlib import Path

PRICES = Path(__file__).resolve().parent / "pricing" / "prices.json"


def load_prices(path=PRICES):
    with open(path) as f:
        return json.load(f)["models"]


# The spec prefixes that reach openrouter.ai: a chat model, and a decision model on the Decisions API
# (`jev:`, LlmSpikeGui's DecisionsBridge). Both send the game state and need the egress opt-in.
DECISIONS_PREFIX = "jev:"
API_PREFIXES = ("openrouter:", DECISIONS_PREFIX)


# A Claude model through the local Claude Code CLI, answering by structured output (ClaudeCliBridge.PREFIX). It
# sends the game state to Anthropic, not to openrouter.ai. Owner, 2026-10-24: a development lane on the owner's own
# subscription, used only with its explicit opt-in (ClaudeCliBridge.OPT_IN) and never in a benchmark score.
CLI_PREFIX = "claude-cli:"
CLI_OPT_IN = "CLAUDE_CLI_SEAT"
# Specs that ask no model at all (LlmSpikeGui's ScriptBridge, GreedyBridge and ReplayBridge).
ZERO_MODEL_PREFIXES = ("script:", "greedy:", "replay:")
# Every prefix the JVM factory routes (LLMBridgeFactory.ROUTED_PREFIXES; test_cost_report checks the two agree). A spec
# with none of them was the bare Claude CLI text lane, retired on 2026-10-25.
KNOWN_PREFIXES = API_PREFIXES + (CLI_PREFIX,) + ZERO_MODEL_PREFIXES


def is_decisions(spec):
    """True for a decision-model spec: a seat with no lookups, answering typed questions."""
    return str(spec or "").startswith(DECISIONS_PREFIX)


def is_cli(spec):
    """True for a `claude-cli:` spec: priced from the CLI's reported cost, not from tokens."""
    return str(spec or "").startswith(CLI_PREFIX)


def lane_of(spec):
    """The prefix of a seat whose backend gives only the `answer` call (`jev:` or `claude-cli:`: no lookups, no preview,
    no text answers), or None."""
    return DECISIONS_PREFIX if is_decisions(spec) else CLI_PREFIX if is_cli(spec) else None


def is_bare(spec):
    """True for a spec no lane takes: a bare Claude name such as `opus`, once the Claude CLI text lane (retired
    2026-10-25). A recorded game line may still name one."""
    s = str(spec or "")
    return bool(s) and not s.startswith(KNOWN_PREFIXES)


def is_cli_lane(spec):
    """True for a spec the local Claude Code CLI answers or answered: `claude-cli:` (ClaudeCliBridge), or a bare name a
    game recorded before the text lane was retired. Both are the development lane (owner, 2026-10-24)."""
    return is_cli(spec) or is_bare(spec)


def misspelled_cli(spec):
    """True for a spec that names a "cli:" lane (any case) but is not `claude-cli:<model>`: a typo the JVM would
    otherwise send to the bare text lane."""
    s = str(spec or "")
    return "cli:" in s.lower() and not s.startswith(CLI_PREFIX)


def cli_flag_refusal(specs, effort, thinking_tokens):
    """Why --claude-cli-effort / --claude-cli-thinking-tokens are refused for these seats (run_batch.py and
    puzzle_solve.py), or None."""
    cli = any(is_cli(s) for s in specs)
    for flag, v in (("--claude-cli-effort", effort), ("--claude-cli-thinking-tokens", thinking_tokens)):
        if v is not None and not cli:
            return "%s is the Claude CLI's (a %s seat); no other seat reads it" % (flag, CLI_PREFIX)
    if thinking_tokens is not None and thinking_tokens < 1:
        return "--claude-cli-thinking-tokens must be a positive number"
    return None


def cli_env(effort, thinking_tokens):
    """A claude-cli: seat's CLI settings as the JVM's environment, pinned for both tiers ("" is the CLI's own default),
    so the operator's shell (CLAUDE_BRIDGE_EFFORT_SMART, MAX_THINKING_TOKENS ...) never reaches it. run_batch.py and
    puzzle_solve.py both launch with these."""
    return {"CLAUDE_BRIDGE_EFFORT": effort or "", "CLAUDE_BRIDGE_EFFORT_SMART": "", "CLAUDE_BRIDGE_EFFORT_FAST": "",
            "CLAUDE_CLI_THINKING_TOKENS": str(thinking_tokens) if thinking_tokens else ""}


def cli_opt_in_refusal(specs, environ):
    """Why a launch whose model seats name `specs` is refused (the JVM's own refusal, said before anything starts): a
    misspelled "cli:" spec, a retired bare Claude name, or a `claude-cli:` seat without the operator's CLI_OPT_IN=1.
    None otherwise."""
    typo = [s for s in specs if misspelled_cli(s)]
    if typo:
        return "%s names a cli: lane but is not %s<model>" % (", ".join(map(str, typo)), CLI_PREFIX)
    bare = [s for s in specs if is_bare(s)]
    if bare:
        return ("%s: the bare Claude CLI text lane is retired (owner, 2026-10-25); use %s<model> with %s=1"
                % (", ".join(map(str, bare)), CLI_PREFIX, CLI_OPT_IN))
    if not any(is_cli(s) for s in specs) or environ.get(CLI_OPT_IN) == "1":
        return None
    return ("a %s seat runs the Claude Code CLI on the operator's own Claude subscription: it is for local development "
            "only and never part of a benchmark score (owner, 2026-10-24). Set %s=1 to choose it." % (CLI_PREFIX, CLI_OPT_IN))


def budget_prices(prices):
    """The prices a budget is checked at: each model's `max` (its dearest endpoint that takes tools) where it
    states one. A report prices at the list rate; a budget must not be overrun when latency routing lands on a
    dearer endpoint, a priority tier or another provider (provider audit, 2026-10-24)."""
    return {m: dict(p, **(p.get("max") or {})) for m, p in prices.items()}


# A price older than this is reported as stale: a provider's rise makes the budget guard under-count in silence.
PRICE_MAX_AGE_DAYS = 30


def stale_prices(prices, models, today=None):
    """[(model, oldest date, age in days)] for each priced, non-free model of `models` whose list price
    (`checked`) or budget bound (the date in `max_note`) was last checked more than PRICE_MAX_AGE_DAYS ago; a price
    with no readable date counts as stale. Free means $0 at the list rate and at the bound (`max`), which
    run_batch.py's budget reads."""
    today = today or datetime.date.today()
    out = []
    for m in sorted(set(models)):
        p = prices.get(m)
        bound = dict(p or {}, **((p or {}).get("max") or {}))
        if not p or not any(bound.get(k) for k in ("prompt", "completion")):
            continue                                # unpriced, or free at both rates (the loopback fake)
        ages = []
        for d in re.findall(r"\d{4}-\d{2}-\d{2}", "%s %s" % (p.get("checked", ""), p.get("max_note", ""))):
            try:
                ages.append(((today - datetime.date.fromisoformat(d)).days, d))
            except ValueError:
                continue                            # not a calendar date: read as no date
        age, oldest = max(ages) if ages else (None, None)
        if age is None or age > PRICE_MAX_AGE_DAYS:
            out.append((m, oldest or "no date", age))
    return out


def stale_warning(stale):
    """One line naming each stale price, or None."""
    if not stale:
        return None
    return ("WARNING: price older than %d days, re-check eval/pricing/prices.json before trusting a budget: %s"
            % (PRICE_MAX_AGE_DAYS, ", ".join("%s (checked %s%s)" % (m, d, "" if a is None else ", %d days ago" % a)
                                             for m, d, a in stale)))


def model_id(spec):
    """`openrouter:deepseek/deepseek-v4.1-flash` -> `deepseek/deepseek-v4.1-flash`, `jev:typesafe/jev-1.14` ->
    `typesafe/jev-1.14`; None for a non-API spec."""
    if not spec or not str(spec).startswith(API_PREFIXES):
        return None
    return str(spec).split(":", 1)[1]


def game_usage(path):
    """{'game': the game row or None, 'tiers': {(seat, tier): [calls, prompt, completion, cached, cache_write,
    reported_usd, unreported]}} for one file. `seat` is the row's player name (None on a row that has none);
    `reported_usd` sums the rows' own `cost_usd`, and `unreported` is [prompt, completion, cached, cache_write] of the
    rows that carry none."""
    ctx, dec, game = {}, {}, None

    def add(bucket, tier, r):
        b = bucket.setdefault((r.get("seat"), tier), [0, 0, 0, 0, 0, 0.0, [0, 0, 0, 0]])
        tokens = [max(0, r.get(k) or 0) for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "cache_write_tokens")]
        b[0] += 1
        for i, t in enumerate(tokens):
            b[1 + i] += t
            if r.get("cost_usd") is None:
                b[6][i] += t
        b[5] += max(0.0, r.get("cost_usd") or 0.0)
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


def usd_of(p, pin, pout, cached=0, written=0):
    """Dollars for one bucket of tokens. `cached` and `written` are counted inside `pin`."""
    full = max(0, pin - cached - written)
    return (full * p["prompt"] + cached * p.get("input_cache_read", p["prompt"])
            + written * p.get("input_cache_write", p["prompt"]) + pout * p["completion"])


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
    out = {"dir": run_dir, "games": 0, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "usd": 0.0, "unfinished": 0,
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
        for (seat, tier), (calls, pin, pout, cached, written, reported, unreported) in u["tiers"].items():
            out["calls"] += calls
            out["prompt_tokens"] += pin
            out["cached_tokens"] += cached
            out["completion_tokens"] += pout
            # Priced by the calling seat's own spec: under seat A's, a model-B-vs-ai batch reads as $0 and
            # --budget-usd never stops it. A name both seats share is priced at the dearer spec (an upper bound).
            specs = [g.get("%s_%s" % ("smart" if tier == "smart" else "fast", i)) for i in identities_of(g, seat)]
            cli = [s for s in specs if is_cli(s)]
            priced = {s: p for s, p in ((s, price_of(prices, s)) for s in specs) if p is not None}
            out["unpriced"].update(model_id(s) for s in specs if s not in priced and model_id(s))
            usd = {s: usd_of(p, pin, pout, cached, written) for s, p in priced.items()}
            if cli:
                # The CLI's own figure for every row it reported (ClaudeCliBridge), plus a name-sharing token-priced
                # seat's rows (those with no cost_usd) at that seat's price: the two seats' spends add.
                others = [usd_of(p, *unreported) for s, p in priced.items()]
                usd = {cli[0]: reported + max(others or [0.0])}
                priced[cli[0]] = {"checked": "CLI-reported"}
            if not usd:
                continue
            spec = max(usd, key=usd.get)
            out["models"].add(model_id(spec) or spec)
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
    if r.get("cached_tokens"):
        line += "  (%.0f%% of prompt tokens read from cache)" % (100.0 * r["cached_tokens"] / max(1, r["prompt_tokens"]))
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
        print(json.dumps([dict(r, unpriced=sorted(r["unpriced"]), models=sorted(r["models"]), checked=sorted(r["checked"]),
                               stale=[m for m, _, _ in stale_prices(prices, r["models"])])
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
    print("prices checked %s; cached prompt tokens priced at the cache rate where rows record them; "
          "calls with no row are not counted" % (", ".join(checked) or "n/a"))
    warn = stale_warning(stale_prices(prices, [m for r in rows for m in r["models"]]))
    if warn:
        print(warn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
