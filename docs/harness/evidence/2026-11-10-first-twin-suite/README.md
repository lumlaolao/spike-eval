# First real twin suite: do the zero-model arms play twins as they play originals? (issue #453)

**The question.** A twin keeps each card's rules text and gives it an invented name, so a faithful twin must play
identically for random, PASS, first-option, the floor, Forge's AI and the search prover. Any difference is a
twin-building defect (or the documented description-cut effect: a seat cut a long description by length, which no
zero-model arm reads). This measures it on the whole rules sample, before any paid model comparison.

**Answer.** None found. On 26 twin puzzles, 130 puzzle-arms and 5,616 games per side (same seeds), every result
row, every decision trace (turn, phase, type, option count, choice, each option's text with the card names mapped),
and every `[ROUTER]` line matches; the 26 search proofs match; `puzzle_score.py --twin-results` scores the
zero-model arms and prints a recall gap of exactly 0.000 for each. Rows: `paired-rows.jsonl`.

## Inputs and build

| | |
|---|---|
| Checkout | `d85546e66ec52cd86f249c0acda59ae9bcd24869` (main, merge of #454) |
| Build overlay sha | `edaca55afdf6` (`STAGES="preflight classpath compile" FORGE_HOME=/root/forge`, `eval/harness_acceptance.sh`) |
| Engine | Forge `34a8662b7966a6ee757411feae2ae8ff38cce436` |
| Source suite | `eval/suites/puzzles-rules-sample-v1.json`, sha256 `1115d67f6c901fe906e0f2a5d4aa88abe1331324c9d368d31ada2a3c969d29ff` (its `suite_sha256` field `b652e12c4452239d335e83a7edc890015ed74bd1220c88dd7ef029318775be2f`); 36 records, 29 active, all open split |
| Twin manifest | `twin-manifest.json`, file sha256 `7f0b507d4ac2dfa7e78e7b466805c0382593e2d5b30e2daf37de0bb72914ac8e`, `--seed 1`; 67 cards, 68 scripts installed (the edition file included) |
| Candidate twin suite | file sha256 `70c580433be5821ca9f6d11598724cd08cca022fc51bc84f3029b3f31ae911a8` (26 candidate records) |
| Frozen twin suite | file sha256 `e42b14c5e9e91183c16d5ae2c653d47ccefe9b44010fcda79234b1ced102dac5`, `suite_sha256` `31ec06bfa2c181056034a18e6e4751fc5c134c6e880381f618282fb9f8e1ea75` (25 active) |

Twin boards, scripts, the manifest and the suites are under `runs/twins/first-v1/` and not committed: the boards are
third-party works, and the manifest maps each original name to its twin name, which would let a later model
read the invented names back to the cards. This folder holds ids, hashes and counts only.

## Commands (all zero-model; `FORGE_HOME=/root/forge`; `T=runs/twins/first-v1`)

```bash
python3 eval/puzzles/puzzle_twin.py make --suite eval/suites/puzzles-rules-sample-v1.json --ids <the 29 active ids> --seed 1 --out $T
python3 eval/puzzles/puzzle_twin.py install --manifest $T/twin-manifest.json --forge /root/forge
python3 eval/puzzles/puzzle_twin.py suite --manifest $T/twin-manifest.json --suite eval/suites/puzzles-rules-sample-v1.json --out $T/twin-suite.json
python3 eval/puzzles/puzzle_twin.py verify --suite $T/twin-suite.json           # 26 x ok, exit 0 (verify.log)
# the free arms, originals and twins, same seeds (random 1..200, the others 1..4), 200 rollouts, -j 4
python3 eval/puzzles/puzzle_baselines.py --build <build> run --suite $T/twin-suite.json --baselines-only --rollouts 200 -j 4 --write $T/twin-baselines.json --out-dir runs/twins/out-twin
python3 eval/puzzles/puzzle_baselines.py --build <build> run --suite eval/suites/puzzles-rules-sample-v1.json --ids <the 26 originals> --baselines-only --rollouts 200 -j 4 --write $T/orig-baselines.json --out-dir runs/twins/out-orig
# the search prover, budget 500, opponent search (the default)
python3 eval/puzzles/puzzle_baselines.py --build <build> prove --suite $T/twin-suite.json --budget 500 -j 4 --write $T/twin-proofs.json --out-dir runs/twins/prove-twin
python3 eval/puzzles/puzzle_baselines.py --build <build> prove --suite eval/suites/puzzles-rules-sample-v1.json --ids <the 26 originals> --budget 500 -j 4 --write $T/orig-proofs.json --out-dir runs/twins/prove-orig
# to make the twins scoreable: lines + gate, then freeze
cp $T/twin-proofs.json $T/twin-measured.json
python3 eval/puzzles/puzzle_baselines.py --build <build> run --suite $T/twin-measured.json --rollouts 200 -j 4 --resume --out-dir runs/twins/out-twin
python3 eval/puzzles/puzzle_format.py freeze $T/twin-measured.json --out $T/twin-frozen.json   # 25 active
# the pairing, once per arm (k=200 for random, 4 for the others; --solver ai-log for the ai arm)
python3 eval/puzzles/puzzle_score.py --suite eval/suites/puzzles-rules-sample-v1.json --split open --k 4 --solver pass \
  --ids <the 25 originals> --twin-suite $T/twin-frozen.json --twin-results runs/twins/out-twin/*/pass/puzzle-results.jsonl \
  runs/twins/out-orig/*/pass/puzzle-results.jsonl
# the comparison (this folder)
python3 docs/harness/evidence/2026-11-10-first-twin-suite/compare_twins.py --orig runs/twins/out-orig --twin runs/twins/out-twin \
  --manifest $T/twin-manifest.json --prove-orig $T/prove-orig.log --prove-twin $T/prove-twin.log --out paired-rows.jsonl
```

Wall time: each side's arms 24 min (about 80 MB of raw runs, deleted), each proof pass 8 min, the line gate 4 min.
Rollouts were the default 200 for random (the source suite was measured at 50), so no reduction was needed.

## Twinned and refused

26 of 29 active puzzles twinned (67 cards, one twin each, 68 scripts with the edition). Three refused by `make`
(`make.log`):

| Puzzle | Reason | Meaning |
|---|---|---|
| rules-RP_035 | Animate Dead: names another card (`named Worldgorger Dragon`) | the script mentions a second card by name, so renaming one would break the other |
| rules-RP_124 | the engine names Reanimate in its source | a Forge engine or AI string literal decides on that name |
| rules-RP_243 | Thing in the Ice: a card with more than one face is not twinned | two-faced card (the script's alternate face) |

The refusals are the tool's own guards, not defects; they leave 26 of 29 (90%) twinnable. `verify --suite` exited 0
on all 26.

## Result: original against twin

Cells are solves on the original = solves on the twin (random of 200 seeds, the others of 4). Every cell is equal,
and so is the underlying set of seeds (`results_sha256` equals `twin_results_sha256` in every row).

| Puzzle | random /200 | pass /4 | first /4 | floor /4 | ai /4 | proof (status, rollouts, opp. points, replies beaten, actions) |
|---|---|---|---|---|---|---|
| rules-RP_017 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 9, 0, 0, 1 |
| rules-RP_050 | 33 = 33 | 0 = 0 | 0 = 0 | 0 = 0 | 3 = 3 | proven, 4, 0, 0, 1 |
| rules-RP_055 | 2 = 2 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 24, 0, 0, 2 |
| rules-RP_098 | 19 = 19 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 8, 0, 0, 2 |
| rules-RP_151 | 33 = 33 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 7, 0, 0, 2 |
| rules-RP_190 | 28 = 28 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 17, 1, 3, 1 |
| rules-RP_197 | 31 = 31 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 4, 0, 0, 1 |
| rules-RP_204 | 19 = 19 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 16, 0, 5, 2 |
| rules-RP_210 | 58 = 58 | 0 = 0 | 4 = 4 | 0 = 0 | 4 = 4 | proven, 3, 0, 0, 1 |
| rules-RP_286 | 14 = 14 | 0 = 0 | 0 = 0 | 0 = 0 | 4 = 4 | proven, 2, 0, 0, 2 |
| rules-RP_324 | 16 = 16 | 0 = 0 | 4 = 4 | 0 = 0 | 0 = 0 | proven, 2, 0, 0, 2 |
| rules-RP_337 | 19 = 19 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 3, 0, 0, 1 |
| rules-RP_370 | 10 = 10 | 0 = 0 | 0 = 0 | 0 = 0 | 4 = 4 | proven, 13, 0, 0, 2 |
| rules-RP_385 | 8 = 8 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 12, 0, 0, 2 |
| rules-RP_411 | 19 = 19 | 0 = 0 | 4 = 4 | 0 = 0 | 4 = 4 | proven, 3, 0, 0, 1 |
| rules-RP_443 | 2 = 2 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 20, 0, 0, 3 |
| rules-RP_444 | 30 = 30 | 0 = 0 | 0 = 0 | 0 = 0 | 4 = 4 | proven, 6, 0, 0, 2 |
| rules-RP_449 | 14 = 14 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 14, 0, 3, 2 |
| rules-RP_458 | 17 = 17 | 0 = 0 | 0 = 0 | 0 = 0 | 4 = 4 | proven, 15, 0, 0, 3 |
| rules-RP_464 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 174, 0, 0, 2 |
| rules-RP_475 | 18 = 18 | 0 = 0 | 0 = 0 | 0 = 0 | 4 = 4 | proven, 6, 0, 0, 1 |
| rules-RP_477 | 36 = 36 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 9, 0, 1, 2 |
| rules-RP_520 | 12 = 12 | 0 = 0 | 4 = 4 | 0 = 0 | 0 = 0 | proven, 5, 0, 0, 4 |
| rules-RP_529 | 9 = 9 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 36, 0, 3, 3 |
| rules-RP_532 | 16 = 16 | 0 = 0 | 4 = 4 | 0 = 0 | 0 = 0 | proven, 3, 0, 0, 2 |
| rules-RP_542 | 3 = 3 | 0 = 0 | 0 = 0 | 0 = 0 | 0 = 0 | proven, 97, 0, 0, 2 |

Totals over 26 puzzles, original = twin: random 466 of 5,200, PASS 0 of 104, first 20 of 104, floor 0 of 104, ai 31
of 104.

| Check | Games | Differing |
|---|---|---|
| Result row (solved, turns, win condition, loss reason, solver actions, draws, status), per seed | 5,616 per side | 0 |
| Decision trace per game (turn, phase, type, option count, choice, option text with names mapped) | 5,616 per side, 17,439 decisions per side | 0 |
| `[ROUTER]` lines of the arm's `jvm.log`, names mapped | 130 logs | 0 |
| Search proof (status, rollouts, opponent choice points, replies beaten, actions) | 26 puzzles | 0 |

Because nothing differs, there is no first differing line to report.

**Why the check can fail (negative control).** Comparing the same games without the name map differs in 5,129 of 5,304
random and ai games, so the comparison reads the names and is not vacuous. Comparing option text uncut (not cut at
the description) also finds 0 differing games: the zero-model arms never see the seat's length-dependent description
cut, so that documented effect is invisible here and remains for the model arm only.

## The pairing score (`puzzle_score.py --twin-results`)

It accepts the zero-model arms' result rows, but only against a **frozen suite with active twin records**. The
candidate suite from `suite` is refused (`suite rules-twins has no active open twin puzzles`): a twin is active only
after the gate (`prove`, the line gate in `run`, `freeze`). After that it scores (`scores.txt`):

| Arm | solver | k | pairs | pass@1 gap (original - twin) | solved original / twin | discordant pairs |
|---|---|---|---|---|---|---|
| random | `random` | 200 | 25 | +0.000, CI [0.000, 0.000] | 0/25, 0/25 | 0 |
| PASS | `pass` | 4 | 25 | +0.000 | 0/25, 0/25 | 0 |
| first | `first` | 4 | 25 | +0.000 | 4/25, 4/25 | 0 |
| floor | `floor` | 4 | 25 | +0.000 | 0/25, 0/25 | 0 |
| Forge AI | `ai-log` | 4 | 25 | +0.000 | 7/25, 7/25 | 0 |

25 pairs, not 26: rules-RP_210's twin was dropped by the gate (`random_above_threshold`, random 58 of 200 = 0.29),
and its original measures the same 58 of 200 at 200 rollouts; the source suite kept it active on its 50-rollout
sample. That is a threshold effect of the rollout count, not a twin difference. Every row carries `PILOT` (a subset
of the suite, run with `--ids`). The "floors" the scorer prints for the recall gap include a random gap of +0.007,
the scorer's own estimate and not a measured difference here. McNemar says nothing with no discordant pairs. The
scorer's headline numbers on the originals are a side effect of scoring zero-model arms and not a result of this note.

## Cleanup and the state of `$FORGE_HOME`

`puzzle_twin.py` has no uninstall command. After measuring, the twins were removed with the tool's own semantics: `install --replace` with
the manifest emptied of cards (a copy under `runs/`), which deletes every twin script not in the new manifest:

```bash
python3 eval/puzzles/puzzle_twin.py install --manifest $T/empty-manifest.json --forge /root/forge --replace   # wrote 1 file
```

State left in `/root/forge`: no twin script; `forge-gui/res/cardsfolder/zz_twins/twins-installed.json` (an empty `{}`
record) and `forge-gui/res/editions/Spike Twins.txt` (a header with no cards), both untracked in the Forge checkout,
which the tool writes. The checkout's tracked files were not touched. `STAGES="preflight classpath compile javatests"`
acceptance passed with the 68 twin files installed (8 PASS lines). The run after the cleanup is reported in the
hand-off, not here (STANDARDS section 7: no final-head acceptance in a doc). Re-measuring needs `install` again (twins are refused unless installed).

## Not determined

- Anything about a model: names are the only change, and only the zero-model arms were played. The paired model
  score (about $0.40 on Luna, owner-gated) is not run, and neither is the description-cut effect on a model's
  reading.
- A single seeded Forge AI per seed: identical traces show the AI does not use names on these 26 boards, not that
  it never would (the 3 refusals are the cards the engine does name).
- The source suite's 7 dropped records, and the 3 refused puzzles, were not twinned.
- The search proof ran at budget 500, not the gate's `SEARCH_LINE_BUDGET`; identical on both sides, but the
  numbers are not the source suite's own.
- Whether the invented names are safe from recall (a model might know none of them) is a property of the name
  generator, not tested here.
- Only one `make` seed (1); another seed gives other names, and was not tried.

## `twin_set_sha256` per twin record

| Twin record | `twin_set_sha256` |
|---|---|
| rules-RP_017-twin | `000225a28caadce20acfbe9ec6e944cbef132bc57ec68373723f52401d2c98d7` |
| rules-RP_050-twin | `6d95831a2b0cd4a9843ad200cefb951d445f7d1eb12fc06806fa00733360d888` |
| rules-RP_055-twin | `3f3c1e35f6f29564e54d6ba58703a7b9e51346491136171426e8f9bf077c82c5` |
| rules-RP_098-twin | `1bc29af60dad7f2baa0ab4ad8f4f9e008b90adbc89a4b0f8883902e316640bc1` |
| rules-RP_151-twin | `6af5b3e85346a7ac254a5dbba70a8ee894b128eed9bc6a37b42e3615ead864f9` |
| rules-RP_190-twin | `52724fc380880c06304c1fab6d2b20bba4337d5b8bf7d288c7a3f9552397dec2` |
| rules-RP_197-twin | `64dd48b5f7ffa8b3aa4c420f4b02449d4d0344fa60ab750ab42849efd16f664b` |
| rules-RP_204-twin | `2772f375184b84fbaa62764e04355fdb7ef53e891165de2e92c725a010987df3` |
| rules-RP_210-twin | `0810d9791d99f95ba6f4aefb753729a346dd9ef0de69acb1b1303072be4fdb67` |
| rules-RP_286-twin | `e41a796f9fc6f4d0a67d63a4ac709ff572444af1609f1132ed04c7c7d6b9d20f` |
| rules-RP_324-twin | `be26130b06148e415d0a5af881eb6f8577bc2364884d2593bd0129af748fc89f` |
| rules-RP_337-twin | `a14be8114ee53f10812d9a341e58162d986b0619cb0c0ba2f5e31da53883e717` |
| rules-RP_370-twin | `88c2ff8ed00e4d67ee14a64cb0e1d3d16493d07ba30628411d92a6a226781ae1` |
| rules-RP_385-twin | `d01f74477a481018b288056f7ed11d72a0c49c93fff43433989e7ec10de2f861` |
| rules-RP_411-twin | `126add9b2eaa90387801250ffceff675e92ea5f8da6ab16604c3a55175a23f77` |
| rules-RP_443-twin | `f21426f375e44fdde6b8fcab29aea6a0754be0756fa7cb5ddb9df702d6141d02` |
| rules-RP_444-twin | `343cf51e1df5c2c8792b33e1e8226a1fdafac7818364de4f893929fe7858e292` |
| rules-RP_449-twin | `1e0bb397dc61f636c1f1851a657026d895afdbffc38baccbeb8825dc0ee0f5f6` |
| rules-RP_458-twin | `88172b8c9779aaa26416e3aa43330d5c1532993e32e503faca9b800e5e26f35a` |
| rules-RP_464-twin | `cebd22ae34e51f7ed36ee45ec1b14fcf207ad093b5e8a0d74c0c2a1108dbf7ba` |
| rules-RP_475-twin | `b34fbb63d8f2ffa0e13d3ee3f3894252a8ddab5e8c292534ef77d67462e66974` |
| rules-RP_477-twin | `fa14380e7510a9e9f763ac8c8a31c90ff53450942b433e773c183186ee627b52` |
| rules-RP_520-twin | `348d5d76855409fe3c668f914e93353d52532f635c46c421cec7a4b2a9fb4ac0` |
| rules-RP_529-twin | `ad95ba88accfd0423e2167d536ba19dfd5840ece9bcc806da09e158ee807a022` |
| rules-RP_532-twin | `9844623bfb690055233973e3c75c2ae74df757cdb4f4ac9f1ffc3dc80c00283a` |
| rules-RP_542-twin | `f231e8eab62db35a13e7681b604c92f50a78125edf51fba6bb0ed953fcc754c1` |
