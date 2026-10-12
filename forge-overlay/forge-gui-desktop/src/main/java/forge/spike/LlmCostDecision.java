package forge.spike;

import java.util.Set;

import com.google.common.collect.Sets;

import forge.ai.AiCostDecision;
import forge.ai.ComputerUtilCost;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardLists;
import forge.game.card.CardPredicates;
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

            forge.game.card.CardCollectionView pick = player.getController().choosePermanentsToSacrifice(ability, amount, amount,
                    typeList, "Sacrifice as a cost");
            if (pick != null && !pick.isEmpty()) return PaymentDecision.card(pick);
        } catch (Exception ignore) {
            // fall through to the engine's own decision
        }
        return super.visit(cost);
    }
}
