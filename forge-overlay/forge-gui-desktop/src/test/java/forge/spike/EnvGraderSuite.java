package forge.spike;

import java.io.BufferedWriter;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Map;
import java.util.TreeMap;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

/**
 * Runs the hidden grader's instances (EnvInstances) on four seats and writes the report
 * eval/harness_env_grade.py `table` reads (-Dharness.envgrade.report, default /tmp/env-grade.tsv):
 * ours, the default bridge (priority to the model, everything else Forge's AI), and ours with the combat or the
 * cost hooks handed to Forge's AI. It asserts what makes the grader a grader, not what ours scores: in every family
 * where the default bridge leaves the decision to Forge's AI, ours passes more instances than it does, and each
 * ablation loses its own family. A grader that cannot tell them apart fails here.
 * -Dharness.envgrade.only=<text> runs only the instances whose id contains it.
 *
 * Named Suite, not Test: it is the environment's hidden grader, about ten minutes over 50 instances and four seats,
 * so acceptance compiles it (a broken grader fails the compile stage) but runs it only on demand
 * (`eval/harness_env_grade.py instances`).
 */
public class EnvGraderSuite {

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @Test
    public void theGraderTellsOursFromTheDefaultBridgeAndEachAblationLosesItsFamily() throws Exception {
        String only = System.getProperty("harness.envgrade.only");
        EnvGrader.SeatFactory[] seats = {EnvGrader.OURS, EnvGrader.DEFAULT, EnvGrader.OURS_MINUS_COMBAT, EnvGrader.OURS_MINUS_COSTS};
        Map<String, Map<String, int[]>> passes = new TreeMap<>();   // seat -> family -> {passed, total}
        File out = new File(System.getProperty("harness.envgrade.report", "/tmp/env-grade.tsv"));
        // Written row by row: a run cut short still leaves every instance it finished.
        try (BufferedWriter w = Files.newBufferedWriter(out.toPath(), StandardCharsets.UTF_8)) {
            w.write(String.join("\t", EnvGrader.COLUMNS) + "\n");
            for (EnvGrader.Instance in : EnvInstances.all()) {
                if (only != null && !in.id.contains(only)) continue;
                for (EnvGrader.SeatFactory s : seats) {
                    String row = EnvGrader.grade(s, in);
                    w.write(row + "\n");
                    w.flush();
                    int[] c = passes.computeIfAbsent(s.name(), k -> new TreeMap<>()).computeIfAbsent(in.family, k -> new int[2]);
                    c[1]++;
                    if (row.split("\t", -1)[12].equals("1")) c[0]++;
                }
            }
        }
        StringBuilder table = new StringBuilder();
        for (Map.Entry<String, Map<String, int[]>> s : passes.entrySet())
            for (Map.Entry<String, int[]> f : s.getValue().entrySet())
                table.append(s.getKey()).append(' ').append(f.getKey()).append(' ').append(f.getValue()[0]).append('/').append(f.getValue()[1]).append('\n');
        System.out.println("[ENVGRADE] report=" + out + "\n" + table);
        if (only != null) return;
        Map<String, int[]> ours = passes.get("ours"), dflt = passes.get("default");
        for (Map.Entry<String, int[]> f : ours.entrySet()) {
            if (f.getKey().equals("play")) continue;   // the default bridge routes priority: it may score there
            Assert.assertTrue(f.getValue()[0] > dflt.get(f.getKey())[0],
                    "the grader does not tell ours from the default bridge on " + f.getKey() + ":\n" + table);
        }
        Assert.assertTrue(passes.get("ours-minus-combat").get("combat")[0] < ours.get("combat")[0], "handing combat to Forge's AI cost nothing:\n" + table);
        Assert.assertTrue(passes.get("ours-minus-costs").get("costs")[0] < ours.get("costs")[0], "handing costs to Forge's AI cost nothing:\n" + table);
    }
}
