#!/usr/bin/env python3
"""Every version stamp the harness carries, read from the code, and the page that lists them.

A result is comparable only to results from the same versions, and they live in constants across the Java
files listed in STAMPS, plus the pinned engine commit. This reads them from the source (never a hand-kept copy) and writes
docs/harness/VERSIONS.md; `--check` fails when that page no longer matches the code, so it cannot go stale.

Versions are not put in the model's prompt: the model gains nothing from them, and a changing prefix would break the
prompt cache. Each game line and model-call row records them instead (the `game_line` column).

  python3 eval/versions.py            # print the table
  python3 eval/versions.py --write    # rewrite docs/harness/VERSIONS.md
  python3 eval/versions.py --check    # exit 1 if the page is stale
"""
import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPIKE = os.path.join(REPO, "forge-overlay", "forge-gui-desktop", "src", "main", "java", "forge", "spike")
PAGE = os.path.join(REPO, "docs", "harness", "VERSIONS.md")

# (file, constant, game-line field, what a change to it means)
STAMPS = (
    ("LlmSpikeGui.java", "HARNESS_EPOCH", "harness_epoch",
     "the interface the model plays through, as one number: any change to what the seat is asked or shown, or to how "
     "its answer is carried out. Results pool only within one epoch."),
    ("SeatObservation.java", "VERSION", "observation_version",
     "the faithful state the seat reads: the blocks, their labels and their order."),
    ("SeatObservation.java", "CACHE_VERSION", "(`observation_version` under `CONTEXT_PROFILE=v5-cache`: `VERSION+CACHE_VERSION`)",
     "the cache order of that state: the append-only record it opens with (finished turns, card text when first seen), "
     "where the archive lines go, reveals printed once, and the guide's words for them."),
    ("AnswerTool.java", "VERSION", "answer_tool_version", "the `answer` and `set_plan` functions and their validation."),
    ("NativeTools.java", "VERSION", "native_tools_version", "the lookup tools' definitions (inspect, search, read, simulate)."),
    ("ContextMemory.java", "VERSION", "memory_tools_version", "what the evidence lookups return."),
    ("PlayerLookahead.java", "VERSION", "lookahead_version", "the `simulate` preview and the attack outlook."),
    ("RulesTool.java", "VERSION", "rules_tool_version", "the comprehensive-rules lookup."),
    ("ChatSessionBridge.java", "VERSION", "one_chat_version", "the one-conversation-per-seat arm (ONE_CHAT)."),
    ("ChatSessionBridge.java", "TURN_VERSION", "turn_chat_version", "the conversation bounded by the turn (TURN_CHAT)."),
    ("DecisionsBridge.java", "VERSION", "(`decisions_bridge_version_a` / `_b`; puzzle row `decisions_bridge_version`)",
     "the typed questions a `jev:` decision-model seat is asked and how its answers become `answer` calls "
     "(on a game line or puzzle row only where a `jev:` seat plays)."),
    ("ClaudeCliBridge.java", "VERSION", "(`claude_cli_bridge_version_a` / `_b`; puzzle row `claude_cli_bridge_version`)",
     "how a `claude-cli:` seat runs the Claude Code CLI (argv, isolation, the answer schema, one process per turn) and "
     "how its structured output becomes `answer` and `set_plan` calls (only where such a seat plays)."),
    ("ReplayBridge.java", "VERSION", "(puzzle row `replay_version`)",
     "how a `replay:` seat serves a recorded attempt's model turns back and what it calls a drift (tooling only: the "
     "seat is sent nothing new, so it is not a condition and does not move HARNESS_EPOCH; a replay row is never scored), "
     "and its handover to the search arm after K rows (REPLAY_HANDOVER, step regret; `step-search.jsonl` rows)."),
    ("Transcript.java", "VERSION", "(puzzle row `transcript_version`, puzzle manifest `record.transcript_version`)",
     "the run's full record kept beside the rows (TRANSCRIPTS=1, on for every puzzle_solve.py run): the exact HTTP "
     "body of every model request and response, and each game's whole Forge log. Recording only: what the model is "
     "sent does not change with it, so it is not a condition and does not move HARNESS_EPOCH."),
)


def read_stamps():
    rows = []
    for fname, const, field, covers in STAMPS:
        with open(os.path.join(SPIKE, fname), encoding="utf-8") as f:
            m = re.search(r'static final String %s = "([^"]+)"' % const, f.read())
        if not m:
            raise SystemExit("versions.py: %s.%s not found" % (fname, const))
        rows.append((fname.replace(".java", "") + "." + const, m.group(1), field, covers))
    with open(os.path.join(REPO, "FORGE_COMMIT")) as f:
        rows.append(("FORGE_COMMIT", f.read().strip()[:12], "engine_sha",
                     "the pinned Forge engine; results compare only within one engine version."))
    return rows


def render(rows):
    out = ["# Version stamps", "",
           "Generated by `python3 eval/versions.py --write` from the source; `eval/tests/test_versions.py` fails when this page "
           "no longer matches the code. Bump a stamp in the same change that alters what it covers "
           "(`docs/STANDARDS.md` section 4), and record why in that constant's comment.", "",
           "Versions are not in the model's prompt: the model gains nothing from them, and a changing prefix would break "
           "the prompt cache. Every game line records them, so a result names the interface it was measured on.", "",
           "| stamp | value | game line | a change means |", "|---|---|---|---|"]
    for name, value, field, covers in rows:
        out.append("| `%s` | `%s` | %s | %s |" % (name, value, field if field.startswith("(") else "`%s`" % field, covers))
    out += ["", "Also on every game line: `context_profile` and `context_sha` (the observation profile and a hash of its "
            "template), `overlay_sha` (every overlay source, and each engine patch as the file it makes), `deck_sha`, "
            "and, for a panel, the benchmark version "
            "(`eval/benchmark_version.py`).", "",
            "`overlay_sha` does not hash a checkout's `res/`, so the card scripts of a twin puzzle (`eval/puzzles/puzzle_twin.py`) "
            "are stamped on the puzzle record instead: `twin_set_sha256`, the hash of the sha256 of every generated script the "
            "twin needs (`twin_cards`). It is in `suite_sha256` (with the twin-only `twin_of` and `original_pzl_sha256`) and in the puzzle manifest's `record`, and a game or baseline "
            "run on a twin refuses unless the installed scripts match it.", ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    text = render(read_stamps())
    if a.write:
        with open(PAGE, "w", encoding="utf-8") as f:
            f.write(text)
        return 0
    if a.check:
        current = open(PAGE, encoding="utf-8").read() if os.path.exists(PAGE) else ""
        if current != text:
            print("versions.py: docs/harness/VERSIONS.md is stale; run python3 eval/versions.py --write", file=sys.stderr)
            return 1
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
