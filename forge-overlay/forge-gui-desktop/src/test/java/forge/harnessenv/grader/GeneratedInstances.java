package forge.harnessenv.grader;

import static forge.harnessenv.grader.Grader.blocks;
import static forge.harnessenv.grader.Grader.option;
import static forge.harnessenv.grader.Grader.repeat;
import static forge.harnessenv.grader.Grader.seq;
import static forge.harnessenv.grader.Grader.subsets;
import static forge.harnessenv.grader.Instances.board;
import static forge.harnessenv.grader.Instances.declareAttackers;
import static forge.harnessenv.grader.Instances.declareBlockers;
import static forge.harnessenv.grader.Instances.find;
import static forge.harnessenv.grader.Instances.inst;
import static forge.harnessenv.grader.Instances.legalBlocks;
import static forge.harnessenv.grader.Instances.me;
import static forge.harnessenv.grader.Instances.mine;
import static forge.harnessenv.grader.Instances.rival;
import static forge.harnessenv.grader.Instances.rivalAttacks;
import static forge.harnessenv.grader.Instances.spell;
import static forge.harnessenv.grader.Instances.theirs;
import static forge.harnessenv.grader.Instances.target;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Function;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import forge.game.Game;
import forge.game.GameObject;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.phase.PhaseType;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

/**
 * The instances eval/harness_env/harness_env_generate.py draws from the card scripts, built from their templates: each template
 * is one board shape with the drawn card in its place, and the legal outcomes come from the engine for that card
 * (CombatUtil and AttackConstraints for combat, SpellAbility.canTarget for a target). An instance whose board cannot
 * be built (a card the database does not load) is discarded and named, never graded.
 */
public final class GeneratedInstances {
    private GeneratedInstances() { }

    /** The set file's instances that build, and the ids of those discarded with the reason. */
    public static List<Grader.Instance> load(Path set, List<String> discarded) throws IOException {
        JsonObject s = JsonParser.parseString(new String(Files.readAllBytes(set), StandardCharsets.UTF_8)).getAsJsonObject();
        List<Grader.Instance> out = new ArrayList<>();
        for (JsonElement e : s.getAsJsonArray("instances")) {
            JsonObject i = e.getAsJsonObject();
            String id = i.get("template").getAsString() + "." + i.get("id").getAsString();
            Grader.Instance in = build(id, i.get("template").getAsString(), i.get("card").getAsString());
            if (in == null) { discarded.add(id + "\tunknown template"); continue; }
            try { in.board.get(); out.add(in); }
            catch (Throwable x) { discarded.add(id + "\t" + i.get("card").getAsString() + ": " + x.getClass().getSimpleName() + " " + x.getMessage()); }
        }
        return out;
    }

    static Grader.Instance build(String id, String template, String card) {
        switch (template) {
            case "attacker-keyword": {
                Function<Game, Combat> attack = rivalAttacks(card);
                return inst(id, "combat", "declareBlockers",
                        board(PhaseType.COMBAT_DECLARE_BLOCKERS, g -> { mine(g, "Grizzly Bears", ZoneType.Battlefield);
                            mine(g, "Hill Giant", ZoneType.Battlefield); mine(g, "Giant Spider", ZoneType.Battlefield);
                            theirs(g, card, ZoneType.Battlefield); }),
                        declareBlockers(attack), g -> legalBlocks(g, attack), 0, 1, repeat(blocks(3, 1)), card);
            }
            case "blocker-keyword": {
                Function<Game, Combat> attack = rivalAttacks("Savannah Lions", "Wind Drake");
                return inst(id, "combat", "declareBlockers",
                        board(PhaseType.COMBAT_DECLARE_BLOCKERS, g -> { mine(g, card, ZoneType.Battlefield); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                            theirs(g, "Savannah Lions", ZoneType.Battlefield); theirs(g, "Wind Drake", ZoneType.Battlefield); }),
                        declareBlockers(attack), g -> legalBlocks(g, attack), 0, 1, repeat(blocks(2, 2)), card);
            }
            case "attacker":
                return inst(id, "combat", "declareAttackers",
                        board(PhaseType.COMBAT_DECLARE_ATTACKERS, g -> { mine(g, card, ZoneType.Battlefield); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                            theirs(g, "Jace, the Mind Sculptor", ZoneType.Battlefield); }),
                        // Who attacks, then where each one attacks, one decision each: two attackers may go different ways.
                        declareAttackers(), Instances::legalAttacks, 0, 1, seq(subsets(2, 0), option(2), option(2)), card);
            case "single-target-spell":
                return inst(id, "targets", "chooseTargetsFor",
                        board(PhaseType.MAIN1, g -> { mine(g, card, ZoneType.Hand); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                            theirs(g, "Hill Giant", ZoneType.Battlefield); theirs(g, "Ornithopter", ZoneType.Battlefield); }),
                        target(card), g -> legalTargets(g, card), 0, 1, repeat(option(6)), card);
            case "sac-outlet":
                return inst(id, "costs", "getCostDecisionMaker",
                        // A library to draw from and a hand to discard: outlets that draw or discard show which creature paid.
                        board(PhaseType.MAIN1, g -> { mine(g, card, ZoneType.Battlefield); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                            mine(g, "Memnite", ZoneType.Battlefield); mine(g, "Savannah Lions", ZoneType.Battlefield);
                            for (String c : new String[] {"Ponder", "Brainstorm", "Opt", "Island", "Forest"}) mine(g, c, ZoneType.Library);
                            for (String c : new String[] {"Lightning Bolt", "Counterspell", "Swamp"}) mine(g, c, ZoneType.Hand);
                            theirs(g, "Hill Giant", ZoneType.Battlefield); }),
                        Instances::play, null, 3, 2, seq(option(3), option(4), option(4)), card);
            case "charm":
                return inst(id, "modes", "chooseModeForAbility",
                        board(PhaseType.MAIN1, g -> {
                            for (String land : new String[] {"Plains", "Island", "Swamp", "Mountain", "Forest"})
                                for (int n = 0; n < 2; n++) mine(g, land, ZoneType.Battlefield);
                            mine(g, card, ZoneType.Hand); mine(g, "Grizzly Bears", ZoneType.Battlefield);
                            for (String c : new String[] {"Ponder", "Brainstorm", "Opt"}) mine(g, c, ZoneType.Library);
                            theirs(g, "Hill Giant", ZoneType.Battlefield); theirs(g, "Ornithopter", ZoneType.Battlefield);
                            theirs(g, "Lightning Bolt", ZoneType.Hand);
                        }),
                        Instances::play, null, 2, 2, seq(option(3), option(4), option(3)), card);
            default:
                return null;
        }
    }

    /** What the spell can target on this board, by the engine (SpellAbility.canTarget), and cancelling. */
    static Set<String> legalTargets(Game g, String card) {
        SpellAbility sa = spell(g, card);
        Set<String> out = new TreeSet<>();
        List<GameObject> candidates = new ArrayList<>();
        candidates.add(me(g));
        candidates.add(rival(g));
        for (String n : new String[] {"Grizzly Bears", "Hill Giant", "Ornithopter"}) candidates.add(find(g, n));
        for (GameObject o : candidates) if (sa.canTarget(o)) out.add(o instanceof Card ? ((Card) o).getName() : ((forge.game.player.Player) o).getName());
        out.add("?cancel");
        return out;
    }
}
