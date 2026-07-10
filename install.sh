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
chmod +x "$FORGE_HOME/run-llm-spike.sh"
echo "Done. See README.md for build & run steps."
