package forge.spike;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.swing.SwingUtilities;

import com.google.common.collect.Lists;

import forge.GuiDesktop;
import forge.LobbyPlayer;
import forge.Singletons;
import forge.ai.ComputerUtilAbility;
import forge.ai.ComputerUtilMana;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.deck.Deck;
import forge.deck.DeckSection;
import forge.deck.DeckgenUtil;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.GameType;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardCollectionView;
import forge.game.card.CardLists;
import forge.game.card.CardPredicates;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.phase.PhaseHandler;
import forge.game.phase.PhaseType;
import forge.game.player.DelayedReveal;
import forge.game.player.Player;
import forge.game.player.PlayerActionConfirmMode;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.TargetRestrictions;
import forge.game.zone.ZoneType;
import forge.gamemodes.match.HostedMatch;
import forge.gui.GuiBase;
import forge.util.ITriggerEvent;
import forge.util.collect.FCollectionView;

/**
 * SPIKE (throwaway): launch the real Forge desktop window and WATCH two custom
 * LLM PlayerControllers play a game AGAINST EACH OTHER, in spectator mode.
 *
 * v2 of the Opus-vs-Opus variant. Goals of this pass:
 *   1. FULL CONTEXT — every decision prompt carries a compact snapshot of the whole
 *      game state (both boards, hands, graveyards, life, lands, stack) plus a per-seat
 *      deck game-plan framing sent once into a persistent per-seat conversation (chat style).
 *   2. ROUTE (NEARLY) ALL DECISIONS TO THE LLM — beyond mulligan/play/attack/block, a single
 *      generic decision router is adapted onto the high-value choice callbacks: discard
 *      (the Faithless-Looting / reanimator-fuel fix), single & multi entity selection,
 *      cards-for-effect, confirmAction, chooseNumber. Pure spell-target assignment
 *      (chooseTargetsFor) still delegates to Forge AI — but fixing discard makes the yard
 *      hold exactly the right fatty, so AI targeting picks it by construction.
 *   3. MODEL TIERING — a fast model (haiku) handles high-frequency low-stakes spots; the
 *      smart model (opus) handles load-bearing sequencing/combat/targets. A per-game call
 *      budget and try/catch->AI on every override guarantee it never hangs or crashes.
 *
 * Run with working dir = forge-gui-desktop/ so assets resolve to ../forge-gui/res.
 */
public final class LlmSpikeGui {
    private LlmSpikeGui() {}

    /** Our agent's controller: extend the AI, route decisions to Claude, delegate to super on
     *  anything we don't handle or that fails. One instance per seat — all state is per-instance. */
    static class PlayerControllerLLM extends PlayerControllerAi {
        private static final int MAX_PLAY_DECISIONS_PER_TURN = 25;
        private static final int SMART_BUDGET = envInt("SMART_BUDGET", 320); // per-game load-bearing LLM calls
        private static final int FAST_BUDGET  = envInt("FAST_BUDGET", 900);  // per-game cheap LLM calls

        private final String seat;        // player label (for logs)
        private final String deckPlan;    // strategy framing for this seat
        private final ClaudeBridge smart; // opus — sequencing, combat, targets
        private final ClaudeBridge fast;  // haiku — confirms, numbers, cleanup
        private int smartCalls = 0, fastCalls = 0;
        private int autoPicks = 0;      // deterministic LEGAL fallback (a sane engine-supplied option, NOT Forge's AI brain)
        private int aiDelegations = 0;  // true handoff to Forge's AI for a mechanic we don't model — we want this ~0

        // per-turn play-loop state (was static in v1 — now per-instance so two seats don't collide)
        private int lastPlayTurn = -1;
        private int playDecisionsThisTurn = 0;
        private String lastAskSig = "";
        private boolean lastWasPass = false;

        PlayerControllerLLM(Game game, Player p, LobbyPlayer lp, String seat, String deckPlan,
                            String smartModel, String fastModel) {
            super(game, p, lp);
            this.seat = seat;
            this.deckPlan = deckPlan;
            this.smart = new ClaudeBridge(seat + "/smart", smartModel, framing(seat, deckPlan, smartModel));
            this.fast  = new ClaudeBridge(seat + "/fast",  fastModel,  framing(seat, deckPlan, fastModel));
        }

        private void p(String s) { System.out.println("[" + seat + "] " + s); }
        private static int envInt(String k, int def) {
            try { String v = System.getenv(k); return v == null ? def : Integer.parseInt(v.trim()); }
            catch (Exception e) { return def; }
        }
        private static String framing(String seat, String plan, String model) {
            return "You are a world-class Magic: The Gathering (Legacy) player piloting the \"" + seat
                    + "\" deck in a game inside the Forge engine. GAME PLAN: " + plan + "\n"
                    + "I am the engine. For each decision I will send you the current game state and a question with a "
                    + "NUMBERED list of the ONLY legal options. Think about your game plan, then choose. "
                    + "Reply with at most two short sentences of reasoning, then end your message with a final line in EXACTLY "
                    + "the format I request (e.g. `CHOICE: 3` or `PICKS: 1,4`). I parse ONLY that final line, so never put "
                    + "anything after it. Play to WIN the game, not just the current decision.";
        }

        // ---------------- generic decision router ----------------

        /** Ask a bridge to pick ONE option (1-based in the prompt); returns 0-based index, or -1 for
         *  none/parse-failure. `noneLabel` (nullable) adds an extra "decline" option. */
        private int decideOne(ClaudeBridge b, String question, List<String> options, String noneLabel) {
            StringBuilder menu = new StringBuilder();
            int n = 1;
            for (String o : options) menu.append("  ").append(n++).append(") ").append(oneLine(o)).append('\n');
            int noneIdx = -1;
            if (noneLabel != null) { noneIdx = n; menu.append("  ").append(n++).append(") ").append(noneLabel).append('\n'); }
            String base = snapshot() + "\nDECISION: " + question + "\nOptions:\n" + menu
                    + "Reason briefly, then end with EXACTLY: CHOICE: <number>";
            for (int attempt = 0; attempt < 2; attempt++) {
                String prompt = attempt == 0 ? base
                        : "That reply had no parseable choice. Answer with ONE line only, nothing else: CHOICE: <a number from the list above>.";
                String reply = b.ask(prompt);
                if (reply == null) return -2;              // bridge dead -> caller decides
                int c = parseTagNumber(reply, "CHOICE:");
                if (c == noneIdx && noneIdx > 0) return -1; // explicit decline
                if (c >= 1 && c <= options.size()) return c - 1;
            }
            return -1;                                      // still unparseable after retry -> caller's legal default
        }

        /** Ask a bridge to pick BETWEEN min..max options; returns 0-based indices, or null on failure. */
        private List<Integer> decideMany(ClaudeBridge b, String question, List<String> options, int min, int max) {
            StringBuilder menu = new StringBuilder();
            int n = 1;
            for (String o : options) menu.append("  ").append(n++).append(") ").append(oneLine(o)).append('\n');
            String base = snapshot() + "\nDECISION: " + question + "\nChoose between " + min + " and " + max
                    + " of these (by number):\n" + menu
                    + "Reason briefly, then end with EXACTLY: PICKS: <comma-separated numbers>";
            for (int attempt = 0; attempt < 2; attempt++) {
                String prompt = attempt == 0 ? base
                        : "That reply had no parseable PICKS line. Answer with ONE line only, nothing else: PICKS: <"
                          + min + " to " + max + " numbers from the list above, comma-separated>.";
                String reply = b.ask(prompt);
                if (reply == null) return null;
                String seg = lastSegment(reply, "PICKS:");
                if (seg == null) continue;
                java.util.LinkedHashSet<Integer> picks = new java.util.LinkedHashSet<>();
                Matcher m = Pattern.compile("\\d+").matcher(seg);
                while (m.find()) {
                    int idx = Integer.parseInt(m.group());
                    if (idx >= 1 && idx <= options.size()) picks.add(idx - 1);
                }
                List<Integer> out = new ArrayList<>(picks);
                if (out.size() > max) out = out.subList(0, max);
                if (out.size() >= min) return out;
            }
            return null; // still short after retry -> caller's legal default
        }

        // ---------------- full-context serializer ----------------

        private String snapshot() {
            try {
                Player me = getPlayer();
                Game g = me.getGame();
                PhaseHandler ph = g.getPhaseHandler();
                Player opp = null;
                for (Player o : me.getOpponents()) { opp = o; break; }
                int untapped = 0, totalLands = 0;
                for (Card c : me.getCardsIn(ZoneType.Battlefield)) {
                    if (c.isLand()) { totalLands++; if (!c.isTapped()) untapped++; }
                }
                StringBuilder s = new StringBuilder();
                s.append("== Turn ").append(ph.getTurn()).append(' ').append(ph.getPhase())
                        .append(" (active: ").append(ph.getPlayerTurn() != null ? ph.getPlayerTurn().getName() : "?").append(") ==\n");
                s.append("YOU [").append(seat).append("] life ").append(me.getLife()).append('\n');
                s.append("  hand(").append(me.getCardsIn(ZoneType.Hand).size()).append("): ").append(names(me.getCardsIn(ZoneType.Hand), false, 40)).append('\n');
                s.append("  board: ").append(perms(me)).append('\n');
                s.append("  lands: ").append(untapped).append(" untapped / ").append(totalLands).append(" total\n");
                s.append("  yard(").append(me.getCardsIn(ZoneType.Graveyard).size()).append("): ").append(names(me.getCardsIn(ZoneType.Graveyard), false, 30)).append('\n');
                if (opp != null) {
                    s.append("OPP [").append(opp.getName()).append("] life ").append(opp.getLife()).append('\n');
                    s.append("  board: ").append(perms(opp)).append('\n');
                    s.append("  yard(").append(opp.getCardsIn(ZoneType.Graveyard).size()).append("): ").append(names(opp.getCardsIn(ZoneType.Graveyard), false, 30)).append('\n');
                    s.append("  hand: ").append(opp.getCardsIn(ZoneType.Hand).size()).append(" cards\n");
                }
                int stackSize = g.getStack().size();
                s.append("stack: ").append(stackSize == 0 ? "(empty)" : (stackSize + " pending; top=" + safeStackTop(g)));
                return s.toString();
            } catch (Exception e) {
                return "== (state unavailable) ==";
            }
        }
        private static String safeStackTop(Game g) {
            try { return g.getStack().peekAbility().getHostCard().getName(); } catch (Exception e) { return "?"; }
        }
        private static String names(Iterable<Card> cards, boolean withPT, int cap) {
            StringBuilder b = new StringBuilder(); int i = 0;
            for (Card c : cards) {
                if (i++ >= cap) { b.append(", ..."); break; }
                if (b.length() > 0) b.append(", ");
                b.append(c.getName());
                if (withPT && c.isCreature()) b.append('(').append(c.getNetPower()).append('/').append(c.getNetToughness()).append(')');
            }
            return b.length() == 0 ? "(none)" : b.toString();
        }
        /** permanents with P/T + tapped/sick flags. */
        private static String perms(Player pl) {
            StringBuilder b = new StringBuilder();
            for (Card c : pl.getCardsIn(ZoneType.Battlefield)) {
                if (c.isLand()) continue; // lands summarized separately for `me`; keep opp board tight too
                if (b.length() > 0) b.append(", ");
                b.append(c.getName());
                if (c.isCreature()) b.append('(').append(c.getNetPower()).append('/').append(c.getNetToughness()).append(')');
                if (c.isTapped()) b.append("[T]");
                if (c.isSick() && c.isCreature()) b.append("[sick]");
            }
            return b.length() == 0 ? "(no nonland permanents)" : b.toString();
        }

        // ---------------- existing 4 decisions, now with full context + smart bridge ----------------

        @Override
        public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
            CardCollectionView hand = player.getCardsIn(ZoneType.Hand);
            int lands = CardLists.filter(hand, CardPredicates.LANDS).size();
            int size = hand.size();
            // HARD FLOOR: never spiral below 5 cards. A functional 5-card hand beats mulliganing to oblivion —
            // this is the guardrail against a model that likes to ship every imperfect hand.
            if (size <= 5) { p("[ROUTER] mulligan: FLOOR keep (" + lands + "/" + size + ")"); return true; }
            // A 2-4 land opener is a routine, near-always-keepable hand -> keep deterministically, no LLM.
            if (lands >= 2 && lands <= 4) {
                p("[ROUTER] mulligan: heuristic KEEP (" + lands + "/" + size + " lands)");
                return true;
            }
            // Only the extremes (0-1 lands, or 5+ = flood) reach the model, and only above the 5-card floor.
            if (!smartOk()) return aiDelegate("mulligan(no-budget)", super.mulliganKeepHand(firstPlayer, cardsToReturn));
            StringBuilder n = new StringBuilder();
            for (Card c : hand) { if (n.length() > 0) n.append(", "); n.append(c.getName()); }
            int afterKeep = size; // this Forge deals a shrinking hand each mulligan; you keep exactly what you see
            String prompt = "MULLIGAN DECISION. This is a " + size + "-card hand with " + lands + " land(s): " + n
                    + ". If you MULLIGAN you will look at a NEW hand of only " + Math.max(0, size - 1)
                    + " cards (each mulligan costs you a card), and you can never go below 5. If you KEEP you play this "
                    + afterKeep + "-card hand as-is. Keeping a workable hand almost always beats shrinking it. "
                    + "For a combo deck, a hand with your enablers + a payoff is a KEEP even on few lands; only ship a hand "
                    + "that genuinely cannot function. Keep or mulligan? End with EXACTLY one word: KEEP or MULLIGAN.";
            p("[ROUTER] mulligan: " + lands + "/" + size + " lands -> asking " + smart.model + "...");
            String reply = smart.ask(prompt);
            if (reply == null) return aiDelegate("mulligan(bridge-down)", super.mulliganKeepHand(firstPlayer, cardsToReturn));
            String r = reply.toUpperCase();
            int k = r.indexOf("KEEP"), mu = r.indexOf("MULLIGAN");
            boolean keep = k >= 0 && (mu < 0 || k < mu);
            p("[ROUTER] mulligan: " + (keep ? "KEEP" : "MULLIGAN"));
            return keep;
        }

        @Override
        public SpellAbility getAbilityToPlay(Card hostCard, List<SpellAbility> abilities, ITriggerEvent triggerEvent) {
            return super.getAbilityToPlay(hostCard, abilities, triggerEvent);
        }

        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            Player defender = null;
            for (Player pl : combat.getDefendingPlayers()) { defender = pl; break; }
            if (defender == null) { super.declareAttackers(attacker, combat); return; }
            CardCollection legal = new CardCollection();
            for (Card c : attacker.getCreaturesInPlay()) if (CombatUtil.canAttack(c, defender)) legal.add(c);
            if (legal.isEmpty()) return;
            if (!smartOk()) { noteAi("declareAttackers(no-budget)"); super.declareAttackers(attacker, combat); return; }
            StringBuilder mine = new StringBuilder();
            for (Card c : legal) { if (mine.length() > 0) mine.append(", "); mine.append(c.getName()).append(" (").append(c.getNetPower()).append('/').append(c.getNetToughness()).append(')'); }
            String prompt = "COMBAT — declare attackers. Attackers available: " + mine + ". "
                    + "Which attack? End with EXACTLY: ATTACKERS: <comma-separated names, or NONE>";
            p("[ROUTER] attackers: " + legal.size() + " can attack -> asking " + smart.model + "...");
            String reply = smart.ask(prompt);
            if (reply == null) { noteAi("declareAttackers(bridge-down)"); super.declareAttackers(attacker, combat); return; }
            String decision = lastSegment(reply, "ATTACKERS:");
            if (decision == null) decision = "";
            String d = decision.toUpperCase();
            int declared = 0; StringBuilder chosen = new StringBuilder();
            if (!d.contains("NONE")) {
                for (Card c : legal) if (d.contains(c.getName().toUpperCase())) {
                    combat.addAttacker(c, defender); declared++;
                    if (chosen.length() > 0) chosen.append(", "); chosen.append(c.getName());
                }
            }
            p("[ROUTER] attackers: " + (declared == 0 ? "none" : chosen.toString()));
        }

        @Override
        public void declareBlockers(Player defender, Combat combat) {
            try {
                CardCollection attackers = combat.getAttackers();
                if (attackers == null || attackers.isEmpty()) return;
                CardCollection blockers = new CardCollection();
                for (Card c : defender.getCreaturesInPlay())
                    for (Card atk : attackers) if (CombatUtil.canBlock(atk, c, combat)) { blockers.add(c); break; }
                if (blockers.isEmpty()) return;
                if (!smartOk()) { noteAi("declareBlockers(no-budget)"); super.declareBlockers(defender, combat); return; }
                StringBuilder atk = new StringBuilder();
                for (Card a : attackers) { if (atk.length() > 0) atk.append(", "); atk.append(a.getName()).append(" (").append(a.getNetPower()).append('/').append(a.getNetToughness()).append(')'); }
                StringBuilder blk = new StringBuilder();
                for (Card b : blockers) { if (blk.length() > 0) blk.append(", "); blk.append(b.getName()).append(" (").append(b.getNetPower()).append('/').append(b.getNetToughness()).append(')'); }
                String prompt = "COMBAT — declare blockers. Incoming: " + atk + ". Your blockers: " + blk + ". "
                        + "Assign blocks (each blocker blocks one attacker). End with EXACTLY: "
                        + "BLOCKS: blocker->attacker, blocker->attacker   (or BLOCKS: NONE)";
                p("[ROUTER] blockers: " + attackers.size() + " atk / " + blockers.size() + " blk -> asking " + smart.model + "...");
                String reply = smart.ask(prompt);
                if (reply == null) { noteAi("declareBlockers(bridge-down)"); super.declareBlockers(defender, combat); return; }
                String line = lastSegment(reply, "BLOCKS:");
                if (line == null || line.toUpperCase().contains("NONE")) { p("[ROUTER] blockers: NONE"); return; }
                int assigned = 0;
                for (String pair : line.split(",")) {
                    int arrow = pair.indexOf("->"); if (arrow < 0) continue;
                    Card b = matchByName(blockers, pair.substring(0, arrow).toUpperCase());
                    Card a = matchByName(attackers, pair.substring(arrow + 2).toUpperCase());
                    if (a != null && b != null && CombatUtil.canBlock(a, b, combat)) {
                        combat.addBlocker(a, b); assigned++; blockers.remove(b);
                    }
                }
                p("[ROUTER] blockers: assigned " + assigned);
            } catch (Exception e) {
                noteAi("declareBlockers(err " + e + ")");
                super.declareBlockers(defender, combat);
            }
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            // IMPORTANT: the engine passes priority ONLY on a null return (see PhaseHandler.mainLoopStep:
            // `if (chosenSa == null) break;`). An EMPTY list means "play zero abilities, then ask again",
            // which spins the priority loop ~999x ("AI looped too much"). So PASS must be null, never empty.
            final List<SpellAbility> PASS = null;
            try {
                Player me = getPlayer();
                PhaseHandler phh = me.getGame().getPhaseHandler();
                boolean ownMain = phh.isPlayerTurn(me) && (phh.is(PhaseType.MAIN1) || phh.is(PhaseType.MAIN2));

                // SPEED: on a reactive (not-our-main) priority window, only think when something can
                // actually matter — a spell/ability is on the stack, or we're in combat (tricks/removal).
                // Otherwise passing priority is virtually always correct, so pass WITHOUT spending an LLM call.
                if (!ownMain && me.getGame().getStack().isEmpty() && !phh.inCombat()) return PASS;

                // enumerate castable non-land, non-mana abilities
                List<SpellAbility> options = new ArrayList<>();
                for (SpellAbility sa : ComputerUtilAbility.getSpellAbilities(
                        ComputerUtilAbility.getAvailableCards(me.getGame(), me), me)) {
                    if (sa.isLandAbility() || sa.isManaAbility()) continue;
                    if (!sa.canPlay()) continue;
                    if (!ComputerUtilMana.canPayManaCost(sa, me, 0, false)) continue;
                    options.add(sa);
                    if (options.size() >= 12) break;
                }
                // Distinct lands we could play this main phase (by name), each offered as its own option so the
                // model chooses WHICH land — colored sources, fetchlands, painlands all matter for sequencing.
                List<SpellAbility> landPlays = new ArrayList<>();
                List<Card> landCards = new ArrayList<>();
                if (ownMain) {
                    CardCollection lands = ComputerUtilAbility.getAvailableLandsToPlay(me.getGame(), me);
                    if (lands != null) {
                        java.util.HashSet<String> seen = new java.util.HashSet<>();
                        for (Card lc : lands) {
                            if (!seen.add(lc.getName())) continue;
                            SpellAbility lsa = null;
                            for (SpellAbility sa : lc.getAllPossibleAbilities(me, true)) if (sa.isLandAbility()) { lsa = sa; break; }
                            if (lsa != null) { landPlays.add(lsa); landCards.add(lc); }
                            if (landPlays.size() >= 4) break;
                        }
                    }
                }
                boolean haveLand = !landPlays.isEmpty();
                // Nothing to do -> pass (deterministic; never Forge's AI).
                if (options.isEmpty() && !haveLand) return PASS;
                if (!ownMain && options.isEmpty()) return PASS;

                // pick tier: own main phase = smart sequencing; reactive instant-speed window = fast
                ClaudeBridge b = ownMain ? smart : fast;
                boolean haveBudget = ownMain ? smartOk() : fastOk();

                int turn = phh.getTurn();
                if (turn != lastPlayTurn) { lastPlayTurn = turn; playDecisionsThisTurn = 0; }
                boolean overCap = playDecisionsThisTurn >= MAX_PLAY_DECISIONS_PER_TURN;
                if (!haveBudget || overCap) { // exhausted -> develop mana if possible, else pass. Not the AI.
                    if (haveLand) { auto("play(no-budget): land " + landCards.get(0).getName()); return Lists.newArrayList(landPlays.get(0)); }
                    return PASS;
                }

                StringBuilder menu = new StringBuilder(); int idx = 1;
                for (SpellAbility sa : options) menu.append("  ").append(idx++).append(") CAST ").append(saLabel(sa)).append('\n');
                int landStart = idx;
                for (int i = 0; i < landPlays.size(); i++) menu.append("  ").append(idx++).append(") PLAY LAND ").append(landCards.get(i).getName()).append('\n');
                int passIdx = idx; menu.append("  ").append(idx).append(") PASS (do nothing)\n");

                String sig = turn + "|" + phh.getPhase() + "|" + menu;
                if (sig.equals(lastAskSig) && lastWasPass) return PASS; // identical spot we already passed on
                lastAskSig = sig;
                String menuStr = menu.toString();

                if (ownMain) p("[TALLY] smart=" + smartCalls + " fast=" + fastCalls + " auto=" + autoPicks + " ai=" + aiDelegations);
                p("[ROUTER] play(" + (ownMain ? "main" : "react") + "): " + options.size() + " castable"
                        + (haveLand ? " + " + landPlays.size() + " land" : "") + " -> asking " + b.model + "...");
                playDecisionsThisTurn++;

                int choice = -1;
                for (int attempt = 0; attempt < 2 && choice < 1; attempt++) {
                    String prompt = attempt == 0
                            ? snapshot() + "\nDECISION: You have priority (" + phh.getPhase()
                              + (ownMain ? ", your main phase" : ", instant-speed window") + "). Choose ONE action:\n" + menuStr
                              + "Reason briefly, then end with EXACTLY: CHOICE: <number>"
                            : "That reply had no parseable choice. Answer with ONE line only: CHOICE: <a number 1.." + passIdx + " from the list above>.";
                    String reply = b.ask(prompt);
                    if (reply == null) { // bridge dead -> develop or pass, not the AI
                        if (haveLand) { auto("play(bridge-down): land " + landCards.get(0).getName()); return Lists.newArrayList(landPlays.get(0)); }
                        return PASS;
                    }
                    choice = parseTagNumber(reply, "CHOICE:");
                }
                if (choice == passIdx || choice < 1 || choice > passIdx) { lastWasPass = true; p("[ROUTER] play: PASS"); return PASS; }
                lastWasPass = false;
                if (haveLand && choice >= landStart && choice < passIdx) {
                    int li = choice - landStart;
                    p("[ROUTER] play: land " + landCards.get(li).getName());
                    return Lists.newArrayList(landPlays.get(li));
                }
                SpellAbility chosen = options.get(choice - 1);
                p("[ROUTER] play: casts " + saLabel(chosen));
                return Lists.newArrayList(chosen);
            } catch (Exception e) {
                p("[ROUTER] play: error " + e + " -> PASS");
                return PASS;
            }
        }

        // ---------------- NEW: routed choice callbacks (generic router + AI fallback) ----------------

        @Override
        public <T extends GameEntity> T chooseSingleEntityForEffect(FCollectionView<T> optionList, DelayedReveal delayedReveal,
                SpellAbility sa, String title, boolean isOptional, Player relatedPlayer, Map<String, Object> params) {
            List<T> opts = new ArrayList<>();
            for (T t : optionList) opts.add(t);
            if (opts.isEmpty())
                return isOptional ? null : super.chooseSingleEntityForEffect(optionList, delayedReveal, sa, title, isOptional, relatedPlayer, params);
            // delayedReveal carries a reveal side-effect — keep the engine's reveal semantics.
            if (delayedReveal != null)
                return aiDelegate("choose-entity(delayedReveal)" + srcOf(sa),
                        super.chooseSingleEntityForEffect(optionList, delayedReveal, sa, title, isOptional, relatedPlayer, params));
            if (opts.size() == 1 && !isOptional) return opts.get(0); // forced, no need to ask
            try {
                if (!smartOk()) { auto("choose-entity(no-budget)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                List<String> labels = new ArrayList<>();
                for (T t : opts) labels.add(entityLabel(t));
                String q = (title == null || title.isEmpty() ? "Choose one" : title) + srcOf(sa);
                int c = decideOne(smart, q, labels, isOptional ? "Decline / none" : null);
                if (c == -2) { auto("choose-entity(bridge-down)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                if (c < 0) { if (isOptional) return null; auto("choose-entity(unparsed)->first" + srcOf(sa)); return opts.get(0); }
                p("[ROUTER] choose-entity: " + labels.get(c) + srcOf(sa));
                return opts.get(c);
            } catch (Exception e) {
                auto("choose-entity(err)->first" + srcOf(sa)); return isOptional ? null : opts.get(0);
            }
        }

        /** Route the reanimation / tutor target: "which card leaves this zone" (Reanimate, Animate Dead,
         *  Entomb, Unmarked Grave, fetch effects...). THE key strategic pick for the reanimator deck. */
        @Override
        public Card chooseSingleCardForZoneChange(ZoneType destination, List<ZoneType> origin, SpellAbility sa,
                CardCollection fetchList, DelayedReveal delayedReveal, String selectPrompt, boolean isOptional, Player decider) {
            if (fetchList == null || fetchList.isEmpty() || delayedReveal != null || decider != getPlayer())
                return super.chooseSingleCardForZoneChange(destination, origin, sa, fetchList, delayedReveal, selectPrompt, isOptional, decider);
            List<Card> opts = new ArrayList<>(fetchList);
            if (opts.size() == 1 && !isOptional) return opts.get(0); // forced
            try {
                if (!smartOk()) { auto("zone-change(no-budget)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(entityLabel(c));
                String q = (selectPrompt == null || selectPrompt.isEmpty() ? "Choose a card" : selectPrompt)
                        + " -> " + destination + srcOf(sa)
                        + " (pick the card that best advances your game plan — e.g. reanimate your biggest threat).";
                int c = decideOne(smart, q, labels, isOptional ? "Decline / none" : null);
                if (c == -2) { auto("zone-change(bridge-down)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                if (c < 0) { if (isOptional) return null; auto("zone-change(unparsed)->first" + srcOf(sa)); return opts.get(0); }
                p("[ROUTER] zone-change: " + labels.get(c) + " -> " + destination + srcOf(sa));
                return opts.get(c);
            } catch (Exception e) {
                auto("zone-change(err)->first" + srcOf(sa)); return isOptional ? null : opts.get(0);
            }
        }

        /** Route spell/ability TARGETING for the clean single-target case (removal, Reanimate, Thoughtseize...).
         *  Heavily guarded: exactly one target, no chained sub-ability targets, >1 candidate. Anything else
         *  delegates to Forge's targeting brain. Every candidate is pre-validated by sa.canTarget(). */
        @Override
        public boolean chooseTargetsFor(SpellAbility sa) {
            try {
                if (sa == null || !sa.usesTargeting() || sa.getMaxTargets() != 1 || sa.getSubAbility() != null)
                    return super.chooseTargetsFor(sa);
                TargetRestrictions tr = sa.getTargetRestrictions();
                if (tr == null) return super.chooseTargetsFor(sa);
                List<GameEntity> cands = tr.getAllCandidates(sa, false);
                if (cands == null || cands.size() <= 1 || !smartOk()) return super.chooseTargetsFor(sa);
                int min = sa.getMinTargets();
                List<String> labels = new ArrayList<>();
                for (GameEntity ge : cands) labels.add(entityLabel(ge));
                String q = "Choose the TARGET for " + saLabel(sa) + " (the spell/ability resolves against whatever you pick).";
                int c = decideOne(smart, q, labels, min == 0 ? "No target (decline)" : null);
                if (c == -2) return super.chooseTargetsFor(sa);      // bridge dead -> AI targeting
                if (c < 0) {                                          // decline / unparsed
                    if (min == 0) { sa.resetTargets(); return true; } // optional -> legally choose no target
                    return super.chooseTargetsFor(sa);
                }
                sa.resetTargets();
                boolean added = sa.getTargets().add(cands.get(c));
                if (!added || sa.getTargets().size() < Math.max(1, min)) return super.chooseTargetsFor(sa); // safety net
                p("[ROUTER] target: " + labels.get(c) + " <- " + saLabel(sa));
                return true;
            } catch (Exception e) {
                return super.chooseTargetsFor(sa);
            }
        }

        @Override
        public CardCollection chooseCardsToDiscardFrom(Player p, SpellAbility sa, CardCollection validCards,
                int min, int max, CardCollectionView visibleToChooser) {
            if (validCards == null || validCards.isEmpty())
                return super.chooseCardsToDiscardFrom(p, sa, validCards, min, max, visibleToChooser);
            if (p != getPlayer()) // someone else is discarding under our control window — defer to engine
                return aiDelegate("discard(other-player)", super.chooseCardsToDiscardFrom(p, sa, validCards, min, max, visibleToChooser));
            List<Card> opts = new ArrayList<>(validCards);
            if (opts.size() <= min) return firstN(opts, min, "discard(forced)"); // whole hand forced
            try {
                if (!smartOk()) return firstN(opts, min, "discard(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(c.getName());
                String q = "You must DISCARD from your hand" + srcOf(sa) + ". Pick which card(s) to discard "
                        + "(remember your game plan — e.g. discarding a huge creature you can later reanimate is often correct).";
                List<Integer> picks = decideMany(smart, q, labels, min, max);
                if (picks == null) return firstN(opts, min, "discard(unparsed)");
                CardCollection out = new CardCollection();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(opts.get(i)); if (log.length() > 0) log.append(", "); log.append(opts.get(i).getName()); }
                p("[ROUTER] discard: " + log);
                return out;
            } catch (Exception e) {
                return firstN(opts, min, "discard(err)");
            }
        }

        @Override
        public CardCollectionView chooseCardsForEffect(CardCollectionView sourceList, SpellAbility sa, String title,
                int min, int max, boolean isOptional, Map<String, Object> params) {
            if (sourceList == null || sourceList.isEmpty())
                return super.chooseCardsForEffect(sourceList, sa, title, min, max, isOptional, params);
            List<Card> opts = new ArrayList<>(sourceList);
            if (opts.size() <= Math.max(1, min) && !isOptional) return firstN(opts, min, "cards-for-effect(forced)");
            try {
                if (!smartOk()) return isOptional ? new CardCollection() : firstN(opts, min, "cards-for-effect(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(c.getName());
                String q = (title == null || title.isEmpty() ? "Choose cards" : title) + srcOf(sa);
                List<Integer> picks = decideMany(smart, q, labels, isOptional ? 0 : min, max);
                if (picks == null) return isOptional ? new CardCollection() : firstN(opts, min, "cards-for-effect(unparsed)");
                CardCollection out = new CardCollection();
                for (int i : picks) out.add(opts.get(i));
                return out;
            } catch (Exception e) {
                return isOptional ? new CardCollection() : firstN(opts, min, "cards-for-effect(err)");
            }
        }

        @Override
        public boolean confirmAction(SpellAbility sa, PlayerActionConfirmMode mode, String message,
                List<String> options, Card cardToShow, Map<String, Object> params) {
            if (!fastOk()) return aiDelegate("confirm(no-budget)", super.confirmAction(sa, mode, message, options, cardToShow, params));
            try {
                String q = "Yes/No decision" + srcOf(sa) + ": " + (message == null ? String.valueOf(mode) : message);
                List<String> yn = Lists.newArrayList("YES (do it)", "NO (decline)");
                int c = decideOne(fast, q, yn, null);
                if (c == -2) return aiDelegate("confirm(bridge-down)", super.confirmAction(sa, mode, message, options, cardToShow, params));
                boolean yes = (c == 0); // c==-1 (unparsed) -> decline, a safe legal default
                p("[ROUTER] confirm(" + oneLine(message == null ? String.valueOf(mode) : message) + "): " + (yes ? "YES" : "NO"));
                return yes;
            } catch (Exception e) {
                return aiDelegate("confirm(err)", super.confirmAction(sa, mode, message, options, cardToShow, params));
            }
        }

        @Override
        public int chooseNumber(SpellAbility sa, String title, int min, int max) {
            if (max <= min) return min; // forced, deterministic
            if (!fastOk()) return aiDelegate("number(no-budget)", super.chooseNumber(sa, title, min, max));
            try {
                List<String> opts = new ArrayList<>();
                if (max - min <= 12) { for (int v = min; v <= max; v++) opts.add(String.valueOf(v)); }
                else { opts.add(String.valueOf(min)); opts.add(String.valueOf((min + max) / 2)); opts.add(String.valueOf(max)); }
                String q = (title == null ? "Choose a number" : title) + " (" + min + ".." + max + ")" + srcOf(sa);
                int c = decideOne(fast, q, opts, null);
                if (c == -2) return aiDelegate("number(bridge-down)", super.chooseNumber(sa, title, min, max));
                if (c < 0) return aiDelegate("number(unparsed)", super.chooseNumber(sa, title, min, max));
                int val = Integer.parseInt(opts.get(c));
                p("[ROUTER] number(" + oneLine(title) + "): " + val);
                return val;
            } catch (Exception e) {
                return aiDelegate("number(err)", super.chooseNumber(sa, title, min, max));
            }
        }

        // ---------------- budget + parse helpers ----------------

        private boolean smartOk() { if (smartCalls >= SMART_BUDGET) return false; smartCalls++; return true; }
        private boolean fastOk()  { if (fastCalls  >= FAST_BUDGET)  return false; fastCalls++;  return true; }

        /** Record + loudly log a genuine handoff to Forge's built-in AI. Grep "[FALLBACK->AI]" to audit how
         *  often the engine brain — not the LLM — actually made a call. Goal: keep this near zero. */
        private void noteAi(String where) {
            aiDelegations++;
            p("[FALLBACK->AI] " + where + "  (ai=" + aiDelegations + " smart=" + smartCalls + " fast=" + fastCalls + " auto=" + autoPicks + ")");
        }
        private <T> T aiDelegate(String where, T result) { noteAi(where); return result; }
        private void auto(String what) { autoPicks++; p("[AUTO-LEGAL] " + what); }
        /** Deterministic legal fallback for "pick some cards": take the first n (a legal set), never the AI. */
        private CardCollection firstN(List<Card> opts, int n, String why) {
            CardCollection out = new CardCollection();
            for (int i = 0; i < n && i < opts.size(); i++) out.add(opts.get(i));
            auto(why + " -> " + names(out, false, 10));
            return out;
        }

        private static String srcOf(SpellAbility sa) {
            try { return sa != null && sa.getHostCard() != null ? " [" + sa.getHostCard().getName() + "]" : ""; }
            catch (Exception e) { return ""; }
        }
        private static String entityLabel(GameEntity e) {
            if (e instanceof Card) {
                Card c = (Card) e;
                String s = c.getName();
                if (c.isCreature()) s += " (" + c.getNetPower() + "/" + c.getNetToughness() + ")";
                return s + " [" + c.getZone() + "]";
            }
            return e.getName();
        }
        private static String oneLine(String s) {
            if (s == null) return "";
            s = s.replaceAll("\\s+", " ").trim();
            return s.length() > 90 ? s.substring(0, 90) : s;
        }
        private static String saLabel(SpellAbility sa) {
            String host = sa.getHostCard() != null ? sa.getHostCard().getName() : "?";
            String desc = sa.getDescription();
            if (desc == null || desc.isEmpty()) desc = String.valueOf(sa);
            desc = desc.replaceAll("\\s+", " ").trim();
            if (desc.length() > 70) desc = desc.substring(0, 70);
            return host + " [" + desc + "]";
        }
        private static String lastSegment(String reply, String key) {
            String found = null;
            for (String ln : reply.split("\\R")) {
                int i = ln.toUpperCase().indexOf(key.toUpperCase());
                if (i >= 0) found = ln.substring(i + key.length());
            }
            return found;
        }
        private static int parseTagNumber(String reply, String key) {
            String seg = lastSegment(reply, key);
            if (seg == null) return -1;
            Matcher m = Pattern.compile("\\d+").matcher(seg);
            return m.find() ? Integer.parseInt(m.group()) : -1;
        }
        private static Card matchByName(Iterable<Card> cards, String upperSeg) {
            for (Card c : cards) if (upperSeg.contains(c.getName().toUpperCase())) return c;
            return null;
        }
    }

    /** LobbyPlayer whose in-game Player uses OUR controller, carrying seat + plan + models. */
    static class LobbyPlayerLLM extends LobbyPlayerAi {
        private final String plan, smartModel, fastModel;
        LobbyPlayerLLM(String name, String plan, String smartModel, String fastModel) {
            super(name, null); this.plan = plan; this.smartModel = smartModel; this.fastModel = fastModel;
        }
        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player p = new Player(getName(), game, id);
            p.setFirstController(new PlayerControllerLLM(game, p, this, getName(), plan, smartModel, fastModel));
            return p;
        }
    }

    /**
     * PERSISTENT per-(seat,model) bridge to Claude via the CLI stream-json mode. One long-lived
     * `claude -p --model <model> ...` process per instance, its own conversation (chat memory).
     * A one-time `preamble` (persona + deck plan + output contract) is prepended to the first ask.
     */
    static final class ClaudeBridge {
        private static final String BIN = System.getenv().getOrDefault("CLAUDE_BIN", "claude");
        private static final long DEADLINE_MS = parseLong(System.getenv("CLAUDE_DEADLINE_MS"), 90000);
        private static long parseLong(String v, long def) { try { return v == null ? def : Long.parseLong(v.trim()); } catch (Exception e) { return def; } }
        final String label;
        final String model;
        private final String preamble;
        private boolean primed = false;
        private Process proc;
        private BufferedReader out;
        private BufferedWriter in;

        ClaudeBridge(String label, String model, String preamble) {
            this.label = label;
            this.model = System.getenv().getOrDefault("CLAUDE_MODEL", model);
            this.preamble = preamble;
        }

        private void ensureStarted() throws Exception {
            if (proc != null && proc.isAlive()) return;
            System.out.println("[CLAUDE " + label + "] starting persistent " + model + " session...");
            ProcessBuilder pb = new ProcessBuilder(BIN, "-p", "--model", model,
                    "--input-format", "stream-json", "--output-format", "stream-json", "--verbose");
            pb.redirectErrorStream(false);
            proc = pb.start();
            out = new BufferedReader(new InputStreamReader(proc.getInputStream(), StandardCharsets.UTF_8));
            in  = new BufferedWriter(new OutputStreamWriter(proc.getOutputStream(), StandardCharsets.UTF_8));
            primed = false;
            final Process fproc = proc;
            Thread errDrain = new Thread(() -> {
                try (BufferedReader er = new BufferedReader(new InputStreamReader(fproc.getErrorStream(), StandardCharsets.UTF_8))) {
                    while (er.readLine() != null) { }
                } catch (Exception ignore) { }
            }, "claude-stderr-" + label);
            errDrain.setDaemon(true);
            errDrain.start();
        }

        synchronized String ask(String prompt) {
            long t0 = System.currentTimeMillis();
            try {
                ensureStarted();
                String full = (!primed && preamble != null) ? (preamble + "\n\n" + prompt) : prompt;
                primed = true;
                in.write("{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\""
                        + jsonEscape(full) + "\"}]}}");
                in.write("\n"); in.flush();
                long deadline = System.currentTimeMillis() + DEADLINE_MS;
                while (System.currentTimeMillis() < deadline) {
                    if (!out.ready()) {
                        if (!proc.isAlive()) { System.out.println("[CLAUDE " + label + "] process exited"); reset(); return null; }
                        Thread.sleep(20); continue;
                    }
                    String line = out.readLine();
                    if (line == null) { reset(); return null; }
                    if (line.contains("\"type\":\"result\"")) {
                        String r = extractJsonString(line, "\"result\":\"");
                        System.out.println("[CLAUDE " + label + "] replied in " + (System.currentTimeMillis() - t0) + " ms");
                        return r;
                    }
                }
                System.out.println("[CLAUDE " + label + "] timed out"); return null;
            } catch (Exception e) {
                System.out.println("[CLAUDE " + label + "] error: " + e); reset(); return null;
            }
        }

        private void reset() { try { if (proc != null) proc.destroyForcibly(); } catch (Exception ignore) { } proc = null; out = null; in = null; }

        private static String jsonEscape(String s) {
            StringBuilder b = new StringBuilder(s.length() + 16);
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '"':  b.append("\\\""); break;
                    case '\\': b.append("\\\\"); break;
                    case '\n': b.append("\\n"); break;
                    case '\r': b.append("\\r"); break;
                    case '\t': b.append("\\t"); break;
                    default: if (c < 0x20) b.append(String.format("\\u%04x", (int) c)); else b.append(c);
                }
            }
            return b.toString();
        }
        private static String extractJsonString(String line, String key) {
            int k = line.indexOf(key);
            if (k < 0) return null;
            int i = k + key.length();
            StringBuilder b = new StringBuilder();
            while (i < line.length()) {
                char c = line.charAt(i++);
                if (c == '\\' && i < line.length()) {
                    char n = line.charAt(i++);
                    switch (n) {
                        case 'n': b.append('\n'); break;
                        case 'r': b.append('\r'); break;
                        case 't': b.append('\t'); break;
                        case '"': b.append('"'); break;
                        case '\\': b.append('\\'); break;
                        case '/': b.append('/'); break;
                        case 'u': if (i + 4 <= line.length()) { b.append((char) Integer.parseInt(line.substring(i, i + 4), 16)); i += 4; } break;
                        default: b.append(n);
                    }
                } else if (c == '"') break;
                else b.append(c);
            }
            return b.toString();
        }
    }

    /** Parse a plain "N Cardname" decklist (blank line separates mainboard from sideboard). */
    static Deck loadDeck(String name, String path) throws Exception {
        Deck deck = new Deck(name);
        DeckSection section = DeckSection.Main;
        int mainCt = 0, sideCt = 0;
        Pattern entry = Pattern.compile("^(\\d+)\\s+(.*\\S)\\s*$");
        for (String raw : Files.readAllLines(Paths.get(path), StandardCharsets.UTF_8)) {
            String line = raw.trim();
            if (line.isEmpty()) { section = DeckSection.Sideboard; continue; }
            if (line.startsWith("#") || line.equalsIgnoreCase("sideboard")) { section = DeckSection.Sideboard; continue; }
            Matcher m = entry.matcher(line);
            if (!m.find()) continue;
            int qty = Integer.parseInt(m.group(1));
            deck.getOrCreate(section).add(m.group(2).trim(), qty);
            if (section == DeckSection.Main) mainCt += qty; else sideCt += qty;
        }
        System.out.println("[DECK] loaded '" + name + "': " + mainCt + " main, " + sideCt + " sideboard  (" + path + ")");
        return deck;
    }

    public static void main(String[] args) {
        GuiBase.setInterface(new GuiDesktop());
        Singletons.initializeOnce(true);
        Singletons.getControl().initialize();
        new Thread(() -> {
            try { Thread.sleep(3500); } catch (InterruptedException ignored) {}
            SwingUtilities.invokeLater(LlmSpikeGui::startSpectatorMatch);
        }, "llm-spike-launcher").start();
    }

    private static void startSpectatorMatch() {
        System.out.println("[LLM-SEAM] building Opus-vs-Opus spectator match...");
        String home = System.getProperty("user.home");
        String pathA = System.getenv().getOrDefault("DECK_A", home + "/Downloads/Deck - Grixis Reanimator.txt");
        String pathB = System.getenv().getOrDefault("DECK_B", home + "/Downloads/Deck - Mardu Energy.txt");
        String nameA = System.getenv().getOrDefault("DECK_A_NAME", "Grixis Reanimator");
        String nameB = System.getenv().getOrDefault("DECK_B_NAME", "Mardu Energy");
        String smartModel = System.getenv().getOrDefault("SMART_MODEL", "opus");
        String fastModel  = System.getenv().getOrDefault("FAST_MODEL", "haiku");

        String planA = "Grixis Reanimator combo: mulligan aggressively toward a hand that can DISCARD a huge creature "
                + "(Griselbrand, Atraxa Grand Unifier, Archon of Cruelty) via Faithless Looting / Cabal Therapy / Unmask, "
                + "then REANIMATE it turn 1-2 with Reanimate / Animate Dead / Shallow Grave (use Dark Ritual + Lotus Petal for speed). "
                + "Griselbrand's draw-7 refuels and protects. Strip disruption with Thoughtseize/Cabal Therapy. Win fast before they set up.";
        String planB = "Mardu Energy aggro-disruption: deploy efficient threats (Ajani, Ocelot Pride, Guide of Souls, Amped Raptor, "
                + "Voice of Victory), attack every turn and race their life total. Disrupt the combo with Thoughtseize, Cabal Therapy, "
                + "Orcish Bowmasters, and Swords to Plowshares; Wasteland/Karakas hit their mana. Against Reanimator, prioritize stripping "
                + "or removing their fatty and pressuring life so they can't durdle.";

        Deck deckA, deckB;
        try { deckA = loadDeck(nameA, pathA); deckB = loadDeck(nameB, pathB); }
        catch (Exception e) {
            System.out.println("[DECK] failed to load decklists (" + e + ") -> random color decks");
            deckA = DeckgenUtil.getRandomColorDeck(true); deckB = DeckgenUtil.getRandomColorDeck(true);
        }

        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(new RegisteredPlayer(deckA).setPlayer(new LobbyPlayerLLM(nameA, planA, smartModel, fastModel)));
        players.add(new RegisteredPlayer(deckB).setPlayer(new LobbyPlayerLLM(nameB, planB, smartModel, fastModel)));

        HostedMatch hosted = GuiBase.getInterface().hostMatch();
        hosted.startMatch(GameType.Constructed, EnumSet.of(GameType.Constructed), players, null);
        System.out.println("[LLM-SEAM] Opus (" + nameA + ", smart=" + smartModel + ") vs Opus (" + nameB
                + ") started; fast tier=" + fastModel + ". Watch the Forge window.");
    }
}
