# Comprehensive Rules text (the RULES tool's source)

`MagicCompRules.txt` is the **exact official Wizards text** of the Magic: The Gathering
Comprehensive Rules, vendored here so the harness can look rules up **offline** with no network and
no per-query cost. It is read by `RulesTool` (overlay `forge.spike.RulesTool`), which serves the
`RULES` tool to a seat when `RULES_TOOL=1`.

| | |
|---|---|
| Source | `https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt` |
| Effective | September 25, 2026 |
| sha256 | `8d860e451f20f38865b725b42d82feb714c725373dd8f3b32b8652b3eeb070ca` |
| Size / lines | 977,752 bytes / 9,372 lines |
| Rules parsed | 3,165 (rule lines) |
| Glossary parsed | 741 terms |

This is **public, static reference data** — no card text, no game state, no hidden information. The
same file underlies everyone's copy of the rules, so exposing it through the tool loop is safe (the
MEMORY loop's other channels are seat-private; this one is not a leak).

## Nothing is derived

`RulesTool` parses this file **directly** at load. There is no generated index to drift out of sync,
so the file above is the single source of truth. Its contents are guarded by
`eval/test_rules_corpus.py` (acceptance `pytests` stage), which re-parses it and checks the sha256,
the effective date, the rule/glossary counts, and spot rules.

## Updating to a new CR

1. Download the new official `.txt` from `magic.wizards.com/en/rules` (the `media.wizards.com`
   `MagicCompRules <YYYYMMDD>.txt` file).
2. Replace `MagicCompRules.txt`.
3. Update the sha256/size/counts in this README **and** the pinned constants in
   `eval/test_rules_corpus.py` — deliberately, like re-pinning an acceptance canary.
4. Re-run `bash eval/harness_acceptance.sh`.

A changed sha is a **provenance event**: runs made with the old text are not comparable to runs made
with the new one, exactly like an engine or context-profile bump. The game row stamps
`rules_tool`, `rules_tool_version`, and `rules_effective` so an artifact says which text it used.
