package forge.spike;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.File;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import forge.game.card.Card;
import forge.game.zone.ZoneType;
import forge.util.FileSection;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.RulesProbeLabelExport.Board;
import static forge.spike.RulesProbeLabelExport.Labelled;
import static forge.spike.RulesProbeLabelExport.boardOf;
import static forge.spike.RulesProbeLabelExport.label;

/**
 * The rules probe's engine labeller (RulesProbeLabelExport) on one built board and its near-miss variants: the labels
 * are the propose checker's, with the categories it gives, and the export writes what rules_probe.py reads. The export
 * of every board is RulesProbeLabelExport's own run, on demand.
 */
public class RulesProbeLabelTest {
    @BeforeClass
    public void initializeModel() { new PuzzleSetupTest().initializeModel(); }

    @BeforeMethod
    public void on() { RulesProbeLabelExport.arm(); }

    @AfterMethod(alwaysRun = true)
    public void off() { RulesProbeLabelExport.disarm(); }

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
        Map<String, List<String>> vs = RulesProbeLabelExport.variants(s.get("state"));
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

    /** The export writes what rules_probe.py `engine` and `engine-report` read: a state row per variant, item rows
     *  naming it, and a closing summary with the drop counts (the numbers the note cites come from it). */
    @Test
    public void theExportWritesStatesItemsAndASummary() throws Exception {
        File dir = Files.createTempDirectory("rules-probe-label").toFile();
        File pzl = new File(dir, "probe-check.pzl");
        Files.write(pzl.toPath(), SMALL_BOARD.getBytes(StandardCharsets.UTF_8));
        StringWriter w = new StringWriter();
        JsonObject sum = RulesProbeLabelExport.export(List.of(pzl), PuzzleSetupTest.repoRoot(), w, 0, 1, 10, true);
        List<JsonObject> rows = new ArrayList<>();
        for (String ln : w.toString().split("\n")) rows.add(JsonParser.parseString(ln).getAsJsonObject());
        JsonObject first = rows.get(0), last = rows.get(rows.size() - 1);
        Assert.assertEquals(first.get("t").getAsString(), "state");
        Assert.assertEquals(first.get("board").getAsString(), "probe-check");
        Assert.assertEquals(last.get("t").getAsString(), "summary", "the last row is the summary");
        Assert.assertEquals(last, sum);
        Assert.assertEquals(sum.get("boards_labelled").getAsInt(), 1);
        Assert.assertEquals(sum.get("rows").getAsInt(), rows.size() - 2, "every row between is an item");
        Assert.assertTrue(sum.get("substituted_dropped").getAsInt() >= 1, "the tapped Mountain's ability: " + sum);
        Assert.assertEquals(rows.get(1).get("schema").getAsString(), RulesProbeLabelExport.SCHEMA);
        Assert.assertEquals(rows.get(1).get("state_id").getAsString(), first.get("id").getAsString());
    }

    /** A relative output lands in the Forge checkout (the JVM runs in its forge-gui-desktop), which install.sh
     *  overwrites; one in the repository outside runs/ (git-ignored, where a run's data lives) is committed by the next
     *  `git add`. Both are refused. */
    @Test
    public void anOutputInsideTheRepositoryOrTheForgeCheckoutIsRefused() {
        File repo = new File("/x/repo"), forge = new File("/x/forge");
        Assert.assertNotNull(RulesProbeLabelExport.outputRefusal("raw.jsonl.gz", repo, forge), "a relative path");
        Assert.assertNotNull(RulesProbeLabelExport.outputRefusal("/x/repo/eval/raw.jsonl.gz", repo, forge), "in the repo");
        Assert.assertNotNull(RulesProbeLabelExport.outputRefusal("/x/forge/raw.jsonl.gz", repo, forge), "in the checkout");
        Assert.assertNotNull(RulesProbeLabelExport.outputRefusal("/x/repo/runs/../raw.jsonl", repo, forge), "normalised");
        Assert.assertNull(RulesProbeLabelExport.outputRefusal("/x/repo/runs/rules-probe/raw.jsonl.gz", repo, forge));
        Assert.assertNull(RulesProbeLabelExport.outputRefusal("/tmp/probe/raw.jsonl.gz", repo, forge));
        Assert.assertNull(RulesProbeLabelExport.outputRefusal("/x/repository-copy/raw.jsonl", repo, forge), "a prefix only");
    }
}
