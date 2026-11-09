# Misreading probes on RP_001 (PR #441)

`rp001-probes.jsonl`: one line per probe, with the line played, the CLI's last line, the build's overlay hash,
the result row's probe fields, and the first `[ROUTER] play` / `target` lines of the game's `jvm.log`
(the choice the seat took at each menu). Zero-model, no network.

**Input:** `eval/puzzles/rules-pilot/RP_001.pzl`, sha256 `759ac618c31296240ceb4c2660551fa6a8f527e097ee17eb7f895fdf0fc4f144`.
**Build:** overlay sha `edaca55afdf6`, compiled with
`STAGES="preflight classpath compile javatests" FORGE_HOME=/root/forge OUT=<build> bash eval/harness_acceptance.sh`.

**Command**, once per line below:

```bash
FORGE_HOME=/root/forge python3 eval/puzzles/puzzle_baselines.py --build <build> probe \
  --pzl eval/puzzles/rules-pilot/RP_001.pzl --line "<line>" --out-dir <dir>
```

| Line | Verdict | Exit |
|---|---|---|
| `Solver:4:1 Solver:4:1 Solver:3:1 Solver:3:1 Solver:2:0` (the search arm's proof) | accepted | 1 |
| `Solver:4:0` | rejected | 0 |
| `Solver:4:1` | accepted | 1 |
| `Solver:4:2` | rejected | 0 |
| `Solver:4:3` | rejected | 0 |
| `Solver:4:0 Solver:2:0` (the whole misreading) | rejected | 0 |
| `Solver:4:0 Solver:2:0 Solver:2:0` | line not consumed: 2 of 3 steps | 1 |

**The first menu** is inferred from the four one-step probes' first `[ROUTER] play` line: 0 casts Ray of
Revelation, 1 taps a Plains for mana, 2 plays Dark Depths, 3 passes. The log names the choice taken, not the
menu. Each one-step probe is that choice plus one seeded random completion (seed 0), so its verdict says
nothing about the rule: `Solver:4:1` reaches the state because the random finish then played Dark Depths
and cast Ray in the proof's order.

**The whole misreading** (`misread_line` of RP_001 in `eval/puzzles/rules-pilot/manifest.jsonl`: Ray of
Revelation on Blood Moon before Dark Depths) is `Solver:4:0 Solver:2:0`. Its row is `rejected`, with only
`tokens p0 Battlefield "Marit Lage" = 1 (was 0)` failing: Blood Moon is in the graveyard, and Dark Depths
entered with its ice counters. Each verdict is one seeded completion; whether ANY completion of the
misreading is accepted is not determined here (WORK-PLAN, the search-arm follow-up).

The run directories were not kept; this file holds what the PR cites from them.
