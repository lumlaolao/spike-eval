# The RULES tool — the model looks the rules up, it does not recall them

**Status:** built and validated (acceptance stage `rules`, 11 stages total). **Off by default**
(`RULES_TOOL=1` to enable), so a run without it is byte-identical to one from before it existed.

## Why

A real Legacy player knows the rules and can look them up; a model that *recalls* them confabulates
interaction rules (layers, replacement effects, state-based actions, the combat step order) under
exactly the pressure where it matters. The harness already hands each seat its own decklist **and its
cards' rules text from Forge's card DB** (`DECKLIST=1`), so the model never has to guess what a card
does. This is the other half of that principle: it never has to guess what a **rule** says either.

## What it is

`RulesTool` (overlay `forge.spike.RulesTool`) is a read-only index over the vendored official
Comprehensive Rules text (`data/rules/MagicCompRules.txt`, effective 2026-09-25; see
`data/rules/README.md` for provenance and how to update it). It parses the text **directly** — there
is no derived index to drift — into rules (id → text), section/chapter headings, a table of contents,
and the Glossary.

The tool is surfaced to the seat through the same tool loop as `MEMORY`/`INSPECT`
(`ContextMemory.ask`). It is **public, static reference text**: no engine state, no hidden
information, no network, no per-game mutation — which is precisely why it is safe to expose where the
other channels are seat-private.

## Grammar (one line; shares the MEMORY tool budget)

| Command | Returns |
|---|---|
| `RULES SEARCH <offset> <words>` | compact hits: rule id + one short line each |
| `RULES READ <rule> [offset]` | **one rule's full text** (+ the rule numbers it cites) |
| `RULES SECTION <number> [offset]` | a bounded **digest** of a whole section/chapter (id + one line per rule) |
| `RULES TERM <word>` | a Glossary definition |
| `RULES TOC [offset]` | chapter headings, to find a rule number |

## Context management (managed, never a dump)

The whole point is that this must not flood the prompt:

- **Every reply is bounded** at ~1200 characters (tighter than MEMORY's 2000) and self-describing:
  `... chars a..b/N next=<offset|END>`. The model pages with the `next=` offset.
- **Digest first, full text second.** `SECTION`/`SEARCH` return ids + one-liners; the model `READ`s
  only the specific rules in question. So the common case costs a few hundred characters, not a
  chapter.
- **Tool budget.** It rides the existing per-ask (2) and per-game (64) tool budget, so a rules-happy
  model is bounded by construction. The acceptance asserts **no single RULES result exceeds the page
  cap** in a real game (`rules_tool_rows=1/1 first_result=1310_chars`).
- **The envelope is labelled honestly**: a rules result is framed as `RULES RESULT (public rules
  text)`, never `MEMORY RESULT (historical data)` — the model is told it is reading universal
  reference text, not this seat's history or current game state.

Nothing is written to the archive: a lookup is stateless reference data, not an observation.

## Provenance and the with/without toggle

- Off by default. When off, no grammar line is added and `RULES` is not a tool request → the prompt
  bytes are **unchanged**, so a demonstration of the tool's value is a clean A/B:
  `MEMORY_TOOLS=1 RULES_TOOL=0` vs `MEMORY_TOOLS=1 RULES_TOOL=1`.
- The game row stamps `rules_tool`, `rules_tool_version` (`rules-tool-v1`), and `rules_effective`
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

- No web fetch: the tool reads the vendored file only. Determinism and no-egress are worth more than
  freshness, and `data/rules/README.md` documents the one-line update.
- The rules grammar cannot see game state. It answers "what does the rule say"; the observation
  answers "what is true now". Keeping them separate is the point.
