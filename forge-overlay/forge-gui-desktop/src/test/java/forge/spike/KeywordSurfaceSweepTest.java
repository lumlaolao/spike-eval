package forge.spike;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import forge.card.CardRules;
import forge.card.ICardFace;
import forge.game.Game;
import forge.game.GameActionUtil;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.keyword.Keyword;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.OptionalCostValue;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.item.PaperCard;
import forge.model.FModel;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import static forge.spike.LineFindingProbeTest.Recorder;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

/**
 * Every mechanic Forge knows (its {@link Keyword} enum), on a real card that has it, in every zone and in the turn
 * moments a card can be played from:
 * <ul><li><b>parity</b>: what a human could do with it now (the engine's {@code Card.getAllPossibleAbilities}, the list
 *     the GUI offers on a click, kept when it can be paid and has a legal target) is on the seat's menu
 *     ({@code enumeratePlayOptions}), and the menu offers nothing the engine does not;
 * <li><b>playthrough</b>: each of those plays, made by the seat with a model that answers every ask in the format it
 *     asks for, is played, resolves, never hands a choice to Forge's AI and keeps the per-ask contract.</ul>
 * A mechanic the menu does not reach is a play surface the model can never use. What remains open is listed in
 * {@code eval/keyword_gaps.txt}, which may only shrink.
 */
public class KeywordSurfaceSweepTest {

    /** A moment of the turn the seat can act in. */
    enum Moment {
        MAIN(PhaseType.MAIN1, true, ZoneType.Hand, ZoneType.Battlefield, ZoneType.Graveyard, ZoneType.Exile),
        COMBAT(PhaseType.COMBAT_DECLARE_BLOCKERS, true, ZoneType.Hand, ZoneType.Battlefield, ZoneType.Graveyard),
        UPKEEP(PhaseType.UPKEEP, true, ZoneType.Hand, ZoneType.Battlefield),
        THEIR_TURN(PhaseType.MAIN1, false, ZoneType.Hand, ZoneType.Battlefield, ZoneType.Graveyard);
        final PhaseType phase; final boolean mine; final ZoneType[] zones;
        Moment(PhaseType phase, boolean mine, ZoneType... zones) { this.phase = phase; this.mine = mine; this.zones = zones; }
    }

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    static final int PER_KEYWORD = 3;

    /** Up to {@link #PER_KEYWORD} cards per keyword, the first by name that are not Alchemy rebalances. */
    static Map<Keyword, List<String>> samples() {
        Map<Keyword, List<String>> out = new EnumMap<>(Keyword.class);
        List<PaperCard> cards = new ArrayList<>(FModel.getMagicDb().getCommonCards().getUniqueCards());
        cards.sort((a, b) -> a.getName().compareTo(b.getName()));
        for (PaperCard pc : cards) {
            String n = pc.getName();
            if (n.startsWith("A-")) continue;
            CardRules r = pc.getRules();
            for (ICardFace f : r.getAllFaces()) {
                if (f == null) continue;
                for (String kw : f.getKeywords()) {
                    try {
                        Keyword k = Keyword.getInstance(kw).getKeyword();
                        if (k == Keyword.UNDEFINED) continue;
                        List<String> l = out.computeIfAbsent(k, x -> new ArrayList<>());
                        if (l.size() < PER_KEYWORD && !l.contains(n)) l.add(n);
                    } catch (Exception ignore) { }
                }
            }
        }
        return out;
    }

    /** A board where most costs can be paid: four of each basic, creatures (one tapped, one attacking unblocked in
     *  combat), an artifact, cards in the graveyard and library, and a creature on the other side to target. */
    static Game board(Moment m) {
        Game g = bareGame(m.phase);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        if (!m.mine) g.getPhaseHandler().devModeSet(m.phase, rival);
        for (String l : new String[] {"Plains", "Island", "Swamp", "Mountain", "Forest"})
            for (int i = 0; i < 4; i++) card(l, me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Hill Giant", me, ZoneType.Battlefield);
        card("Savannah Lions", me, ZoneType.Battlefield).setTapped(true);
        card("Ornithopter", me, ZoneType.Battlefield);
        for (String c : new String[] {"Ponder", "Lightning Bolt", "Grizzly Bears", "Island", "Counterspell", "Giant Growth"})
            card(c, me, ZoneType.Graveyard);
        for (int i = 0; i < 10; i++) card(i % 2 == 0 ? "Island" : "Grizzly Bears", me, ZoneType.Library);
        for (int i = 0; i < 10; i++) card("Mountain", rival, ZoneType.Library);
        card("Mountain", me, ZoneType.Hand);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        if (m == Moment.COMBAT) {
            Combat combat = new Combat(me);
            combat.addAttacker(bears, rival);
            bears.setTapped(true);
            combat.setBlocked(bears, false);
            g.getPhaseHandler().setCombat(combat);
        }
        return g;
    }

    static String key(SpellAbility sa) {
        return sa.getHostCard().getId() + "|" + sa.getAlternativeCost() + "|" + sa.getCardStateName() + "|"
                + (sa.isLandAbility() ? "land" : sa.getDescription());
    }

    /** What the engine lets a human do now with {@code c}: its possible abilities, payable, with a legal target. A
     *  spell playable only with an optional cost (retrace, jump-start) counts, as the human path asks for it. */
    static Map<String, SpellAbility> engineOptions(Card c, Player me) {
        Map<String, SpellAbility> out = new TreeMap<>();
        for (SpellAbility sa : c.getAllPossibleAbilities(me, false)) {
            sa.setActivatingPlayer(me);
            if (sa.isLandAbility()) { if (sa.canPlay() && me.canPlayLand(c, false, sa)) out.put(key(sa), sa); continue; }
            if (sa.isManaAbility()) {
                if (LlmSpikeGui.PlayerControllerLLM.costlyManaAbility(sa, me)) out.put(key(sa), sa);
                continue;
            }
            try {
                if (LlmSpikeGui.PlayerControllerLLM.offerable(sa, me, true) && payableAtLeastX(sa)) { out.put(key(sa), sa); continue; }
                if (!sa.isSpell()) continue;
                for (OptionalCostValue v : GameActionUtil.getOptionalCostValues(sa)) {
                    SpellAbility with = LlmSpikeGui.PlayerControllerLLM.withOptional(sa, List.of(v));
                    with.setActivatingPlayer(me);
                    if (LlmSpikeGui.PlayerControllerLLM.offerable(with, me, true)) { out.put(key(sa), sa); break; }
                }
            } catch (Exception ignore) { }
        }
        return out;
    }

    /** The engine's own test of the costs beyond mana, at the least X they allow: Forge's AI price ignores a minimum
     *  X ("remove X counters, X at least 1"), which a human cannot activate with no counter. */
    static boolean payableAtLeastX(SpellAbility sa) {
        if (sa.getPayCosts() == null || !sa.getPayCosts().hasXInAnyCostPart()) return true;
        Integer x = sa.getXManaCostPaid();
        forge.game.cost.CostPartMana cm = sa.getPayCosts().getCostMana();
        sa.setXManaCostPaid(cm == null ? 0 : Math.max(0, cm.getXMin()));
        try { return forge.game.cost.CostPayment.canPayAdditionalCosts(sa.getPayCosts(), sa, false); }
        finally { sa.setXManaCostPaid(x); }
    }

    static Map<String, SpellAbility> menuOptions(Card c, Player me, boolean ownMain) {
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, ownMain, true);
        Map<String, SpellAbility> menu = new TreeMap<>();
        for (SpellAbility sa : po.spells) if (sa.getHostCard().getId() == c.getId()) menu.put(key(sa), sa);
        for (SpellAbility sa : po.landPlays) if (sa.getHostCard().getId() == c.getId()) menu.put(key(sa), sa);
        return menu;
    }

    /** Answers every ask in the format the ask says: the first option, one pick, the lowest number, no blocks. */
    static final class FormatAnswerer extends Recorder {
        static final Pattern RANGE = Pattern.compile("Choose between (\\d+) and (\\d+) of these");
        static final Pattern NUMBER = Pattern.compile("Every integer from (-?\\d+) through (-?\\d+)");
        final List<String> unknown = new ArrayList<>();
        FormatAnswerer() { super("CHOICE: 1"); }
        @Override public String ask(String prompt) {
            prompts.add(prompt);
            String d = prompt.substring(Math.max(0, prompt.lastIndexOf("DECISION:")));
            Matcher r = RANGE.matcher(d);
            if (r.find()) {
                int min = Integer.parseInt(r.group(1)), max = Integer.parseInt(r.group(2));
                if (max == 0) return "PICKS: NONE";
                StringBuilder b = new StringBuilder("PICKS: 1");
                for (int i = 2; i <= min; i++) b.append(",").append(i);
                return b.toString();
            }
            Matcher n = NUMBER.matcher(d);
            if (n.find()) return "NUMBER: " + n.group(1);
            if (d.contains("BLOCKS:")) return "BLOCKS: NONE";
            if (d.contains("KEEP or MULLIGAN")) return "KEEP";
            if (d.contains("NUMBER:")) return "NUMBER: 0";
            if (d.contains("\n  1) ")) return "CHOICE: 1";
            unknown.add(d.length() > 300 ? d.substring(0, 300) : d);
            return "CHOICE: 1";
        }
    }

    static String oneLine(Object o) {
        String s = o instanceof SpellAbility sa ? sa.getHostCard().getName() + " :: " + sa.getAlternativeCost() + " :: " + sa
                : String.valueOf(o);
        s = s.replaceAll("\\s+", " ");
        return s.length() > 240 ? s.substring(0, 240) : s;
    }

    static void resolveAll(Game g) {
        for (int i = 0; i < 30; i++) {
            g.getTriggerHandler().runWaitingTriggers();
            g.getStack().addAllTriggeredAbilitiesToStack();
            if (g.getStack().isEmpty()) break;
            g.getStack().resolveStack();
        }
        g.getAction().checkStateEffects(true);
    }

    /** Plays one option the engine allows, through the seat, and returns what went wrong (empty when nothing). */
    static List<String> playThrough(Keyword k, String name, Moment m, ZoneType zone, String optKey) {
        List<String> bad = new ArrayList<>();
        PrintStream was = System.out;
        ByteArrayOutputStream log = new ByteArrayOutputStream();
        List<String> asked = java.util.Collections.synchronizedList(new ArrayList<>());
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = asked::add;
        try {
            Game g = board(m);
            Player me = g.getPlayers().get(0);
            FormatAnswerer model = new FormatAnswerer();
            LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
            Card c = card(name, me, zone);
            SpellAbility sa = menuOptions(c, me, m.mine && m.phase.isMain()).get(optKey);
            if (sa == null) return bad; // a parity gap, reported there
            System.setOut(new PrintStream(log, true, StandardCharsets.UTF_8));
            boolean played = ctrl.playChosenSpellAbility(sa);
            resolveAll(g);
            System.setOut(was);
            if (!played) bad.add("not played");
            if (ctrl.aiDelegationsCount() > 0) bad.add("Forge AI chose " + ctrl.aiDelegationsCount() + "x");
            for (String line : log.toString(StandardCharsets.UTF_8).split("\n"))
                if (line.contains("(unparsed)") || line.contains("FALLBACK->AI") || line.contains("Couldn't add to stack")
                        || line.contains("failed to target") || line.contains("Exception"))
                    bad.add("log: " + oneLine(line));
            for (String u : model.unknown) bad.add("ask with no known answer format: " + oneLine(u));
            for (String p : new ArrayList<>(asked)) {
                try { AskContract.checkOne(k + "/" + name, p); }
                catch (AssertionError e) {
                    bad.add("contract: " + oneLine(e.getMessage()));
                    if (System.getProperty("harness.keyword.only") != null) System.err.println("[ASK] " + p);
                }
            }
        } catch (Throwable t) {
            System.setOut(was);
            StackTraceElement at = t.getStackTrace().length > 0 ? t.getStackTrace()[0] : null;
            bad.add("threw " + t.getClass().getSimpleName() + ": " + oneLine(String.valueOf(t.getMessage())) + " at " + at);
        } finally {
            System.setOut(was);
            LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
            LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = null;
        }
        return bad;
    }

    @Test
    public void everyKeywordsPlaysAreOnTheMenuAndPlay() throws Exception {
        Map<Keyword, List<String>> samples = samples();
        String only = System.getProperty("harness.keyword.only");
        List<String> report = new ArrayList<>();
        Set<String> gaps = new LinkedHashSet<>();
        int plays = 0;
        List<Map.Entry<Keyword, String>> cases = new ArrayList<>();
        for (Map.Entry<Keyword, List<String>> e : samples.entrySet())
            for (String n : e.getValue()) cases.add(Map.entry(e.getKey(), n));
        for (Map.Entry<Keyword, String> e : cases) {
            if (only != null && !only.contains(e.getKey().name())) continue;
            for (Moment m : Moment.values()) {
                for (ZoneType zone : m.zones) {
                    String where = e.getKey() + "\t" + e.getValue() + "\t" + m + "\t" + zone;
                    Map<String, SpellAbility> engine, menu;
                    try {
                        Game g = board(m);
                        Player me = g.getPlayers().get(0);
                        seat(g, new FormatAnswerer(), new FormatAnswerer());
                        Card c = card(e.getValue(), me, zone);
                        engine = engineOptions(c, me);
                        menu = menuOptions(c, me, m.mine && m.phase.isMain());
                    } catch (Throwable t) {
                        gaps.add(where + "\terror\t" + t.getClass().getSimpleName() + ": " + oneLine(String.valueOf(t.getMessage())));
                        continue;
                    }
                    for (String k : engine.keySet()) if (!menu.containsKey(k)) gaps.add(where + "\tmissing\t" + oneLine(engine.get(k)));
                    for (String k : menu.keySet()) if (!engine.containsKey(k)) gaps.add(where + "\textra\t" + oneLine(menu.get(k)));
                    report.add(where + "\tengine=" + engine.size() + "\tmenu=" + menu.size());
                    for (String k : engine.keySet()) {
                        if (!menu.containsKey(k)) continue;
                        plays++;
                        for (String b : playThrough(e.getKey(), e.getValue(), m, zone, k))
                            gaps.add(where + "\tplay\t" + oneLine(engine.get(k)) + "\t" + b);
                    }
                }
            }
        }
        File out = new File(System.getProperty("harness.keyword.report", "/tmp/keyword-sweep.tsv"));
        List<String> lines = new ArrayList<>(report);
        lines.add("");
        lines.addAll(gaps);
        Files.write(out.toPath(), lines, StandardCharsets.UTF_8);
        List<Keyword> none = new ArrayList<>();
        for (Keyword k : Keyword.getAllKeywords()) if (!samples.containsKey(k)) none.add(k);
        System.out.println("[SWEEP] keywords=" + samples.size() + " of " + Keyword.getAllKeywords().size() + " cards=" + cases.size()
                + " plays=" + plays + " gaps=" + gaps.size() + " no card: " + none + " report=" + out);
        // No card in Forge's pool uses Absorb, Frenzy or Poisonous as a keyword; the agendas exist only on Conspiracy
        // draft cards, which start the game face down in the command zone (not a constructed game).
        if (only == null) Assert.assertEquals(none.toString(), "[Absorb, Double agenda, Frenzy, Hidden agenda, Poisonous]",
                "keywords with no card to sweep");
        Assert.assertTrue(gaps.isEmpty(), gaps.size() + " play surfaces the seat does not reach or cannot play:\n"
                + String.join("\n", gaps.stream().limit(40).toList()));
    }
}
