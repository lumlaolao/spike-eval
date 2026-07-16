#!/usr/bin/env bash
# Copy the overlay + launcher into a Forge checkout.
# Usage: FORGE_HOME=~/code/forge ./install.sh
set -e

FORGE_HOME="${FORGE_HOME:-$HOME/code/forge}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$FORGE_HOME/forge-gui-desktop" ]; then
  echo "ERROR: '$FORGE_HOME' does not look like a Forge checkout (no forge-gui-desktop/)." >&2
  echo "Clone it first:  git clone --depth 1 https://github.com/Card-Forge/forge.git \"$FORGE_HOME\"" >&2
  exit 1
fi

echo "Overlaying spike files into $FORGE_HOME ..."
cp -Rv "$HERE/forge-overlay/." "$FORGE_HOME/"
cp -v  "$HERE/run-llm-spike.sh" "$FORGE_HOME/run-llm-spike.sh"
cp -v  "$HERE/run-llm-headless.sh" "$FORGE_HOME/run-llm-headless.sh"
chmod +x "$FORGE_HOME/run-llm-spike.sh" "$FORGE_HOME/run-llm-headless.sh"

# Reproducibility pin: eval results are only comparable against the same engine version.
PIN_FILE="$HERE/FORGE_COMMIT"
CUR="$(git -C "$FORGE_HOME" rev-parse HEAD 2>/dev/null || echo unknown)"
if [ -f "$PIN_FILE" ]; then
  PINNED="$(cat "$PIN_FILE")"
  if [ "$CUR" != "$PINNED" ]; then
    echo "WARNING: Forge checkout is at $CUR but the overlay was validated against $PINNED" >&2
    echo "         (update FORGE_COMMIT deliberately after re-validating, or checkout the pin)" >&2
  fi
else
  echo "$CUR" > "$PIN_FILE"
  echo "Recorded Forge engine pin: $CUR"
fi
echo "Done. See README.md for build & run steps."
