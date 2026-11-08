package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
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
 * The replay bridge (step regret, step 1): a recorded attempt's model turns, answered again through the seat's own
 * answer path, must give the recorded game; a decision asked differently from the recorded one is a drift that stops
 * the replay, never a guess. The recording here is a real game: the splice board's written line (ScriptBridge) played
 * by the model seat with the game log on, so every row the replay reads is one the seat wrote.
 */
public class ReplayBridgeTest {
    private static final File REPO = PuzzleSetupTest.repoRoot();
    private static final File SPLICE = new File(REPO, "eval/puzzles/hard/gen/splice_06dad20af5b3.pzl");
    private static final long SEED = 1L;

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

    /** The recorded attempts are faithful-profile games (CONTEXT_PROFILE=v4-faithful): the labels the replay matches
     *  are that profile's. */
    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void clearStatics() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        LlmSpikeGui.EvalLog.closeForTest();
        LlmSpikeGui.puzzleSolverSeat = null;
        LlmSpikeGui.puzzleBrief = null;
        LlmSpikeGui.puzzleLastTurn = -1;
        LlmSpikeGui.SEED_USED = 0L;
    }

    /** One puzzle game as the runner plays it, with its game log written to a fresh file under {@code dir}. */
    private static final class Game1 {
        LlmSpikePuzzle.Played played;
        File log;
    }

    private static Game1 play(Map<String, List<String>> sections, String spec, File dir) throws Exception {
        LlmSpikeGui.EvalLog.closeForTest();
        LlmSpikeGui.EvalLog.initToDir(dir.getPath(), SEED);
        MyRandom.setRandom(new java.util.Random(SEED));
        LlmSpikeGui.SEED_USED = SEED;
        Game1 g = new Game1();
        try {
            g.played = LlmSpikePuzzle.playOnce(SPLICE, "splice_06dad20af5b3", sections, "llm", "ai", spec, spec, SEED,
                    120_000L, 0, true, true);
            g.log = LlmSpikeGui.EvalLog.currentFile();
        } finally {
            LlmSpikeGui.EvalLog.closeForTest();
        }
        return g;
    }

    private static Map<String, List<String>> board() {
        return FileSection.parseSections(FileUtil.readFile(SPLICE));
    }

    private static File tempDir(String name) throws Exception {
        File d = Files.createTempDirectory("replay-" + name).toFile();
        d.deleteOnExit();
        return d;
    }

    /** The decision rows of a game log: type, n_options, choice and options, in order. */
    private static List<String> decisions(File log) throws Exception {
        List<String> out = new ArrayList<>();
        for (String l : Files.readAllLines(log.toPath(), StandardCharsets.UTF_8)) {
            JsonObject r = JsonParser.parseString(l).getAsJsonObject();
            if (!"decision".equals(r.get("t").getAsString())) continue;
            out.add(r.get("type").getAsString() + " n=" + r.get("n_options").getAsInt() + " c=" + r.get("choice").getAsInt()
                    + " " + r.get("options"));
        }
        return out;
    }

    private static ReplayBridge replayOf(LlmSpikePuzzle.Played p) { return LlmSpikePuzzle.replayLine(p.game); }

    @Test
    public void aRecordedGameReplaysToTheSameEngineLogAndOutcome() throws Exception {
        Game1 rec = play(board(), "script:" + SPLICE.getPath(), tempDir("rec"));
        Assert.assertEquals(rec.played.status, "solved", "the written line solves the board: " + rec.played.logLines);
        Assert.assertNotNull(rec.log, "the recording has a game log");

        // The attempt's result row beside its game log, as the runner writes it: the replay must give its outcome.
        writeResult(rec, rec.played.status, LlmSpikePuzzle.logFingerprint(rec.played.logLines));
        Game1 rep = play(board(), ReplayBridge.PREFIX + rec.log.getPath(), tempDir("rep"));
        Assert.assertNull(LlmSpikeGui.FORK_HOOK, "the runner puts the fork hook back when the game is over");
        ReplayBridge b = replayOf(rep.played);
        Assert.assertNotNull(b, "a replay: spec builds the replay bridge");
        Assert.assertEquals(b.report().get("replay_recorded_status"), "solved", "the recorded outcome was read");
        Assert.assertEquals(b.report().get("replay_version"), "replay-bridge-v3", "the stamp's literal (eval/versions.py)");
        Assert.assertNull(b.drift, "nothing drifted");
        Assert.assertTrue(b.reproduced(), "every recorded turn and decision row was used: " + b.report());
        Assert.assertTrue(b.turns.size() > 10, "the splice line is a dozen decisions or more: " + b.turns.size());
        Assert.assertEquals(rep.played.status, rec.played.status);
        // Line for line except the wall-clock "Match Duration" line, which logFingerprint leaves out too: it read 00s
        // against 01s under load in #373's acceptance while every engine line matched.
        Assert.assertEquals(LlmSpikePuzzle.withoutWallClock(rep.played.logLines),
                LlmSpikePuzzle.withoutWallClock(rec.played.logLines),
                "the same engine log, line for line");
        Assert.assertEquals(LlmSpikePuzzle.logFingerprint(rep.played.logLines),
                LlmSpikePuzzle.logFingerprint(rec.played.logLines));
        Assert.assertEquals(decisions(rep.log), decisions(rec.log), "the same decision rows");
    }

    @Test
    public void aChangedBoardDriftsAtTheFirstDecisionItChangesAndStops() throws Exception {
        Game1 rec = play(board(), "script:" + SPLICE.getPath(), tempDir("rec2"));
        // One Lava Spike fewer in hand: the first priority menu has one CAST fewer than the recorded one.
        Map<String, List<String>> changed = board();
        List<String> state = new ArrayList<>(changed.get("state"));
        for (int i = 0; i < state.size(); i++)
            if (state.get(i).startsWith("p0hand=")) state.set(i, state.get(i).replaceFirst("Lava Spike;", ""));
        changed.put("state", state);

        Game1 rep = play(changed, ReplayBridge.PREFIX + rec.log.getPath(), tempDir("rep2"));
        ReplayBridge b = replayOf(rep.played);
        Assert.assertNotNull(b.drift, "the changed menu is a drift");
        Assert.assertTrue(b.drift.startsWith("turn 1 "), b.drift);
        Assert.assertTrue(b.drift.contains("options"), b.drift);
        Assert.assertFalse(b.reproduced());
        Assert.assertEquals(rep.played.status, "drift", "a drifted replay is no result");
        Assert.assertTrue(decisions(rep.log).size() < decisions(rec.log).size(),
                "the replay stopped: " + decisions(rep.log).size() + " rows of " + decisions(rec.log).size());
        Assert.assertEquals(b.report().get("replay_drift"), b.drift);
        // The runner ends the game at the drift: the seat is asked nothing more (it was asked again, a reaction
        // window with five castable spells, when only the 200 ms poll stopped it).
        Assert.assertEquals(b.asksAfterDrift, 0, "asks served after the drift");
    }

    private static void writeResult(Game1 rec, String status, String logSha) throws Exception {
        JsonObject row = new JsonObject();
        row.addProperty("schema", "pzl-result-v1");
        row.addProperty("seed", SEED);
        row.addProperty("status", status);
        row.addProperty("log_sha256", logSha);
        Files.write(new File(rec.log.getParentFile(), "puzzle-results.jsonl").toPath(),
                (row + "\n").getBytes(StandardCharsets.UTF_8));
    }

    @Test
    public void anOutcomeOtherThanTheRecordedOneIsADriftAtTheEnd() throws Exception {
        Game1 rec = play(board(), "script:" + SPLICE.getPath(), tempDir("rec3"));
        writeResult(rec, "failed", "0000");
        Game1 rep = play(board(), ReplayBridge.PREFIX + rec.log.getPath(), tempDir("rep3"));
        ReplayBridge b = replayOf(rep.played);
        Assert.assertNotNull(b.drift, "every turn matched but the outcome did not");
        Assert.assertTrue(b.drift.startsWith("game end:") && b.drift.contains("solved") && b.drift.contains("failed"), b.drift);
        Assert.assertEquals(rep.played.status, "drift");
        Assert.assertEquals(b.report().get("replay_recorded_status"), "failed");
    }

    @Test
    public void aReplayWithTheGameLogOffIsRefused() throws Exception {
        Game1 rec = play(board(), "script:" + SPLICE.getPath(), tempDir("rec4"));
        LlmSpikeGui.EvalLog.closeForTest();
        MyRandom.setRandom(new java.util.Random(SEED));
        LlmSpikeGui.SEED_USED = SEED;
        String spec = ReplayBridge.PREFIX + rec.log.getPath();
        try {
            LlmSpikePuzzle.playOnce(SPLICE, "splice_06dad20af5b3", board(), "llm", "ai", spec, spec, SEED, 120_000L, 0, true, true);
            Assert.fail("a replay ran with no game log, so no decision row could be checked");
        } catch (IllegalStateException expected) {
            Assert.assertTrue(expected.getMessage().contains("EVAL_OUT"), expected.getMessage());
        }
        Assert.assertNull(LlmSpikeGui.FORK_HOOK, "the fork hook is left as it was");
    }

    /** The refusal where a game builds its seat from model strings (oneTierPair): the fast tier's spec is never
     *  contacted, since requireBothTiers runs before LLMBridgeFactory.create makes any bridge. */
    @Test
    public void aSeatWithAReplayOnOneTierIsRefusedWhereItIsBuilt() throws Exception {
        new LineFindingProbeTest().initializeModel();
        forge.game.Game g = LineFindingProbeTest.bareGame(forge.game.phase.PhaseType.MAIN1);
        forge.game.player.Player me = g.getPlayers().get(0);
        forge.ai.LobbyPlayerAi lp = new forge.ai.LobbyPlayerAi("Solver", null);
        String rec = recording(callRow(1, MENU, "TOOL CALL answer {\"option\":1}")).getPath();
        try {
            new LlmSpikeGui.PlayerControllerLLM(g, me, lp, "Solver", "", ReplayBridge.PREFIX + rec, "openrouter:zero-model/none", "llm");
            Assert.fail("a seat replaying on its smart tier only was built");
        } catch (IllegalArgumentException expected) {
            Assert.assertTrue(expected.getMessage().contains("both tiers"), expected.getMessage());
        }
    }

    @Test
    public void aReplayOnOneTierOnlyIsRefused() {
        ReplayBridge.requireBothTiers("replay:a", "replay:a");
        ReplayBridge.requireBothTiers("openrouter:x/y", "openrouter:x/z");
        for (String[] pair : new String[][] {{"replay:a", "openrouter:x/y"}, {"openrouter:x/y", "replay:a"}, {"replay:a", "replay:b"}}) {
            try {
                ReplayBridge.requireBothTiers(pair[0], pair[1]);
                Assert.fail("built " + Arrays.toString(pair));
            } catch (IllegalArgumentException expected) {
                Assert.assertTrue(expected.getMessage().contains("both tiers"), expected.getMessage());
            }
        }
    }

    /** A game log in a directory of its own: a result row beside it would be read as the attempt's (readResult). */
    private static File recording(String... rows) throws Exception {
        File f = new File(tempDir("log"), "game-1-100.jsonl");
        f.deleteOnExit();
        Files.write(f.toPath(), String.join("\n", rows).getBytes(StandardCharsets.UTF_8));
        return f;
    }

    private static String callRow(int call, String prompt, String raw) {
        JsonObject o = new JsonObject();
        o.addProperty("t", "context_call");
        o.addProperty("protocol", "native");
        o.addProperty("seat", "Solver");
        o.addProperty("call", call);
        o.addProperty("round", 0);
        o.addProperty("prompt", prompt);
        o.addProperty("raw_reply", raw);
        return o.toString();
    }

    private static String messages(String user) {
        JsonArray a = new JsonArray();
        a.add(NativeTools.message("user", user));
        return a.toString();
    }

    private static final String MENU = "== Turn 1 MAIN2 (active: Solver | priority: you) ==\n\n"
            + "DECISION: You have priority (MAIN2, your main phase). Choose ONE action:\n"
            + "  1) CAST Lava Spike @2 {R} [Lava Spike deals 3 damage to target player or planeswalker.]\n"
            + "  2) PASS (do nothing)\n"
            + "ANSWER: call `answer` with `option`: one number from the list.";

    @Test
    public void eachRecordedTurnIsServedWithItsCallsInOrder() throws Exception {
        String raw = "TOOL CALL set_plan {\"goal\":\"burn\",\"next\":\"Spike\"}\nTOOL CALL answer {\"option\":1}";
        ReplayBridge b = ReplayBridge.fromFile("Solver/smart", recording(callRow(1, MENU, raw)).getPath());
        NativeTools.Turn t = b.chat(messages(MENU), "[]", true);
        Assert.assertEquals(t.calls.size(), 2);
        Assert.assertEquals(t.calls.get(0).name, AnswerTool.SET_PLAN);
        Assert.assertEquals(t.calls.get(1).name, AnswerTool.NAME);
        Assert.assertEquals(t.calls.get(1).arguments, "{\"option\":1}");
        Assert.assertNull(b.drift);
        Assert.assertEquals(b.unusedTurns(), 0);
    }

    @Test
    public void aRetryAfterNoReplyIsTheSameRecordedTurn() throws Exception {
        String rec1 = callRow(1, MENU, null);
        ReplayBridge b = ReplayBridge.fromFile("Solver/smart",
                recording(rec1, callRow(2, MENU, "TOOL CALL answer {\"option\":2}")).getPath());
        Assert.assertNull(b.chat(messages(MENU), "[]", true), "the recorded turn had no reply");
        b.restart();
        Assert.assertNull(b.chat(messages(MENU), "[]", true), "the seat's retry is the same turn, not the next");
        Assert.assertEquals(b.chat(messages(MENU), "[]", true).calls.get(0).arguments, "{\"option\":2}");
        Assert.assertNull(b.drift);
    }

    @Test
    public void aRelabelledOptionIsADriftAndTheRestIsTheDoNothingAnswer() throws Exception {
        ReplayBridge b = ReplayBridge.fromFile("Solver/smart",
                recording(callRow(1, MENU, "TOOL CALL answer {\"option\":1}"),
                        callRow(2, MENU, "TOOL CALL answer {\"option\":1}")).getPath());
        String other = MENU.replace("CAST Lava Spike @2", "CAST Lava Spike @3");
        NativeTools.Turn t = b.chat(messages(other), "[]", true);
        Assert.assertNotNull(b.drift);
        Assert.assertTrue(b.drift.contains("@3"), b.drift);
        Assert.assertEquals(t.calls.get(0).arguments, "{\"option\":2}", "PASS, not the recorded option");
        Assert.assertEquals(b.chat(messages(MENU), "[]", true).calls.get(0).arguments, "{\"option\":2}",
                "after a drift nothing more is replayed");
    }

    private static final String PICKS = "== Turn 1 MAIN2 (active: Solver | priority: you) ==\n\n"
            + "DECISION: Choose cards to sacrifice:\n"
            + "  1) Mountain @8\n"
            + "  2) Mountain @9\n"
            + "ANSWER: call `answer` with `options`: the numbers you pick, exactly 1.";
    private static final String BLOCKS = "== Turn 2 COMBAT_DECLARE_BLOCKERS (active: Opponent | priority: you) ==\n\n"
            + "DECISION: Declare blockers.\n"
            + "ATTACKERS: 1 = Grizzly Bears 2/2 @2\n"
            + "YOUR BLOCKERS: 1 = Typhoid Rats 1/1 @3; 2 = Boggart Ram-Gang 3/3 @4\n"
            + "ANSWER: call `answer` with `blocks`: the pairs you declare.";

    private static final String PICK_RANGE_ASK = PICKS.replace("exactly 1", "between 1 and 2");
    private static final String NUMBER_ASK = "== Turn 1 MAIN2 (active: Solver | priority: you) ==\n\n"
            + "DECISION: Choose X for Fireball.\n"
            + "ANSWER: call `answer` with `number`: an integer from 0 through 4.";

    @Test
    public void aQuestionWithTheSameNumberedOptionsButAnotherRangeOrListDrifts() throws Exception {
        String[][] cases = {
                {PICKS, PICKS.replace("exactly 1", "exactly 2"), "exactly"},
                {BLOCKS, BLOCKS.replace("; 2 = Boggart Ram-Gang 3/3 @4", ""), "Boggart"},
                // Only the upper end differs: a pick range and a number range.
                {PICK_RANGE_ASK, PICK_RANGE_ASK.replace("between 1 and 2", "between 1 and 3"), "between 1 and 3"},
                {NUMBER_ASK, NUMBER_ASK.replace("from 0 through 4", "from 0 through 5"), "from 0 through 5"},
        };
        for (String[] c : cases) {
            ReplayBridge b = ReplayBridge.fromFile("Solver/smart",
                    recording(callRow(1, c[0], "TOOL CALL answer {\"options\":[1]}")).getPath());
            b.chat(messages(c[1]), "[]", true);
            Assert.assertNotNull(b.drift, "drifts: " + c[1]);
            Assert.assertTrue(b.drift.contains(c[2]), b.drift);
            ReplayBridge same = ReplayBridge.fromFile("Solver/smart",
                    recording(callRow(1, c[0], "TOOL CALL answer {\"options\":[1]}")).getPath());
            same.chat(messages(c[0]), "[]", true);
            Assert.assertNull(same.drift, "the recorded question itself is no drift");
        }
    }

    private static String decisionRow(String type, int n, int choice, String... options) {
        JsonObject o = new JsonObject();
        o.addProperty("t", "decision");
        o.addProperty("seat", "Solver");
        o.addProperty("type", type);
        o.addProperty("n_options", n);
        o.addProperty("choice", choice);
        JsonArray a = new JsonArray();
        for (String x : options) a.add(x);
        o.add("options", a);
        return o.toString();
    }

    private static final List<String> SPIKE_PASS = Arrays.asList("CAST Lava Spike @2 {R}", "PASS (do nothing)");

    @Test
    public void aDecisionRowOtherThanTheRecordedOneDrifts() throws Exception {
        String rec = recording(callRow(1, MENU, "TOOL CALL answer {\"option\":1}"),
                decisionRow("play", 2, 0, SPIKE_PASS.toArray(new String[0]))).getPath();
        ReplayBridge same = ReplayBridge.fromFile("Solver/smart", rec);
        same.onDecisionRow("Opponent", "play", 2, -1, SPIKE_PASS, -1);
        same.onDecisionRow("Solver", "play", 2, 0, SPIKE_PASS, -1);
        Assert.assertNull(same.drift, "the recorded row, and another seat's row, are no drift");
        String[][] wrong = {{"play", "2", "-1"}, {"target", "2", "0"}, {"play", "3", "0"}};
        for (String[] w : wrong) {
            ReplayBridge b = ReplayBridge.fromFile("Solver/smart", rec);
            b.onDecisionRow("Solver", w[0], Integer.parseInt(w[1]), Integer.parseInt(w[2]), SPIKE_PASS, -1);
            Assert.assertNotNull(b.drift, Arrays.toString(w));
            Assert.assertTrue(b.drift.startsWith("decision row 1: "), b.drift);
        }
        ReplayBridge relabelled = ReplayBridge.fromFile("Solver/smart", rec);
        relabelled.onDecisionRow("Solver", "play", 2, 0, Arrays.asList("CAST Lava Spike @3 {R}", "PASS (do nothing)"), -1);
        Assert.assertTrue(relabelled.drift.startsWith("decision row 1: "), String.valueOf(relabelled.drift));
        same.onDecisionRow("Solver", "play", 2, -1, SPIKE_PASS, -1);
        Assert.assertNotNull(same.drift, "a second row where the recording has one");
        Assert.assertTrue(same.drift.contains("after the recording's last"), same.drift);
    }

    @Test
    public void anAskWithANewRefusalOrAnotherMessageCountDrifts() throws Exception {
        // The recording's second round: its answer was refused once and asked again.
        JsonArray recMsgs = new JsonArray();
        recMsgs.add(NativeTools.message("user", MENU));
        recMsgs.add(JsonParser.parseString(ReplayBridge.turnOf("TOOL CALL set_plan {\"goal\":\"burn\"}", 1).messageJson));
        JsonObject tool = new JsonObject();
        tool.addProperty("role", "tool");
        tool.addProperty("tool_call_id", "x");
        tool.addProperty("content", "Plan recorded. It does not answer the decision: call `answer`.");
        recMsgs.add(tool);
        JsonObject row = JsonParser.parseString(callRow(1, MENU, "TOOL CALL answer {\"option\":1}")).getAsJsonObject();
        row.addProperty("round", 1);
        row.add("messages", recMsgs);
        String rec = recording(row.toString()).getPath();

        ReplayBridge same = ReplayBridge.fromFile("Solver/smart", rec);
        same.chat(recMsgs.toString(), "[]", true);
        Assert.assertNull(same.drift, "the recorded round itself");

        JsonArray refused = recMsgs.deepCopy();
        refused.get(2).getAsJsonObject().addProperty("content", "ERROR: `option` must be 1..2. Nothing was done.");
        ReplayBridge b = ReplayBridge.fromFile("Solver/smart", rec);
        b.chat(refused.toString(), "[]", true);
        Assert.assertNotNull(b.drift, "a refusal the recording did not have");
        Assert.assertTrue(b.drift.contains("a refusal"), b.drift);

        ReplayBridge c = ReplayBridge.fromFile("Solver/smart", rec);
        c.chat(messages(MENU), "[]", true);
        Assert.assertNotNull(c.drift, "a first round where the recording's was the second");
        Assert.assertTrue(c.drift.contains("1 messages where the recording had 3"), c.drift);
    }

    @Test
    public void twoResultRowsForTheSeedAreRefusedNotPicked() throws Exception {
        File log = recording(callRow(1, MENU, "TOOL CALL answer {\"option\":1}"));
        String row = "{\"seed\":1,\"status\":\"%s\",\"log_sha256\":\"x\"}";
        Files.write(new File(log.getParentFile(), "puzzle-results.jsonl").toPath(), (String.format(row, "solved") + "\n"
                + "{\"seed\":2,\"status\":\"failed\"}\n" + String.format(row, "failed") + "\n").getBytes(StandardCharsets.UTF_8));
        try {
            ReplayBridge.fromFile("Solver/smart", log.getPath());
            Assert.fail("two result rows for seed 1, one of them taken");
        } catch (IllegalArgumentException expected) {
            Assert.assertTrue(expected.getMessage().contains("2 result rows for seed 1"), expected.getMessage());
        }
        Files.write(new File(log.getParentFile(), "puzzle-results.jsonl").toPath(),
                (String.format(row, "solved") + "\n{\"seed\":2,\"status\":\"failed\"}\n").getBytes(StandardCharsets.UTF_8));
        Assert.assertEquals(ReplayBridge.fromFile("Solver/smart", log.getPath()).recordedStatus, "solved", "the seed's own row");
    }

    @Test
    public void aDirectoryRecordingIsReadForTheSeedPlayed() throws Exception {
        File dir = tempDir("dir");
        Files.write(new File(dir, "game-1-100.jsonl").toPath(),
                callRow(1, MENU, "TOOL CALL answer {\"option\":1}").getBytes(StandardCharsets.UTF_8));
        Files.write(new File(dir, "game-2-100.jsonl").toPath(),
                callRow(1, MENU, "TOOL CALL answer {\"option\":2}").getBytes(StandardCharsets.UTF_8));
        Assert.assertEquals(ReplayBridge.recordingFor(dir.getPath(), 2L).getName(), "game-2-100.jsonl");
        try {
            ReplayBridge.recordingFor(dir.getPath(), 3L);
            Assert.fail("no recording for seed 3");
        } catch (IllegalArgumentException expected) {
            Assert.assertTrue(expected.getMessage().contains("seed 3"), expected.getMessage());
        }
        LlmSpikeGui.SEED_USED = 2L;
        LLMBridge b = LlmSpikeGui.LLMBridgeFactory.create("Solver/smart", ReplayBridge.PREFIX + dir.getPath(), "");
        Assert.assertTrue(b instanceof ReplayBridge, String.valueOf(b));
        Assert.assertTrue(b.supportsNativeTools());
    }

    /** ReplayBridge.RANGES copies the wording AnswerTool writes for an answer's range. If AnswerTool rewords one, ranges()
     *  would find nothing in the recorded and the live ask alike and the range check would switch off without a test
     *  going red (simplify review of #365). Each kind that writes a range (MANY, MANY_REPEAT, NUMBER, ALLOC) is built
     *  through AnswerTool itself and must yield one statement holding both ends. */
    @Test
    public void everyAnswerRangeAnswerToolWritesIsReadWithBothEnds() {
        assertRangeRead(AnswerTool.Spec.many(5, 1, 3), "1", "3");
        assertRangeRead(AnswerTool.Spec.many(5, 2, 2), "2");
        assertRangeRead(AnswerTool.Spec.repeat(5, 1, 3), "1", "3");
        assertRangeRead(AnswerTool.Spec.repeat(5, 2, 2), "2");
        assertRangeRead(AnswerTool.Spec.number(-1, 4), "-1", "4");
        assertRangeRead(AnswerTool.Spec.number(0, 12), "0", "12");
        assertRangeRead(AnswerTool.Spec.alloc(3, 6), "3", "6");
    }

    /** One range statement read from the ask AnswerTool writes for {@code spec}, holding each of {@code ends} as a whole
     *  number (a sign and the digits around it count, so "1" is not found in "-1" or "12"). */
    private static void assertRangeRead(AnswerTool.Spec spec, String... ends) {
        String line = AnswerTool.instruction(spec, false);
        List<String> found = ReplayBridge.ranges(new ScriptBridge.Question(line));
        Assert.assertEquals(found.size(), 1, spec.kind + ": " + line);
        for (String v : ends)
            Assert.assertTrue(java.util.regex.Pattern.compile("(?<![-\\d])" + java.util.regex.Pattern.quote(v) + "(?!\\d)")
                    .matcher(found.get(0)).find(), v + " in " + found + " from " + line);
    }
}
