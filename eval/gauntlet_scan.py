"""Reads a keyword gauntlet run (eval/keyword_gauntlet.sh) and lists every harness defect it shows.

Per game: exit code, turns, winner and the seat's [TALLY] line. Then every line that marks a defect: a choice
Forge's AI made (FALLBACK->AI, ai=N in the tally), an answer the seat could not read, a text parse or a text answer
format on a tool ask, a play that failed to pay or to target, a decision lost, a crash. A game with no game line
or a non-zero exit also counts. Exits 1 when anything is found.

usage: python3 eval/gauntlet_scan.py <run dir>
"""
import collections
import glob
import json
import re
import sys

BAD = re.compile(r"FALLBACK->AI|\(unparsed|TEXT-PARSE|TEXT-FORMAT-LEAK|Couldn't add to stack|failed to target"
                 r"|FAILED to pay|Exception|Error\b|threw|AI looped")


def scan(root):
    findings = collections.Counter()
    games = 0
    for d in sorted(glob.glob(root + "/g-*")):
        games += 1
        tag = d.rsplit("/", 1)[1]
        log = open(d + "/game.log", errors="replace").read()
        tally = re.findall(r"\[TALLY\][^\n]*", log)
        game = None
        for f in glob.glob(d + "/game-*.jsonl"):
            for line in open(f):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("t") == "game":
                    game = row
                if row.get("answer_via") == "lost":
                    findings["decision lost: %s" % row.get("type")] += 1
        exit_code = open(d + "/exit_code").read().strip() if glob.glob(d + "/exit_code") else "?"
        print(tag, "exit", exit_code, "turns", game and game.get("turns"), "winner", game and game.get("winner"),
              (tally[-1] if tally else "no tally")[:90])
        if exit_code != "0":
            findings["%s: exit %s" % (tag, exit_code)] += 1
        if game is None or game.get("winner") in (None, "invalid"):
            findings["%s: no finished game" % tag] += 1
        if tally and not re.search(r"\bai=0\b", tally[-1]):
            findings["%s: Forge AI decided for the seat (%s)" % (tag, tally[-1])] += 1
        for line in log.split("\n"):
            if BAD.search(line) and "JAVA_TOOL" not in line:
                findings[re.sub(r"\d+", "N", line.strip())[:220]] += 1
    print()
    print("%d games, %d defect lines" % (games, sum(findings.values())))
    for k, n in findings.most_common():
        print(n, k)
    return 0 if games and not findings else 1


if __name__ == "__main__":
    sys.exit(scan(sys.argv[1]))
