package forge.spike;

import java.util.ArrayList;
import java.util.List;

import org.testng.Assert;
import org.testng.annotations.Test;

import forge.game.GameLogEntryType;
import forge.spike.GameHistory.Ev;
import forge.spike.GameHistory.Rendered;
import forge.spike.LlmSpikeGui.ContextProfile;

/**
 * Drives the history renderer on synthetic logs — no Game, no JVM game loop.
 *
 * This is the code path that decides what the model is allowed to remember, and it shipped once
 * with no test at all. The property these tests exist to defend is not "the output looks nice", it
 * is NO SILENT LOSS: across a whole game, every logged event must reach the seat at least once, or
 * be visibly accounted for as compacted. A history renderer that quietly drops events is strictly
 * worse than the fixed ten-line tail it replaced, because the system message tells the model to
 * trust the snapshot over its own memory of earlier turns.
 */
public class GameHistoryTest {

    private static List<Ev> log(int n) {
        List<Ev> evs = new ArrayList<>();
        for (int i = 1; i <= n; i++) {
            // One TURN entry every 4 events, so turn attribution and spans are exercised.
            boolean isTurn = (i % 4 == 1);
            int turn = (i - 1) / 4 + 1;
            evs.add(new Ev(i, turn,
                    isTurn ? GameLogEntryType.TURN : GameLogEntryType.STACK_ADD,
                    isTurn ? ("Turn " + turn + " (Bob)") : ("event-" + i)));
        }
        return evs;
    }

    private static int countBullets(String s) {
        int n = 0, i = 0;
        while ((i = s.indexOf("\n  - ", i)) >= 0) { n++; i += 5; }
        if (s.startsWith("  - ")) n++;
        return n;
    }

    // ---- FIXED_TAIL: the historical behavior, pinned --------------------------------------------

    @Test
    public void fixedTailShowsExactlyTheCapAndNeverMore() {
        Rendered r = GameHistory.renderEvents(log(50), ContextProfile.V1_COMPAT, -1);
        Assert.assertTrue(r.text.startsWith("RECENT EVENTS (oldest->newest):\n"),
                "the v1 header is a byte-format pin: " + r.text);
        Assert.assertEquals(countBullets(r.text), 10, "v1-compat renders exactly 10 events");
        Assert.assertTrue(r.text.contains("event-50"), "the tail must end at the newest event");
        Assert.assertFalse(r.text.contains("event-2 "), "the tail must not reach back to the start");
    }

    @Test
    public void fixedTailIgnoresTheWatermarkEntirely() {
        // v1-compat must be watermark-independent, or an ai-teacher row (watermark -1) and a student
        // row (watermark 40) would render differently and stop hash-pairing.
        String a = GameHistory.renderEvents(log(50), ContextProfile.V1_COMPAT, -1).text;
        String b = GameHistory.renderEvents(log(50), ContextProfile.V1_COMPAT, 40).text;
        Assert.assertEquals(a, b);
    }

    @Test
    public void shorterLogThanCapRendersEverything() {
        Rendered r = GameHistory.renderEvents(log(3), ContextProfile.V1_COMPAT, -1);
        Assert.assertEquals(countBullets(r.text), 3);
    }

    // ---- the watermark contract -----------------------------------------------------------------

    @Test
    public void emptyLogReportsWatermarkZeroNotFailure() {
        Rendered r = GameHistory.renderEvents(new ArrayList<Ev>(), ContextProfile.V2_MEMORY, -1);
        Assert.assertEquals(r.text, "");
        Assert.assertEquals(r.watermark, 0, "an empty log is a legitimate state, not a failed render");
    }

    @Test
    public void aSuccessfulRenderReportsTheFullLogSize() {
        Rendered r = GameHistory.renderEvents(log(37), ContextProfile.V2_MEMORY, 30);
        Assert.assertEquals(r.watermark, 37,
                "the seat has now been shown, or told about, every event in the log");
    }

    /** THE CENTRAL INVARIANT. Simulate a game as a sequence of decisions, adopting the renderer's
     *  watermark each time exactly as snapshot() does, and assert every event was shown at least
     *  once. This is the test that would have caught the read-vs-advance bug: if the caller could
     *  advance the watermark past events the renderer never emitted, ids would go missing here. */
    @Test
    public void noEventIsEverSilentlySkippedAcrossASequenceOfDecisions() {
        ContextProfile ctx = ContextProfile.V2_MEMORY;
        boolean[] shown = new boolean[201];
        int watermark = -1;
        int lastSize = 0;
        for (int size = 5; size <= 120; size += 7) {          // the log grows between decisions
            Rendered r = GameHistory.renderEvents(log(size), ctx, watermark);
            for (int id = 1; id <= size; id++) {
                if (r.text.contains("event-" + id + "\n") || r.text.contains("event-" + id + " ")) {
                    shown[id] = true;
                }
            }
            if (r.watermark != Rendered.KEEP_WATERMARK) watermark = r.watermark;
            lastSize = size;
        }
        // Only events that were ever LOGGED can have been shown: the loop's final log has lastSize
        // entries, and asserting past that would fail on events that never happened.
        List<Integer> missed = new ArrayList<>();
        for (int id = 1; id <= lastSize; id++) {
            if (id % 4 == 1) continue;                        // TURN entries render as "Turn N (Bob)"
            if (!shown[id]) missed.add(id);
        }
        Assert.assertTrue(missed.isEmpty(), "events never shown to the seat: " + missed);
        Assert.assertTrue(lastSize >= 100, "the sequence must be long enough to exercise the cap");
    }

    // ---- SINCE_LAST_DECISION: gap, cap, and the truncation announcement -------------------------

    @Test
    public void sinceLastDecisionShowsExactlyTheGap() {
        Rendered r = GameHistory.renderEvents(log(40), ContextProfile.V2_MEMORY, 34);
        Assert.assertEquals(countBullets(r.text), 6, "40 logged - 34 seen = 6 new");
        Assert.assertTrue(r.text.contains("event-40"));
        Assert.assertFalse(r.text.contains("event-34\n"), "already-seen events must not repeat");
    }

    @Test
    public void aMinusOneWatermarkMeansTheWholeGameSoFar() {
        Rendered r = GameHistory.renderEvents(log(12), ContextProfile.V2_MEMORY, -1);
        Assert.assertEquals(countBullets(r.text), 12, "no prior decision => show everything");
    }

    @Test
    public void oversizedGapIsCappedAndTheTruncationIsAnnounced() {
        ContextProfile small = ContextProfile.forTest(
                "t", GameHistory.Mode.SINCE_LAST_DECISION, ContextProfile.EVENTS_SINCE_LAST_DECISION,
                5, 0, 0, 0);
        Rendered r = GameHistory.renderEvents(log(40), small, 0);
        Assert.assertTrue(r.text.contains("omitted"),
                "a truncated window MUST say so — silent truncation is the bug being fixed: " + r.text);
        Assert.assertEquals(countBullets(r.text), 6, "5 events + the omission notice line");
        Assert.assertTrue(r.text.contains("event-40"), "truncation drops the OLDEST, keeps the newest");
    }

    @Test
    public void anExactlyFullWindowIsNotAnnouncedAsTruncated() {
        ContextProfile small = ContextProfile.forTest(
                "t", GameHistory.Mode.SINCE_LAST_DECISION, ContextProfile.EVENTS_SINCE_LAST_DECISION,
                5, 0, 0, 0);
        Rendered r = GameHistory.renderEvents(log(40), small, 35);
        Assert.assertFalse(r.text.contains("omitted"),
                "gap == cap is complete, not truncated: " + r.text);
    }

    @Test
    public void noNewEventsRendersNothing() {
        Rendered r = GameHistory.renderEvents(log(20), ContextProfile.V2_MEMORY, 20);
        Assert.assertEquals(r.text, "", "nothing has happened since the last decision");
        Assert.assertEquals(r.watermark, 20);
    }

    // ---- TIERED: the ladder --------------------------------------------------------------------

    /** The faithful profile names the source of its history blocks: Forge's game log, so a line naming the seat's
     *  player reads as the engine's record, not as anyone's message. Older profiles keep their bytes. */
    @Test
    public void theFaithfulHistorySaysItIsTheForgeGameLog() {
        Rendered f = GameHistory.renderEvents(log(400), ContextProfile.V4_FAITHFUL, 390);
        Assert.assertTrue(f.text.contains("GAME HISTORY (Forge game log, compacted;"), f.text);
        Assert.assertTrue(f.text.contains("SINCE YOUR LAST DECISION (Forge game log, oldest->newest"), f.text);
        Rendered v3 = GameHistory.renderEvents(log(400), ContextProfile.V3_COMPACT, 390);
        Assert.assertFalse(v3.text.contains("Forge game log"), v3.text);
        Assert.assertTrue(SeatObservation.GUIDE.contains("Where each part comes from"), SeatObservation.GUIDE);
    }

    @Test
    public void tieredKeepsTheWholeGameInBoundedSpace() {
        Rendered r = GameHistory.renderEvents(log(400), ContextProfile.V3_COMPACT, 390);
        Assert.assertTrue(r.text.contains("GAME HISTORY (compacted"), r.text);
        Assert.assertTrue(r.text.contains("SINCE YOUR LAST DECISION"), r.text);
        Assert.assertTrue(r.text.contains("[E1-"),
                "the ladder must still reach event 1 of a 400-event game: " + r.text);
        Assert.assertTrue(r.text.length() < 6000,
                "400 events must not render as 400 lines; got " + r.text.length() + " chars");
    }

    @Test
    public void tieredAnnouncesTheIdRangeEveryDigestCovers() {
        // Compaction has to be visible: a model that cannot see WHICH events were summarized reads
        // the gap in the id sequence as events that never happened.
        Rendered r = GameHistory.renderEvents(log(200), ContextProfile.V3_COMPACT, 195);
        int firstDigest = r.text.indexOf("[E");
        Assert.assertTrue(firstDigest > 0, "digest lines must carry canonical id ranges: " + r.text);
        Assert.assertTrue(r.text.contains("-E"), "id ranges are spans, not points: " + r.text);
    }

    @Test
    public void tieredVerbatimEventsCarryTheirCanonicalIds() {
        Rendered r = GameHistory.renderEvents(log(100), ContextProfile.V3_COMPACT, 96);
        Assert.assertTrue(r.text.contains("[E100] event-100"),
                "verbatim lines must be individually addressable: " + r.text);
    }

    @Test
    public void tieredWithNothingToCompactStillRendersTheRecentBlock() {
        Rendered r = GameHistory.renderEvents(log(4), ContextProfile.V3_COMPACT, -1);
        Assert.assertTrue(r.text.contains("SINCE YOUR LAST DECISION"), r.text);
        Assert.assertEquals(r.watermark, 4);
    }
}
