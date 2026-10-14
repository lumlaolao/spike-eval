package forge.spike;

import java.util.Set;

import com.google.common.collect.Sets;

import forge.ai.AiCostDecision;
import forge.ai.ComputerUtilCost;
import forge.game.card.CardCollectionView;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardLists;
import forge.game.card.CardPredicates;
import forge.game.cost.CostExile;
import forge.game.cost.CostReturn;
import forge.game.cost.CostSacrifice;
import forge.game.cost.PaymentDecision;
import forge.game.keyword.Keyword;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

/**
 * Cost decision maker for the LLM seat. It routes the CARD-CHOOSING non-mana costs — which permanent
 * to sacrifice for a cost (Cabal Therapy flashback, Feign Death-style, edict-adjacent payments) — to
 * the model-owned controller hook, and delegates every other cost type to {@link AiCostDecision}.
 *
 * <p>The candidate filter below is copied VERBATIM from {@code ComputerUtil.chooseSacrificeType} so
 * legality is identical to the engine's own AI path (sacrifice-eligible, not the pumped target, not a
 * Casualty-granting permanent). The model only ever chooses among those legal candidates; a short or
 * empty pick falls back to the engine's decision. This is the G7 "ownership" fix for
 * {@code getCostDecisionMaker} without re-deriving legality.
 */
final class LlmCostDecision extends AiCostDecision {
    /** Marks a sacrifice the seat is paying as a COST (as opposed to one an effect forces on it), so
     *  the controller can ask the right question. */
    static final String COST_MESSAGE = "Sacrifice as a cost";

    LlmCostDecision(Player ai0, SpellAbility sa, boolean effect) {
        super(ai0, sa, effect);
    }

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
            fellBack("empty pick");
        } catch (Exception e) {
            fellBack(e.getClass().getSimpleName());
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
            CardCollectionView pick = player.getController().chooseCardsForEffect(typeList, ability,
                    "COST: to pay for " + source.getName() + " you must EXILE " + amount + " of these cards from "
                    + from + ". They leave now, as the cost is paid. Choose which", amount, amount, false, null);
            if (pick != null && pick.size() == amount) return PaymentDecision.card(pick);
            fellBack("exile pick of " + (pick == null ? 0 : pick.size()));
        } catch (Exception e) {
            fellBack(e.getClass().getSimpleName());
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

            CardCollectionView pick = player.getController().chooseCardsForEffect(typeList, ability,
                    "COST: to pay for " + source.getName() + " you must RETURN " + amount
                    + " of these permanents to your hand. They leave the battlefield now, as the cost is paid. Choose which",
                    amount, amount, false, null);
            if (pick != null && pick.size() == amount) return PaymentDecision.card(pick);
            fellBack("return pick of " + (pick == null ? 0 : pick.size()));
        } catch (Exception e) {
            fellBack(e.getClass().getSimpleName());
        }
        return super.visit(cost);
    }

    /** The model-owned route failed and the engine's AI is about to choose. Never silent. */
    private void fellBack(String why) {
        try {
            if (player.getController() instanceof LlmSpikeGui.PlayerControllerLLM)
                ((LlmSpikeGui.PlayerControllerLLM) player.getController()).costDecisionFellBack(why);
        } catch (Exception ignore) { }
    }
}
