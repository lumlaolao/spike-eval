package forge.spike;

import java.util.Collections;
import java.util.Set;

import com.google.common.collect.Sets;

import forge.ai.AiCardMemory;
import forge.ai.AiCostDecision;
import forge.ai.ComputerUtilCost;
import forge.ai.ComputerUtilMana;
import forge.game.card.CardCollectionView;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardLists;
import forge.game.card.CardPredicates;
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
    }

    /** What the mana this ability makes is for, when it is activated during another payment. */
    private String manaFor;

    /** This mana ability is being activated to pay for {@code paid}: its tap parts say so, and keep
     *  {@code reserved} out, because the rest of that payment still needs them. */
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
            typeList = ComputerUtilCost.paymentChoicesWithoutTargets(typeList, ability, player);
            if (source.hasKeyword(Keyword.CASUALTY)) {
                typeList = new CardCollection(CardLists.filter(typeList, CardPredicates.hasSVar("AIDontSacToCasualty").negate()));
            }
            if (typeList.size() < amount) return null; // engine handles the impossible case

            CardCollectionView pick = player.getController().choosePermanentsToSacrifice(ability, amount, amount,
                    typeList, COST_MESSAGE);
            if (pick != null && !pick.isEmpty()) return PaymentDecision.card(pick);
            if (amount <= 0) return super.visit(cost); // nothing to choose (X=0): the engine's bookkeeping
            fellBack("sacrifice-cost", "empty pick");
        } catch (Exception e) {
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
        try {
            final int amount = cost.getAbilityAmount(ability);
            CardCollection typeList = new CardCollection(cost.zoneRestriction != 1
                    ? player.getGame().getCardsIn(cost.getFrom()) : player.getCardsIn(cost.getFrom()));
            typeList = CardLists.getValidCards(typeList, type.split(";"), source.getController(), source, ability);
            typeList = CardLists.filter(typeList, CardPredicates.canExiledBy(ability, isEffect()));
            typeList = ComputerUtilCost.paymentChoicesWithoutTargets(typeList, ability, player);
            if (amount <= 0 || typeList.size() <= amount) return super.visit(cost); // nothing to choose

            String from = (cost.zoneRestriction == 1 ? "your " : "any player's ") + (cost.getFrom().size() == 1
                    ? cost.getFrom().get(0).name().toLowerCase() : String.valueOf(cost.getFrom()).toLowerCase());
            CardCollectionView pick = ask(typeList, amount, "exile", "COST: to pay for " + source.getName()
                    + " you must EXILE " + amount + " of these cards from " + from
                    + ". They leave now, as the cost is paid. Choose which");
            if (pick != null) return PaymentDecision.card(pick);
        } catch (Exception e) {
            fellBack("exile-cost", e.getClass().getSimpleName());
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
        try {
            final int amount = cost.getAbilityAmount(ability);
            CardCollection typeList = CardLists.getValidCards(player.getCardsIn(ZoneType.Battlefield),
                    cost.getType().split(";"), source.getController(), source, ability);
            typeList = ComputerUtilCost.paymentChoicesWithoutTargets(typeList, ability, player);
            if (amount <= 0 || typeList.size() <= amount) return super.visit(cost); // nothing to choose

            CardCollectionView pick = ask(typeList, amount, "return", "COST: to pay for " + source.getName()
                    + " you must RETURN " + amount
                    + " of these permanents to your hand. They leave the battlefield now, as the cost is paid. Choose which");
            if (pick != null) return PaymentDecision.card(pick);
        } catch (Exception e) {
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
        try {
            final int amount = cost.getAbilityAmount(ability);
            CardCollection typeList = tapCandidates(cost);
            if (typeList == null || amount <= 0 || typeList.size() <= amount) return engineTap(cost); // nothing to choose

            CardCollectionView pick = ask(typeList, amount, "tap", "COST: to pay for " + source.getName()
                    + (manaFor == null ? "" : " (making mana for " + manaFor + ")") + " you must TAP " + amount
                    + " of these untapped permanents. A tapped creature cannot attack or block until it untaps. Choose which");
            if (pick != null) {
                tapped.addAll(pick);
                return PaymentDecision.card(pick);
            }
        } catch (Exception e) {
            fellBack("tap-cost", e.getClass().getSimpleName());
        }
        return engineTap(cost);
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
     *  ability refreshes it first. Without a mana part nothing is reserved. */
    private Set<Card> manaSources() {
        if (!ability.getPayCosts().hasManaCost()) return Collections.emptySet();
        ComputerUtilMana.canPayManaCost(ability, player, 0, isEffect());
        Set<Card> reserved = AiCardMemory.getMemorySet(player, AiCardMemory.MemorySet.PAYS_TAP_COST);
        return reserved == null ? Collections.emptySet() : reserved;
    }

    /** The engine's own pick, kept in {@code tapped}: the parent's exclusion set is private, so without
     *  this a later tap part could name a card an earlier one already took. */
    private PaymentDecision engineTap(CostTapType cost) {
        PaymentDecision d = super.visit(cost);
        if (d != null && d.cards != null) tapped.addAll(d.cards);
        return d;
    }

    /** Asks the seat for exactly {@code amount} of {@code typeList}; null (logged as a fallback) when
     *  the pick is short, so the caller lets the engine choose. */
    private CardCollectionView ask(CardCollection typeList, int amount, String kind, String question) {
        CardCollectionView pick = player.getController().chooseCardsForEffect(typeList, ability, question,
                amount, amount, false, null);
        if (pick != null && pick.size() == amount) return pick;
        fellBack(kind + "-cost", kind + " pick of " + (pick == null ? 0 : pick.size()));
        return null;
    }

    /** The model-owned route failed and the engine's AI is about to choose. Never silent. */
    private void fellBack(String kind, String why) {
        try {
            if (player.getController() instanceof LlmSpikeGui.PlayerControllerLLM)
                ((LlmSpikeGui.PlayerControllerLLM) player.getController()).costDecisionFellBack(kind, why);
        } catch (Exception ignore) { }
    }
}
