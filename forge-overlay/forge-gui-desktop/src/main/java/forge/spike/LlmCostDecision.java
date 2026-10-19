package forge.spike;

import java.util.Collections;
import java.util.List;
import java.util.Set;

import com.google.common.collect.Sets;

import forge.ai.AiCardMemory;
import forge.ai.AiCostDecision;
import forge.ai.ComputerUtilMana;
import forge.game.card.CardCollectionView;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardLists;
import forge.game.card.CardPredicates;
import forge.game.cost.CostDiscard;
import forge.game.cost.CostExile;
import forge.game.cost.CostReturn;
import forge.game.cost.CostSacrifice;
import forge.game.cost.CostTapType;
import forge.game.cost.PaymentDecision;
import forge.game.keyword.Keyword;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

/**
 * Cost decision maker for the LLM seat. It routes the CARD-CHOOSING non-mana costs — what a cost
 * sacrifices, exiles, returns to hand or taps (see each visit) — to the model-owned controller hooks,
 * and delegates every other cost type to {@link AiCostDecision}.
 */
final class LlmCostDecision extends AiCostDecision {
    /** Marks a sacrifice the seat is paying as a COST (as opposed to one an effect forces on it), so
     *  the controller can ask the right question. */
    static final String COST_MESSAGE = "Sacrifice as a cost";

    /** What this cost's earlier tap parts already took, so a later one cannot take them again. */
    private final CardCollection tapped = new CardCollection();

    LlmCostDecision(Player ai0, SpellAbility sa, boolean effect) {
        super(ai0, sa, effect);
        // A new payment of this ability: what an earlier one held for its tap part (a payment that never
        // reached its mana) must not hold anything now. Forge reuses an ability's SpellAbility object.
        LlmSpikeGui.PlayerControllerLLM seat = seat();
        if (seat != null) seat.clearTapPartHolds(sa);
    }

    /** The play this cost pays for is not committed yet (the seat's playChosenSpellAbility: CostPayment
     *  decides every part before paying any), so each cost ask may be answered "cancel". */
    private boolean cancellable;
    private boolean cancelled;

    LlmCostDecision offerCancel() { cancellable = true; return this; }

    /** The seat answered a cost ask with "cancel": the payment returns null and nothing is paid. */
    boolean cancelled() { return cancelled; }

    /** What the mana this ability makes is for, when it is activated during another payment. */
    private String manaFor;

    /** This mana ability is being activated to pay for {@code paid}: its tap parts say so, and keep
     *  {@code reserved} out, because the rest of that payment still needs them. A pick with no choice
     *  goes to the engine, which does not see {@code reserved}; it skips the plan's {T} sources only
     *  because the plan's test payment just filled PAYS_TAP_COST, which the parent copies when built. */
    LlmCostDecision forPayment(SpellAbility paid, Iterable<Card> reserved) {
        manaFor = paid.getHostCard() == null ? null : paid.getHostCard().getName();
        tapped.addAll(reserved);
        return this;
    }

    /** Which permanent a sacrifice cost takes. The candidate filter is copied VERBATIM from
     *  {@code ComputerUtil.chooseSacrificeType} so legality is identical to the engine's own AI path
     *  (sacrifice-eligible, not the pumped target, not a Casualty-granting permanent); a short or empty
     *  pick falls back to the engine's decision. */
    @Override
    public PaymentDecision visit(CostSacrifice cost) {
        // Forced / no-choice shapes stay with the engine (pay-from-source, original host, sacrifice-all).
        if (cost.payCostFromSource() || "OriginalHost".equals(cost.getType()) || "All".equals(cost.getAmount())) {
            return super.visit(cost);
        }
        CardCollection candidates = null;
        int need = 0;
        try {
            String type = cost.getType();
            boolean differentNames = false;
            if (type.contains("+WithDifferentNames")) {
                differentNames = true;
                type = type.replace("+WithDifferentNames", "");
            }
            final int amount = cost.getAbilityAmount(ability);

            CardCollection typeList = new CardCollection(CardLists.getValidCards(player.getCardsIn(ZoneType.Battlefield),
                    type.split(";"), source.getController(), source, ability));
            if (differentNames) {
                final Set<Card> unique = Sets.newHashSet();
                for (final Card c : typeList) if (!c.hasNoName()) unique.add(c);
                typeList.clear();
                typeList.addAll(unique);
            }
            typeList = new CardCollection(CardLists.filter(typeList, CardPredicates.canBeSacrificedBy(ability, isEffect())));
            if (typeList.size() < amount) return null; // engine handles the impossible case
            // Inside a payment (Thermopod paying for a spell, rules pilot RP_198), the sources the rest of the payment
            // still needs stay out when enough remain, as for a tap part.
            if (manaFor != null) {
                CardCollection spare = new CardCollection(typeList);
                spare.removeAll(tapped);
                if (spare.size() >= amount) typeList = spare;
            }
            candidates = typeList;
            need = amount;

            offer();
            CardCollectionView pick = player.getController().choosePermanentsToSacrifice(ability, amount, amount,
                    typeList, COST_MESSAGE);
            if (tookCancel()) return null;
            if (pick != null && !pick.isEmpty()) return PaymentDecision.card(pick);
            if (amount <= 0) return super.visit(cost); // nothing to choose (X=0): the engine's bookkeeping
            return firstLegal(candidates, need, "sacrifice", "empty pick");
        } catch (Exception e) {
            if (candidates != null) return firstLegal(candidates, need, "sacrifice", e.getClass().getSimpleName());
            fellBack("sacrifice-cost", e.getClass().getSimpleName());
        }
        return super.visit(cost);
    }

    /** Which card an exile cost takes (Unmask and Force of Will exile a card from hand, others from a
     *  graveyard). Forge's AI exiles the lowest-power legal card, so with Griselbrand and Thoughtseize
     *  in hand it decides what the seat keeps, with no row and no log line (runs/loop/g08 follow-up).
     *  Candidates are {@code ComputerUtil.chooseExileFrom}'s; the shapes with no choice in them
     *  (from the source, all, top of a graveyard, library, a total-mana-value bound, any one zone)
     *  stay the engine's. */
    @Override
    public PaymentDecision visit(CostExile cost) {
        String type = cost.getType();
        if (cost.payCostFromSource() || "All".equals(type) || type.contains("FromTopGrave") || type.contains("+withTotalCMCGE")
                || cost.zoneRestriction == 0
                || (cost.getFrom().size() == 1 && cost.getFrom().get(0) == ZoneType.Library)) {
            return super.visit(cost);
        }
        CardCollection candidates = null;
        int need = 0;
        try {
            final int amount = cost.getAbilityAmount(ability);
            CardCollection typeList = new CardCollection(cost.zoneRestriction != 1
                    ? player.getGame().getCardsIn(cost.getFrom()) : player.getCardsIn(cost.getFrom()));
            typeList = CardLists.getValidCards(typeList, type.split(";"), source.getController(), source, ability);
            typeList = CardLists.filter(typeList, CardPredicates.canExiledBy(ability, isEffect()));
            if (amount <= 0 || typeList.size() < amount) return super.visit(cost); // nothing to choose
            if (typeList.size() == amount) return forced(typeList, "exile");
            candidates = typeList;
            need = amount;

            String from = (cost.zoneRestriction == 1 ? "your " : "any player's ") + zonesInWords(cost.getFrom());
            CardCollectionView pick = ask(typeList, amount, "exile", "COST: to pay for " + source.getName()
                    + " you must EXILE " + amount + " of these cards from " + from
                    + ". They leave now, as the cost is paid. Choose which");
            if (pick != null) return PaymentDecision.card(pick);
            if (cancelled) return null;
        } catch (Exception e) {
            if (candidates != null) return firstLegal(candidates, need, "exile", e.getClass().getSimpleName());
            fellBack("exile-cost", e.getClass().getSimpleName());
        }
        return super.visit(cost);
    }

    /** The zones a cost takes cards from, in words: "graveyard", "graveyard or hand", "graveyard,
     *  hand or library" (issue #109: the Java list "[graveyard, hand]" was printed). */
    static String zonesInWords(List<ZoneType> zones) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < zones.size(); i++) {
            if (i > 0) b.append(i == zones.size() - 1 ? " or " : ", ");
            b.append(zones.get(i).name().toLowerCase());
        }
        return b.toString();
    }

    /** Which cards a discard cost discards (Survival of the Fittest: "Discard a creature card").
     *  Forge's AI picks with getCardsToDiscard. Shapes with no choice in them (the last drawn card, the
     *  source itself, the whole hand, at random, same or different names) stay the engine's. */
    @Override
    public PaymentDecision visit(CostDiscard cost) {
        String type = cost.getType();
        if (cost.payCostFromSource() || "LastDrawn".equals(type) || "Hand".equals(type) || "Random".equals(type)
                || type.contains("WithSameName") || type.contains("+WithDifferentNames")) {
            return super.visit(cost);
        }
        CardCollection candidates = null;
        int need = 0;
        try {
            final int amount = cost.getAbilityAmount(ability);
            CardCollection typeList = CardLists.getValidCards(player.getCardsIn(ZoneType.Hand),
                    type.split(";"), source.getController(), source, ability);
            if (amount <= 0 || typeList.size() < amount) return super.visit(cost); // nothing to choose
            if (typeList.size() == amount) return forced(typeList, "discard");
            candidates = typeList;
            need = amount;
            CardCollectionView pick = ask(typeList, amount, "discard", "COST: to pay for " + source.getName()
                    + " you must DISCARD " + amount + " of these cards from your hand. Choose which");
            if (pick != null) return PaymentDecision.card(pick);
            if (cancelled) return null;
        } catch (Exception e) {
            if (candidates != null) return firstLegal(candidates, need, "discard", e.getClass().getSimpleName());
            fellBack("discard-cost", e.getClass().getSimpleName());
        }
        return super.visit(cost);
    }

    /** Which permanent a return-to-hand cost bounces (Daze returns an Island, Gush two). Forge's AI
     *  takes the lowest-power one, so between a tapped and an untapped Island it decides whether the
     *  seat still has mana this turn (runs/loop/g09, Daze). Candidates are
     *  {@code ComputerUtil.chooseReturnType}'s. */
    @Override
    public PaymentDecision visit(CostReturn cost) {
        if (cost.payCostFromSource()) return super.visit(cost);
        CardCollection candidates = null;
        int need = 0;
        try {
            final int amount = cost.getAbilityAmount(ability);
            CardCollection typeList = CardLists.getValidCards(player.getCardsIn(ZoneType.Battlefield),
                    cost.getType().split(";"), source.getController(), source, ability);
            if (amount <= 0 || typeList.size() < amount) return super.visit(cost); // nothing to choose
            if (typeList.size() == amount) return forced(typeList, "return");
            candidates = typeList;
            need = amount;

            CardCollectionView pick = ask(typeList, amount, "return", "COST: to pay for " + source.getName()
                    + " you must RETURN " + amount
                    + " of these permanents to your hand. They leave the battlefield now, as the cost is paid. Choose which");
            if (pick != null) return PaymentDecision.card(pick);
            if (cancelled) return null;
        } catch (Exception e) {
            if (candidates != null) return firstLegal(candidates, need, "return", e.getClass().getSimpleName());
            fellBack("return-cost", e.getClass().getSimpleName());
        }
        return super.visit(cost);
    }

    /** Which permanents a tap-type cost taps (Glare of Subdual and Opposition tap a creature to tap
     *  something). Forge's AI taps the lowest-power ones, so it decides which creatures stay up to
     *  attack or block. Candidates are {@code ComputerUtil.chooseTapType}'s; crewing (total power) and
     *  creature-type sharing stay the engine's, as does a pick with no choice. A MANA ability's tap
     *  cost (Heritage Druid) reaches this class only through the seat's
     *  {@code PlayerControllerLLM.payManaCost}, which activates it before the engine's payment does.
     */
    @Override
    public PaymentDecision visit(CostTapType cost) {
        String shape = cost.getType();
        if (shape.contains("+withTotalPowerGE") || shape.contains(".sharesCreatureTypeWith")) {
            PaymentDecision d = tapByPowerOrType(cost, shape);
            if (d != null || cancelled) return d;
        }
        CardCollection candidates = null;
        int need = 0;
        try {
            final int amount = cost.getAbilityAmount(ability);
            CardCollection typeList = tapCandidates(cost);
            candidates = typeList;
            need = amount;
            if (typeList == null || amount <= 0 || typeList.size() < amount) return engineTap(cost);
            // Exactly enough: no choice, but the pick is still this list. The engine's own pick excludes a
            // reserve read when it was built, not the one just refreshed, so it could name the mana
            // creature this payment taps (Llanowar Elves feeding a Signet): one tap for two jobs.
            if (typeList.size() == amount) return reserve(typeList);

            CardCollectionView pick = ask(typeList, amount, "tap", "COST: to pay for " + source.getName()
                    + (manaFor == null ? "" : " (making mana for " + manaFor + ")") + " you must TAP " + amount
                    + " of these untapped permanents. A tapped creature cannot attack or block until it untaps. Choose which");
            if (pick != null) return reserve(pick);
            if (cancelled) return null;
        } catch (Exception e) {
            if (candidates != null && candidates.size() >= need && need > 0)
                return reserve(new CardCollection(candidates.subList(0, need)));
            fellBack("tap-cost", e.getClass().getSimpleName());
        }
        return engineTap(cost);
    }

    /** The tap part's pick. It is also held for this ability's own mana payment (issue #111): the
     *  candidates leave out what a TEST payment taps, but the real payment can differ (it prepays a
     *  filter such as a Signet from a mana creature the test never used), and tapping a card the tap
     *  part named makes the tap a no-op. Held, the payment finds another source or fails. */
    private PaymentDecision reserve(CardCollectionView pick) {
        tapped.addAll(pick);
        LlmSpikeGui.PlayerControllerLLM seat = seat();
        if (seat != null && manaFor == null) seat.holdForTapPart(ability, pick);
        return PaymentDecision.card(pick);
    }

    /** The permanents this tap part may take, or null for a shape the engine keeps: crewing (total
     *  power) and creature-type sharing. */
    private CardCollection tapCandidates(CostTapType cost) {
        String type = cost.getType();
        if (type.contains("+withTotalPowerGE") || type.contains("sharesCreatureTypeWith")) return null;
        CardCollection all = new CardCollection(player.getCardsIn(ZoneType.Battlefield));
        all.removeAll(tapped);
        all.removeAll(manaSources());
        CardCollection typeList = CardLists.getValidCards(all, type.split(";"), source.getController(), source, ability);
        typeList = CardLists.filter(typeList, CardPredicates.CAN_TAP);
        if (!cost.canTapSource) typeList.remove(source);
        if (ability.isKeyword(Keyword.STATION)) typeList = CardLists.filter(typeList, c -> c.getNetPower() > 0);
        return typeList;
    }

    /** Whether paying {@code cost} leaves a choice of which permanents to tap. */
    boolean hasTapChoice(CostTapType cost) {
        CardCollection typeList = tapCandidates(cost);
        return typeList != null && typeList.size() > cost.getAbilityAmount(ability);
    }

    /** The permanents this ability's own mana payment will tap. The mana part is paid before the tap
     *  part, and tapping a tapped card is a silent no-op, so offering one would let the cost go unpaid.
     *  AiCostDecision reads PAYS_TAP_COST as it stands, but the seat test-pays many other abilities
     *  while building its menu, so that set belongs to whichever was last; a test payment of THIS
     *  ability refreshes it first. It test-pays even when the printed cost has no mana part: a raise
     *  (Suppression Field: {2} more) gives it one, and the test payment computes the cost with raises
     *  (issue #110); with no mana to pay, the refreshed set is empty. */
    private Set<Card> manaSources() {
        ComputerUtilMana.canPayManaCost(ability, player, 0, isEffect());
        Set<Card> reserved = AiCardMemory.getMemorySet(player, AiCardMemory.MemorySet.PAYS_TAP_COST);
        return reserved == null ? Collections.emptySet() : reserved;
    }

    /** Crew and other "tap creatures with total power N or more", and "tap two creatures that share a creature type"
     *  (Weight of Conscience): which creatures, the seat's. Left to the engine, Forge's AI chose, and for crew it would
     *  not tap a creature it valued above the vehicle. A pick short of the power, or a pair sharing no type, takes a
     *  fixed legal set, recorded: the largest powers first, or the first pair that shares a type. Null when the cost
     *  cannot be paid this way (the engine's answer then stands). */
    private PaymentDecision tapByPowerOrType(CostTapType cost, String shape) {
        boolean power = shape.contains("+withTotalPowerGE");
        String type = shape;
        int need = 0;
        if (power) {
            String n = shape.split("withTotalPowerGE")[1];
            need = Integer.parseInt(n);
            type = shape.replace("+withTotalPowerGE" + n, "");
        } else type = shape.replace(".sharesCreatureTypeWith", "");
        CardCollection all = new CardCollection(player.getCardsIn(ZoneType.Battlefield));
        all.removeAll(tapped);
        CardCollection list = CardLists.getValidCards(all, type.split(";"), source.getController(), source, ability);
        if (!cost.canTapSource) list.remove(source);
        list = CardLists.filter(list, ability.isCrew() ? CardPredicates.CAN_CREW : CardPredicates.CAN_TAP);
        if (power) {
            if (CardLists.getTotalPower(list, ability) < need) return null;
            offer();
            CardCollectionView pick = player.getController().chooseCardsForEffect(list, ability, "COST: to pay for "
                    + source.getName() + " you must TAP untapped creatures with total power " + need + " or more."
                    + " A tapped creature cannot attack or block until it untaps. Choose which", 1, list.size(), false, null);
            if (tookCancel()) return null;
            if (pick != null && CardLists.getTotalPower(new CardCollection(pick), ability) >= need) return reserve(pick);
            CardCollection byPower = new CardCollection(list);
            byPower.sort((a, b) -> Integer.compare(b.getNetPower(), a.getNetPower()));
            CardCollection out = new CardCollection();
            for (Card c : byPower) { if (CardLists.getTotalPower(out, ability) >= need) break; out.add(c); }
            LlmSpikeGui.PlayerControllerLLM seat = seat();
            if (seat != null) seat.costDecisionDefault("tap-cost(power short)->largest " + out.size());
            return reserve(out);
        }
        int amount = cost.getAbilityAmount(ability);
        final CardCollection able = list;
        CardCollection sharing = CardLists.filter(able, c -> CardLists.count(able, CardPredicates.sharesCreatureTypeWith(c)) > 1);
        if (sharing.size() < amount) return null;
        CardCollectionView pick = ask(sharing, amount, "tap", "COST: to pay for " + source.getName() + " you must TAP "
                + amount + " untapped creatures that share a creature type. Choose which");
        if (pick == null) return null;
        boolean ok = true;
        for (Card c : pick) for (Card o : pick) if (c != o && !CardPredicates.sharesCreatureTypeWith(o).test(c)) ok = false;
        if (ok) return reserve(pick);
        for (Card a : sharing) {
            CardCollection pair = new CardCollection(CardLists.filter(sharing, CardPredicates.sharesCreatureTypeWith(a)));
            if (pair.size() >= amount) {
                LlmSpikeGui.PlayerControllerLLM seat = seat();
                if (seat != null) seat.costDecisionDefault("tap-cost(no shared type)->first sharing set");
                return reserve(new CardCollection(pair.subList(0, amount)));
            }
        }
        return null;
    }

    /** The engine's own pick, kept in {@code tapped}: the parent's exclusion set is private, so without
     *  this a later tap part could name a card an earlier one already took. */
    private PaymentDecision engineTap(CostTapType cost) {
        PaymentDecision d = super.visit(cost);
        if (d != null && d.cards != null) tapped.addAll(d.cards);
        return d;
    }

    /** Asks the seat for exactly {@code amount} of {@code typeList}. A short pick takes the first legal
     *  cards, recorded as a fixed fallback; null only when the seat cancelled. */
    private CardCollectionView ask(CardCollection typeList, int amount, String kind, String question) {
        offer();
        CardCollectionView pick = player.getController().chooseCardsForEffect(typeList, ability, question,
                amount, amount, false, null);
        if (tookCancel()) return null;
        if (pick != null && pick.size() == amount) return pick;
        return firstLegal(typeList, amount, kind, kind + " pick of " + (pick == null ? 0 : pick.size())).cards;
    }

    /** Exactly as many candidates as the cost takes: they are the payment. Left to Forge's AI, the pick could be
     *  refused (it declines to exile a permanent it values: Braided Net's craft with Altar of the Wretched the
     *  only other artifact failed to pay, and was chosen again 999 times in the keyword gauntlet). */
    private PaymentDecision forced(CardCollection candidates, String kind) {
        LlmSpikeGui.PlayerControllerLLM seat = seat();
        if (seat != null) seat.costDecisionDefault(kind + "-cost(forced-only-legal)->" + candidates.size());
        return PaymentDecision.card(candidates);
    }

    /** The first {@code amount} legal candidates, recorded as a fixed fallback: never Forge's AI. */
    private PaymentDecision firstLegal(CardCollection candidates, int amount, String kind, String why) {
        CardCollection pick = new CardCollection(candidates.subList(0, Math.min(amount, candidates.size())));
        LlmSpikeGui.PlayerControllerLLM seat = seat();
        if (seat != null) seat.costDecisionDefault(kind + "-cost(" + why + ")->first " + pick.size());
        return PaymentDecision.card(pick);
    }

    /** Offers "cancel" on the ask about to be made, when this payment may still be cancelled. A mana
     *  ability's tap part ({@link #forPayment}) is paid while the parent payment is under way, so it
     *  never offers it. */
    private void offer() {
        LlmSpikeGui.PlayerControllerLLM seat = seat();
        if (seat != null) { seat.offerCostCancel = cancellable && manaFor == null && !isEffect(); seat.costCancelled = false; }
    }

    /** Whether the ask just made was answered "cancel"; it stays recorded on this decision. */
    private boolean tookCancel() {
        LlmSpikeGui.PlayerControllerLLM seat = seat();
        if (seat != null) seat.offerCostCancel = false;
        if (seat != null && seat.costCancelled) cancelled = true;
        return cancelled;
    }

    private LlmSpikeGui.PlayerControllerLLM seat() {
        return player.getController() instanceof LlmSpikeGui.PlayerControllerLLM
                ? (LlmSpikeGui.PlayerControllerLLM) player.getController() : null;
    }

    /** The model-owned route failed and the engine's AI is about to choose. Never silent. */
    private void fellBack(String kind, String why) {
        try {
            if (player.getController() instanceof LlmSpikeGui.PlayerControllerLLM)
                ((LlmSpikeGui.PlayerControllerLLM) player.getController()).costDecisionFellBack(kind, why);
        } catch (Exception ignore) { }
    }
}
