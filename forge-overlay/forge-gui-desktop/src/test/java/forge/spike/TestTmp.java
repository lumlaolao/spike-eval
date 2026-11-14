package forge.spike;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;

/**
 * The tests' temp files and directories, all removed with what is in them when the test JVM exits. A directory's
 * {@code deleteOnExit} fails while it holds a file, so a test that wrote into one left it behind: every test takes its
 * temp paths from here instead. Acceptance's javatests stage fails, naming them, if anything is left in the JVM's
 * {@code java.io.tmpdir} when the tests end.
 */
final class TestTmp {
    private static final List<Path> MADE = new ArrayList<>();

    static {
        Runtime.getRuntime().addShutdownHook(new Thread(TestTmp::removeAll, "test-tmp-cleanup"));
    }

    private TestTmp() { }

    /** A new temp directory, removed with its contents at exit. */
    static Path dir(String prefix) throws IOException {
        return made(Files.createTempDirectory(prefix));
    }

    /** A new empty temp file, removed at exit. */
    static Path file(String prefix, String suffix) throws IOException {
        return made(Files.createTempFile(prefix, suffix));
    }

    private static synchronized Path made(Path p) {
        MADE.add(p);
        return p;
    }

    private static synchronized void removeAll() {
        for (Path p : MADE) {
            if (!Files.exists(p)) continue;
            try (Stream<Path> walk = Files.walk(p)) {
                walk.sorted(Comparator.reverseOrder()).forEach(q -> q.toFile().delete());
            } catch (IOException | RuntimeException ignore) {
                // best effort at exit: the stage's guard names whatever is left
            }
        }
    }
}
