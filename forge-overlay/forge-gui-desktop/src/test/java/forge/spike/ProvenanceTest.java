package forge.spike;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

/** The game line names the engine and the deck it played even when the launcher set nothing (critique E10). */
public class ProvenanceTest {

    @Test
    public void theEngineCommitIsReadFromTheCheckoutWhenUnset() throws Exception {
        Path co = TestTmp.dir("prov-engine");
        Path git = co.resolve(".git");
        Files.createDirectories(git.resolve("refs/heads"));
        String sha = "34a8662b7966a6ee757411feae2ae8ff38cce436";
        Files.write(git.resolve("HEAD"), "ref: refs/heads/pin\n".getBytes(StandardCharsets.UTF_8));
        Files.write(git.resolve("refs/heads/pin"), (sha + "\n").getBytes(StandardCharsets.UTF_8));
        Map<String, String> env = new HashMap<>();
        Assert.assertEquals(LlmSpikeGui.engineSha(env, co.toFile()), sha);
        Files.delete(git.resolve("refs/heads/pin"));
        Files.write(git.resolve("packed-refs"), ("# pack-refs\n" + sha + " refs/heads/pin\n").getBytes(StandardCharsets.UTF_8));
        Assert.assertEquals(LlmSpikeGui.engineSha(env, co.toFile()), sha, "a packed ref");
        Files.write(git.resolve("HEAD"), (sha + "\n").getBytes(StandardCharsets.UTF_8));
        Assert.assertEquals(LlmSpikeGui.engineSha(env, co.toFile()), sha, "a detached head");
        env.put("ENGINE_SHA", "f".repeat(40));
        Assert.assertEquals(LlmSpikeGui.engineSha(env, co.toFile()), "f".repeat(40), "the launcher's value wins");
        Assert.assertEquals(LlmSpikeGui.engineSha(new HashMap<>(), new File("/nonexistent")), "unknown");
    }

    @Test
    public void theDeckHashIsRunBatchsWhenUnset() throws Exception {
        Path deck = TestTmp.dir("prov-deck").resolve("burn.txt");
        Files.write(deck, "4 Lightning Bolt\n56 Mountain\n".getBytes(StandardCharsets.UTF_8));
        Map<String, String> env = new HashMap<>();
        env.put("DECK_A", deck.toString());
        // sha256("4 Lightning Bolt\n56 Mountain\n")[:12], as eval/run_batch.py's _deck_sha makes it
        java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
        byte[] d = md.digest(Files.readAllBytes(deck));
        StringBuilder want = new StringBuilder();
        for (int i = 0; i < 6; i++) want.append(String.format("%02x", d[i]));
        Assert.assertEquals(LlmSpikeGui.deckSha(env), want.toString());
        env.put("DECK_SHA", "pairpairpair");
        Assert.assertEquals(LlmSpikeGui.deckSha(env), "pairpairpair");
        Assert.assertEquals(LlmSpikeGui.deckSha(new HashMap<>()), "unknown");
    }
}
