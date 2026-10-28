# Lessons the code is built on

Each of these cost a day or more to learn and is now enforced by a test, a canary or an owner rule (`CLAUDE.md`,
`.claude/rules/`). Where one is cited elsewhere, this is the page it means.

1. **Pass priority with `null`, never an empty list.** An empty list means "play nothing, then ask again", and the
   engine's priority loop spins 999 times.
2. **Constrain the model to the engine's legal list,** and offer only what can be paid: a count, mode or target the
   cast cannot pay fails the whole cast.
3. **Answers are tool calls, never text.** A free-form reply that reasoned about a creature it decided not to use was
   read as an attack with it; text grammars refused 30 of 43 tool requests for their form. Zero text parsing.
4. **The AI cast path does no targeting.** Set engine-validated targets on the chosen ability before returning it, or
   the cast fizzles.
5. **Never fall back to Forge's AI.** A failure takes a fixed legal option, recorded; Forge's AI quietly made
   load-bearing calls before.
6. **No model ask while pricing.** Building a prompt prices the hand; a hook asked from there builds another prompt,
   forever. The guard is saved and restored, never cleared.
7. **Every number reports its floor.** A replay match of 88% was 10 points over doing nothing.
