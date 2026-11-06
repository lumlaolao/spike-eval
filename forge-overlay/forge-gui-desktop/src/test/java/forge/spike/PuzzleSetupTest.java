package forge.spike;

import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import forge.GuiDesktop;
import forge.LobbyPlayer;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.staticability.StaticAbilityCantBeCast;
import forge.game.zone.ZoneType;
import forge.gamemodes.puzzle.Puzzle;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.FileSection;
import forge.util.FileUtil;

/** A .pzl board as the puzzle runner puts it down: what the board says must be what the game holds. */
public class PuzzleSetupTest {
    /** The zero-model spec the baseline JVMs use (eval/puzzles/puzzle_baselines.py): the pass arm never calls it. */
    private static final String NO_MODEL = "openrouter:zero-model/none";
    /** The repo root: tests run from the Forge checkout's forge-gui-desktop, so only the rules file's -D path
     *  ($REPO/data/rules/MagicCompRules.txt, set by acceptance) names it. */
    private static final File REPO = repoRoot();

    static File repoRoot() {
        String rules = System.getProperty("harness.rules.file");
        if (rules == null) throw new IllegalStateException("-Dharness.rules.file is not set; the repo root comes from it");
        return new File(rules).getAbsoluteFile().getParentFile().getParentFile().getParentFile();
    }

    /** Both statics the runner sets: a brief left behind turns every later test's system prompt into a puzzle's
     *  (SeatObservationTest's non-puzzle framing failed after this class, pr-review of PR #273). */
    @AfterMethod
    public void clearSolverSeat() { LlmSpikeGui.puzzleSolverSeat = null; LlmSpikeGui.puzzleBrief = null; }

    /** The solver's plan and own-card tracking read DECKLIST: an exported DECKLIST=0 must not fail these tests
     *  (round 4 of PR #394), so each runs against an empty environment and puts it back. */
    private Map<String, String> savedEnv;
    @org.testng.annotations.BeforeMethod
    public void emptyEnvironment() { savedEnv = LlmSpikeGui.envOverride; LlmSpikeGui.envOverride = Map.of(); }
    @AfterMethod
    public void restoreEnvironment() { LlmSpikeGui.envOverride = savedEnv; }

    @BeforeClass
    public void initializeModel() {
        if (GuiBase.getInterface() == null) {
            GuiBase.setInterface(new GuiDesktop());
            FModel.initialize(null, preferences -> {
                preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
                preferences.setPref(FPref.UI_LANGUAGE, "en-US");
                return null;
            });
        }
    }

    private static Game boardOf(String pzl) throws Exception {
        File f = new File(REPO,
                "eval/puzzles/rules-pilot/" + pzl);
        return gameOf(FileSection.parseSections(FileUtil.readFile(f)), pzl);
    }

    /** The board of {@code sections} applied on a thread named "Game..." (GameState.applyToGame runs inline only there)
     *  by the runner's own start-hook method: the board, the NamedCard restore and the goal card's brief. Both seats are
     *  the pass arm's; RulesProbeLabelExport replaces the solver's. */
    static Game gameOf(Map<String, List<String>> sections, String name) throws Exception {
        Puzzle puzzle = new Puzzle(sections, name, false);
        List<RegisteredPlayer> players = new ArrayList<>();
        LobbyPlayer solverSeat = LlmSpikeGui.seatFor("pass", "Solver", "", NO_MODEL, NO_MODEL);
        LlmSpikeGui.puzzleSolverSeat = solverSeat;  // the goal-bearing seat, designated before the game is built
        players.add(new RegisteredPlayer(new Deck()).setPlayer(solverSeat));
        players.add(new RegisteredPlayer(new Deck()).setPlayer(LlmSpikeGui.seatFor("pass", "Opponent", "", NO_MODEL, NO_MODEL)));
        Match match = new Match(new GameRules(GameType.Puzzle), players, "PuzzleSetupTest");
        Game game = match.createGame();
        Throwable[] thrown = {null};
        Thread t = new Thread(() -> {
            try {
                LlmSpikePuzzle.briefSolver(sections, puzzle);
                LlmSpikePuzzle.applyBoard(game, puzzle);
            } catch (Throwable e) {
                thrown[0] = e;
            }
        }, "Game-PuzzleSetupTest");
        t.start();
        t.join(60000);
        if (thrown[0] != null) throw new AssertionError("board did not apply", thrown[0]);
        if (t.isAlive()) throw new AssertionError("board did not apply in 60 s");
        return game;
    }

    /** A puzzle's solver reads its own cards' rules text, as a game seat reads its deck's (#391): the splice board's
     *  answer turns on Splice onto Arcane, Lava Spike being Arcane and Thermo-Alchemist untapping. The seat is built by
     *  the helper playOnce seats it with, and its plan is what its system prompt carries. Its library (four Colossal
     *  Dreadmaw, the solver's hidden draw) and the opponent's (Plains) are not shown. DECKLIST=0, the game seat's
     *  opt-out, gives none. */
    @Test
    public void aPuzzleSolverReadsItsOwnCardsButNotItsLibrary() throws Exception {
        Map<String, List<String>> sections = FileSection.parseSections(
                FileUtil.readFile(new File(REPO, "eval/puzzles/hard/gen2/splice2_325cd33e0a57.pzl")));
        LlmSpikePuzzle.briefSolver(sections, new Puzzle(sections, "splice2_325cd33e0a57", false));
        String plan = ((LlmSpikeGui.LobbyPlayerLLM) LlmSpikePuzzle.solverSeat(sections, "llm", NO_MODEL, NO_MODEL)).plan();
        String system = LlmSpikeGui.PlayerControllerLLM.seatFraming("Solver", plan, true, false, true);
        Assert.assertTrue(system.contains("<your_deck>\nYOUR CARDS"), system);
        Assert.assertTrue(system.contains("Glacial Ray deals 2 damage to any target. Splice onto Arcane {1}{R}"), system);
        String spike = system.lines().filter(l -> l.startsWith("  Lava Spike ")).findFirst().orElse("");
        Assert.assertTrue(spike.contains("Arcane"), "Lava Spike's type line: " + spike);
        Assert.assertTrue(system.contains("Whenever you cast an instant or sorcery spell, untap Thermo-Alchemist."), system);
        Assert.assertFalse(system.contains("Colossal Dreadmaw"), "the solver's library was shown");
        Assert.assertFalse(system.contains("Plains"), "the opponent's library was shown");
        LlmSpikeGui.envOverride = Map.of("DECKLIST", "0");
        try {
            Assert.assertEquals(LlmSpikePuzzle.solverPlan(sections), "", "DECKLIST=0 keeps the solver's cards out");
        } finally { LlmSpikeGui.envOverride = Map.of(); }
    }

    /** Who records the text of its own cards first seen after setup (YOUR CARDS SINCE THE START): the puzzle's solver,
     *  and no one else. Not a puzzle opponent's model seat (it is not the solver), not the solver under DECKLIST=0
     *  (the opt-out from own-card text), not a game seat (no solver named). */
    @Test
    public void onlyThePuzzleSolverTracksItsLaterCards() throws Exception {
        Map<String, List<String>> sections = FileSection.parseSections(
                FileUtil.readFile(new File(REPO, "eval/puzzles/hard/gen2/splice2_325cd33e0a57.pzl")));
        LobbyPlayer solver = LlmSpikePuzzle.solverSeat(sections, "llm", NO_MODEL, NO_MODEL);
        LobbyPlayer opponent = LlmSpikeGui.seatFor("llm", "Opponent", "", NO_MODEL, NO_MODEL);
        LlmSpikeGui.puzzleSolverSeat = solver;
        Assert.assertTrue(LlmSpikeGui.tracksOwnCards(LlmSpikeGui.isPuzzleSolver(solver)), "the solver");
        Assert.assertFalse(LlmSpikeGui.tracksOwnCards(LlmSpikeGui.isPuzzleSolver(opponent)), "the puzzle opponent's seat");
        LlmSpikeGui.envOverride = Map.of("DECKLIST", "0");
        try {
            Assert.assertFalse(LlmSpikeGui.tracksOwnCards(LlmSpikeGui.isPuzzleSolver(solver)), "the solver under DECKLIST=0");
        } finally { LlmSpikeGui.envOverride = Map.of(); }
        LlmSpikeGui.puzzleSolverSeat = null;
        Assert.assertFalse(LlmSpikeGui.tracksOwnCards(LlmSpikeGui.isPuzzleSolver(solver)), "a game seat");
    }

    /** Which [state] cards are the solver's to read: its side's zones it can see, by both key spellings (p0, human),
     *  "play" for the battlefield; never the opponent's (p1, ai), its library, a token, or a face-down card in exile
     *  that is not foretold (CR 406.3); a face-down permanent and a foretold card are its own to know. */
    @Test
    public void theSolversCardsAreItsSideItCanSeeAndNoOneElses() {
        Map<String, List<String>> sections = Map.of("state", List.of(
                "p1hand=Lightning Bolt", "aihand=Counterspell", "p1battlefield=Tarmogoyf",
                "humangraveyard=Ponder", "p0play=Grizzly Bears", "p0battlefield=Exalted Angel|FaceDown",
                "p0exile=Brainstorm|FaceDown;Saw It Coming|FaceDown|Foretold", "p0library=Colossal Dreadmaw",
                "p0hand=t:w_1_1_soldier"));
        String plan = LlmSpikePuzzle.solverPlan(sections);
        for (String mine : new String[] {"  Ponder ", "  Grizzly Bears ", "  Exalted Angel ", "  Saw It Coming "})
            Assert.assertTrue(plan.contains(mine), mine + " is the solver's: " + plan);
        for (String not : new String[] {"Lightning Bolt", "Counterspell", "Tarmogoyf", "Brainstorm", "Colossal Dreadmaw", "soldier"})
            Assert.assertFalse(plan.contains(not), not + " is not the solver's to read: " + plan);
    }

    /** The game seat's YOUR CARDS form, byte for byte: the puzzle's type-line form must not have moved it. */
    @Test
    public void theGameSeatsCardTextIsUnchanged() {
        Deck d = new Deck();
        d.getMain().add(forge.StaticData.instance().getCommonCards().getCard("Lightning Bolt"));
        d.getMain().add(forge.StaticData.instance().getCommonCards().getCard("Mountain"));
        Assert.assertEquals(LlmSpikeGui.cardText(d),
                "  Lightning Bolt {R} — Lightning Bolt deals 3 damage to any target.\n  Mountain no cost — ({T}: Add {R}.)\n");
    }

    /** Gitaxian Probe's script tells Forge's AI never to pay its {U/P} with life (AIPhyrexianPayment$ Never), and the
     *  seat's menu asked the AI's payment check: with no blue source the Probe was "(not castable now)" and never on
     *  the menu, though 2 life pays it (hard draft h_0, its written line drifted there, 2026-10-20). Mutagenic Growth,
     *  with no such hint, was offered beside it. A legal play is never hidden. */
    @Test
    public void aPhyrexianSpellTheAiWillNotPayLifeForIsStillTheSeats() throws Exception {
        Game game = boardOf("../hard/h_0.pzl");
        Player solver = game.getPlayers().get(0);
        List<String> offered = new ArrayList<>();
        for (SpellAbility sa : LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(solver, true, true).spells)
            offered.add(sa.getHostCard().getName());
        Assert.assertTrue(offered.contains("Mutagenic Growth"), "the control: " + offered);
        Assert.assertTrue(offered.contains("Gitaxian Probe"), "Probe is payable with 2 life: " + offered);
        Card probe = null;
        for (Card c : solver.getZone(ZoneType.Hand)) if ("Gitaxian Probe".equals(c.getName())) probe = c;
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.castTag(solver, probe,
                LlmSpikeGui.PlayerControllerLLM.untappedManaTally(solver)), "(castable)");
        Assert.assertEquals(probe.getFirstSpellAbility().getParam("AIPhyrexianPayment"), "Never",
                "the hint is the AI's to keep: the check hides it, never deletes it");
    }

    /** The Puzzle Goal card's text is Forge's whole header, so `inspect` on it read RP_443's "Goal: Survive" and its
     *  name, "Bolster picks the least toughness", that the <puzzle> block no longer shows (epoch 67). */
    @Test
    public void aRulesPuzzlesGoalCardReadsAsItsBrief() throws Exception {
        Game game = boardOf("RP_443.pzl");
        Card goal = null;
        for (Player p : game.getPlayers())
            for (Card c : p.getZone(ZoneType.Command)) if ("Puzzle Goal".equals(c.getName())) goal = c;
        Assert.assertNotNull(goal, "the goal card is installed with the board");
        String text = goal.getOracleText();
        Assert.assertTrue(text.contains("Reduce the opponent to 0 life this turn."), text);
        for (String hidden : new String[] {"Survive", "Difficulty", "Bolster picks"})
            Assert.assertFalse(text.contains(hidden), hidden + " is on the goal card: " + text);
        Assert.assertEquals(LlmSpikeGui.puzzleBrief, text, "the <puzzle> block and the card say one thing");
    }

    /** Forge's HumanControl warning is the one line of its header a rules puzzle keeps: the solver then decides for
     *  the opponent too, and must be told (pr-review of PR #273). Forge's own text, so a change to it shows here. */
    @Test
    public void aHumanControlledRulesPuzzleKeepsForgesWarning() throws Exception {
        Map<String, List<String>> s = FileSection.parseSections(FileUtil.readFile(new File(REPO,
                "eval/puzzles/rules-pilot/RP_443.pzl")));
        s.get("metadata").add("HumanControl:True");
        String forge = new Puzzle(s, "RP_443", false).getGoalDescription();
        String brief = LlmSpikePuzzle.seatBrief(s, forge);
        Assert.assertTrue(forge.contains("making all decisions for your opponent"), forge);
        Assert.assertTrue(brief.startsWith(forge.substring(0, forge.indexOf("Rules pilot 443"))), brief);
        Assert.assertTrue(brief.contains("Reduce the opponent to 0 life this turn."), brief);
        for (String hidden : new String[] {"Survive", "Difficulty", "Bolster picks"})
            Assert.assertFalse(brief.contains(hidden), hidden + " reached the seat: " + brief);
    }

    private static Card onBattlefield(Player p, String name) {
        for (Card c : p.getZone(ZoneType.Battlefield)) if (name.equals(c.getName())) return c;
        throw new AssertionError(name + " is not on " + p.getName() + "'s battlefield");
    }

    /** Rules pilot RP_207 (batch 10 re-gate, 2026-10-19): with Pithing Needle|NamedCard:Endbringer on the board, the
     *  search's first rollout activated Endbringer before any mutate, and uniqueness found "ping, then mutate" as a
     *  second solution (rp10/RP_207-search). The Needle the game holds must name exactly Endbringer and stop it. */
    @Test
    public void aPzlNamedCardIsWhatTheNeedleOnTheBattlefieldNames() throws Exception {
        Game game = boardOf("RP_207.pzl");
        Player solver = game.getPlayers().get(0), opp = game.getPlayers().get(1);
        Card needle = onBattlefield(opp, "Pithing Needle");
        Assert.assertEquals(needle.getNamedCards(), List.of("Endbringer"));
        Card endbringer = onBattlefield(solver, "Endbringer");
        SpellAbility ping = null;
        for (SpellAbility sa : endbringer.getSpellAbilities())
            if (sa.isActivatedAbility() && !sa.isManaAbility()) ping = sa;
        Assert.assertNotNull(ping, "Endbringer's ping");
        Assert.assertTrue(StaticAbilityCantBeCast.cantBeActivatedAbility(ping, endbringer, solver),
                "Pithing Needle naming Endbringer does not stop its ping");
    }

    /** The engine's canPlay() leaves statics out (Forge checks checkRestrictions only as the play is made), so the
     *  seat's menu offered Endbringer's ping under the Needle and the harness played it: RP_207's second "solution"
     *  (rp10/RP_207-search, "ping, then mutate"). */
    @Test
    public void anActivationAStaticForbidsIsNotOffered() {
        Game game = SeatObservationTest.bareGame();
        Player me = game.getPlayers().get(0), rival = game.getPlayers().get(1);
        Card endbringer = SeatObservationTest.card("Endbringer", me, ZoneType.Battlefield);
        endbringer.setSickness(false);
        SpellAbility ping = null;
        for (SpellAbility sa : endbringer.getSpellAbilities())
            if (sa.isActivatedAbility() && !sa.isManaAbility() && sa.getPayCosts().getCostMana() == null) ping = sa;
        Assert.assertNotNull(ping, "Endbringer's {T} ping");
        ping.setActivatingPlayer(me);
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.offerable(ping, me, false), "control: no Needle, offered");
        Card needle = SeatObservationTest.card("Pithing Needle", rival, ZoneType.Battlefield);
        needle.addNamedCard("Endbringer");
        Assert.assertFalse(ping.checkRestrictions(me), "fixture: the Needle forbids the ping");
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.offerable(ping, me, false),
                "the seat is offered an activation Pithing Needle forbids");
    }

    /** The same gap for casting: Gaddock Teeg forbids noncreature spells with {X}, and canPlay() does not ask it. */
    @Test
    public void aSpellAStaticForbidsIsNotOffered() {
        Game game = SeatObservationTest.bareGame();
        Player me = game.getPlayers().get(0), rival = game.getPlayers().get(1);
        for (int i = 0; i < 4; i++) SeatObservationTest.card("Mountain", me, ZoneType.Battlefield);
        SeatObservationTest.card("Gaddock Teeg", rival, ZoneType.Battlefield);
        Card fireball = SeatObservationTest.card("Fireball", me, ZoneType.Hand);
        SpellAbility cast = fireball.getFirstSpellAbility();
        cast.setActivatingPlayer(me);
        Assert.assertFalse(cast.checkRestrictions(me), "fixture: Gaddock Teeg forbids Fireball");
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.offerable(cast, me, false),
                "the seat is offered a spell Gaddock Teeg forbids");
    }

    /** The same board through Match.startGame with the runner's hook: the game that starts after it must still hold
     *  the .pzl's name (the search arm saw the ping offered, so the names changed after the hook). */
    @Test
    public void theNeedlesNameSurvivesTheGameStarting() throws Exception {
        File f = new File(REPO,
                "eval/puzzles/rules-pilot/RP_207.pzl");
        Puzzle puzzle = new Puzzle(FileSection.parseSections(FileUtil.readFile(f)), "RP_207", false);
        List<RegisteredPlayer> players = new ArrayList<>();
        LobbyPlayer solverSeat = LlmSpikeGui.seatFor("pass", "Solver", "", NO_MODEL, NO_MODEL);
        LlmSpikeGui.puzzleSolverSeat = solverSeat;
        RegisteredPlayer a = new RegisteredPlayer(new Deck()).setPlayer(solverSeat);
        a.setStartingHand(0);
        RegisteredPlayer b = new RegisteredPlayer(new Deck()).setPlayer(LlmSpikeGui.seatFor("pass", "Opponent", "",
                NO_MODEL, NO_MODEL));
        b.setStartingHand(0);
        players.add(a);
        players.add(b);
        GameRules rules = new GameRules(GameType.Puzzle);
        rules.setGamesPerMatch(1);
        Match match = new Match(rules, players, "PuzzleSetupTest");
        Game game = match.createGame();
        List<String> afterHook = new ArrayList<>();
        Thread t = new Thread(() -> match.startGame(game, () -> {
            PuzzleArms.SETUP = true;
            try { puzzle.applyToGame(game); } finally { PuzzleArms.SETUP = false; }
            LlmSpikePuzzle.restoreNamedCards(puzzle);
            afterHook.addAll(onBattlefield(game.getPlayers().get(1), "Pithing Needle").getNamedCards());
        }), "Game-PuzzleSetupTest");
        t.start();
        t.join(60000);
        Assert.assertFalse(t.isAlive(), "the pass-arm game outlived 60 s");
        Assert.assertEquals(afterHook, List.of("Endbringer"), "after the hook");
        Assert.assertEquals(onBattlefield(game.getRegisteredPlayers().get(1), "Pithing Needle").getNamedCards(),
                List.of("Endbringer"), "after the game");
    }

    /** Restrictions are about the spell on the stack (review of epoch 63): asked about the card in hand with no cast zone,
     *  Drannith Magistrate's "not cast from their hand" matched every hand spell and the menu lost them all. A spell
     *  cast from hand under an opponent's Magistrate is legal. */
    @Test
    public void aHandSpellUnderAnOpponentsDrannithMagistrateIsOffered() {
        Game game = SeatObservationTest.bareGame();
        Player me = game.getPlayers().get(0), rival = game.getPlayers().get(1);
        SeatObservationTest.card("Mountain", me, ZoneType.Battlefield);
        SeatObservationTest.card("Drannith Magistrate", rival, ZoneType.Battlefield);
        SpellAbility bolt = SeatObservationTest.card("Lightning Bolt", me, ZoneType.Hand).getFirstSpellAbility();
        bolt.setActivatingPlayer(me);
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.offerable(bolt, me, false),
                "a legal cast from hand is hidden under the opponent's Drannith Magistrate");
    }

    /** And the other way: Grafdigger's Cage forbids casting from a graveyard, which the card in the graveyard with no
     *  cast zone never matched, so a flashback cast was still offered. */
    @Test
    public void aFlashbackCastUnderGrafdiggersCageIsNotOffered() {
        Game game = SeatObservationTest.bareGame();
        Player me = game.getPlayers().get(0), rival = game.getPlayers().get(1);
        SeatObservationTest.card("Mountain", me, ZoneType.Battlefield);
        SeatObservationTest.card("Mountain", me, ZoneType.Battlefield);
        Card dart = SeatObservationTest.card("Lava Dart", me, ZoneType.Graveyard);
        SpellAbility flashback = null;
        for (SpellAbility sa : dart.getAllPossibleAbilities(me, false)) if (sa.isFlashback()) flashback = sa;
        Assert.assertNotNull(flashback, "Lava Dart's flashback");
        flashback.setActivatingPlayer(me);
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.offerable(flashback, me, true), "control: no Cage, offered");
        SeatObservationTest.card("Grafdigger's Cage", rival, ZoneType.Battlefield);
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.offerable(flashback, me, true),
                "a flashback cast Grafdigger's Cage forbids is offered");
    }
}
