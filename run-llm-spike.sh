#!/usr/bin/env bash
# Launch the Forge desktop GUI with our LLM-Agent controller in a spectator match.
# Run from the forge repo root; it cd's into forge-gui-desktop so assets resolve to ../forge-gui/res.
set -e
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
# Ensure the `claude` CLI (used by HaikuDecider) is on PATH for the launched JVM.
PATH="$JAVA_HOME/bin:$HOME/.local/bin:$PATH"
export PATH

cd "$(dirname "$0")/forge-gui-desktop"

# The classpath file is a build artifact in /tmp, so it vanishes on a /tmp
# sweep or reboot while the compiled classes stay put. Without this guard the
# only symptom is `cat: /tmp/forge_cp.txt: No such file or directory` and
# exit 1 from EVERY game in a batch, naming neither the cause nor the fix --
# that silently emptied a full overnight run. Deliberately NOT self-healing:
# regenerating has no lock, so N parallel launchers would race each other
# (see CLAUDE.md). Refuse loudly and let the caller regenerate once.
CP_FILE=/tmp/forge_cp.txt
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

exec java $ADDOPENS \
  -Xmx4096m \
  -Dio.netty.tryReflectionSetAccessible=true \
  -Dfile.encoding=UTF-8 \
  -Djava.util.Arrays.useLegacyMergeSort=true \
  -Dsun.java2d.d3d=false \
  -cp "$CP" \
  forge.spike.LlmSpikeGui
