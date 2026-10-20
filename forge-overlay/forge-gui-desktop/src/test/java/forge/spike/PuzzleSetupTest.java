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
    /** The zero-model spec the baseline JVMs use (eval/puzzle_baselines.py): the pass arm never calls it. */
    private static final String NO_MODEL = "openrouter:zero-model/none";
    /** The repo root: tests run from the Forge checkout's forge-gui-desktop, so only the rules file's -D path
     *  ($REPO/data/rules/MagicCompRules.txt, set by acceptance) names it. */
    private static final File REPO = repoRoot();

    private static File repoRoot() {
        String rules = System.getProperty("harness.rules.file");
        if (rules == null) throw new IllegalStateException("-Dharness.rules.file is not set; the repo root comes from it");
        return new File(rules).getAbsoluteFile().getParentFile().getParentFile().getParentFile();
    }

    /** Both statics the runner sets: a brief left behind turns every later test's system prompt into a puzzle's
     *  (SeatObservationTest's non-puzzle framing failed after this class, pr-review of PR #273). */
    @AfterMethod
    public void clearSolverSeat() { LlmSpikeGui.puzzleSolverSeat = null; LlmSpikeGui.puzzleBrief = null; }

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

    /** The board applied on a thread named "Game..." (GameState.applyToGame runs inline only there) by the runner's
     *  own start-hook method: the board, the NamedCard restore and the goal card's brief. */
    private static Game boardOf(String pzl) throws Exception {
        File f = new File(REPO,
                "eval/puzzles/rules-pilot/" + pzl);
        Map<String, List<String>> sections = FileSection.parseSections(FileUtil.readFile(f));
        Puzzle puzzle = new Puzzle(sections, pzl, false);
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
        return game;
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
