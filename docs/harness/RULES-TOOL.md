# The RULES tool — the model looks the rules up, it does not recall them

**Status:** built and validated (acceptance stage `rules`). Off in the JVM and in `run_batch.py` unless asked
(`RULES_TOOL=1`, `run_batch.py --rules-tool`); the play scripts `run-llm-headless.sh` and `run-llm-spike.sh` turn it
on. A run without it sends the same prompt bytes as one from before it existed.

## Why

A real Legacy player knows the rules and can look them up; a model that *recalls* them confabulates
interaction rules (layers, replacement effects, state-based actions, the combat step order) under
exactly the pressure where it matters. The harness already hands each seat its own decklist **and its
cards' rules text from Forge's card DB** (`DECKLIST=1`), so the model never has to guess what a card
does. This is the other half of that principle: it never has to guess what a **rule** says either.

## What it is

`RulesTool` (overlay `forge.spike.RulesTool`) is a read-only index over the official
Comprehensive Rules text (`data/rules/MagicCompRules.txt`, effective 2026-09-25, downloaded once by
`eval/fetch_comp_rules.py` and checked against its sha256; see
`data/rules/README.md` for provenance and how to update it). It parses the text **directly** — there
is no derived index to drift — into rules (id → text), section/chapter headings, a table of contents,
and the Glossary.

The seat calls it as the native function `rules_lookup` (`action`: search, read, section, term or toc; `query`;
`offset`), which `NativeTools` turns into the executor's grammar below; that grammar is sent as text only to a
backend without function calling. It is **public, static reference text**: no engine state, no hidden
information, no network, no per-game mutation — which is precisely why it is safe to expose where the
other channels are seat-private.

## The executor's grammar (shares the lookup budget)

| Command | Returns |
|---|---|
| `RULES SEARCH <offset> <words>` | compact hits: rule id + one short line each |
| `RULES READ <rule> [offset]` | **one rule's full text** (+ the rule numbers it cites) |
| `RULES SECTION <number> [offset]` | a bounded **digest** of a whole section/chapter (id + one line per rule) |
| `RULES TERM <word>` | a Glossary definition |
| `RULES TOC [offset]` | chapter headings, to find a rule number |

## Context management (managed, never a dump)

The whole point is that this must not flood the prompt:

- **Every reply is bounded** at about 1200 characters (`RulesTool.PAGE_CHARS`) and self-describing:
  `... chars a..b/N next=<offset|END>`. The model pages with the `next=` offset.
- **Digest first, full text second.** `SECTION`/`SEARCH` return ids + one-liners; the model `READ`s
  only the specific rules in question. So the common case costs a few hundred characters, not a
  chapter.
- **Tool budget.** It shares the lookups' loop guards (`ContextMemory.TOOLS_PER_ASK` and `TOOLS_PER_GAME`,
  [MODEL-LIMITS.md](MODEL-LIMITS.md)). The acceptance stage `rules` checks that no rules result exceeds the page cap
  in a real game.
- **The envelope is labelled honestly**: on the text path a rules result is framed as a result of public rules
  text, never as historical data (`ContextMemory`), and the function's description says the same — the
  model is told it is reading universal reference text, not this seat's history or current game state.

Nothing is written to the archive: a lookup is stateless reference data, not an observation.

## Provenance and the with/without toggle

- Off by default. When off, no grammar line is added and `RULES` is not a tool request → the prompt
  bytes are **unchanged**, so a demonstration of the tool's value is a clean A/B: `run_batch.py --memory-tools`
  against `run_batch.py --memory-tools --rules-tool`.
- The game row stamps `rules_tool`, `rules_tool_version` (`RulesTool.VERSION`), and `rules_effective`
  (the CR date). The acceptance `faithful` stage also asserts that **no prompt advertises the rules
  grammar without `RULES_TOOL=1`** — the default-off promise is a gate, not a comment.
- A new CR text is a provenance event (like an engine or context-profile bump): update the pins in
  `eval/test_rules_corpus.py` and `data/rules/README.md` deliberately.

## How it is gated

- `RulesToolTest` (Java): parser + command semantics on an in-memory fixture; the real corpus's
  shape and spot rules when the file resolves (`-Dharness.rules.file=…`, which the acceptance sets).
- `eval/test_rules_corpus.py` (acceptance `pytests`): sha256, effective date, rule/glossary counts,
  spot rules, README provenance.
- Acceptance stage `rules`: a loopback game with `RULES_TOOL=1` where the fake model performs a real
  `RULES SEARCH` — proves the end-to-end path (transport → controller → `RulesTool` → audit row),
  the provenance stamp, the per-prompt grammar, and the page cap.

## Not done (deliberate)

- No web fetch during a game: the tool reads the downloaded file only. Determinism and no-egress are worth more than
  freshness, and `data/rules/README.md` documents how to move to a new text.
- The rules grammar cannot see game state. It answers "what does the rule say"; the observation
  answers "what is true now". Keeping them separate is the point.
