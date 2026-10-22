package forge.spike;

import static forge.spike.EnvGrader.attacks;
import static forge.spike.EnvGrader.blocks;
import static forge.spike.EnvGrader.blocksOf;
import static forge.spike.EnvGrader.digest;
import static forge.spike.EnvGrader.json;
import static forge.spike.EnvGrader.keeps;
import static forge.spike.EnvGrader.names;
import static forge.spike.EnvGrader.numbers;
import static forge.spike.EnvGrader.option;
import static forge.spike.EnvGrader.ordered;
import static forge.spike.EnvGrader.orders;
import static forge.spike.EnvGrader.repeat;
import static forge.spike.EnvGrader.seq;
import static forge.spike.EnvGrader.set;
import static forge.spike.EnvGrader.splits;
import static forge.spike.EnvGrader.subsets;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.BiFunction;
import java.util.function.Function;
import java.util.function.Supplier;

import forge.card.ColorSet;
import forge.card.MagicColor;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.combat.AttackConstraints;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.PlayerActionConfirmMode;
import forge.game.player.PlayerController;
import forge.game.player.PlayerController.BinaryChoiceType;
import forge.game.replacement.ReplacementEffect;
import forge.game.spellability.OptionalCostValue;
import forge.game.spellability.SpellAbility;
import forge.game.trigger.WrappedAbility;
import forge.game.zone.ZoneType;
import forge.util.collect.FCollection;

/**
 * The hidden grader's instance catalog: one entry per decision surface (combat first, then every family the
 * ownership ledger lists), each a fresh board, the engine action that reaches the hook, and the outcome read from the
 * engine. Boards are the golden checks' proven setups where one exists; legal outcome sets come from the engine
 * (CombatUtil, AttackConstraints) or from the arguments the hook is handed. The families group surfaces as the owner
 * listed them (2026-10-22): damage prevention and replacement choices sit with combat.
 */
final class EnvInstances {
    private EnvInstances() { }

    // ------------------------------------------------------------------ board helpers

    static Game bare(PhaseType ph) { return LineFindingProbeTest.bareGame(ph); }
    static Player me(Game g) { return g.getPlayers().get(0); }
    static Player rival(Game g) { return g.getPlayers().get(1); }
    static Card mine(Game g, String name, ZoneType z) { return LineFindingProbeTest.card(name, me(g), z); }
    static Card theirs(Game g, String name, ZoneType z) { return LineFindingProbeTest.card(name, rival(g), z); }

    static Card find(Game g, String name) {
        for (Card c : g.getCardsInGame()) if (c.getName().equals(name)) return c;
        throw new IllegalStateException("no " + name + " on the board");
    }

    static SpellAbility spell(Game g, String name) {
        SpellAbility sa = find(g, name).getFirstSpellAbility();
        sa.setActivatingPlayer(me(g));
        return sa;
    }

    /** One priority window as the engine's loop runs it (PhaseHandler: choose, then play each), then the stack. */
    static String play(Game g, PlayerController c) {
        List<SpellAbility> sas = c.chooseSpellAbilityToPlay();
        if (sas != null) for (SpellAbility sa : sas) c.playChosenSpellAbility(sa);
        KeywordSurfaceSweepTest.resolveAll(g);
        return digest(g);
    }

    static Supplier<Game> board(PhaseType ph, java.util.function.Consumer<Game> setup) {
        return () -> { Game g = bare(ph); setup.accept(g); return g; };
    }

    // ------------------------------------------------------------------ engine-legal combat outcomes

    /** Every attack the engine allows the seat: each creature home or at one defender it can attack, kept when the
     *  attack constraints count no more violations than the best attack (CombatUtil.validateAttackers). */
    static Set<String> legalAttacks(Game g) {
        Player me = me(g);
        Combat combat = new Combat(me);
        g.getPhaseHandler().setCombat(combat);   // attack requirements read the game's combat
        AttackConstraints k = combat.getAttackConstraints();
        int best = k.getLegalAttackers().getRight();
        List<Card> mine = new ArrayList<>(me.getCreaturesInPlay());
        List<GameEntity> defs = new ArrayList<>();
        for (GameEntity d : CombatUtil.getAllPossibleDefenders(me)) defs.add(d);
        Set<String> out = new TreeSet<>();
        int choices = defs.size() + 1;
        int total = (int) Math.pow(choices, mine.size());
        for (int code = 0; code < total; code++) {
            Map<Card, GameEntity> m = new HashMap<>();
            boolean ok = true;
            for (int i = 0, x = code; i < mine.size(); i++, x /= choices) {
                int d = x % choices;
                if (d == 0) continue;
                if (!CombatUtil.canAttack(mine.get(i), defs.get(d - 1))) { ok = false; break; }
                m.put(mine.get(i), defs.get(d - 1));
            }
            if (!ok) continue;
            int v = k.countViolations(m);
            if (v == -1 || v > best) continue;
            Combat c = new Combat(me);
            for (Map.Entry<Card, GameEntity> e : m.entrySet()) c.addAttacker(e.getKey(), e.getValue());
            out.add(attacks(c));
        }
        return out;
    }

    /** Every block the engine allows: each of the seat's creatures blocks a set of attackers it can block (added one
     *  at a time through CombatUtil.canBlock), kept when CombatUtil.validateBlocks finds nothing wrong. */
    static Set<String> legalBlocks(Game g, Function<Game, Combat> attack) {
        Player me = me(g);
        List<Card> mine = new ArrayList<>(me.getCreaturesInPlay());
        List<Card> atk = new ArrayList<>(attack.apply(g).getAttackers());
        int per = 1 << atk.size();
        int total = (int) Math.pow(per, mine.size());
        Set<String> out = new TreeSet<>();
        for (int code = 0; code < total; code++) {
            Combat c = attack.apply(g);
            boolean ok = true;
            for (int i = 0, x = code; i < mine.size() && ok; i++, x /= per) {
                int mask = x % per;
                for (int a = 0; a < atk.size(); a++) {
                    if ((mask & (1 << a)) == 0) continue;
                    Card attacker = c.getAttackers().get(a);
                    if (!CombatUtil.canBlock(attacker, mine.get(i), c)) { ok = false; break; }
                    c.addBlocker(attacker, mine.get(i));
                }
            }
            if (ok && CombatUtil.validateBlocks(c, me) == null) out.add(blocksOf(c));
        }
        return out;
    }

    /** The rival attacks the seat with the named creatures (already on its battlefield). */
    static Function<Game, Combat> rivalAttacks(String... attackers) {
        return g -> {
            Combat c = new Combat(rival(g));
            for (String a : attackers) for (Card x : rival(g).getCreaturesInPlay()) if (x.getName().equals(a) && !c.isAttacking(x)) { c.addAttacker(x, me(g)); break; }
            g.getAction().checkStaticAbilities();
            return c;
        };
    }

    static String damage(Map<Card, Integer> m, List<String> blockers) {
        List<String> parts = new ArrayList<>();
        for (String b : blockers) {
            int v = 0;
            for (Map.Entry<Card, Integer> e : m.entrySet()) if (e.getKey() != null && e.getKey().getName().equals(b)) v += e.getValue();
            parts.add(b + "=" + v);
        }
        Integer over = m.get(null);
        if (over != null && over > 0) parts.add("player=" + over);
        return String.join(",", parts);
    }

    static Set<String> tramples(String blocker, int lethal, int total) {
        Set<String> out = new TreeSet<>();
        for (int k = lethal; k <= total; k++) out.add(blocker + "=" + k + (total - k > 0 ? ",player=" + (total - k) : ""));
        return out;
    }

    static String yesNo(boolean b) { return b ? "yes" : "no"; }

    // ------------------------------------------------------------------ the catalog

    static List<EnvGrader.Instance> all() {
        List<EnvGrader.Instance> out = new ArrayList<>();
        combat(out);
        play(out);
        targets(out);
        modes(out);
        costs(out);
        triggers(out);
        gameStart(out);
        library(out);
        choices(out);
        variants(out);
        return out;
    }

    private static EnvGrader.Instance inst(String id, String family, String hook, Supplier<Game> board,
            BiFunction<Game, PlayerController, String> drive, Function<Game, Set<String>> legal, int minDistinct, int minAsks,
            List<List<String>> profiles, String... show) {
        return new EnvGrader.Instance(id, family, hook, board, drive, legal, minDistinct, minAsks, profiles, show);
    }

    /** The engine's declare-attackers step (PhaseHandler.declareAttackersTurnBasedAction): the game's combat is set,
     *  and a declaration CombatUtil.validateAttackers refuses is asked again. Three tries here, where the engine
     *  would ask forever; what stands after them is the outcome. */
    private static BiFunction<Game, PlayerController, String> declareAttackers() {
        return (g, c) -> {
            Combat combat = new Combat(me(g));
            g.getPhaseHandler().setCombat(combat);
            for (int i = 0; i < 3; i++) {
                c.declareAttackers(me(g), combat);
                combat.removeAbsentCombatants();
                if (CombatUtil.validateAttackers(combat)) break;
            }
            return attacks(combat);
        };
    }

    private static BiFunction<Game, PlayerController, String> declareBlockers(Function<Game, Combat> attack) {
        return (g, c) -> { Combat combat = attack.apply(g); c.declareBlockers(me(g), combat); return blocksOf(combat); };
    }

    static void combat(List<EnvGrader.Instance> out) {
        PhaseType atk = PhaseType.COMBAT_DECLARE_ATTACKERS, blk = PhaseType.COMBAT_DECLARE_BLOCKERS;
        out.add(inst("combat.attack.choose-attackers", "combat", "declareAttackers",
                board(atk, g -> { mine(g, "Grizzly Bears", ZoneType.Battlefield); mine(g, "Hill Giant", ZoneType.Battlefield); }),
                declareAttackers(), EnvInstances::legalAttacks, 0, 1, repeat(subsets(2, 1)), "Grizzly Bears", "Hill Giant"));
        out.add(inst("combat.attack.planeswalker", "combat", "declareAttackers",
                board(atk, g -> { mine(g, "Grizzly Bears", ZoneType.Battlefield); theirs(g, "Jace, the Mind Sculptor", ZoneType.Battlefield); }),
                declareAttackers(), EnvInstances::legalAttacks, 0, 1, repeat(subsets(1, 3)), "Grizzly Bears", "Jace, the Mind Sculptor"));
        out.add(inst("combat.attack.battle", "combat", "declareAttackers",
                board(atk, g -> {
                    mine(g, "Grizzly Bears", ZoneType.Battlefield);
                    mine(g, "Invasion of Gobakhan", ZoneType.Battlefield).setProtectingPlayer(rival(g));
                }),
                declareAttackers(), EnvInstances::legalAttacks, 0, 1, repeat(subsets(1, 3)), "Grizzly Bears", "Invasion of Gobakhan"));
        out.add(inst("combat.attack.must-attack", "combat", "declareAttackers",
                board(atk, g -> { mine(g, "Bloodrock Cyclops", ZoneType.Battlefield); mine(g, "Grizzly Bears", ZoneType.Battlefield); }),
                declareAttackers(), EnvInstances::legalAttacks, 0, 1, repeat(subsets(2, 1)), "Bloodrock Cyclops", "Grizzly Bears"));
        out.add(inst("combat.attack.cant-attack", "combat", "declareAttackers",
                board(atk, g -> {
                    mine(g, "Wall of Wood", ZoneType.Battlefield);
                    mine(g, "Grizzly Bears", ZoneType.Battlefield);
                    mine(g, "Hill Giant", ZoneType.Battlefield).setSickness(true);
                }),
                declareAttackers(), EnvInstances::legalAttacks, 0, 1, repeat(subsets(3, 1)), "Grizzly Bears"));
        out.add(inst("combat.attack.tax", "combat", "payCombatCost",
                board(PhaseType.MAIN1, g -> {
                    theirs(g, "Ghostly Prison", ZoneType.Battlefield);
                    mine(g, "Grizzly Bears", ZoneType.Battlefield);
                    mine(g, "Forest", ZoneType.Battlefield);
                    mine(g, "Forest", ZoneType.Battlefield);
                }),
                (g, c) -> {
                    g.getPhaseHandler().devAdvanceToPhase(PhaseType.COMBAT_DECLARE_ATTACKERS);
                    int tapped = 0;
                    for (Card f : me(g).getCardsIn(ZoneType.Battlefield)) if (f.getName().equals("Forest") && f.isTapped()) tapped++;
                    return (g.getCombat() == null ? "no attack" : attacks(g.getCombat())) + "|tapped=" + tapped;
                },
                g -> set("no attack|tapped=0", "Grizzly Bears->Rival|tapped=2"), 0, 1, repeat(subsets(1, 3)), "Ghostly Prison"));

        Function<Game, Combat> lions = rivalAttacks("Savannah Lions");
        out.add(inst("combat.block.choose-blockers", "combat", "declareBlockers",
                board(blk, g -> { mine(g, "Grizzly Bears", ZoneType.Battlefield); mine(g, "Hill Giant", ZoneType.Battlefield);
                    theirs(g, "Savannah Lions", ZoneType.Battlefield); }),
                declareBlockers(lions), g -> legalBlocks(g, lions), 0, 1, repeat(blocks(2, 1)), "Savannah Lions", "Grizzly Bears", "Hill Giant"));
        Function<Game, Combat> brute = rivalAttacks("Boggart Brute");
        out.add(inst("combat.block.menace", "combat", "declareBlockers",
                board(blk, g -> { mine(g, "Grizzly Bears", ZoneType.Battlefield); mine(g, "Hill Giant", ZoneType.Battlefield);
                    theirs(g, "Boggart Brute", ZoneType.Battlefield); }),
                declareBlockers(brute), g -> legalBlocks(g, brute), 0, 1, repeat(blocks(2, 1)), "Boggart Brute"));
        Function<Game, Combat> drake = rivalAttacks("Wind Drake");
        out.add(inst("combat.block.flying-and-reach", "combat", "declareBlockers",
                board(blk, g -> { mine(g, "Grizzly Bears", ZoneType.Battlefield); mine(g, "Giant Spider", ZoneType.Battlefield);
                    theirs(g, "Wind Drake", ZoneType.Battlefield); }),
                declareBlockers(drake), g -> legalBlocks(g, drake), 0, 1, repeat(blocks(2, 1)), "Wind Drake", "Giant Spider"));
        out.add(inst("combat.block.cant-block", "combat", "declareBlockers",
                board(blk, g -> { mine(g, "Goblin Raider", ZoneType.Battlefield); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                    mine(g, "Hill Giant", ZoneType.Battlefield).setTapped(true); theirs(g, "Savannah Lions", ZoneType.Battlefield); }),
                declareBlockers(lions), g -> legalBlocks(g, lions), 0, 1, repeat(blocks(3, 1)), "Savannah Lions", "Grizzly Bears"));
        Function<Game, Combat> unicorn = rivalAttacks("Prized Unicorn");
        out.add(inst("combat.block.must-block", "combat", "declareBlockers",
                board(blk, g -> { mine(g, "Grizzly Bears", ZoneType.Battlefield); mine(g, "Hill Giant", ZoneType.Battlefield);
                    theirs(g, "Prized Unicorn", ZoneType.Battlefield); }),
                declareBlockers(unicorn), g -> legalBlocks(g, unicorn), 0, 1, repeat(blocks(2, 1)), "Prized Unicorn"));
        Function<Game, Combat> two = rivalAttacks("Grizzly Bears", "Savannah Lions");
        out.add(inst("combat.block.two-attackers", "combat", "declareBlockers",
                board(blk, g -> { mine(g, "Palace Guard", ZoneType.Battlefield); mine(g, "Hill Giant", ZoneType.Battlefield);
                    theirs(g, "Grizzly Bears", ZoneType.Battlefield); theirs(g, "Savannah Lions", ZoneType.Battlefield); }),
                declareBlockers(two), g -> legalBlocks(g, two), 0, 1, repeat(blocks(2, 2)), "Palace Guard", "Grizzly Bears", "Savannah Lions"));

        List<String> orderAnswers = new ArrayList<>(orders(2));
        orderAnswers.add(json("options", EnvGrader.ints(List.of(3)), "option", 3));
        out.add(inst("combat.damage.order", "combat", "orderBlockers",
                board(blk, g -> { mine(g, "Colossal Dreadmaw", ZoneType.Battlefield); theirs(g, "Grizzly Bears", ZoneType.Battlefield);
                    theirs(g, "Savannah Lions", ZoneType.Battlefield); }),
                (g, c) -> ordered(c.orderBlockers(find(g, "Colossal Dreadmaw"),
                        new CardCollection(Arrays.asList(find(g, "Savannah Lions"), find(g, "Grizzly Bears"))))),
                g -> set("Grizzly Bears>Savannah Lions", "Savannah Lions>Grizzly Bears"), 0, 1, repeat(orderAnswers),
                "Grizzly Bears", "Savannah Lions"));
        out.add(inst("combat.damage.trample", "combat", "assignCombatDamage",
                board(PhaseType.COMBAT_DAMAGE, g -> { mine(g, "Colossal Dreadmaw", ZoneType.Battlefield); theirs(g, "Grizzly Bears", ZoneType.Battlefield); }),
                (g, c) -> damage(c.assignCombatDamage(find(g, "Colossal Dreadmaw"), new CardCollection(find(g, "Grizzly Bears")), null, 6,
                        rival(g), true), List.of("Grizzly Bears")),
                g -> tramples("Grizzly Bears", 2, 6), 0, 1, repeat(splits(6)), "Colossal Dreadmaw", "Grizzly Bears"));
        out.add(inst("combat.damage.trample-lethal-is-the-rules", "combat", "assignCombatDamage",
                board(PhaseType.COMBAT_DAMAGE, g -> { mine(g, "Colossal Dreadmaw", ZoneType.Battlefield); theirs(g, "Darksteel Myr", ZoneType.Battlefield); }),
                (g, c) -> damage(c.assignCombatDamage(find(g, "Colossal Dreadmaw"), new CardCollection(find(g, "Darksteel Myr")), null, 6,
                        rival(g), true), List.of("Darksteel Myr")),
                g -> tramples("Darksteel Myr", 1, 6), 0, 1, repeat(splits(6)), "Darksteel Myr"));
        out.add(inst("combat.damage.divide-among-blockers", "combat", "assignCombatDamage",
                board(PhaseType.COMBAT_DAMAGE, g -> { mine(g, "Craw Wurm", ZoneType.Battlefield); theirs(g, "Grizzly Bears", ZoneType.Battlefield);
                    theirs(g, "Llanowar Elves", ZoneType.Battlefield); }),
                (g, c) -> damage(c.assignCombatDamage(find(g, "Craw Wurm"),
                        new CardCollection(Arrays.asList(find(g, "Grizzly Bears"), find(g, "Llanowar Elves"))), null, 6, rival(g), true),
                        List.of("Grizzly Bears", "Llanowar Elves")),
                g -> { Set<String> s = new TreeSet<>(); for (int k = 0; k <= 6; k++) s.add("Grizzly Bears=" + k + ",Llanowar Elves=" + (6 - k)); return s; },
                0, 1, repeat(splits(6)), "Grizzly Bears", "Llanowar Elves"));
        out.add(inst("combat.damage.double-strike-steps", "combat", "assignCombatDamage",
                board(blk, g -> {
                    Card swift = mine(g, "Boros Swiftblade", ZoneType.Battlefield);
                    Card bears = theirs(g, "Grizzly Bears", ZoneType.Battlefield), rune = theirs(g, "Runeclaw Bear", ZoneType.Battlefield);
                    Combat combat = new Combat(me(g));
                    combat.addAttacker(swift, rival(g));
                    swift.setTapped(true);
                    combat.addBlocker(swift, bears);
                    combat.addBlocker(swift, rune);
                    combat.setBlocked(swift, true);
                    // What the declare-blockers step leaves behind: the blockers each attacker deals damage to.
                    combat.orderBlockersForDamageAssignment(swift, new CardCollection(Arrays.asList(bears, rune)));
                    g.getPhaseHandler().setCombat(combat);
                }),
                (g, c) -> {
                    // The two damage steps as PhaseHandler.onPhaseBegin runs them, with state-based actions between.
                    Combat combat = g.getCombat();
                    if (combat.assignCombatDamage(true)) combat.dealAssignedDamage();
                    g.getAction().checkStateEffects(true);
                    combat.removeAbsentCombatants();
                    if (combat.assignCombatDamage(false)) combat.dealAssignedDamage();
                    g.getAction().checkStateEffects(true);
                    List<String> marks = new ArrayList<>();
                    for (Card x : rival(g).getCreaturesInPlay()) marks.add(x.getName() + " d" + x.getDamage());
                    java.util.Collections.sort(marks);
                    return "dead=" + names(rival(g).getCardsIn(ZoneType.Graveyard)) + " " + marks;
                },
                null, 3, 2, seq(splits(1), splits(1)), "Boros Swiftblade", "Grizzly Bears", "Runeclaw Bear"));
        out.add(inst("combat.trick.in-the-blockers-step", "combat", "chooseSpellAbilityToPlay",
                board(blk, g -> {
                    Card bears = mine(g, "Grizzly Bears", ZoneType.Battlefield);
                    Card giant = theirs(g, "Hill Giant", ZoneType.Battlefield);
                    mine(g, "Forest", ZoneType.Battlefield);
                    mine(g, "Giant Growth", ZoneType.Hand);
                    Combat combat = new Combat(me(g));
                    combat.addAttacker(bears, rival(g));
                    bears.setTapped(true);
                    combat.addBlocker(bears, giant);
                    combat.setBlocked(bears, true);
                    g.getPhaseHandler().setCombat(combat);
                }),
                EnvInstances::play, null, 3, 1, seq(option(3), option(3)), "Giant Growth", "Hill Giant"));
        out.add(inst("combat.prevention.shield-split", "combat", "divideShield",
                board(PhaseType.MAIN1, g -> { mine(g, "Mana Clash", ZoneType.Hand); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                    mine(g, "Savannah Lions", ZoneType.Battlefield); }),
                (g, c) -> {
                    Map<GameEntity, Integer> affected = new LinkedHashMap<>();
                    affected.put(find(g, "Grizzly Bears"), 0);
                    affected.put(find(g, "Savannah Lions"), 0);
                    Map<GameEntity, Integer> r = c.divideShield(find(g, "Mana Clash"), affected, 2);
                    return "Grizzly Bears=" + r.getOrDefault(find(g, "Grizzly Bears"), 0) + ",Savannah Lions=" + r.getOrDefault(find(g, "Savannah Lions"), 0);
                },
                g -> set("Grizzly Bears=0,Savannah Lions=2", "Grizzly Bears=1,Savannah Lions=1", "Grizzly Bears=2,Savannah Lions=0"),
                0, 1, repeat(splits(2)), "Grizzly Bears", "Savannah Lions"));
        out.add(inst("combat.replacement.which-applies", "combat", "chooseSingleReplacementEffect",
                board(PhaseType.MAIN1, g -> { mine(g, "Furnace of Rath", ZoneType.Battlefield); mine(g, "Dictate of the Twin Gods", ZoneType.Battlefield); }),
                (g, c) -> {
                    List<ReplacementEffect> res = new ArrayList<>();
                    res.add(find(g, "Furnace of Rath").getReplacementEffects().get(0));
                    res.add(find(g, "Dictate of the Twin Gods").getReplacementEffects().get(0));
                    ReplacementEffect r = c.chooseSingleReplacementEffect(res);
                    return r == null ? "none" : r.getHostCard().getName();
                },
                g -> set("Furnace of Rath", "Dictate of the Twin Gods"), 0, 1, repeat(option(2)), "Furnace of Rath", "Dictate of the Twin Gods"));
    }

    static void play(List<EnvGrader.Instance> out) {
        out.add(inst("play.choose-a-play", "play", "chooseSpellAbilityToPlay",
                board(PhaseType.MAIN1, g -> { mine(g, "Forest", ZoneType.Battlefield); mine(g, "Forest", ZoneType.Battlefield);
                    mine(g, "Grizzly Bears", ZoneType.Hand); mine(g, "Forest", ZoneType.Hand); }),
                EnvInstances::play, null, 3, 1, repeat(option(4)), "Grizzly Bears", "Forest"));
    }

    static void targets(List<EnvGrader.Instance> out) {
        BiFunction<Game, PlayerController, String> target = (g, c) -> {
            SpellAbility sa = spell(g, "Lightning Bolt");
            if (!c.chooseTargetsFor(sa)) return "cancel";
            List<String> t = new ArrayList<>();
            for (Card x : sa.getTargets().getTargetCards()) t.add(x.getName());
            for (Player p : sa.getTargets().getTargetPlayers()) t.add(p.getName());
            return t.isEmpty() ? "none" : String.join(",", t);
        };
        out.add(inst("targets.any-target", "targets", "chooseTargetsFor",
                board(PhaseType.MAIN1, g -> { mine(g, "Lightning Bolt", ZoneType.Hand); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                    theirs(g, "Hill Giant", ZoneType.Battlefield); }),
                target, g -> set("Seat", "Rival", "Grizzly Bears", "Hill Giant", "?cancel"), 0, 1, repeat(option(5)),
                "Lightning Bolt", "Grizzly Bears", "Hill Giant"));
        out.add(inst("targets.through-a-cast", "targets", "chooseTargetsFor",
                board(PhaseType.MAIN1, g -> { mine(g, "Mountain", ZoneType.Battlefield); mine(g, "Lightning Bolt", ZoneType.Hand);
                    theirs(g, "Hill Giant", ZoneType.Battlefield); }),
                EnvInstances::play, null, 4, 1, seq(option(3), option(3)), "Lightning Bolt", "Hill Giant"));
    }

    static void modes(List<EnvGrader.Instance> out) {
        List<String> pairs = new ArrayList<>();
        for (int a = 1; a <= 4; a++) for (int b = a + 1; b <= 4; b++) pairs.add(json("options", EnvGrader.ints(List.of(a, b)), "option", a));
        pairs.add(json("options", EnvGrader.ints(List.of(5)), "option", 5));
        out.add(inst("modes.choose-two-of-four", "modes", "chooseModeForAbility",
                board(PhaseType.MAIN1, g -> {
                    mine(g, "Badlands", ZoneType.Battlefield); mine(g, "Badlands", ZoneType.Battlefield); mine(g, "Mountain", ZoneType.Battlefield);
                    mine(g, "Kolaghan's Command", ZoneType.Hand); mine(g, "Grizzly Bears", ZoneType.Graveyard);
                    theirs(g, "Ornithopter", ZoneType.Battlefield); theirs(g, "Savannah Lions", ZoneType.Battlefield);
                    theirs(g, "Lightning Bolt", ZoneType.Hand);
                }),
                EnvInstances::play, null, 6, 2, seq(option(2), pairs, option(2)), "Kolaghan's Command"));
    }

    static void costs(List<EnvGrader.Instance> out) {
        out.add(inst("costs.sacrifice-as-cost", "costs", "getCostDecisionMaker",
                board(PhaseType.MAIN1, g -> { mine(g, "Goblin Bombardment", ZoneType.Battlefield); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                    mine(g, "Memnite", ZoneType.Battlefield); mine(g, "Savannah Lions", ZoneType.Battlefield); }),
                EnvInstances::play, null, 3, 2, seq(option(2), option(3), option(2)), "Goblin Bombardment", "Memnite"));
        List<String> delve = new ArrayList<>();
        for (int mask = 0; mask < 16; mask++) {
            List<Integer> s = new ArrayList<>();
            for (int i = 0; i < 4; i++) if ((mask & (1 << i)) != 0) s.add(i + 1);
            if (s.size() >= 3) delve.add(json("options", EnvGrader.ints(s), "number", s.size(), "option", s.get(0)));
        }
        out.add(inst("costs.delve", "costs", "chooseCardsToDelve",
                board(PhaseType.MAIN1, g -> {
                    for (int i = 0; i < 5; i++) mine(g, "Island", ZoneType.Battlefield);
                    mine(g, "Treasure Cruise", ZoneType.Hand);
                    for (String n : new String[] {"Lightning Bolt", "Counterspell", "Ponder", "Brainstorm"}) mine(g, n, ZoneType.Graveyard);
                    for (int i = 0; i < 4; i++) mine(g, "Island", ZoneType.Library);
                }),
                EnvInstances::play, null, 5, 2, seq(option(2), delve), "Treasure Cruise", "Counterspell"));
        out.add(inst("costs.kicker", "costs", "chooseOptionalCosts",
                board(PhaseType.MAIN1, g -> { for (int i = 0; i < 5; i++) mine(g, "Mountain", ZoneType.Battlefield); mine(g, "Burst Lightning", ZoneType.Hand); }),
                (g, c) -> {
                    SpellAbility sa = spell(g, "Burst Lightning");
                    List<OptionalCostValue> costs = forge.game.GameActionUtil.getOptionalCostValues(sa);
                    List<OptionalCostValue> r = c.chooseOptionalCosts(sa, costs);
                    return r == null || r.isEmpty() ? "none" : String.valueOf(r.get(0).getType());
                },
                g -> set("none", "Kicker1"), 0, 1, repeat(subsets(1, 0)), "Burst Lightning"));
        out.add(inst("costs.unless-cost", "costs", "payCostToPreventEffect",
                board(PhaseType.MAIN1, g -> { mine(g, "Mountain", ZoneType.Battlefield);
                    LineFindingProbeTest.rivalCasts(g, "Force Spike", mine(g, "Lightning Bolt", ZoneType.Hand)); }),
                (g, c) -> yesNo(c.payCostToPreventEffect(new forge.game.cost.Cost("1", false), g.getStack().peekAbility(), false,
                        new FCollection<>(me(g))))
                        + "|tapped=" + find(g, "Mountain").isTapped(),
                g -> set("yes|tapped=true", "no|tapped=false"), 0, 1, repeat(option(2)), "Force Spike"));
        out.add(inst("costs.pay-life", "costs", "confirmPayment",
                board(PhaseType.MAIN1, g -> mine(g, "Snuff Out", ZoneType.Hand)),
                (g, c) -> yesNo(c.confirmPayment(null, "Pay 4 life?", spell(g, "Snuff Out"))),
                g -> set("yes", "no"), 0, 1, repeat(option(2)), "Snuff Out"));
        out.add(inst("costs.x", "costs", "announceRequirements",
                board(PhaseType.MAIN1, g -> { for (int i = 0; i < 3; i++) mine(g, "Forest", ZoneType.Battlefield);
                    mine(g, "Green Sun's Zenith", ZoneType.Hand); mine(g, "Llanowar Elves", ZoneType.Library); mine(g, "Forest", ZoneType.Library); }),
                EnvInstances::play, null, 3, 2, seq(option(2), numbers(0, 2), option(2)), "Green Sun's Zenith"));
    }

    static void triggers(List<EnvGrader.Instance> out) {
        List<String> two = new ArrayList<>(orders(2));
        two.add(json("options", EnvGrader.ints(List.of(3)), "option", 3));
        out.add(inst("triggers.order-simultaneous", "triggers-and-replacements", "orderSimultaneousSa",
                board(PhaseType.MAIN1, g -> { theirs(g, "Savannah Lions", ZoneType.Battlefield); mine(g, "Palace Jailer", ZoneType.Hand); }),
                (g, c) -> {
                    g.getAction().moveToPlay(find(g, "Palace Jailer"), null, null);
                    g.getTriggerHandler().runWaitingTriggers();
                    g.getStack().addAllTriggeredAbilitiesToStack();
                    return digest(g);
                },
                null, 2, 1, repeat(two), "Palace Jailer"));
        out.add(inst("triggers.optional", "triggers-and-replacements", "confirmTrigger",
                board(PhaseType.MAIN1, g -> mine(g, "Recruiter of the Guard", ZoneType.Battlefield)),
                (g, c) -> {
                    Card src = find(g, "Recruiter of the Guard");
                    SpellAbility sa = src.getTriggers().iterator().next().ensureAbility();
                    sa.setActivatingPlayer(me(g));
                    WrappedAbility w = new WrappedAbility(src.getTriggers().iterator().next(), sa, me(g)) {
                        @Override public boolean isMandatory() { return false; }
                    };
                    return yesNo(c.confirmTrigger(w));
                },
                g -> set("yes", "no"), 0, 1, repeat(option(2)), "Recruiter of the Guard"));
        out.add(inst("replacements.optional", "triggers-and-replacements", "confirmReplacementEffect",
                board(PhaseType.MAIN1, g -> mine(g, "Mox Diamond", ZoneType.Hand)),
                (g, c) -> yesNo(c.confirmReplacementEffect(find(g, "Mox Diamond").getReplacementEffects().get(0), spell(g, "Mox Diamond"), null,
                        "Do you want to discard a land card instead of putting Mox Diamond onto the battlefield?")),
                g -> set("yes", "no"), 0, 1, repeat(option(2)), "Mox Diamond"));
    }

    static void gameStart(List<EnvGrader.Instance> out) {
        String[] hand = {"Lightning Bolt", "Ponder", "Brainstorm", "Grizzly Bears", "Savannah Lions", "Llanowar Elves", "Counterspell"};
        out.add(inst("game-start.mulligan", "game-start", "mulliganKeepHand",
                board(PhaseType.MAIN1, g -> { for (String n : hand) mine(g, n, ZoneType.Hand); }),
                (g, c) -> c.mulliganKeepHand(me(g), 0) ? "keep" : "mulligan",
                g -> set("keep", "mulligan"), 0, 1, repeat(keeps()), "Lightning Bolt", "Counterspell"));
        out.add(inst("game-start.bottom-after-mulligan", "game-start", "tuckCardsViaMulligan",
                board(PhaseType.MAIN1, g -> { for (String n : hand) mine(g, n, ZoneType.Hand); }),
                (g, c) -> names(c.tuckCardsViaMulligan(new CardCollection(me(g).getCardsIn(ZoneType.Hand)), 1)),
                g -> set(hand), 0, 1, repeat(option(7)), "Lightning Bolt", "Counterspell"));
        out.add(inst("game-start.play-or-draw", "game-start", "chooseStartingPlayer",
                board(PhaseType.MAIN1, g -> { }),
                (g, c) -> c.chooseStartingPlayer(true).getName(),
                g -> set("Seat", "Rival"), 0, 1, repeat(option(2))));
    }

    static void library(List<EnvGrader.Instance> out) {
        Set<String> partitions = set("top=|bottom=Counterspell,Lightning Bolt", "top=Counterspell|bottom=Lightning Bolt",
                "top=Lightning Bolt|bottom=Counterspell", "top=Counterspell,Lightning Bolt|bottom=");
        out.add(inst("library.scry-two", "library", "arrangeForScry",
                board(PhaseType.MAIN1, g -> { mine(g, "Lightning Bolt", ZoneType.Library); mine(g, "Counterspell", ZoneType.Library); }),
                (g, c) -> {
                    org.apache.commons.lang3.tuple.ImmutablePair<CardCollection, CardCollection> r =
                            c.arrangeForScry(new CardCollection(Arrays.asList(find(g, "Lightning Bolt"), find(g, "Counterspell"))));
                    return "top=" + names(r.getLeft()) + "|bottom=" + names(r.getRight());
                },
                g -> partitions, 0, 1, repeat(subsets(2, 0)), "Lightning Bolt", "Counterspell"));
        Set<String> surveil = set("top=|grave=Counterspell,Lightning Bolt", "top=Counterspell|grave=Lightning Bolt",
                "top=Lightning Bolt|grave=Counterspell", "top=Counterspell,Lightning Bolt|grave=");
        out.add(inst("library.surveil-two", "library", "arrangeForSurveil",
                board(PhaseType.MAIN1, g -> { mine(g, "Lightning Bolt", ZoneType.Library); mine(g, "Counterspell", ZoneType.Library); }),
                (g, c) -> {
                    org.apache.commons.lang3.tuple.ImmutablePair<CardCollection, CardCollection> r =
                            c.arrangeForSurveil(new CardCollection(Arrays.asList(find(g, "Lightning Bolt"), find(g, "Counterspell"))));
                    return "top=" + names(r.getLeft()) + "|grave=" + names(r.getRight());
                },
                g -> surveil, 0, 1, repeat(subsets(2, 0)), "Lightning Bolt", "Counterspell"));
        out.add(inst("library.search", "library", "chooseSingleCardForZoneChange",
                board(PhaseType.MAIN1, g -> { mine(g, "Demonic Tutor", ZoneType.Hand); mine(g, "Lightning Bolt", ZoneType.Library);
                    mine(g, "Counterspell", ZoneType.Library); }),
                (g, c) -> {
                    Card got = c.chooseSingleCardForZoneChange(ZoneType.Hand, Arrays.asList(ZoneType.Library), spell(g, "Demonic Tutor"),
                            new CardCollection(Arrays.asList(find(g, "Lightning Bolt"), find(g, "Counterspell"))), null, "Select a card", false, me(g));
                    return got == null ? "none" : got.getName();
                },
                g -> set("Lightning Bolt", "Counterspell"), 0, 1, repeat(option(2)), "Lightning Bolt", "Counterspell"));
        out.add(inst("library.put-back-order", "library", "orderMoveToZoneList",
                board(PhaseType.MAIN1, g -> { mine(g, "Brainstorm", ZoneType.Hand); mine(g, "Lightning Bolt", ZoneType.Hand);
                    mine(g, "Counterspell", ZoneType.Hand); }),
                (g, c) -> ordered(c.orderMoveToZoneList(new CardCollection(Arrays.asList(find(g, "Lightning Bolt"), find(g, "Counterspell"))),
                        ZoneType.Library, spell(g, "Brainstorm"))),
                g -> set("Lightning Bolt>Counterspell", "Counterspell>Lightning Bolt"), 0, 1, repeat(orders(2)), "Lightning Bolt", "Counterspell"));
    }

    static void choices(List<EnvGrader.Instance> out) {
        out.add(inst("choices.sacrifice", "choices", "choosePermanentsToSacrifice",
                board(PhaseType.MAIN1, g -> { theirs(g, "Diabolic Edict", ZoneType.Hand); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                    mine(g, "Savannah Lions", ZoneType.Battlefield); }),
                (g, c) -> {
                    SpellAbility sa = find(g, "Diabolic Edict").getFirstSpellAbility();
                    sa.setActivatingPlayer(rival(g));
                    return names(c.choosePermanentsToSacrifice(sa, 1, 1,
                            new CardCollection(Arrays.asList(find(g, "Grizzly Bears"), find(g, "Savannah Lions"))), "Sacrifice a creature"));
                },
                g -> set("Grizzly Bears", "Savannah Lions"), 0, 1, repeat(option(2)), "Diabolic Edict", "Grizzly Bears", "Savannah Lions"));
        out.add(inst("choices.discard-two", "choices", "chooseCardsToDiscardFrom",
                board(PhaseType.MAIN1, g -> { mine(g, "Faithless Looting", ZoneType.Hand); mine(g, "Lightning Bolt", ZoneType.Hand);
                    mine(g, "Counterspell", ZoneType.Hand); mine(g, "Ponder", ZoneType.Hand); }),
                (g, c) -> {
                    CardCollection hand = new CardCollection(Arrays.asList(find(g, "Lightning Bolt"), find(g, "Counterspell"), find(g, "Ponder")));
                    return names(c.chooseCardsToDiscardFrom(me(g), spell(g, "Faithless Looting"), hand, 2, 2, hand));
                },
                g -> set("Counterspell,Lightning Bolt", "Lightning Bolt,Ponder", "Counterspell,Ponder"), 0, 1, repeat(subsets(3, 0)),
                "Faithless Looting", "Ponder"));
        out.add(inst("choices.cleanup-discard", "choices", "chooseCardsToDiscardToMaximumHandSize",
                board(PhaseType.CLEANUP, g -> { mine(g, "Lightning Bolt", ZoneType.Hand); mine(g, "Counterspell", ZoneType.Hand); mine(g, "Ponder", ZoneType.Hand); }),
                (g, c) -> names(c.chooseCardsToDiscardToMaximumHandSize(1)),
                g -> set("Lightning Bolt", "Counterspell", "Ponder"), 0, 1, repeat(option(3)), "Counterspell", "Ponder"));
        out.add(inst("choices.number", "choices", "chooseNumber",
                board(PhaseType.MAIN1, g -> mine(g, "Galvanic Discharge", ZoneType.Hand)),
                (g, c) -> String.valueOf(c.chooseNumber(spell(g, "Galvanic Discharge"), "amount of energy to pay", 0, 3)),
                g -> set("0", "1", "2", "3"), 0, 1, repeat(numbers(0, 3)), "Galvanic Discharge"));
        out.add(inst("choices.binary", "choices", "chooseBinary",
                board(PhaseType.MAIN1, g -> mine(g, "Twiddle", ZoneType.Hand)),
                (g, c) -> yesNo(c.chooseBinary(spell(g, "Twiddle"), "Tap or untap the permanent?", BinaryChoiceType.TapOrUntap, (Boolean) null)),
                g -> set("yes", "no"), 0, 1, repeat(option(2)), "Twiddle"));
        out.add(inst("choices.colour", "choices", "chooseColor",
                board(PhaseType.MAIN1, g -> mine(g, "Painter's Servant", ZoneType.Battlefield)),
                (g, c) -> String.valueOf(c.chooseColor("Choose a color", spell(g, "Painter's Servant"),
                        ColorSet.fromEnums(MagicColor.Color.WHITE, MagicColor.Color.BLUE, MagicColor.Color.RED))),
                g -> set(String.valueOf(MagicColor.WHITE), String.valueOf(MagicColor.BLUE), String.valueOf(MagicColor.RED)), 0, 1,
                repeat(option(3)), "Painter's Servant"));
        out.add(inst("choices.yes-no", "choices", "confirmAction",
                board(PhaseType.MAIN1, g -> mine(g, "Ponder", ZoneType.Hand)),
                (g, c) -> yesNo(c.confirmAction(spell(g, "Ponder"), PlayerActionConfirmMode.Random, "Do you want to shuffle your library?", null, null, null)),
                g -> set("yes", "no"), 0, 1, repeat(option(2)), "Ponder"));
        out.add(inst("choices.entity", "choices", "chooseSingleEntityForEffect",
                board(PhaseType.MAIN1, g -> { mine(g, "Oblivion Ring", ZoneType.Battlefield); theirs(g, "Savannah Lions", ZoneType.Battlefield);
                    theirs(g, "Grizzly Bears", ZoneType.Battlefield); }),
                (g, c) -> {
                    FCollection<GameEntity> opts = new FCollection<>();
                    opts.add(find(g, "Savannah Lions"));
                    opts.add(find(g, "Grizzly Bears"));
                    GameEntity got = c.chooseSingleEntityForEffect(opts, null, spell(g, "Oblivion Ring"), "Choose a creature", false, null, null);
                    return got == null ? "none" : got.getName();
                },
                g -> set("Savannah Lions", "Grizzly Bears"), 0, 1, repeat(option(2)), "Oblivion Ring", "Savannah Lions", "Grizzly Bears"));
        out.add(inst("choices.reveal", "choices", "chooseCardsToRevealFromHand",
                board(PhaseType.MAIN1, g -> { mine(g, "Lightning Bolt", ZoneType.Hand); mine(g, "Counterspell", ZoneType.Hand); }),
                (g, c) -> { String r = names(c.chooseCardsToRevealFromHand(0, 1, new CardCollection(me(g).getCardsIn(ZoneType.Hand))));
                    return r.isEmpty() ? "none" : r; },
                g -> set("none", "Lightning Bolt", "Counterspell"), 0, 1, repeat(subsets(2, 0)), "Lightning Bolt", "Counterspell"));
    }

    static void variants(List<EnvGrader.Instance> out) {
        out.add(inst("variants.flip-call", "variants", "chooseFlipResult",
                board(PhaseType.MAIN1, g -> mine(g, "Mana Clash", ZoneType.Hand)),
                (g, c) -> yesNo(c.chooseFlipResult(find(g, "Mana Clash").getFirstSpellAbility(), me(g), true)),
                g -> set("yes", "no"), 0, 1, repeat(option(2)), "Mana Clash"));
    }
}
