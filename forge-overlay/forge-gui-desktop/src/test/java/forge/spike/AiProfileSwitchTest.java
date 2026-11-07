package forge.spike;

import java.io.File;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import forge.LobbyPlayer;
import forge.ai.AiProfileUtil;
import forge.ai.AiProps;
import forge.ai.LobbyPlayerAi;
import forge.game.player.RegisteredPlayer;
import forge.localinstance.properties.ForgeConstants;

/** The per-seat Forge AI profile switch (issue #381). The default, AI_PROFILE_A/B empty, is the seat every recorded
 *  STR-AI score met: {@code new LobbyPlayerAi(name, null)} with the compiled AiProps defaults. A named profile is
 *  applied as {@code GamePlayerUtil.createAiPlayer} applies it; an unknown one, or one on a seat that is no Forge
 *  AI, is refused at start rather than falling back silently to the default. */
public class AiProfileSwitchTest {

    private static final String PROBE = "ProbeProfile";

    /** Run {@code body} with Forge's profile table holding a single profile, PROBE (MULLIGAN_THRESHOLD=9, a value no
     *  shipped profile and no compiled default has), then put the real table back. */
    private static void withProbeProfile(Runnable body) throws Exception {
        new LineFindingProbeTest().initializeModel();
        File dir = Files.createTempDirectory("ai-profiles").toFile();
        File f = new File(dir, PROBE + ".ai");
        Files.write(f.toPath(), Arrays.asList("# probe", "MULLIGAN_THRESHOLD=9"));
        try {
            AiProfileUtil.loadAllProfiles(dir.getPath());
            body.run();
        } finally {
            AiProfileUtil.loadAllProfiles(ForgeConstants.AI_PROFILE_DIR);
            f.delete();
            dir.delete();
        }
    }

    @Test
    public void anEmptyProfileBuildsTheSeatThatWasBuiltBefore() {
        for (String empty : new String[] {"", "  ", null}) {
            LobbyPlayer seat = LlmSpikeGui.seatFor("ai", "Opponent", "", "", "", empty);
            Assert.assertEquals(seat.getClass(), LobbyPlayerAi.class, "a plain Forge AI, not a subclass");
            Assert.assertEquals(((LobbyPlayerAi) seat).getAiProfile(), "", "the profile name stays empty");
            Assert.assertEquals(AiProfileUtil.getAIProp(seat, AiProps.MULLIGAN_THRESHOLD),
                    AiProps.MULLIGAN_THRESHOLD.getDefault(), "the compiled default, as before");
        }
        LobbyPlayer old = LlmSpikeGui.seatFor("ai", "Opponent", "", "", "");
        Assert.assertEquals(old.getClass(), LobbyPlayerAi.class);
        Assert.assertEquals(((LobbyPlayerAi) old).getAiProfile(), "");
        Assert.assertEquals(LlmSpikeGui.seatFor("ai-log", "Opponent", "", "", "").getClass(),
                LlmSpikeGui.LobbyPlayerAiLogging.class);
    }

    @Test
    public void aNamedProfileIsAppliedToAnAiSeat() throws Exception {
        withProbeProfile(() -> {
            LobbyPlayer ai = LlmSpikeGui.seatFor("ai", "Opponent", "", "", "", PROBE);
            Assert.assertEquals(ai.getClass(), LobbyPlayerAi.class);
            Assert.assertEquals(((LobbyPlayerAi) ai).getAiProfile(), PROBE);
            Assert.assertEquals(AiProfileUtil.getAIProp(ai, AiProps.MULLIGAN_THRESHOLD), "9");
            LobbyPlayer logging = LlmSpikeGui.seatFor("ai-log", "Opponent", "", "", "", " " + PROBE + " ");
            Assert.assertEquals(((LobbyPlayerAi) logging).getAiProfile(), PROBE, "trimmed, on the logging AI too");
            // The seat built with no profile in the same JVM still meets the compiled default.
            Assert.assertEquals(AiProfileUtil.getAIProp(LlmSpikeGui.seatFor("ai", "Other", "", "", ""),
                    AiProps.MULLIGAN_THRESHOLD), AiProps.MULLIGAN_THRESHOLD.getDefault());
        });
    }

    @Test
    public void anUnknownProfileIsRefusedAtStartNotFallenBack() throws Exception {
        withProbeProfile(() -> {
            try {
                LlmSpikeGui.seatFor("ai", "Opponent", "", "", "", "Defualt");
                Assert.fail("an unknown profile built a seat");
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains("'Defualt'") && e.getMessage().contains(PROBE), e.getMessage());
            }
        });
    }

    @Test
    public void forgesRandomEntriesAreNotProfilesAndAreRefused() {
        for (String random : new String[] {AiProfileUtil.AI_PROFILE_RANDOM_MATCH, AiProfileUtil.AI_PROFILE_RANDOM_DUEL}) {
            Assert.assertNotNull(LlmSpikeGui.aiProfileRefusal(random, Arrays.asList("Default", "Reckless")), random);
        }
        Assert.assertNull(LlmSpikeGui.aiProfileRefusal("Default", Arrays.asList("Default", "Reckless")));
        Assert.assertNull(LlmSpikeGui.aiProfileRefusal("", Arrays.asList("Default")), "empty: the compiled defaults");
        Assert.assertTrue(LlmSpikeGui.aiProfileRefusal("Default", java.util.Collections.<String>emptyList())
                .contains("none were loaded"));
    }

    @Test
    public void aProfileOnASeatThatIsNoForgeAiIsRefused() {
        for (String ctl : new String[] {"llm", "random", "floor"}) {
            try {
                LlmSpikeGui.seatFor(ctl, "Seat", "", "", "", "Default");
                Assert.fail("a profile was accepted on a " + ctl + " seat");
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains("not a Forge AI"), e.getMessage());
            }
        }
    }

    private static List<RegisteredPlayer> build(Map<String, String> env) {
        new LineFindingProbeTest().initializeModel();
        LlmSpikeGui.envOverride = env;
        try {
            return LlmSpikeGui.buildPlayers();
        } finally {
            LlmSpikeGui.envOverride = null;
        }
    }

    @Test
    public void theMatchReadsTheProfileOfEachSeatFromItsOwnVariable() throws Exception {
        withProbeProfile(() -> {
            Map<String, String> env = new HashMap<>();
            env.put("CONTROLLER_A", "ai");
            env.put("CONTROLLER_B", "ai");
            env.put("AI_PROFILE_B", PROBE);
            List<RegisteredPlayer> players = build(env);
            Assert.assertEquals(((LobbyPlayerAi) players.get(0).getPlayer()).getAiProfile(), "", "seat A: unset");
            Assert.assertEquals(((LobbyPlayerAi) players.get(1).getPlayer()).getAiProfile(), PROBE, "seat B");
            env.put("AI_PROFILE_B", "");
            Assert.assertEquals(((LobbyPlayerAi) build(env).get(1).getPlayer()).getAiProfile(), "", "empty = unset");
        });
    }

    private static <T> T withEnv(Map<String, String> env, java.util.function.Supplier<T> body) {
        LlmSpikeGui.envOverride = env;
        try {
            return body.get();
        } finally {
            LlmSpikeGui.envOverride = null;
        }
    }

    @Test
    public void thePuzzleOpponentReadsAiProfileBThroughTheSameEnv() throws Exception {
        withProbeProfile(() -> {
            Map<String, String> env = new HashMap<>();
            env.put("AI_PROFILE_A", "NotForTheOpponent");
            Assert.assertEquals(((LobbyPlayerAi) withEnv(env, () -> LlmSpikePuzzle.opponentSeat("ai", "", ""))).getAiProfile(),
                    "", "AI_PROFILE_B unset: the compiled defaults, whatever A names");
            env.put("AI_PROFILE_B", " " + PROBE + " ");
            LobbyPlayerAi opp = (LobbyPlayerAi) withEnv(env, () -> LlmSpikePuzzle.opponentSeat("ai", "", ""));
            Assert.assertEquals(opp.getAiProfile(), PROBE);
            Assert.assertEquals(AiProfileUtil.getAIProp(opp, AiProps.MULLIGAN_THRESHOLD), "9");
            env.put("AI_PROFILE_B", "Defualt");
            try {
                withEnv(env, () -> LlmSpikePuzzle.opponentSeat("ai", "", ""));
                Assert.fail("an unknown profile seated the puzzle opponent");
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains("'Defualt'"), e.getMessage());
            }
        });
    }

    @Test
    public void theGameLineStampsTheProfilesTheSeatsRead() {
        Map<String, String> env = new HashMap<>();
        Assert.assertEquals(withEnv(env, LlmSpikeGui::aiProfileStamp),
                ",\"ai_profile_a\":\"\",\"ai_profile_b\":\"\"", "unset: stamped empty");
        env.put("AI_PROFILE_A", "  Reckless ");
        env.put("AI_PROFILE_B", "Default");
        Assert.assertEquals(withEnv(env, LlmSpikeGui::aiProfileStamp),
                ",\"ai_profile_a\":\"Reckless\",\"ai_profile_b\":\"Default\"", "trimmed, per identity");
    }
}
