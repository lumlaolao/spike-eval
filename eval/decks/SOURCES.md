# Format decks: where each list came from

The Modern, Standard and Pauper decks are real top-8 lists from MTGTop8 (fetched 2026-10-15), copied as MTGO
exported them (main deck and sideboard). Two names were corrected to the engine's spelling, which keeps the
accents MTGO drops: Troll of Khazad-dûm, Lórien Revealed. Each deck is checked against the pinned engine's
format data by `eval/deck_legality.py` (and `eval/test_deck_legality.py`); `run_batch.py --format` refuses a
deck that is not legal. Each `.plan` names the archetype and the placement, nothing more: the seat gets the
decklist and every card's rules text with it.

Metagame shares at the time (MTGTop8, last two weeks): Standard, Dimir Aggro 12%, UW Control 12%, Izzet
Control 11%; Modern, Broodscale Bloodchief 19%, UR Aggro 10%; Pauper, Affinity 11%, Burn 10%, Dimir Control 10%.

| Deck | Archetype | Player | Place | Event | Date | List |
|---|---|---|---|---|---|---|
| `standard-ur-aggro.txt` | UR Aggro | mami | 1st | MTGO Challenge 32 | 2026-10-13 | https://www.mtgtop8.com/event?e=92285&d=901035 |
| `standard-dimir-aggro.txt` | Dimir Aggro | fazparte | 2nd | MTGO Challenge 32 | 2026-10-13 | https://www.mtgtop8.com/event?e=92285&d=901036 |
| `standard-dimir-control.txt` | Dimir Control | _IlNano_ | 3-4 | MTGO Challenge 32 | 2026-10-13 | https://www.mtgtop8.com/event?e=92285&d=901026 |
| `standard-uw-control.txt` | UW Control | SoulStrong | 3-4 | MTGO Challenge 32 | 2026-10-13 | https://www.mtgtop8.com/event?e=92285&d=901028 |
| `standard-boros-dragons.txt` | Boros Dragons | MattTumavitch13 | 5-8 | MTGO Challenge 32 | 2026-10-13 | https://www.mtgtop8.com/event?e=92285&d=901027 |
| `standard-izzet-spellementals.txt` | Izzet Spellementals | Pompuskaya | 5-8 | MTGO Challenge 32 | 2026-10-13 | https://www.mtgtop8.com/event?e=92285&d=901034 |
| `standard-mono-green-landfall.txt` | Mono Green Landfall | jtl005 | 5-8 | MTGO Challenge 32 | 2026-10-13 | https://www.mtgtop8.com/event?e=92285&d=901030 |
| `modern-urzatron.txt` | UrzaTron | kthanakit26 | 1st | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92282&d=900999 |
| `modern-rakdos-delirium.txt` | Rakdos Delirium | LucasG1ggs | 2nd | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92282&d=900993 |
| `modern-esper-blink.txt` | Esper Blink | Dasikk | 3-4 | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92282&d=901007 |
| `modern-mono-black-aggro.txt` | Mono Black Aggro | Selfeisek | 3-4 | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92282&d=900998 |
| `modern-broodscale.txt` | Broodscale Bloodchief | masongrode | 5-8 | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92282&d=900994 |
| `modern-ur-cutter-prowess.txt` | UR Cutter Prowess | AJV20 | 5-8 | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92282&d=900995 |
| `pauper-madness-burn.txt` | Madness Burn | MisterTwin | 1st | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92295&d=901115 |
| `pauper-weenie-white.txt` | Weenie White | cabrabr12 | 2nd | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92295&d=901110 |
| `pauper-dimir-mystic.txt` | Dimir Mystic | Beicodegeia | 3-4 | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92295&d=901106 |
| `pauper-mono-blue-delver.txt` | Mono Blue Delver | discoverN | 3-4 | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92295&d=901101 |
| `pauper-grixis-affinity.txt` | Grixis Affinity | manohito | 5-8 | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92295&d=901109 |
| `pauper-hot-dogs.txt` | Hot Dogs | Mirozel1 | 5-8 | MTGO Challenge 32 | 2026-10-14 | https://www.mtgtop8.com/event?e=92295&d=901103 |
