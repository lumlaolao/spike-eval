package forge.spike;

import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.common.collect.Lists;

import org.apache.commons.lang3.tuple.ImmutablePair;

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
import forge.game.card.CardCollection;
import forge.game.card.CardCollectionView;
import forge.game.combat.Combat;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.PlayerActionConfirmMode;
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

    // ---- disambiguate(): repeated menu labels get " #N" ordinals; unique menus pass through ----

    @Test
    public void disambiguate_noDuplicates_returnsSameListUnchanged() {
        List<String> in = Lists.newArrayList("CAST Bolt", "PLAY LAND Mountain", "PASS (do nothing)");
        // The contract is "UNCHANGED unless a label repeats" — same list identity, no copy, so
        // the no-dup fast path can never perturb byte-identical teacher/student menus.
        Assert.assertSame(LlmSpikeGui.PlayerControllerLLM.disambiguate(in), in);
    }

    @Test
    public void disambiguate_repeatsGetOrdinals_uniqueEntriesUntouched() {
        List<String> out = LlmSpikeGui.PlayerControllerLLM.disambiguate(Lists.newArrayList(
                "CAST Wasteland [{T}, Sacrifice CARDNAME: ...]",
                "CAST Wasteland [{T}, Sacrifice CARDNAME: ...]",
                "PASS (do nothing)"));
        Assert.assertEquals(out, Lists.newArrayList(
                "CAST Wasteland [{T}, Sacrifice CARDNAME: ...] #1",
                "CAST Wasteland [{T}, Sacrifice CARDNAME: ...] #2",
                "PASS (do nothing)"));
    }

    @Test
    public void disambiguate_multipleGroupsAndTriples_occurrenceOrder() {
        List<String> out = LlmSpikeGui.PlayerControllerLLM.disambiguate(
                Lists.newArrayList("A", "B", "A", "C", "A", "B"));
        Assert.assertEquals(out, Lists.newArrayList("A #1", "B #1", "A #2", "C", "A #3", "B #2"));
    }

    @Test
    public void disambiguate_idempotent() {
        // Applied at both prompt builders AND log choke points (evalDec/emit), so double
        // application on an already-suffixed list must be a no-op.
        List<String> once = LlmSpikeGui.PlayerControllerLLM.disambiguate(Lists.newArrayList(
                "Griselbrand (7/7)", "Griselbrand (7/7)", "Unmask", "Unmask"));
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.disambiguate(once), once);
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
    // Fix round A (bridge internals, HARNESS-ANALYSIS.md F2 + the truncation-retry ordering bug
    // documented in ModelCfg's class javadoc): isNonAnswer/looksLikeDecisionTag/
    // collapseDegenerateRepetition are the pure predicates OpenRouterBridge.ask() now consults, in
    // order, to decide truncation-retry vs. bridge-down vs. degenerate-reply collapse. They're plain
    // static string functions, so exercised directly here rather than through a live HTTP bridge.
    // ================================================================================

    @Test
    public void extractJsonString_compactAndSpacedJsonBothParse() {
        // OpenRouter emits compact JSON ("content":"..."), OpenAI-compatible local servers
        // (mlx_lm.server / Python json.dumps) emit spaced JSON ("content": "..."). The old
        // exact-substring match missed the spaced form entirely -> content=null -> every local-server
        // reply mislabeled a non-answer (2026-07-22 stage-1 student eval: 30 games, 0 model calls).
        String compact = "{\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"CHOICE: 2\"},\"finish_reason\":\"stop\"}]}";
        String spaced = "{\"choices\": [{\"message\": {\"role\": \"assistant\", \"content\": \"CHOICE: 2\"}, \"finish_reason\": \"stop\"}]}";
        Assert.assertEquals(LlmSpikeGui.extractJsonString(compact, "\"content\":\""), "CHOICE: 2");
        Assert.assertEquals(LlmSpikeGui.extractJsonString(spaced, "\"content\":\""), "CHOICE: 2");
        Assert.assertEquals(LlmSpikeGui.extractJsonString(spaced, "\"finish_reason\":\""), "stop");
        // A non-string value (reasoning-only reply with "content": null) must return null -> the
        // honest bridge-down path, not the string "null".
        String nullContent = "{\"choices\": [{\"message\": {\"role\": \"assistant\", \"content\": null}}]}";
        Assert.assertNull(LlmSpikeGui.extractJsonString(nullContent, "\"content\":\""));
        Assert.assertNull(LlmSpikeGui.extractJsonString(compact, "\"absent_key\":\""));
    }

    // ================================================================================
    // Resolved model identity (2026-07-26). The configured spec is not an identity: an alias like
    // "openrouter:default_model" is resolved server-side, so runs/stage1-student-eval and
    // runs/stage2-student-eval carry identical provenance despite having been played by different
    // adapters. extractServedModel reads what the endpoint says answered, for the game line.
    // ================================================================================

    @Test
    public void extractServedModel_readsTopLevelModelField() {
        String compact = "{\"id\":\"chatcmpl-1\",\"object\":\"chat.completion\",\"created\":1,"
                + "\"model\":\"student-stage2\",\"choices\":[{\"message\":{\"content\":\"CHOICE: 2\"}}]}";
        String spaced = "{\"id\": \"chatcmpl-1\", \"model\": \"mlx-community/adapter-v2\", "
                + "\"choices\": [{\"message\": {\"content\": \"CHOICE: 2\"}}]}";
        Assert.assertEquals(LlmSpikeGui.extractServedModel(compact), "student-stage2");
        Assert.assertEquals(LlmSpikeGui.extractServedModel(spaced), "mlx-community/adapter-v2");
    }

    @Test
    public void extractServedModel_completionTextCannotMasqueradeAsTheModelId() {
        // The failure this guard exists for: a reply that happens to contain the literal
        // "model":" would otherwise be read as the served model and mislabel the entire run,
        // silently, in the field that decides which checkpoint owns the win rate. Only the body
        // BEFORE "choices" is searched, so the completion text can never win.
        String noModelField = "{\"id\":\"chatcmpl-1\",\"choices\":[{\"message\":{\"content\":"
                + "\"I would set \\\"model\\\":\\\"gpt-9\\\" here\"}}]}";
        Assert.assertNull(LlmSpikeGui.extractServedModel(noModelField));

        String realFieldWins = "{\"model\":\"real-checkpoint\",\"choices\":[{\"message\":{\"content\":"
                + "\"I would set \\\"model\\\":\\\"gpt-9\\\" here\"}}]}";
        Assert.assertEquals(LlmSpikeGui.extractServedModel(realFieldWins), "real-checkpoint");
    }

    @Test
    public void extractServedModel_absentOrEmptyYieldsNullNotAGuess() {
        // null -> the game line records "unknown". An endpoint that names nothing must leave the
        // run honestly unattributed rather than inheriting the alias we asked for.
        Assert.assertNull(LlmSpikeGui.extractServedModel(null));
        Assert.assertNull(LlmSpikeGui.extractServedModel("{\"choices\":[]}"));
        Assert.assertNull(LlmSpikeGui.extractServedModel("{\"model\":\"\",\"choices\":[]}"));
        Assert.assertNull(LlmSpikeGui.extractServedModel("not json at all"));
    }

    @Test
    public void resolvedTemperature_distinguishesUnsetFromZero() {
        // "default" (field omitted from the request body) and an explicit 0.0 are different
        // configurations; collapsing them would make two different runs look identical.
        Assert.assertEquals(LlmSpikeGui.resolvedTemperature("haiku"), "n/a");
        Assert.assertEquals(LlmSpikeGui.resolvedTemperature(null), "n/a");
        String t = LlmSpikeGui.resolvedTemperature("openrouter:some/unknown-model");
        Assert.assertTrue(t.equals("default") || t.matches("-?\\d+(\\.\\d+)?"),
                "expected 'default' or a number, got: " + t);
    }

    /**
     * REPLACES modelCfgSha_isBuiltinWhenNoOverrideIsSet (2026-09-01), which pinned the literal
     * "builtin" for every run without an OPENROUTER_MODEL_CFG override -- i.e. for essentially every
     * artifact on disk. That made the field useless as provenance: the built-in per-model timeouts
     * and token caps were never hashed, so changing one moved no sha and left every prior run
     * indistinguishable from every later one. The old test was not wrong about the code; it faithfully
     * pinned a contract that shouldn't have existed. modelCfgSha() now hashes the EFFECTIVE table.
     * See ContextProfileTest for the full-coverage assertions.
     */
    @Test
    public void modelCfgSha_hashesTheEffectiveTableNotJustTheOverride() {
        String sha = LlmSpikeGui.modelCfgSha();
        Assert.assertNotEquals(sha, "builtin",
                "the built-in table must be hashed, not skipped with a constant");
        Assert.assertTrue(sha.matches("[0-9a-f]{12}"), "expected 12 hex chars, got: " + sha);
    }

    @Test
    public void isNonAnswer_emptyOrNullIsNonAnswer() {
        // The exact content shape a fully-length-truncated reply has BEFORE any output token ever
        // came out (see OpenRouterBridge.ask(): this must stay true so the final bridge-down
        // classification still fires once truncation-retry has had its shot).
        Assert.assertTrue(LlmSpikeGui.isNonAnswer(null));
        Assert.assertTrue(LlmSpikeGui.isNonAnswer(""));
    }

    @Test
    public void isNonAnswer_knownInfraMessagesAreNonAnswers() {
        Assert.assertTrue(LlmSpikeGui.isNonAnswer("You've hit your session limit, try again later."));
        Assert.assertTrue(LlmSpikeGui.isNonAnswer("429 rate limit exceeded"));
        Assert.assertTrue(LlmSpikeGui.isNonAnswer("Unable to locate a Java Runtime"));
    }

    @Test
    public void isNonAnswer_realDecisionTextIsNotANonAnswer() {
        Assert.assertFalse(LlmSpikeGui.isNonAnswer("I will pass priority.\nCHOICE: 2"));
    }

    @Test
    public void looksLikeDecisionTag_completeChoiceTagIsRecognized() {
        Assert.assertTrue(LlmSpikeGui.looksLikeDecisionTag("Reasoning here.\nCHOICE: 3"));
        Assert.assertTrue(LlmSpikeGui.looksLikeDecisionTag("PICKS: 1, 4"));
        Assert.assertTrue(LlmSpikeGui.looksLikeDecisionTag("BLOCKS: Wall of Omens->Goblin Guide 2"));
    }

    @Test
    public void looksLikeDecisionTag_truncatedTagWithNoDigitIsNotRecognized() {
        // PARSE RE-ASK INVESTIGATION (runs/improve/iter-2, game-112 decision idx 124, model
        // gemini-3.5-flash): the model's first attempt was cut to exactly this text -- the tag
        // keyword made it out but got truncated before the number. The old bare-substring check
        // (`u.contains("CHOICE")`) treated this as "already answered" and skipped the doubled-budget
        // truncation retry that exists for precisely this case.
        String truncated = "We must pass priority to the blockers step so we can block the opponent's "
                + "attacking Voice of Victory with our Voice of Victory, preserving our last point of "
                + "life.\n\nCHOICE: ";
        Assert.assertFalse(LlmSpikeGui.looksLikeDecisionTag(truncated),
                "a CHOICE: tag with no digit after it is a truncated non-answer, not a complete decision");
    }

    @Test
    public void looksLikeDecisionTag_plainProseWithNoTagAtAllIsNotRecognized() {
        // The other 7/8 iter-2 re-asks: the model answered entirely in prose and never emitted any
        // tag keyword at all (a genuine model non-answer on the terse-format contract, not a parser
        // gap -- the controller's stern one-line re-ask prompt is the correct, already-working fix).
        // (game-112 decision idx 105, model gemini-3.5-flash's first attempt, verbatim.)
        String prose = "To survive the combat damage step at 3 life while facing attacks from Voice of "
                + "Victory (1 power) and its mobilised Warrior tokens (1 power each), we must use Goblin";
        Assert.assertFalse(LlmSpikeGui.looksLikeDecisionTag(prose));
    }

    @Test
    public void looksLikeDecisionTag_keepAndMulliganNeedNoDigit() {
        Assert.assertTrue(LlmSpikeGui.looksLikeDecisionTag("I'll KEEP this hand."));
        Assert.assertTrue(LlmSpikeGui.looksLikeDecisionTag("MULLIGAN, too risky."));
    }

    @Test
    public void collapseDegenerateRepetition_collapsesQF1StyleRepeatedChoiceLines() {
        // Reproduces tournament QF-1 decision_line 87: a fast-tier gemini reply that degenerated into
        // 157 consecutive repetitions of "CHOICE: 1" (2390 chars, HARNESS-ANALYSIS.md F2).
        StringBuilder reply = new StringBuilder();
        for (int i = 0; i < 157; i++) reply.append("CHOICE: 1\n");
        String collapsed = LlmSpikeGui.collapseDegenerateRepetition(reply.toString());
        long lineCount = collapsed.chars().filter(c -> c == '\n').count() + 1;
        Assert.assertTrue(collapsed.length() < reply.length(),
                "157x repeated line must be collapsed down, got: " + collapsed.length() + " chars");
        Assert.assertTrue(lineCount <= 2, "collapse must leave at most one occurrence (+trailing blank line), got "
                + lineCount + " lines: " + collapsed);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.parseTagNumber(collapsed, "CHOICE:"), 1,
                "the single surviving occurrence must still parse correctly");
    }

    @Test
    public void collapseDegenerateRepetition_tenOrFewerRepeatsIsUntouched() {
        // Boundary: "more than 10" consecutive repeats is the trigger -- exactly 10 must be a no-op,
        // per the task's own threshold ("repeated more than 10 times consecutively").
        StringBuilder reply = new StringBuilder();
        for (int i = 0; i < 10; i++) reply.append("CHOICE: 1\n");
        String in = reply.toString();
        Assert.assertEquals(LlmSpikeGui.collapseDegenerateRepetition(in), in,
                "exactly 10 repeats must NOT be collapsed (only strictly more than 10 triggers it)");
    }

    @Test
    public void collapseDegenerateRepetition_longLinesAreNeverCollapsedEvenIfRepeated() {
        // The <=20-char guard: a long repeated line (e.g. identical multi-sentence reasoning restated
        // several times, which is a real pattern but NOT the QF-1 degenerate-generation defect) must
        // be left alone -- only short, tag-shaped lines are in scope for this cheap guard.
        String longLine = "This is a long line of reasoning that exceeds twenty characters easily.";
        StringBuilder reply = new StringBuilder();
        for (int i = 0; i < 20; i++) reply.append(longLine).append('\n');
        String in = reply.toString();
        Assert.assertEquals(LlmSpikeGui.collapseDegenerateRepetition(in), in,
                "lines longer than 20 chars must never be collapsed, regardless of repeat count");
    }

    @Test
    public void collapseDegenerateRepetition_normalReplyIsUnchanged() {
        String normal = "I will hold up removal for their next threat.\nCHOICE: 2";
        Assert.assertEquals(LlmSpikeGui.collapseDegenerateRepetition(normal), normal);
        Assert.assertNull(LlmSpikeGui.collapseDegenerateRepetition(null));
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

    /** Puts a fresh Card of `name` into `p`'s hand and returns it — the reveal-from-hand analogue of
     *  {@link #addBattlefieldCard}, used by the chooseCardsToRevealFromHand routing tests. */
    private Card addHandCard(String name, Player p) {
        IPaperCard paperCard = FModel.getMagicDb().getCommonCards().getCard(name);
        if (paperCard == null) {
            StaticData.instance().attemptToLoadCard(name);
            paperCard = FModel.getMagicDb().getCommonCards().getCard(name);
        }
        Card c = Card.fromPaperCard(paperCard, p);
        c.setGameTimestamp(p.getGame().getNextTimestamp());
        p.getZone(ZoneType.Hand).add(c);
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
        // The "@N" suffix is the persistent object id added 2026-08-02 (commit 17267cb9) so the
        // model can tell two same-named permanents apart and track them across turns. This
        // assertion still read "[untapped]" for a month afterwards: `make jtest` joined its class
        // names with '+', which surefire matches against nothing, so the whole Java suite silently
        // never ran. Match on the id PREFIX rather than a literal id — the ids are engine-assigned
        // and pinning them here would couple this test to allocation order for no benefit.
        Assert.assertTrue(summary.contains("Volcanic Island x1 [untapped @"), summary);
        Assert.assertTrue(summary.contains("Volcanic Island x1 [tapped @"), summary);
        Assert.assertTrue(summary.contains("Wasteland x1 [untapped @"), summary);
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

    // ================================================================================
    // Multi-blocker combat damage (HARNESS-ANALYSIS.md/iter-1 C1 follow-up): assignCombatDamage's
    // "[AI-EXOTIC-DAMAGE]" escape hatch for 2+ blockers has been deleted — it handed even routine
    // double-blocks (e.g. 1 damage among 2 blockers, the exact iter-1 tier=ai violation) to Forge's
    // built-in combat AI. It's now a deterministic lethal-first split in "floor" mode (no model budget,
    // so it never leaves Java) — these tests exercise that path end-to-end against a REAL Game/Player/
    // Card via the same construction as the candidateLabel/landSummary tests above, proving (a) the
    // engine's own blocker order is honored when no model is consulted, (b) lethal amounts are computed
    // per blocker (not dumped arbitrarily), and (c) undistributed damage tramples through to the
    // defender when the attacker has trample, or piles onto the last blocker when it doesn't — either
    // way every point of damage is accounted for, never dropped, never Forge's AI judgment call.
    // ================================================================================

    @Test
    public void assignCombatDamage_multiBlockerNoTrample_floorModeAssignsLethalFirstThenExcessToLast() throws Exception {
        Game game = buildBareGame();
        Player attackerOwner = game.getPlayers().get(1);
        Player defenderPlayer = game.getPlayers().get(0);
        Card attacker = addBattlefieldCard("Craw Wurm", attackerOwner); // 6/4, vanilla, no trample
        Card blocker1 = addBattlefieldCard("Llanowar Elves", defenderPlayer); // 1/1
        Card blocker2 = addBattlefieldCard("Elvish Mystic", defenderPlayer);  // 1/1
        CardCollection blockers = new CardCollection();
        blockers.add(blocker1);
        blockers.add(blocker2);

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, defenderPlayer, lp, "seat-x", "", "not-used", "not-used", "floor");

        Map<Card, Integer> result = ctrl.assignCombatDamage(
                attacker, blockers, new CardCollection(), 3, defenderPlayer, false);

        Assert.assertEquals((int) result.get(blocker1), 1,
                "first blocker (engine order, no model consulted) gets exactly its lethal amount (1 toughness)");
        Assert.assertEquals((int) result.get(blocker2), 2,
                "second blocker absorbs its own lethal amount plus the leftover that has nowhere else to go (no trample)");
        int total = 0;
        for (int v : result.values()) total += v;
        Assert.assertEquals(total, 3, "all combat damage must be accounted for across blockers, none silently lost");
    }

    @Test
    public void assignCombatDamage_multiBlockerWithTrample_floorModeSpillsExcessToDefender() throws Exception {
        Game game = buildBareGame();
        Player attackerOwner = game.getPlayers().get(1);
        Player defenderPlayer = game.getPlayers().get(0);
        Card attacker = addBattlefieldCard("Iron Tusk Elephant", attackerOwner); // 3/3, vanilla Trample
        Card blocker1 = addBattlefieldCard("Llanowar Elves", defenderPlayer); // 1/1
        Card blocker2 = addBattlefieldCard("Elvish Mystic", defenderPlayer);  // 1/1
        CardCollection blockers = new CardCollection();
        blockers.add(blocker1);
        blockers.add(blocker2);

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, defenderPlayer, lp, "seat-x", "", "not-used", "not-used", "floor");

        Map<Card, Integer> result = ctrl.assignCombatDamage(
                attacker, blockers, new CardCollection(), 3, defenderPlayer, false);

        Assert.assertEquals((int) result.get(blocker1), 1, "first blocker gets exactly its lethal amount");
        Assert.assertEquals((int) result.get(blocker2), 1, "second blocker gets exactly its lethal amount");
        Assert.assertEquals((int) result.get(null), 1,
                "the undistributed point tramples through to the defender (attacker has Trample) instead of "
                + "vanishing or being handed to Forge's AI");
        int total = 0;
        for (int v : result.values()) total += v;
        Assert.assertEquals(total, 3, "all combat damage must be accounted for, none silently lost");
    }

    // ================================================================================
    // Fix-cycle regression coverage (2026-07-21 harness-fixes pass):
    //   - DEFECT 1: confirmAction's bridge-down path used to hand off to Forge's AI (the hard-invariant
    //     violation: 6 tier=ai rows in the gpt-5 model-sweep, every one confirm(bridge-down)). It must
    //     now take the same deterministic NO the unparsed branch already used.
    //   - DEFECT 2: declareAttackers/arrangeTopN (scry/surveil) used to fall through to the model even
    //     at n_options==1, mislabeling a forced pick as tier=smart. Both must now short-circuit to
    //     tier=auto without ever consulting the model, same convention as choose-entity/zone-change/
    //     targets.
    // DeadBridge lets these exercise the real bridge-down code path deterministically (ask() always
    // returns null, exactly what a real dead ClaudeBridge/OpenRouterBridge returns after its own
    // internal retries are exhausted) without spawning a process or making a network call.
    // ================================================================================

    /** Fake LLMBridge for tests: ask() always returns null (bridge-down), never touching a real
     *  CLI/API backend. LLMBridge is package-visible (not public) precisely so an in-package test
     *  double like this one can implement it directly. */
    static final class DeadBridge implements LLMBridge {
        private final String label;
        DeadBridge(String label) { this.label = label; }
        @Override public String ask(String prompt) { return null; }
        @Override public String getLabel() { return label; }
        @Override public String getModel() { return "dead-bridge"; }
        @Override public long getTotalMs() { return 0; }
        @Override public int getCalls() { return 0; }
    }

    @Test(timeOut = 30000)
    public void confirmAction_bridgeDown_deterministicNoNeverDelegatesToForgeAi() throws Exception {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, p, lp, "seat-x", "",
                new DeadBridge("seat-x/smart"), new DeadBridge("seat-x/fast"), "llm");

        boolean result = ctrl.confirmAction(null, PlayerActionConfirmMode.OptionalChoose,
                "Do you want to shuffle the library?", null, null, null);

        Assert.assertFalse(result,
                "a dead bridge must take the same deterministic NO the unparsed branch already uses, "
                + "never a Forge-AI coinflip");
        // confirm's fallback path logs via evalDec() directly rather than the auto() helper (true both
        // before and after this fix — the confirm(unparsed) branch it now mirrors always worked this
        // way), so aiDelegationsCount is the metric that actually captures the hard-invariant defect.
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0,
                "confirm(bridge-down) must never hand off to Forge's AI (this was the hard-invariant defect: "
                + "6 tier=ai rows in the gpt-5 model-sweep, every one confirm(bridge-down))");
    }

    @Test
    public void declareAttackers_singleLegalAttacker_forcedOnlyLegalNeverAsksModel() throws Exception {
        Game game = buildBareGame();
        Player attackerOwner = game.getPlayers().get(1);
        Player defenderPlayer = game.getPlayers().get(0);
        Card onlyAttacker = addBattlefieldCard("Craw Wurm", attackerOwner);
        onlyAttacker.setSickness(false); // make sure it's actually legal to attack with
        game.getPhaseHandler().devModeSet(PhaseType.MAIN1, attackerOwner);
        game.getAction().checkStateEffects(true);

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        // mode="llm" with a real (nonzero) SMART_BUDGET -- proves the forced-only-legal short-circuit
        // fires BEFORE any budget/model check, not merely because the floor arm has no budget.
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, attackerOwner, lp, "seat-x", "",
                new DeadBridge("seat-x/smart"), new DeadBridge("seat-x/fast"), "llm");

        Combat combat = new Combat(attackerOwner);
        ctrl.declareAttackers(attackerOwner, combat);

        Assert.assertEquals(combat.getAttackers().size(), 1, "the sole legal attacker should be declared");
        Assert.assertTrue(combat.getAttackers().contains(onlyAttacker));
        Assert.assertEquals(ctrl.autoPicksCount(), 1,
                "n_options==1 must be a deterministic auto-pick, never a model call (the forced-pick mislabel)");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "must never hand off to Forge's AI");
    }

    @Test
    public void arrangeForSurveil_singleCardPeek_forcedOnlyLegalKeepsItOnTop() throws Exception {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        Card seen = addBattlefieldCard("Craw Wurm", p);
        CardCollection topN = new CardCollection();
        topN.add(seen);

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, p, lp, "seat-x", "",
                new DeadBridge("seat-x/smart"), new DeadBridge("seat-x/fast"), "llm");

        ImmutablePair<CardCollection, CardCollection> result = ctrl.arrangeForSurveil(topN);

        Assert.assertEquals(result.getLeft().size(), 1, "the single peeked card should stay on top");
        Assert.assertTrue(result.getLeft().contains(seen));
        Assert.assertEquals(result.getRight().size(), 0, "nothing should be sent to the graveyard");
        Assert.assertEquals(ctrl.autoPicksCount(), 1,
                "n_options==1 must be a deterministic auto-pick, never a model call (the surveil forced-pick mislabel)");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "must never hand off to Forge's AI");
    }

    // ================================================================================
    // getAbilityToPlay (harness fix round B): previously a bare `return super.getAbilityToPlay(...)`
    // -- the file's last unlogged Forge-AI delegation (PlayerControllerAi's own implementation is just
    // `abilities.get(0)`, zero jsonl/log trace). Now routed: n_options==1 is a deterministic auto-pick
    // (the common case -- GameActionUtil.getAdditionalCostSpell seeds the list with exactly `[sa]`
    // unless the card has an AlternateAdditionalCost keyword), and a real >1-option choice with a dead
    // bridge falls back deterministically to the first option, never to Forge's AI.
    // ================================================================================

    @Test
    public void getAbilityToPlay_singleOption_forcedOnlyLegalNeverAsksModel() throws Exception {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        Card host = addBattlefieldCard("Craw Wurm", p);
        SpellAbility sa = host.getFirstSpellAbility();

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, p, lp, "seat-x", "",
                new DeadBridge("seat-x/smart"), new DeadBridge("seat-x/fast"), "llm");

        SpellAbility result = ctrl.getAbilityToPlay(host, Lists.newArrayList(sa), null);

        Assert.assertSame(result, sa, "the sole legal ability variant should be returned as-is");
        Assert.assertEquals(ctrl.autoPicksCount(), 1,
                "a single legal ability must be a deterministic auto-pick, never a model call");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "must never hand off to Forge's AI");
    }

    @Test
    public void getAbilityToPlay_multiOption_bridgeDownFallsBackToFirstNeverForgeAi() throws Exception {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        Card host = addBattlefieldCard("Craw Wurm", p);
        SpellAbility sa1 = host.getFirstSpellAbility();
        SpellAbility sa2 = sa1.copy(); // a 2nd cast-mode variant, e.g. an alternate-cost bundle
        List<SpellAbility> abilities = Lists.newArrayList(sa1, sa2);

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, p, lp, "seat-x", "",
                new DeadBridge("seat-x/smart"), new DeadBridge("seat-x/fast"), "llm");

        SpellAbility result = ctrl.getAbilityToPlay(host, abilities, null);

        Assert.assertSame(result, sa1, "a dead bridge must fall back to the first option deterministically");
        Assert.assertEquals(ctrl.autoPicksCount(), 1,
                "bridge-down must be a deterministic auto-pick, never a model call");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0,
                "a dead bridge must never fall through to Forge's AI for this decision");
    }

    // ================================================================================
    // INVESTIGATION 3 (truncation/bridge-down root cause): gpt-5 and deepseek's fast-tier decisions
    // (confirm/play) were losing their entire token budget to hidden reasoning at the flat
    // DEFAULT_FAST_MAX_TOKENS=512 every model shares, leaving zero room for the actual answer. The fix
    // gives ONLY the reasoning-heavy row (matched by substring "deepseek"/"gpt") a larger fastMaxTokens/
    // fastTimeoutMs; models with no evidence of this failure mode keep the flat default.
    // ================================================================================

    @Test
    public void modelCfg_reasoningModelsGetLargerFastTierBudgetThanFlatDefault() {
        LlmSpikeGui.ModelCfg gpt = LlmSpikeGui.ModelCfg.forModel("openai/gpt-5");
        LlmSpikeGui.ModelCfg deepseek = LlmSpikeGui.ModelCfg.forModel("deepseek/deepseek-v4-pro");
        LlmSpikeGui.ModelCfg other = LlmSpikeGui.ModelCfg.forModel("some/other-model");

        Assert.assertTrue(gpt.fastMaxTokens > LlmSpikeGui.ModelCfg.DEFAULT_FAST_MAX_TOKENS,
                "gpt-5 is a reasoning model that burns hidden reasoning tokens even on the fast tier -- "
                + "the flat 512-token default left zero room for the final CHOICE/PICKS line every time "
                + "(8/8 finish_reason=length events had empty content in the gpt-5 model-sweep game)");
        Assert.assertEquals(deepseek.fastMaxTokens, gpt.fastMaxTokens,
                "gpt and deepseek share the same ModelCfg row (both matched the same evidence)");
        Assert.assertTrue(gpt.fastTimeoutMs > LlmSpikeGui.ModelCfg.DEFAULT_FAST_TIMEOUT_MS,
                "a larger token budget needs more time to generate");
        Assert.assertEquals(other.fastMaxTokens, LlmSpikeGui.ModelCfg.DEFAULT_FAST_MAX_TOKENS,
                "models without evidence of this failure mode (kimi/grok/default) must keep the flat default");
        Assert.assertEquals(other.fastTimeoutMs, LlmSpikeGui.ModelCfg.DEFAULT_FAST_TIMEOUT_MS);
    }

    // ================================================================================
    // chooseCardsToRevealFromHand (audit follow-up): the last unrouted PlayerController choose-cards
    // hook. Previously fell straight through to PlayerControllerAi's bare valid.subList(0,min(max,size))
    // with zero jsonl/log trace. Now routed like discard/cards-for-effect. These prove the three
    // fallback ladder rungs: (1) the no-model floor arm is a deterministic auto-pick (never Forge AI),
    // (2) a whole-set-forced reveal is a deterministic auto-pick, and (3) a dead bridge takes the
    // minimum legal subset without consulting Forge AI.
    // ================================================================================

    @Test
    public void chooseCardsToRevealFromHand_floorMode_deterministicNoBudgetNeverForgeAi() throws Exception {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        CardCollection valid = new CardCollection();
        valid.add(addHandCard("Llanowar Elves", p));
        valid.add(addHandCard("Craw Wurm", p));

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, p, lp, "seat-x", "",
                new DeadBridge("seat-x/smart"), new DeadBridge("seat-x/fast"), "floor");

        CardCollectionView revealed = ctrl.chooseCardsToRevealFromHand(1, 2, valid);

        Assert.assertEquals(revealed.size(), 1,
                "the no-model floor arm reveals the deterministic legal minimum (min=1), clamped to <=max");
        Assert.assertEquals(ctrl.autoPicksCount(), 1,
                "floor mode must be a deterministic auto-pick, never a model call");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0,
                "the no-model floor arm must NEVER hand off to Forge's AI (keeps the ai_tier hard invariant at 0)");
    }

    @Test
    public void chooseCardsToRevealFromHand_wholeSetForced_deterministicAutoPickNeverModel() throws Exception {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        CardCollection valid = new CardCollection();
        valid.add(addHandCard("Llanowar Elves", p));
        valid.add(addHandCard("Craw Wurm", p));

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        // mode="llm" with a live budget — proves the forced short-circuit fires BEFORE any budget/model
        // check, not merely because the floor arm has no budget.
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, p, lp, "seat-x", "",
                new DeadBridge("seat-x/smart"), new DeadBridge("seat-x/fast"), "llm");

        // min == valid.size(): the whole valid set must be revealed — no real choice.
        CardCollectionView revealed = ctrl.chooseCardsToRevealFromHand(2, 2, valid);

        Assert.assertEquals(revealed.size(), 2, "a forced reveal of the whole valid set returns every card");
        Assert.assertEquals(ctrl.autoPicksCount(), 1,
                "a whole-set-forced reveal must be a deterministic auto-pick, never a model call");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "must never hand off to Forge's AI");
    }

    @Test
    public void chooseCardsToRevealFromHand_bridgeDown_usesMinimumLegalSubsetNeverAi() throws Exception {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        CardCollection valid = new CardCollection();
        valid.add(addHandCard("Llanowar Elves", p));
        valid.add(addHandCard("Craw Wurm", p));
        valid.add(addHandCard("Elvish Mystic", p));

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, p, lp, "seat-x", "",
                new DeadBridge("seat-x/smart"), new DeadBridge("seat-x/fast"), "llm");

        // A range makes over-revealing distinguishable from the minimum legal fallback.
        CardCollectionView revealed = ctrl.chooseCardsToRevealFromHand(1, 3, valid);
        Assert.assertEquals(revealed.size(), 1);
        Assert.assertSame(revealed.get(0), valid.get(0));
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
        Assert.assertEquals(ctrl.autoPicksCount(), 1);
    }

    // ================================================================================
    // D0 — PlayerControllerAiLogging (Forge-AI distillation seat). Covers the index-mapping helpers
    // (incl. duplicate-name disambiguation), the play-menu land/spell mapping, and — as a full-game
    // integration test — the combat before/after diff reconciliation + the ai-teacher row invariant
    // (every enumerable ai-teacher row has choice in range, options non-empty, never a degenerate -1).
    // ================================================================================

    @Test
    public void playKey_duplicateNamedSpellsCollide_identityStillDisambiguates() {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        SpellAbility sa1 = addBattlefieldCard("Craw Wurm", p).getFirstSpellAbility();
        SpellAbility sa2 = addBattlefieldCard("Craw Wurm", p).getFirstSpellAbility();
        Assert.assertNotSame(sa1, sa2, "two distinct copies -> two distinct SpellAbility objects");
        // Same hostName|api|description key: the duplicate-name collision the design flags.
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.playKey(sa1),
                LlmSpikeGui.PlayerControllerLLM.playKey(sa2),
                "two copies of the same card produce the same play key (the ambiguity case)");
        List<SpellAbility> menu = Lists.newArrayList(sa1, sa2);
        // Object identity resolves the ambiguity exactly when the AI returned one of OUR enumerated objects.
        Assert.assertEquals(LlmSpikeGui.PlayerControllerAiLogging.indexByIdentity(menu, sa2), 1,
                "identity match pins the SECOND copy to index 1, never index 0");
        Assert.assertEquals(LlmSpikeGui.PlayerControllerAiLogging.indexByIdentity(menu, sa1), 0);
        // Key match is the best-effort fallback for a foreign object: it takes the FIRST slot with that
        // key (a defined, documented disambiguation) rather than guessing a wrong later index.
        Assert.assertEquals(LlmSpikeGui.PlayerControllerAiLogging.indexByKey(menu, sa2.copy()), 0,
                "a foreign (fresh-enumeration) copy with the shared key maps to the first matching slot");
    }

    @Test
    public void indexByKey_matchesFreshEnumerationCopyOfADistinctSpell() {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        SpellAbility elves = addBattlefieldCard("Llanowar Elves", p).getFirstSpellAbility();
        SpellAbility wurm  = addBattlefieldCard("Craw Wurm", p).getFirstSpellAbility();
        List<SpellAbility> menu = Lists.newArrayList(elves, wurm);
        // The AI returns a different object instance for the same underlying spell (fresh getSpellAbilities
        // enumeration) -> identity fails, key match still lands the right index.
        Assert.assertEquals(LlmSpikeGui.PlayerControllerAiLogging.indexByIdentity(menu, wurm.copy()), -1);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerAiLogging.indexByKey(menu, wurm.copy()), 1,
                "a fresh copy of Craw Wurm maps to its menu slot by hostName|api|description key");
    }

    @Test
    public void indexOfPlay_mapsLandByHostNameAndSpellByIdentity() {
        Game game = buildBareGame();
        Player p = game.getPlayers().get(0);
        Card wurm = addBattlefieldCard("Craw Wurm", p);
        SpellAbility wurmSa = wurm.getFirstSpellAbility();
        Card island = addHandCard("Island", p);
        SpellAbility islandLand = landAbilityOf(island, p);
        Assert.assertNotNull(islandLand, "Island must expose a play-land ability");

        List<SpellAbility> spells = Lists.newArrayList(wurmSa);
        List<SpellAbility> landPlays = Lists.newArrayList(islandLand);
        List<Card> landCards = Lists.newArrayList(island);
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po =
                new LlmSpikeGui.PlayerControllerLLM.PlayOptions(spells, landPlays, landCards);

        LobbyPlayer lp = new LobbyPlayerAi("seat-x", null);
        LlmSpikeGui.PlayerControllerAiLogging ctrl =
                new LlmSpikeGui.PlayerControllerAiLogging(game, p, lp, "seat-x");

        Assert.assertEquals(ctrl.indexOfPlay(wurmSa, po), 0, "the spell maps to its own slot by identity");
        Assert.assertEquals(ctrl.indexOfPlay(islandLand, po), 1,
                "a land play maps to the land block right after the spells (spells.size()+landIndex)");
        // A DIFFERENT Island card's land ability (same name, foreign object) still maps by host name.
        Card island2 = addHandCard("Island", p);
        SpellAbility island2Land = landAbilityOf(island2, p);
        Assert.assertNotNull(island2Land);
        Assert.assertEquals(ctrl.indexOfPlay(island2Land, po), 1,
                "a foreign land ability with the same host land name still maps to the land slot");
        // A spell the AI picked that is NOT in our (capped) enumeration -> -1, so the caller logs a
        // non-enumerable diagnostic rather than a wrong index.
        SpellAbility foreign = addBattlefieldCard("Elvish Mystic", p).getFirstSpellAbility();
        Assert.assertEquals(ctrl.indexOfPlay(foreign, po), -1,
                "a spell absent from the menu returns -1 (never a wrong index)");
    }

    /** Find a card's play-land ability the same way enumeratePlayOptions does. */
    private SpellAbility landAbilityOf(Card lc, Player p) {
        for (SpellAbility sa : lc.getAllPossibleAbilities(p, false)) if (sa.isLandAbility()) return sa;
        return null;
    }

    // ---- BLOCKER fix: the ai-teacher rebuilds the FULL templated prompt, byte-identical to the LLM
    //      seat's decideOne/decideMany/play prompt (shared builders are the single source of the template).

    @Test
    public void buildDecideOnePrompt_matchesTheDecideOneWireFormatExactly() {
        String snap = "== Turn 3 ==\nYOU life:20\nOPP life:18";
        List<String> opts = Lists.newArrayList("Lightning Bolt", "Brainstorm");
        // No decline option: options numbered 1..n, snapshot + PRIORITY/DECISION/Options + CHOICE tail.
        String got = LlmSpikeGui.PlayerControllerLLM.buildDecideOnePrompt(snap, "Pick a spell", opts, null);
        String expect = snap + "\nPRIORITY: you\nDECISION: Pick a spell\nOptions:\n"
                + "  1) Lightning Bolt\n  2) Brainstorm\n"
                + "Reason briefly, then end with EXACTLY: CHOICE: <number>";
        Assert.assertEquals(got, expect, "decideOne prompt template drifted");
        // With a decline label appended as the final numbered option (raw, not oneLine'd).
        String gotNone = LlmSpikeGui.PlayerControllerLLM.buildDecideOnePrompt(snap, "Pick a spell", opts, "Decline / none");
        Assert.assertTrue(gotNone.contains("  3) Decline / none\n"),
                "the decline option is appended as the last numbered menu entry");
    }

    @Test
    public void buildDecideManyPrompt_matchesTheDecideManyWireFormatExactly() {
        String snap = "== Turn 5 ==";
        List<String> opts = Lists.newArrayList("Goblin (1/1)", "Elf (2/1)");
        String got = LlmSpikeGui.PlayerControllerLLM.buildDecideManyPrompt(snap, "Declare attackers", opts, 0, 2);
        String expect = snap + "\nPRIORITY: you\nDECISION: Declare attackers\nChoose between 0 and 2 of these (by number):\n"
                + "  1) Goblin (1/1)\n  2) Elf (2/1)\n"
                + "Reason briefly, then end with EXACTLY: PICKS: <comma-separated numbers>"
                + " (or PICKS: NONE to choose zero)";
        Assert.assertEquals(got, expect, "decideMany prompt template drifted");
    }

    @Test
    public void playPrompt_prefixPlusTailMatchesTheLlmSeatPlayPrompt() {
        String snap = "== Turn 2 ==";
        List<String> labels = Lists.newArrayList("CAST Lightning Bolt", "PLAY LAND Mountain", "PASS (do nothing)");
        String menuStr = LlmSpikeGui.PlayerControllerLLM.numberedPlayMenu(labels);
        Assert.assertEquals(menuStr, "  1) CAST Lightning Bolt\n  2) PLAY LAND Mountain\n  3) PASS (do nothing)\n",
                "numbered play menu format drifted");
        String got = LlmSpikeGui.PlayerControllerLLM.playPromptPrefix(snap, PhaseType.MAIN1, true, menuStr)
                + LlmSpikeGui.PlayerControllerLLM.PLAY_PROMPT_TAIL;
        String expect = snap + "\nDECISION: You have priority (" + PhaseType.MAIN1
                + ", your main phase). Choose ONE action:\n" + menuStr
                + "Reason briefly, then end with EXACTLY: CHOICE: <number>";
        Assert.assertEquals(got, expect, "play prompt (non-plan) template drifted");
        // Reactive (not own main) window uses the instant-speed wording.
        String react = LlmSpikeGui.PlayerControllerLLM.playPromptPrefix(snap, PhaseType.COMBAT_DECLARE_BLOCKERS, false, menuStr);
        Assert.assertTrue(react.contains(", instant-speed window). Choose ONE action:"),
                "a reactive window reads as an instant-speed window");
    }

    // ---- MEDIUM fix: the ai-teacher's three bespoke (non-decideOne/Many) prompts — mulligan, attackers
    //      question, blockers — go through shared builders so they can never drift from the LLM seat's
    //      wire format. These pins are the byte-for-byte contract both seats emit.

    @Test
    public void buildMulliganPrompt_matchesTheMulliganWireFormatExactly() {
        String got = LlmSpikeGui.PlayerControllerLLM.buildMulliganPrompt(7, 1, "Island, Brainstorm, Ponder", true);
        String expect = "MULLIGAN DECISION. You are ON THE PLAY (you go first)."
                + " This is a 7-card hand with 1 land(s): Island, Brainstorm, Ponder"
                + ". If you MULLIGAN you will look at a NEW hand of only 6"
                + " cards (each mulligan costs you a card), and you can never go below 5. If you KEEP you play this "
                + "7-card hand as-is. Keeping a workable hand almost always beats shrinking it. "
                + "For a combo deck, a hand with your enablers + a payoff is a KEEP even on few lands; only ship a hand "
                + "that genuinely cannot function. Keep or mulligan? End with EXACTLY one word: KEEP or MULLIGAN.";
        Assert.assertEquals(got, expect, "mulligan prompt template drifted");
    }

    @Test
    public void buildAttackersQuestion_matchesTheAttackersQuestionWireFormatExactly() {
        // Non-infect: life-based lethal hint, poisonNeeded = 10 - oppPoison, no infect parenthetical.
        String got = LlmSpikeGui.PlayerControllerLLM.buildAttackersQuestion("Bob", 18, 0, false);
        String expect = "COMBAT — declare attackers vs Bob (life 18"
                + "). Pick which creatures attack (attacking with 0 is legal and sometimes correct — "
                + "weigh their untapped mana and possible blocks). "
                + "COUNT LETHAL: opp life 18, poison 0/10 -- lethal needs 18 dmg OR 10 poison"
                + ". If your unblocked/unstoppable damage meets either number, attack with everything needed and WIN NOW.";
        Assert.assertEquals(got, expect, "attackers question template drifted");
        // Infect present + existing poison: parenthetical appears, poisonNeeded reflects counters.
        String gotInf = LlmSpikeGui.PlayerControllerLLM.buildAttackersQuestion("Bob", 20, 3, true);
        Assert.assertTrue(gotInf.contains("poison 3/10 -- lethal needs 20 dmg OR 7 poison"
                + " (infect attackers deal poison, not life loss)"),
                "infect attackers add the poison parenthetical and correct poisonNeeded");
    }

    @Test
    public void buildBlockersPrompt_matchesTheBlockersWireFormatExactly() {
        String snap = "== Turn 6 ==";
        String got = LlmSpikeGui.PlayerControllerLLM.buildBlockersPrompt(snap, "Goblin (2/2)", "Wall (0/4)");
        String expect = snap + "\nCOMBAT — declare blockers. Incoming: Goblin (2/2). Your blockers: Wall (0/4). "
                + "Assign blocks (each blocker blocks one attacker). Weigh your life total vs losing blockers. "
                + "End with EXACTLY: BLOCKS: blocker->attacker, blocker->attacker   (or BLOCKS: NONE)";
        Assert.assertEquals(got, expect, "blockers prompt template drifted");
    }

    @Test(timeOut = 220000)
    public void aiLogging_fullGame_everyEnumerableAiTeacherRowIsValid() throws Exception {
        java.io.File dir = java.nio.file.Files.createTempDirectory("ai-log-test").toFile();
        long seed = 4242L;
        forge.util.MyRandom.setRandom(new java.util.Random(seed));
        LlmSpikeGui.EvalLog.closeForTest();
        LlmSpikeGui.EvalLog.initToDir(dir.getAbsolutePath(), seed);
        try {
            Deck d1 = DeckgenUtil.getRandomColorDeck(true);
            Deck d2 = DeckgenUtil.getRandomColorDeck(true);
            List<RegisteredPlayer> players = Lists.newArrayList();
            players.add(new RegisteredPlayer(d1).setPlayer(new LlmSpikeGui.LobbyPlayerAiLogging("AiLog-A")));
            players.add(new RegisteredPlayer(d2).setPlayer(new LlmSpikeGui.LobbyPlayerAiLogging("AiLog-B")));
            GameRules rules = new GameRules(GameType.Constructed);
            Match match = new Match(rules, players, "AiLogTest");
            final Game game = match.createGame();
            Thread worker = new Thread(() -> {
                try { match.startGame(game); }
                catch (Throwable t) { System.out.println("[AI-LOG-TEST] game thread threw: " + t); }
            }, "forge-game-ailog");
            worker.start();
            worker.join(180000);
            if (worker.isAlive()) { game.setGameOver(GameEndReason.Draw); worker.join(10000); }
        } finally {
            LlmSpikeGui.EvalLog.closeForTest();
        }

        java.io.File[] files = dir.listFiles((d, n) -> n.endsWith(".jsonl"));
        Assert.assertNotNull(files, "no jsonl produced");
        Assert.assertTrue(files.length >= 1, "expected a per-game jsonl file");

        java.util.Set<String> INDEX_STYLE = java.util.Set.of(
                "ability-to-play", "entity", "zone-change", "targets", "number", "card-name", "mulligan");
        int aiTeacher = 0, enumerable = 0, promptChecked = 0, passRows = 0;
        java.util.Map<String, Integer> byType = new java.util.HashMap<>();
        for (java.io.File f : files) {
            for (String line : java.nio.file.Files.readAllLines(f.toPath())) {
                if (!line.contains("\"tier\":\"ai-teacher\"")) continue;
                aiTeacher++;
                String type = jstr(line, "type");
                int nOpt = jint(line, "n_options");
                int choice = jint(line, "choice");
                byType.merge(type, 1, Integer::sum);
                if (nOpt > 0) {
                    enumerable++;
                    Assert.assertTrue(line.contains("\"options\":[\""),
                            "enumerable ai-teacher row must have a non-empty options list: " + line);
                    // BLOCKER fix: the prompt must be the FULL templated prompt (snapshot + question +
                    // numbered menu), not a bare board snapshot. Assert against the RAW line (jstr's regex
                    // truncates a JSON string at the first escaped quote). raw_reply is null on ai-teacher
                    // rows, so these markers can only come from the prompt field.
                    Assert.assertFalse(line.contains("\"prompt\":null"),
                            "enumerable ai-teacher row must carry a non-null prompt: " + line);
                    Assert.assertTrue(line.contains("DECISION") || line.contains("COMBAT")
                                    || line.contains("MULLIGAN"),
                            "ai-teacher prompt must carry the decision framing, not a bare snapshot: " + line);
                    Assert.assertTrue(line.contains("1)") || line.contains("KEEP or MULLIGAN")
                                    || line.contains("BLOCKS:"),
                            "ai-teacher prompt must carry the numbered/keyword option menu: " + line);
                    promptChecked++;
                    if ("play".equals(type)) {
                        // The play row's PASS is logged as choice=-1 (mirrors the LLM seat); every real
                        // action is a 0-based index into the labels (incl. PASS slot), i.e. < n_options.
                        Assert.assertTrue(choice == -1 || (choice >= 0 && choice < nOpt),
                                "play choice must be -1 (PASS) or in [0,n_options): " + line);
                        if (choice == -1) passRows++;
                    } else if (INDEX_STYLE.contains(type)) {
                        Assert.assertTrue(choice >= 0,
                                "index-style ai-teacher row must never carry a degenerate choice=-1: " + line);
                        Assert.assertTrue(choice < nOpt,
                                "index-style choice must be in [0,n_options): " + line);
                    } else {
                        Assert.assertTrue(choice >= 0,
                                "count-style ai-teacher row must never carry a degenerate choice=-1: " + line);
                        Assert.assertTrue(choice <= nOpt,
                                "count-style choice must be in [0,n_options] (subset size): " + line);
                    }
                }
            }
        }
        System.out.println("[AI-LOG-TEST] ai-teacher rows=" + aiTeacher + " enumerable=" + enumerable
                + " prompt-checked=" + promptChecked + " play-PASS=" + passRows + " byType=" + byType);
        Assert.assertTrue(aiTeacher > 0, "the ai-logging seat must emit ai-teacher rows");
        Assert.assertTrue(enumerable > 0, "at least some ai-teacher rows must be enumerable (n_options>0)");
        Assert.assertTrue(promptChecked > 0, "at least some ai-teacher rows must carry a full templated prompt");
    }

    private static int jint(String line, String key) {
        Matcher m = Pattern.compile("\"" + key + "\":(-?\\d+)").matcher(line);
        Assert.assertTrue(m.find(), key + " not found in: " + line);
        return Integer.parseInt(m.group(1));
    }
    private static String jstr(String line, String key) {
        Matcher m = Pattern.compile("\"" + key + "\":\"([^\"]*)\"").matcher(line);
        Assert.assertTrue(m.find(), key + " not found in: " + line);
        return m.group(1);
    }
    @Test
    public void memoryToolsRouteThroughRealControllerAndAuditEveryRound() throws Exception {
        Game game = buildBareGame();
        Player player = game.getPlayers().get(0);
        game.getGameLog().add(forge.game.GameLogEntryType.STACK_ADD, "p2 cast MemoryCanarySpell");
        java.util.List<String> prompts = new java.util.ArrayList<>();
        LLMBridge bridge = new LLMBridge() {
            public String ask(String prompt) {
                prompts.add(prompt);
                return LlmSpikeGui.PlayerControllerLLM.MEMORY_TOOLS && prompts.size() == 1
                        ? "MEMORY SEARCH 0 MemoryCanarySpell" : "CHOICE: 1";
            }
            public String getLabel() { return "test/fast"; }
            public String getModel() { return "fake"; }
            public long getTotalMs() { return 0; }
            public int getCalls() { return prompts.size(); }
            public int getLastPromptTokens() { return 10; }
            public int getLastCompletionTokens() { return 5; }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(
                game, player, new LobbyPlayerAi("memory-test", null), "memory-test", "",
                new DeadBridge("test/smart"), bridge, "llm");
        java.nio.file.Path dir = java.nio.file.Files.createTempDirectory("context-controller-test");
        LlmSpikeGui.EvalLog.closeForTest();
        LlmSpikeGui.EvalLog.initToDir(dir.toString(), 42);
        java.io.File file = LlmSpikeGui.EvalLog.currentFile();
        try {
            Assert.assertTrue(ctrl.confirmAction(null, PlayerActionConfirmMode.OptionalChoose,
                    "Resolve the canary choice?", null, null, null));
            Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
        } finally {
            LlmSpikeGui.EvalLog.closeForTest();
        }
        String trace = java.nio.file.Files.readString(file.toPath());
        if (LlmSpikeGui.PlayerControllerLLM.MEMORY_TOOLS) {
            Assert.assertEquals(prompts.size(), 2);
            Assert.assertTrue(prompts.get(1).contains("MEMORY RESULT (historical data):"));
            Assert.assertTrue(prompts.get(1).contains("M1 (observation): [E"));
            Assert.assertTrue(prompts.get(1).contains("Resolve the canary choice?"));
            Assert.assertEquals(trace.split(java.util.regex.Pattern.quote("\"t\":\"context_call\""), -1).length - 1, 2);
            Assert.assertTrue(trace.contains("\"t\":\"context_tool\""));
            Assert.assertTrue(trace.contains("\"prompt_tokens\":10"));
        } else {
            Assert.assertEquals(prompts.size(), 1);
            Assert.assertFalse(trace.contains("context_call"));
        }
    }


    static final class FixedReplyBridge implements LLMBridge {
        final String reply;
        final List<String> prompts = new java.util.ArrayList<>();
        FixedReplyBridge(String reply) { this.reply = reply; }
        public String ask(String prompt) { prompts.add(prompt); return reply; }
        public String getLabel() { return "test/numeric"; }
        public String getModel() { return "fake"; }
        public long getTotalMs() { return 0; }
        public int getCalls() { return prompts.size(); }
    }

    private LlmSpikeGui.PlayerControllerLLM choiceController(FixedReplyBridge bridge) {
        Game game = buildBareGame();
        return new LlmSpikeGui.PlayerControllerLLM(game, game.getPlayers().get(0),
                new LobbyPlayerAi("choice-test", null), "choice-test", "", bridge, bridge, "llm");
    }

    @Test
    public void numericParamsOverloadAllowsInteriorValuesAndFullIntegerRange() {
        FixedReplyBridge bridge = new FixedReplyBridge("NUMBER: 7");
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(bridge);
        Assert.assertEquals(ctrl.chooseNumber(null, "Choose", 0, 100, java.util.Map.of()), 7);
        Assert.assertTrue(bridge.prompts.get(0).contains("Every integer from 0 through 100"));
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
        bridge = new FixedReplyBridge("NUMBER: 2147483647");
        ctrl = choiceController(bridge);
        Assert.assertEquals(ctrl.chooseNumber(null, "Choose", Integer.MIN_VALUE, Integer.MAX_VALUE), Integer.MAX_VALUE);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void numericListOverloadPreservesSparseOptionsAndMaxBoundaryMenu() {
        FixedReplyBridge bridge = new FixedReplyBridge("CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(bridge);
        Assert.assertEquals(ctrl.chooseNumber(null, "Sparse", Lists.newArrayList(-9, 17, 10000), null), 17);
        Assert.assertEquals(ctrl.chooseNumber(null, "Boundary", Integer.MAX_VALUE - 1, Integer.MAX_VALUE), Integer.MAX_VALUE);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void numericRepliesRejectOutOfRangeOverflowAndMixedActions() {
        for (String reply : Lists.newArrayList("NUMBER: 101", "NUMBER: 999999999999", "NUMBER: 7\nCHOICE: 1",
                "NUMBER: 7\nMEMORY READ 1 0", "I choose NUMBER: 7")) {
            Assert.assertNull(LlmSpikeGui.PlayerControllerLLM.parseNumberReply(reply, 0, 100));
        }
        FixedReplyBridge bridge = new FixedReplyBridge("NUMBER: 999999999999");
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(bridge);
        Assert.assertEquals(ctrl.chooseNumber(null, "Invalid", 3, 100), 3);
        Assert.assertEquals(bridge.prompts.size(), 2);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void binaryOverloadsUseModelAndHumanOptionOrdering() {
        FixedReplyBridge bridge = new FixedReplyBridge("CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(bridge);
        for (forge.game.player.PlayerController.BinaryChoiceType kind : forge.game.player.PlayerController.BinaryChoiceType.values()) {
            Assert.assertFalse(ctrl.chooseBinary(null, "Choose", kind, java.util.Map.of()));
            Assert.assertFalse(ctrl.chooseBinary(null, "Choose", kind, Boolean.TRUE));
        }
        Assert.assertTrue(bridge.prompts.stream().anyMatch(p -> p.contains("Leave tapped")));
        Assert.assertEquals(bridge.prompts.size(), 16);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void optionalPaymentsAndReplacementsHonorModelAcceptanceAndDecline() {
        for (String reply : Lists.newArrayList("CHOICE: 1", "CHOICE: 2")) {
            FixedReplyBridge bridge = new FixedReplyBridge(reply);
            LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(bridge);
            boolean expected = reply.endsWith("1");
            Assert.assertEquals(ctrl.confirmPayment(null, "Pay two life?", null), expected);
            Assert.assertEquals(ctrl.confirmReplacementEffect(null, null, null, "Dredge instead of drawing?"), expected);
            Assert.assertEquals(bridge.prompts.size(), 2);
            Assert.assertTrue(bridge.prompts.get(0).contains("Pay two life?"));
            Assert.assertTrue(bridge.prompts.get(1).contains("Dredge instead of drawing?"));
            Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
        }
    }

    @Test
    public void g7StartingPlayerIsModelRoutedAndDeterministicWhenUnrouted() {
        // "Draw first" is the second menu option -> the opponent starts.
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(new FixedReplyBridge("CHOICE: 2"));
        Assert.assertSame(ctrl.chooseStartingPlayer(true), ctrl.getPlayer().getOpponents().iterator().next());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
        // "Play first" -> self, and an unparseable reply falls back to play (never Forge AI).
        ctrl = choiceController(new FixedReplyBridge("CHOICE: 1"));
        Assert.assertSame(ctrl.chooseStartingPlayer(true), ctrl.getPlayer());
        ctrl = choiceController(new FixedReplyBridge("not a choice"));
        Assert.assertSame(ctrl.chooseStartingPlayer(true), ctrl.getPlayer());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void g7ColorSomeTypeAndProtectionChoicesAreModelRouted() {
        // Colour: pick the second colour in the engine's set (Blue).
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(new FixedReplyBridge("CHOICE: 2"));
        Assert.assertEquals(ctrl.chooseColor("name a colour", null,
                forge.card.ColorSet.fromEnums(forge.card.MagicColor.Color.WHITE, forge.card.MagicColor.Color.BLUE)),
                forge.card.MagicColor.BLUE);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);

        // A single-colour set is not a real choice: deterministic fallback, no model call.
        ctrl = choiceController(new FixedReplyBridge("CHOICE: 1"));
        Assert.assertEquals(ctrl.chooseColor("x", null,
                forge.card.ColorSet.fromEnums(forge.card.MagicColor.Color.RED)), forge.card.MagicColor.RED);

        // Type naming.
        ctrl = choiceController(new FixedReplyBridge("CHOICE: 1"));
        Assert.assertEquals(ctrl.chooseSomeType("creature type", null, java.util.Arrays.asList("Goblin", "Elf"), false), "Goblin");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);

        // Protection colour: second of three, then an unparseable reply falls back to the first.
        ctrl = choiceController(new FixedReplyBridge("CHOICE: 2"));
        Assert.assertEquals(ctrl.chooseProtectionType(null, java.util.Arrays.asList("red", "green", "white")), "green");
        ctrl = choiceController(new FixedReplyBridge("garbage"));
        Assert.assertEquals(ctrl.chooseProtectionType(null, java.util.Arrays.asList("red", "green")), "red");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void g7CleanupDiscardAndDelveAreModelRouted() {
        // Cleanup discard to hand size: pick the second of three hand cards.
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(new FixedReplyBridge("PICKS: 2"));
        Player me = ctrl.getPlayer();
        addHandCard("Plains", me);
        addHandCard("Island", me);
        addHandCard("Mountain", me);
        CardCollectionView discarded = ctrl.chooseCardsToDiscardToMaximumHandSize(1);
        Assert.assertEquals(discarded.size(), 1, "must discard exactly one card");
        Assert.assertEquals(discarded.iterator().next().getName(), "Island");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);

        // Delve: exile the second of three graveyard cards.
        ctrl = choiceController(new FixedReplyBridge("PICKS: 2"));
        me = ctrl.getPlayer();
        addGraveCard("Forest", me);
        addGraveCard("Swamp", me);
        addGraveCard("Island", me);
        CardCollectionView exiled = ctrl.chooseCardsToDelve(1, new CardCollection(me.getCardsIn(ZoneType.Graveyard)));
        Assert.assertEquals(exiled.size(), 1, "must exile exactly one card");
        Assert.assertEquals(exiled.iterator().next().getName(), "Swamp");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void decklistIsGroupedAndSorted() {
        Deck d = new Deck("t");
        d.getOrCreate(forge.deck.DeckSection.Main).add("Mountain", 20);
        d.getOrCreate(forge.deck.DeckSection.Main).add("Lightning Bolt", 4);
        d.getOrCreate(forge.deck.DeckSection.Main).add("Lightning Bolt", 0);
        Assert.assertEquals(LlmSpikeGui.decklist(d), "4x Lightning Bolt, 20x Mountain");
        Assert.assertEquals(LlmSpikeGui.decklist(null), "");
    }

    @Test
    public void g7OptionalCostsDeclineDeterministicallyWithoutAi() {
        // With no spell context the affordability check cannot pass, so the seat declines every
        // optional cost and never delegates to Forge AI (the old behaviour was super = AI).
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(new FixedReplyBridge("PICKS: 1"));
        List<forge.game.spellability.OptionalCostValue> vals = new java.util.ArrayList<>();
        vals.add(new forge.game.spellability.OptionalCostValue(
                forge.game.spellability.OptionalCost.Buyback, new forge.game.cost.Cost("1", true)));
        Assert.assertTrue(ctrl.chooseOptionalCosts(null, vals).isEmpty());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void g7PreventEffectCostIsModelRouted() {
        // Model declines an "unless" cost (Daze/Force Spike style): no payment, no AI delegation.
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(new FixedReplyBridge("CHOICE: 2"));
        Assert.assertFalse(ctrl.payCostToPreventEffect(new forge.game.cost.Cost("1", true), null, false, null));
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
        // Null cost is never paid.
        ctrl = choiceController(new FixedReplyBridge("CHOICE: 1"));
        Assert.assertFalse(ctrl.payCostToPreventEffect(null, null, false, null));
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Card into `p`'s graveyard (mirrors addHandCard). */
    private Card addGraveCard(String name, Player p) {
        IPaperCard paperCard = FModel.getMagicDb().getCommonCards().getCard(name);
        if (paperCard == null) { StaticData.instance().attemptToLoadCard(name); paperCard = FModel.getMagicDb().getCommonCards().getCard(name); }
        Card c = Card.fromPaperCard(paperCard, p);
        c.setGameTimestamp(p.getGame().getNextTimestamp());
        p.getZone(ZoneType.Graveyard).add(c);
        return c;
    }

    @Test
    public void optionalConfirmationMalformedRepliesDeclineWithoutAi() {
        FixedReplyBridge bridge = new FixedReplyBridge("not a choice");
        LlmSpikeGui.PlayerControllerLLM ctrl = choiceController(bridge);
        Assert.assertFalse(ctrl.confirmPayment(null, "Pay?", null));
        Assert.assertFalse(ctrl.confirmReplacementEffect(null, null, null, "Replace?"));
        Assert.assertEquals(bridge.prompts.size(), 4);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void triggerConfirmationDoesNotChangeTargetsAndMandatoryDoesNotAskModel() {
        Game game = buildBareGame();
        Player player = game.getPlayers().get(0);
        Card source = addBattlefieldCard("Forest", player);
        SpellAbility sa = forge.game.ability.AbilityFactory.getAbility(
                "AB$ GainLife | Cost$ 0 | LifeAmount$ 1 | SpellDescription$ Gain one life.", source);
        sa.getTargets().add(player);
        Object targets = sa.getTargets();
        FixedReplyBridge bridge = new FixedReplyBridge("CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(game, player,
                new LobbyPlayerAi("trigger-test", null), "trigger-test", "", bridge, bridge, "llm");
        forge.game.trigger.WrappedAbility optional = new forge.game.trigger.WrappedAbility(null, sa, player) {
            @Override public boolean isMandatory() { return false; }
        };
        Assert.assertFalse(ctrl.confirmTrigger(optional));
        Assert.assertEquals(bridge.prompts.size(), 1);
        Assert.assertSame(sa.getTargets(), targets);
        Assert.assertTrue(com.google.common.collect.Iterables.contains(sa.getTargets().getTargetPlayers(), player));
        forge.game.trigger.WrappedAbility mandatory = new forge.game.trigger.WrappedAbility(null, sa, player) {
            @Override public boolean isMandatory() { return true; }
        };
        Assert.assertTrue(ctrl.confirmTrigger(mandatory));
        Assert.assertEquals(bridge.prompts.size(), 1);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }
}
