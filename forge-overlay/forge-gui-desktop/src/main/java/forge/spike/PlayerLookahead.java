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
    /** How a preview names the targets it assumed; the greedy floor (GreedyBridge) reads them back. */
    static final String ASSUMING = "[assuming target: ";

    /** Stamped on the game line: what a preview covers and reports is part of what the model sees.
     *  v2 (2026-10-13): activated abilities as well as spells; names of permanents that left or
     *  entered; hand and library sizes; a refusal instead of "no change" when the copy could not pay;
     *  and previews that work at all when a sideboard holds a characteristic-defining card.
     *  v3 (2026-10-13): a previewed line may start with (or include) a land drop, so "play the land,
     *  then the spell it pays for" can be asked; the tool's option numbers are the decision's own
     *  menu numbers (see LlmSpikeGui's simulate handler).
     *  v5 (2026-10-15): a line is no longer capped at three plays (docs/harness/MODEL-LIMITS.md).
     *  v6 (2026-10-16): the opponent's hand is re-dealt from hand and library before the preview, so a
     *  preview no longer reveals hidden cards (review H2).
     *  v7 (2026-10-16): imprinted cards are copied, so a Chrome Mox pays on the copy as in the game.
     *  v8 (2026-10-16): an option cast for an alternative cost (Force of Will's pitch) is previewed with that cost, and
     *  the copy holds the stack, so a response is previewed against the spell it answers.
     *  v9 (2026-10-17): the attack outlook's damage for an attacker no creature could block says "damage". */
    // v10 (2026-10-17): a preview restores the targets of every ability of the chain (Arc Trail's second target
    // stayed aimed, and the real cast never asked for it), and a target choice of a play can be previewed.
    // v11 (2026-10-17): the target preview is offered only while aiming a play from the priority menu; a trigger's
    // target cannot be replayed on the copy (runs/loop/g04 T5 got "unavailable").
    // v12 (2026-10-18): a mana ability is previewed as a step of a line, and the report shows the mana pool.
    // v13 (2026-10-18): a remembered card, or a static effect's affected card, that no copied zone holds no longer breaks
    // the copy (runs/loop/g16 and g23: "Couldn't map Enroot", Carnivorous Cultivator's prepared spell); since g23 showed
    // the simulator's own internal copy failing too, this is an engine patch to GameCopier (ENGINE-PATCHES.md).
    // v14 (2026-10-18): the same, for every copy: GameCopier is patched, and the harness-side parking is gone.
    // v15 (2026-10-19, runs/loop/g28 T6): a counterspell is aimed at the spell on the copy's stack (Force of Negation
    // against Seam Rip was unavailable, then countered nothing), and the delta reports exile counts.
    // v16 (2026-11-06, issue #395): a modal spell's target is previewed in the modes the seat chose, also for a mode
    // with its own chained ability and a cast for an alternative cost; the copy chose the modes again with Forge's
    // AI, and the seat's target on the AI's mode failed to target (Barbed Lightning).
    static final String VERSION = "lookahead-preview-v16";

    private PlayerLookahead() { }

    /** A copy of {@code live} with both libraries re-dealt from a fixed seed, built WITHOUT touching
     *  the live game or its RNG stream. The libraries are determinized so the preview does not
     *  depend on the real (unseen) hidden order — the player-safety point of the whole exercise. */
    /**
     * How deep this thread is inside a preview. While it is non-zero, every player the engine
     * creates is a player of a COPY, and must be driven by the engine's own AI, never by the seat's
     * model. Read by {@code LobbyPlayerLLM.createIngamePlayer}.
     *
     * Found by watching one game (runs/loop/g01, 2026-10-13): each preview made the engine score
     * the copy, the scoring simulates the coming combat on a second copy, and that copy's seat was
     * built with a full model controller. The seat's model was asked to declare attackers and
     * confirm triggers in a game that did not exist; the answers cost money and seconds, and were
     * written to the real game's decision log as if they had happened (7 attacker rows in a game
     * with 5 attack steps).
     */
    private static final ThreadLocal<Integer> DEPTH = ThreadLocal.withInitial(() -> 0);

    static boolean inPreview() { return DEPTH.get() > 0; }
    private static void enter() { DEPTH.set(DEPTH.get() + 1); }
    private static void exit() { DEPTH.set(Math.max(0, DEPTH.get() - 1)); }

    static Game copyForPreview(Game live, long seed) {
        if (live == null) return null;
        Random liveRandom = MyRandom.getRandom();
        enter();
        try {
            // Everything the copy does from here draws from THIS Random, not the live one. The live
            // Random object is never used, so restoring the reference restores its exact state.
            MyRandom.setRandom(new Random(seed));
            Game copy;
            UncopiedEffects parked = UncopiedEffects.park(live);
            StaleEffectCards stale = StaleEffectCards.park(live);
            GameCopier copier = new GameCopier(live);
            try { copy = copier.makeCopy(); } finally { stale.restore(); parked.restore(); }
            copyImprints(live, copier);
            for (Player p : copy.getPlayers()) {
                Zone lib = p.getZone(ZoneType.Library);
                if (lib != null && lib.size() >= 2) lib.shuffle();
            }
            return copy;
        } finally {
            MyRandom.setRandom(liveRandom);
            exit();
        }
    }

    /** Puts the live stack's spells on the copy, bottom first, with their targets (the engine's own copyStack, which its
     *  simulator leaves off). Without it a response was previewed against an empty stack: a counterspell's report
     *  showed the opponent unchanged (LookaheadProbeTest.aForceOfWillPitchIsPreviewed). Abilities on the stack are not
     *  copied (the engine cannot either). True when anything was put on the copy's stack. */
    static boolean copyStack(Game live, Game copy, GameCopier copier) {
        if (live.getStack().isEmpty() || !copy.getStack().isEmpty()) return false;
        java.util.List<forge.game.spellability.SpellAbilityStackInstance> bottomFirst = new java.util.ArrayList<>();
        for (forge.game.spellability.SpellAbilityStackInstance si : live.getStack()) bottomFirst.add(0, si);
        boolean any = false;
        for (forge.game.spellability.SpellAbilityStackInstance si : bottomFirst) {
            try {
                forge.game.spellability.SpellAbility orig = si.getSpellAbility();
                if (orig == null || !orig.isSpell()) continue;
                forge.game.card.Card host = (forge.game.card.Card) copier.find(orig.getHostCard());
                if (host == null) continue;
                forge.game.spellability.SpellAbility match = null;
                for (forge.game.spellability.SpellAbility c : host.getSpellAbilities()) {
                    if (!c.isSpell()) continue;
                    if (match == null || java.util.Objects.equals(c.getDescription(), orig.getDescription())) match = c;
                }
                if (match == null) continue;
                match.setActivatingPlayer((Player) copier.find(orig.getActivatingPlayer()));
                if (orig.usesTargeting() && orig.getTargets() != null)
                    for (forge.game.GameObject t : orig.getTargets()) {
                        Object ct = copier.find(t);
                        if (ct instanceof forge.game.GameObject) match.getTargets().add((forge.game.GameObject) ct);
                    }
                copy.getStack().add(match);
                any = true;
            } catch (RuntimeException ignore) { }
        }
        return any;
    }

    /** The copy's version of a live option cast for an alternative cost (Force of Will's pitch: pay 1 life and exile a
     *  blue card). The simulator matches an ability by its text among the card's own abilities, where the alternative
     *  is not, and fell back to the hard cast, which the copy could not pay (runs/c3/doomsday-game T8, T19). Any other
     *  option is returned as it is, for the simulator to map. Targets set on the live option are carried over. */
    /** The copy's version of mana ability {@code live}: the host's mana ability with the same description. */
    static forge.game.spellability.SpellAbility manaAbilityOnCopy(forge.game.spellability.SpellAbility live, GameSimulator sim) {
        try {
            forge.game.card.Card copyHost = (forge.game.card.Card) sim.getGameCopier().find(live.getHostCard());
            Player copySeat = (Player) sim.getGameCopier().find(live.getActivatingPlayer());
            if (copyHost == null || copySeat == null) return null;
            for (forge.game.spellability.SpellAbility s : copyHost.getManaAbilities()) {
                if (!java.util.Objects.equals(s.getDescription(), live.getDescription())) continue;
                s.setActivatingPlayer(copySeat);
                return s;
            }
        } catch (RuntimeException ignore) { }
        return null;
    }

    static forge.game.spellability.SpellAbility alternativeOnCopy(forge.game.spellability.SpellAbility live, GameSimulator sim, Player seat) {
        try {
            forge.game.card.Card host = live.getHostCard();
            if (host == null || !live.isSpell()) return live;
            boolean plain = host.getSpellAbilities().contains(live);
            if (plain && !targetsTheStack(live)) return live;
            forge.game.card.Card copyHost = (forge.game.card.Card) sim.getGameCopier().find(host);
            Player copySeat = (Player) sim.getGameCopier().find(seat);
            if (copyHost == null || copySeat == null) return live;
            for (forge.game.spellability.SpellAbility base : copyHost.getSpellAbilities()) {
                if (!base.isSpell()) continue;
                base.setActivatingPlayer(copySeat);
                java.util.List<forge.game.spellability.SpellAbility> versions = plain
                        ? java.util.Collections.singletonList(base)
                        : forge.game.GameActionUtil.getAlternativeCosts(base, copySeat, false);
                for (forge.game.spellability.SpellAbility alt : versions) {
                    if (!java.util.Objects.equals(alt.getDescription(), live.getDescription())) continue;
                    alt.setActivatingPlayer(copySeat);
                    forge.game.spellability.SpellAbility a = alt, l = live;
                    for (; a != null && l != null; a = a.getSubAbility(), l = l.getSubAbility()) copyTargets(l, a, sim);
                    carryModes(live, alt, sim);
                    return alt;
                }
            }
        } catch (RuntimeException ignore) { }
        return live;
    }

    /** The modes the seat chose for modal option {@code sa}, as its mode list holds them: each chained mode matched by
     *  its text, the abilities a mode chains after itself (Bushwhack's fight) skipped. Null when {@code sa} is not
     *  modal, already has a chosen list (Forge's own mode choice sets one), has no mode chained, or chains a mode that
     *  is not one of its own. */
    static java.util.List<forge.game.spellability.AbilitySub> chainedModes(forge.game.spellability.SpellAbility sa) {
        if (sa.getApi() != forge.game.ability.ApiType.Charm || sa.getChosenList() != null || sa.getSubAbility() == null)
            return null;
        java.util.List<forge.game.spellability.AbilitySub> modes = new java.util.ArrayList<>();
        for (forge.game.spellability.AbilitySub sub = sa.getSubAbility(); sub != null; sub = sub.getSubAbility()) {
            if (!sub.hasParam("SpellDescription")) continue;
            forge.game.spellability.AbilitySub mode = modeLike(sa.getAdditionalAbilityList("Choices"), sub);
            if (mode == null) return null;
            modes.add(mode);
        }
        return modes.isEmpty() ? null : modes;
    }

    /** Aims {@code a}, an ability on the copy, at the copy's versions of live ability {@code l}'s targets. */
    private static void copyTargets(forge.game.spellability.SpellAbility l, forge.game.spellability.SpellAbility a,
                                    GameSimulator sim) {
        if (l.getTargets() == null) return;
        a.resetTargets();
        for (forge.game.GameObject t : l.getTargets()) {
            forge.game.GameObject ct = targetOnCopy(t, sim);
            if (ct != null) a.getTargets().add(ct);
        }
    }

    /** Gives {@code alt}, the copy's version of a live option the simulator plays as it is, the live option's chosen
     *  modes, each aimed where the live mode and the abilities chained after it are: the engine copies modes and
     *  targets only for an option it maps itself (Wreck and Rebuild's flashback chose its modes again on the copy). */
    private static void carryModes(forge.game.spellability.SpellAbility live, forge.game.spellability.SpellAbility alt,
                                   GameSimulator sim) {
        if (live.getApi() != forge.game.ability.ApiType.Charm || live.getChosenList() == null) return;
        java.util.List<forge.game.spellability.AbilitySub> choices = alt.getAdditionalAbilityList("Choices");
        java.util.List<forge.game.spellability.AbilitySub> modes = new java.util.ArrayList<>();
        for (forge.game.spellability.AbilitySub m : live.getChosenList()) {
            forge.game.spellability.AbilitySub c = modeLike(choices, m);
            if (c == null) return;
            modes.add(c);
        }
        alt.setChosenList(modes);
        for (forge.game.spellability.SpellAbility m = live.getSubAbility(); m != null; m = m.getSubAbility()) {
            forge.game.spellability.AbilitySub c = m.hasParam("SpellDescription") ? modeLike(choices, m) : null;
            if (c == null) continue;
            forge.game.spellability.SpellAbility l = m, a = c;
            do {
                copyTargets(l, a, sim);
                l = l.getSubAbility();
                a = a.getSubAbility();
            } while (l != null && a != null && !l.hasParam("SpellDescription"));
        }
    }

    /** The first of {@code choices} with {@code sa}'s mode text, as the engine matches modes (SpellAbilityChoiceCopier). */
    private static forge.game.spellability.AbilitySub modeLike(java.util.List<forge.game.spellability.AbilitySub> choices,
                                                               forge.game.spellability.SpellAbility sa) {
        for (forge.game.spellability.AbilitySub c : choices)
            if (java.util.Objects.equals(c.getParam("SpellDescription"), sa.getParam("SpellDescription"))) return c;
        return null;
    }

    /** True when an ability of {@code sa}'s chain is aimed at a spell on the stack. */
    static boolean targetsTheStack(forge.game.spellability.SpellAbility sa) {
        for (forge.game.spellability.SpellAbility s = sa; s != null; s = s.getSubAbility())
            if (s.getTargets() != null)
                for (forge.game.GameObject t : s.getTargets())
                    if (t instanceof forge.game.spellability.SpellAbility) return true;
        return false;
    }

    /** The copy's version of target {@code t}. A spell on the stack is the copy's stack entry with the same card: the
     *  copier maps cards and players only, and a counter aimed at the spell's card fizzles (runs/loop/g28 T6, Force of
     *  Negation against Seam Rip: the preview showed Seam Rip entering). */
    static forge.game.GameObject targetOnCopy(forge.game.GameObject t, GameSimulator sim) {
        try {
            if (!(t instanceof forge.game.spellability.SpellAbility)) return sim.getGameCopier().find(t);
            forge.game.card.Card liveHost = ((forge.game.spellability.SpellAbility) t).getHostCard();
            forge.game.card.Card copyHost = liveHost == null ? null : (forge.game.card.Card) sim.getGameCopier().find(liveHost);
            if (copyHost == null) return null;
            for (forge.game.spellability.SpellAbilityStackInstance si : copyHost.getGame().getStack())
                if (si.getSpellAbility() != null && si.getSpellAbility().getHostCard() == copyHost) return si.getSpellAbility();
        } catch (RuntimeException ignore) { }
        return null;
    }

    /** The engine's copier leaves imprinted cards behind, so a Chrome Mox on the copy makes no mana and the preview
     *  of a spell it pays for came back unavailable (runs/c3/ant-storm-default T11, Ponder off a Mox imprinting
     *  Brainstorm). Each live card's imprints are set on its copy. */
    static void copyImprints(Game live, GameCopier copier) {
        for (Player p : live.getPlayers()) {
            for (forge.game.card.Card c : p.getCardsIn(ZoneType.Battlefield)) {
                if (!c.hasImprintedCard()) continue;
                try {
                    forge.game.card.Card copy = (forge.game.card.Card) copier.find(c);
                    if (copy == null) continue;
                    for (forge.game.card.Card i : c.getImprintedCards()) {
                        Object ci = copier.find(i);
                        if (ci instanceof forge.game.card.Card) copy.addImprintedCard((forge.game.card.Card) ci);
                    }
                } catch (RuntimeException ignore) { }
            }
        }
    }

    /**
     * Hide from the copy what the seat cannot see: every library is shuffled, and each opponent's hand is
     * re-dealt, at its real size, from that opponent's hand and library together. Before this only the
     * libraries were shuffled and the opponent's real hand stayed in the copy (2026-10-16 review H2): a
     * Show and Tell preview named the card the opponent would put in, and a discard preview's hand delta
     * said whether they held a legal card. The pool is put in name order before the seeded shuffle, so two
     * states that differ only in hidden cards give the same copy, and so the same preview.
     */
    static void determinize(Game copy, Player copySeat) {
        for (Player p : copy.getPlayers()) {
            Zone lib = p.getZone(ZoneType.Library);
            Zone hand = p.getZone(ZoneType.Hand);
            boolean opponent = copySeat != null && p != copySeat && p.isOpponentOf(copySeat);
            if (opponent && lib != null && hand != null && hand.size() > 0) {
                java.util.List<forge.game.card.Card> pool = new java.util.ArrayList<>();
                for (forge.game.card.Card c : hand.getCards()) pool.add(c);
                for (forge.game.card.Card c : lib.getCards()) pool.add(c);
                pool.sort(java.util.Comparator.comparing(forge.game.card.Card::getName));
                java.util.Collections.shuffle(pool, MyRandom.getRandom());
                int n = hand.size();
                hand.setCards(new java.util.ArrayList<>(pool.subList(0, n)));
                lib.setCards(new java.util.ArrayList<>(pool.subList(n, pool.size())));
            } else if (lib != null && lib.size() >= 2) {
                java.util.List<forge.game.card.Card> order = new java.util.ArrayList<>();
                for (forge.game.card.Card c : lib.getCards()) order.add(c);
                order.sort(java.util.Comparator.comparing(forge.game.card.Card::getName));
                java.util.Collections.shuffle(order, MyRandom.getRandom());
                lib.setCards(order);
            }
        }
    }

    /** Objective, public-only delta between two states of the same seat: life totals and board /
     *  graveyard / exile sizes. Deliberately narrow — it reports counts, never hidden identities, so it is
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
        // Exile is public: a counterspell that exiles, a pitched card, a removal spell that exiles (runs/loop/g28 T6, Force
        // of Negation exiling Seam Rip read "Rival no change").
        int exileBefore = before.getCardsIn(ZoneType.Exile).size(), exileAfter = after.getCardsIn(ZoneType.Exile).size();
        if (exileAfter != exileBefore) b.append("exile ").append(exileBefore).append(" -> ").append(exileAfter).append("; ");
        // Sizes of hidden zones are public; their contents are not and are never named.
        // What a mana ability does is the mana it adds (runs/loop/g10: a line ending in Gaea's Cradle).
        String poolBefore = SeatObservation.manaPool(before), poolAfter = SeatObservation.manaPool(after);
        if (!poolAfter.equals(poolBefore)) b.append("mana pool ").append(poolBefore).append(" -> ").append(poolAfter).append("; ");
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
     *  <p>It never reports hidden identities —
     *  this is a *rules preview* of the immediate resolution, not a win-probability rollout (see
     *  docs/harness/LOOKAHEAD-DESIGN.md). The opponent is played by the engine AI, and only
     *  libraries are shuffled and each opponent's hand is re-dealt from its hand and library
     *  ({@link #determinize}), so nothing the seat cannot see shapes the preview. */
    static String previewCast(Game live, Player seat, forge.game.spellability.SpellAbility liveOption, long seed) {
        return previewCast(live, seat, liveOption, seed, null);
    }

    /** As {@link #previewCast(Game, Player, SpellAbility, long)} but aimed at {@code targetHint} when
     *  it names a legal candidate (case-insensitive substring), so the model can ask "what if I aim
     *  this at their creature" and not only the default target. */
    static String previewCast(Game live, Player seat, forge.game.spellability.SpellAbility liveOption, long seed, String targetHint) {
        return previewLine(live, seat, java.util.Collections.singletonList(liveOption), seed, targetHint);
    }

    /**
     * The preview of a PASS option. PASS resolves nothing, so there is no copy to make and nothing to
     * resolve: the honest answer is that nothing changes. It is spelled out because a PASS preview is
     * the one request that can mean "what happens if I do nothing", which the preview cannot answer --
     * it never plays out the opponent's turn. Refusing it (before this, the last menu line was refused
     * outright; 6 of 92 requests in runs/lines/ctl2/native-lookahead and one in runs/loop/g03 asked
     * for PASS) left the seat an error where the harness does know the answer.
     */
    static String previewPass() {
        return "if you pass, nothing resolves: the board, hands, life and graveyards are unchanged. "
             + "A preview cannot play out the opponent's turn.";
    }


    /**
     * Preview a LINE: play {@code liveOptions} in order on ONE copy, resolving each before the next,
     * and report the public delta of the whole line. This is what a sequencing question needs: "can
     * I cast both, and what is left standing afterwards". Each step must be an option that exists
     * now; a step the copy cannot pay for or play after the earlier ones ends the line and is
     * reported as such, which is itself the answer to "can I do both this turn".
     * {@code targetHint} aims the FIRST step; later steps take their first legal target.
     */
    static String previewLine(Game live, Player seat, java.util.List<forge.game.spellability.SpellAbility> liveOptions,
                              long seed, String targetHint) {
        return previewLine(live, seat, liveOptions, seed, targetHint, null, null);
    }

    /** As {@link #previewLine(Game, Player, java.util.List, long, String)}, with {@code aimed} (an ability of the
     *  first option's chain, during the seat's choice of its target) aimed at {@code at}. Abilities the seat has
     *  already aimed keep their targets; the rest take the default. */
    static String previewLine(Game live, Player seat, java.util.List<forge.game.spellability.SpellAbility> liveOptions,
                              long seed, String targetHint, forge.game.spellability.SpellAbility aimed, forge.game.GameEntity at) {
        if (live == null || seat == null || liveOptions == null || liveOptions.isEmpty() || liveOptions.contains(null))
            return "(no preview)";
        // No length cap: each step is a distinct option of the menu, so a line is bounded by the menu
        // (the 3-play cap was never reached in recorded games, but a storm turn wants more).
        if (new java.util.HashSet<>(liveOptions).size() != liveOptions.size())
            return "(a line names each option once: the same option cannot be played twice)";
        // v2 previews any CAST or ACTIVATE option. In the first arm that could ask, 19 of 38 requests
        // were for an activated ability (Goblin Bombardment, a planeswalker) and all were turned away.
        // v3 also plays a land drop: the seat asked for "land, then the spell it pays for" and was
        // refused (runs/loop/g02 turn 6, `simulate {"option":4,"then":[3]}`; 13 of 92 requests named a
        // PLAY LAND option in runs/lines/ctl2/native-lookahead). v12 plays a mana ability too: it is an
        // ACTIVATE option, and refusing it refused the whole line "cast Heritage Druid twice, then tap Gaea's
        // Cradle" (runs/loop/g10 turn 6). The report shows the mana it adds.
        Random liveRandom = MyRandom.getRandom();
        // A live option carries no target until the model commits, so the engine cannot resolve a
        // targeting spell. Set first-legal targets for the preview and RESTORE them in the finally:
        // this is transient decision scaffolding on an ability not yet on the stack (the real cast's
        // ensureTargetsSet overwrites targets anyway), and the live board/hands/life/RNG are never
        // touched. A test pins that the option's own targets are unchanged after a preview.
        // Every ability of each chain: restoring only the first left Arc Trail's "any other target" aimed, and
        // the real cast skips an ability that already has a target, so the seat was never asked for it.
        java.util.Map<forge.game.spellability.SpellAbility, java.util.List<forge.game.GameObject>> savedTargets = new java.util.IdentityHashMap<>();
        for (forge.game.spellability.SpellAbility o : liveOptions) {
            for (forge.game.spellability.SpellAbility s = o; s != null; s = s.getSubAbility()) {
                java.util.List<forge.game.GameObject> t = new java.util.ArrayList<>();
                try { if (s.getTargets() != null) t.addAll(s.getTargets()); } catch (Exception ignore) { }
                savedTargets.put(s, t);
            }
        }
        java.util.List<forge.game.spellability.SpellAbility> modesListed = new java.util.ArrayList<>();
        enter();
        try {
            // The seat's modes, which its mode choice only chains: else the copy's AI chose its own (issue #395).
            for (forge.game.spellability.SpellAbility o : liveOptions) {
                java.util.List<forge.game.spellability.AbilitySub> modes = chainedModes(o);
                if (modes == null) continue;
                o.setChosenList(modes);
                modesListed.add(o);
            }
            MyRandom.setRandom(new Random(seed));
            GameSimulator sim;
            UncopiedEffects parked = UncopiedEffects.park(live);   // the constructor is where the copy is made
            StaleEffectCards stale = StaleEffectCards.park(live);
            try {
                sim = new GameSimulator(new SimulationController(new GameStateEvaluator.Score(0)), live, seat, null);
            } finally { stale.restore(); parked.restore(); }
            Game simGame = sim.getSimulatedGameState();
            if (simGame == null) return "(preview unavailable)";
            copyImprints(live, sim.getGameCopier());
            boolean stackBelow = copyStack(live, simGame, sim.getGameCopier());
            // Determinize BEFORE anything resolves so the preview cannot depend on what the seat cannot see.
            determinize(simGame, (Player) sim.getGameCopier().find(seat));
            StringBuilder b = new StringBuilder("if you ");
            String stopped = null, delta = "";
            boolean sacrifice = false;
            for (int i = 0; i < liveOptions.size(); i++) {
                forge.game.spellability.SpellAbility o = liveOptions.get(i);
                String label;
                try { label = o.getHostCard() == null ? "the spell" : o.getHostCard().getName(); }
                catch (Exception e) { label = "the spell"; }
                String assumed = selectFirstTargets(o, i == 0 ? targetHint : null, i == 0 ? aimed : null, at);
                boolean isLand = o.isLandAbility();
                boolean ok;
                if (isLand) {
                    // Play the land on the copy directly. GameSimulator.simulateSpellAbility's land
                    // branch recurses with the LIVE ability (sa = origSa, not the copy's) and the AI
                    // picker then replays it: in the Mardu mirror that exhausted the heap (acceptance
                    // nativetool stage, 2 of 19 previews, OutOfMemoryError in Spell.<init>). The copy's
                    // own land ability is played instead; the land reaching the copy's battlefield is
                    // the success test (a second land in the turn fails canPlay and stops the line).
                    Player copyMe = (Player) sim.getGameCopier().find(seat);
                    forge.game.card.Card copyLand = (forge.game.card.Card) sim.getGameCopier().find(o.getHostCard());
                    forge.game.spellability.SpellAbility copyDrop = null;
                    if (copyLand != null && copyMe != null) {
                        for (forge.game.spellability.SpellAbility s : copyLand.getAllPossibleAbilities(copyMe, false)) {
                            if (s.isLandAbility()) { copyDrop = s; break; }
                        }
                    }
                    if (copyDrop != null) copyDrop.setActivatingPlayer(copyMe);
                    ok = copyDrop != null && copyDrop.canPlay();
                    if (ok) copyMe.playLand(copyLand, copyDrop);
                    ok = ok && copyLand.isInZone(ZoneType.Battlefield);
                } else if (o.isManaAbility()) {
                    // A mana ability uses no stack: the copy's own ability is paid for and resolved at once, and the
                    // copy's pool holding more than before is the success test.
                    Player copyMe = (Player) sim.getGameCopier().find(seat);
                    forge.game.spellability.SpellAbility copyMana = manaAbilityOnCopy(o, sim);
                    int before = copyMe == null ? 0 : copyMe.getManaPool().totalMana();
                    // playNoStack asks no static (Null Rod put into play by an earlier step), so the menu's test does.
                    ok = copyMana != null && copyMe != null && LlmSpikeGui.PlayerControllerLLM.playable(copyMana, copyMe)
                            && forge.ai.ComputerUtil.playNoStack(copyMe, copyMana, simGame, false)
                            && copyMe.getManaPool().totalMana() > before;
                } else {
                    // Play on the copy; the engine pays the costs there. The sentinel score is its way
                    // of saying it could not (no legal payment on the copy after earlier steps). Played
                    // means it is on the copy's stack; the engine can report success for an option whose
                    // card an earlier step already moved, with nothing put on the stack, and that is not
                    // a play and must not be reported as one.
                    GameStateEvaluator.Score played = sim.simulateSpellAbility(alternativeOnCopy(o, sim, seat), false);
                    ok = simGame.getStack().size() > 0 && (played == null || played.value != Integer.MIN_VALUE);
                }
                if (!ok) {
                    if (i == 0) return "(preview unavailable: the engine could not pay for or play this option on the copy)";
                    stopped = label;
                    break;
                }
                GameSimulator.resolveStack(simGame, simGame.getPlayers().get(1));
                b.append(i == 0 ? "" : ", then ")
                 .append(isLand ? "play " : (o.isSpell() ? "cast " : "activate ")).append(label)
                 .append(assumed.isEmpty() ? "" : " " + ASSUMING + assumed + "]");
                sacrifice |= o.getPayCosts() != null && o.getPayCosts().hasSpecificCostType(forge.game.cost.CostSacrifice.class);
                // Taken after EACH completed step: a later step that fails to be paid for can leave
                // the copy half-changed (its card already off the hand), and that must not be reported.
                StringBuilder d = new StringBuilder(": you ").append(publicDelta(seat, (Player) sim.getGameCopier().find(seat)));
                for (Player opp : seat.getOpponents())
                    d.append("; ").append(opp.getName()).append(' ').append(publicDelta(opp, (Player) sim.getGameCopier().find(opp)));
                delta = d.toString();
            }
            b.append(delta);
            if (stackBelow) b.append(". What was already on the stack resolved after it, in order, with no other responses");
            if (stopped != null)
                b.append(". After that the engine could NOT pay for or play ").append(stopped)
                 .append(" on the copy, so the line stops there");
            // A cost with a choice in it (sacrifice a creature) was paid by the engine on the copy.
            if (sacrifice) b.append(". The engine chose what to sacrifice for this preview; in the real play YOU choose");
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
            // Restore every ability's own targets (usually none), THEN the live RNG reference.
            for (java.util.Map.Entry<forge.game.spellability.SpellAbility, java.util.List<forge.game.GameObject>> e : savedTargets.entrySet()) {
                try {
                    e.getKey().resetTargets();
                    for (forge.game.GameObject go : e.getValue()) e.getKey().getTargets().add(go);
                } catch (Exception ignore) { }
            }
            for (forge.game.spellability.SpellAbility o : modesListed) o.setChosenList(null);
            MyRandom.setRandom(liveRandom);
            exit();
        }
    }

    /** Set the FIRST legal target on each targeting ability of {@code root}'s chain that has none,
     *  mirroring the live {@code ensureTargetsSet} fallback. Returns a comma-joined description of the
     *  assumed targets (empty when the spell does not target), so the preview can disclose what it
     *  assumed. The caller is responsible for restoring the ability's original targets. */
    private static String selectFirstTargets(forge.game.spellability.SpellAbility root, String hint,
            forge.game.spellability.SpellAbility aimed, forge.game.GameEntity at) {
        StringBuilder assumed = new StringBuilder();
        for (forge.game.spellability.SpellAbility s = root; s != null; s = s.getSubAbility()) {
            try {
                if (!s.usesTargeting()) continue;
                if (s.getTargets() != null && !s.getTargets().isEmpty()) continue;
                forge.game.spellability.TargetRestrictions tr = s.getTargetRestrictions();
                if (tr == null) continue;
                // A counterspell targets the spells on the stack, as the seat's menu offers them; the engine's card
                // candidates came back empty for a permanent spell, so Force of Negation's preview against Seam Rip
                // was unavailable (runs/loop/g28 T6).
                if (tr.getZone() != null && tr.getZone().size() == 1 && tr.getZone().get(0) == ZoneType.Stack) {
                    String spell = aimStackTarget(s, tr, hint);
                    if (spell != null) assumed.append(assumed.length() > 0 ? ", " : "").append(spell);
                    continue;
                }
                java.util.List<forge.game.GameEntity> cands = tr.getAllCandidates(s, false);
                if (cands == null || cands.isEmpty()) continue;
                int min = Math.max(1, s.getMinTargets());
                s.resetTargets();
                for (int i = 0; i < min && i < cands.size(); i++) {
                    forge.game.GameEntity ge = i > 0 ? cands.get(i)
                            : s == aimed && at != null && cands.contains(at) ? at : pickTarget(s, cands, hint);
                    if (s.getTargets().contains(ge)) continue;
                    s.getTargets().add(ge);
                    if (assumed.length() > 0) assumed.append(", ");
                    assumed.append(ge instanceof forge.game.card.Card ? ((forge.game.card.Card) ge).getName() : String.valueOf(ge));
                }
            } catch (Exception ignore) { }
        }
        return assumed.toString();
    }

    /** Aims {@code s} at a spell on the stack, chosen among the engine's stack targets: the one whose card's name
     *  matches {@code hint}, else the top one an opponent controls, else the top one. Returns the card's name, or null
     *  when nothing can be targeted. */
    private static String aimStackTarget(forge.game.spellability.SpellAbility s,
            forge.game.spellability.TargetRestrictions tr, String hint) {
        java.util.List<forge.game.GameObject> cands = LlmSpikeGui.PlayerControllerLLM.targetCandidates(s, tr);
        forge.game.spellability.SpellAbility pick = null;
        String h = hint == null ? "" : hint.trim().toLowerCase(Locale.ROOT);
        int bestLen = 0;
        for (forge.game.GameObject o : cands) {
            forge.game.spellability.SpellAbility sa = (forge.game.spellability.SpellAbility) o;
            String n = sa.getHostCard() == null ? "" : sa.getHostCard().getName().toLowerCase(Locale.ROOT);
            if (!h.isEmpty() && !n.isEmpty() && (n.contains(h) || h.contains(n)) && n.length() > bestLen) {
                pick = sa; bestLen = n.length();
            }
        }
        forge.game.player.Player me = s.getActivatingPlayer();
        for (forge.game.GameObject o : cands) {
            if (pick != null) break;
            forge.game.spellability.SpellAbility sa = (forge.game.spellability.SpellAbility) o;
            if (me != null && sa.getActivatingPlayer() != null && me.isOpponentOf(sa.getActivatingPlayer())) pick = sa;
        }
        if (pick == null && !cands.isEmpty()) pick = (forge.game.spellability.SpellAbility) cands.get(0);
        if (pick == null || pick.getHostCard() == null) return null;
        s.resetTargets();
        s.getTargets().add(pick);
        return pick.getHostCard().getName();
    }

    /** A candidate whose name matches {@code hint}, else the default. The hint is whatever the seat
     *  sent: a bare name, or the label the board printed for the target ("Cephalid Illusionist 1/1
     *  [untapped] [sick] @75"), which CONTAINS the name. Matching only name-contains-hint missed the
     *  label -- it is longer than the name -- so the pick fell silently to the default and the preview
     *  reported a target the seat had not asked about (runs/loop/g05 T5). When several names match,
     *  the longest wins, so a name that is a prefix of another cannot take the pick. */
    private static forge.game.GameEntity pickTarget(forge.game.spellability.SpellAbility s,
            java.util.List<forge.game.GameEntity> cands, String hint) {
        if (hint != null && !hint.trim().isEmpty()) {
            String h = hint.trim().toLowerCase(Locale.ROOT);
            forge.game.GameEntity best = null;
            int bestLen = 0;
            for (forge.game.GameEntity ge : cands) {
                String nm = ge instanceof forge.game.card.Card ? ((forge.game.card.Card) ge).getName() : String.valueOf(ge);
                if (nm == null || nm.isEmpty()) continue;
                String n = nm.toLowerCase(Locale.ROOT);
                if ((n.contains(h) || h.contains(n)) && n.length() > bestLen) { best = ge; bestLen = n.length(); }
            }
            if (best != null) return best;
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
    /**
     * Works around a second engine defect the same way. {@code Player.removeMonarchEffect} takes "The
     * Monarch" out of the command zone but leaves the card's zone pointer, so once the monarchy changes
     * hands the old monarch's effect card looks in play, {@code Player.copyEffectCardsToSnapshot} maps
     * it, and the copier throws "Couldn't map The Monarch": every preview after Palace Jailer's
     * monarchy moved failed (runs/compare one-tier and two-tier, 14 previews). While the copy is made,
     * each player's effect card that is not in the zone it points at has that pointer cleared, and it
     * is put back afterwards, so the live game is exactly as it was.
     */
    static final class StaleEffectCards {
        static final String[] FIELDS = {"keywordEffect", "monarchEffect", "initiativeEffect", "blessingEffect",
                "enduringStoryEffect", "contraptionSprocketEffect", "radiationEffect", "speedEffect"};
        private final java.util.List<forge.game.card.Card> cards = new java.util.ArrayList<>();
        private final java.util.List<Zone> zones = new java.util.ArrayList<>();

        static StaleEffectCards park(Game live) {
            StaleEffectCards s = new StaleEffectCards();
            for (Player p : live.getPlayers()) {
                for (String name : FIELDS) {
                    try {
                        java.lang.reflect.Field f = Player.class.getDeclaredField(name);
                        f.setAccessible(true);
                        forge.game.card.Card c = (forge.game.card.Card) f.get(p);
                        Zone z = c == null ? null : c.getZone();
                        if (z != null && !z.contains(c)) { s.cards.add(c); s.zones.add(z); c.setZone(null); }
                    } catch (ReflectiveOperationException | RuntimeException e) {
                        System.out.println("[LOOKAHEAD] cannot check effect card " + name + " (engine changed?): " + e);
                    }
                }
            }
            return s;
        }

        int size() { return cards.size(); }

        void restore() {
            for (int i = 0; i < cards.size(); i++) cards.get(i).setZone(zones.get(i));
        }
    }

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
