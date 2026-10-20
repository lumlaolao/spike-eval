"""Summary of the re-tiered 526: tier_v2 counts, the v1 tier beside them, L/O/T/B distributions, skills, AI rate."""
import collections
import json
import statistics
import sys

d = json.load(open(sys.argv[1]))
P = d["puzzles"]
print("records", len(P))
print("proof", dict(collections.Counter((r.get("proof") or {}).get("status") for r in P)))
print("tier_v2", dict(sorted(collections.Counter(str(r.get("tier_v2")) for r in P).items())))
print("tier v1", dict(sorted(collections.Counter(str(r.get("tier")) for r in P).items())))
ai = [((r.get("baselines") or {}).get("ai") or {}).get("solved") for r in P]
print("ai solved of 4", dict(sorted(collections.Counter(str(x) for x in ai).items())))
D = [r["difficulty"] for r in P if r.get("difficulty")]
for k in ("L", "D", "O", "T", "B", "board_objects"):
    v = [x[k] for x in D if isinstance(x.get(k), int)]
    if v:
        c = collections.Counter(v)
        top = ", ".join("%s:%d" % (a, b) for a, b in sorted(c.items())[:12])
        print("%-13s n=%d median=%s max=%s  %s" % (k, len(v), statistics.median(v), max(v), top))
sk = collections.Counter(s for r in P for s in (r.get("skills") or []))
print("skills", dict(sk.most_common()))
why = collections.Counter()
for r in P:
    t, x = r.get("tier_v2"), r.get("difficulty") or {}
    if t == 3:
        why["T3 by L>=6" if (x.get("L") or 0) >= 6 else "T3 by O>=2"] += 1
    elif t == 2:
        if (x.get("L") or 0) >= 3: why["T2 by L 3-5"] += 1
        elif (x.get("O") or 0) >= 1: why["T2 by O>=1"] += 1
        elif (x.get("T") or 1) >= 2: why["T2 by T>=2"] += 1
        else: why["T2 short line the AI fails"] += 1
    elif t == 1:
        why["T1"] += 1
    else:
        why["no tier: " + ("no proof" if not r.get("difficulty") else "no AI rate" if r.get("baselines", {}).get("ai") is None
                           else "missing T" if x.get("T") is None else "other")] += 1
print("why", dict(why.most_common()))
