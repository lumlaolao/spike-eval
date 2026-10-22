# Spec: the goal, the owner's rules, the interface the grader drives

Status: draft for the pilot (`harness-env-abi/1`). Sections 1 and 2 are what a good seat is; section 3 is how the
grader reaches yours. The grader's tests are not described here beyond what you must implement.

## 1. The goal

A benchmark of how well language models play Magic needs a seat in which **the model is the only bottleneck**: the
harness never decides for the model, never asks it blind, never ignores its answer, and never limits it. You are
building that seat.

## 2. The owner's rules

1. **Every decision is the model's.** Each `PlayerController` call on your player that chooses something (a play,
   a target, a cost, an order, a number, a colour, a mode, an attack, a block, a mulligan, a yes or no) is put to the
   model. The only exceptions: exactly one legal answer exists; or the model itself asked to pass until a named
   point and nothing it could not have foreseen happened since. Either exception is logged with its reason.
2. **Never Forge's AI.** No decision of your seat is delegated to Forge's AI controller or its helper classes
   (`forge.ai` decision code), directly or through an inherited method that decides. Reading engine state is fine.
3. **Every legal option, nothing else.** The options offered are exactly the ones the engine would allow a human
   player in that spot, including every alternative and optional cost, every legal target set, and the ways to
   decline. A legal action is never hidden.
4. **Shown, whole and fair.** Each request carries the state the seat's player may see (both sides' battlefields,
   graveyards, exile, the stack, life, hand sizes, its own hand and library knowledge), the card asking with its
   rules text, and for each option what it does and at what cost, whose each card is, and the play a decision
   follows. Nothing is cut short. No engine-internal ids, no `CARDNAME` placeholders, no filter syntax. Hidden
   information (the opponent's hand, library order) is never shown or hinted.
5. **Answered by a function call.** The model answers through the `answer` function (section 3.2). Text is never
   parsed as an answer. A reply that calls nothing may be reminded; if it still answers nothing, the seat takes a
   fixed, logged, do-nothing option where one exists (pass, no attack, no block, decline) and the first legal
   option only where a choice is mandatory, never a land drop, an attack or Forge's AI.
6. **The answer is the one carried out.** What the engine does after the call is exactly the option the call named.
7. **Logged.** Every decision writes one JSON line: what was asked, the options, the answer, who answered (`model`,
   or `auto` with its reason).

## 3. The interface (`harness-env-abi/1`)

### 3.1 The factory

The grader loads your seat through this interface, which it puts on the classpath (do not ship your own copy):

```java
package forge.harnessenv;

public interface SeatFactory {
    /** The controller for {@code player}; every model request it makes goes to {@code model}. */
    forge.game.player.PlayerController create(forge.game.Game game, forge.game.player.Player player,
            forge.LobbyPlayer lobbyPlayer, ModelEndpoint model);
}

public final class ModelEndpoint {
    public final String baseUrl;   // POST {baseUrl}/chat/completions, OpenAI-compatible, no key
    public final String model;     // the model name to send
    public final java.nio.file.Path logDir;  // write your decision log here
}
```

Name your implementation in `overlay/harness-env.properties`: `seat.factory=<fully.qualified.ClassName>`. It must
have a public no-argument constructor. The grader builds games itself (any phase, any board, any card) and calls
`create` for the seat it tests; the other seat is Forge's AI or a scripted player.

### 3.2 The wire

Each decision is one or more `POST /chat/completions` requests (the `messages`/`tools`/`tool_choice` form). Every
request that asks for a decision offers a function named `answer` whose arguments include the field that decision
needs:

| Decision | Field | Value |
|---|---|---|
| one option from a numbered list | `option` | its number, 1-based, in the order your request lists them |
| several (or none) from a numbered list | `options` | the numbers, in order |
| blocks | `blocks` | a list of `{"blocker": i, "attacker": j}`, numbers from your two lists |
| divide an amount | `amounts` | one integer per listed recipient |
| a number | `number` | an integer |
| keep or mulligan | `keep` | `true` or `false` |
| name a card | `card` | its English name |

You may add fields, other functions (lookups, notes) and more rounds. An out-of-range call should be answered with
the error in the same conversation. The grader's scripted model may fill several of these fields in one call (for
example `option` and `options` together); your seat reads the field its decision needs and ignores the rest. It also
answers with prose only, with an empty reply, or with garbage, and none of those may choose anything.

### 3.3 Limits

A decision gets 10 seconds of wall time beyond the model's own latency. Your seat may not open network connections
other than `baseUrl`, start processes, or read files outside the overlay and `logDir`.
