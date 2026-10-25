#!/usr/bin/env bash
# Launch the Forge desktop GUI with our LLM-Agent controller: a spectator match, or a person against the model
# (CONTROLLER_A=human, which play-vs-llm.sh sets).
# Run from the forge repo root; it cd's into forge-gui-desktop so assets resolve to ../forge-gui/res.
set -e
# Repo root, captured BEFORE the cd below: the rules tool needs an absolute CR path.
REPO="$(cd "$(dirname "$0")" && pwd)"
# The Homebrew JDK on a Mac; anywhere else (Linux, CI) the java already on PATH is used.
# Apple silicon and Intel prefixes, in setup.sh's order (eval/test_setup.py keeps the two lists equal).
for BREW in /opt/homebrew/opt/openjdk@17 /usr/local/opt/openjdk@17; do
  BREW_JDK="$BREW/libexec/openjdk.jdk/Contents/Home"
  if [ -d "$BREW_JDK" ]; then export JAVA_HOME="$BREW_JDK"; break; fi
done
# Ensure the `claude` CLI (ClaudeCliBridge, the claude-cli: development lane) is on PATH for the launched JVM.
PATH="${JAVA_HOME:+$JAVA_HOME/bin:}$HOME/.local/bin:$PATH"
export PATH

cd "$(dirname "$0")/forge-gui-desktop"

# The classpath file is a build artifact in /tmp, so it vanishes on a /tmp
# sweep or reboot while the compiled classes stay put. Without this guard the
# only symptom is `cat: /tmp/forge_cp.txt: No such file or directory` and
# exit 1 from EVERY game in a batch, naming neither the cause nor the fix --
# that silently emptied a full overnight run. Deliberately NOT self-healing:
# regenerating has no lock, so N parallel launchers would race each other
# (eval/CONCURRENCY.md). Refuse loudly and let the caller regenerate once.
CP_FILE="${FORGE_CP_FILE:-/tmp/forge_cp.txt}"     # FORGE_CP_FILE: elsewhere (play-vs-llm.sh passes it on)
if [ ! -s "$CP_FILE" ]; then
  echo "[launcher] missing or empty $CP_FILE (maven-generated classpath)." >&2
  echo "[launcher] regenerate it ONCE, with no games running:" >&2
  echo "    cd $(cd .. && pwd) && mvn -q -o -pl forge-gui-desktop -am \\" >&2
  echo "        dependency:build-classpath -Dmdep.outputFile=$CP_FILE" >&2
  exit 3
fi
CP="target/classes:$(cat "$CP_FILE")"

ADDOPENS="--add-opens java.desktop/java.beans=ALL-UNNAMED \
--add-opens java.desktop/javax.swing.border=ALL-UNNAMED \
--add-opens java.desktop/javax.swing.event=ALL-UNNAMED \
--add-opens java.desktop/sun.swing=ALL-UNNAMED \
--add-opens java.desktop/java.awt.image=ALL-UNNAMED \
--add-opens java.desktop/java.awt.color=ALL-UNNAMED \
--add-opens java.desktop/sun.awt.image=ALL-UNNAMED \
--add-opens java.desktop/javax.swing=ALL-UNNAMED \
--add-opens java.desktop/java.awt=ALL-UNNAMED \
--add-opens java.desktop/java.awt.font=ALL-UNNAMED \
--add-opens java.base/java.util=ALL-UNNAMED \
--add-opens java.base/java.lang=ALL-UNNAMED \
--add-opens java.base/java.lang.reflect=ALL-UNNAMED \
--add-opens java.base/java.text=ALL-UNNAMED \
--add-opens java.base/jdk.internal.misc=ALL-UNNAMED \
--add-opens java.base/sun.nio.ch=ALL-UNNAMED \
--add-opens java.base/java.nio=ALL-UNNAMED \
--add-opens java.base/java.math=ALL-UNNAMED \
--add-opens java.base/java.util.concurrent=ALL-UNNAMED \
--add-opens java.base/java.net=ALL-UNNAMED"

# ---- ALWAYS-ON MATCH MODEL (2026-10-13) -------------------------------------------------------
# See run-llm-headless.sh: the same defaults, so the GUI and headless seats play the same way.
# Compare with the baseline:  CONTEXT_PROFILE=v1-compat MEMORY_TOOLS=0 RULES_TOOL=0 ./run-llm-spike.sh
export CONTEXT_PROFILE="${CONTEXT_PROFILE:-v4-faithful}"
export MEMORY_TOOLS="${MEMORY_TOOLS:-1}"
export RULES_TOOL="${RULES_TOOL:-1}"
export RULES_FILE="${RULES_FILE:-$REPO/data/rules/MagicCompRules.txt}"

exec java $ADDOPENS \
  -Xmx4096m \
  -Dio.netty.tryReflectionSetAccessible=true \
  -Dfile.encoding=UTF-8 \
  -Djava.util.Arrays.useLegacyMergeSort=true \
  -Dsun.java2d.d3d=false \
  -cp "$CP" \
  forge.spike.LlmSpikeGui
