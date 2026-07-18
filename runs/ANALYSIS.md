# Decision-corpus analysis

## runs/20260717-193807-floor-vs-random

```
files=40  game_lines=40  missing_game_line->invalid=0  skipped_malformed_lines=0  legacy_short_lines=0  decisions=5071

--- decision counts by type x tier ---
type                  auto   total
play                  3979    3979
target                 557     557
attackers              252     252
card-name               92      92
mulligan                80      80
discard                 56      56
confirm                 38      38
entity                  12      12
choose-entity            5       5

--- fallbacks by normalized reason ---
play(no-budget)                       362   e.g. Identity-A t1 play: 'play(no-budget): land Karakas' [game-1-1784317090784.jsonl]
card-name(random)                      92   e.g. Identity-B t11 card-name: 'card-name(random)->Acererak the Archlich' [game-1-1784317095798.jsonl]
mulligan(no-model)                     40   e.g. Identity-A t0 mulligan: 'mulligan(no-model)->KEEP' [game-1-1784317090784.jsonl]
mulligan(random)                       40   e.g. Identity-B t0 mulligan: 'mulligan(random)->KEEP' [game-1-1784317090784.jsonl]
discard(forced)                         5   e.g. Identity-B t5 discard: 'discard(forced) -> Ajani, Nacatl Pariah' [game-10-1784317228986.jsonl]
choose-entity(no-budget)                5   e.g. Identity-A t80 choose-entity: 'choose-entity(no-budget)->first []' [game-11-1784317249744.jsonl]

--- llm-identity fallback tiers (seats whose controller is llm) ---
no llm arm in this scope -> rates not applicable
NOTE (ai tier is PARTIAL): the ai counter only sees fallbacks inside overridden methods. Combat damage order, X announcement, edict sacrifices, mana tapping and mulligan bottoming are un-overridden and always run on Forge AI invisibly, emitting no decision line. ai=0 does NOT mean the model drove every decision.

--- latency by tier (ms; only lines with latency_ms>0 — several sites hardcode 0 even when a model ran) ---
tier           n      p50      p90      max
auto           6        1        1        1

--- option-count (n_options) by type ---
type                 menus   no-menu   bad   min   med   p90   max
play                  3617       362     0     2     2     4     9
target                 557         0     0     1     3     8    25
attackers              252         0     0     1     3     8    26
card-name                0        92     0     -     -     -     -
mulligan                 0        80     0     -     -     -     -
discard                 51         5     0     2     7     7     7
confirm                 38         0     0     2     2     2     2
entity                  12         0     0     2     2     2     2
choose-entity            0         5     0     -     -     -     -

--- first-option pick rate (index-choice types, n_options>1, 0<=choice<n; model = model answered, fallback-decided lines excluded) ---
type                model_n  P(first)  1/n base   auto_n  P(first)
confirm                   0         -         -       38     52.6%
entity                    0         -         -       12     50.0%
play                      0         -         -     2125     71.7%
target                    0         -         -      476     35.3%
(play baseline uses 1/(n-1): the play menu's last slot is PASS, logged as choice=-1, so choice==n_options-1 is structurally unreachable for the non-pass lines counted here)

--- per-game decision volume ---
games_with_decisions=40  mean=126.8  max=542

--- prompt/raw_reply coverage ---
model-consulted (prompt!=null): 0   full round-trip (prompt+raw_reply): 0   prompt-without-reply (bridge died mid-call): 0   contaminated replies: 0

--- ai-tier handoff audit (invariant: zero; each line is bug-adjacent) ---
(none)
(the ai counter is a LOWER BOUND — see the ai-PARTIAL note in the llm-identity section above)
```

## runs/haiku-vs-random

```
files=20  game_lines=20  missing_game_line->invalid=0  skipped_malformed_lines=0  legacy_short_lines=0  decisions=2781

--- decision counts by type x tier ---
type                 smart    fast    auto      ai   total
play                   575     873     723       0    2171
target                  91       0     127       0     218
attackers              110       0      63       0     173
blockers                12       0      49       0      61
mulligan                31       0      20       0      51
card-name               15       0      25       1      41
discard                 11       0      19       0      30
confirm                  0      12      16       0      28
entity                   5       0       3       0       8

--- fallbacks by normalized reason ---
blockers(random)                       49   e.g. Identity-B t7 blockers: 'blockers(random)->0' [game-1-1784317462139.jsonl]
card-name(random)                      25   e.g. Identity-B t13 card-name: 'card-name(random)->A-Capenna Express' [game-1-1784318101167.jsonl]
mulligan(random)                       20   e.g. Identity-B t0 mulligan: 'mulligan(random)->KEEP' [game-1-1784317462139.jsonl]
discard(forced)                         6   e.g. Identity-B t5 discard: 'discard(forced) -> Ajani, Nacatl Pariah' [game-10-1784356419550.jsonl]
attackers(bridge-down)                  3   e.g. Identity-A t12 attackers: 'attackers(bridge-down)->all-in x3' [game-5-1784328531476.jsonl]
play(bridge-down)                       1   e.g. Identity-A t20 play: 'play(bridge-down): land Wasteland' [game-2-1784319944952.jsonl]
target(unparsed-or-dead)                1   e.g. Identity-A t14 target: 'target(unparsed-or-dead)->first' [game-5-1784328531476.jsonl]
card-name(no-match)                     1   e.g. Identity-A t12 card-name: 'card-name(no-match)' [game-7-1784352282551.jsonl]

--- llm-identity fallback tiers (seats whose controller is llm) ---
llm identities: Identity-A
llm decisions=1745   ai=1 (0.1%)   auto=9 (0.5%)   smart/fast-with-fallback-reason=0
(aggregate.py's gate numerator = ai + auto + smart/fast-with-fallback-reason)
NOTE (ai tier is PARTIAL): the ai counter only sees fallbacks inside overridden methods. Combat damage order, X announcement, edict sacrifices, mana tapping and mulligan bottoming are un-overridden and always run on Forge AI invisibly, emitting no decision line. ai=0 does NOT mean the model drove every decision.

--- latency by tier (ms; only lines with latency_ms>0 — several sites hardcode 0 even when a model ran) ---
tier           n      p50      p90      max
auto           5        1        1        1
fast         881     3776    16647    78905
smart        579    15602    32590    86600

--- option-count (n_options) by type ---
type                 menus   no-menu   bad   min   med   p90   max
play                  2170         1     0     2     2     5     9
target                 218         0     0     1     2     6    12
attackers              170         3     0     1     2     4    27
blockers                12        49     0     1     1     3     3
mulligan                31        20     0     2     2     2     2
card-name                0        41     0     -     -     -     -
discard                 24         6     0     2     3     6     6
confirm                 28         0     0     2     2     2     2
entity                   8         0     0     2     2     4     4

--- first-option pick rate (index-choice types, n_options>1, 0<=choice<n; model = model answered, fallback-decided lines excluded) ---
type                model_n  P(first)  1/n base   auto_n  P(first)
confirm                  12     66.7%     50.0%       16     75.0%
entity                    5     80.0%     46.7%        3     33.3%
mulligan                 31     58.1%     50.0%        0         -
play                    337     61.1%     51.4%      423     66.9%
target                   83     32.5%     36.0%       99     33.3%
(play baseline uses 1/(n-1): the play menu's last slot is PASS, logged as choice=-1, so choice==n_options-1 is structurally unreachable for the non-pass lines counted here)

--- per-game decision volume ---
games_with_decisions=20  mean=139.1  max=345

--- prompt/raw_reply coverage ---
model-consulted (prompt!=null): 1733   full round-trip (prompt+raw_reply): 1729   prompt-without-reply (bridge died mid-call): 4   contaminated replies: 0

--- ai-tier handoff audit (invariant: zero; each line is bug-adjacent) ---
  runs/haiku-vs-random/game-7-1784352282551.jsonl seat=Identity-A turn=12 type=card-name fallback='card-name(no-match)'
(the ai counter is a LOWER BOUND — see the ai-PARTIAL note in the llm-identity section above)
```

## runs/haiku-vs-floor

```
files=20  game_lines=19  missing_game_line->invalid=1  skipped_malformed_lines=0  legacy_short_lines=0  decisions=1676
  (no game line, decisions still counted: runs/haiku-vs-floor/game-5-1784364509072.jsonl)

--- decision counts by type x tier ---
type                 smart    fast    auto      ai   total
play                   409     952      81       0    1442
attackers               90       0       1       0      91
target                  56       0       1       0      57
mulligan                28       0      20       1      49
confirm                  0      13       0       0      13
discard                 12       0       0       0      12
card-name               10       0       0       0      10
entity                   2       0       0       0       2

--- fallbacks by normalized reason ---
play(no-budget)                        77   e.g. Identity-B t2 play: 'play(no-budget): land Wasteland' [game-1-1784357698665.jsonl]
mulligan(no-model)                     20   e.g. Identity-B t0 mulligan: 'mulligan(no-model)->KEEP' [game-1-1784357698665.jsonl]
play(bridge-down)                       4   e.g. Identity-A t1 play: 'play(bridge-down): land Plateau' [game-2-1784359438407.jsonl]
mulligan(bridge-down)                   1   e.g. Identity-A t0 mulligan: 'mulligan(bridge-down)' [game-5-1784364509072.jsonl]
target(unparsed-or-dead)                1   e.g. Identity-A t12 target: 'target(unparsed-or-dead)->first' [game-7-1784368559385.jsonl]
attackers(bridge-down)                  1   e.g. Identity-A t12 attackers: 'attackers(bridge-down)->all-in x2' [game-7-1784368559385.jsonl]

--- llm-identity fallback tiers (seats whose controller is llm) ---
llm identities: Identity-A
llm decisions=1579   ai=1 (0.1%)   auto=6 (0.4%)   smart/fast-with-fallback-reason=0
(aggregate.py's gate numerator = ai + auto + smart/fast-with-fallback-reason)
NOTE (ai tier is PARTIAL): the ai counter only sees fallbacks inside overridden methods. Combat damage order, X announcement, edict sacrifices, mana tapping and mulligan bottoming are un-overridden and always run on Forge AI invisibly, emitting no decision line. ai=0 does NOT mean the model drove every decision.

--- latency by tier (ms; only lines with latency_ms>0 — several sites hardcode 0 even when a model ran) ---
tier           n      p50      p90      max
fast         961     3081    13338    66220
smart        409    15604    33887    74752

--- option-count (n_options) by type ---
type                 menus   no-menu   bad   min   med   p90   max
play                  1361        81     0     2     3     4    10
attackers               90         1     0     1     3     7    23
target                  57         0     0     2     4     7    11
mulligan                28        21     0     2     2     2     2
confirm                 13         0     0     2     2     2     2
discard                 12         0     0     3     5     7     7
card-name                0        10     0     -     -     -     -
entity                   2         0     0     2     2     2     2

--- first-option pick rate (index-choice types, n_options>1, 0<=choice<n; model = model answered, fallback-decided lines excluded) ---
type                model_n  P(first)  1/n base   auto_n  P(first)
confirm                  13     69.2%     50.0%        0         -
entity                    2    100.0%     50.0%        0         -
mulligan                 28     60.7%     50.0%        0         -
play                    232     43.5%     45.8%        0         -
target                   56     58.9%     31.6%        0         -
(play baseline uses 1/(n-1): the play menu's last slot is PASS, logged as choice=-1, so choice==n_options-1 is structurally unreachable for the non-pass lines counted here)

--- per-game decision volume ---
games_with_decisions=20  mean=83.8  max=196

--- prompt/raw_reply coverage ---
model-consulted (prompt!=null): 1579   full round-trip (prompt+raw_reply): 1572   prompt-without-reply (bridge died mid-call): 7   contaminated replies: 0

--- ai-tier handoff audit (invariant: zero; each line is bug-adjacent) ---
  runs/haiku-vs-floor/game-5-1784364509072.jsonl seat=Identity-A turn=0 type=mulligan fallback='mulligan(bridge-down)'
(the ai counter is a LOWER BOUND — see the ai-PARTIAL note in the llm-identity section above)
```

## runs/haiku-vs-ai

```
files=20  game_lines=20  missing_game_line->invalid=0  skipped_malformed_lines=0  legacy_short_lines=0  decisions=405

--- decision counts by type x tier ---
type                 smart    fast    auto   total
play                   250     104       0     354
mulligan                39       0       0      39
attackers                3       0       3       6
target                   2       0       0       2
card-name                1       0       0       1
entity                   1       0       0       1
confirm                  0       1       0       1
choose-entity            0       0       1       1

--- fallbacks by normalized reason ---
attackers(bridge-down)                  3   e.g. Identity-A t13 attackers: 'attackers(bridge-down)->all-in x2' [game-1-1784401570806.jsonl]
choose-entity(unparsed)                 1   e.g. Identity-A t14 choose-entity: 'choose-entity(unparsed)->first [Ajani, Nacatl Avenger]' [game-1-1784401570806.jsonl]

--- llm-identity fallback tiers (seats whose controller is llm) ---
llm identities: Identity-A
llm decisions=405   ai=0 (0.0%)   auto=4 (1.0%)   smart/fast-with-fallback-reason=0
(aggregate.py's gate numerator = ai + auto + smart/fast-with-fallback-reason)
NOTE (ai tier is PARTIAL): the ai counter only sees fallbacks inside overridden methods. Combat damage order, X announcement, edict sacrifices, mana tapping and mulligan bottoming are un-overridden and always run on Forge AI invisibly, emitting no decision line. ai=0 does NOT mean the model drove every decision.

--- latency by tier (ms; only lines with latency_ms>0 — several sites hardcode 0 even when a model ran) ---
tier           n      p50      p90      max
fast         105     1553    10267    57947
smart        251      964     3867    35739

--- option-count (n_options) by type ---
type                 menus   no-menu   bad   min   med   p90   max
play                   354         0     0     2     4     5    10
mulligan                39         0     0     2     2     2     2
attackers                3         3     0     1     2     3     3
target                   2         0     0     2     2     4     4
card-name                0         1     0     -     -     -     -
choose-entity            0         1     0     -     -     -     -
confirm                  1         0     0     2     2     2     2
entity                   1         0     0     2     2     2     2

--- first-option pick rate (index-choice types, n_options>1, 0<=choice<n; model = model answered, fallback-decided lines excluded) ---
type                model_n  P(first)  1/n base   auto_n  P(first)
confirm                   1    100.0%     50.0%        0         -
entity                    1    100.0%     50.0%        0         -
mulligan                 39      2.6%     50.0%        0         -
play                     12     33.3%     39.8%        0         -
target                    2      0.0%     37.5%        0         -
(play baseline uses 1/(n-1): the play menu's last slot is PASS, logged as choice=-1, so choice==n_options-1 is structurally unreachable for the non-pass lines counted here)

--- per-game decision volume ---
games_with_decisions=20  mean=20.2  max=149

--- prompt/raw_reply coverage ---
model-consulted (prompt!=null): 405   full round-trip (prompt+raw_reply): 405   prompt-without-reply (bridge died mid-call): 0   contaminated replies: 324
WARNING: 324 model-consulted line(s) carry a non-answer raw_reply (e.g. rate-limit message). These decisions were NOT driven by the model; the batch is contaminated. Patterns: you've hit your session limit=324

--- ai-tier handoff audit (invariant: zero; each line is bug-adjacent) ---
(none)
(the ai counter is a LOWER BOUND — see the ai-PARTIAL note in the llm-identity section above)
```

## runs/floor-vs-ai

```
files=20  game_lines=20  missing_game_line->invalid=0  skipped_malformed_lines=0  legacy_short_lines=0  decisions=93

--- decision counts by type x tier ---
type                  auto   total
play                    72      72
mulligan                20      20
choose-entity            1       1

--- fallbacks by normalized reason ---
play(no-budget)                        72   e.g. Identity-A t1 play: 'play(no-budget): land Arid Mesa' [game-1-1784403734551.jsonl]
mulligan(no-model)                     20   e.g. Identity-A t0 mulligan: 'mulligan(no-model)->KEEP' [game-1-1784403734551.jsonl]
choose-entity(no-budget)                1   e.g. Identity-A t8 choose-entity: 'choose-entity(no-budget)->first []' [game-5-1784403774377.jsonl]

--- llm-identity fallback tiers (seats whose controller is llm) ---
no llm arm in this scope -> rates not applicable
NOTE (ai tier is PARTIAL): the ai counter only sees fallbacks inside overridden methods. Combat damage order, X announcement, edict sacrifices, mana tapping and mulligan bottoming are un-overridden and always run on Forge AI invisibly, emitting no decision line. ai=0 does NOT mean the model drove every decision.

--- latency by tier (ms; only lines with latency_ms>0 — several sites hardcode 0 even when a model ran) ---
(no measured latencies)

--- option-count (n_options) by type ---
type                 menus   no-menu   bad   min   med   p90   max
play                     0        72     0     -     -     -     -
mulligan                 0        20     0     -     -     -     -
choose-entity            0         1     0     -     -     -     -

--- first-option pick rate (index-choice types, n_options>1, 0<=choice<n; model = model answered, fallback-decided lines excluded) ---
type                model_n  P(first)  1/n base   auto_n  P(first)
(no eligible lines)

--- per-game decision volume ---
games_with_decisions=20  mean=4.7  max=8

--- prompt/raw_reply coverage ---
model-consulted (prompt!=null): 0   full round-trip (prompt+raw_reply): 0   prompt-without-reply (bridge died mid-call): 0   contaminated replies: 0

--- ai-tier handoff audit (invariant: zero; each line is bug-adjacent) ---
(none)
(the ai counter is a LOWER BOUND — see the ai-PARTIAL note in the llm-identity section above)
```

## COMBINED (5 dirs)

```
files=120  game_lines=119  missing_game_line->invalid=1  skipped_malformed_lines=0  legacy_short_lines=0  decisions=10026
  (no game line, decisions still counted: runs/haiku-vs-floor/game-5-1784364509072.jsonl)

--- decision counts by type x tier ---
type                 smart    fast    auto      ai   total
play                  1234    1929    4855       0    8018
target                 149       0     685       0     834
attackers              203       0     319       0     522
mulligan                98       0     140       1     239
card-name               26       0     117       1     144
discard                 23       0      75       0      98
confirm                  0      26      54       0      80
blockers                12       0      49       0      61
entity                   8       0      15       0      23
choose-entity            0       0       7       0       7

--- fallbacks by normalized reason ---
play(no-budget)                       511   e.g. Identity-A t1 play: 'play(no-budget): land Karakas' [game-1-1784317090784.jsonl]
card-name(random)                     117   e.g. Identity-B t11 card-name: 'card-name(random)->Acererak the Archlich' [game-1-1784317095798.jsonl]
mulligan(no-model)                     80   e.g. Identity-A t0 mulligan: 'mulligan(no-model)->KEEP' [game-1-1784317090784.jsonl]
mulligan(random)                       60   e.g. Identity-B t0 mulligan: 'mulligan(random)->KEEP' [game-1-1784317090784.jsonl]
blockers(random)                       49   e.g. Identity-B t7 blockers: 'blockers(random)->0' [game-1-1784317462139.jsonl]
discard(forced)                        11   e.g. Identity-B t5 discard: 'discard(forced) -> Ajani, Nacatl Pariah' [game-10-1784317228986.jsonl]
attackers(bridge-down)                  7   e.g. Identity-A t12 attackers: 'attackers(bridge-down)->all-in x3' [game-5-1784328531476.jsonl]
choose-entity(no-budget)                6   e.g. Identity-A t80 choose-entity: 'choose-entity(no-budget)->first []' [game-11-1784317249744.jsonl]
play(bridge-down)                       5   e.g. Identity-A t20 play: 'play(bridge-down): land Wasteland' [game-2-1784319944952.jsonl]
target(unparsed-or-dead)                2   e.g. Identity-A t14 target: 'target(unparsed-or-dead)->first' [game-5-1784328531476.jsonl]
card-name(no-match)                     1   e.g. Identity-A t12 card-name: 'card-name(no-match)' [game-7-1784352282551.jsonl]
mulligan(bridge-down)                   1   e.g. Identity-A t0 mulligan: 'mulligan(bridge-down)' [game-5-1784364509072.jsonl]
choose-entity(unparsed)                 1   e.g. Identity-A t14 choose-entity: 'choose-entity(unparsed)->first [Ajani, Nacatl Avenger]' [game-1-1784401570806.jsonl]

--- llm-identity fallback tiers (seats whose controller is llm) ---
llm identities: Identity-A
llm decisions=3729   ai=2 (0.1%)   auto=19 (0.5%)   smart/fast-with-fallback-reason=0
(aggregate.py's gate numerator = ai + auto + smart/fast-with-fallback-reason)
NOTE (ai tier is PARTIAL): the ai counter only sees fallbacks inside overridden methods. Combat damage order, X announcement, edict sacrifices, mana tapping and mulligan bottoming are un-overridden and always run on Forge AI invisibly, emitting no decision line. ai=0 does NOT mean the model drove every decision.

--- latency by tier (ms; only lines with latency_ms>0 — several sites hardcode 0 even when a model ran) ---
tier           n      p50      p90      max
auto          11        1        1        1
fast        1947     3326    14844    78905
smart       1239    12468    30868    86600

--- option-count (n_options) by type ---
type                 menus   no-menu   bad   min   med   p90   max
play                  7502       516     0     2     2     4    10
target                 834         0     0     1     3     7    25
attackers              515         7     0     1     2     7    27
mulligan                98       141     0     2     2     2     2
card-name                0       144     0     -     -     -     -
discard                 87        11     0     2     5     7     7
confirm                 80         0     0     2     2     2     2
blockers                12        49     0     1     1     3     3
entity                  23         0     0     2     2     2     4
choose-entity            0         7     0     -     -     -     -

--- first-option pick rate (index-choice types, n_options>1, 0<=choice<n; model = model answered, fallback-decided lines excluded) ---
type                model_n  P(first)  1/n base   auto_n  P(first)
confirm                  26     69.2%     50.0%       54     59.3%
entity                    8     87.5%     47.9%       15     46.7%
mulligan                 98     36.7%     50.0%        0         -
play                    581     53.5%     48.9%     2548     70.9%
target                  141     42.6%     34.2%      575     35.0%
(play baseline uses 1/(n-1): the play menu's last slot is PASS, logged as choice=-1, so choice==n_options-1 is structurally unreachable for the non-pass lines counted here)

--- per-game decision volume ---
games_with_decisions=120  mean=83.5  max=542

--- prompt/raw_reply coverage ---
model-consulted (prompt!=null): 3717   full round-trip (prompt+raw_reply): 3706   prompt-without-reply (bridge died mid-call): 11   contaminated replies: 324
WARNING: 324 model-consulted line(s) carry a non-answer raw_reply (e.g. rate-limit message). These decisions were NOT driven by the model; the batch is contaminated. Patterns: you've hit your session limit=324

--- ai-tier handoff audit (invariant: zero; each line is bug-adjacent) ---
  runs/haiku-vs-random/game-7-1784352282551.jsonl seat=Identity-A turn=12 type=card-name fallback='card-name(no-match)'
  runs/haiku-vs-floor/game-5-1784364509072.jsonl seat=Identity-A turn=0 type=mulligan fallback='mulligan(bridge-down)'
(the ai counter is a LOWER BOUND — see the ai-PARTIAL note in the llm-identity section above)
```

## Play-tightening: the un-routed surface

```
decision types seen ONLY with tier auto/ai (never routed to a model):
  choose-entity      7 line(s): auto=7

structurally invisible surfaces (NO decision lines exist for these, by construction):
  - combat damage assignment order
  - X announcement (choosing X when casting)
  - edict sacrifice choice (opponent-forced sacrifices)
  - mana tapping / payment ordering
  - mulligan bottoming (which cards go to the bottom after a keep)
These surfaces are STRUCTURALLY INVISIBLE: they produce no decision line at all, so their absence from this corpus is itself the point — zero lines is not zero decisions, it is zero routing. Additionally, chooseTargetsFor early-super returns (multi-target spells, sub-ability targeting) are silent Forge-AI targeting handoffs not counted as tier ai.
```
