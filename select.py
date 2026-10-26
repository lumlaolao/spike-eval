"""Every frontier5 game so far, per model and board: a good one (it ended, no decision lost to routing, and no limit
fired: no reply cut at max_tokens, no call past its deadline, not capped) is kept; a board with none is to do.
Prints the table; --todo prints "model board" lines; --json writes the chosen result per (model, board)."""
import json
import re
import sys
from pathlib import Path

RUNS = Path("runs/puzzles")   # run from the repository root
ROOTS = ["frontier5", "frontier5-kimi-k2.6-int4", "frontier5-long", "frontier5-fill"]
BOARDS = ["hard-atog-b8053a21d4a8", "hard-hoofdrain-60c5afda2f25", "hard-splice2-325cd33e0a57",
          "hard-drain2-516c13f84175", "hard-elf2-2753b3ca0d06"]
# Called off by the owner (2026-10-26): not played further, not in the totals.
EXCLUDE = {"z-ai_glm-5.3-prime", "openai_gpt-6.1-sol-pro"}
# Kept by the owner although a limit fired (2026-10-26): Kimi K2.6 solved drain2 with 1 of 80 decisions cut at the
# 32000-token cap and answered with reasoning off; not replayed.
KEEP = {("moonshotai_kimi-k2.6", "hard-drain2-516c13f84175")}
# Stopped by the owner as too slow (2026-10-26) and scored a failure (owner): over the time budget, never replayed.
STOPPED = {("mistralai_mistral-large-4-0", b) for b in ("hard-elf2-2753b3ca0d06", "hard-splice2-325cd33e0a57",
                                                        "hard-hoofdrain-60c5afda2f25")}
# GLM 5.3's elf2: lost to the harness's answer-call bug (NOTES.md), stopped once lost and scored a failure (owner).
STOPPED.add(("z-ai_glm-5.3", "hard-elf2-2753b3ca0d06"))
# The owner counts Grok 4.3's first atog game (solved under the 30-minute cap) over its 90-minute replay (2026-10-26).
FIRST_SOLVE = {("x-ai_grok-4.3", "hard-atog-b8053a21d4a8")}
LIMIT = re.compile(r"finish_reason=length|wall-clock timeout after")


def model_of(d):
    return d.name.split("__")[0]


def games(pz):
    """(rows file, its jvm log) pairs of one puzzle directory: the live one and each set-aside one."""
    out = [(pz / "puzzle-results.jsonl", pz / "jvm.log")]
    for f in sorted(pz.glob("puzzle-results.partial*.jsonl")):
        n = f.name[len("puzzle-results.partial"):-len(".jsonl")]
        out.append((f, pz / ("jvm.partial%s.log" % n)))
    return out


table = {}
for root in ROOTS:
    base = RUNS / root
    if not base.is_dir():
        continue
    for d in sorted(x for x in base.iterdir() if x.is_dir()):
        m = model_of(d)
        if m in EXCLUDE:
            continue
        if root == "frontier5" and m == "moonshotai_kimi-k2.6":
            continue   # the bf16-host attempt; K2.6 plays on int4 hosts (its released format)
        for pz in sorted(x for x in d.iterdir() if x.is_dir() and x.name in BOARDS):
            for rows_f, log_f in games(pz):
                if not rows_f.is_file():
                    continue
                rows = [json.loads(l) for l in rows_f.read_text().splitlines() if l.strip()]
                if not rows:
                    continue
                r = rows[-1]
                log = log_f.read_text(errors="replace") if log_f.is_file() else ""
                why = []
                if r.get("status") not in ("solved", "failed"):
                    why.append("status " + str(r.get("status")))
                if r.get("routing_refusals"):
                    why.append("routing refusals")
                if LIMIT.search(log) and (m, pz.name) not in KEEP:
                    why.append("limit fired")
                if not log:
                    why.append("no log")
                table.setdefault((m, pz.name), []).append(
                    {"root": root, "rows": str(rows_f), "status": r.get("status"), "good": not why, "why": why,
                     "minutes": round((r.get("duration_ms") or 0) / 60000)})

models = sorted(({m for m, _ in table} | {model_of(d) for root in ROOTS if (RUNS / root).is_dir()
                                           for d in (RUNS / root).iterdir() if d.is_dir()}) - EXCLUDE)
import subprocess  # noqa: E402
LIVE = [open("/proc/%s/cmdline" % p, "rb").read().decode(errors="replace")
        for p in subprocess.run(["pgrep", "java"], capture_output=True, text=True).stdout.split()
        if Path("/proc/%s/cmdline" % p).exists()]
todo, chosen = [], {}
for m in models:
    cells = []
    for b in BOARDS:
        good = [g for g in table.get((m, b), []) if g["good"]]
        if (m, b) in FIRST_SOLVE:
            good = [g for g in good if g["status"] == "solved"] or good
        if good:
            chosen["%s %s" % (m, b)] = good[0]
            cells.append("S" if good[0]["status"] == "solved" else "F")
        elif (m, b) in STOPPED and not any(m in s and b in s for s in LIVE):
            chosen["%s %s" % (m, b)] = {"root": "stopped", "status": "failed", "good": True,
                                        "why": ["stopped by the owner: over the time budget"]}
            cells.append("F")
        else:
            todo.append((m, b))
            cells.append(".")
    print("%-30s %s  good %d/5" % (m, " ".join(cells), sum(c in "SF" for c in cells)))
print("good games %d/%d, to do %d (4 stopped boards count as failures: 3 Mistral Large 4 over time, GLM 5.3 elf2)"
      % (5 * len(models) - len(todo), 5 * len(models), len(todo)))
if "--todo" in sys.argv:
    for m, b in todo:
        print("TODO", m, b)
if "--json" in sys.argv:
    Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(chosen, indent=1))
