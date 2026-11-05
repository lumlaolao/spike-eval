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

    private static ReplayBridge replayOf(LlmSpikePuzzle.Played p) {
        LlmSpikeGui.PlayerControllerLLM seat = LlmSpikePuzzle.solverLlm(p.game);
        Assert.assertNotNull(seat, "the solver is the model seat");
        return seat.replayBridge();
    }

    @Test
    public void aRecordedGameReplaysToTheSameEngineLogAndOutcome() throws Exception {
        Game1 rec = play(board(), "script:" + SPLICE.getPath(), tempDir("rec"));
        Assert.assertEquals(rec.played.status, "solved", "the written line solves the board: " + rec.played.logLines);
        Assert.assertNotNull(rec.log, "the recording has a game log");

        Game1 rep = play(board(), ReplayBridge.PREFIX + rec.log.getPath(), tempDir("rep"));
        ReplayBridge b = replayOf(rep.played);
        Assert.assertNotNull(b, "a replay: spec builds the replay bridge");
        Assert.assertNull(b.drift, "nothing drifted");
        Assert.assertTrue(b.reproduced(), "every recorded turn and decision row was used: " + b.report());
        Assert.assertTrue(b.turns.size() > 10, "the splice line is a dozen decisions or more: " + b.turns.size());
        Assert.assertEquals(rep.played.status, rec.played.status);
        Assert.assertEquals(rep.played.logLines, rec.played.logLines, "the same engine log, line for line");
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
    }

    private static File recording(String... rows) throws Exception {
        File f = File.createTempFile("game-1-", ".jsonl");
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
        LLMBridge b = LlmSpikeGui.LLMBridgeFactory.create("Solver/smart", ReplayBridge.PREFIX + dir.getPath(), "");
        Assert.assertTrue(b instanceof ReplayBridge, String.valueOf(b));
        Assert.assertTrue(b.supportsNativeTools());
        Assert.assertEquals(Arrays.asList(LlmSpikeGui.LLMBridgeFactory.ROUTED_PREFIXES).contains(ReplayBridge.PREFIX), true);
    }
}
