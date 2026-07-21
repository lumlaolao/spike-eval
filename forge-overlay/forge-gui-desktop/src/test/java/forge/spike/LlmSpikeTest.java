package forge.spike;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.common.collect.Lists;

import forge.GuiDesktop;
import forge.LobbyPlayer;
import forge.StaticData;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.deck.Deck;
import forge.deck.DeckgenUtil;
import forge.game.Game;
import forge.game.GameEndReason;
import forge.game.GameRules;
import forge.game.GameStage;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.item.IPaperCard;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.ITriggerEvent;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

/**
 * CODE SPIKE (throwaway): prove that a custom PlayerController can be injected into a
 * full Forge game via the LobbyPlayer -> Player.setFirstController seam, WITHOUT forking
 * the engine, and that the engine calls it back synchronously at real decision points.
 *
 * NO LLM here on purpose. We only prove the seam. Each overridden callback bumps a
 * counter and delegates to the built-in AI so the game still plays legally.
 */
public class LlmSpikeTest {
    private static boolean initialized = false;

    @BeforeClass
    public void initializeModel() {
        if (initialized) return;
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, preferences -> {
            preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            preferences.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
        initialized = true;
    }

    /** Counters proving our controller was actually driven by the engine. */
    static final AtomicInteger cbMulligan   = new AtomicInteger();
    static final AtomicInteger cbAbility     = new AtomicInteger();
    static final AtomicInteger cbAttackers   = new AtomicInteger();
    static final AtomicInteger cbBlockers    = new AtomicInteger();
    static int maxLegalOptionsSeen = 0;

    /** Our agent's controller: extend the AI, override a few callbacks, delegate to super. */
    static class PlayerControllerLLM extends PlayerControllerAi {
        PlayerControllerLLM(Game game, Player p, LobbyPlayer lp) { super(game, p, lp); }

        @Override
        public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
            cbMulligan.incrementAndGet();
            System.out.println("[LLM-SEAM] mulliganKeepHand called (hand decision at game start)");
            return super.mulliganKeepHand(firstPlayer, cardsToReturn);
        }

        @Override
        public SpellAbility getAbilityToPlay(Card hostCard, List<SpellAbility> abilities, ITriggerEvent triggerEvent) {
            int n = cbAbility.incrementAndGet();
            int legal = abilities == null ? 0 : abilities.size();
            if (legal > maxLegalOptionsSeen) maxLegalOptionsSeen = legal;
            if (n <= 5) {
                System.out.println("[LLM-SEAM] getAbilityToPlay #" + n
                        + " -> engine handed me " + legal + " pre-validated legal option(s)");
            }
            return super.getAbilityToPlay(hostCard, abilities, triggerEvent);
        }

        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            cbAttackers.incrementAndGet();
            System.out.println("[LLM-SEAM] declareAttackers called (combat decision)");
            super.declareAttackers(attacker, combat);
        }

        @Override
        public void declareBlockers(Player defender, Combat combat) {
            cbBlockers.incrementAndGet();
            System.out.println("[LLM-SEAM] declareBlockers called (combat decision)");
            super.declareBlockers(defender, combat);
        }
    }

    /** LobbyPlayer whose in-game Player uses OUR controller instead of PlayerControllerAi. */
    static class LobbyPlayerLLM extends LobbyPlayerAi {
        LobbyPlayerLLM(String name) { super(name, null); }

        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player p = new Player(getName(), game, id);
            p.setFirstController(new PlayerControllerLLM(game, p, this));
            return p;
        }
    }

    @Test(timeOut = 180000)
    public void injectsCustomControllerIntoFullGame() throws Exception {
        // Two random constructed decks (one call each), same generator the AI uses.
        Deck deckLLM = DeckgenUtil.getRandomColorDeck(true);
        Deck deckAI  = DeckgenUtil.getRandomColorDeck(true);

        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(new RegisteredPlayer(deckLLM).setPlayer(new LobbyPlayerLLM("LLM-Agent")));   // seat 0: our controller
        players.add(new RegisteredPlayer(deckAI).setPlayer(new LobbyPlayerAi("Forge-AI", null))); // seat 1: built-in AI

        GameRules rules = new GameRules(GameType.Constructed);
        Match match = new Match(rules, players, "LlmSpike");
        final Game game = match.createGame();

        // Run the full game on a worker thread with a hard cap so a durdle can't hang the test.
        Thread worker = new Thread(() -> {
            try {
                match.startGame(game);
            } catch (Throwable t) {
                System.out.println("[LLM-SEAM] game thread threw: " + t);
            }
        }, "forge-game");
        worker.start();
        worker.join(150000);
        if (worker.isAlive()) {
            System.out.println("[LLM-SEAM] game exceeded cap; ending as draw");
            game.setGameOver(GameEndReason.Draw);
            worker.join(10000);
        }

        System.out.println("========== SPIKE RESULT ==========");
        System.out.println("mulliganKeepHand calls : " + cbMulligan.get());
        System.out.println("getAbilityToPlay calls : " + cbAbility.get());
        System.out.println("declareAttackers calls : " + cbAttackers.get());
        System.out.println("declareBlockers calls  : " + cbBlockers.get());
        System.out.println("max legal options in one getAbilityToPlay : " + maxLegalOptionsSeen);
        System.out.println("game over? " + game.isGameOver()
                + "  outcome: " + (game.getOutcome() == null ? "n/a" : game.getOutcome().toString()));
        System.out.println("==================================");

        // The proof: our injected controller was actually driven by the engine.
        int total = cbMulligan.get() + cbAbility.get() + cbAttackers.get() + cbBlockers.get();
        Assert.assertTrue(total > 0,
                "Custom PlayerController was never called -> injection seam does NOT work");
    }

    // ================================================================================
    // Parser hardening (HARNESS-ANALYSIS.md Infra-4): parseTagNumber/lastSegment must survive
    // markdown wrapping, missing/alternate separators, an "Answer:" synonym, trailing prose, and
    // PLAN co-mingling, while still preserving last-occurrence-wins semantics. These call the real
    // package-private static helpers on forge.spike.LlmSpikeGui.PlayerControllerLLM directly — no
    // bridge/instance needed, since the parsers are pure string functions.
    // ================================================================================

    @Test
    public void parseTagNumber_markdownBold() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber("**CHOICE: 3**", "CHOICE:"), 3);
    }

    @Test
    public void parseTagNumber_codeFence() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber("```\nCHOICE: 4\n```", "CHOICE:"), 4);
    }

    @Test
    public void parseTagNumber_inlineCodeSpan() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber("My pick: `CHOICE: 5`", "CHOICE:"), 5);
    }

    @Test
    public void parseTagNumber_dashSeparator() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber("CHOICE - 5", "CHOICE:"), 5);
    }

    @Test
    public void parseTagNumber_noSeparator() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber("CHOICE 6", "CHOICE:"), 6);
    }

    @Test
    public void parseTagNumber_answerSynonym() {
        String reply = "I think the aggressive line is best here.\nAnswer: 7";
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber(reply, "CHOICE:"), 7);
    }

    @Test
    public void parseTagNumber_answerSynonymRequiresColon() {
        // "answer" as an ordinary English word (no colon) must NOT be mistaken for the ANSWER: tag —
        // only the literal CHOICE: line should be matched here.
        String reply = "CHOICE: 8\nI am confident in this answer overall.";
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber(reply, "CHOICE:"), 8);
    }

    @Test
    public void parseTagNumber_trailingProseAfterNumber() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber("CHOICE: 8 (this is my final answer)", "CHOICE:"), 8);
    }

    @Test
    public void parseTagNumber_planComingledWithChoice() {
        // A numeral inside a co-mingled PLAN line ("play 2 lands") must not be picked up instead of
        // the real CHOICE tag that follows it.
        String reply = "PLAN: play 2 lands then attack with everything.\nCHOICE: 9";
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber(reply, "CHOICE:"), 9);
    }

    @Test
    public void parseTagNumber_degenerateRepetitionLastWins() {
        StringBuilder reply = new StringBuilder();
        for (int i = 0; i < 157; i++) reply.append("CHOICE: 1\n");
        reply.append("CHOICE: 2");
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber(reply.toString(), "CHOICE:"), 2,
                "last occurrence must win even after 157 identical repeats (QF-1 degenerate-generation case)");
    }

    @Test
    public void parseTagNumber_lastOccurrenceWinsAcrossSynonyms() {
        // CHOICE: appears first, the model's actual final line uses the ANSWER: synonym instead —
        // the LATER line (by position in the reply), not the primary tag, must win.
        String reply = "CHOICE: 2\nOn reflection, Answer: 5";
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber(reply, "CHOICE:"), 5);
    }

    @Test
    public void parseTagNumber_unparseableStillReturnsMinusOne() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber("I don't know what to do here.", "CHOICE:"), -1);
    }

    @Test
    public void picksSegment_markdownAndFlexibleSeparatorVariants() {
        // Mirrors decideMany's own extraction (Pattern "\\d+" over the PICKS: segment) against inputs
        // hardened per Infra-4: markdown-wrapped, dash separator, and no separator at all.
        Assert.assertEquals(extractPicks("**PICKS: 1, 3, 4**"), List.of(1, 3, 4));
        Assert.assertEquals(extractPicks("PICKS - 2,5"), List.of(2, 5));
        Assert.assertEquals(extractPicks("Reasoning first.\nPICKS 1 2 3"), List.of(1, 2, 3));
    }

    @Test
    public void blocksSegment_markdownWrapped() {
        String reply = "**BLOCKS: Wall of Omens->Goblin Guide**";
        String line = LlmSpikeGui.PlayerControllerLLM.lastSegment(reply, "BLOCKS:");
        Assert.assertNotNull(line);
        Assert.assertTrue(line.trim().startsWith("Wall of Omens->Goblin Guide"),
                "markdown wrapping must not defeat the BLOCKS: tag match, got: " + line);
    }

    // Observability (HARNESS-ANALYSIS.md C5/D3/C6/C7; proposal Infra 6): cap() is the helper backing
    // jsonl's new additive first_failed_reply field — it must truncate long diagnostic snippets to
    // a bounded size without throwing on null/short input, since it runs on the "first" of possibly
    // several dead replies whose length is otherwise unbounded.

    @Test
    public void cap_truncatesLongStringToExactLength() {
        String s = "x".repeat(600);
        String capped = LlmSpikeGui.PlayerControllerLLM.cap(s, 500);
        Assert.assertEquals(capped.length(), 500);
        Assert.assertEquals(capped, "x".repeat(500));
    }

    @Test
    public void cap_leavesShortOrNullStringUnchanged() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.cap("short reply", 500), "short reply");
        Assert.assertNull(LlmSpikeGui.PlayerControllerLLM.cap(null, 500));
        // exactly-at-the-limit must NOT be truncated (off-by-one guard)
        String exact = "y".repeat(500);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.cap(exact, 500), exact);
    }

    /** Test-local replica of decideMany's PICKS number extraction, exercised against the same
     *  hardened lastSegment() the production path uses. */
    private static List<Integer> extractPicks(String reply) {
        String seg = LlmSpikeGui.PlayerControllerLLM.lastSegment(reply, "PICKS:");
        Assert.assertNotNull(seg, "PICKS: tag not found in: " + reply);
        List<Integer> out = Lists.newArrayList();
        Matcher m = Pattern.compile("\\d+").matcher(seg);
        while (m.find()) out.add(Integer.parseInt(m.group()));
        return out;
    }

    // ================================================================================
    // Truncation hardening (HARNESS-ANALYSIS.md C4; proposal Prompt 5): truncateWords() backs both
    // oneLine() and saLabel()'s option-text cap — it must cut at a word boundary and append an
    // ellipsis instead of severing mid-word/mid-number, and must be a no-op when the input already fits.
    // ================================================================================

    @Test
    public void truncateWords_cutsAtWordBoundaryWithEllipsis() {
        String s = "Target creature gets +4/+4 until end of turn if it's your main phase, otherwise +2/+2.";
        String out = LlmSpikeGui.truncateWords(s, 30);
        Assert.assertTrue(out.endsWith("..."), "must append an ellipsis, got: " + out);
        Assert.assertTrue(out.length() <= 33, "must respect the cap (+ellipsis), got: " + out);
        // must not sever mid-word: strip the ellipsis and confirm what remains is whole words
        String body = out.substring(0, out.length() - 3);
        Assert.assertFalse(body.endsWith(" "), "should trim trailing space before the ellipsis: " + out);
        Assert.assertTrue(s.startsWith(body), "truncated body must be a clean word-boundary prefix of the original: " + out);
    }

    @Test
    public void truncateWords_leavesShortStringUnchanged() {
        Assert.assertEquals(LlmSpikeGui.truncateWords("short text", 90), "short text");
        Assert.assertNull(LlmSpikeGui.truncateWords(null, 90));
        // exactly-at-the-limit must NOT be truncated (off-by-one guard)
        String exact = "y".repeat(90);
        Assert.assertEquals(LlmSpikeGui.truncateWords(exact, 90), exact);
    }

    // ================================================================================
    // Prompt/game-state additions (HARNESS-ANALYSIS.md A1; proposals Prompt 1-2, Tools 3):
    // candidateLabel()/landSummary() must make duplicate-named permanents distinguishable by tap
    // state — the QF-3 misfire class ("the untapped Volcanic Island" among 3 identically-named,
    // unlabeled options) must become structurally impossible. These build a REAL Game/Player/Card
    // via the same construction Forge's own AI unit tests use (forge.ai.AITest), so the assertions
    // exercise the production isTapped()/isInPlay()/getType() calls, not a stub.
    // ================================================================================

    private Game buildBareGame() {
        List<RegisteredPlayer> players = Lists.newArrayList();
        Deck d1 = new Deck();
        players.add(new RegisteredPlayer(d1).setPlayer(new LobbyPlayerAi("p1", null)));
        players.add(new RegisteredPlayer(d1).setPlayer(new LobbyPlayerAi("p2", null)));
        GameRules rules = new GameRules(GameType.Constructed);
        Match match = new Match(rules, players, "LlmSpikeTest-candidateLabel");
        Game game = new Game(players, rules, match);
        Player p = game.getPlayers().get(0);
        game.setAge(GameStage.Play);
        game.getPhaseHandler().devModeSet(PhaseType.MAIN1, p);
        return game;
    }

    /** Puts a fresh Card of `name` onto `p`'s battlefield (untapped) and returns it — mirrors
     *  forge.ai.AITest#addCard, reimplemented locally so this test file stays self-contained. */
    private Card addBattlefieldCard(String name, Player p) {
        IPaperCard paperCard = FModel.getMagicDb().getCommonCards().getCard(name);
        if (paperCard == null) {
            StaticData.instance().attemptToLoadCard(name);
            paperCard = FModel.getMagicDb().getCommonCards().getCard(name);
        }
        Card c = Card.fromPaperCard(paperCard, p);
        c.setGameTimestamp(p.getGame().getNextTimestamp());
        p.getZone(ZoneType.Battlefield).add(c);
        return c;
    }

    @Test
    public void candidateLabel_disambiguatesDuplicateNamedPermanentsByTapState() {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        Card untapped = addBattlefieldCard("Volcanic Island", p);
        Card tapped = addBattlefieldCard("Volcanic Island", p);
        tapped.tap(false, null, null);

        String untappedLabel = LlmSpikeGui.PlayerControllerLLM.candidateLabel(untapped);
        String tappedLabel = LlmSpikeGui.PlayerControllerLLM.candidateLabel(tapped);
        Assert.assertTrue(untappedLabel.contains("Volcanic Island"), untappedLabel);
        Assert.assertTrue(untappedLabel.contains("[untapped]"), untappedLabel);
        Assert.assertTrue(tappedLabel.contains("[tapped]"), tappedLabel);
        Assert.assertNotEquals(untappedLabel, tappedLabel,
                "two identically-named permanents in different tap states must never render as the same "
                + "menu option string (the QF-3 Rishadan Port misfire class)");
    }

    @Test
    public void landSummary_groupsPerCopyByNameAndTapState() {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        addBattlefieldCard("Volcanic Island", p);
        Card tappedIsland = addBattlefieldCard("Volcanic Island", p);
        tappedIsland.tap(false, null, null);
        addBattlefieldCard("Wasteland", p);

        String summary = LlmSpikeGui.PlayerControllerLLM.landSummary(p);
        Assert.assertTrue(summary.startsWith("2 untapped / 3 total"), summary);
        Assert.assertTrue(summary.contains("Volcanic Island x1 [untapped]"), summary);
        Assert.assertTrue(summary.contains("Volcanic Island x1 [tapped]"), summary);
        Assert.assertTrue(summary.contains("Wasteland x1 [untapped]"), summary);
    }

    // ================================================================================
    // Group 4 (Mechanics routing) pure-helper coverage.
    // ================================================================================

    // namedIndices() backs the intent-aware bridge-down fallback (HARNESS-ANALYSIS.md C3): when the
    // bridge dies mid-decision, prefer whatever the model's OWN stated turn plan already named over a
    // blind default (attackers all-in / first-candidate targeting).
    @Test
    public void namedIndices_matchesPlanNamedCandidateCaseInsensitively() {
        List<String> cands = Lists.newArrayList("Blighted Agent", "Noble Hierarch");
        List<Integer> hit = LlmSpikeGui.PlayerControllerLLM.namedIndices(cands,
                "attack with blighted agent alone, Hierarch is unnecessary");
        Assert.assertEquals(hit, Lists.newArrayList(0),
                "plan names only the first candidate (case-insensitively) -> only index 0 should match");
    }

    @Test
    public void namedIndices_matchesMultipleNamedCandidatesInMenuOrder() {
        List<String> cands = Lists.newArrayList("Tabernacle", "Tranquil Thicket", "Grove of the Burnwillows");
        List<Integer> hit = LlmSpikeGui.PlayerControllerLLM.namedIndices(cands,
                "PLAN: Life from the Loam back Tabernacle, Grove of the Burnwillows, and Tranquil Thicket");
        Assert.assertEquals(hit, Lists.newArrayList(0, 1, 2),
                "all three plan-named lands should match, in MENU order (not plan-mention order)");
    }

    @Test
    public void namedIndices_emptyWhenNoPlanYet() {
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.namedIndices(Lists.newArrayList("Anything"), "").isEmpty());
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.namedIndices(Lists.newArrayList("Anything"), null).isEmpty());
    }

    @Test
    public void namedIndices_emptyWhenPlanNamesNothingOnOffer() {
        List<Integer> hit = LlmSpikeGui.PlayerControllerLLM.namedIndices(
                Lists.newArrayList("Blighted Agent"), "PLAN: durdle around and pass");
        Assert.assertTrue(hit.isEmpty());
    }

    // trampleSplit() backs assignCombatDamage's single-blocker/trample menu (HARNESS-ANALYSIS.md C1):
    // routing this decision through the LLM pipeline instead of Forge's built-in AI still needs
    // correct lethal-vs-overflow math for the two menu options it offers.
    @Test
    public void trampleSplit_overkillYieldsTrampleOverflow() {
        // 6 power trampler vs. a 2-toughness blocker: 2 lethal, 4 tramples over.
        int[] split = LlmSpikeGui.PlayerControllerLLM.trampleSplit(6, 2);
        Assert.assertEquals(split[0], 2, "lethal amount");
        Assert.assertEquals(split[1], 4, "trample overflow");
    }

    @Test
    public void trampleSplit_cannotKillYieldsNoOverflow() {
        // killDamage > damageDealt (e.g. indestructible blocker, or just not enough damage) -> every
        // point must go to the blocker; nothing legally tramples over.
        int[] split = LlmSpikeGui.PlayerControllerLLM.trampleSplit(3, 99);
        Assert.assertEquals(split[0], 3, "all damage stays on the blocker");
        Assert.assertEquals(split[1], 0, "no legal trample overflow when the blocker can't be killed");
    }

    @Test
    public void trampleSplit_exactLethalYieldsNoOverflow() {
        int[] split = LlmSpikeGui.PlayerControllerLLM.trampleSplit(4, 4);
        Assert.assertEquals(split[0], 4);
        Assert.assertEquals(split[1], 0, "exactly lethal, nothing left to trample over");
    }
}
