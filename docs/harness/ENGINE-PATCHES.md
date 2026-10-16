# Engine patches

The Forge checkout is never edited by hand (`CLAUDE.md`, "The one rule"). Where a decision the seat should
own has no `PlayerController` hook, or an engine bug distorts play, the harness carries a patched copy of
the engine file in `forge-overlay/`, at the file's own path. Owner approval: 2026-10-15.

How a patch reaches the game:
- `install.sh` copies `forge-overlay/` over the checkout, so the Maven build compiles the patched file;
- the isolated acceptance build (`eval/harness_acceptance.sh`, stage `compile`) compiles every overlay file
  under `*/src/main/java/` and puts those classes before the checkout's, so the patched class wins there too.

The rules:
- only where the seat has no hook, or the engine is wrong; never to make a run pass;
- the whole upstream file at `FORGE_COMMIT` is copied, and every change is marked `HARNESS PATCH`;
- the table below records the sha256 of the upstream original; `eval/test_engine_patches.py` fails when the
  pinned checkout's original differs (a re-pin forces a re-diff) or a changed hunk carries no marker;
- each patch has a golden check, and the change goes through acceptance and the canaries like any other.

| Overlay file | Upstream sha256 at FORGE_COMMIT | What it changes | Golden check |
|---|---|---|---|
| `forge-overlay/forge-ai/src/main/java/forge/ai/ComputerUtilMana.java` | `dd768c381ef7caf53852f2b3c21d3c0958d73f41188393a47443fde6fd0fbb36` | For a generic shard paid by an any-colour source (Lotus Petal, LED, Birds of Paradise), a model seat's colour is left open, so `ManaEffect` asks its controller; Forge's AI payment used to fix the first of WUBRG. `HARNESS_SEAT_CHOOSES_ANY_COLOR` is false unless `PlayerControllerLLM` sets it, for model seats only, so the opponent AI and the zero-model arms are unchanged. | `GoldenChecksTest.theColourAnAnyColourSourceMakesForGenericIsTheSeats` |

On an engine re-pin (`docs/harness/ENGINE-PIN.md`): for each row, diff the new upstream file against the old
one, port the change into the patched copy, and record the new sha256.
