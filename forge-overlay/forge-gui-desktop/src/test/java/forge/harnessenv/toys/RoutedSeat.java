package forge.harnessenv.toys;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import forge.card.ColorSet;
import forge.card.MagicColor;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardCollectionView;
import forge.game.player.DelayedReveal;
import forge.game.player.Player;
import forge.game.player.PlayerActionConfirmMode;
import forge.game.spellability.SpellAbility;
import forge.game.trigger.WrappedAbility;
import forge.harnessenv.ModelEndpoint;
import forge.util.collect.FCollectionView;

/**
 * A toy that routes priority and ten other decisions (an entity, a sacrifice, tap or untap, yes or no, a number, a
 * colour, a coin-flip call, an optional trigger, the mulligan, play or draw) to the model, everything else to
 * Forge's AI. Two flaws can be switched on, each for one grader check to catch:
 * <ul><li>{@code parseText}: a reply that calls nothing is read for a number (the no-parse check);
 * <li>{@code aiAfterAsking}: for the named hooks the model is asked and then Forge's AI decides anyway, and for
 *     {@code aiHint} hooks Forge's AI is consulted first and its answer thrown away (only the Forge-AI counter can
 *     see that: the model's answer is the one used).</ul>
 * When the model answers nothing usable the toy takes the first option, a fixed legal choice.
 */
class RoutedSeat extends ToySeat {
    private final Set<String> aiAfterAsking, aiHint;

    RoutedSeat(Game g, Player p, ModelEndpoint m, boolean parseText, Set<String> aiAfterAsking, Set<String> aiHint) {
        super(g, p, m, parseText);
        this.aiAfterAsking = aiAfterAsking;
        this.aiHint = aiHint;
    }

    private static String source(SpellAbility sa) {
        if (sa == null || sa.getHostCard() == null) return "";
        Card c = sa.getHostCard();
        return " [" + c.getName() + ": " + c.getOracleText().replace('\n', ' ') + "]";
    }

    private int pick(String question, List<String> labels) {
        int o = ask(question, labels);
        return o < 0 ? 0 : o;
    }

    private int yesNo(String question) { return pick(question, List.of("Yes", "No")); }

    @Override
    public <T extends GameEntity> T chooseSingleEntityForEffect(FCollectionView<T> optionList, DelayedReveal delayedReveal, SpellAbility sa,
            String title, boolean isOptional, Player relatedPlayer, Map<String, Object> params) {
        if (optionList.isEmpty()) return null;
        List<String> labels = new ArrayList<>();
        for (T t : optionList) labels.add(t.getName());
        return optionList.get(pick(title + source(sa), labels));
    }

    @Override
    public CardCollectionView choosePermanentsToSacrifice(SpellAbility sa, int min, int max, CardCollectionView validTargets, String message) {
        List<Card> cards = new ArrayList<>(validTargets);
        List<String> labels = new ArrayList<>();
        for (Card c : cards) labels.add(c.getName());
        int o = pick("Sacrifice which?" + source(sa), labels);
        if (aiAfterAsking.contains("choosePermanentsToSacrifice")) return super.choosePermanentsToSacrifice(sa, min, max, validTargets, message);
        CardCollection out = new CardCollection();
        if (max > 0 && !cards.isEmpty()) out.add(cards.get(o));
        for (Card c : cards) if (out.size() < min && !out.contains(c)) out.add(c);
        return out;
    }

    @Override
    public boolean chooseBinary(SpellAbility sa, String question, BinaryChoiceType kindOfChoice, Boolean defaultChoice) {
        int o = yesNo(question + source(sa));
        if (aiAfterAsking.contains("chooseBinary")) return super.chooseBinary(sa, question, kindOfChoice, defaultChoice);
        return o == 0;
    }

    @Override
    public boolean confirmAction(SpellAbility sa, PlayerActionConfirmMode mode, String message, List<String> options, Card cardToShow,
            Map<String, Object> params) {
        return yesNo(message + source(sa)) == 0;
    }

    @Override
    public int chooseNumber(SpellAbility sa, String title, int min, int max) {
        List<String> labels = new ArrayList<>();
        for (int v = min; v <= max; v++) labels.add(String.valueOf(v));
        return min + pick(title + source(sa), labels);
    }

    @Override
    public byte chooseColor(String message, SpellAbility sa, ColorSet colors) {
        if (aiHint.contains("chooseColor")) super.chooseColor(message, sa, colors);   // a "hint", thrown away
        List<Byte> bytes = new ArrayList<>();
        List<String> labels = new ArrayList<>();
        for (byte c : MagicColor.WUBRG) if (colors.hasAnyColor(c)) { bytes.add(c); labels.add(MagicColor.toShortString(c)); }
        if (bytes.isEmpty()) return MagicColor.COLORLESS;
        return bytes.get(pick(message + source(sa), labels));
    }

    @Override
    public boolean chooseFlipResult(SpellAbility sa, Player flipper, boolean call) {
        return yesNo((call ? "Call heads?" : "Keep the won flip?") + source(sa)) == 0;
    }

    @Override
    public boolean confirmTrigger(WrappedAbility wrapper) {
        if (wrapper.isMandatory()) return true;
        return yesNo("Use this optional trigger?" + source(wrapper)) == 0;
    }

    @Override
    public boolean mulliganKeepHand(Player player, int cardsToReturn) {
        List<String> hand = new ArrayList<>();
        for (Card c : player.getCardsIn(forge.game.zone.ZoneType.Hand)) hand.add(c.getName());
        int o = pick("Your hand: " + String.join(", ", hand) + ". Keep or mulligan?", List.of("Keep", "Mulligan"));
        if (aiAfterAsking.contains("mulliganKeepHand")) return super.mulliganKeepHand(player, cardsToReturn);
        return o == 0;
    }

    @Override
    public Player chooseStartingPlayer(boolean isFirstGame) {
        int o = pick("Play first or draw first?", List.of("Play first", "Draw first"));
        return o == 0 ? getPlayer() : getGame().getNextPlayerAfter(getPlayer());
    }
}
