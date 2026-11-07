package forge.spike;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import forge.GuiDesktop;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.FileSection;
import forge.util.FileUtil;
import forge.util.MyRandom;

/**
 * Step regret, step 2: a recorded attempt replayed for its first K decision rows (ReplayBridge), then the same seat
 * handed to the search arm, which proves the position after K winnable, proves it lost (every line played, none
 * capped), or finds nothing. The recordings are real games on the splice board: its written line (solved), and the
 * same line with the Lightning Bolt aimed at the solver (failed, the opponent left at 3 life).
 */
public class StepRegretTest {
    private static final File REPO = PuzzleSetupTest.repoRoot();
    private static final File SPLICE = new File(REPO, "eval/puzzles/hard/gen/splice_06dad20af5b3.pzl");
    private static final String ID = "splice_06dad20af5b3";
    private static final long SEED = 1L;
    private static final int BUDGET = 200;

    @BeforeClass
    public void initializeModel() {
        if (GuiBase.getInterface() == null) {
            GuiBase.setInterface(new GuiDesktop());
            FModel.initialize(null, preferences -> {
                preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
                preferences.setPref(FPref.UI_LANGUAGE, "en-US");
                return null;
            });
        }
    }

    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void clearStatics() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        LlmSpikeGui.EvalLog.closeForTest();
        LlmSpikeGui.puzzleSolverSeat = null;
        LlmSpikeGui.puzzleBrief = null;
        LlmSpikeGui.SEED_USED = 0L;
    }

    private static Map<String, List<String>> board() { return FileSection.parseSections(FileUtil.readFile(SPLICE)); }

    /** A game of the written {@code script} played by the model seat with the game log on: the recording. */
    private static File record(String script, String expectStatus) throws Exception {
        File dir = Files.createTempDirectory("step-regret-rec").toFile();
        dir.deleteOnExit();
        LlmSpikeGui.EvalLog.closeForTest();
        LlmSpikeGui.EvalLog.initToDir(dir.getPath(), SEED);
        MyRandom.setRandom(new java.util.Random(SEED));
        LlmSpikeGui.SEED_USED = SEED;
        String spec = ScriptBridge.PREFIX + script;
        File log;
        try {
            LlmSpikePuzzle.Played p = LlmSpikePuzzle.playOnce(SPLICE, ID, board(), "llm", "ai", spec, spec, SEED, 120_000L, 0,
                    true, true);
            Assert.assertEquals(p.status, expectStatus, "the recording: " + p.logLines);
            log = LlmSpikeGui.EvalLog.currentFile();
        } finally {
            LlmSpikeGui.EvalLog.closeForTest();
        }
        return log;
    }

    /** The board's written line with its Lightning Bolt aimed at the solver: it ends with the opponent at 3 life. */
    private static File boltAtSelf() throws Exception {
        List<String> lines = Files.readAllLines(SPLICE.toPath(), StandardCharsets.UTF_8);
        int sol = lines.indexOf("[solution]");
        List<String> out = new ArrayList<>(lines.subList(sol, lines.size()));
        int bolt = out.indexOf("cast Lightning Bolt");
        Assert.assertEquals(out.get(bolt + 1), "opponent", "the written line aims its Bolt at the opponent");
        out.set(bolt + 1, "you");
        File f = Files.createTempFile("bolt-at-self", ".txt").toFile();
        f.deleteOnExit();
        Files.write(f.toPath(), out, StandardCharsets.UTF_8);
        return f;
    }

    /** The recording's decision rows of the solver, as "type n=N c=C [options]". */
    private static List<JsonObject> rows(File log) throws Exception {
        List<JsonObject> out = new ArrayList<>();
        for (String l : Files.readAllLines(log.toPath(), StandardCharsets.UTF_8)) {
            JsonObject r = JsonParser.parseString(l).getAsJsonObject();
            if ("decision".equals(r.get("t").getAsString()) && "Solver".equals(r.get("seat").getAsString())) out.add(r);
        }
        return out;
    }

    private static LlmSpikePuzzle.StepResult search(File log, int k, Integer force) throws Exception {
        LlmSpikePuzzle.StepResult r = LlmSpikePuzzle.stepSearch(SPLICE, ID, board(), true, ReplayBridge.PREFIX + log.getPath(),
                SEED, k, force, BUDGET, 32, 120_000L);
        Assert.assertNull(LlmSpikeGui.FORK_HOOK, "the fork hook is put back");
        Assert.assertNull(PuzzleArms.searchLine, "the search line is cleared");
        Assert.assertEquals(ReplayBridge.HANDOVER, -1, "the handover is put back");
        return r;
    }

    @Test
    public void aSolvedLineIsWinnableBeforeItsLastDecisionAndAtItsEnd() throws Exception {
        File rec = record(SPLICE.getPath(), "solved");
        List<JsonObject> rows = rows(rec);
        int last = rows.size() - 1;
        Assert.assertEquals(rows.get(last).get("type").getAsString(), "play", "the line ends with Thermo-Alchemist's ping");

        LlmSpikePuzzle.StepResult before = search(rec, last, null);
        Assert.assertEquals(before.verdict, "proven", before.toString());
        Assert.assertNull(before.drift, before.toString());
        // The line starts at the handover: its first step is the recorded last decision, a play menu of n options.
        Assert.assertTrue(before.line.startsWith("Solver:" + rows.get(last).get("n_options").getAsInt() + ":"), before.line);

        // Handed over after the last row: the recorded game itself, which solved.
        LlmSpikePuzzle.StepResult end = search(rec, rows.size(), null);
        Assert.assertEquals(end.verdict, "proven", end.toString());
        // Nothing is left to choose: one rollout (the recorded game), and the witness replayed once more.
        Assert.assertEquals(end.replays, 2, end.toString());
        Assert.assertEquals(end.line, "", "no draw after the last row");
    }

    @Test
    public void aLostLineIsUnwinnableAfterTheBoltAndWinnableBeforeIt() throws Exception {
        File rec = record(boltAtSelf().getPath(), "failed");
        List<JsonObject> rows = rows(rec);
        int bolt = -1;   // the Bolt's target row: the first target row whose choice is the solver
        for (int i = 0; i < rows.size(); i++) {
            JsonObject r = rows.get(i);
            if ("target".equals(r.get("type").getAsString()) && r.get("choice").getAsInt() == 0
                    && r.get("options").getAsJsonArray().get(0).getAsString().startsWith("you")) { bolt = i; break; }
        }
        Assert.assertTrue(bolt > 0, "the recording aims a spell at the solver");

        LlmSpikePuzzle.StepResult end = search(rec, rows.size(), null);
        Assert.assertEquals(end.verdict, "unwinnable", "every line after the last decision was played and lost: " + end);
        Assert.assertTrue(end.exhausted, end.toString());
        Assert.assertEquals(end.capped, 0, end.toString());

        LlmSpikePuzzle.StepResult after = search(rec, bolt + 1, null);
        Assert.assertEquals(after.verdict, "unwinnable", "3 damage short with one ping left: " + after);

        LlmSpikePuzzle.StepResult before = search(rec, bolt, null);
        Assert.assertEquals(before.verdict, "proven", "the Bolt at the opponent still wins: " + before);

        // The floor at the Bolt's target: of the three targets, only the opponent keeps the win.
        LlmSpikePuzzle.StepResult probe = LlmSpikePuzzle.stepProbe(SPLICE, ID, board(), true,
                ReplayBridge.PREFIX + rec.getPath(), SEED, bolt, 120_000L);
        Assert.assertNull(probe.drift, probe.toString());
        Assert.assertEquals(probe.bound, 3, "you, the opponent, Thermo-Alchemist: " + probe);
        Assert.assertEquals(probe.modelValue, 0, "the recording chose the solver");
        List<String> kept = new ArrayList<>();
        for (int o = 0; o < probe.bound; o++) {
            LlmSpikePuzzle.StepResult f = search(rec, bolt, o);
            Assert.assertTrue("proven".equals(f.verdict) || "unwinnable".equals(f.verdict), "every target is decided: " + f);
            Assert.assertEquals(f.forced, Integer.valueOf(o));
            if ("proven".equals(f.verdict)) kept.add(String.valueOf(o));
        }
        Assert.assertEquals(kept, java.util.Arrays.asList("1"), "only the opponent keeps the win");
    }

    @Test
    public void aForcedMultiPickOrAHandoverPastTheRecordingIsRefused() throws Exception {
        File rec = record(SPLICE.getPath(), "solved");
        List<JsonObject> rows = rows(rec);
        int splice = -1;
        for (int i = 0; i < rows.size(); i++) if (rows.get(i).has("picks")) { splice = i; break; }
        Assert.assertTrue(splice > 0, "the line splices Desperate Ritual: a multi-pick row");
        LlmSpikePuzzle.StepResult probe = LlmSpikePuzzle.stepProbe(SPLICE, ID, board(), true,
                ReplayBridge.PREFIX + rec.getPath(), SEED, splice, 120_000L);
        Assert.assertNotNull(probe.refused, "a multi-pick decision is not forced in v1: " + probe);
        Assert.assertTrue(probe.refused.contains("multi-pick"), "refused for the multi-pick, not another reason: " + probe);

        LlmSpikePuzzle.StepResult past = search(rec, rows.size() + 1, null);
        Assert.assertEquals(past.verdict, "drift", "the game ended before the handover: " + past);
    }

    /** The recording with decision row {@code i}'s first option renamed: the replay up to it still matches. */
    private static File withRowChanged(File log, int i) throws Exception {
        List<String> out = new ArrayList<>();
        int seen = 0;
        for (String l : Files.readAllLines(log.toPath(), StandardCharsets.UTF_8)) {
            JsonObject r = JsonParser.parseString(l).getAsJsonObject();
            if ("decision".equals(r.get("t").getAsString()) && "Solver".equals(r.get("seat").getAsString()) && seen++ == i) {
                com.google.gson.JsonArray o = r.getAsJsonArray("options");
                o.set(0, new com.google.gson.JsonPrimitive("CAST Something Else @99"));
                l = r.toString();
            }
            out.add(l);
        }
        File f = new File(Files.createTempDirectory("step-regret-changed").toFile(), log.getName());
        f.getParentFile().deleteOnExit();
        f.deleteOnExit();
        Files.write(f.toPath(), out, StandardCharsets.UTF_8);
        return f;
    }

    @Test
    public void anUnforcedSearchWhoseFirstDecisionIsNotTheRecordingsIsADrift() throws Exception {
        // The handover switches inside row K's write, so the rest of that hook runs the arm's branches: the first
        // decision after it must still be the recording's row K+1, or the position searched is not the recorded one.
        File rec = record(SPLICE.getPath(), "solved");
        int last = rows(rec).size() - 1;
        LlmSpikePuzzle.StepResult r = search(withRowChanged(rec, last), last, null);
        Assert.assertEquals(r.verdict, "drift", "the row after the handover differs from the recording's: " + r);
        Assert.assertTrue(String.valueOf(r.drift).contains("the first decision after the handover"), r.toString());
    }

    /** A replay seat over {@code rows}, handed over after the first {@code k}, whose first decision after it was {@code live}. */
    private static ReplayBridge bridgeAfter(List<ReplayBridge.Row> rows, int k, ReplayBridge.Row live, int draws) {
        ReplayBridge.HANDOVER = k;
        ReplayBridge rb;
        try { rb = new ReplayBridge("Solver/smart", "rec", new ArrayList<>(), rows); }
        finally { ReplayBridge.HANDOVER = -1; }
        rb.firstAfter = live;
        rb.drawsAtFirstAfter = draws;
        return rb;
    }

    private static ReplayBridge.Row row(String type, int n, boolean multi) {
        return new ReplayBridge.Row(type, n, 0, java.util.Arrays.asList("a", "b", "c").subList(0, n), multi);
    }

    @Test
    public void theFirstDecisionAfterTheHandoverTakesOneDrawOrAMultiPicksAtMostItsMenu() {
        List<ReplayBridge.Row> rows = new ArrayList<>();
        rows.add(row("play", 3, false));
        rows.add(row("splice", 3, true));
        Assert.assertNull(LlmSpikePuzzle.firstAfterMismatch(bridgeAfter(rows, 0, row("play", 3, false), 1)));
        String two = LlmSpikePuzzle.firstAfterMismatch(bridgeAfter(rows, 0, row("play", 3, false), 2));
        Assert.assertNotNull(two, "a single-choice row that took two draws drifted");
        Assert.assertTrue(two.contains("2 search draws"), two);
        Assert.assertNotNull(LlmSpikePuzzle.firstAfterMismatch(bridgeAfter(rows, 0, row("play", 3, false), 0)), "none is not one");
        // A multi-pick takes one draw per pick, up to its menu; more were drawn in the rest of row K's hook.
        Assert.assertNull(LlmSpikePuzzle.firstAfterMismatch(bridgeAfter(rows, 1, row("splice", 3, true), 3)));
        String four = LlmSpikePuzzle.firstAfterMismatch(bridgeAfter(rows, 1, row("splice", 3, true), 4));
        Assert.assertNotNull(four, "a multi-pick row after more draws than its menu drifted");
        Assert.assertTrue(four.contains("4 search draws"), four);
    }

    @Test
    public void aGameThatEndsBeforeTheFirstDecisionAfterTheHandoverIsAMismatchNotACrash() {
        List<ReplayBridge.Row> rows = new ArrayList<>();
        rows.add(row("play", 3, false));
        String m = LlmSpikePuzzle.firstAfterMismatch(bridgeAfter(rows, 0, null, -1));
        Assert.assertNotNull(m);
        Assert.assertTrue(m.contains("the game ended before the first decision after the handover"), m);
        Assert.assertNull(LlmSpikePuzzle.firstAfterMismatch(bridgeAfter(rows, 1, null, -1)), "no row K+1 to check past the end");
    }

    @Test
    public void theWitnessReplayChecksItsFirstDecisionToo() throws Exception {
        // The search proves the position on the unchanged recording; the recording then changes under the witness
        // replay, which still solves but whose first decision after the handover is no longer the recording's.
        File rec = record(SPLICE.getPath(), "solved");
        int last = rows(rec).size() - 1;
        File changed = withRowChanged(rec, last);
        LlmSpikePuzzle.BEFORE_WITNESS = () -> {
            try { Files.copy(changed.toPath(), rec.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING); }
            catch (java.io.IOException e) { throw new RuntimeException(e); }
        };
        try {
            LlmSpikePuzzle.StepResult r = search(rec, last, null);
            Assert.assertEquals(r.verdict, "drift", "the witness replay is checked like every rollout: " + r);
            Assert.assertTrue(String.valueOf(r.drift).contains("the first decision after the handover"), r.toString());
        } finally {
            LlmSpikePuzzle.BEFORE_WITNESS = null;
        }
    }

    /** The next long of the engine RNG after building a Solver seat of {@code mode} with {@code bridge} on seed 7. */
    private static long nextAfterSeat(String mode, LLMBridge bridge) {
        forge.game.Game g = SeatObservationTest.bareGame();
        MyRandom.setRandom(new java.util.Random(7L));
        LlmSpikeGui.PlayerControllerLLM c = new LlmSpikeGui.PlayerControllerLLM(g, g.getPlayers().get(0),
                new forge.ai.LobbyPlayerAi("Solver", null), "Solver", "", bridge, bridge, mode);
        Assert.assertNotNull(c);
        return MyRandom.getRandom().nextLong();
    }

    @Test
    public void theHandedOverSeatDrawsNothingFromTheEngineAndTheSearchArmStillDrawsItsLong() {
        List<ReplayBridge.Turn> turns = new ArrayList<>();
        turns.add(new ReplayBridge.Turn(1, "", 1, new ArrayList<>(), null));
        long untouched = new java.util.Random(7L).nextLong();
        java.util.Random once = new java.util.Random(7L);
        once.nextLong();
        long afterOneDraw = once.nextLong();
        ReplayBridge.HANDOVER = 0;
        ReplayBridge handed;
        try {
            handed = new ReplayBridge("Solver/smart", "rec", turns, new ArrayList<>());
        } finally {
            ReplayBridge.HANDOVER = -1;
        }
        Assert.assertEquals(nextAfterSeat("llm", handed), untouched, "a replay seat handed over at 0 draws no long");
        Assert.assertTrue(handed.handedOver);
        Assert.assertEquals(nextAfterSeat("llm", new ReplayBridge("Solver/smart", "rec", turns, new ArrayList<>())), untouched,
                "a plain replay seat draws none either");
        Assert.assertEquals(nextAfterSeat("search", handed), afterOneDraw, "the search arm draws its one long, as before");
    }
}
