package forge.spike;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/** The deck file's one grammar: the JVM reads eval/tests/deck_grammar_cases.json as deck_legality.py does (critique
 *  A41: a comment or blank line anywhere sent the rest of the list to the sideboard, "4x Bolt" was dropped silently,
 *  and the legality check read the same file another way). */
public class DeckGrammarTest {

    private static List<String> rows(JsonArray a) {
        List<String> out = new ArrayList<>();
        for (JsonElement e : a) out.add(e.getAsJsonArray().get(0).getAsInt() + " " + e.getAsJsonArray().get(1).getAsString());
        return out;
    }

    @Test
    public void theSharedCases() throws Exception {
        File f = new File(PuzzleSetupTest.repoRoot(), "eval/tests/deck_grammar_cases.json");
        JsonObject root = JsonParser.parseString(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8)).getAsJsonObject();
        int n = 0;
        for (JsonElement ce : root.getAsJsonArray("cases")) {
            JsonObject c = ce.getAsJsonObject();
            String text = c.get("text").getAsString();
            List<String> lines = Arrays.asList(text.split("\n", -1));
            if (c.has("error")) {
                try {
                    LlmSpikeGui.deckEntries(lines);
                    Assert.fail("accepted: " + text);
                } catch (IllegalArgumentException e) {
                    Assert.assertTrue(e.getMessage().contains(c.get("error").getAsString()), e.getMessage());
                }
            } else {
                List<String> main = new ArrayList<>(), side = new ArrayList<>();
                for (String[] en : LlmSpikeGui.deckEntries(lines)) ("main".equals(en[0]) ? main : side).add(en[1] + " " + en[2]);
                Assert.assertEquals(main, rows(c.getAsJsonArray("main")), text);
                Assert.assertEquals(side, rows(c.getAsJsonArray("side")), text);
            }
            n++;
        }
        Assert.assertTrue(n >= 10, "cases read: " + n);
    }

    /** A name no card database holds was skipped by CardPool.add, and the deck played short. */
    @Test
    public void aCardTheEngineLacksIsRefused() throws Exception {
        new LineFindingProbeTest().initializeModel();
        File d = Files.createTempFile("deck", ".txt").toFile();
        d.deleteOnExit();
        Files.write(d.toPath(), "4 Lightning Bolt\n56 Mountian\n".getBytes(StandardCharsets.UTF_8));
        try {
            LlmSpikeGui.loadDeck("typo", d.getPath());
            Assert.fail("a misspelt card was accepted");
        } catch (java.io.IOException e) {
            Assert.assertTrue(e.getMessage().contains("Mountian"), e.getMessage());
        }
        Files.write(d.toPath(), "4 Lightning Bolt\n# lands\n56 Mountain\n".getBytes(StandardCharsets.UTF_8));
        Assert.assertEquals(LlmSpikeGui.loadDeck("ok", d.getPath()).getMain().countAll(), 60);
    }
}
