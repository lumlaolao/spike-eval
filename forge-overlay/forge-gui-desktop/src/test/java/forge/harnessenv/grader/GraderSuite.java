package forge.harnessenv.grader;

import java.io.BufferedWriter;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;

import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import forge.game.Game;
import forge.game.phase.PhaseType;
import forge.harnessenv.SeatFactory;

/**
 * Runs the hidden grader's instances on a list of seats and writes the report eval/harness_env_grade.py `table`
 * reads (-Dharness.envgrade.report, default /tmp/env-grade.tsv). Seats: -Dharness.envgrade.seats=name=class,...
 * (each class a harness-env-abi/1 SeatFactory); the default list is the validation set: ours, the default bridge,
 * ours without its combat or cost hooks, a text-parsing toy and a toy that hands decisions to Forge's AI.
 * -Dharness.envgrade.only=<text> runs only the instances whose id contains it.
 *
 * On the validation set it asserts what makes the grader a grader: ours beats the default bridge on every family
 * but play, each ablation loses its family, the text parser fails the no-parse check somewhere it was asked, and the
 * Forge-AI counter catches the AI delegator, including where its answers were the model's. A grader that cannot tell
 * these apart fails here. Named Suite, not Test: about fifteen minutes, so acceptance compiles it but runs it only
 * on demand (`eval/harness_env_grade.py instances`).
 */
public class GraderSuite {

    static final String VALIDATION = "ours=forge.spike.HarnessEnvOurs$Ours,default=forge.harnessenv.toys.Toys$PriorityOnly,"
            + "ours-minus-combat=forge.spike.HarnessEnvOurs$MinusCombat,ours-minus-costs=forge.spike.HarnessEnvOurs$MinusCosts,"
            + "toy-text=forge.harnessenv.toys.Toys$TextParser,toy-ai=forge.harnessenv.toys.Toys$AiDelegate";

    @BeforeClass
    public void init() { Boards.initModel(); }

    @AfterClass(alwaysRun = true)
    public void stop() { if (Grader.server != null) Grader.server.close(); }

    @Test
    public void everySeatIsGradedOnEveryInstanceAndTheValidationSeatsComeOutApart() throws Exception {
        String spec = System.getProperty("harness.envgrade.seats", VALIDATION);
        String only = System.getProperty("harness.envgrade.only");
        Map<String, SeatFactory> seats = new LinkedHashMap<>();
        for (String s : spec.split(",")) {
            String[] kv = s.split("=", 2);
            seats.put(kv[0].trim(), Grader.load(kv[1].trim()));
        }
        // The Forge-AI counter is a grader patch of PlayerControllerAi; without it every count reads 0, so say so.
        Game probe = Boards.bareGame(PhaseType.MAIN1);
        probe.getPlayers().get(1).getController().chooseStartingPlayer(true);
        boolean counting = AiCounter.patched();
        Map<String, Map<String, int[]>> passes = new TreeMap<>();
        Map<String, int[]> flags = new TreeMap<>();   // seat -> {instances where no-parse failed while asked, instances with an AI count}
        File out = new File(System.getProperty("harness.envgrade.report", "/tmp/env-grade.tsv"));
        try (BufferedWriter w = Files.newBufferedWriter(out.toPath(), StandardCharsets.UTF_8)) {
            w.write(String.join("\t", Grader.COLUMNS) + "\n");
            for (Grader.Instance in : Instances.all()) {
                if (only != null && !in.id.contains(only)) continue;
                for (Map.Entry<String, SeatFactory> s : seats.entrySet()) {
                    String row = Grader.grade(s.getKey(), s.getValue(), in);
                    if (!counting) row = row.replaceFirst("\t\\d+\t([^\t]*)$", "\t-1\t$1");
                    w.write(row + "\n");
                    w.flush();
                    String[] f = row.split("\t", -1);
                    int[] c = passes.computeIfAbsent(s.getKey(), k -> new TreeMap<>()).computeIfAbsent(in.family, k -> new int[2]);
                    c[1]++;
                    if (f[12].equals("1")) c[0]++;
                    int[] fl = flags.computeIfAbsent(s.getKey(), k -> new int[2]);
                    if (f[7].equals("1") && f[11].equals("0")) fl[0]++;
                    if (Integer.parseInt(f[13]) > 0) fl[1]++;
                }
            }
        }
        StringBuilder table = new StringBuilder("ai counter " + (counting ? "on" : "OFF (not patched)") + "\n");
        for (Map.Entry<String, Map<String, int[]>> s : passes.entrySet())
            for (Map.Entry<String, int[]> f : s.getValue().entrySet())
                table.append(s.getKey()).append(' ').append(f.getKey()).append(' ').append(f.getValue()[0]).append('/').append(f.getValue()[1]).append('\n');
        System.out.println("[ENVGRADE] report=" + out + "\n" + table);
        if (only != null || !spec.equals(VALIDATION)) return;
        Assert.assertTrue(counting, "the Forge-AI counter is not on the classpath (eval/harness_env_aicount.py)");
        Map<String, int[]> ours = passes.get("ours"), dflt = passes.get("default");
        for (Map.Entry<String, int[]> f : ours.entrySet()) {
            if (f.getKey().equals("play")) continue;   // the default bridge routes priority: it may score there
            Assert.assertTrue(f.getValue()[0] > dflt.get(f.getKey())[0], "ours does not beat the default bridge on " + f.getKey() + ":\n" + table);
        }
        Assert.assertTrue(passes.get("ours-minus-combat").get("combat")[0] < ours.get("combat")[0], "handing combat to Forge's AI cost nothing:\n" + table);
        Assert.assertTrue(passes.get("ours-minus-costs").get("costs")[0] < ours.get("costs")[0], "handing costs to Forge's AI cost nothing:\n" + table);
        Assert.assertTrue(flags.get("toy-text")[0] > 0, "the no-parse check never caught the text parser:\n" + table);
        Assert.assertTrue(flags.get("toy-ai")[1] > 0, "the Forge-AI counter never caught the AI delegator:\n" + table);
        Assert.assertEquals(flags.get("ours")[1], 0, "Forge's AI decided for ours:\n" + table);
    }
}
