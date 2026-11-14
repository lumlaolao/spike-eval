package forge.spike;

import java.util.ArrayList;
import java.util.List;

import org.testng.Assert;
import org.testng.ITestResult;
import org.testng.annotations.AfterClass;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

/**
 * The golden checks again, on the default protocol: every seat answers by calling `answer`
 * (ToolAnswerBridge). Every decision goes through the call (owner, 2026-10-15), so beyond each check's own
 * assertions, no tool ask any decision type makes may offer a text answer format. The game log is on, as in a
 * game: a cancelled cost was paid anyway only while decision rows were being written (random gauntlet), which no
 * check saw with the log off.
 */
public class GoldenChecksToolTest extends GoldenChecksTest {

    @BeforeClass
    public void toolMode() throws java.io.IOException {
        LineFindingProbeTest.TOOL_MODE = true;
        LlmSpikeGui.EvalLog.initToDir(TestTmp.dir("golden-tool-log").toString(), 7);
    }

    @AfterClass(alwaysRun = true)
    public void textMode() {
        LlmSpikeGui.EvalLog.closeForTest();
        LineFindingProbeTest.TOOL_MODE = false;
        LineFindingProbeTest.TOOL_BRIDGES.clear();
        LineFindingProbeTest.TOOL_SEATS.clear();
    }

    @BeforeMethod
    public void freshBridges() {
        LineFindingProbeTest.TOOL_BRIDGES.clear();
        LineFindingProbeTest.TOOL_SEATS.clear();
    }

    /** Leaks per test, asserted once at the end: a failing @AfterMethod would skip every later test. */
    private static final List<String> LEAKS = java.util.Collections.synchronizedList(new ArrayList<>());
    private static int chats, texts;

    @AfterMethod(alwaysRun = true)
    public void collectLeaks(ITestResult result) {
        for (ToolAnswerBridge b : new ArrayList<>(LineFindingProbeTest.TOOL_BRIDGES)) {
            for (String l : b.leaks) LEAKS.add(result.getName() + ": ..." + l + "...");
            chats += b.chats;
            texts += b.texts;
            if (b.texts > 0) LEAKS.add(result.getName() + ": " + b.texts + " ask(s) went out as text, not as a tool ask");
        }
        // Zero text parsing (owner, 2026-10-15): a decision on a tool ask reads the typed answer, never the reply.
        for (LlmSpikeGui.PlayerControllerLLM c : new ArrayList<>(LineFindingProbeTest.TOOL_SEATS))
            if (c.textParsesOnToolAsks > 0)
                LEAKS.add(result.getName() + ": " + c.textParsesOnToolAsks + " decision(s) read text on a tool ask");
    }

    @Test(priority = 2000)
    public void noToolAskOffersATextAnswer() {
        Assert.assertTrue(chats > 50, "only " + chats + " asks were answered by a call: the tool path was not exercised");
        Assert.assertTrue(LEAKS.isEmpty(), "tool asks that offer a text answer: " + LEAKS);
    }
}
