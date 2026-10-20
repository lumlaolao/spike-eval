"""The report's paired comparisons: per-puzzle pass@1 differences, 10,000 bootstrap resamples of the puzzles
(Random(7), puzzle ids sorted), 95% percentile interval.

    python3 eval/puzzles/rules-pilot/benchmark-v1/paired.py eval/puzzles/rules-pilot/benchmark-v1
"""
import glob
import json
import os
import random
import sys


def rates(root):
    out = {}
    for d in glob.glob(os.path.join(root, "*")):
        rows = [json.loads(l) for f in glob.glob(d + "/puzzle-results.jsonl") for l in open(f) if l.strip()]
        if rows:
            out[os.path.basename(d)] = sum(bool(r.get("solved")) for r in rows) / len(rows)
    return out


def boot(diffs, n=10000, seed=7):
    rng = random.Random(seed)
    m = sum(diffs) / len(diffs)
    bs = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(n))
    return m, bs[int(0.025 * (n - 1))], bs[int(-(-0.975 * (n - 1) // 1))]


def main(base):
    r = {(e, a): rates(os.path.join(base, d, a)) for e, d in (("66", "results"), ("67", "results-e67")) for a in ("off", "on")}
    ids = sorted(r[("66", "off")])
    rows = [("off, epoch 67 minus epoch 66", [r[("67", "off")][p] - r[("66", "off")][p] for p in ids]),
            ("on, epoch 67 minus epoch 66", [r[("67", "on")][p] - r[("66", "on")][p] for p in ids]),
            ("epoch 67, on minus off", [r[("67", "on")][p] - r[("67", "off")][p] for p in ids]),
            ("epoch 66, on minus off", [r[("66", "on")][p] - r[("66", "off")][p] for p in ids]),
            ("on drop minus off drop", [(r[("67", "on")][p] - r[("66", "on")][p]) - (r[("67", "off")][p] - r[("66", "off")][p])
                                        for p in ids])]
    for name, diffs in rows:
        m, lo, hi = boot(diffs)
        print("| %s | %+.3f | [%.3f, %.3f] |" % (name, m, lo, hi))


if __name__ == "__main__":
    main(sys.argv[1])
