#!/usr/bin/env python3
"""Deal Java test classes into N shards for acceptance's javatests stage, heaviest first to the lightest shard.

  python3 eval/javatests_shards.py N <class,class,...> [seconds.tsv]

Prints one comma-separated class list per shard, space-separated (an empty shard is left out). The weights are the
seconds each class took in an acceptance run (seconds.tsv: "<simple class name>\t<seconds>"); a class not listed
counts 1 s. The deal is deterministic: the same inputs give the same shards.
"""
import sys


def weights(path):
    out = {}
    if path:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    name, sec = line.split()
                    out[name] = float(sec)
    return out


def deal(n, classes, secs):
    shards = [[0.0, i, []] for i in range(n)]
    for c in sorted(classes, key=lambda c: (-secs.get(c.split(".")[-1], 1.0), c)):
        s = min(shards, key=lambda s: (s[0], s[1]))
        s[0] += secs.get(c.split(".")[-1], 1.0)
        s[2].append(c)
    return [sorted(s[2]) for s in shards if s[2]]


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if len(a) < 2 or not a[0].isdigit() or int(a[0]) < 1:
        print(__doc__, file=sys.stderr)
        return 2
    classes = [c for c in a[1].split(",") if c]
    print(" ".join(",".join(s) for s in deal(int(a[0]), classes, weights(a[2] if len(a) > 2 else None))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
