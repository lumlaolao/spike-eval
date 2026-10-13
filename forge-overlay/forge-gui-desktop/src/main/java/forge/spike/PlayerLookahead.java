package forge.spike;

import java.util.Locale;
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
    /** Stamped on the game line: what a preview covers and reports is part of what the model sees.
     *  v2 (2026-10-13): activated abilities as well as spells; names of permanents that left or
     *  entered; hand and library sizes; a refusal instead of "no change" when the copy could not pay;
     *  and previews that work at all when a sideboard holds a characteristic-defining card. */
    static final String VERSION = "lookahead-preview-v2";

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
            Game copy;
            UncopiedEffects parked = UncopiedEffects.park(live);
            try { copy = new GameCopier(live).makeCopy(); } finally { parked.restore(); }
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
        // Sizes of hidden zones are public; their contents are not and are never named.
        int handBefore = before.getCardsIn(ZoneType.Hand).size(), handAfter = after.getCardsIn(ZoneType.Hand).size();
        if (handAfter != handBefore) b.append("hand ").append(handBefore).append(" -> ").append(handAfter).append("; ");
        int libBefore = before.getCardsIn(ZoneType.Library).size(), libAfter = after.getCardsIn(ZoneType.Library).size();
        if (libAfter != libBefore) b.append("library ").append(libBefore).append(" -> ").append(libAfter).append("; ");
        // WHICH permanents left or arrived: the battlefield is public, and a count alone hides the
        // whole point of a sacrifice or a removal spell.
        String left = namesOnlyIn(before, after), entered = namesOnlyIn(after, before);
        if (!left.isEmpty()) b.append("left the battlefield: ").append(left).append("; ");
        if (!entered.isEmpty()) b.append("entered the battlefield: ").append(entered).append("; ");
        String out = b.toString().trim();
        if (out.endsWith(";")) out = out.substring(0, out.length() - 1).trim();
        return out.isEmpty() ? "no change" : out;
    }

    /** Names (with multiplicity) of battlefield permanents {@code a} controls that {@code b} does not. */
    private static String namesOnlyIn(Player a, Player b) {
        java.util.List<String> other = new java.util.ArrayList<>();
        for (forge.game.card.Card c : b.getCardsIn(ZoneType.Battlefield)) other.add(c.getName());
        java.util.List<String> out = new java.util.ArrayList<>();
        for (forge.game.card.Card c : a.getCardsIn(ZoneType.Battlefield)) {
            if (!other.remove(c.getName())) out.add(c.getName());
        }
        java.util.Collections.sort(out);
        return String.join(", ", out);
    }

    /** Immediate-resolution preview of playing {@code liveOption} (a spell or an activated ability): play it on a copy,
     *  determinize the copy's libraries BEFORE resolution, resolve, and report the public-only delta
     *  for the seat and each opponent. Runs entirely inside one isolated RNG: the live game and its
     *  RNG stream are untouched (same guarantee as {@link #copyForPreview}).
     *
     *  <p>It refuses land drops and mana abilities, and never reports hidden identities —
     *  this is a *rules preview* of the immediate resolution, not a win-probability rollout (see
     *  docs/harness/LOOKAHEAD-DESIGN.md). The opponent is played by the engine AI, and only
     *  libraries are determinized (the opponent's hand is not), so it is partial determinization. */
    static String previewCast(Game live, Player seat, forge.game.spellability.SpellAbility liveOption, long seed) {
        return previewCast(live, seat, liveOption, seed, null);
    }

    /** As {@link #previewCast(Game, Player, SpellAbility, long)} but aimed at {@code targetHint} when
     *  it names a legal candidate (case-insensitive substring), so the model can ask "what if I aim
     *  this at their creature" and not only the default target. */
    static String previewCast(Game live, Player seat, forge.game.spellability.SpellAbility liveOption, long seed, String targetHint) {
        if (live == null || seat == null || liveOption == null) return "(no preview)";
        // v2 previews any CAST or ACTIVATE option. In the first arm that could ask, 19 of 38 requests
        // were for an activated ability (Goblin Bombardment, a planeswalker) and all were turned away.
        if (liveOption.isLandAbility() || liveOption.isManaAbility())
            return "(a preview covers CAST and ACTIVATE options, not land drops or mana abilities)";
        final boolean spell = liveOption.isSpell();
        Random liveRandom = MyRandom.getRandom();
        // The live option carries no target until the model commits, so the engine cannot resolve a
        // targeting spell. Set first-legal targets for the preview and RESTORE them in the finally:
        // this is transient decision scaffolding on an ability not yet on the stack (the real cast's
        // ensureTargetsSet overwrites targets anyway), and the live board/hands/life/RNG are never
        // touched. A test pins that the option's own targets are unchanged after a preview.
        java.util.List<forge.game.GameObject> savedTargets = new java.util.ArrayList<>();
        try { if (liveOption.getTargets() != null) savedTargets.addAll(liveOption.getTargets()); } catch (Exception ignore) { }
        try {
            MyRandom.setRandom(new Random(seed));
            String label;
            try { label = liveOption.getHostCard() == null ? "the spell" : liveOption.getHostCard().getName(); }
            catch (Exception e) { label = "the spell"; }
            String assumed = selectFirstTargets(liveOption, targetHint);
            GameSimulator sim;
            UncopiedEffects parked = UncopiedEffects.park(live);   // the constructor is where the copy is made
            try {
                sim = new GameSimulator(new SimulationController(new GameStateEvaluator.Score(0)), live, seat, null);
            } finally { parked.restore(); }
            // Play on the copy, resolve separately below. The engine pays the costs there; the
            // sentinel score is its way of saying it could not (no legal payment on the copy).
            GameStateEvaluator.Score played = sim.simulateSpellAbility(liveOption, false);
            Game simGame = sim.getSimulatedGameState();
            if (simGame == null) return "(preview unavailable)";
            if (played != null && played.value == Integer.MIN_VALUE)
                return "(preview unavailable: the engine could not pay for or play this option on the copy)";
            // Determinize BEFORE resolution so the preview cannot depend on the real hidden order.
            for (Player p : simGame.getPlayers()) {
                Zone lib = p.getZone(ZoneType.Library);
                if (lib != null && lib.size() >= 2) lib.shuffle();
            }
            GameSimulator.resolveStack(simGame, simGame.getPlayers().get(1));

            Player seatAfter = (Player) sim.getGameCopier().find(seat);
            StringBuilder b = new StringBuilder(spell ? "if you cast " : "if you activate ").append(label)
                    .append(assumed.isEmpty() ? "" : " [assuming target: " + assumed + "]")
                    .append(": you ").append(publicDelta(seat, seatAfter));
            for (Player opp : seat.getOpponents()) {
                Player oppAfter = (Player) sim.getGameCopier().find(opp);
                b.append("; ").append(opp.getName()).append(' ').append(publicDelta(opp, oppAfter));
            }
            // A cost with a choice in it (sacrifice a creature) was paid by the engine on the copy.
            if (liveOption.getPayCosts() != null && liveOption.getPayCosts().hasSpecificCostType(forge.game.cost.CostSacrifice.class))
                b.append(". The engine chose what to sacrifice for this preview; in the real play YOU choose");
            return b.toString();
        } catch (Throwable t) {
            // The model is told only that the preview failed; the log says why. Failing without a
            // reason in the log is how every spell preview of a 16-game arm came back empty unnoticed.
            StackTraceElement[] st = t.getStackTrace();
            StringBuilder where = new StringBuilder();
            for (int i = 0; i < Math.min(6, st.length); i++) where.append(" <- ").append(st[i]);
            System.out.println("[LOOKAHEAD] preview failed: " + t + where);
            return "(preview failed: " + t.getClass().getSimpleName() + ")";
        } finally {
            // Restore the live option's own targets (usually none), THEN the live RNG reference.
            try {
                liveOption.resetTargets();
                for (forge.game.GameObject o : savedTargets) liveOption.getTargets().add(o);
            } catch (Exception ignore) { }
            MyRandom.setRandom(liveRandom);
        }
    }

    /** Set the FIRST legal target on each targeting ability of {@code root}'s chain that has none,
     *  mirroring the live {@code ensureTargetsSet} fallback. Returns a comma-joined description of the
     *  assumed targets (empty when the spell does not target), so the preview can disclose what it
     *  assumed. The caller is responsible for restoring the ability's original targets. */
    private static String selectFirstTargets(forge.game.spellability.SpellAbility root, String hint) {
        StringBuilder assumed = new StringBuilder();
        for (forge.game.spellability.SpellAbility s = root; s != null; s = s.getSubAbility()) {
            try {
                if (!s.usesTargeting()) continue;
                if (s.getTargets() != null && !s.getTargets().isEmpty()) continue;
                forge.game.spellability.TargetRestrictions tr = s.getTargetRestrictions();
                if (tr == null) continue;
                java.util.List<forge.game.GameEntity> cands = tr.getAllCandidates(s, false);
                if (cands == null || cands.isEmpty()) continue;
                int min = Math.max(1, s.getMinTargets());
                s.resetTargets();
                for (int i = 0; i < min && i < cands.size(); i++) {
                    forge.game.GameEntity ge = (i == 0) ? pickTarget(s, cands, hint) : cands.get(i);
                    if (s.getTargets().contains(ge)) continue;
                    s.getTargets().add(ge);
                    if (assumed.length() > 0) assumed.append(", ");
                    assumed.append(ge instanceof forge.game.card.Card ? ((forge.game.card.Card) ge).getName() : String.valueOf(ge));
                }
            } catch (Exception ignore) { }
        }
        return assumed.toString();
    }

    /** A candidate whose name matches {@code hint} (case-insensitive substring), else the default. */
    private static forge.game.GameEntity pickTarget(forge.game.spellability.SpellAbility s,
            java.util.List<forge.game.GameEntity> cands, String hint) {
        if (hint != null && !hint.trim().isEmpty()) {
            String h = hint.trim().toLowerCase(Locale.ROOT);
            for (forge.game.GameEntity ge : cands) {
                String nm = ge instanceof forge.game.card.Card ? ((forge.game.card.Card) ge).getName() : String.valueOf(ge);
                if (nm != null && nm.toLowerCase(Locale.ROOT).contains(h)) return ge;
            }
        }
        return defaultTarget(s, cands);
    }

    /** Prefer an OPPOSING player, then an opposing permanent, then the first legal candidate: a
     *  preview ("if I aim this at them...") is far more useful against them than against yourself,
     *  and the choice is disclosed as the assumed target. */
    private static forge.game.GameEntity defaultTarget(forge.game.spellability.SpellAbility s,
            java.util.List<forge.game.GameEntity> cands) {
        forge.game.player.Player me = s.getActivatingPlayer();
        for (forge.game.GameEntity ge : cands) {
            if (ge instanceof forge.game.player.Player && me != null && me.isOpponentOf((forge.game.player.Player) ge)) return ge;
        }
        for (forge.game.GameEntity ge : cands) {
            if (ge instanceof forge.game.card.Card) {
                forge.game.player.Player ctrl = ((forge.game.card.Card) ge).getController();
                if (ctrl != null && me != null && me.isOpponentOf(ctrl)) return ge;
            }
        }
        return cands.get(0);
    }

    /**
     * Works around an engine defect without touching the engine or the game.
     *
     * {@link GameCopier} copies seven zones and not the Sideboard. A characteristic-defining ability
     * ("Barrowgoyf's power is equal to...") applies in every zone, so the engine keeps a static effect
     * whose source is a SIDEBOARD card, and the copier then throws "Couldn't map Barrowgoyf" for it
     * ({@code GameCopier.makeCopy -> StaticEffect.removeMapped}). Every preview in a game where either
     * player had such a card in the sideboard failed: 16 of 16 spell previews in
     * runs/lines/ctl/native-lookahead, 104 of 104 on loopback with the Mardu deck.
     *
     * While the copy is made, the effects whose source is in a zone the copier does not copy are taken
     * out of the engine's effect table, and put back afterwards in the original order. Nothing on any
     * card is changed and the table is identical before and after; a sideboard card cannot matter to
     * a preview of the next spell. The table is private in {@code forge.game.StaticEffects}, hence the
     * reflection: an engine bump that renames it makes {@link #park} report, not fail silently
     * (LookaheadProbeTest pins the field; docs/harness/ENGINE-PIN.md lists it).
     */
    static final class UncopiedEffects {
        static final java.util.Set<ZoneType> COPIED = java.util.EnumSet.of(ZoneType.Battlefield, ZoneType.Hand,
                ZoneType.Graveyard, ZoneType.Library, ZoneType.Exile, ZoneType.Stack, ZoneType.Command);
        private final java.util.Map<Object, Object> table;      // the live table, or null when nothing was parked
        private final java.util.LinkedHashMap<Object, Object> original;
        final int parked;

        private UncopiedEffects(java.util.Map<Object, Object> table, java.util.LinkedHashMap<Object, Object> original, int parked) {
            this.table = table; this.original = original; this.parked = parked;
        }

        @SuppressWarnings("unchecked")
        static java.util.Map<Object, Object> tableOf(Game game) throws ReflectiveOperationException {
            java.lang.reflect.Field f = forge.game.StaticEffects.class.getDeclaredField("staticEffects");
            f.setAccessible(true);
            return (java.util.Map<Object, Object>) f.get(game.getStaticEffects());
        }

        static UncopiedEffects park(Game live) {
            try {
                java.util.Map<Object, Object> table = tableOf(live);
                java.util.LinkedHashMap<Object, Object> original = new java.util.LinkedHashMap<>(table);
                int n = 0;
                for (java.util.Map.Entry<Object, Object> e : original.entrySet()) {
                    forge.game.card.Card src = ((forge.game.StaticEffect) e.getValue()).getSource();
                    Zone z = src == null ? null : src.getZone();
                    if (z != null && !COPIED.contains(z.getZoneType())) { table.remove(e.getKey()); n++; }
                }
                return n == 0 ? new UncopiedEffects(null, null, 0) : new UncopiedEffects(table, original, n);
            } catch (ReflectiveOperationException | RuntimeException e) {
                System.out.println("[LOOKAHEAD] cannot park uncopied-zone effects (engine changed?): " + e);
                return new UncopiedEffects(null, null, 0);
            }
        }

        /** Put the table back exactly: same entries, same iteration order. */
        void restore() {
            if (table == null) return;
            table.clear();
            table.putAll(original);
        }
    }
}
