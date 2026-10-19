#!/bin/bash
# usage: gtest.sh <methods-or-class-spec>  (compiles, then runs)
R=/home/user/mtg-forge-llm-agent
cd $R && STAGES="classpath compile" FORGE_HOME=/root/forge bash eval/harness_acceptance.sh > ${SCRATCH:-/tmp/puzzle-scratch}/gtest-build.txt 2>&1 || { tail -5 ${SCRATCH:-/tmp/puzzle-scratch}/gtest-build.txt; exit 1; }
O=$(grep -o '/home/user/mtg-forge-llm-agent/runs/harness-acceptance/[0-9TZ]*' ${SCRATCH:-/tmp/puzzle-scratch}/gtest-build.txt | tail -1)
cd /root/forge/forge-gui-desktop && MEMORY_TOOLS=1 java -Dapple.awt.UIElement=true -Dharness.ownership.tsv=$R/docs/harness/controller-ownership.tsv -Dharness.rules.file=$R/data/rules/MagicCompRules.txt -Dharness.decks.dir=$R/eval/decks -Dharness.tooluse.json=$R/eval/tooluse/tools.json -cp "$O/classes:$(cat $O/classpath.txt)" org.testng.TestNG "$@" -d ${SCRATCH:-/tmp/puzzle-scratch}/tng60 2>&1 | grep -E "Total tests|Failures:|^FAILED${GREP:+|$GREP}" | head
