package forge.spike;

import java.util.ArrayList;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import forge.game.Game;
import forge.game.GameLogEntry;
import forge.game.GameLogEntryType;

import forge.spike.LlmSpikeGui.ContextProfile;

/**
 * Renders the game's past into the model's prompt.
 *
 * Extracted from {@code PlayerControllerLLM.buildSnapshot}, where it was a nine-line block with a
 * hardcoded {@code Math.min(10, ...)}. It earns its own file because it is the harness's only
 * mechanism for the thing a Magic player does constantly and a stateless snapshot cannot do at all:
 * remember. A Legacy game here runs past 100 turns, and a fixed tail of ten log lines silently
 * discards most of it.
 *
 * THREE MODES, INCREASING IN AMBITION. Each is selected by a {@link ContextProfile}, so which one a
 * run used is recorded in that run's provenance and two runs on different modes are never pooled:
 *
 *   FIXED_TAIL           the last N events, verbatim. The pre-2026-09 behavior. Cheap, and wrong
 *                        past the first few turns.
 *   SINCE_LAST_DECISION  every event this seat has not been shown, verbatim, capped. Complete over
 *                        the recent past, but the cap is a cliff: cross it and the oldest events
 *                        vanish exactly as before, just later.
 *   TIERED               a resolution ladder. Recent events verbatim; earlier turns digested to one
 *                        line each; older turns digested harder, keeping only what changes a game.
 *                        Prompt growth slows while the horizon keeps extending; this is not a hard size bound.
 *
 * WHY TIERED IS NOT JUST "A BIGGER WINDOW". Every window scheme trades horizon against prompt size
 * at a fixed exchange rate. A ladder changes the rate: the recent past is worth full fidelity, turn
 * 4 of a 40-turn game is worth one line, and the difference between "Bob cast Brainstorm" and the
 * three STACK_RESOLVE entries around it decays much faster than the fact that Bob cast Brainstorm.
 *
 * COMPACTION IS ANNOUNCED, NEVER SILENT. Every event carries a canonical id {@code E<n>} — its
 * 1-based index in the filtered narration stream, stable for the whole game — and every digested block prints the id
 * RANGE it covers. A model reading {@code T5-T8 [E40-E91]: ...} can see that fifty-two events exist
 * there and that it is being shown a summary of them. This is the same discipline the snapshot
 * already applies with {@code @N} object ids: the model gets a durable handle on things it can only
 * partially see. A summary that hides its own lossiness is worse than a shorter honest one, because
 * the system message instructs the model to trust the snapshot over its own memory.
 */
final class GameHistory {

    private GameHistory() { }

    /** How much of the past a profile renders, and at what resolution. */
    enum Mode {
        /** Last {@code recentEvents} entries, verbatim. */
        FIXED_TAIL,
        /** Everything since this seat's previous decision, verbatim, capped at {@code recentEventsMax}. */
        SINCE_LAST_DECISION,
        /** Verbatim recent + per-turn digests + hard-compacted older turns. */
        TIERED
    }

    /** Game-log types worth narrating at all. Moved here with the renderer; PHASE/MANA/INFORMATION
     *  are pure noise at every resolution. */
    static final Set<GameLogEntryType> EVENT_TYPES = EnumSet.of(
            GameLogEntryType.TURN, GameLogEntryType.MULLIGAN, GameLogEntryType.ZONE_CHANGE,
            GameLogEntryType.DAMAGE, GameLogEntryType.LIFE, GameLogEntryType.LAND,
            GameLogEntryType.DISCARD, GameLogEntryType.COMBAT, GameLogEntryType.STACK_ADD,
            GameLogEntryType.STACK_RESOLVE, GameLogEntryType.GAME_OUTCOME);

    /** The types that survive the HARDEST compaction tier: what a player would still recall about a
     *  turn ten turns later. Casts and land drops define what the opponent's deck is doing; damage,
     *  life and discards define the race. STACK_RESOLVE, ZONE_CHANGE and COMBAT are dropped here —
     *  they are the play-by-play texture of a turn, and the cast that caused them is already kept,
     *  so dropping them loses detail without losing the event. */
    private static final Set<GameLogEntryType> HIGH_SIGNAL = EnumSet.of(
            GameLogEntryType.STACK_ADD, GameLogEntryType.LAND, GameLogEntryType.DAMAGE,
            GameLogEntryType.LIFE, GameLogEntryType.DISCARD, GameLogEntryType.MULLIGAN,
            GameLogEntryType.GAME_OUTCOME);

    /**
     * A render's output AND the watermark the caller may now adopt.
     *
     * THE POINT OF THIS TYPE is that a caller must never compute the new watermark itself. The
     * first version of this code did: {@code snapshot()} rendered, then independently recounted the
     * log and advanced the watermark to that total. If rendering threw (a malformed log entry is
     * enough), the events section was omitted while the watermark still advanced past them — the
     * seat was then permanently blind to events it had never been shown, with no notice in the
     * prompt and no trace in provenance. A caught exception turning into silent data loss is worse
     * than the truncation bug this whole file exists to fix, so the coupling is now structural: the
     * only source of a new watermark is the renderer that actually rendered.
     *
     * {@code watermark == KEEP_WATERMARK} means "I rendered nothing you can rely on — do not
     * advance", which is the safe direction: re-showing events costs prompt space, dropping them
     * costs the game.
     */
    static final class Rendered {
        /** Sentinel: the caller must leave its watermark untouched. */
        static final int KEEP_WATERMARK = -1;
        final String text;
        final int watermark;
        Rendered(String text, int watermark) { this.text = text; this.watermark = watermark; }
    }

    /** One logged event, paired with its canonical id and owning turn. Package-private so tests can
     *  drive the rendering arithmetic on synthetic logs, with no Game and no JVM game loop. */
    static final class Ev {
        final int id;               // 1-based index in the oldest-first log; stable for the game
        final int turn;             // turn number this event belongs to (0 = pre-first-turn)
        final GameLogEntryType type;
        final String msg;
        Ev(int id, int turn, GameLogEntryType type, String msg) {
            this.id = id; this.turn = turn; this.type = type; this.msg = msg;
        }
    }

    /**
     * Render the history block, INCLUDING its trailing newline, or "" when there is nothing to say.
     *
     * @param watermark the count of events this seat had already been shown; -1 for "no previous
     *                  decision", which means the whole game so far. Ignored by FIXED_TAIL.
     */
    static Rendered render(Game g, ContextProfile ctx, int watermark) {
        List<Ev> evs;
        try {
            evs = collect(g);
        } catch (Exception e) {
            // History is an enhancement, never a hard dependency — but a failed render must NOT be
            // mistaken for "the seat has seen everything". Keep the watermark.
            return new Rendered("", Rendered.KEEP_WATERMARK);
        }
        return renderEvents(evs, ctx, watermark);
    }

    /** The pure half: everything from here down is a function of (events, profile, watermark), so
     *  the truncation and gap arithmetic is testable on synthetic logs without a Game. */
    static Rendered renderEvents(List<Ev> evs, ContextProfile ctx, int watermark) {
        if (evs.isEmpty()) {
            // Nothing logged yet is a legitimate state (pre-first-turn), not a failure: a watermark
            // of 0 is exactly right and lets the next call render the whole opening.
            return new Rendered("", 0);
        }
        String text;
        try {
            switch (ctx.historyMode) {
                case SINCE_LAST_DECISION: text = renderSinceLastDecision(evs, ctx, watermark); break;
                case TIERED:              text = renderTiered(evs, ctx, watermark); break;
                case FIXED_TAIL:
                default:                  text = renderFixedTail(evs, ctx); break;
            }
        } catch (Exception e) {
            return new Rendered("", Rendered.KEEP_WATERMARK);
        }
        // The seat has now been shown (or been told about) every event in the log. FIXED_TAIL does
        // not use the watermark to render, but reporting it keeps one rule for every mode: the
        // renderer is the only thing that may move it.
        return new Rendered(text, evs.size());
    }

    /** Oldest-first events with canonical ids and turn attribution. The engine hands back
     *  newest-first, and TURN entries are the turn boundaries. */
    static List<Ev> collect(Game g) {
        List<GameLogEntry> raw = g.getGameLog().getLogEntriesForTypes(EVENT_TYPES); // newest-first
        List<Ev> out = new ArrayList<>(raw.size());
        int turn = 0;
        for (int i = raw.size() - 1; i >= 0; i--) {                                 // -> oldest-first
            GameLogEntry e = raw.get(i);
            String msg = LlmSpikeGui.oneLine(e.message());
            if (e.type() == GameLogEntryType.TURN) {
                int parsed = parseTurn(msg);
                // A TURN line whose number will not parse must not silently reset attribution to 0;
                // advancing by one keeps later blocks ordered and labelled plausibly.
                turn = parsed > 0 ? parsed : turn + 1;
            }
            out.add(new Ev(out.size() + 1, turn, e.type(), msg));
        }
        return out;
    }

    /** "Turn 7 (Bob)" -> 7; 0 when no leading turn number is present. */
    static int parseTurn(String msg) {
        if (msg == null) return 0;
        int i = 0, n = msg.length();
        while (i < n && !Character.isDigit(msg.charAt(i))) i++;
        int v = 0, digits = 0;
        while (i < n && Character.isDigit(msg.charAt(i)) && digits < 6) {
            v = v * 10 + (msg.charAt(i++) - '0');
            digits++;
        }
        return digits == 0 ? 0 : v;
    }

    // ---- mode 1: the historical fixed tail -------------------------------------------------------

    /** BYTE-IDENTICAL to the pre-extraction block. Any change here silently invalidates every
     *  artifact recorded under v1-compat; add a mode instead. */
    private static String renderFixedTail(List<Ev> evs, ContextProfile ctx) {
        int n = Math.min(ctx.recentEvents, evs.size());
        if (n <= 0) return "";
        StringBuilder s = new StringBuilder("RECENT EVENTS (oldest->newest):\n");
        for (int i = evs.size() - n; i < evs.size(); i++) {
            s.append("  - ").append(evs.get(i).msg).append('\n');
        }
        return s.toString();
    }

    // ---- mode 2: everything since this seat last acted -------------------------------------------

    private static String renderSinceLastDecision(List<Ev> evs, ContextProfile ctx, int watermark) {
        int seen = Math.max(0, watermark);
        int gap = Math.max(0, evs.size() - seen);
        int n = Math.min(gap, ctx.recentEventsMax);
        if (n <= 0) return "";
        StringBuilder s = new StringBuilder("RECENT EVENTS (oldest->newest):\n");
        if (gap > n) s.append("  - (...older events this turn omitted...)\n");
        for (int i = evs.size() - n; i < evs.size(); i++) {
            s.append("  - ").append(evs.get(i).msg).append('\n');
        }
        return s.toString();
    }

    // ---- mode 3: the resolution ladder ------------------------------------------------------------

    private static String renderTiered(List<Ev> evs, ContextProfile ctx, int watermark) {
        int seen = Math.max(0, watermark);
        int gap = Math.max(0, evs.size() - seen);
        int verbatim = Math.min(gap, ctx.recentEventsMax);
        int splitAt = evs.size() - verbatim;              // [0, splitAt) is digested, the rest verbatim

        StringBuilder s = new StringBuilder();
        if (splitAt > 0) {
            int currentTurn = evs.get(evs.size() - 1).turn;
            // Turns at or above this boundary get a full digest; everything older gets the
            // high-signal-only digest. digestTurns == 0 sends the entire past to the hard tier.
            int fullFrom = currentTurn - ctx.digestTurns + 1;
            List<int[]> spans = turnSpans(evs, splitAt);   // {turn, firstIdx, lastIdx} oldest-first

            // The hard tier collapses CONSECUTIVE old turns into one line: forty turns of one line
            // each is not a summary, it is the log again.
            List<String> lines = new ArrayList<>();
            int i = 0;
            while (i < spans.size()) {
                int[] sp = spans.get(i);
                if (sp[0] >= fullFrom) {
                    lines.add(digestLine(evs, sp[1], sp[2], "T" + sp[0], ctx.digestPerTurn, false));
                    i++;
                } else {
                    int j = i;
                    while (j + 1 < spans.size() && spans.get(j + 1)[0] < fullFrom
                            && (j + 1 - i) < ctx.compactTurnsPerLine) {
                        j++;
                    }
                    int[] last = spans.get(j);
                    String label = sp[0] == last[0] ? "T" + sp[0] : "T" + sp[0] + "-T" + last[0];
                    lines.add(digestLine(evs, sp[1], last[2], label, ctx.digestPerTurn, true));
                    i = j + 1;
                }
            }
            if (!lines.isEmpty()) {
                // The faithful profile says where the lines come from: the engine's log, not anyone's words to the seat.
                s.append(ctx.faithfulObservation ? "GAME HISTORY (Forge game log, compacted; E<n> are canonical event ids):\n"
                                                 : "GAME HISTORY (compacted; E<n> are canonical event ids):\n");
                for (String ln : lines) if (!ln.isEmpty()) s.append("  ").append(ln).append('\n');
            }
        }
        if (verbatim > 0) {
            String src = ctx.faithfulObservation ? "Forge game log, " : "";
            s.append(gap > verbatim
                    ? "SINCE YOUR LAST DECISION (" + src + "oldest->newest; older ones are in the compacted block above):\n"
                    : "SINCE YOUR LAST DECISION (" + src + "oldest->newest):\n");
            for (int i = splitAt; i < evs.size(); i++) {
                s.append("  - [E").append(evs.get(i).id).append("] ").append(evs.get(i).msg).append('\n');
            }
        }
        return s.toString();
    }

    // ---- the cache order: finished turns once, the rest as TIERED renders it ---------------------------------

    /** {@link #renderSplit}'s output: the digest line of each turn that is final now and was not before (oldest
     *  first, for the caller's append-only record), the last turn so recorded, and the rest of the history. */
    static final class Split {
        final List<String> finished;
        final int finishedThrough;
        final String rest;
        final int watermark;
        Split(List<String> finished, int finishedThrough, String rest, int watermark) {
            this.finished = finished; this.finishedThrough = finishedThrough; this.rest = rest; this.watermark = watermark;
        }
    }

    /**
     * The TIERED history in two parts, for a prompt whose opening must not change between decisions (the provider's
     * prompt cache matches a prefix). A turn's digest line is final once the turn has ended and the seat was shown
     * every event of it before: it goes to {@code finished} the first time that holds, and never again. Everything
     * else (the open part of a turn, then the events since the last decision, verbatim) is {@code rest}, worded as
     * TIERED words it. Together they show every event once, as TIERED does.
     *
     * @param finishedThrough the last turn the caller has recorded, -1 for none
     */
    static Split renderSplit(List<Ev> evs, ContextProfile ctx, int watermark, int finishedThrough) {
        if (evs.isEmpty()) return new Split(new ArrayList<>(), finishedThrough, "", 0);
        try {
            int seen = Math.max(0, watermark);
            int gap = Math.max(0, evs.size() - seen);
            int verbatim = Math.min(gap, ctx.recentEventsMax);
            int splitAt = evs.size() - verbatim;
            List<String> finished = new ArrayList<>();
            List<String> open = new ArrayList<>();
            int through = finishedThrough;
            for (int[] sp : turnSpans(evs, splitAt)) {
                if (sp[0] <= finishedThrough) continue;
                String line = digestLine(evs, sp[1], sp[2], "T" + sp[0], ctx.digestPerTurn, false);
                // Ended (a later turn's event follows its last one), so all of it lies before the verbatim part.
                boolean done = open.isEmpty() && sp[2] + 1 < evs.size() && evs.get(sp[2] + 1).turn > sp[0];
                if (done) { finished.add(line); through = sp[0]; }
                else open.add(line);
            }
            StringBuilder s = new StringBuilder();
            if (!open.isEmpty()) {
                s.append("GAME HISTORY, CONTINUED (Forge game log, compacted; the part of a turn that had not ended when "
                        + "you last decided):\n");
                for (String ln : open) s.append("  ").append(ln).append('\n');
            }
            if (verbatim > 0) {
                s.append(gap > verbatim
                        ? "SINCE YOUR LAST DECISION (Forge game log, oldest->newest; older ones are in the history above):\n"
                        : "SINCE YOUR LAST DECISION (Forge game log, oldest->newest):\n");
                for (int i = splitAt; i < evs.size(); i++) {
                    s.append("  - [E").append(evs.get(i).id).append("] ").append(evs.get(i).msg).append('\n');
                }
            }
            return new Split(finished, through, s.toString(), evs.size());
        } catch (Exception e) {
            return new Split(new ArrayList<>(), finishedThrough, "", Rendered.KEEP_WATERMARK);
        }
    }

    /** {turn, firstIdx, lastIdx} for each turn appearing in [0, end), oldest-first. */
    private static List<int[]> turnSpans(List<Ev> evs, int end) {
        List<int[]> spans = new ArrayList<>();
        for (int i = 0; i < end; i++) {
            int t = evs.get(i).turn;
            if (!spans.isEmpty() && spans.get(spans.size() - 1)[0] == t) {
                spans.get(spans.size() - 1)[2] = i;
            } else {
                spans.add(new int[] {t, i, i});
            }
        }
        return spans;
    }

    /**
     * One digest line for events [from, to], labelled and carrying its canonical id range.
     *
     * Repeated messages collapse to "msg xN" — a turn that logs the same trigger six times is one
     * fact, not six — and TURN entries themselves are dropped, since the label already says which
     * turn this is.
     */
    private static String digestLine(List<Ev> evs, int from, int to, String label,
                                     int cap, boolean highSignalOnly) {
        LinkedHashMap<String, Integer> counts = new LinkedHashMap<>();
        for (int i = from; i <= to; i++) {
            Ev e = evs.get(i);
            if (e.type == GameLogEntryType.TURN) continue;
            if (highSignalOnly && !HIGH_SIGNAL.contains(e.type)) continue;
            if (e.msg == null || e.msg.isEmpty()) continue;
            counts.merge(e.msg, 1, Integer::sum);
        }
        String ids = "[E" + evs.get(from).id + "-E" + evs.get(to).id + "]";
        if (counts.isEmpty()) {
            // The block is not empty — it was compacted to nothing. Say so, so the model does not
            // read the gap in the id sequence as events that never happened.
            return label + " " + ids + ": (no high-signal events)";
        }
        StringBuilder b = new StringBuilder(label).append(' ').append(ids).append(": ");
        int shown = 0;
        for (Map.Entry<String, Integer> e : counts.entrySet()) {
            if (shown == cap) {
                b.append("; ...(").append(counts.size() - cap).append(" more)");
                break;
            }
            if (shown++ > 0) b.append("; ");
            b.append(e.getKey());
            if (e.getValue() > 1) b.append(" x").append(e.getValue());
        }
        return b.toString();
    }
}
