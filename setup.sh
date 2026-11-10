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
# not make is never moved. macOS, Linux, and Windows in Git Bash (scripts/platform.sh holds the differences).
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
. "$REPO/scripts/platform.sh"
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
# The Homebrew JDK goes first when it is there: it is keg-only (not on PATH), macOS always has a /usr/bin/java that
# only says no Java is installed, and Homebrew's mvn would otherwise build on its own newest JDK. run-llm-spike.sh
# plays on the same one. SETUP_JDK_DIRS replaces the list (a test seam).
for jdk in ${SETUP_JDK_DIRS:-/opt/homebrew/opt/openjdk@17 /usr/local/opt/openjdk@17}; do
  if [ -x "$jdk/bin/java" ]; then
    export PATH="$jdk/bin:$PATH"
    if [ -d "$jdk/libexec/openjdk.jdk/Contents/Home" ]; then export JAVA_HOME="$jdk/libexec/openjdk.jdk/Contents/Home"; fi
    break
  fi
done
if [ "$ON_WINDOWS" = 1 ]; then
  hint="winget install EclipseAdoptium.Temurin.17.JDK Python.Python.3.12   and Maven from
    https://maven.apache.org/download.cgi (unzip it and put its bin folder on PATH); then open a new Git Bash"
else
  case "$(uname -s)" in
    Darwin) hint="brew install openjdk@17 maven git python" ;;
    *) hint="sudo apt-get install openjdk-17-jdk maven git python3   (or your system's package manager)" ;;
  esac
fi
missing=""
for t in git java mvn; do command -v "$t" >/dev/null 2>&1 || missing="$missing $t"; done
# Run, not looked up: Windows has a python3 that only says how to install it.
python3 -c "" >/dev/null 2>&1 || missing="$missing python3"
if [ -n "$missing" ]; then
  echo "setup: missing:$missing. Install them with:" >&2; echo "    $hint" >&2; exit 3
fi
# `|| true`: a java that fails (the macOS stub) must reach the message below, not end the script in silence.
jline="$(java -version 2>&1 | grep -v '^Picked up' | head -1 || true)"
jv="$(echo "$jline" | sed -E 's/.*version "([0-9]+)(\.([0-9]+))?.*/\1 \3/')"
major="${jv%% *}"; [ "$major" = 1 ] && major="${jv##* }"
case "$major" in ''|*[!0-9]*) major=0 ;; esac
if [ "$major" -lt 17 ]; then
  echo "setup: Forge needs Java 17 or newer; 'java -version' says: $jline" >&2
  echo "    $hint" >&2; exit 3
fi
# Forge's build enforces Maven 3.8.1 or newer and Java 17 for the build (its pom's requireMavenVersion and
# requireJavaVersion); some apt releases ship Maven 3.6, and Maven builds on $JAVA_HOME, not the java on PATH.
mvn_out="$(mvn -v 2>&1 || true)"
mver="$(echo "$mvn_out" | grep -oE 'Apache Maven [0-9]+\.[0-9]+\.[0-9]+' | head -1 | sed 's/Apache Maven //' || true)"
IFS=. read -r m1 m2 m3 <<< "${mver:-0.0.0}"
if [ $(( m1 * 10000 + m2 * 100 + m3 )) -lt 30801 ]; then
  echo "setup: Forge's build needs Maven 3.8.1 or newer; 'mvn -v' says:" >&2
  echo "$mvn_out" | head -3 | sed 's/^/    /' >&2
  echo "    brew install maven, or https://maven.apache.org/download.cgi (an older apt maven is too old)" >&2; exit 3
fi
mjava="$(echo "$mvn_out" | sed -nE 's/^Java version: ([0-9]+).*/\1/p' | head -1)"
if [ -n "$mjava" ] && [ "$mjava" -lt 17 ]; then
  echo "setup: Maven builds on Java $mjava (its JAVA_HOME); Forge needs 17 or newer. Point JAVA_HOME at a JDK 17+" >&2
  echo "    or unset it, then run setup again." >&2; exit 3
fi
step "tools: git, Java $major, Maven $mver, Python 3"

# ---- 2. Forge at the pin ----------------------------------------------------------------------------------------
# The marker lives inside .git (never a change in the checkout) and is written as soon as the checkout exists, so a
# fetch that drops midway (a network cut, ^C) leaves a checkout the next run recognises and finishes.
marker="$forge/.git/llm-agent-setup"
if [ ! -e "$forge" ]; then
  git init -q "$forge"
  : > "$marker"
  # Forge's files as they are at the pin. Its .gitattributes says text=auto, so on Windows git would check them
  # out with CRLF whatever core.autocrlf says; core.eol lf is the setting that stops it.
  git -C "$forge" config core.autocrlf false
  git -C "$forge" config core.eol lf
  git -C "$forge" remote add origin "$FORGE_URL"
elif [ ! -d "$forge/.git" ]; then
  echo "setup: $forge exists and is not a git checkout; pick another --forge-dir" >&2; exit 4
fi
head="$(git -C "$forge" rev-parse HEAD 2>/dev/null || echo unknown)"
if [ "$head" = "$PIN" ]; then
  step "Forge is at the pin $PIN"
elif [ -e "$marker" ]; then
  step "fetching Forge $PIN into $forge (shallow, about 1 GB)"
  git -C "$forge" fetch --depth 1 origin "$PIN"
  git -C "$forge" checkout -q -f FETCH_HEAD
else
  echo "setup: $forge is a Forge checkout this script did not make, at $head; this repository is tested" >&2
  echo "    against $PIN. Move it there yourself (git -C $forge fetch --depth 1 $FORGE_URL $PIN &&" >&2
  echo "    git -C $forge checkout FETCH_HEAD), or give setup a new --forge-dir." >&2
  exit 4
fi

# ---- 3. the overlay, the build, the classpath -------------------------------------------------------------------
# The Comprehensive Rules text (Wizards', so not in this repository) for the optional RULES tool; install.sh copies
# it into the checkout. Without the network the tool alone is missing, so a failed download only warns.
step "fetching the Comprehensive Rules text"
python3 "$REPO/eval/fetch_comp_rules.py" || echo "WARNING: no rules text: the RULES tool (RULES_TOOL=1) will be missing" >&2
step "laying this repository's changes on Forge"
FORGE_HOME="$forge" bash "$REPO/install.sh" > /dev/null
step "building (the first build downloads Forge's libraries; later ones take a minute or two)"
# -DskipLaunch4j: Forge's package phase wraps a Windows .exe that no game uses, with a bundled x86 tool that does
# not run on an Apple silicon Mac without Rosetta or on arm64 Linux, and the build stopped there.
(cd "$forge" && mvn -q -pl forge-gui-desktop -am install -DskipTests -DskipLaunch4j)
(cd "$forge" && mvn -q -pl forge-gui-desktop -am dependency:build-classpath \
    -Dmdep.outputFile="$(native_path "${FORGE_CP_FILE:-/tmp/forge_cp.txt}")")
echo "$forge" > "$REPO/.forge-home"           # where play-vs-llm.sh finds Forge

# ---- 4. the OpenRouter key and the consent ----------------------------------------------------------------------
secrets="$REPO/secrets.env"
if [ -f "$secrets" ] && grep -Eq '^[[:space:]]*OPENROUTER_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]' "$secrets"; then
  if grep -Eq '^[[:space:]]*OPENROUTER_ALLOW_EGRESS[[:space:]]*=[[:space:]]*1' "$secrets"; then
    allow="already allows games to be sent to openrouter.ai (OPENROUTER_ALLOW_EGRESS=1)"
  else
    allow="does not allow games to be sent to openrouter.ai yet"
  fi
  step "secrets.env already holds a key and $allow; left as it is. Not yours? Delete it and run setup again."
else
  step "your OpenRouter key (https://openrouter.ai/keys); it is stored in secrets.env, readable only by you"
  # A key already in the shell is used only when the person says so: the default is no, and with nobody at the
  # terminal (an agent's or a CI shell) it is never written to the file. SETUP_ASSUME_TTY=1 is a test seam.
  key=""
  if [ -n "${OPENROUTER_API_KEY:-}" ]; then
    if [ -t 0 ] || [ "${SETUP_ASSUME_TTY:-}" = 1 ]; then
      printf "An OpenRouter key (OPENROUTER_API_KEY) is set in your shell. Use it and save it to secrets.env? [y/N] "
      read -r use || use=""
      use="${use%$'\r'}"
      case "$use" in y|Y|yes|YES) key="$OPENROUTER_API_KEY" ;; esac
    else
      echo "setup: a key is set in your shell (OPENROUTER_API_KEY) and was not saved, because nobody is at the" \
        "terminal to ask. play-vs-llm.sh still uses the key from your shell while it is set." >&2
    fi
  fi
  if [ -z "$key" ]; then
    printf "OpenRouter key (input hidden): "
    if [ -t 0 ]; then read -r -s key || true; echo; else read -r key || true; fi
    key="${key%$'\r'}"
  fi
  echo "A game against an openrouter: model sends the board and the model's view of the game to openrouter.ai."
  printf "Allow that? [y/N] "
  read -r yes || yes=""
  yes="${yes%$'\r'}"
  case "$yes" in y|Y|yes|YES) consent="OPENROUTER_ALLOW_EGRESS=1" ;; *) consent="# OPENROUTER_ALLOW_EGRESS=1" ;; esac
  (umask 077
   printf '# Written by setup.sh. Never committed (.gitignore).\nOPENROUTER_API_KEY=%s\n%s\n' "$key" "$consent" \
     > "$secrets")
  chmod 600 "$secrets"
  [ -n "$key" ] || echo "setup: no key given; add it to secrets.env (OPENROUTER_API_KEY=...) before playing" >&2
  case "$consent" in \#*) echo "setup: consent not given; play-vs-llm.sh will refuse an openrouter: model until you" \
      "uncomment OPENROUTER_ALLOW_EGRESS=1 in secrets.env" >&2 ;; esac
fi

step "done. Play:  bash play-vs-llm.sh      (bash play-vs-llm.sh --help for decks, models and the live commentary)"
