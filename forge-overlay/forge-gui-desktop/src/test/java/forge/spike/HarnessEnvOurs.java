package forge.spike;

import java.util.List;

import forge.LobbyPlayer;
import forge.ai.PlayerControllerAi;
import forge.card.mana.ManaCost;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardCollectionView;
import forge.game.combat.Combat;
import forge.game.cost.Cost;
import forge.game.cost.CostDecisionMakerBase;
import forge.game.cost.CostPart;
import forge.game.cost.CostPartMana;
import forge.game.mana.Mana;
import forge.game.mana.ManaConversionMatrix;
import forge.game.player.Player;
import forge.game.player.PlayerController;
import forge.game.spellability.OptionalCostValue;
import forge.game.spellability.SpellAbility;
import forge.harnessenv.ModelEndpoint;
import forge.harnessenv.SeatFactory;
import forge.util.collect.FCollectionView;

/**
 * Our seat behind harness-env-abi/1, the way a submission of it would name it in harness-env.properties: the real
 * PlayerControllerLLM on the answer tool, its real OpenRouter bridge pointed at the grader's loopback endpoint (no key
 * is sent to loopback, no egress gate applies), faithful profile. With the two ablations the grader is validated on:
 * ours with every combat hook, or every cost hook, handed to a Forge-AI controller of the same player.
 */
public final class HarnessEnvOurs {
    private HarnessEnvOurs() { }

    static LLMBridge bridge(ModelEndpoint m) {
        return new LlmSpikeGui.OpenRouterBridge("Seat/smart", m.model, "", m.baseUrl, 60_000);
    }

    private static void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    public static final class Ours implements SeatFactory {
        @Override public PlayerController create(Game g, Player p, LobbyPlayer lp, ModelEndpoint m) {
            faithful();
            LLMBridge b = bridge(m);
            return new LlmSpikeGui.PlayerControllerLLM(g, p, lp, "Seat", "", b, b, "llm");
        }
    }

    public static final class MinusCombat implements SeatFactory {
        @Override public PlayerController create(Game g, Player p, LobbyPlayer lp, ModelEndpoint m) {
            faithful();
            return new OursMinusCombat(g, p, lp, bridge(m));
        }
    }

    public static final class MinusCosts implements SeatFactory {
        @Override public PlayerController create(Game g, Player p, LobbyPlayer lp, ModelEndpoint m) {
            faithful();
            return new OursMinusCosts(g, p, lp, bridge(m));
        }
    }

    static final class OursMinusCombat extends LlmSpikeGui.PlayerControllerLLM {
        private PlayerControllerAi ai;
        OursMinusCombat(Game g, Player p, LobbyPlayer lp, LLMBridge b) { super(g, p, lp, "Seat", "", b, b, "llm"); }
        private PlayerControllerAi ai() { if (ai == null) ai = new PlayerControllerAi(getGame(), getPlayer(), getLobbyPlayer()); return ai; }
        @Override public void declareAttackers(Player attacker, Combat combat) { ai().declareAttackers(attacker, combat); }
        @Override public void declareBlockers(Player defender, Combat combat) { ai().declareBlockers(defender, combat); }
        @Override public java.util.Map<Card, Integer> assignCombatDamage(Card attacker, CardCollectionView blockers, CardCollectionView remaining,
                int damageDealt, GameEntity defender, boolean overrideOrder) {
            return ai().assignCombatDamage(attacker, blockers, remaining, damageDealt, defender, overrideOrder);
        }
        @Override public CardCollection orderBlockers(Card attacker, CardCollection blockers) { return ai().orderBlockers(attacker, blockers); }
        @Override public CardCollection orderAttackers(Card blocker, CardCollection attackers) { return ai().orderAttackers(blocker, attackers); }
        @Override public boolean payCombatCost(Card card, Cost cost, SpellAbility sa, String prompt) { return ai().payCombatCost(card, cost, sa, prompt); }
    }

    static final class OursMinusCosts extends LlmSpikeGui.PlayerControllerLLM {
        private PlayerControllerAi ai;
        OursMinusCosts(Game g, Player p, LobbyPlayer lp, LLMBridge b) { super(g, p, lp, "Seat", "", b, b, "llm"); }
        private PlayerControllerAi ai() { if (ai == null) ai = new PlayerControllerAi(getGame(), getPlayer(), getLobbyPlayer()); return ai; }
        @Override public CostDecisionMakerBase getCostDecisionMaker(Player player, SpellAbility ability, boolean effect, String prompt) {
            return ai().getCostDecisionMaker(player, ability, effect, prompt);
        }
        @Override public List<OptionalCostValue> chooseOptionalCosts(SpellAbility chosen, List<OptionalCostValue> values) { return ai().chooseOptionalCosts(chosen, values); }
        @Override public boolean payCostToPreventEffect(Cost cost, SpellAbility sa, boolean alreadyPaid, FCollectionView<Player> allPayers) {
            return ai().payCostToPreventEffect(cost, sa, alreadyPaid, allPayers);
        }
        @Override public boolean confirmPayment(CostPart costPart, String question, SpellAbility sa) { return ai().confirmPayment(costPart, question, sa); }
        @Override public CardCollectionView chooseCardsToDelve(int genericAmount, CardCollection grave) { return ai().chooseCardsToDelve(genericAmount, grave); }
        @Override public boolean payManaCost(ManaCost toPay, CostPartMana costPartMana, SpellAbility sa, String prompt, ManaConversionMatrix matrix, boolean effect) {
            return ai().payManaCost(toPay, costPartMana, sa, prompt, matrix, effect);
        }
        @Override public Mana chooseManaFromPool(List<Mana> manaChoices) { return ai().chooseManaFromPool(manaChoices); }
    }
}
