import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.Properties;
import org.testng.IReporter;
import org.testng.ISuite;
import org.testng.ISuiteResult;
import org.testng.ITestContext;
import org.testng.ITestResult;
import org.testng.xml.XmlSuite;

/**
 * The grader's own TestNG reporter (eval/harness_env/harness_env_task_grade.py compiles it outside the worktree).
 * It reads a spec file named by -Dharness.grader.spec (a result path and a per-run nonce), deletes the file and the
 * property before any test class loads, and at the end of the run writes every result, with the nonce, to that path.
 * The grader reads that file and never testng-results.xml. Same-user candidate code can still look for the file.
 */
public class HarnessGraderReporter implements IReporter {
    private final String path;
    private final String nonce;

    public HarnessGraderReporter() throws IOException {
        Path spec = Paths.get(System.getProperty("harness.grader.spec"));
        Properties p = new Properties();
        p.load(Files.newBufferedReader(spec, StandardCharsets.UTF_8));
        Files.delete(spec);
        System.clearProperty("harness.grader.spec");
        path = p.getProperty("result");
        nonce = p.getProperty("nonce");
    }

    @Override
    public void generateReport(List<XmlSuite> xmlSuites, List<ISuite> suites, String outputDirectory) {
        StringBuilder rows = new StringBuilder();
        for (ISuite suite : suites) {
            for (ISuiteResult sr : suite.getResults().values()) {
                ITestContext c = sr.getTestContext();
                add(rows, "PASS", false, c.getPassedTests().getAllResults());
                add(rows, "FAIL", false, c.getFailedTests().getAllResults());
                add(rows, "FAIL", false, c.getFailedButWithinSuccessPercentageTests().getAllResults());
                add(rows, "SKIP", false, c.getSkippedTests().getAllResults());
                add(rows, "FAIL", true, c.getFailedConfigurations().getAllResults());
                add(rows, "SKIP", true, c.getSkippedConfigurations().getAllResults());
            }
        }
        String json = "{\"nonce\":" + quote(nonce) + ",\"results\":[" + rows + "]}";
        try {
            Files.write(Paths.get(path), json.getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private static void add(StringBuilder rows, String status, boolean config, Iterable<ITestResult> results) {
        for (ITestResult r : results) {
            if (rows.length() > 0) rows.append(',');
            rows.append('[').append(quote(status)).append(',').append(config).append(',')
                .append(quote(r.getTestClass().getName())).append(',')
                .append(quote(r.getMethod().getMethodName())).append(']');
        }
    }

    private static String quote(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }
}
