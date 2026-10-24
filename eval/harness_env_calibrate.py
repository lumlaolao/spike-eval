#!/usr/bin/env python3
"""Calibration of the harness-building benchmark's number (docs/harness/HARNESS-ENV-FRAMEWORK.md, section 4.5):
several ways to turn a per-family profile into one score, compared on the same instance report, and a
recommendation by stated rules rather than by taste.

Settings (every benchmark setting is gated to 0 for a seat with any Forge-AI decision; the dense ones are not):
  dense-equal               mean over families (the RL reward)
  dense-corpus              mean weighted by how often each family's decisions occur in recorded games
  geometric-equal-0.001     geometric mean, equal weights, a family at zero counts as 0.001 (the current benchmark)
  geometric-equal-0.01      the same with a softer floor
  geometric-corpus-0.001    geometric mean weighted by the corpus
  geometric-mixed-0.001     weights half equal, half corpus
  strict-any-zero           the dense mean, or 0 when any family is at zero
Corpus weights come from every "t":"decision" row under a runs directory (decision type -> family, family_of_type),
each family floored at 2% so a rare family still counts. A runs directory grows, so the counts are saved with
`--counts <json>` beside `--runs` and read back from that file alone without it: the committed counts
(docs/harness/evidence/2026-10-24-corpus-decision-counts.json) are what the framework's table was computed from.

Criteria, each a statement about the validation seats that the number must make true:
  ours near the ceiling          ours >= 0.9
  default near zero              the priority-only bridge <= 0.05
  missing family far below       ours-minus-combat <= half of ours
  partial family below           ours-minus-costs < ours - 0.05
  one failure is not a cliff     one more failed instance in ours' smallest family costs at most a quarter
  Forge AI gated                 the AI delegator scores 0
The recommendation is the first setting, in the order above, that meets every criterion the report can test.

What it cannot do: say which criteria are right. They are the owner's sentences (2026-10-22) written as tests;
a different sentence is a different recommendation.

Usage: harness_env_calibrate.py <env-grade.tsv> [--runs <dir>] [--counts <json>]
"""
import collections
import json
import os
import sys

import harness_env_grade as hg

SETTINGS = ("geometric-equal-0.001", "geometric-mixed-0.001", "geometric-equal-0.01", "geometric-corpus-0.001",
            "strict-any-zero", "dense-equal", "dense-corpus")
TYPE_FAMILY = [
    ("play", ("play", "ability-to-play", "play-from-effect")),
    ("targets", ("target", "targets", "attack-target")),
    ("combat", ("attackers", "blockers", "damage-assign", "damage-order", "combat-cost", "enlist", "exert")),
    ("costs", ("splice", "mana-sources", "phyrexian", "mana-from-pool", "prevent-effect", "optional-costs", "convoke",
               "harmonize", "delve", "announce-x", "return-cost", "discard-cost", "exile-cost", "keyword-cost")),
    ("triggers-and-replacements", ("trigger-order", "confirm-trigger", "replacement-order", "confirm-replacement",
                                   "static-order")),
    ("game-start", ("mulligan", "mulligan-bottom", "starting-player", "opening-hand")),
    ("library", ("scry", "surveil", "top-or-bottom")),
    ("variants", ("dice", "flip", "roll", "sticker", "sprocket")),
]


def family_of_type(t):
    """The hook family a recorded decision type belongs to; anything unlisted is a choice."""
    for fam, types in TYPE_FAMILY:
        if t in types or (fam == "variants" and t.startswith(types)):
            return fam
    return "choices"


def decision_counts(root):
    counts = collections.Counter()
    for r, _, files in os.walk(root):
        for f in files:
            if not f.endswith(".jsonl"):
                continue
            with open(os.path.join(r, f), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"decision"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and d.get("t") == "decision":
                        counts[d.get("type", "?")] += 1
    return counts


def load_counts(runs, counts_file):
    """Decision counts from `runs` (saved to `counts_file` when one is named), or read back from `counts_file`."""
    if runs:
        counts = dict(sorted(decision_counts(runs).items()))
        if counts_file:
            with open(counts_file, "w", encoding="utf-8") as f:
                json.dump(counts, f, indent=1, sort_keys=True)
        return counts
    with open(counts_file, encoding="utf-8") as f:
        return json.load(f)


def corpus_weights(counts, families, floor=0.02):
    by = collections.Counter()
    for t, n in counts.items():
        by[family_of_type(t)] += n
    total = sum(by[f] for f in families) or 1
    raw = {f: max(by[f] / total, floor) for f in families}
    s = sum(raw.values())
    return {f: v / s for f, v in raw.items()}


def _geometric(scores, weights, floor):
    total = sum(weights[f] for f in scores) or 1
    out = 1.0
    for f, s in scores.items():
        out *= max(floor, s) ** (weights[f] / total)
    return out


def score(setting, fam_scores, weights):
    if not fam_scores:
        return 0.0
    equal = {f: 1.0 for f in fam_scores}
    mixed = {f: 0.5 / len(fam_scores) + 0.5 * weights.get(f, 0) for f in fam_scores}
    if setting == "dense-equal":
        return sum(fam_scores.values()) / len(fam_scores)
    if setting == "dense-corpus":
        tw = sum(weights.get(f, 0) for f in fam_scores) or 1
        return sum(weights.get(f, 0) * s for f, s in fam_scores.items()) / tw
    if setting == "geometric-equal-0.001":
        return _geometric(fam_scores, equal, 0.001)
    if setting == "geometric-equal-0.01":
        return _geometric(fam_scores, equal, 0.01)
    if setting == "geometric-corpus-0.001":
        return _geometric(fam_scores, {f: weights.get(f, 0) for f in fam_scores}, 0.001)
    if setting == "geometric-mixed-0.001":
        return _geometric(fam_scores, mixed, 0.001)
    if setting == "strict-any-zero":
        return 0.0 if min(fam_scores.values()) <= 0 else sum(fam_scores.values()) / len(fam_scores)
    raise ValueError(setting)


def family_scores(table):
    """{seat: {family: pass rate}}, with `empty` (no harness) at zero everywhere."""
    fams = sorted({f for seat in table.values() for f in seat})
    return {seat: hg.family_rates(table.get(seat, {}), fams) for seat in hg._seats(table)}


CHECKS = ("asked", "offered", "carried", "shown", "noparse")


def evaluate(rows, weights, gate=True):
    """Every setting on every seat, the criteria, the recommendation. gate=False shows the family rule alone: no run-wide
    Forge-AI gate, and an instance passes on its five checks (as if the counter had seen nothing)."""
    if not gate:
        rows = [dict(r, **{"pass": "1" if all(r.get(c) == "1" for c in CHECKS) else "0"}) if all(c in r for c in CHECKS) else r
                for r in rows]
    table = hg.env_table(rows, by="hook")
    gated = hg.ai_gated(rows) if gate else set()
    fs = family_scores(table)
    scores = {}
    for s in SETTINGS:
        scores[s] = {}
        for seat, f in fs.items():
            v = score(s, f, weights)
            if seat in gated and not s.startswith("dense"):
                v = 0.0
            scores[s][seat] = round(v, 6)
    criteria = {}
    for s in SETTINGS:
        sc, c = scores[s], {}
        if "ours" in sc:
            c["ours near the ceiling"] = sc["ours"] >= 0.9
        if "default" in sc:
            c["default near zero"] = sc["default"] <= 0.05
        if "ours-minus-combat" in sc and "ours" in sc:
            c["missing family far below"] = sc["ours-minus-combat"] <= 0.5 * sc["ours"]
        if "ours-minus-costs" in sc and "ours" in sc:
            c["partial family below"] = sc["ours-minus-costs"] < sc["ours"] - 0.05
        if "ours" in table:
            cells = table["ours"]
            small = min(cells, key=lambda f: cells[f]["total"])
            worse = dict(fs["ours"])
            worse[small] = max(0, cells[small]["passed"] - 1) / cells[small]["total"]
            v = score(s, worse, weights)
            c["one failure is not a cliff"] = sc["ours"] - v <= 0.25 * sc["ours"]
        if gate and "toy-ai" in sc and not s.startswith("dense"):
            c["Forge AI gated"] = sc["toy-ai"] == 0.0
        criteria[s] = c
    benchmark_settings = [s for s in SETTINGS if not s.startswith("dense")]
    passing = [s for s in benchmark_settings if all(criteria[s].values())]
    recommended = passing[0] if passing else max(benchmark_settings, key=lambda s: sum(criteria[s].values()))
    return {"scores": scores, "criteria": criteria, "recommended": recommended, "weights": weights, "families": fs}


def markdown(result):
    seats = hg._seats(result["scores"][SETTINGS[0]])
    lines = ["| setting | " + " | ".join(seats) + " | criteria met |", "|---|" + "---|" * (len(seats) + 1)]
    for s in SETTINGS:
        c = result["criteria"][s]
        met = "%d/%d" % (sum(c.values()), len(c)) + ("" if all(c.values()) else " (fails: " + ", ".join(k for k, v in c.items() if not v) + ")")
        name = s + (" **recommended**" if s == result["recommended"] else "")
        lines.append("| %s | %s | %s |" % (name, " | ".join("%.3f" % result["scores"][s][x] for x in seats), met))
    w = result["weights"]
    lines.append("\ncorpus weights: " + ", ".join("%s %.3f" % (f, w[f]) for f in sorted(w, key=lambda f: -w[f])))
    return "\n".join(lines)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    rows = hg.read_env_report(argv[1])
    fams = sorted({hg.family(r["hook"]) for r in rows})
    runs = argv[argv.index("--runs") + 1] if "--runs" in argv else None
    counts_file = argv[argv.index("--counts") + 1] if "--counts" in argv else None
    if runs or counts_file:
        weights = corpus_weights(load_counts(runs, counts_file), fams)
    else:
        weights = {f: 1.0 / len(fams) for f in fams}
    for gate in (True, False):
        result = evaluate(rows, weights, gate=gate)
        print("\n%s\n" % ("With the run-wide Forge-AI gate (the published benchmark):" if gate
                          else "Without it (the family rule alone, as if the counter had seen nothing):"))
        print(markdown(result))
        print(json.dumps({"recommended": result["recommended"], "criteria": result["criteria"][result["recommended"]]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
