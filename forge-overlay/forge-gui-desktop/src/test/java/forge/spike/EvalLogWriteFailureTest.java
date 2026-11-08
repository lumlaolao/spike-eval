package forge.spike;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.io.Writer;

import org.testng.Assert;
import org.testng.annotations.Test;

/** A row that fails to write (a full disk) is never silent (issue #427): PrintWriter swallows the IOException, so
 *  EvalLog asks checkError() after each row, says so once on stdout and remembers it for the game line. */
public class EvalLogWriteFailureTest {
    /** A disk that is full: every write throws. */
    private static final class FullDisk extends Writer {
        @Override public void write(char[] c, int off, int len) throws IOException { throw new IOException("No space left on device"); }
        @Override public void flush() throws IOException { throw new IOException("No space left on device"); }
        @Override public void close() { }
    }

    private static String captureStdout(Runnable r) {
        PrintStream old = System.out;
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        System.setOut(new PrintStream(buf, true));
        try { r.run(); } finally { System.setOut(old); }
        return buf.toString();
    }

    @Test
    public void aFailedRowWriteIsReportedOnceAndRemembered() {
        LlmSpikeGui.EvalLog.closeForTest();
        try {
            LlmSpikeGui.EvalLog.initToWriter(new FullDisk(), "game-7-1.jsonl");
            Assert.assertFalse(LlmSpikeGui.EvalLog.writeFailed(), "nothing has failed yet");
            String out = captureStdout(() -> {
                LlmSpikeGui.EvalLog.write("{\"t\":\"decision\",\"n\":1}");
                LlmSpikeGui.EvalLog.write("{\"t\":\"decision\",\"n\":2}");
                LlmSpikeGui.EvalLog.write("{\"t\":\"decision\",\"n\":3}");
            });
            Assert.assertTrue(LlmSpikeGui.EvalLog.writeFailed(), "the failure is remembered for the game line");
            int lines = 0;
            for (String l : out.split("\n")) if (l.startsWith("[EVAL] write failed")) { lines++; Assert.assertTrue(l.contains("game-7-1.jsonl"), l); }
            Assert.assertEquals(lines, 1, "reported once, not once per row: " + out);
        } finally {
            LlmSpikeGui.EvalLog.closeForTest();
        }
    }

    @Test
    public void aHealthyLogReportsNothingAndANewLogResetsTheFlag() {
        LlmSpikeGui.EvalLog.closeForTest();
        try {
            LlmSpikeGui.EvalLog.initToWriter(new FullDisk(), "bad.jsonl");
            captureStdout(() -> LlmSpikeGui.EvalLog.write("{\"t\":\"x\"}"));
            Assert.assertTrue(LlmSpikeGui.EvalLog.writeFailed());
            LlmSpikeGui.EvalLog.closeForTest();
            LlmSpikeGui.EvalLog.initToWriter(new java.io.StringWriter(), "good.jsonl");
            Assert.assertFalse(LlmSpikeGui.EvalLog.writeFailed(), "a new log starts clean");
            String out = captureStdout(() -> LlmSpikeGui.EvalLog.write("{\"t\":\"x\"}"));
            Assert.assertFalse(out.contains("write failed"), out);
            Assert.assertFalse(LlmSpikeGui.EvalLog.writeFailed());
        } finally {
            LlmSpikeGui.EvalLog.closeForTest();
        }
    }
}
