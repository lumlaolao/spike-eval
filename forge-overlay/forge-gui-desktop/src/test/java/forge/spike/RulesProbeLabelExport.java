package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
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

import forge.ai.LobbyPlayerAi;
import forge.game.Game;
import forge.game.GameActionUtil;
import forge.game.card.Card;
import forge.game.phase.PhaseHandler;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.item.PaperCard;
import forge.model.FModel;
import forge.util.FileSection;
import forge.util.FileUtil;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.LineFindingProbeTest.Recorder;
import static forge.spike.ProposeActionsTest.prop;

/**
 * The rules probe's engine labeller (eval/research/2026-11-05-rules-probe.md, sections 6 and 9) and its export. A .pzl
 * board is put down by PuzzleSetupTest.gameOf, the puzzle runner's start hook, and every proposal the solver could make
 * with priority (every visible card with every verb, each ability number, each way to cast it the card has and some it
 * has not, each face, each target of a one-target play) is labelled by the propose arm's checker (matchProposal):
 * accepted is legal, "ILLEGAL (category): reason" is the checker's category and sentence, a FORMAT error is dropped (it
 * is about how the proposal is written, not the rules). Near-miss variants change one line of the board; each is
 * labelled by the checker, never by the edit. The label is the propose checker's: it inherits that checker's defects
 * (SUBSTITUTED below; its category order, section 9.3 of the note) and Forge's rules bugs.
 *
 * Named Export, not Test: acceptance compiles it and never runs it. RulesProbeLabelTest holds the always-on tests. Run:
 *   java -Dharness.rules.file=$REPO/data/rules/MagicCompRules.txt -Dharness.probe.out=/abs/raw-K.jsonl.gz
 *        -Dharness.probe.shard=K/6 ... org.testng.TestNG -testclass forge.spike.RulesProbeLabelExport
 * from $FORGE_HOME/forge-gui-desktop, K = 0..5, one JVM after another. -Dharness.probe.boards (comma-separated paths
 * under the repository, default eval/puzzles), -Dharness.probe.limit (boards) and -Dharness.probe.variants=base narrow
 * it. The output must be an absolute path outside the repository and the Forge checkout. Each shard writes one state row
 * per board variant, one row per labelled proposal and a closing summary row (its drop counts);
 * `python3 eval/rules_probe.py engine` turns the rows into probe items.
 *
 * Why shards: a JVM keeps something of every board it builds (not found). With -Xmx2g one JVM ran out of heap after
 * about 375 of the 1,372 boards under eval/puzzles (each board up to nine variants), so six shards of about 229 boards
 * each run one after another; on an idle machine the six take about four minutes.
 */
public class RulesProbeLabelExport {
    static final String SCHEMA = "rules-probe-engine-raw-v1";
    /** Ways to cast that a card may lack: named on every non-land card, so a cast way the card does not have is an
     *  item (Flashback on Desperate Ritual was a refusal a model made, runs/propose/e75-haiku-hard-propose). */
    static final List<String> WRONG_WAYS = List.of("Flashback", "Evoke", "alternative", "flash");
    /** The verdict of an activation the checker accepted for another card's menu entry: matchProposal lets a menu mana
     *  ability stand for every copy of the card's name ("another copy's entry stands for this one"), whatever the copy's
     *  zone or tapped state, so a tapped Mountain or one in hand reads as legal beside an untapped one. No label. It drops
     *  some legal items too: a second untapped copy's ability, matched to the first copy's entry, is dropped with them. */
    static final String SUBSTITUTED = "SUBSTITUTED:";
    private static final Pattern ILLEGAL = Pattern.compile("^ILLEGAL \\(([^)]+)\\): (.*)$", Pattern.DOTALL);

    @BeforeClass
    public void initializeModel() { new PuzzleSetupTest().initializeModel(); }

    @BeforeMethod
    public void on() { arm(); }

    @AfterMethod(alwaysRun = true)
    public void off() { disarm(); }

    /** The propose seat's statics, as ProposeActionsTest sets them. */
    static void arm() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        AnswerTool.PROPOSE_FOR_TEST = true;
    }

    static void disarm() {
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

    /** The board of {@code sections} (PuzzleSetupTest.gameOf) with a propose seat for the solver (p0) holding priority. */
    static Board boardOf(Map<String, List<String>> sections, String name) throws Exception {
        Board b = new Board();
        b.game = PuzzleSetupTest.gameOf(sections, name);
        b.me = b.game.getPlayers().get(0);
        // The seat is named as its player, as in a game: the state's header names the active player and its YOU line
        // the seat, and a reader of the state (rules_probe.parse_window) tells whose turn it is by comparing them.
        Recorder model = new Recorder("CHOICE: 1");
        b.ctrl = new LlmSpikeGui.PlayerControllerLLM(b.game, b.me, new LobbyPlayerAi(b.me.getName(), null),
                b.me.getName(), "", model, model, "llm");
        b.me.dangerouslySetController(b.ctrl);
        PhaseHandler phh = b.game.getPhaseHandler();
        // The question is what the solver may do with priority; the checker does not ask who holds it, so a board put
        // down on the opponent's turn, or under the opponent's spell, gives it to the solver (as the opponent passing).
        phh.setPriority(b.me);
        b.ownMain = phh.isPlayerTurn(b.me) && (phh.is(PhaseType.MAIN1) || phh.is(PhaseType.MAIN2));
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

    /** Every candidate proposal at the board's priority, labelled. FORMAT and SUBSTITUTED verdicts are kept here (the
     *  writer drops them) so a test can see them. */
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

    /** Why the export may not write {@code path}, or null: it must be absolute (a relative one would land in the Forge
     *  checkout's forge-gui-desktop, where the JVM runs and which install.sh overwrites), outside the checkout, and in
     *  the repository only under its git-ignored runs/. */
    static String outputRefusal(String path, File repo, File forge) {
        java.nio.file.Path p = java.nio.file.Paths.get(path);
        if (!p.isAbsolute()) return "-Dharness.probe.out must be an absolute path: " + path;
        p = p.normalize();
        java.nio.file.Path r = repo.toPath().toAbsolutePath().normalize(), f = forge.toPath().toAbsolutePath().normalize();
        if (p.startsWith(f)) return "-Dharness.probe.out is inside the Forge checkout " + f + ": " + p;
        if (p.startsWith(r) && !p.startsWith(r.resolve("runs"))) return "-Dharness.probe.out is in the repository outside runs/: " + p;
        return null;
    }

    /** A board's id: its path under eval/puzzles without ".pzl", or its file name for a board elsewhere. */
    static String boardId(File repo, File f) {
        java.nio.file.Path root = repo.toPath().resolve("eval/puzzles").toAbsolutePath().normalize();
        java.nio.file.Path p = f.toPath().toAbsolutePath().normalize();
        String id = p.startsWith(root) ? root.relativize(p).toString() : f.getName();
        return id.replaceAll("\\.pzl$", "");
    }

    /** Label the boards {@code files} (every {@code n}th from the {@code k}th, at most {@code limit}) into {@code w}:
     *  a state row per board variant, a row per labelled proposal, and one closing summary row. Returns that summary. */
    static JsonObject export(List<File> files, File repo, Writer w, int k, int n, int limit, boolean baseOnly) throws IOException {
        String epoch = LlmSpikeGui.HARNESS_EPOCH, obsVersion = SeatObservation.VERSION;
        String pin = new String(java.nio.file.Files.readAllBytes(new File(repo, "FORGE_COMMIT").toPath()), StandardCharsets.UTF_8).trim();
        int boards = 0, failed = 0, rows = 0, formats = 0, substituted = 0;
        for (int i = 0; i < files.size() && boards < limit; i++) {
            if (i % n != k) continue;
            File f = files.get(i);
            String board = boardId(repo, f);
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
                } catch (Throwable e) {
                    failed++;
                    System.out.println("[RULES-PROBE] " + board + " " + v.getKey() + " did not label: " + e);
                } finally {
                    disarm();
                    arm();
                }
            }
            if (boards % 25 == 0) { w.flush(); System.out.println("[RULES-PROBE] boards=" + boards + " rows=" + rows); }
        }
        JsonObject sum = new JsonObject();
        sum.addProperty("t", "summary");
        sum.addProperty("schema", SCHEMA);
        sum.addProperty("shard", k + "/" + n);
        sum.addProperty("boards_labelled", boards);
        sum.addProperty("rows", rows);
        sum.addProperty("format_dropped", formats);
        sum.addProperty("substituted_dropped", substituted);
        sum.addProperty("failed", failed);
        w.write(sum.toString());
        w.write("\n");
        return sum;
    }

    /** -Dharness.probe.out=FILE (required): label the boards and write the rows (the class comment says how to run it). */
    @Test
    public void exportTheBoards() throws Exception {
        String outPath = System.getProperty("harness.probe.out");
        Assert.assertNotNull(outPath, "name the output: -Dharness.probe.out=/absolute/path/raw.jsonl.gz");
        File repo = PuzzleSetupTest.repoRoot();
        String refused = outputRefusal(outPath, repo, new File(System.getProperty("user.dir")).getAbsoluteFile().getParentFile());
        Assert.assertNull(refused, refused);
        List<File> files = boardFiles(repo, System.getProperty("harness.probe.boards", "eval/puzzles"));
        String[] shard = System.getProperty("harness.probe.shard", "0/1").split("/");
        int limit = Integer.parseInt(System.getProperty("harness.probe.limit", "1000000"));
        boolean baseOnly = "base".equals(System.getProperty("harness.probe.variants"));
        JsonObject sum;
        try (OutputStream os = outPath.endsWith(".gz") ? new GZIPOutputStream(new FileOutputStream(outPath)) : new FileOutputStream(outPath);
             Writer w = new OutputStreamWriter(os, StandardCharsets.UTF_8)) {
            sum = export(files, repo, w, Integer.parseInt(shard[0]), Integer.parseInt(shard[1]), limit, baseOnly);
        }
        System.out.println("[RULES-PROBE] " + sum + " -> " + outPath);
        Assert.assertTrue(sum.get("boards_labelled").getAsInt() > 0, "no board under the named paths");
    }
}
