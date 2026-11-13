#!/usr/bin/env bash
# Copy the overlay (the Java and the run-llm-*.sh launchers) into a Forge checkout, and write the engine patches into it.
# Usage: FORGE_HOME=~/code/forge ./install.sh
set -e

FORGE_HOME="${FORGE_HOME:-$HOME/code/forge}"
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/scripts/platform.sh"                # python3 under that name on Windows too

if [ ! -d "$FORGE_HOME/forge-gui-desktop" ]; then
  echo "ERROR: '$FORGE_HOME' does not look like a Forge checkout (no forge-gui-desktop/)." >&2
  echo "Run 'bash setup.sh' instead: it fetches Forge at the tested commit (FORGE_COMMIT), then calls this." >&2
  exit 1
fi

# Reproducibility pin: eval results are only comparable against the same engine version. Checked before anything is
# written: the engine patches are applied to the pin's originals, so on another commit they would build a mixed engine.
PIN_FILE="$HERE/FORGE_COMMIT"
CUR="$(git -C "$FORGE_HOME" rev-parse HEAD 2>/dev/null || echo unknown)"
if [ -f "$PIN_FILE" ]; then
  PINNED="$(cat "$PIN_FILE")"
  if [ "$CUR" != "$PINNED" ]; then
    echo "Forge checkout is at $CUR but the overlay was validated against $PINNED" >&2
    if [ "${ALLOW_PIN_MISMATCH:-}" != 1 ]; then
      echo "ERROR: check out the pin first (git -C $FORGE_HOME checkout $PINNED), or update FORGE_COMMIT deliberately" >&2
      echo "       after re-validating; ALLOW_PIN_MISMATCH=1 installs anyway (a mixed engine, never a score)." >&2
      exit 1
    fi
    echo "WARNING: ALLOW_PIN_MISMATCH=1: installing on another engine commit" >&2
  fi
else
  echo "$CUR" > "$PIN_FILE"
  echo "Recorded Forge engine pin: $CUR"
fi

# The seat's package (forge/spike) is ours alone: a file the overlay no longer has (renamed or deleted) would still be
# compiled into the checkout build and loaded by a game, so it is removed before the copy.
for kind in main test; do
  for f in "$FORGE_HOME"/forge-gui-desktop/src/$kind/java/forge/spike/*.java; do
    [ -f "$f" ] || continue
    if [ ! -f "$HERE/forge-overlay/forge-gui-desktop/src/$kind/java/forge/spike/$(basename "$f")" ]; then
      rm -f "$f"
      echo "Removed $f: the overlay no longer has it"
    fi
  done
done

echo "Overlaying spike files into $FORGE_HOME ..."
cp -Rv "$HERE/forge-overlay/." "$FORGE_HOME/"
chmod +x "$FORGE_HOME/run-llm-spike.sh" "$FORGE_HOME/run-llm-headless.sh"

# The Comprehensive Rules text the RULES tool reads. The play scripts resolve it relative to their
# OWN directory (the checkout they were copied into), so it has to live there too -- otherwise
# RULES_TOOL=1 logs "rules text did not load" and silently leaves the seat without the tool.
if [ -f "$HERE/data/rules/MagicCompRules.txt" ]; then
  mkdir -p "$FORGE_HOME/data/rules"
  cp -v "$HERE/data/rules/MagicCompRules.txt" "$FORGE_HOME/data/rules/MagicCompRules.txt"
fi

# Our changes to Forge's own files are diffs (engine-patches/, docs/harness/ENGINE-PATCHES.md), applied to each
# file as it is at the pin (git show, never the working tree). One that does not apply stops the install here.
echo "Writing the engine patches into $FORGE_HOME ..."
python3 "$HERE/eval/engine_patches.py" apply --forge "$FORGE_HOME" --out "$FORGE_HOME"

# Twin cards (eval/puzzles/puzzle_twin.py), OPT-IN: the generated card scripts of a twin puzzle set go into this
# checkout's cardsfolder (zz_twins/, a Custom_Set edition), regenerated from the checkout's own originals and checked
# against the manifest's hashes. They are not in forge-overlay/ (the overlay holds no res/ and a twin is derived from
# a third-party script) and the overlay_sha does not cover them: a twin record's twin_set_sha256 does. Without
# TWIN_MANIFEST nothing is written. TWIN_MODE=keep leaves twins another manifest installed, =replace deletes them;
# with neither, another manifest's twins stop the install.
if [ -n "${TWIN_MANIFEST:-}" ]; then
  case "${TWIN_MODE:-}" in
    keep) TWIN_FLAG=--keep ;;
    replace) TWIN_FLAG=--replace ;;
    "") TWIN_FLAG= ;;
    *) echo "ERROR: TWIN_MODE must be keep or replace, not '$TWIN_MODE'." >&2; exit 1 ;;
  esac
  echo "Installing the twin cards of $TWIN_MANIFEST into $FORGE_HOME ..."
  python3 "$HERE/eval/puzzles/puzzle_twin.py" install --manifest "$TWIN_MANIFEST" --forge "$FORGE_HOME" $TWIN_FLAG
fi
echo "Done. See README.md for build & run steps."
