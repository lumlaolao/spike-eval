#!/usr/bin/env python3
"""A second model reads each decision the seat made and says whether it made sense.

match_review.py lists what the harness took from the model. This lists what the model did with what
it was given: a play that does not follow from the state or from the seat's own plan (Dark Ritual
cast, then a pass with the mana unspent), an attack into a lethal block. It also asks whether the
QUESTION was sound, which separates a harness defect (the options did not fit the state) from a
model mistake.

It is an audit, not a score. An LLM judge is a model with opinions: its verdicts are candidates to
read, and a confirmed blunder is a puzzle candidate (the harness-audit skill). Never report its counts
as play strength.

For each judged decision the judge sees the seat's full prompt (the state and the question exactly as
the seat saw it), the decisions the seat already made this turn, the option it chose, and its own
reasoning. It answers one JSON object: verdict ok | questionable | blunder, a better option number if
any, a one-sentence reason, and question_ok with the problem when the question itself looked wrong.

  python3 eval/blunder_judge.py runs/frontier/luna --budget-usd 0.25           # judge: claude-haiku-5.5
  python3 eval/blunder_judge.py runs/loop/g01 --estimate        # what judging would cost; no calls

Every call sends a seat's game state to openrouter.ai: it requires OPENROUTER_ALLOW_EGRESS=1, which
this script never sets. Stdlib only; writes <run>/judge-<model>.jsonl and prints a sheet.
"""
import argparse
import glob
import json
import os
import re
import sys
import urllib.request

import cost_report

VERSION = "blunder-judge-v2"   # v2: the judge is given every named card's rules text
DEFAULT_TYPES = ("play", "attackers", "blockers", "targets", "mulligan")
URL = "https://openrouter.ai/api/v1/chat/completions"

INSTRUCTIONS = (
    "You are reviewing one decision a player made in a game of Magic: The Gathering, played in the Forge "
    "rules engine. Below is exactly what the player was shown (the game state, from its own seat, and the "
    "question with its numbered options), what it had already done this turn, the option it chose, and "
    "its own reasoning.\n"
    "Judge the decision as a strong player would, from what the player could see (not hidden cards). "
    "verdict: \"ok\" (a reasonable play), \"questionable\" (a clearly better option existed but this is "
    "defensible), or \"blunder\" (it throws away resources, tempo or the game for no reason the state "
    "supports; e.g. making mana and then passing with it unspent, attacking into a lethal block). "
    "Judge the play, not the outcome. Also say whether the QUESTION itself was sound: question_ok is false "
    "when the options do not fit the state shown, an obvious legal option is missing, or the text is "
    "garbled; then say what is wrong.\n"
    "Answer with ONE JSON object and nothing else: "
    "{\"verdict\": \"ok|questionable|blunder\", \"better\": <option number or null>, "
    "\"reason\": \"<one sentence>\", \"question_ok\": true|false, \"question_problem\": \"<empty or one sentence>\"}")


def rows_of(path):
    out = []
    with open(path, errors="replace") as f:
        for n, line in enumerate(f, 1):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            r["_line"] = n
            out.append(r)
    return out


def judged(rows, types=DEFAULT_TYPES):
    """The model's own decisions worth judging: a real choice (more than one option), answered by the
    model (smart/fast tier), with the prompt recorded."""
    for r in rows:
        if r.get("t") != "decision" or r.get("tier") not in ("smart", "fast"):
            continue
        if types and r.get("type") not in types:
            continue
        if (r.get("n_options") or 0) < 2 or not r.get("prompt"):
            continue
        yield r


def chosen_label(r):
    opts, ch = r.get("options") or [], r.get("choice")
    if r.get("type") in ("attackers", "blockers") or not isinstance(ch, int):
        return str(r.get("raw_reply") or "").strip().splitlines()[-1:] or ["(see reasoning)"]
    if ch < 0:
        return ["PASS / none (no option taken)"]
    return ["%d) %s" % (ch + 1, opts[ch])] if ch < len(opts) else ["option %d" % (ch + 1)]


def turn_so_far(rows, r):
    """The seat's earlier decisions in the same turn, oldest first: what it chose and why, one line each."""
    out = []
    for p in rows:
        if p is r:
            break
        if p.get("t") == "decision" and p.get("seat") == r.get("seat") and p.get("turn") == r.get("turn") \
                and p.get("tier") in ("smart", "fast", "auto"):
            why = " ".join(str(p.get("raw_reply") or "").split())[:200]
            out.append("- %s %s: chose %s%s" % (p.get("phase"), p.get("type"), chosen_label(p)[0][:120],
                                                ("  (its reason: %s)" % why) if why else ""))
    return out[-8:]


_ORACLE = None
# A card name as the seat's prompt prints it: before its cost, P/T, count, tag list or @handle.
NAME_AT = re.compile(r"([A-Z][A-Za-z',.\- ]{1,40}?)(?= \d*\{| \d+/\d+| x\d| \[| @\d|,|\)|$)", re.M)


def oracle_index(forge_home=None):
    """name -> Oracle text from the pinned Forge checkout's card scripts; {} when they are absent."""
    global _ORACLE
    if _ORACLE is None:
        _ORACLE = {}
        root = os.path.join(forge_home or os.environ.get("FORGE_HOME", os.path.expanduser("~/code/forge")),
                            "forge-gui", "res", "cardsfolder")
        for path in glob.glob(os.path.join(root, "*", "*.txt")):
            name = oracle = None
            with open(path, errors="replace") as f:
                for line in f:
                    if line.startswith("Name:") and name is None:
                        name = line[5:].strip()
                    elif line.startswith("Oracle:") and oracle is None:
                        oracle = line[7:].strip().replace("\\n", " / ")
            if name and oracle is not None:
                _ORACLE.setdefault(name, oracle)
    return _ORACLE


def card_texts(prompt, index):
    """The rules text of every card the prompt names, so the judge does not play from memory (a judge
    without it said Rishadan Port enters tapped and Aether Vial has flash; neither is true)."""
    seen = []
    for m in NAME_AT.finditer(prompt or ""):
        name = m.group(1).strip()
        for cand in (name, re.sub(r"^(?:CAST|ACTIVATE|PLAY LAND) ", "", name)):
            if cand in index and cand not in seen:
                seen.append(cand)
    return ["%s: %s" % (n, index[n]) for n in seen]


def judge_prompt(rows, r, index=None):
    earlier = turn_so_far(rows, r)
    texts = card_texts(str(r.get("prompt")), oracle_index() if index is None else index)
    return (INSTRUCTIONS
            + "\nThe CARD TEXT section is the official rules text of every card named; trust it over memory."
            + "\n\n=== WHAT THE PLAYER WAS SHOWN ===\n" + str(r.get("prompt"))
            + "\n\n=== WHAT IT HAD ALREADY DONE THIS TURN ===\n" + ("\n".join(earlier) if earlier else "(nothing)")
            + "\n\n=== CARD TEXT ===\n" + ("\n".join(texts) if texts else "(none found)")
            + "\n\n=== THE OPTION IT CHOSE ===\n" + "\n".join(chosen_label(r))
            + "\n\n=== ITS REASONING ===\n" + str(r.get("raw_reply") or "(none)"))


def parse_verdict(text):
    """The judge's JSON object, or None. Tolerates a code fence or prose around the object."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except ValueError:
        return None
    if v.get("verdict") not in ("ok", "questionable", "blunder"):
        return None
    v["question_ok"] = v.get("question_ok") is not False
    return v


def openrouter(spec, prompt, max_tokens):
    """One chat completion; returns (text, prompt_tokens, completion_tokens)."""
    body = {"model": cost_report.model_id(spec), "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    if os.environ.get("OPENROUTER_API_KEY"):
        req.add_header("Authorization", "Bearer " + os.environ["OPENROUTER_API_KEY"])
    with urllib.request.urlopen(req, timeout=180) as resp:
        d = json.loads(resp.read())
    u = d.get("usage") or {}
    text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def cost(price, pt, ct):
    return pt * price["prompt"] + ct * price["completion"]


def judge_run(run, spec, budget, types, limit, max_tokens, call=openrouter, out=sys.stdout):
    prices = cost_report.load_prices()
    price = cost_report.price_of(prices, spec)
    if price is None:
        raise SystemExit("%s is not in eval/pricing/prices.json; add it before judging with it" % spec)
    # The budget is checked at the dearest endpoint (cost_report.budget_prices); the reported spend is the list price.
    bound_price = cost_report.price_of(cost_report.budget_prices(prices), spec)
    tag = re.sub(r"[^A-Za-z0-9.-]+", "_", cost_report.model_id(spec))
    sheet = os.path.join(run, "judge-%s.jsonl" % tag)
    spent, bound, results = 0.0, 0.0, []
    with open(sheet, "w") as f:
        for path in sorted(glob.glob(os.path.join(run, "*.jsonl"))):
            if os.path.basename(path).startswith("judge-"):
                continue
            rows = rows_of(path)
            for r in list(judged(rows, types))[:limit or None]:
                if bound >= budget:
                    out.write("[budget] $%.2f reached at the dearest endpoint's price; stopping\n" % bound)
                    return results, spent, sheet
                prompt = judge_prompt(rows, r)
                text, pt, ct = call(spec, prompt, max_tokens)
                spent, bound = spent + cost(price, pt, ct), bound + cost(bound_price, pt, ct)
                if parse_verdict(text) is None and bound < budget:      # one retry: an empty or cut reply
                    text, pt, ct = call(spec, prompt, max_tokens)
                    spent, bound = spent + cost(price, pt, ct), bound + cost(bound_price, pt, ct)
                v = parse_verdict(text) or {"verdict": "unparsed", "raw": text[:500]}
                rec = {"t": "judgement", "version": VERSION, "judge": spec, "file": os.path.basename(path),
                       "line": r["_line"], "turn": r.get("turn"), "phase": r.get("phase"), "type": r.get("type"),
                       "chosen": chosen_label(r)[0][:200], **v}
                f.write(json.dumps(rec) + "\n")
                results.append(rec)
    return results, spent, sheet


def render(results, spent, sheet):
    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines = ["%s: %d decisions judged, $%.2f  |  %s" % (VERSION, len(results), spent,
             "  ".join("%s %d" % (k, counts[k]) for k in ("ok", "questionable", "blunder", "unparsed") if k in counts))]
    for r in results:
        if r["verdict"] in ("blunder", "questionable") or not r.get("question_ok", True):
            lines.append("  %-12s %s line %d T%s %s %s: chose %s%s\n               %s%s" % (
                r["verdict"].upper(), r["file"], r["line"], r["turn"], r["phase"], r["type"], r["chosen"][:80],
                (" (better: %s)" % r["better"]) if r.get("better") else "", r.get("reason", ""),
                ("\n               QUESTION: " + r.get("question_problem", "")) if not r.get("question_ok", True) else ""))
    lines.append("sheet: " + sheet)
    return "\n".join(lines)


def estimate(run, types, limit):
    n, chars = 0, 0
    for path in sorted(glob.glob(os.path.join(run, "*.jsonl"))):
        if os.path.basename(path).startswith("judge-"):
            continue
        rows = rows_of(path)
        for r in list(judged(rows, types))[:limit or None]:
            n += 1
            chars += len(judge_prompt(rows, r))
    return n, chars // 4                                    # ~4 characters per token


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="a run directory holding game *.jsonl files")
    ap.add_argument("--judge", default="openrouter:anthropic/claude-haiku-5.5")
    ap.add_argument("--budget-usd", type=float)
    ap.add_argument("--types", default=",".join(DEFAULT_TYPES), help="decision types to judge, comma-separated; '' for all")
    ap.add_argument("--limit", type=int, default=0, help="at most this many decisions per game (0 = all)")
    ap.add_argument("--max-tokens", type=int, default=8000,
                    help="includes hidden reasoning: at 2000, 5 of 49 Haiku 5.5 replies came back empty or cut")
    ap.add_argument("--estimate", action="store_true", help="count decisions and prompt tokens; no calls")
    a = ap.parse_args(argv)
    types = tuple(t for t in a.types.split(",") if t)
    if a.estimate:
        n, toks = estimate(a.run, types, a.limit)
        price = cost_report.price_of(cost_report.load_prices(), a.judge)
        usd = cost(price, toks, n * 300) if price else float("nan")
        print("%d decisions, ~%dK prompt tokens -> about $%.2f with %s (300 completion tokens each assumed)"
              % (n, toks // 1000, usd, a.judge))
        return 0
    if a.budget_usd is None:
        ap.error("--budget-usd is required (run --estimate first)")
    if os.environ.get("OPENROUTER_ALLOW_EGRESS") != "1":
        ap.error("judging sends game state to openrouter.ai; set OPENROUTER_ALLOW_EGRESS=1 yourself to consent")
    results, spent, sheet = judge_run(a.run, a.judge, a.budget_usd, types, a.limit, a.max_tokens)
    print(render(results, spent, sheet))
    return 0


if __name__ == "__main__":
    sys.exit(main())
