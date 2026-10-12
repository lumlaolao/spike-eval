package forge.spike;

import java.util.Random;

import forge.ai.simulation.GameCopier;
import forge.ai.simulation.GameSimulator;
import forge.ai.simulation.GameStateEvaluator;
import forge.ai.simulation.SimulationController;
import forge.game.Game;
import forge.game.player.Player;
import forge.game.zone.Zone;
import forge.game.zone.ZoneType;
import forge.util.MyRandom;

/**
 * The G8 lookahead primitive: a SAFE, determinized copy of the live game on which a preview can be
 * run without touching the real game (see {@code docs/harness/LOOKAHEAD-DESIGN.md}).
 *
 * <p>Two guarantees this class exists to provide, both test-pinned:
 * <ol>
 *   <li><b>No live-state aliasing.</b> {@link GameCopier} yields a distinct object graph, so a
 *       simulation on the copy cannot mutate the live board, hands or life totals.</li>
 *   <li><b>No live-RNG consumption.</b> Forge draws from a process-wide {@link MyRandom}. Shuffling
 *       the copy's hidden libraries would advance that shared stream and so change the LIVE game's
 *       future draws. This class installs a fresh {@link Random} for the copy's shuffles and
 *       restores the live reference in a {@code finally}, leaving the live stream's state
 *       bit-identical.</li>
 * </ol>
 *
 * <p>Scope note (v1): this is the primitive only — copy + determinize + a public-delta reporter.
 * Applying a specific option and reporting its resolution is the next step and belongs to the tool
 * wiring, not here. It is NOT yet wired to the seat; nothing calls it during a real game.
 */
final class PlayerLookahead {
    static final String VERSION = "lookahead-preview-v1";

    private PlayerLookahead() { }

    /** A copy of {@code live} with both libraries re-dealt from a fixed seed, built WITHOUT touching
     *  the live game or its RNG stream. The libraries are determinized so the preview does not
     *  depend on the real (unseen) hidden order — the player-safety point of the whole exercise. */
    static Game copyForPreview(Game live, long seed) {
        if (live == null) return null;
        Random liveRandom = MyRandom.getRandom();
        try {
            // Everything the copy does from here draws from THIS Random, not the live one. The live
            // Random object is never used, so restoring the reference restores its exact state.
            MyRandom.setRandom(new Random(seed));
            Game copy = new GameCopier(live).makeCopy();
            for (Player p : copy.getPlayers()) {
                Zone lib = p.getZone(ZoneType.Library);
                if (lib != null && lib.size() >= 2) lib.shuffle();
            }
            return copy;
        } finally {
            MyRandom.setRandom(liveRandom);
        }
    }

    /** Objective, public-only delta between two states of the same seat: life totals and board /
     *  graveyard sizes. Deliberately narrow — it reports counts, never hidden identities, so it is
     *  safe to show the model. Returns "no change" when nothing moved. */
    static String publicDelta(Player before, Player after) {
        if (before == null || after == null) return "(state unavailable)";
        StringBuilder b = new StringBuilder();
        int lifeBefore = before.getLife(), lifeAfter = after.getLife();
        if (lifeAfter != lifeBefore) b.append("life ").append(lifeBefore).append(" -> ").append(lifeAfter).append("; ");
        int boardBefore = before.getCardsIn(ZoneType.Battlefield).size();
        int boardAfter = after.getCardsIn(ZoneType.Battlefield).size();
        if (boardAfter != boardBefore) b.append("board ").append(boardBefore).append(" -> ").append(boardAfter).append("; ");
        int yardBefore = before.getCardsIn(ZoneType.Graveyard).size();
        int yardAfter = after.getCardsIn(ZoneType.Graveyard).size();
        if (yardAfter != yardBefore) b.append("graveyard ").append(yardBefore).append(" -> ").append(yardAfter).append("; ");
        String out = b.toString().trim();
        if (out.endsWith(";")) out = out.substring(0, out.length() - 1).trim();
        return out.isEmpty() ? "no change" : out;
    }

    /** Immediate-resolution preview of playing {@code liveOption} (a spell): play it on a copy,
     *  determinize the copy's libraries BEFORE resolution, resolve, and report the public-only delta
     *  for the seat and each opponent. Runs entirely inside one isolated RNG: the live game and its
     *  RNG stream are untouched (same guarantee as {@link #copyForPreview}).
     *
     *  <p>v1 refuses anything that is not a spell ability, and never reports hidden identities —
     *  this is a *rules preview* of the immediate resolution, not a win-probability rollout (see
     *  docs/harness/LOOKAHEAD-DESIGN.md). The opponent is played by the engine AI, and only
     *  libraries are determinized (the opponent's hand is not), so it is partial determinization. */
    static String previewCast(Game live, Player seat, forge.game.spellability.SpellAbility liveOption, long seed) {
        if (live == null || seat == null || liveOption == null) return "(no preview)";
        if (!liveOption.isSpell()) return "(lookahead v1 supports spell options only)";
        Random liveRandom = MyRandom.getRandom();
        try {
            MyRandom.setRandom(new Random(seed));
            String label;
            try { label = liveOption.getHostCard() == null ? "the spell" : liveOption.getHostCard().getName(); }
            catch (Exception e) { label = "the spell"; }
            GameSimulator sim = new GameSimulator(
                    new SimulationController(new GameStateEvaluator.Score(0)), live, seat, null);
            sim.simulateSpellAbility(liveOption, false); // play on the copy, resolve separately below
            Game simGame = sim.getSimulatedGameState();
            if (simGame == null) return "(preview unavailable)";
            // Determinize BEFORE resolution so the preview cannot depend on the real hidden order.
            for (Player p : simGame.getPlayers()) {
                Zone lib = p.getZone(ZoneType.Library);
                if (lib != null && lib.size() >= 2) lib.shuffle();
            }
            GameSimulator.resolveStack(simGame, simGame.getPlayers().get(1));

            Player seatAfter = (Player) sim.getGameCopier().find(seat);
            StringBuilder b = new StringBuilder("if you cast ").append(label).append(": you ")
                    .append(publicDelta(seat, seatAfter));
            for (Player opp : seat.getOpponents()) {
                Player oppAfter = (Player) sim.getGameCopier().find(opp);
                b.append("; ").append(opp.getName()).append(' ').append(publicDelta(opp, oppAfter));
            }
            return b.toString();
        } catch (Throwable t) {
            return "(preview failed: " + t.getClass().getSimpleName() + ")";
        } finally {
            MyRandom.setRandom(liveRandom);
        }
    }
}
