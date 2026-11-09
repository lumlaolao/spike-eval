package forge.spike;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.concurrent.TimeUnit;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import forge.CardStorageReader;
import forge.GuiDesktop;
import forge.StaticData;
import forge.card.CardEdition;
import forge.card.CardRarity;
import forge.card.CardRules;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.player.Player;
import forge.game.spellability.SpellAbilityStackInstance;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.item.PaperCard;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.FileSection;
import forge.util.FileUtil;

/**
 * Twin puzzles in the engine (PR #371): every committed twin board resolves and places exactly its listed cards.
 * Forge skips an unknown card name with a stderr line and no exception (GameState.java:1306), so the board a twin
 * with one wrong name builds is a board missing a card, and a solve rate on it measures nothing. The twins are not
 * committed: they are made as the runner makes them (puzzle_twin.py make, then install into a scratch checkout holding
 * only the originals' scripts, never into $FORGE_HOME), read back by Forge's own CardStorageReader and entered in the
 * card database as a card no edition lists (CardDb.loadCard, then addCard in the UNKNOWN set, the two steps of
 * addUnassignedCardPrints for one card; calling that method would add every real unassigned card too). Fixed sample: every active
 * puzzle of rules-sample-v1 and hard-gen-v1 (the two suites the PR twinned), not a subset. The cards are left in the
 * test JVM's card database (names no real card has).
 */
public class TwinCardsTest {
    private static final File REPO = PuzzleSetupTest.repoRoot();
    /** The checkout's root: tests run from its forge-gui-desktop. */
    private static final File FORGE = new File("..").getAbsoluteFile().toPath().normalize().toFile();
    private static final String[] SUITES = {"puzzles-rules-sample-v1", "puzzles-hard-gen-v1"};
    /** The only boards puzzle_twin.py refuses, each with the part of the reason that says why (RP_035's Animate Dead
     *  names Worldgorger Dragon in its script, RP_124 the engine names Reanimate, RP_243 has two faces; gen-v1's
     *  storm board has the engine name Lotus Petal). A change here is a finding. */
    private static final Map<String, String> REFUSED = Map.of(
            "rules-RP_035", "Animate Dead: names another card: named Worldgorger Dragon",
            "rules-RP_124", "the engine names Reanimate in its source",
            "rules-RP_243", "Thing in the Ice: a card with more than one face is not twinned",
            "hard-storm-2ef0ca14337b", "the engine names Lotus Petal in its source");
    /** Boards with a stack line, which no suite twinned has: the pilot boards whose stack line names a card (RP_106's Bonecrusher Giant has two faces). Their
     *  twins are made, installed and checked like a suite's (#453). */
    private static final String[] STACK_BOARDS = {"094", "113", "114", "134", "136", "155", "180", "186"};
    private static final String STACK_SUITE = "puzzles-stack-lines";
    private static final String[] ZONES = {"hand", "battlefield", "graveyard", "library", "exile"};

    /** One suite's twins: puzzle id, original and twin .pzl, original -> twin names. */
    private static final class Twin {
        String id; File original; File twin; Map<String, String> names = new TreeMap<>();
    }
    private static final List<Twin> TWINS = new ArrayList<>();
    private static final Map<String, String> REFUSED_NOW = new TreeMap<>();
    private static final List<String> INSTALLED = new ArrayList<>();
    private static Path scratch;

    private Map<String, String> savedEnv;
    @BeforeMethod
    public void emptyEnvironment() { savedEnv = LlmSpikeGui.envOverride; LlmSpikeGui.envOverride = Map.of(); }
    @AfterMethod
    public void restoreEnvironment() {
        LlmSpikeGui.envOverride = savedEnv;
        LlmSpikeGui.puzzleSolverSeat = null; LlmSpikeGui.puzzleBrief = null; LlmSpikeGui.puzzleLastTurn = -1;
    }

    @BeforeClass
    public void makeAndLoadTwins() throws Exception {
        if (GuiBase.getInterface() == null) {
            GuiBase.setInterface(new GuiDesktop());
            FModel.initialize(null, preferences -> {
                preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
                preferences.setPref(FPref.UI_LANGUAGE, "en-US");
                return null;
            });
        }
        // puzzle_twin.py refuses an --out outside the repository's runs/ (third-party twins are never committed)
        scratch = Files.createDirectories(new File(REPO, "runs/twin-cards-test").toPath());
        for (String suite : SUITES) makeSuite(suite);
        makeSuite(STACK_SUITE, stackSuite());
        Assert.assertFalse(TWINS.isEmpty(), "no twin was made");
        // Both suites' twin scripts, read by Forge's reader and registered as cards no edition lists.
        List<CardRules> rules = new ArrayList<>();
        List<String> made = new ArrayList<>(List.of(SUITES));
        made.add(STACK_SUITE);
        for (String suite : made) {
            File dir = new File(scratch.toFile(), suite + "/forge/forge-gui/res/cardsfolder");
            for (CardRules cr : new CardStorageReader(dir.getPath(), null, false).loadCards()) rules.add(cr);
        }
        var db = StaticData.instance().getCommonCards();
        for (CardRules cr : rules) {
            db.loadCard(cr.getName(), CardEdition.UNKNOWN_CODE, cr);
            db.addCard(new PaperCard(cr, CardEdition.UNKNOWN_CODE, CardRarity.Special));
            INSTALLED.add(cr.getName());
        }
    }

    /** puzzle_twin.py make and install, as the runner would run them, into a scratch checkout of the originals. */
    private static void makeSuite(String suite) throws Exception {
        makeSuite(suite, new File(REPO, "eval/suites/" + suite + ".json"));
    }

    private static void makeSuite(String suite, File suiteFile) throws Exception {
        JsonObject s = JsonParser.parseString(Files.readString(suiteFile.toPath())).getAsJsonObject();
        List<String> ids = new ArrayList<>();
        for (JsonElement e : s.getAsJsonArray("puzzles")) {
            JsonObject p = e.getAsJsonObject();
            if ("active".equals(p.get("status").getAsString())) ids.add(p.get("id").getAsString());
        }
        File out = new File(scratch.toFile(), suite);
        run(REPO, "python3", "-I", "eval/puzzles/puzzle_twin.py", "make", "--suite", suiteFile.getPath(),
                "--ids", String.join(",", ids), "--seed", "1", "--out", out.getPath(), "--forge", FORGE.getPath());
        JsonObject m = JsonParser.parseString(Files.readString(new File(out, "twin-manifest.json").toPath()))
                .getAsJsonObject();
        File fake = new File(out, "forge");
        for (JsonElement e : m.getAsJsonArray("cards")) {
            String script = e.getAsJsonObject().get("script").getAsString();
            Path dst = new File(fake, "forge-gui/res/cardsfolder/" + script).toPath();
            Files.createDirectories(dst.getParent());
            Files.copy(new File(FORGE, "forge-gui/res/cardsfolder/" + script).toPath(), dst,
                    StandardCopyOption.REPLACE_EXISTING);
        }
        run(REPO, "python3", "-I", "eval/puzzles/puzzle_twin.py", "install", "--manifest",
                new File(out, "twin-manifest.json").getPath(), "--forge", fake.getPath());
        Map<String, String> cardTwin = new TreeMap<>();
        for (JsonElement e : m.getAsJsonArray("cards"))
            cardTwin.put(e.getAsJsonObject().get("original").getAsString(), e.getAsJsonObject().get("twin").getAsString());
        JsonArray puzzles = m.getAsJsonArray("puzzles");
        for (JsonElement e : puzzles) {
            JsonObject p = e.getAsJsonObject();
            Twin t = new Twin();
            t.id = p.get("id").getAsString();
            t.original = new File(REPO, p.get("original_pzl").getAsString());
            t.twin = new File(out, p.get("pzl").getAsString());
            for (JsonElement c : p.getAsJsonArray("cards")) t.names.put(c.getAsString(), cardTwin.get(c.getAsString()));
            TWINS.add(t);
        }
        for (JsonElement e : m.getAsJsonArray("refused"))
            REFUSED_NOW.put(e.getAsJsonObject().get("id").getAsString(), e.getAsJsonObject().get("reason").getAsString());
    }

    /** A suite file of the STACK_BOARDS, so puzzle_twin.py makes them as it makes a committed suite's. */
    private static File stackSuite() throws Exception {
        JsonObject s = JsonParser.parseString(Files.readString(new File(REPO, "eval/suites/" + SUITES[0] + ".json")
                .toPath())).getAsJsonObject();
        JsonArray puzzles = new JsonArray();
        for (String n : STACK_BOARDS) {
            JsonObject rec = s.getAsJsonArray("puzzles").get(0).getAsJsonObject().deepCopy();
            rec.addProperty("id", "stack-RP_" + n);
            rec.addProperty("status", "active");
            rec.addProperty("pzl", "eval/puzzles/rules-pilot/RP_" + n + ".pzl");
            puzzles.add(rec);
        }
        s.add("puzzles", puzzles);
        File f = new File(scratch.toFile(), STACK_SUITE + ".json");
        Files.writeString(f.toPath(), s.toString());
        return f;
    }

    private static void run(File cwd, String... cmd) throws Exception {
        Process p = new ProcessBuilder(cmd).directory(cwd).redirectErrorStream(true).start();
        String out = new String(p.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        if (!p.waitFor(600, TimeUnit.SECONDS)) { p.destroyForcibly(); throw new AssertionError("timed out: " + String.join(" ", cmd)); }
        if (p.exitValue() != 0) throw new AssertionError(String.join(" ", cmd) + " exited " + p.exitValue() + ":\n" + out);
    }

    // ---- the checks, each a list of problems (empty = pass) so a broken board can be shown failing them ----

    /** A [state] line's zone: "p0hand" is (0, "hand"); null for a line that is not a card zone. */
    private static String[] zoneOf(String key) {
        String k = key.trim().toLowerCase();
        for (String z : ZONES) {
            if (k.endsWith(z) && k.length() > z.length()) {
                String who = k.substring(0, k.length() - z.length());
                if (who.equals("p0") || who.equals("human")) return new String[] {"0", z};
                if (who.equals("p1") || who.equals("ai")) return new String[] {"1", z};
            }
        }
        return null;
    }

    private static boolean isStackKey(String key) {
        return key.trim().toLowerCase().matches("(p\\d|human|ai)(precast|putonstack)");
    }

    /** The card a stack line entry names: "Murder->1" is Murder, "The One Ring:TrigPump" the Ring (one of its
     *  abilities); null for a card id ("7:TrigExile->1"), which no twin renames. */
    static String stackCardName(String entry) {
        String head = entry.split("\\|")[0].split("->")[0].trim();
        int colon = head.lastIndexOf(':');
        if (colon > 0 && head.substring(colon + 1).matches("\\w+")) head = head.substring(0, colon);
        return head.isEmpty() || head.matches("\\d+") ? null : head;
    }

    /** What the [state] lists per "player:zone": the card names, tokens and card ids left out. */
    static Map<String, List<String>> listed(Map<String, List<String>> sections) {
        Map<String, List<String>> out = new TreeMap<>();
        for (String line : sections.getOrDefault("state", List.of())) {
            int eq = line.indexOf('=');
            if (eq < 0) continue;
            if (isStackKey(line.substring(0, eq))) {
                String zone = line.substring(0, eq).trim().toLowerCase().endsWith("precast") ? "precast" : "stack";
                for (String entry : line.substring(eq + 1).split(";")) {
                    String name = stackCardName(entry);
                    if (name != null) out.computeIfAbsent(zone, k -> new ArrayList<>()).add(name);
                }
                continue;
            }
            String[] z = zoneOf(line.substring(0, eq));
            if (z == null) continue;
            List<String> names = out.computeIfAbsent(z[0] + ":" + z[1], k -> new ArrayList<>());
            for (String entry : line.substring(eq + 1).split(";")) {
                String head = entry.split("\\|")[0].trim();
                if (!head.isEmpty() && !head.matches("\\d+") && !head.startsWith("t:") && !head.startsWith("T:"))
                    names.add(head);
            }
        }
        out.values().forEach(java.util.Collections::sort);
        return out;
    }

    /** What the built game holds per "player:zone", tokens left out, and on the stack ("stack": the source card of
     *  each item). */
    static Map<String, List<String>> placed(Game game) {
        Map<String, List<String>> out = new TreeMap<>();
        List<String> stack = new ArrayList<>();
        for (SpellAbilityStackInstance item : game.getStack()) stack.add(item.getSourceCard().getName());
        java.util.Collections.sort(stack);
        if (!stack.isEmpty()) out.put("stack", stack);
        for (int i = 0; i < 2; i++) {
            Player p = game.getPlayers().get(i);
            for (String z : ZONES) {
                ZoneType zt = ZoneType.valueOf(Character.toUpperCase(z.charAt(0)) + z.substring(1));
                List<String> names = new ArrayList<>();
                for (Card c : p.getZone(zt)) if (!c.isToken()) names.add(c.getName());
                java.util.Collections.sort(names);
                if (!names.isEmpty()) out.put(i + ":" + z, names);
            }
        }
        return out;
    }

    /** Names in the board's zones that the card database does not know. */
    static List<String> unresolved(Map<String, List<String>> sections) {
        TreeSet<String> missing = new TreeSet<>();
        for (List<String> names : listed(sections).values())
            for (String n : names)
                if (StaticData.instance().getCommonCards().getCard(n) == null) missing.add(n);
        return new ArrayList<>(missing);
    }

    /** The zones where the game holds something other than the board lists. */
    static List<String> misplaced(Map<String, List<String>> sections, Game game) {
        Map<String, List<String>> want = listed(sections), got = placed(game);
        want.values().removeIf(List::isEmpty);
        want.remove("precast");  // a precast spell is cast and resolved while the board is built: no zone holds it
        List<String> bad = new ArrayList<>();
        TreeSet<String> keys = new TreeSet<>(want.keySet());
        keys.addAll(got.keySet());
        for (String k : keys)
            if (!want.getOrDefault(k, List.of()).equals(got.getOrDefault(k, List.of())))
                bad.add(k + " listed " + want.get(k) + " but the game holds " + got.get(k));
        return bad;
    }

    private static Map<String, List<String>> sectionsOf(File pzl) throws IOException {
        return FileSection.parseSections(FileUtil.readFile(pzl));
    }

    // ---- tests ----

    /** The refusals are the ones reviewed; a board that newly refuses (or stops refusing) is a finding. */
    @Test
    public void onlyTheReviewedBoardsAreRefusedForTheReviewedReasons() {
        Assert.assertEquals(REFUSED_NOW, new TreeMap<>(REFUSED), "refused: " + REFUSED_NOW);
    }

    /** Every twin script Forge reads is a card the database holds under the manifest's twin name. */
    @Test
    public void everyTwinCardScriptIsInTheCardDatabase() {
        TreeSet<String> want = new TreeSet<>();
        for (Twin t : TWINS) want.addAll(t.names.values());
        Assert.assertTrue(want.size() > 100, "twin cards: " + want.size());
        List<String> missing = new ArrayList<>();
        for (String n : want) if (StaticData.instance().getCommonCards().getCard(n) == null) missing.add(n);
        Assert.assertEquals(String.join(", ", missing), "", "twin names the card database does not hold");
        Assert.assertTrue(INSTALLED.containsAll(want), "a twin in the manifest was not among the scripts read");
    }

    /** Every name every twin board lists, in any of the five zones, resolves; none is an original's name. */
    @Test
    public void everyNameOnEveryTwinBoardResolves() throws Exception {
        List<String> bad = new ArrayList<>();
        for (Twin t : TWINS) {
            Map<String, List<String>> s = sectionsOf(t.twin);
            List<String> miss = unresolved(s);
            if (!miss.isEmpty()) bad.add(t.id + " does not resolve " + miss);
            for (List<String> names : listed(s).values())
                for (String n : names)
                    if (t.names.containsKey(n)) bad.add(t.id + " still lists the original " + n);
        }
        Assert.assertEquals(String.join("\n", bad), "");
    }

    /** Each twin board, built as the runner builds it, holds exactly its listed cards in each of the five zones,
     *  and that is the original board's placement with the names mapped (the stack lines precast/putonstack
     *  put a card on the stack, "stack"; a precast
     *  card resolves while the board is built and leaves nothing to place, so it is checked for resolution only). */
    @Test
    public void everyTwinBoardPlacesExactlyItsListedCards() throws Exception {
        List<String> bad = new ArrayList<>();
        int checked = 0;
        for (Twin t : TWINS) {
            Map<String, List<String>> s = sectionsOf(t.twin);
            Game game = PuzzleSetupTest.gameOf(s, t.id + "-twin");
            for (String b : misplaced(s, game)) bad.add(t.id + " twin: " + b);
            Map<String, List<String>> orig = placed(PuzzleSetupTest.gameOf(sectionsOf(t.original), t.id));
            Map<String, List<String>> mapped = new TreeMap<>();
            placed(game).forEach((k, v) -> {
                List<String> back = new ArrayList<>();
                for (String n : v) back.add(reverse(t.names, n));
                java.util.Collections.sort(back);
                mapped.put(k, back);
            });
            if (!orig.equals(mapped)) bad.add(t.id + ": mapped back, the twin holds " + mapped + " but the original " + orig);
            checked++;
        }
        Assert.assertEquals(String.join("\n", bad), "");
        Assert.assertEquals(checked, 26 + 115 + STACK_BOARDS.length,
                "26 of rules-sample-v1's 29, 115 of hard-gen-v1's 116 and every stack board are twinned");
    }

    private static String reverse(Map<String, String> names, String twin) {
        for (Map.Entry<String, String> e : names.entrySet()) if (e.getValue().equals(twin)) return e.getKey();
        return twin;  // a basic land or other card left as it was
    }

    /** Red on a broken twin: one misspelled name fails the resolution check and the placement check, so the green
     *  above is a green that could have been red. */
    @Test
    public void aMisspelledNameIsCaughtByBothChecks() throws Exception {
        Twin t = TWINS.get(0);
        String twinName = t.names.values().iterator().next();
        List<String> lines = new ArrayList<>(Files.readAllLines(t.twin.toPath()));
        boolean changed = false;
        for (int i = 0; i < lines.size() && !changed; i++)
            if (lines.get(i).contains(twinName) && lines.get(i).contains("=")) {
                lines.set(i, lines.get(i).replace(twinName, twinName + "x")); changed = true;
            }
        Assert.assertTrue(changed, twinName + " is not on a state line of " + t.id);
        File broken = new File(scratch.toFile(), "broken.pzl");
        Files.write(broken.toPath(), lines);
        Map<String, List<String>> s = sectionsOf(broken);
        Assert.assertEquals(unresolved(s), List.of(twinName + "x"), "resolution check");
        Game game = PuzzleSetupTest.gameOf(s, "broken");
        List<String> bad = misplaced(s, game);
        Assert.assertFalse(bad.isEmpty(), "placement check passed a board missing " + twinName + "x");
        Assert.assertTrue(bad.get(0).contains(twinName + "x"), bad.toString());
    }

    /** Red on a twin whose script is missing from the database: the unresolved check names it. */
    @Test
    public void aTwinWithNoScriptIsCaught() throws Exception {
        Twin t = TWINS.get(0);
        Map<String, List<String>> s = sectionsOf(t.original);  // the original board names real cards only
        Assert.assertEquals(unresolved(s), List.of(), "the control: real names resolve");
        List<String> lines = new ArrayList<>(s.get("state"));
        lines.add("p0hand=Zzz Not A Card");
        Map<String, List<String>> withGhost = new TreeMap<>(s);
        withGhost.put("state", lines);
        Assert.assertEquals(unresolved(withGhost), List.of("Zzz Not A Card"));
    }

    /** Red on a stack line: a twin whose stack card is misspelled fails both checks. Without the stack zone in
     *  listed()/placed() the name is read by neither (#453: they were checked for resolution only, in fact not at all). */
    @Test
    public void aMisspelledNameOnAStackLineIsCaughtByBothChecks() throws Exception {
        Twin t = TWINS.stream().filter(x -> x.id.equals("stack-RP_180")).findFirst().orElseThrow();
        String twinName = t.names.get("Smallpox");
        Assert.assertEquals(listed(sectionsOf(t.twin)).get("stack"), List.of(twinName), "the twin's stack line is read");
        List<String> lines = new ArrayList<>(Files.readAllLines(t.twin.toPath()));
        for (int i = 0; i < lines.size(); i++)
            if (lines.get(i).endsWith("putonstack=" + twinName)) lines.set(i, lines.get(i) + "x");
        File broken = new File(scratch.toFile(), "broken-stack.pzl");
        Files.write(broken.toPath(), lines);
        Map<String, List<String>> s = sectionsOf(broken);
        Assert.assertEquals(unresolved(s), List.of(twinName + "x"), "resolution check");
        List<String> bad;
        try {
            bad = misplaced(s, PuzzleSetupTest.gameOf(s, "broken-stack"));
        } catch (AssertionError | Exception e) {
            return;  // an unknown stack card that stops the board from building is caught as well
        }
        Assert.assertEquals(bad.size(), 1, "placement check: " + bad);
        Assert.assertTrue(bad.get(0).contains(twinName + "x"), bad.toString());
    }

    /** Red on a precast line: its card is read (the twin's name, not the original's) and a misspelling is caught. */
    @Test
    public void aMisspelledNameOnAPrecastLineIsCaughtByTheResolutionCheck() throws Exception {
        Twin t = TWINS.stream().filter(x -> x.id.equals("stack-RP_114")).findFirst().orElseThrow();
        String twinName = t.names.get("Giant Growth");
        Assert.assertEquals(listed(sectionsOf(t.twin)).get("precast"), List.of(twinName), "the twin's precast line is read");
        Assert.assertEquals(unresolved(sectionsOf(t.twin)), List.of(), "the control: the twin's names resolve");
        List<String> lines = new ArrayList<>(Files.readAllLines(t.twin.toPath()));
        for (int i = 0; i < lines.size(); i++)
            if (lines.get(i).contains("precast=" + twinName)) lines.set(i, lines.get(i).replace(twinName, twinName + "x"));
        File broken = new File(scratch.toFile(), "broken-precast.pzl");
        Files.write(broken.toPath(), lines);
        Assert.assertEquals(unresolved(sectionsOf(broken)), List.of(twinName + "x"));
    }

    /** The stack line's entry forms: a target, an ability of the card, a card id (no name), a plain card. */
    @Test
    public void stackLineEntriesNameTheirCard() {
        Assert.assertEquals(stackCardName("Murder->1"), "Murder");
        Assert.assertEquals(stackCardName("The One Ring:TrigPump"), "The One Ring");
        Assert.assertEquals(stackCardName("Epicenter->human"), "Epicenter");
        Assert.assertNull(stackCardName("7:TrigExile->1"));
        Assert.assertEquals(stackCardName("Smallpox"), "Smallpox");
    }
}
