#!/usr/bin/env bash
# Play Magic against a language model: you on Forge's own match screen, the model on the harness's seat.
#
#   bash play-vs-llm.sh                                   # your Burn against GPT-6 Luna on Mardu Energy
#   bash play-vs-llm.sh --deck elves --vs ur-delver       # any eval/decks/<name>.txt, or a path to a decklist
#   bash play-vs-llm.sh --model claude-cli:opus           # Claude Code on your subscription (CLAUDE_CLI_SEAT=1)
#   bash play-vs-llm.sh --commentary [--why]              # also print each model decision here (spoils its hand)
#   bash play-vs-llm.sh --dry-run                         # print what would run, start nothing
#
# The model plays under the benchmark's pilot condition (eval/suites/panels-v1.json): the same prompt, tools and
# `answer` call as its scored games, seeing only what a player in its seat may see. Its every decision, reply and
# request is kept under runs/human/<time>/ (git-ignored).
# An openrouter: model sends the game to openrouter.ai, which needs your consent: OPENROUTER_ALLOW_EGRESS=1 in
# your environment or in secrets.env (see secrets.env.example). This script reads that file and never sets it.
# Games against a person are never part of a benchmark score.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
# Forge: $FORGE_HOME, else where setup.sh put it, else the default checkout.
forge_from="the FORGE_HOME variable in your shell"
recorded=""                                   # where setup.sh built Forge, when it wrote that down
if [ -s "$REPO/.forge-home" ]; then recorded="$(head -1 "$REPO/.forge-home")"; fi
if [ -z "${FORGE_HOME:-}" ]; then
  if [ -n "$recorded" ]; then
    FORGE_HOME="$recorded"; forge_from="setup.sh's record, .forge-home"
  else
    FORGE_HOME="$HOME/code/forge"; forge_from="the default place"
  fi
fi

deck=burn; vs=mardu-energy; model=openrouter:openai/gpt-6-luna; games=1; record=0; dry=0; commentary=0; why=0
usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  echo; echo "flags: --deck D --vs D --model SPEC --games N --record --commentary --why --dry-run"
}
need() { [ $# -ge 2 ] || { echo "play-vs-llm: $1 needs a value" >&2; usage >&2; exit 2; }; }
while [ $# -gt 0 ]; do
  case "$1" in
    --deck) need "$@"; deck="$2"; shift 2 ;;
    --vs) need "$@"; vs="$2"; shift 2 ;;
    --model) need "$@"; model="$2"; shift 2 ;;
    --games) need "$@"; games="$2"; shift 2 ;;
    --record) record=1; shift ;;
    --commentary) commentary=1; shift ;;
    --why) commentary=1; why=1; shift ;;
    --dry-run) dry=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "play-vs-llm: unknown flag $1" >&2; usage >&2; exit 2 ;;
  esac
done

resolve() { # a deck name under eval/decks, or a path to a decklist
  if [ -f "$1" ]; then (cd "$(dirname "$1")" && echo "$PWD/$(basename "$1")")
  elif [ -f "$REPO/eval/decks/$1.txt" ]; then echo "$REPO/eval/decks/$1.txt"
  else echo "play-vs-llm: no deck '$1' (a path, or one of: $(ls "$REPO/eval/decks" | sed -n 's/\.txt$//p' \
      | grep -v '^hook-' | paste -sd' ' -))" >&2; return 1; fi
}
deck_a="$(resolve "$deck")"; deck_b="$(resolve "$vs")"
case "$games" in ''|*[!0-9]*|0) echo "play-vs-llm: --games takes a positive number" >&2; exit 2 ;; esac

# The owner's own consent and key file, read as eval/puzzles/puzzle_solve.py reads it (KEY=VALUE, whitespace and a CR
# trimmed, # comments skipped, the first occurrence kept); the environment wins.
if [ -f "$REPO/secrets.env" ]; then
  while IFS= read -r ln || [ -n "$ln" ]; do
    ln="${ln%$'\r'}"; case "$ln" in *=*) ;; *) continue ;; esac
    k="${ln%%=*}"; v="${ln#*=}"
    k="$(echo "$k" | tr -d '[:space:]')"; v="$(echo "$v" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "$k" in OPENROUTER_API_KEY|OPENROUTER_ALLOW_EGRESS) if [ -z "${!k:-}" ]; then export "$k=$v"; fi ;; esac
  done < "$REPO/secrets.env"
fi
case "$model" in
  openrouter:*) [ "${OPENROUTER_ALLOW_EGRESS:-}" = 1 ] || { echo "play-vs-llm: $model sends the game to" \
      "openrouter.ai; that needs your consent, OPENROUTER_ALLOW_EGRESS=1 in secrets.env (secrets.env.example)" >&2
      exit 3; } ;;
  claude-cli:*) [ "${CLAUDE_CLI_SEAT:-}" = 1 ] || { echo "play-vs-llm: $model runs Claude Code on your" \
      "subscription; opt in with CLAUDE_CLI_SEAT=1" >&2; exit 3; } ;;
esac

out="$REPO/runs/human/$(date -u +%Y%m%dT%H%M%SZ)"
# The pilot condition, from the panels file. Every other key a batch scrubs (run_batch.build_env: the run-defining
# keys, the heuristic shortcuts and plans, CTX_* overrides) is unset, and the further keys below (ones the overlay
# reads that a batch sets from its own flags), so the caller's shell does not change what the seat is asked or shown.
# Kept: the seed, the operator's own Claude CLI effort and thinking settings, and infrastructure (endpoint,
# timeouts, recording, DEMO_FEED). No apostrophe or backtick inside the heredoc: bash 3.2 (STANDARDS section 4).
condition="$(python3 - "$REPO" <<'PY'
import json, os, sys
sys.path.insert(0, sys.argv[1] + "/eval")
import run_batch
fields = json.load(open(sys.argv[1] + "/eval/suites/panels-v1.json"))["pilot_condition"]["fields"]
cond = {k.upper(): ("1" if v else "0") if isinstance(v, bool) else str(v) for k, v in fields.items()}
scrub = list(run_batch.RUN_DEFINING_KEYS + run_batch.CITED_FORCED_KEYS) + [
    "SHADOW_AI", "AUTO_EXIT", "OPENROUTER_MODEL_CFG", "PROMPT_CACHE",
    "PUZZLE", "PUZZLE_DIRS", "SMART_BUDGET", "FAST_BUDGET"]
scrub += [k for k in os.environ if k.startswith((run_batch.CTX_OVERRIDE_PREFIX, "PZL_", "FORK_"))]
for k in dict.fromkeys(scrub):
    if not k.startswith("CLAUDE_") and k != "GAME_SEED":
        print("%s=%s" % (k, cond.get(k, "")))
PY
)"
cond_vars=""
while IFS='=' read -r k v; do
  cond_vars="$cond_vars $k"
  if [ -n "$v" ]; then export "$k=$v"; else unset "$k"; fi
done <<< "$condition"
vars="$cond_vars MATCH_GAMES TRANSCRIPTS RECORD RECORD_OUT GAME_SEED CLAUDE_BRIDGE_EFFORT CLAUDE_BRIDGE_EFFORT_SMART
CLAUDE_BRIDGE_EFFORT_FAST CLAUDE_CLI_THINKING_TOKENS"
export CONTROLLER_A=human CONTROLLER_B=llm
export DECK_A="$deck_a" DECK_B="$deck_b"
# The model's seat keeps the name the benchmark's games give it ("Seat"); the person is "Human".
export DECK_A_NAME=Human DECK_B_NAME=Seat
export SMART_MODEL_B="$model" FAST_MODEL_B="$model"
export MATCH_GAMES="$games" EVAL_OUT="$out" TRANSCRIPTS=1
if [ "$record" = 1 ]; then export RECORD=1 RECORD_OUT="$out/match.mp4"; fi

# The build is checked before anything is announced, so a start that cannot happen prints only why. A FORGE_HOME left
# in the shell wins over setup.sh's record (it is the documented override), so the message says which one was used.
if [ "$dry" != 1 ] && [ ! -x "$FORGE_HOME/run-llm-spike.sh" ]; then
  echo "play-vs-llm: no Forge build at $FORGE_HOME (from $forge_from); run 'bash setup.sh' first" >&2
  if [ -n "$recorded" ] && [ "$recorded" != "$FORGE_HOME" ] && [ -x "$recorded/run-llm-spike.sh" ]; then
    echo "play-vs-llm: setup.sh built Forge at $recorded; to use it, run 'unset FORGE_HOME' and try again" >&2
  fi
  exit 4
fi

echo "play-vs-llm: you on $(basename "$deck_a" .txt), $model on $(basename "$deck_b" .txt), $games game(s)"
echo "play-vs-llm: the model's decisions and replies -> $out"
echo "play-vs-llm: a model may think up to a few minutes on one decision; the screen waits for it"
case "$model" in openrouter:*openai/gpt-6-luna) echo "play-vs-llm: expected cost about \$0.02-0.05 a game" ;; esac
feed=(python3 "$REPO/eval/play_commentary.py" "$out" --follow)
if [ "$why" = 1 ]; then feed+=(--why); fi
if [ "$dry" = 1 ]; then
  for v in $vars; do if [ -n "${!v+x}" ]; then echo "$v=${!v}"; fi; done | sort
  if [ "$commentary" = 1 ]; then echo "would also run: ${feed[*]}"; fi
  echo "would run: $FORGE_HOME/run-llm-spike.sh"
  exit 0
fi
# The launcher's classpath file lives in /tmp, which a reboot clears; one person's game can rebuild it (no batch races
# it here, the reason run-llm-spike.sh only refuses).
export FORGE_CP_FILE="${FORGE_CP_FILE:-/tmp/forge_cp.txt}"
if [ ! -s "$FORGE_CP_FILE" ] && [ -f "$FORGE_HOME/pom.xml" ]; then
  echo "play-vs-llm: rebuilding Forge's classpath list ($FORGE_CP_FILE), once after a reboot"
  (cd "$FORGE_HOME" && mvn -q -o -pl forge-gui-desktop -am dependency:build-classpath \
      -Dmdep.outputFile="$FORGE_CP_FILE")
fi
mkdir -p "$out"
if [ "$commentary" = 1 ]; then
  "${feed[@]}" & feed_pid=$!
  # `|| true`: a feed that already died must not turn the game's status into 1 under set -e.
  trap 'kill $feed_pid 2>/dev/null || true' EXIT
fi
status=0; "$FORGE_HOME/run-llm-spike.sh" || status=$?
echo "play-vs-llm: what the model did and why: python3 eval/play_commentary.py $out --why"
exit $status
