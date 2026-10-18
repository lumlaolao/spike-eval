package forge.spike;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.testng.Assert;

/**
 * docs/STANDARDS.md section 8, the ask contract: what every prompt the seat asks under the faithful
 * profile must satisfy, whatever its decision type. PromptFidelityTest and GoldenChecksTest run it
 * after every test, on every prompt the live controller asked (PlayerControllerLLM.TEST_ASK_TAP).
 */
final class AskContract {
    private AskContract() { }

    static final Pattern CUT_OPTION = Pattern.compile("(?m)^\\s+\\d+\\) [^\\n]*\\.\\.\\.\\]?$");
    static final Pattern BATTLEFIELD_OPTION = Pattern.compile("(?m)^\\s+\\d+\\) [^\\n]*\\[Battlefield[^\\n]*$");
    static final Pattern OPTION = Pattern.compile("(?m)^  (\\d+)\\) ([^\\n]*)$");
    /** A word cut short with "..." (truncateWords), not a "..." placeholder standing alone. */
    static final Pattern CUT_TEXT = Pattern.compile("[^\\s.]\\.\\.\\.(?!\\.)");
    /** The engine formats a card as "Name (id)"; the faithful profile names objects by handle only. */
    static final Pattern ENGINE_ID = Pattern.compile("[A-Za-z'] \\(\\d+\\)");
    /** In the state a count follows a lower-case word ("library (50)"); an engine id follows a card name, whose
     *  last word is capitalised ("Aether Vial (4)", "Show and Tell (104)"). */
    static final Pattern STATE_ENGINE_ID = Pattern.compile("\\b[A-Z][A-Za-z']* \\(\\d+\\)");
    /** Forge's filter syntax ("Card.nonLand", "Creature.cmcEQX", "Planeswalker.Nissa"): runs/loop/g05 showed it raw. */
    static final Pattern ENGINE_FILTER = Pattern.compile("\\b(?:Card|Creature|Permanent|Land|Artifact|Enchantment|Planeswalker|Spell)\\.[A-Za-z!][A-Za-z]");
    static final Pattern RANGE = Pattern.compile("Choose between (\\d+) and (\\d+) of these");
    static final Pattern NUMBER_RANGE = Pattern.compile("Every integer from (-?\\d+) through (-?\\d+) inclusive is legal");
    static final Pattern HAND_TAG = Pattern.compile("@(\\d+) \\((castable[^)]*|payable, not castable now|need [^)]*|not castable now)\\)");
    /** The labels Forge's triggers use in the note they append to their text for debugging, "[Zone Changer: ...]",
     *  "[Card: ..., Activator: ..., SpellAbility: ...]" (Trigger.getImportantStackObjects): every
     *  getMessage("lbl...").append(": ") under forge-game/.../game/trigger at the pinned engine. */
    static final String[] TRIGGER_NOTE_KEYS = {
            "lblActivator", "lblAdapt", "lblAddedOnce", "lblAmount", "lblAttachee", "lblAttachment",
            "lblAttacker", "lblAttackers", "lblAttackingPlayer", "lblBlocker", "lblBlockers", "lblCard",
            "lblCause", "lblChampioned", "lblChangedController", "lblCountered", "lblCrew", "lblCycled",
            "lblDamageSource", "lblDamageTarget", "lblDamaged", "lblDefenders", "lblDestroyed", "lblDestroyer",
            "lblDevoured", "lblDiscarded", "lblEnlisted", "lblEvolved", "lblExerted", "lblExploited",
            "lblExploiter", "lblExplored", "lblExplorer", "lblFighter", "lblGainedAmount", "lblLandPlayed",
            "lblLostAmount", "lblMana", "lblMentor", "lblMentored", "lblMonstrous", "lblNumberAttackers",
            "lblNumberBlockers", "lblObject", "lblOppVotedDiff", "lblOppVotedSame", "lblPaidAmount", "lblPhase",
            "lblPhasedIn", "lblPhasedOut", "lblPlaneswalkedFrom", "lblPlaneswalkedTo", "lblPlayer",
            "lblPlotted", "lblRemovedFrom", "lblRenowned", "lblRoller", "lblSaddled", "lblSaddledBy",
            "lblScryer", "lblSearcher", "lblShuffler", "lblSource", "lblSpecialized", "lblSpellAbility",
            "lblTapped", "lblTarget", "lblTargets", "lblTrains", "lblTransformed", "lblTurnFaceUp",
            "lblUntapped", "lblVehicle", "lblZoneChanger"
    };
    private static Pattern triggerNote;

    /** "[" + one of Forge's trigger-note labels + ": ", as the engine words it. */
    static Pattern triggerNote() {
        if (triggerNote == null) {
            List<String> words = new ArrayList<>();
            for (String k : TRIGGER_NOTE_KEYS) words.add(Pattern.quote(forge.util.Localizer.getInstance().getMessage(k)));
            triggerNote = Pattern.compile("\\[(?:" + String.join("|", words) + "): ");
        }
        return triggerNote;
    }

    /** Words of one eval deck's line (noAskCoachesOneDecksLine); no question may carry them. */
    static final String[] COACHING = {"Griselbrand", "reanimat", "Reanimat", "Petal", "Grixis", "Entomb", "Force of Will"};

    static void check(String test, List<String> asked) {
        for (String p : new ArrayList<>(asked)) checkOne(test, p);
    }

    /** Start of the menu or of the answer instruction: what is before it (from DECISION:) is the question. */
    private static int questionEnd(String p, int d) {
        int end = p.length();
        for (String mark : new String[] {"\nOptions:", "\nChoose between ", "\n  1) ", "\nReason briefly", "\nEvery integer from", "End with EXACTLY"}) {
            int i = p.indexOf(mark, d);
            if (i >= 0 && i < end) end = i;
        }
        return end;
    }

    static void checkOne(String test, String p) {
        int d = p.lastIndexOf("DECISION:");
        Assert.assertTrue(d >= 0, test + ": an ask with no DECISION: " + p);
        Assert.assertTrue(p.contains("== Turn ") && p.contains("| priority: "), test + ": no observation: " + p);
        Assert.assertFalse(CUT_OPTION.matcher(p).find(), test + ": an option is cut short: " + p);
        String decision = p.substring(d);
        Matcher m = BATTLEFIELD_OPTION.matcher(decision);
        while (m.find())
            Assert.assertTrue(m.group().contains("yours") || m.group().contains("opponent's"),
                    test + ": an option does not say whose it is: " + m.group());
        int intent = p.indexOf("THE PLAY YOU CHOSE");
        Assert.assertTrue(intent < 0 || intent < d, test + ": the carried play comes after the decision");

        // The question is read whole: nothing in it is cut short, it names no engine id, and it coaches no deck.
        String question = p.substring(d, questionEnd(p, d));
        Assert.assertFalse(CUT_TEXT.matcher(question).find(), test + ": the question is cut short: " + question);
        // A card the decision is about is quoted in brackets with its text ("[Lotus Petal: {T}, Sacrifice ...]"):
        // naming the source is not coaching. Only the harness's own words are checked.
        String own = question.replaceAll("\\[[^\\]]*\\]", "[]");
        for (String w : COACHING) Assert.assertFalse(own.contains(w), test + ": the question coaches a deck's line (" + w + "): " + question);
        Assert.assertFalse(decision.contains("CARDNAME") || decision.contains("NICKNAME"), test + ": an engine placeholder is shown instead of the card's name: " + decision);
        Matcher filter = ENGINE_FILTER.matcher(decision);
        if (filter.find()) Assert.fail(test + ": the engine's filter syntax is shown (" + filter.group() + "): " + decision);

        // Options: numbered 1..n in order, no label twice, no engine id.
        List<String> labels = new ArrayList<>();
        Matcher o = OPTION.matcher(decision);
        while (o.find()) {
            Assert.assertEquals(Integer.parseInt(o.group(1)), labels.size() + 1, test + ": options are not numbered 1..n in order: " + decision);
            labels.add(o.group(2).trim());
        }
        Set<String> seen = new HashSet<>();
        for (String l : labels) Assert.assertTrue(seen.add(l), test + ": an option repeats with nothing to tell the copies apart: " + l);
        String asked = p.substring(d, labels.isEmpty() ? questionEnd(p, d) : d + endOfMenu(decision, labels.size()));
        Matcher id = ENGINE_ID.matcher(asked);
        if (id.find()) Assert.fail(test + ": an engine card id is shown ('" + id.group() + "'): " + asked);
        // Nor anywhere else the seat reads: the state's stack and event lines carried them (runs/luna-v9/g4, g5).
        // Nor Forge's debugging note on a trigger (the C3 stack carried "[Card: Tendrils of Agony @293, Activator: Seat, ...]").
        Matcher note = triggerNote().matcher(p);
        if (note.find()) Assert.fail(test + ": an engine note is shown ('" + note.group() + "'): ..."
                + p.substring(Math.max(0, note.start() - 120), Math.min(p.length(), note.end() + 80)) + "...");
        Matcher anywhere = STATE_ENGINE_ID.matcher(p.substring(0, d));
        if (anywhere.find()) Assert.fail(test + ": an engine card id is shown in the state ('" + anywhere.group() + "'): ..."
                + p.substring(Math.max(0, anywhere.start() - 120), Math.min(d, anywhere.end() + 60)) + "...");

        // The answer contract matches the menu: a CHOICE names one of at least two options, a subset
        // states a range the menu can meet, a free number states its legal range.
        String tail = decision.substring(Math.max(0, decision.lastIndexOf("EXACTLY")));
        if (tail.contains("CHOICE: <number>"))
            Assert.assertTrue(labels.size() >= 2, test + ": a CHOICE ask with " + labels.size() + " option(s): " + decision);
        if (tail.contains("PICKS:")) {
            Matcher r = RANGE.matcher(decision);
            Assert.assertTrue(r.find(), test + ": a subset ask that does not say how many to pick: " + decision);
            int lo = Integer.parseInt(r.group(1)), hi = Integer.parseInt(r.group(2));
            Assert.assertTrue(0 <= lo && lo <= hi && hi <= labels.size(),
                    test + ": the subset range " + lo + ".." + hi + " does not fit " + labels.size() + " options: " + decision);
        }
        if (tail.contains("NUMBER: <integer>")) {
            Matcher r = NUMBER_RANGE.matcher(decision);
            Assert.assertTrue(r.find() && Integer.parseInt(r.group(1)) < Integer.parseInt(r.group(2)),
                    test + ": a number ask that does not state its legal range: " + decision);
        }

        // The observation never contradicts the menu: a hand card tagged castable is on the play menu,
        // and a hand card the menu casts is not tagged uncastable.
        if (decision.startsWith("DECISION: You have priority")) {
            int h = p.indexOf("\n  hand(");
            String hand = h < 0 ? "" : p.substring(h, p.indexOf('\n', h + 1));
            Matcher t = HAND_TAG.matcher(hand);
            while (t.find()) {
                boolean onMenu = Pattern.compile("(?m)^  \\d+\\) CAST [^\\n]*@" + t.group(1) + "\\b").matcher(decision).find();
                Assert.assertEquals(onMenu, t.group(2).startsWith("castable"),
                        test + ": the hand says @" + t.group(1) + " is (" + t.group(2) + ") but the menu "
                        + (onMenu ? "casts" : "cannot cast") + " it:\n" + hand + "\n" + decision);
            }
        }
    }

    /** Offset (in {@code decision}) just past the n-th option line. */
    private static int endOfMenu(String decision, int n) {
        Matcher o = OPTION.matcher(decision);
        int end = 0;
        for (int i = 0; i < n && o.find(); i++) end = o.end();
        return end;
    }

    /** The source an ask turns on is named with its rules text (or, for an ability, its description). */
    static void namesSource(String test, List<String> asked, String name, String rulesText) {
        String text = LlmSpikeGui.flat(rulesText);
        String head = text.substring(0, Math.min(40, text.length()));
        for (String p : new ArrayList<>(asked)) {
            String decision = p.substring(Math.max(0, p.lastIndexOf("DECISION:")));
            Assert.assertTrue(decision.contains(name) && decision.contains(head),
                    test + ": the ask does not name its source " + name + " with its rules text ('" + head + "'): " + decision);
        }
    }
}
