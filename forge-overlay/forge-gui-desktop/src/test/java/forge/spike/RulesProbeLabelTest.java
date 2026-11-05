package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.zip.GZIPOutputStream;

import forge.GuiDesktop;
import forge.LobbyPlayer;
import forge.ai.LobbyPlayerAi;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameActionUtil;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.phase.PhaseHandler;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.gamemodes.puzzle.Puzzle;
import forge.gui.GuiBase;
import forge.item.PaperCard;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.FileSection;
import forge.util.FileUtil;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.LineFindingProbeTest.Recorder;

/**
 * The rules probe's engine labeller (eval/research/2026-11-05-rules-probe.md, section 6): a .pzl board is put down as
 * the puzzle runner puts it, and every proposal the solver could make at that priority (every visible card x every
 * verb, each ability number, each way to cast it the card has and some it has not, each target) is labelled by the
 * PROPOSE_ACTIONS checker the propose arm uses: accepted is legal, "ILLEGAL (category): reason" is the engine's category
 * and sentence, a FORMAT error is dropped (it is about how the proposal is written, not the rules). Near-miss variants
 * change one line of the board (the phase, whose turn, a land played, a tapped land, a sick creature, a hexproof
 * creature or a Leyline for the opponent, a split second spell on the stack); each variant is labelled by the engine,
 * never by the edit, so an edit that does not break a play gives a legal item beside the base board's.
 *
 * With -Dharness.probe.out=FILE (".gz" compresses) the export test labels the boards under eval/puzzles (or
 * -Dharness.probe.boards, comma-separated paths under the repository) and writes one JSONL row per candidate plus one
 * state row per board variant; `python3 eval/rules_probe.py engine FILE --out ITEMS` turns them into probe items.
 * Without it the export test returns at once, so the suite is unaffected (acceptance fails a skipped test).
 * A JVM keeps something of every board it builds: with -Xmx2g it ran out of heap after about 375 of the 1,376 boards
 * (each with up to nine variants), so the export runs as shards (-Dharness.probe.shard=K/6), one JVM after another.
 * It cannot see a Forge rules bug: the label is Forge's.
 */
public class RulesProbeLabelTest {
    static final String SCHEMA = "rules-probe-engine-raw-v1";
    private static final String NO_MODEL = "openrouter:zero-model/none";
    /** Ways to cast that a card may lack: named on every non-land card, so a cast way the card does not have is an
     *  item (Flashback on Desperate Ritual was a refusal a model made, runs/propose/e75-haiku-hard-propose). */
    static final List<String> WRONG_WAYS = List.of("Flashback", "Evoke", "alternative", "flash");
    /** The verdict of an activation the checker accepted for another card's menu entry: matchProposal lets a menu mana
     *  ability stand for every copy of the card's name ("another copy's entry stands for this one"), whatever the copy's
     *  zone or tapped state, so a tapped Mountain or one in hand reads as legal beside an untapped one. No label. */
    static final String SUBSTITUTED = "SUBSTITUTED:";
    private static final Pattern ILLEGAL = Pattern.compile("^ILLEGAL \\(([^)]+)\\): (.*)$", Pattern.DOTALL);

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

    @BeforeMethod
    public void on() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        AnswerTool.PROPOSE_FOR_TEST = true;
    }

    @AfterMethod(alwaysRun = true)
    public void off() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        AnswerTool.PROPOSE_FOR_TEST = null;
        LlmSpikeGui.puzzleSolverSeat = null;
        LlmSpikeGui.puzzleBrief = null;
    }

    // ------------------------------------------------------------------ the labeller

    /** One labelled candidate: the proposal, the checker's verdict (null = legal) and what the row needs. */
    static final class Labelled {
        AnswerTool.Proposal p;
        String name, where, targetName, targetWhere, verdict;
        boolean manaAbility;
        String label() {
            return verdict == null ? "legal" : verdict.startsWith("ILLEGAL") ? "illegal"
                    : verdict.startsWith(SUBSTITUTED) ? "substituted" : "format";
        }
        String category() {
            java.util.regex.Matcher m = verdict == null ? null : ILLEGAL.matcher(verdict);
            return m != null && m.matches() ? m.group(1) : null;
        }
        String reason() {
            java.util.regex.Matcher m = verdict == null ? null : ILLEGAL.matcher(verdict);
            return m != null && m.matches() ? m.group(2) : null;
        }
        /** The play without handles, so the same play on two variants of a board pairs up: names with their zone and
         *  side (a Swamp in hand and one on the battlefield are two plays). */
        String signature() {
            return p.verb + "|" + name + "@" + where + "|" + p.ability + "|" + p.face + "|" + p.cost + "|"
                    + (targetName == null ? null : targetName + "@" + targetWhere);
        }
    }

    /** A board put down: the game, the solver's seat on it and the solver. */
    static final class Board {
        Game game; Player me; LlmSpikeGui.PlayerControllerLLM ctrl; boolean ownMain; String state;
    }

    /** The board of {@code sections} applied on a "Game-" thread (GameState.applyToGame runs inline only there), with
     *  the runner's own start hook (PuzzleSetupTest.boardOf), and a PROPOSE seat for the solver (p0). */
    static Board boardOf(Map<String, List<String>> sections, String name) throws Exception {
        Puzzle puzzle = new Puzzle(sections, name, false);
        List<RegisteredPlayer> players = new ArrayList<>();
        LobbyPlayer solverSeat = LlmSpikeGui.seatFor("pass", "Solver", "", NO_MODEL, NO_MODEL);
        LlmSpikeGui.puzzleSolverSeat = solverSeat;
        players.add(new RegisteredPlayer(new Deck()).setPlayer(solverSeat));
        players.add(new RegisteredPlayer(new Deck()).setPlayer(LlmSpikeGui.seatFor("pass", "Opponent", "", NO_MODEL, NO_MODEL)));
        Match match = new Match(new GameRules(GameType.Puzzle), players, "RulesProbeLabelTest");
        Game game = match.createGame();
        Throwable[] thrown = {null};
        Thread t = new Thread(() -> {
            try {
                LlmSpikePuzzle.briefSolver(sections, puzzle);
                LlmSpikePuzzle.applyBoard(game, puzzle);
            } catch (Throwable e) {
                thrown[0] = e;
            }
        }, "Game-RulesProbeLabelTest");
        t.start();
        t.join(60000);
        if (thrown[0] != null) throw new IllegalStateException("board did not apply: " + thrown[0], thrown[0]);
        if (t.isAlive()) throw new IllegalStateException("board did not apply in 60 s");
        Board b = new Board();
        b.game = game;
        b.me = game.getPlayers().get(0);
        // The seat is named as its player, as in a game: the state's header names the active player and its YOU line
        // the seat, and a reader of the state (rules_probe.parse_window) tells whose turn it is by comparing them.
        Recorder model = new Recorder("CHOICE: 1");
        b.ctrl = new LlmSpikeGui.PlayerControllerLLM(game, b.me, new LobbyPlayerAi(b.me.getName(), null),
                b.me.getName(), "", model, model, "llm");
        b.me.dangerouslySetController(b.ctrl);
        PhaseHandler phh = game.getPhaseHandler();
        // The question is what the solver may do with priority; the checker does not ask who holds it, so a board put
        // down on the opponent's turn, or under the opponent's spell, gives it to the solver (as the opponent passing).
        phh.setPriority(b.me);
        b.ownMain =phh.isPlayerTurn(b.me) && (phh.is(PhaseType.MAIN1) || phh.is(PhaseType.MAIN2));
        b.state = b.ctrl.snapshot();
        return b;
    }

    /** The objects the seat can name: a card with a current handle that the state shows, not a face-down card and not
     *  an engine effect object (the puzzle's goal, "Keyword Effects" holding a player's hexproof). */
    static List<Card> visible(Board b) {
        List<Card> out = new ArrayList<>();
        SeatObservation obs = b.ctrl.observer();
        for (Card c : b.game.getCardsInGame()) {
            if (c.isImmutable() || c.isFaceDown()) continue;
            int h = obs.handleOf(c);
            if (h < 0 || obs.currentCard(h) != c) continue;
            if (!Pattern.compile("@" + h + "(?!\\d)").matcher(b.state).find()) continue;
            out.add(c);
        }
        return out;
    }

    /** Where a card is, as a signature names it: "hand/you", "battlefield/opp". */
    static String where(Card c, Player me) {
        String z = c.getZone() == null ? "none" : c.getZone().getZoneType().name().toLowerCase(java.util.Locale.ROOT);
        return z + "/" + (c.getController() == me ? "you" : "opp");
    }

    private static AnswerTool.Proposal prop(String verb, int card, Integer ability, String face, String cost) {
        AnswerTool.Proposal p = new AnswerTool.Proposal();
        p.verb = verb; p.card = card; p.ability = ability; p.face = face; p.cost = cost;
        return p;
    }

    /** Every candidate proposal at the board's priority, labelled. FORMAT verdicts are kept here (the writer drops
     *  them) so a test can count them. */
    static List<Labelled> label(Board b) {
        List<Card> cards = visible(b);
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(b.me, b.ownMain, true);
        int passIdx = 1 + po.spells.size() + po.landPlays.size();
        SeatObservation obs = b.ctrl.observer();
        List<Labelled> out = new ArrayList<>();
        for (Card c : cards) {
            int h = obs.handleOf(c);
            List<AnswerTool.Proposal> base = new ArrayList<>();
            List<Boolean> mana = new ArrayList<>();
            base.add(prop("play_land", h, null, null, null)); mana.add(false);
            base.add(prop("cast", h, null, null, null)); mana.add(false);
            if (!c.isLand() || c.isModal()) {
                for (String way : castWays(c, b.me)) { base.add(prop("cast", h, null, null, way)); mana.add(false); }
                for (String face : faces(c)) { base.add(prop("cast", h, null, face, null)); mana.add(false); }
            }
            List<SpellAbility> abs = LlmSpikeGui.PlayerControllerLLM.proposalAbilities(c);
            if (abs.isEmpty()) { base.add(prop("activate", h, null, null, null)); mana.add(false); }
            for (int i = 0; i < abs.size(); i++) { base.add(prop("activate", h, i + 1, null, null)); mana.add(abs.get(i).isManaAbility()); }
            for (int i = 0; i < base.size(); i++) {
                AnswerTool.Proposal p = base.get(i);
                Labelled l = check(b, po, passIdx, p, c, null, null, mana.get(i));
                if (l == null) continue;
                if (l.verdict != null && l.verdict.startsWith("FORMAT: this play targets")) {
                    // A legal play with one target: each object and player it could name, labelled with it.
                    for (Card t : cards) {
                        AnswerTool.Proposal q = p.copy();
                        q.targets = List.of("@" + obs.handleOf(t));
                        Labelled lt = check(b, po, passIdx, q, c, t.getName(), where(t, b.me), mana.get(i));
                        if (lt != null) out.add(lt);
                    }
                    for (String who : List.of("you", "opponent")) {
                        AnswerTool.Proposal q = p.copy();
                        q.targets = List.of(who);
                        Labelled lt = check(b, po, passIdx, q, c, who, "player", mana.get(i));
                        if (lt != null) out.add(lt);
                    }
                }
                out.add(l);
            }
        }
        return out;
    }

    private static Labelled check(Board b, LlmSpikeGui.PlayerControllerLLM.PlayOptions po, int passIdx,
                                  AnswerTool.Proposal p, Card c, String targetName, String targetWhere, boolean manaAbility) {
        Labelled l = new Labelled();
        l.p = p; l.name = c.getName(); l.where = where(c, b.me); l.targetName = targetName; l.targetWhere = targetWhere;
        l.manaAbility = manaAbility;
        int[] hit = {-1};
        try {
            l.verdict = b.ctrl.matchProposal(p.copy(), po.spells, po.landPlays, po.landCards, passIdx, b.me, b.ownMain, hit);
            if (l.verdict == null && "activate".equals(p.verb) && hit[0] >= 1 && hit[0] <= po.spells.size()
                    && po.spells.get(hit[0] - 1).getHostCard() != c)
                l.verdict = SUBSTITUTED + " the menu entry of " + po.spells.get(hit[0] - 1).getHostCard();
        } catch (Exception e) {
            return null;   // the checker cannot judge it: no label rather than a guessed one
        } finally {
            b.ctrl.proposedTarget = null;
        }
        return l;
    }

    /** The ways to cast {@code c} it has (its alternative and optional costs, by the name a proposal gives them) and
     *  the ones of WRONG_WAYS it lacks. */
    static Set<String> castWays(Card c, Player me) {
        Set<String> ways = new LinkedHashSet<>();
        try {
            for (SpellAbility o : c.getAllSpellAbilities()) {
                if (!o.isSpell()) continue;
                List<SpellAbility> all = new ArrayList<>();
                all.add(o);
                try { all.addAll(GameActionUtil.getAlternativeCosts(o, me, false)); } catch (Exception ignore) { }
                for (SpellAbility s : all) {
                    String w = LlmSpikeGui.PlayerControllerLLM.castWay(s);
                    if (w != null) ways.addAll(Arrays.asList(w.split(", ")));
                }
            }
        } catch (Exception ignore) { }
        ways.addAll(WRONG_WAYS);
        return ways;
    }

    /** The faces or halves of a card with more than one, by name. */
    static Set<String> faces(Card c) {
        Set<String> out = new LinkedHashSet<>();
        try {
            for (SpellAbility o : c.getAllSpellAbilities()) {
                String f = LlmSpikeGui.PlayerControllerLLM.faceOf(o);
                if (o.isSpell() && f != null && !f.equals(c.getName())) out.add(f);
            }
        } catch (Exception ignore) { }
        return out;
    }

    // ------------------------------------------------------------------ near-miss variants

    /** Each one-line edit of a board's [state] (section 6 item 4), by name; an edit that does not apply to this
     *  board (no land in hand, the phase already a combat step) is left out. */
    static Map<String, List<String>> variants(List<String> state) {
        Map<String, List<String>> out = new LinkedHashMap<>();
        out.put("base", state);
        String phase = value(state, "activephase"), active = value(state, "activeplayer");
        if (phase != null && !phase.toUpperCase().startsWith("COMBAT") && !"BeginCombat".equalsIgnoreCase(phase))
            out.put("combat", with(state, "activephase", "COMBAT_BEGIN"));
        if (active != null && active.matches("(?i)p0|human"))
            out.put("opp-turn", with(state, "activeplayer", "p1"));
        if (value(state, "p0landsplayed") == null && value(state, "humanlandsplayed") == null && anyLand(value(state, "p0hand")))
            out.put("land-played", with(state, "p0landsplayed", "1"));
        String bf = value(state, "p0battlefield");
        String tapped = markFirst(bf, true, "Tapped");
        if (tapped != null) out.put("tapped-land", with(state, "p0battlefield", tapped));
        String sick = markFirst(bf, false, "SummonSick");
        if (sick != null) out.put("summon-sick", with(state, "p0battlefield", sick));
        String opp = value(state, "p1battlefield");
        out.put("opp-hexproof", with(state, "p1battlefield", opp == null || opp.isEmpty() ? "Gladecover Scout" : opp + ";Gladecover Scout"));
        out.put("opp-leyline", with(state, "p1battlefield", opp == null || opp.isEmpty() ? "Leyline of Sanctity" : opp + ";Leyline of Sanctity"));
        if (value(state, "p1putonstack") == null && value(state, "p0putonstack") == null)
            out.put("split-second", with(state, "p1putonstack", "Angel's Grace"));
        return out;
    }

    static String value(List<String> state, String key) {
        for (String line : state) {
            String[] kv = line.split("=", 2);
            if (kv.length == 2 && kv[0].trim().equalsIgnoreCase(key)) return kv[1].trim();
        }
        return null;
    }

    /** {@code state} with {@code key=v}: the line replaced, or added. */
    static List<String> with(List<String> state, String key, String v) {
        List<String> out = new ArrayList<>();
        boolean done = false;
        for (String line : state) {
            String[] kv = line.split("=", 2);
            if (kv.length == 2 && kv[0].trim().equalsIgnoreCase(key)) { out.add(key + "=" + v); done = true; }
            else out.add(line);
        }
        if (!done) out.add(key + "=" + v);
        return out;
    }

    private static PaperCard paper(String entry) {
        String n = entry.split("\\|", 2)[0].trim();
        try { return FModel.getMagicDb().getCommonCards().getCard(n); } catch (Exception e) { return null; }
    }

    static boolean anyLand(String zone) {
        if (zone == null) return false;
        for (String e : zone.split(";")) { PaperCard pc = paper(e); if (pc != null && pc.getRules().getType().isLand()) return true; }
        return false;
    }

    /** The zone list with {@code flag} added to its first land (land) or first creature (otherwise) without it. */
    static String markFirst(String zone, boolean land, String flag) {
        if (zone == null || zone.isEmpty()) return null;
        String[] es = zone.split(";");
        for (int i = 0; i < es.length; i++) {
            PaperCard pc = paper(es[i]);
            if (pc == null || es[i].contains("|" + flag) || es[i].contains("|Tapped") && land) continue;
            boolean is = land ? pc.getRules().getType().isLand() : pc.getRules().getType().isCreature() && !pc.getRules().getType().isLand();
            if (!is) continue;
            es[i] = es[i] + "|" + flag;
            return String.join(";", es);
        }
        return null;
    }

    // ------------------------------------------------------------------ the rows

    static JsonObject row(String board, String variant, String stateId, Labelled l) {
        JsonObject r = new JsonObject();
        r.addProperty("schema", SCHEMA);
        r.addProperty("board", board);
        r.addProperty("variant", variant);
        r.addProperty("state_id", stateId);
        JsonObject p = new JsonObject();
        p.addProperty("verb", l.p.verb);
        p.addProperty("card", "@" + l.p.card);
        p.addProperty("name", l.name);
        if (l.p.ability != null) p.addProperty("ability", l.p.ability);
        if (l.p.face != null) p.addProperty("face", l.p.face);
        if (l.p.cost != null) p.addProperty("cost", l.p.cost);
        if (l.p.targets != null) {
            JsonArray ts = new JsonArray();
            for (String t : l.p.targets) ts.add(t);
            p.add("targets", ts);
            JsonArray tn = new JsonArray();
            tn.add(l.targetName);
            p.add("target_names", tn);
        }
        r.add("proposal", p);
        r.addProperty("label", l.label());
        r.addProperty("category", l.category());
        r.addProperty("reason", l.reason());
        r.addProperty("mana_ability", l.manaAbility);
        r.addProperty("zone", l.where);
        r.addProperty("signature", l.signature());
        return r;
    }

    static String sha16(String s) {
        try {
            byte[] d = java.security.MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder b = new StringBuilder();
            for (int i = 0; i < 8; i++) b.append(String.format("%02x", d[i]));
            return b.toString();
        } catch (Exception e) { throw new IllegalStateException(e); }
    }

    /** The repository root, from the rules file's -D path ($REPO/data/rules/MagicCompRules.txt), as PuzzleSetupTest. */
    static File repoRoot() {
        String rules = System.getProperty("harness.rules.file");
        if (rules == null) throw new IllegalStateException("-Dharness.rules.file is not set; the repo root comes from it");
        return new File(rules).getAbsoluteFile().getParentFile().getParentFile().getParentFile();
    }

    static List<File> boardFiles(File repo, String spec) {
        List<File> out = new ArrayList<>();
        for (String s : spec.split(",")) {
            File f = new File(repo, s.trim());
            if (f.isDirectory()) collect(f, out);
            else if (f.getName().endsWith(".pzl")) out.add(f);
        }
        out.sort((a, b) -> a.getPath().compareTo(b.getPath()));
        return out;
    }

    private static void collect(File dir, List<File> out) {
        File[] fs = dir.listFiles();
        if (fs == null) return;
        for (File f : fs) { if (f.isDirectory()) collect(f, out); else if (f.getName().endsWith(".pzl")) out.add(f); }
    }

    // ------------------------------------------------------------------ always on: one small built board

    static final String SMALL_BOARD = String.join("\n",
            "[metadata]", "Name:Rules probe label check", "Goal:Win", "Turns:1", "Difficulty:Easy",
            "Description:A built board for the labeller's own test.",
            "[state]", "turn=1", "activeplayer=p0", "activephase=MAIN1", "p0life=20",
            "p0hand=Lightning Bolt;Grizzly Bears;Forest",
            "p0battlefield=Mountain;Mountain|Tapped",
            "p0graveyard=Faithless Looting",
            "p0library=Plains;Plains;Plains",
            "p1life=20",
            "p1battlefield=Gladecover Scout;Hill Giant",
            "p1library=Plains;Plains;Plains");

    private static Labelled find(List<Labelled> ls, String verb, String name, String cost, String target) {
        for (Labelled l : ls)
            if (l.p.verb.equals(verb) && l.name.equals(name) && java.util.Objects.equals(l.p.cost, cost)
                    && java.util.Objects.equals(l.targetName, target)) return l;
        Assert.fail("no candidate " + verb + " " + name + " cost=" + cost + " target=" + target);
        return null;
    }

    private static void assertIllegal(Labelled l, String category) {
        Assert.assertEquals(l.label(), "illegal", l.signature() + ": " + l.verdict);
        Assert.assertEquals(l.category(), category, l.signature() + ": " + l.verdict);
    }

    /** One built board, labelled: the legal plays are legal, and each illegal one carries the category the checker
     *  gives it. It fails when the candidates miss a verb, a cast way or a target, or when the label stops being the
     *  checker's (an always-legal labeller fails the illegal asserts, an always-illegal one the legal asserts). */
    @Test
    public void aBuiltBoardIsLabelledByTheProposeChecker() throws Exception {
        Map<String, List<String>> s = FileSection.parseSections(Arrays.asList(SMALL_BOARD.split("\n")));
        Board b = boardOf(s, "probe-check");
        Assert.assertFalse(b.state.contains("(castable"), "the state is the propose seat's, without hand tags: " + b.state);
        Assert.assertTrue(b.state.contains("(active: Solver |") && b.state.contains("\nYOU [Solver]"),
                "the header's active player and the YOU line name the same seat: " + b.state);
        List<Labelled> ls = label(b);
        Assert.assertEquals(find(ls, "cast", "Lightning Bolt", null, "opponent").label(), "legal");
        Assert.assertEquals(find(ls, "cast", "Lightning Bolt", null, "Hill Giant").label(), "legal");
        Labelled hex = find(ls, "cast", "Lightning Bolt", null, "Gladecover Scout");
        assertIllegal(hex, "target");
        Assert.assertTrue(hex.reason().toLowerCase().contains("hexproof"), hex.verdict);
        Assert.assertEquals(find(ls, "play_land", "Forest", null, null).label(), "legal");
        assertIllegal(find(ls, "cast", "Grizzly Bears", null, null), "cost");
        assertIllegal(find(ls, "cast", "Lightning Bolt", "Flashback", null), "cost");
        assertIllegal(find(ls, "cast", "Faithless Looting", "Flashback", null), "cost");
        assertIllegal(find(ls, "cast", "Forest", null, null), "type");
        assertIllegal(find(ls, "play_land", "Mountain", null, null), "zone");
        assertIllegal(find(ls, "cast", "Hill Giant", null, null), "zone");
        int tapped = -1;
        for (Card c : b.me.getCardsIn(ZoneType.Battlefield)) if (c.isTapped()) tapped = b.ctrl.observer().handleOf(c);
        final int th = tapped;
        Assert.assertTrue(th >= 0, "the board's tapped Mountain");
        Assert.assertTrue(ls.stream().anyMatch(l -> l.p.verb.equals("activate") && l.name.equals("Mountain") && l.p.card != th
                && "legal".equals(l.label()) && l.manaAbility), "the untapped Mountain's mana ability");
        // The checker lets an untapped copy's menu entry stand for a mana ability of the same name; a tapped Mountain's
        // ability is not legal, so that acceptance is no label at all.
        Assert.assertTrue(ls.stream().noneMatch(l -> l.p.verb.equals("activate") && l.p.card == th && "legal".equals(l.label())),
                "the tapped Mountain's {T} ability is labelled legal");
        assertIllegal(find(ls, "activate", "Grizzly Bears", null, null), "type");
        Assert.assertTrue(ls.stream().noneMatch(l -> l.verdict != null && l.verdict.startsWith("FORMAT: no object")),
                "every candidate names an object in view");
    }

    /** The near misses change the label of the play the edit breaks, and only by the engine's verdict: the Forest is
     *  legal on the base board, a land drop error once a land is played; the Bolt is legal, a timing error under a
     *  split second spell; a sorcery-speed creature in combat is timing. */
    @Test
    public void aOneLineVariantIsLabelledByTheEngineNotByTheEdit() throws Exception {
        Map<String, List<String>> s = FileSection.parseSections(Arrays.asList(SMALL_BOARD.split("\n")));
        Map<String, List<String>> vs = variants(s.get("state"));
        Assert.assertTrue(vs.keySet().containsAll(List.of("base", "combat", "opp-turn", "land-played", "tapped-land",
                "opp-hexproof", "opp-leyline", "split-second")), String.valueOf(vs.keySet()));
        Assert.assertFalse(vs.containsKey("summon-sick"), "no creature of the solver's is on the battlefield");
        Map<String, List<Labelled>> by = new LinkedHashMap<>();
        for (Map.Entry<String, List<String>> v : vs.entrySet()) {
            Map<String, List<String>> sv = new LinkedHashMap<>(s);
            sv.put("state", v.getValue());
            Board b = boardOf(sv, "probe-check-" + v.getKey());
            // The probe asks what the solver may do now, and the checker does not check priority: a state where the
            // opponent holds it (their turn, their split second spell) would make every play illegal by the rules.
            Assert.assertTrue(b.state.contains("| priority: you)"), v.getKey() + ": the solver holds priority: " + b.state);
            by.put(v.getKey(), label(b));
            off(); on();
        }
        assertIllegal(find(by.get("land-played"), "play_land", "Forest", null, null), "land drop");
        assertIllegal(find(by.get("combat"), "play_land", "Forest", null, null), "timing");
        assertIllegal(find(by.get("split-second"), "cast", "Lightning Bolt", null, null), "timing");
        assertIllegal(find(by.get("tapped-land"), "cast", "Lightning Bolt", null, null), "cost");
        Assert.assertEquals(find(by.get("combat"), "cast", "Lightning Bolt", null, "opponent").label(), "legal",
                "an instant in combat is the contrast item of the edit");
        assertIllegal(find(by.get("opp-leyline"), "cast", "Lightning Bolt", null, "opponent"), "target");
        // The Leyline's player hexproof is held by an engine effect object ("Keyword Effects", command zone) that the
        // state lists; an effect is not a card the seat can play or target, so it is never a candidate.
        Assert.assertTrue(by.get("opp-leyline").stream().noneMatch(l -> "Keyword Effects".equals(l.name)
                || "Keyword Effects".equals(l.targetName)), "an engine effect object is a candidate");
        Assert.assertEquals(find(by.get("opp-hexproof"), "cast", "Lightning Bolt", null, "Hill Giant").label(), "legal");
    }

    /** -Dharness.probe.out=FILE: label every board (or -Dharness.probe.boards; -Dharness.probe.shard=K/N takes every
     *  Nth from the Kth; -Dharness.probe.limit caps the boards; -Dharness.probe.variants=base for the base boards only)
     *  and write the rows. Unset: nothing to do, and the test passes, so the normal suite neither skips nor runs it. */
    @Test
    public void exportLabelsTheBoardsWhenAnOutputIsNamed() throws Exception {
        String outPath = System.getProperty("harness.probe.out");
        if (outPath == null || outPath.isEmpty()) return;
        File repo = repoRoot();
        List<File> files = boardFiles(repo, System.getProperty("harness.probe.boards", "eval/puzzles"));
        String[] shard = System.getProperty("harness.probe.shard", "0/1").split("/");
        int k = Integer.parseInt(shard[0]), n = Integer.parseInt(shard[1]);
        int limit = Integer.parseInt(System.getProperty("harness.probe.limit", "1000000"));
        boolean baseOnly = "base".equals(System.getProperty("harness.probe.variants"));
        String epoch = LlmSpikeGui.HARNESS_EPOCH, obsVersion = SeatObservation.VERSION;
        String pin = new String(java.nio.file.Files.readAllBytes(new File(repo, "FORGE_COMMIT").toPath()), StandardCharsets.UTF_8).trim();
        int boards = 0, failed = 0, rows = 0, formats = 0, substituted = 0;
        try (OutputStream os = outPath.endsWith(".gz") ? new GZIPOutputStream(new FileOutputStream(outPath)) : new FileOutputStream(outPath);
             Writer w = new OutputStreamWriter(os, StandardCharsets.UTF_8)) {
            for (int i = 0; i < files.size() && boards < limit; i++) {
                if (i % n != k) continue;
                File f = files.get(i);
                String board = repo.toPath().resolve("eval/puzzles").relativize(f.toPath()).toString().replaceAll("\\.pzl$", "");
                Map<String, List<String>> s;
                try { s = FileSection.parseSections(FileUtil.readFile(f)); } catch (Exception e) { failed++; continue; }
                if (s.get("state") == null) { failed++; continue; }
                boards++;
                for (Map.Entry<String, List<String>> v : variants(s.get("state")).entrySet()) {
                    if (baseOnly && !"base".equals(v.getKey())) continue;
                    Map<String, List<String>> sv = new LinkedHashMap<>(s);
                    sv.put("state", v.getValue());
                    try {
                        Board b = boardOf(sv, board);
                        List<Labelled> ls = label(b);
                        String sid = sha16(b.state);
                        JsonObject st = new JsonObject();
                        st.addProperty("t", "state");
                        st.addProperty("id", sid);
                        st.addProperty("text", b.state);
                        st.addProperty("board", board);
                        st.addProperty("variant", v.getKey());
                        st.addProperty("harness_epoch", epoch);
                        st.addProperty("observation_version", obsVersion);
                        st.addProperty("engine_pin", pin);
                        w.write(st.toString());
                        w.write("\n");
                        for (Labelled l : ls) {
                            if ("format".equals(l.label())) { formats++; continue; }
                            if ("substituted".equals(l.label())) { substituted++; continue; }
                            w.write(row(board, v.getKey(), sid, l).toString());
                            w.write("\n");
                            rows++;
                        }
                    } catch (Exception e) {
                        failed++;
                        System.out.println("[RULES-PROBE] " + board + " " + v.getKey() + " did not label: " + e);
                    } finally {
                        off(); on();
                    }
                }
                if (boards % 25 == 0) { w.flush(); System.out.println("[RULES-PROBE] boards=" + boards + " rows=" + rows); }
            }
        }
        System.out.println("[RULES-PROBE] boards=" + boards + " rows=" + rows + " format_dropped=" + formats + " substituted_dropped=" + substituted + " failed=" + failed + " -> " + outPath);
        Assert.assertTrue(boards > 0, "no board under the named paths");
    }
}
