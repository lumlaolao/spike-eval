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

# apple.awt.UIElement: run as a macOS "accessory" process — no Dock icon, and
# critically it CANNOT become the active app, so launching a game no longer
# steals focus from whatever you are typing in. Matters most for batches, where
# every game is a fresh JVM (a 12-wide batch stole focus 12 times a minute).
#
# Do NOT "improve" this to -Djava.awt.headless=true. Forge's desktop code
# initializes AWT/Swing during model load even on the headless path, so
# headless=true dies with ExceptionInInitializerError / HeadlessException before
# the first turn (verified 2026-07-26). This flag suppresses the app's UI
# presence while leaving AWT working; the seed-42 floor canary is unchanged
# (identity A wins in 108 turns) with it set.
exec java $ADDOPENS \
  -Xmx4096m \
  -Dio.netty.tryReflectionSetAccessible=true \
  -Dfile.encoding=UTF-8 \
  -Djava.util.Arrays.useLegacyMergeSort=true \
  -Dsun.java2d.d3d=false \
  -Dapple.awt.UIElement=true \
  -cp "$CP" \
  forge.spike.LlmSpikeHeadless
