# Twin cards: zero-model engine check (2026-11-06)

Branch feat/twin-cards (bb524e3). Zero-model only; nothing paid, no egress. Evidence (run directories) is git-ignored under `runs/twin-exp`.

## Setup
- Private checkout: `cp -al $FORGE_HOME <twin copy>` (pinned 34a8662b, matches FORGE_COMMIT). Only new files
  were added: `forge-gui/res/cardsfolder/zz_twins/*.txt` (107) and `forge-gui/res/editions/Spike Twins.txt`.
- Twins: `puzzle_twin.py make --seed 1 --forge <twin copy>` per suite into `runs/twin-exp/{gen,rules}`;
  gen 12 boards / 46 cards / 0 refused; rules 26 of 29 / 67 cards / 3 refused (RP_035 Animate Dead names Worldgorger
  Dragon; RP_124 engine names Reanimate; RP_243 Thing in the Ice two faces). 6 cards overlap; same twin names and
  same twin sha in both. Merged to `runs/twin-exp/merged-manifest.json` (107 cards), then `install --forge` the copy.
- Gen boards (2 per family, open, active): drain 0154/08bb, elf 4658/70a8, fling 05dc/0a55, ping 0134/1178,
  splice 0551/06da, storm 5bf9/88f9.
- Build: `HARNESS_DEPS_FILE=<an earlier acceptance deps.txt> STAGES="classpath compile" FORGE_HOME=<twin copy>
  OUT=runs/harness-acceptance/twin-exp` (PASS, overlay 0582191d06fb, 4 patches). No mvn ran in the copy. Preflight
  skipped (other agents' JVMs live). The classpath names the copy's target/classes (hardlinks of $FORGE_HOME's).
- Where cards come from: res is read from the filesystem, `../forge-gui/res` from cwd `$FORGE/forge-gui-desktop`
  (Build.run cwd); no forge jar or target/classes holds a cardsfolder (unzip/ls checked). $FORGE_HOME has no zz_twins,
  so every twin that resolved came from the copy; the negative control (below) shows a removal in the copy bites.
- All runs: `puzzle_baselines.py --build runs/harness-acceptance/twin-exp --forge <twin copy>`, -j 1 or 2.
- Comparator: a comparison script (not kept) maps twin names
  back, drops timing/sha/path keys, compares every game-*.jsonl event (prompt, options, answers) and result row.
  Sanity: an empty name map gives HARD diffs; two different boards give COUNT 70 vs 36.

## Results (all criteria pass)
| check | count |
|---|---|
| gen `line --pzl` seed 1, original and twin | 24/24 played+solved; 12/12 originals' log_sha256 = stored replay_log_sha256 |
| gen line logs, name-mapped | 12/12 identical (strict) |
| rules `prove --budget 600` (orig + twin) | 52/52 proven; 26/26 same index line and same rollout count |
| rules proof line vs stored search reference line | 5/5 equal (the other 21 stored lines are random tapes) |
| rules `verify` (stored line replay) | originals 26/26 verified; twin rows all solved/proven, name-mapped 26/26 identical |
| gen baselines random20, pass, first, greedy, ai (x4) | 60/60 arm counts twin = original = frozen suite |
| rules baselines random50, pass, first, floor, ai (x4) | 130/130 twin = original; 129/130 = frozen (RP_050 floor, my artifact, below) |
| name-mapped baseline logs | gen 46 identical + 14 truncation-only; rules 130/130 identical |
| `non-existent card` in any jvm.log | 0 (lines, proofs, verify, baselines) |
| negative control | removed twin of Carrion Feeder; drain-0154 twin: "48 lines unplayed", exit 1, acceptance rejected, stderr `ERROR: Tried to create a non-existent card named Sioshir Dromifioth` |

## Findings
1. No hidden name dependency and no menu-order change: every index-based proof line and every seeded floor replays
   the same; every decision sees the same options in the same order.
2. Presentation difference (not rules): the seat's word-boundary cut (`LlmSpikeGui.java:11436`, append "...") falls
   elsewhere when a twin name is shorter or longer, so a cut description shows a few more or fewer words (e.g.
   "[Zone Changer: Carrion Feeder...]" vs "[Zone Changer: Sioshir...]"; a log line cut in the original and whole in
   the twin). 14 of 60 gen baseline arms, 0 rules. Accepted when the two agree to within 30 chars of the earlier
   cut. A twin name cut mid-name leaves a fragment, never an original. A paired twin score includes this difference.
3. `verify` marks 22 rules twins failed: "replayed game log differs from the recorded one". The log hash carries
   names; a twin suite needs its own replay_log_sha256 (or a name-mapped hash). The 4 search lines passed.
4. Hazard in `install` (not fixed, it is by design): it deletes every zz_twins script not in the manifest, so
   installing the rules manifest after the gen one removes the gen twins, and those boards then lose cards in
   silence. I had to merge the two by hand. Twin names are per-suite (assign_twins attempt bumps), so two suites'
   names for one card could also differ. Suggest one manifest for all suites, or install refusing a clash.
5. RP_050 floor n=8 vs 4: I restarted the run with --resume; the killed run's orphaned JVM appended rows to the same
   dir. A clean rerun (`runs/twin-exp/base-rules-050`) gives 0/4 for both, name-mapped identical.

## Not determined / cut
- Lines on seed 1 only (the reference seed), not seeds 1..3; no `--minimality`; prove only with `--opponent search`.
- Name-mapped prove comparison covers the result row only (the search runner writes no game jsonl).
- Forge's own game log (logLines) is never written to disk; compared through the seat prompts' log and the solver
  actions, not line by line. Opponent-only actions outside the prompts are not compared.
- The comparator ignores keys matching timing/sha/path/model/date/log; a difference only in such a key is unseen.
- Only 12 of 84 open gen boards; held-out boards and the RP_035/124/243 refusals untested.

## Java check (TwinCardsTest, added after this note)
`forge-overlay/.../TwinCardsTest.java` makes the twins as the runner does (`puzzle_twin.py make`, then `install`
into a scratch checkout holding only the originals' scripts under `runs/twin-cards-test`, deleted after; the
checkout is not touched), reads the scripts with Forge's `CardStorageReader`, enters them in the card database as
cards no edition lists, and checks all 141 twinned boards (26 of rules-sample-v1, 115 of hard-gen-v1, every active
puzzle): every twin name is in the database, every name on every board resolves and none is an original's, each
board built by the runner's own start path holds exactly its listed cards in hand, battlefield, graveyard, library
and exile, and that placement equals the original board's with names mapped. 6 tests green, 0 findings. Red on a
broken twin: a deleted twin script (RP_190) turned three checks red, naming Basoth Siden; a misspelled name and an
unknown name are caught in-test. Not covered: stack lines (`precast`, `putonstack`) are checked for resolution only;
the proof replay stays the Python run above (it needs a launched game, not a unit test).

## Cleanup
- <twin copy> deleted (rm -rf). Before that: nothing under $FORGE_HOME newer than the start, and no
  multi-link file in the copy modified. Evidence kept: runs/twin-exp (88 MB) and the build
  runs/harness-acceptance/twin-exp (3.4 MB); both are under runs/ and untracked.
