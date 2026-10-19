#!/bin/bash
# waitg.sh NN : wait until the run_batch writing live/gNN exits (bracketed pattern cannot match this script)
while pgrep -f "[r]un_batch.py --forge-home.*live/g$1 " >/dev/null; do sleep 60; done
tail -5 ${SCRATCH:-/tmp/puzzle-scratch}/live/g$1.launch.log
