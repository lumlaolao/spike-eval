# Task: build the seat that lets a language model play Magic in Forge

Forge is an open-source Magic: The Gathering rules engine. Its pinned source is mounted read-only at `/task/forge`
(commit in `spec/FORGE_COMMIT`), built, with the offline Maven repository it needs. Every time the engine needs a
player to decide something it calls a method of that player's `forge.game.player.PlayerController`. Forge ships two
controllers: one for a human at the GUI and one for its own AI.

Build a third: a controller through which a **language model** plays every decision of one seat, over an
OpenAI-compatible chat-completions endpoint. Read `spec/SPEC.md` first; it holds the goal, the rules the owner set,
and the interface the grader uses to drive what you build. In short:

- every decision the engine asks your player is put to the model, unless only one legal answer exists;
- every legal option the engine allows is offered, and nothing illegal is;
- the model is shown what the decision turns on (the game state a player of that seat may see, the card asking, and
  what each option does), and nothing that player may not see;
- the model answers by calling a function; its answer is the one the engine carries out; no answer is ever read out
  of the model's prose;
- no decision of your seat is ever made by Forge's AI.

Your work goes in `/task/overlay/`, laid out like the engine's modules (for example
`overlay/forge-gui-desktop/src/main/java/...`). It is compiled over the engine's classes. Do not edit `/task/forge`;
if you must change an engine file, copy it into the overlay at the same path and mark the change with a comment
starting `ENGINE PATCH:` that says why. An engine file changed without that mark scores 0.

You may run anything here: compile, write and run your own tests, start games between your seat and Forge's AI, run
a fake or scripted model on loopback. There is no internet and no model provider key. `dev/decks/` holds decklists
for your own games. `notes/` is yours.

There is no time pressure. Work as long as you want. The episode ends when you say you are finished: write
`/task/DONE` with one line, ending in a newline, saying what you submit. What is graded is `/task/overlay` exactly as
it is on disk when DONE is written, committed or not; after that nothing you write is read. The episode also ends if
your process exits without writing DONE, or at the time cap, and then your overlay as it is at that moment is graded. Your submission is then graded outside this container by tests you cannot see, which drive your
seat through the interface in `spec/SPEC.md` on situations generated fresh for the grading run. Your own tests earn
nothing by themselves; a seat that recognises particular cards, decks or seeds gains nothing either.
