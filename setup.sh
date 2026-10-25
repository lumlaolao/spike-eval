#!/usr/bin/env bash
# One command from a fresh clone to a game against a language model:
#
#   bash setup.sh                       # Forge into ~/code/forge, then the build, then your OpenRouter key
#   bash setup.sh --forge-dir DIR       # Forge somewhere else
#   bash play-vs-llm.sh                 # then play
#
# It checks for Java 17 or newer, Maven, git and Python 3 (and says how to install what is missing); fetches Forge at
# exactly the commit this repository was tested against (FORGE_COMMIT), shallow; lays this repository's changes on
# top (install.sh); builds it (the first build downloads Forge's libraries, about 10 minutes); and asks for your
# OpenRouter key and whether games may be sent to openrouter.ai, writing secrets.env (git-ignored, readable only by
# you). Run it again after pulling: it moves its own Forge checkout to a new pin and rebuilds. A Forge checkout it did
# not make is never moved.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
FORGE_URL=https://github.com/Card-Forge/forge.git
PIN="$(tr -d '[:space:]' < "$REPO/FORGE_COMMIT")"
forge="$HOME/code/forge"

while [ $# -gt 0 ]; do
  case "$1" in
    --forge-dir) [ $# -ge 2 ] || { echo "setup: --forge-dir needs a directory" >&2; exit 2; }; forge="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "setup: unknown flag $1 (try --help)" >&2; exit 2 ;;
  esac
done
mkdir -p "$(dirname "$forge")"
forge="$(cd "$(dirname "$forge")" && pwd)/$(basename "$forge")"
step() { echo; echo "==> $*"; }

# ---- 1. tools ---------------------------------------------------------------------------------------------------
# The Homebrew JDK on a Mac is not on PATH until linked; run-llm-spike.sh uses the same one.
for jdk in /opt/homebrew/opt/openjdk@17 /usr/local/opt/openjdk@17; do
  if [ -x "$jdk/bin/java" ] && ! command -v java >/dev/null 2>&1; then export PATH="$jdk/bin:$PATH"; fi
done
case "$(uname -s)" in
  Darwin) hint="brew install openjdk@17 maven git python" ;;
  *) hint="sudo apt-get install openjdk-17-jdk maven git python3   (or your system's package manager)" ;;
esac
missing=""
for t in git java mvn python3; do command -v "$t" >/dev/null 2>&1 || missing="$missing $t"; done
if [ -n "$missing" ]; then
  echo "setup: missing:$missing. Install them with:" >&2; echo "    $hint" >&2; exit 3
fi
jv="$(java -version 2>&1 | grep -v '^Picked up' | head -1 | sed -E 's/.*version "([0-9]+)(\.([0-9]+))?.*/\1 \3/')"
major="${jv%% *}"; [ "$major" = 1 ] && major="${jv##* }"
case "$major" in ''|*[!0-9]*) major=0 ;; esac
if [ "$major" -lt 17 ]; then
  echo "setup: Forge needs Java 17 or newer; 'java -version' says $(java -version 2>&1 | grep -v '^Picked up' | head -1)." >&2
  echo "    $hint" >&2; exit 3
fi
step "tools: git, Java $major, Maven, Python 3"

# ---- 2. Forge at the pin ----------------------------------------------------------------------------------------
marker="$forge/.git/llm-agent-setup"          # inside .git: never shows as a change in the checkout
if [ ! -e "$forge" ]; then
  step "fetching Forge $PIN into $forge (shallow, about 1 GB)"
  git init -q "$forge"
  git -C "$forge" remote add origin "$FORGE_URL"
  git -C "$forge" fetch --depth 1 origin "$PIN"
  git -C "$forge" checkout -q -f FETCH_HEAD
  : > "$marker"
else
  [ -d "$forge/.git" ] || { echo "setup: $forge exists and is not a git checkout; pick another --forge-dir" >&2; exit 4; }
  head="$(git -C "$forge" rev-parse HEAD 2>/dev/null || echo unknown)"
  if [ "$head" != "$PIN" ]; then
    if [ ! -e "$marker" ]; then
      echo "setup: $forge is a Forge checkout this script did not make, at $head; this repository is tested" >&2
      echo "    against $PIN. Move it there yourself (git -C $forge fetch --depth 1 $FORGE_URL $PIN &&" >&2
      echo "    git -C $forge checkout FETCH_HEAD), or give setup a new --forge-dir." >&2
      exit 4
    fi
    step "moving Forge from $head to the pin $PIN"
    git -C "$forge" fetch --depth 1 origin "$PIN"
    git -C "$forge" checkout -q -f FETCH_HEAD
  else
    step "Forge is at the pin $PIN"
  fi
fi

# ---- 3. the overlay, the build, the classpath -------------------------------------------------------------------
step "laying this repository's changes on Forge"
FORGE_HOME="$forge" bash "$REPO/install.sh" > /dev/null
step "building (the first build downloads Forge's libraries; later ones take a minute or two)"
(cd "$forge" && mvn -q -pl forge-gui-desktop -am install -DskipTests)
(cd "$forge" && mvn -q -pl forge-gui-desktop dependency:build-classpath -Dmdep.outputFile=/tmp/forge_cp.txt)
echo "$forge" > "$REPO/.forge-home"           # where play-vs-llm.sh finds Forge

# ---- 4. the OpenRouter key and the consent ----------------------------------------------------------------------
secrets="$REPO/secrets.env"
if [ -f "$secrets" ] && grep -Eq '^[[:space:]]*OPENROUTER_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]' "$secrets"; then
  step "secrets.env already holds a key; left as it is"
else
  step "your OpenRouter key (https://openrouter.ai/keys); it is stored in secrets.env, readable only by you"
  key="${OPENROUTER_API_KEY:-}"
  if [ -z "$key" ]; then
    printf "OpenRouter key (input hidden): "
    if [ -t 0 ]; then read -r -s key; echo; else read -r key || true; fi
  fi
  echo "A game against an openrouter: model sends the board and the model's view of the game to openrouter.ai."
  printf "Allow that? [y/N] "
  read -r yes || yes=""
  case "$yes" in y|Y|yes|YES) consent="OPENROUTER_ALLOW_EGRESS=1" ;; *) consent="# OPENROUTER_ALLOW_EGRESS=1" ;; esac
  (umask 077; printf '# Written by setup.sh. Never committed (.gitignore).\nOPENROUTER_API_KEY=%s\n%s\n' "$key" "$consent" \
    > "$secrets")
  chmod 600 "$secrets"
  [ -n "$key" ] || echo "setup: no key given; add it to secrets.env (OPENROUTER_API_KEY=...) before playing" >&2
  case "$consent" in \#*) echo "setup: consent not given; play-vs-llm.sh will refuse an openrouter: model until you" \
      "uncomment OPENROUTER_ALLOW_EGRESS=1 in secrets.env" >&2 ;; esac
fi

step "done. Play:  bash play-vs-llm.sh      (bash play-vs-llm.sh --help for decks, models and the live commentary)"
