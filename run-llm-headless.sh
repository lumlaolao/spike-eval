#!/usr/bin/env bash
# Run the LLM-vs-LLM match HEADLESS (no window) — console log only.
# Run from the forge repo root; it cd's into forge-gui-desktop so assets resolve to ../forge-gui/res.
set -e
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
# Ensure the `claude` CLI (used by HaikuDecider) is on PATH for the launched JVM.
PATH="$JAVA_HOME/bin:$HOME/.local/bin:$PATH"
export PATH

cd "$(dirname "$0")/forge-gui-desktop"

CP="target/classes:$(cat /tmp/forge_cp.txt)"

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
  forge.spike.LlmSpikeHeadless
