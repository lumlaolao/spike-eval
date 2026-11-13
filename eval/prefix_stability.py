#!/usr/bin/env python3
"""How much of each decision's message the provider's prompt cache can read from the previous one.

The provider caches a prompt by its prefix: the tools and system prompt, then the per-decision message. A decision's
message is read from the cache only as far as it starts with the bytes of the previous message of the same seat
(the previous request ended there). This reads the first-round `context_call` rows of a game directory and prints,
for each seat, the median message size, the median share of a message that is a byte prefix shared with the previous
one, the model rounds a decision took, and, under the cache order (CONTEXT_PROFILE=v5-cache), the share that is the
append-only record and every place where the record was not a prefix of the next message.

  python3 eval/prefix_stability.py <game dir> [--json] [--min-record-share 0.3]

Exit 1 when the record of a message is not a prefix of the next one, when a cache-ordered message prints a REVEALS
TO YOU block, or when the median record share is under --min-record-share (only checked when given).
"""

import argparse
import glob
import json
import os
import statistics
import sys

# The first line of the state after the record: the record is what a message holds before it (and before the
# archive lines ContextMemory puts between the two).
STATE_HEAD = "== Turn "
ARCHIVE_HEAD = "<archive>\n"
RECORD_HEAD = "THE GAME SO FAR ("
REVEALS_HEAD = "\nREVEALS TO YOU ("


def record_of(message):
    """The append-only record a message opens with: the text before the archive lines or the state header."""
    ends = [i for i in (message.find(ARCHIVE_HEAD), message.find(STATE_HEAD)) if i >= 0]
    return message[:min(ends)] if ends else ""


def common_prefix(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def seat_messages(game_dir):
    """{(file, seat): [(call, prompt)]} of the first-round model calls, in call order."""
    out = {}
    calls = {}
    for path in sorted(glob.glob(os.path.join(game_dir, "game-*.jsonl"))):
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("t") != "context_call":
                    continue
                key = (os.path.basename(path), r.get("seat"))
                calls[key] = calls.get(key, 0) + 1
                if r.get("round") == 0 and isinstance(r.get("prompt"), str):
                    out.setdefault(key, []).append((r.get("call") or 0, r["prompt"]))
    for key in out:
        out[key].sort(key=lambda x: x[0])
    return out, calls


def measure(game_dir):
    msgs, calls = seat_messages(game_dir)
    seats = []
    for key, rows in sorted(msgs.items()):
        prompts = [p for _, p in rows]
        shared, recorded, broken = [], [], []
        for i in range(1, len(prompts)):
            prev, cur = prompts[i - 1], prompts[i]
            shared.append(common_prefix(prev, cur) / max(1, len(cur)))
            rec = record_of(prev)
            recorded.append(len(record_of(cur)) / max(1, len(cur)))
            if rec and not cur.startswith(rec):
                at = common_prefix(rec, cur)
                broken.append({"call": rows[i][0], "at": at, "was": rec[max(0, at - 60):at + 60],
                               "now": cur[max(0, at - 60):at + 60]})
        seats.append({
            "file": key[0], "seat": key[1], "decisions": len(prompts),
            "rounds_per_decision": round(calls.get(key, 0) / max(1, len(prompts)), 3),
            "median_message_chars": int(statistics.median(len(p) for p in prompts)) if prompts else 0,
            "median_shared_prefix_share": round(statistics.median(shared), 3) if shared else None,
            "median_record_share": round(statistics.median(recorded), 3) if recorded else None,
            "cache_ordered": any(p.startswith(RECORD_HEAD) or ("\n" + ARCHIVE_HEAD) in p for p in prompts),
            "reveals_blocks": sum(REVEALS_HEAD in p for p in prompts),
            "record_breaks": broken,
        })
    return seats


def problems(seats, min_record_share=None):
    out = []
    for s in seats:
        where = "%s %s" % (s["file"], s["seat"])
        if s["record_breaks"]:
            b = s["record_breaks"][0]
            out.append("%s: the record was not a prefix of the next message %d time(s), first at call %s char %d"
                       % (where, len(s["record_breaks"]), b["call"], b["at"]))
        if s["cache_ordered"] and s["reveals_blocks"]:
            out.append("%s: %d cache-ordered messages print REVEALS TO YOU" % (where, s["reveals_blocks"]))
        if min_record_share is not None and (s["median_record_share"] or 0) < min_record_share:
            out.append("%s: median record share %s under %s" % (where, s["median_record_share"], min_record_share))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-record-share", type=float, default=None)
    a = ap.parse_args(argv)
    seats = measure(a.game_dir)
    if not seats:
        print("prefix_stability: no first-round context_call rows in %s" % a.game_dir, file=sys.stderr)
        return 1
    bad = problems(seats, a.min_record_share)
    if a.json:
        print(json.dumps({"seats": seats, "problems": bad}, indent=1))
    else:
        for s in seats:
            print("%s %s: decisions=%d rounds/decision=%.2f median message %d chars, shared prefix %s, record %s, "
                  "record breaks %d, REVEALS blocks %d"
                  % (s["file"], s["seat"], s["decisions"], s["rounds_per_decision"], s["median_message_chars"],
                     s["median_shared_prefix_share"], s["median_record_share"], len(s["record_breaks"]),
                     s["reveals_blocks"]))
        for p in bad:
            print("PROBLEM " + p)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
