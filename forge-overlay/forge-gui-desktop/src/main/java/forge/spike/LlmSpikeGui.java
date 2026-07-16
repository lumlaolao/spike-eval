package forge.spike;

import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;

import javax.imageio.ImageIO;
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
import forge.StaticData;
import forge.card.ICardFace;
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
import forge.game.spellability.AbilitySub;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.TargetRestrictions;
import forge.game.zone.ZoneType;
import forge.gamemodes.match.HostedMatch;
import forge.gui.GuiBase;
import forge.view.FFrame;
import forge.view.FView;
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
 *      cards-for-effect, confirmAction, chooseNumber. Spell TARGETING is routed too:
 *      chosen spells get engine-validated, LLM-picked targets set BEFORE the AI cast path
 *      (ensureTargetsSet — that path runs no targeting for ordinary spells and would fizzle),
 *      and a chooseTargetsFor override covers the remaining engine-initiated targeting paths.
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
            return "You are a world-class Magic: The Gathering (Legacy) player piloting \"" + seat + "\".\n"
                    + "GAME PLAN: " + plan + "\n"
                    + "PROTOCOL: I am the Forge rules engine. Each message gives you the CURRENT game state and ONE "
                    + "decision with a numbered list of the ONLY legal options — every option is already "
                    + "engine-validated, so pick purely on strategy. The state snapshot in each message is "
                    + "authoritative: trust it over your memory of earlier turns.\n"
                    + "PRINCIPLES: (1) Sequence toward your game plan, not the shiniest single play. "
                    + "(2) At instant speed with nothing to gain, PASS. "
                    + "(3) In combat, count damage and their untapped mana before committing. "
                    + "(4) Life, cards, and tempo are resources — spend them to win, not to feel safe.\n"
                    + "FORMAT: at most two short sentences of reasoning, then a final line in EXACTLY the format I "
                    + "request (e.g. `CHOICE: 3` or `PICKS: 1,4`). I parse ONLY that final line — never write "
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
                String reply = ask(b, prompt);
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
                String reply = ask(b, prompt);
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
                StringBuilder s = new StringBuilder();
                s.append("== Turn ").append(ph.getTurn()).append(' ').append(ph.getPhase())
                        .append(" (active: ").append(ph.getPlayerTurn() != null ? ph.getPlayerTurn().getName() : "?").append(") ==\n");
                s.append("YOU [").append(seat).append("] life ").append(me.getLife()).append('\n');
                s.append("  hand(").append(me.getCardsIn(ZoneType.Hand).size()).append("): ").append(handList(me.getCardsIn(ZoneType.Hand))).append('\n');
                s.append("  board: ").append(perms(me)).append('\n');
                s.append("  lands: ").append(landSummary(me)).append('\n');
                s.append("  yard(").append(me.getCardsIn(ZoneType.Graveyard).size()).append("): ").append(names(me.getCardsIn(ZoneType.Graveyard), true, 30)).append('\n');
                if (opp != null) {
                    s.append("OPP [").append(opp.getName()).append("] life ").append(opp.getLife()).append('\n');
                    s.append("  board: ").append(perms(opp)).append('\n');
                    // Opp lands are public info and load-bearing: untapped mana = removal/counter risk,
                    // and utility lands (Wasteland, Karakas) are threats worth playing around.
                    s.append("  lands: ").append(landSummary(opp)).append('\n');
                    s.append("  yard(").append(opp.getCardsIn(ZoneType.Graveyard).size()).append("): ").append(names(opp.getCardsIn(ZoneType.Graveyard), true, 30)).append('\n');
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
            try {
                SpellAbility top = g.getStack().peekAbility();
                String who = top.getActivatingPlayer() != null ? top.getActivatingPlayer().getName() : "?";
                return top.getHostCard().getName() + " (" + who + ")";
            } catch (Exception e) { return "?"; }
        }
        /** "2 untapped / 4 total (Wasteland, Badlands)" — count + distinct names. */
        private static String landSummary(Player pl) {
            int untapped = 0, total = 0;
            java.util.LinkedHashSet<String> kinds = new java.util.LinkedHashSet<>();
            for (Card c : pl.getCardsIn(ZoneType.Battlefield)) {
                if (!c.isLand()) continue;
                total++;
                if (!c.isTapped()) untapped++;
                kinds.add(c.getName());
            }
            return untapped + " untapped / " + total + " total"
                    + (kinds.isEmpty() ? "" : " (" + String.join(", ", kinds) + ")");
        }
        /** Hand listing with mana cost + P/T — the model may not know newer cards (post-training-cutoff
         *  sets), and cost-at-a-glance is what sequencing decisions actually run on. */
        private static String handList(Iterable<Card> cards) {
            StringBuilder b = new StringBuilder(); int i = 0;
            for (Card c : cards) {
                if (i++ >= 40) { b.append(", ..."); break; }
                if (b.length() > 0) b.append(", ");
                b.append(c.getName());
                try {
                    String mc = costStr(c);
                    if (mc != null && !c.isLand()) b.append(' ').append(mc);
                } catch (Exception ignore) { }
                if (c.isCreature()) b.append(' ').append(c.getNetPower()).append('/').append(c.getNetToughness());
            }
            return b.length() == 0 ? "(empty)" : b.toString();
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
            String reply = ask(smart, prompt);
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
            try {
                Player defender = null;
                for (Player pl : combat.getDefendingPlayers()) { defender = pl; break; }
                if (defender == null) { noteAi("declareAttackers(no-defender)"); super.declareAttackers(attacker, combat); return; }
                CardCollection legal = new CardCollection();
                for (Card c : attacker.getCreaturesInPlay()) if (CombatUtil.canAttack(c, defender)) legal.add(c);
                if (legal.isEmpty()) return;
                if (!smartOk()) { noteAi("declareAttackers(no-budget)"); super.declareAttackers(attacker, combat); return; }
                // Numbered subset pick, same PICKS router as discard: robust to duplicate creature names
                // and substring collisions, and decideMany prepends the full game snapshot — the model sees
                // opp life/board/untapped mana when deciding attacks, not just its own creature list.
                List<String> labels = new ArrayList<>();
                for (Card c : legal) labels.add(c.getName() + " (" + c.getNetPower() + "/" + c.getNetToughness() + ")");
                p("[ROUTER] attackers: " + legal.size() + " can attack -> asking " + smart.model + "...");
                List<Integer> picks = decideMany(smart,
                        "COMBAT — declare attackers vs " + defender.getName() + " (life " + defender.getLife()
                        + "). Pick which creatures attack (attacking with 0 is legal and sometimes correct — "
                        + "weigh their untapped mana and possible blocks).", labels, 0, legal.size());
                if (picks == null) { noteAi("declareAttackers(bridge-down)"); super.declareAttackers(attacker, combat); return; }
                StringBuilder chosen = new StringBuilder();
                for (int i : picks) {
                    combat.addAttacker(legal.get(i), defender);
                    if (chosen.length() > 0) chosen.append(", ");
                    chosen.append(legal.get(i).getName());
                }
                p("[ROUTER] attackers: " + (picks.isEmpty() ? "none" : chosen.toString()));
            } catch (Exception e) {
                noteAi("declareAttackers(err " + e + ")");
                super.declareAttackers(attacker, combat);
            }
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
                String prompt = snapshot() + "\nCOMBAT — declare blockers. Incoming: " + atk + ". Your blockers: " + blk + ". "
                        + "Assign blocks (each blocker blocks one attacker). Weigh your life total vs losing blockers. "
                        + "End with EXACTLY: BLOCKS: blocker->attacker, blocker->attacker   (or BLOCKS: NONE)";
                p("[ROUTER] blockers: " + attackers.size() + " atk / " + blockers.size() + " blk -> asking " + smart.model + "...");
                String reply = ask(smart, prompt);
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
                    // Skip a targeted spell that currently has NO legal target — casting it just fizzles
                    // ("Couldn't add to stack, failed to target"), e.g. Animate Dead with an empty graveyard.
                    if (sa.usesTargeting()) {
                        try {
                            if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(me);
                            TargetRestrictions tr = sa.getTargetRestrictions();
                            if (tr != null && tr.getAllCandidates(sa, false).size() < Math.max(1, sa.getMinTargets())) continue;
                        } catch (Exception ignore) { /* can't evaluate -> offer it, prior behavior */ }
                    }
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
                    String reply = ask(b, prompt);
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
                ensureTargetsSet(chosen); // AI cast path won't target for us — set legal LLM-chosen targets now
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

        /** Route single-target spell/ability TARGETING (removal, Reanimate, Thoughtseize, Wasteland...).
         *  Since WE already chose to cast the spell and the engine reports a legal target, we always SET one
         *  (LLM picks when there's a real choice; auto-take the only candidate otherwise). We never hand it to
         *  Forge's AI targeting brain, which sometimes DECLINES a legal target and fizzles the cast
         *  ("Couldn't add to stack, failed to target"). Every candidate is pre-validated by sa.canTarget(). */
        @Override
        public boolean chooseTargetsFor(SpellAbility sa) {
            try {
                if (sa == null || !sa.usesTargeting() || sa.getMaxTargets() != 1)
                    return super.chooseTargetsFor(sa);
                for (SpellAbility sub = sa.getSubAbility(); sub != null; sub = sub.getSubAbility())
                    if (sub.usesTargeting()) return super.chooseTargetsFor(sa);
                TargetRestrictions tr = sa.getTargetRestrictions();
                if (tr == null) return super.chooseTargetsFor(sa);
                List<GameEntity> cands = tr.getAllCandidates(sa, false);
                int min = sa.getMinTargets();
                if (cands == null || cands.isEmpty()) return super.chooseTargetsFor(sa); // truly no target -> engine cancels
                int pick = 0; // default: the (only, or first) legal candidate — beats an AI decline
                if (cands.size() > 1 && smartOk()) {
                    List<String> labels = new ArrayList<>();
                    for (GameEntity ge : cands) labels.add(entityLabel(ge));
                    String q = "Choose the TARGET for " + saLabel(sa) + " (the spell/ability resolves against whatever you pick).";
                    int c = decideOne(smart, q, labels, min == 0 ? "No target (decline)" : null);
                    if (c == -2) return super.chooseTargetsFor(sa);          // bridge dead -> AI targeting
                    if (c < 0) { if (min == 0) { sa.resetTargets(); return true; } /* optional: no target */ }
                    else pick = c;                                            // required + unparsed falls to pick=0
                }
                sa.resetTargets();
                boolean added = sa.getTargets().add(cands.get(pick));
                if (!added || sa.getTargets().size() < Math.max(1, min)) return super.chooseTargetsFor(sa); // safety net
                p("[ROUTER] target: " + entityLabel(cands.get(pick)) + " <- " + saLabel(sa)
                        + (cands.size() == 1 ? " (only legal)" : ""));
                return true;
            } catch (Exception e) {
                return super.chooseTargetsFor(sa);
            }
        }

        /** Set legal, LLM-chosen targets on a spell BEFORE handing it to the AI cast path. That path
         *  (ComputerUtil.handlePlayingSpellAbility) does NOT run targeting for ordinary spells — so an
         *  untargeted spell we picked would fail hasLegalTargeting and fizzle ("Couldn't add to stack").
         *  We walk the ability chain and, for each targeting ability with no target yet, pick from the
         *  engine's own canTarget-validated candidates (LLM chooses when there's a real choice). */
        private void ensureTargetsSet(SpellAbility root) {
            for (SpellAbility sa = root; sa != null; sa = sa.getSubAbility()) {
                try {
                    if (!sa.usesTargeting()) continue;
                    if (sa.getTargets() != null && !sa.getTargets().isEmpty()) continue; // already targeted
                    if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(getPlayer());
                    TargetRestrictions tr = sa.getTargetRestrictions();
                    if (tr == null) continue;
                    List<GameEntity> cands = tr.getAllCandidates(sa, false);
                    if (cands == null || cands.isEmpty()) continue; // no legal target -> let engine cancel/handle
                    int min = Math.max(1, sa.getMinTargets());
                    int pick = 0; // first legal candidate beats leaving it untargeted (which fizzles)
                    if (cands.size() > 1 && smartOk()) {
                        List<String> labels = new ArrayList<>();
                        for (GameEntity ge : cands) labels.add(entityLabel(ge));
                        int c = decideOne(smart, "Choose the TARGET for " + saLabel(sa)
                                + " (the spell resolves against whatever you pick).", labels,
                                sa.getMinTargets() == 0 ? "No target" : null);
                        if (c == -1 && sa.getMinTargets() == 0) continue; // optional -> deliberately no target
                        if (c >= 0) pick = c;                              // -2/unparsed fall to first legal
                    }
                    sa.resetTargets();
                    sa.getTargets().add(cands.get(pick));
                    for (int i = 0; sa.getTargets().size() < min && i < cands.size(); i++) // top up if it needs >1
                        if (i != pick) sa.getTargets().add(cands.get(i));
                    p("[ROUTER] target: " + entityLabel(cands.get(pick)) + " <- " + saLabel(sa)
                            + (cands.size() == 1 ? " (only legal)" : ""));
                } catch (Exception e) { /* leave targeting to the engine for this ability */ }
            }
        }

        @Override
        public CardCollection chooseCardsToDiscardFrom(Player p, SpellAbility sa, CardCollection validCards,
                int min, int max, CardCollectionView visibleToChooser) {
            if (validCards == null || validCards.isEmpty())
                return super.chooseCardsToDiscardFrom(p, sa, validCards, min, max, visibleToChooser);
            // p != getPlayer() means WE choose what SOMEONE ELSE discards (Thoughtseize / Cabal Therapy
            // "RevealYouChoose") — one of the most load-bearing picks in the matchup. Route it, don't delegate.
            boolean stripping = p != getPlayer();
            List<Card> opts = new ArrayList<>(validCards);
            if (opts.size() <= min) return firstN(opts, min, "discard(forced)"); // whole hand forced
            try {
                if (!smartOk()) return firstN(opts, min, "discard(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(c.getName());
                String q = stripping
                        ? "You are choosing which card(s) " + p.getName() + " must DISCARD from their revealed hand"
                          + srcOf(sa) + ". Strip whatever most threatens your game plan (their removal/disruption/payoff)."
                        : "You must DISCARD from your hand" + srcOf(sa) + ". Pick which card(s) to discard "
                          + "(remember your game plan — e.g. discarding a huge creature you can later reanimate is often correct).";
                List<Integer> picks = decideMany(smart, q, labels, min, max);
                if (picks == null) return firstN(opts, min, "discard(unparsed)");
                CardCollection out = new CardCollection();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(opts.get(i)); if (log.length() > 0) log.append(", "); log.append(opts.get(i).getName()); }
                p("[ROUTER] discard" + (stripping ? "(strip " + p.getName() + ")" : "") + ": " + log);
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

        /** Route "name a card" from an explicit list (Alhammarret, ChooseFromList effects).
         *  Small list -> numbered menu; big list -> free-form name validated against it. */
        @Override
        public String chooseCardName(SpellAbility sa, List<ICardFace> faces, String message) {
            try {
                if (faces == null || faces.isEmpty() || !smartOk())
                    return aiDelegate("card-name(list:no-budget/empty)", super.chooseCardName(sa, faces, message));
                if (faces.size() <= 20) {
                    List<String> labels = new ArrayList<>();
                    for (ICardFace f : faces) labels.add(f.getName());
                    int c = decideOne(smart, "NAME A CARD" + srcOf(sa) + ": " + oneLine(message), labels, null);
                    if (c >= 0) { p("[ROUTER] card-name: " + labels.get(c) + srcOf(sa)); return labels.get(c); }
                    return aiDelegate("card-name(list:unparsed)", super.chooseCardName(sa, faces, message));
                }
                String want = askCardName(sa, message, null);
                if (want != null) {
                    for (ICardFace f : faces)
                        if (f.getName().equalsIgnoreCase(want)) { p("[ROUTER] card-name: " + f.getName() + srcOf(sa)); return f.getName(); }
                }
                return aiDelegate("card-name(list:no-match)", super.chooseCardName(sa, faces, message));
            } catch (Exception e) {
                return aiDelegate("card-name(list:err)", super.chooseCardName(sa, faces, message));
            }
        }

        /** Route "name a card" against a predicate — THE Cabal Therapy path ("name a nonland card").
         *  Free-form LLM answer, validated by the engine's own face lookup + predicate. The persistent
         *  conversation means the model remembers what it saw with an earlier Thoughtseize/Therapy. */
        @Override
        public String chooseCardName(SpellAbility sa, java.util.function.Predicate<ICardFace> cpp, String valid, String message) {
            try {
                if (!smartOk()) return aiDelegate("card-name(no-budget)", super.chooseCardName(sa, cpp, valid, message));
                String want = askCardName(sa, message, valid);
                if (want != null) {
                    ICardFace face = StaticData.instance().getCommonCards().getFaceByName(want);
                    if (face != null && (cpp == null || cpp.test(face))) {
                        p("[ROUTER] card-name: " + face.getName() + srcOf(sa));
                        return face.getName();
                    }
                }
                return aiDelegate("card-name(no-match)", super.chooseCardName(sa, cpp, valid, message));
            } catch (Exception e) {
                return aiDelegate("card-name(err)", super.chooseCardName(sa, cpp, valid, message));
            }
        }

        /** Shared free-form "CARD: <name>" ask (one retry). Returns the raw name or null. */
        private String askCardName(SpellAbility sa, String message, String valid) {
            String q = snapshot() + "\nDECISION: NAME A CARD" + srcOf(sa) + ": " + oneLine(message)
                    + (valid != null && !valid.isEmpty() ? " (must match: " + oneLine(valid) + ")" : "")
                    + ". Use everything you've seen this game — revealed hands, their deck's archetype — to name "
                    + "the card most likely there / most dangerous to your plan. "
                    + "End with EXACTLY: CARD: <exact English card name>";
            for (int attempt = 0; attempt < 2; attempt++) {
                String reply = ask(smart, attempt == 0 ? q
                        : "That was not a legal or recognizable card name here. Answer with ONE line only: CARD: <exact card name>");
                if (reply == null) return null;
                String seg = lastSegment(reply, "CARD:");
                if (seg != null) {
                    String want = seg.replace("\"", "").replace("`", "").trim();
                    if (!want.isEmpty()) return want;
                }
            }
            return null;
        }

        /** Route modal spells ("choose one —") through the numbered menu. */
        @Override
        public List<AbilitySub> chooseModeForAbility(SpellAbility sa, List<AbilitySub> possible, int min, int num, boolean allowRepeat) {
            try {
                if (possible == null || possible.isEmpty() || allowRepeat || !smartOk())
                    return aiDelegate("modes(repeat/no-budget)", super.chooseModeForAbility(sa, possible, min, num, allowRepeat));
                if (possible.size() == 1 && min >= 1) return Lists.newArrayList(possible.get(0)); // forced
                List<String> labels = new ArrayList<>();
                for (AbilitySub ab : possible) labels.add(oneLine(ab.getDescription()));
                List<Integer> picks = decideMany(smart, "MODAL SPELL — choose mode(s) for " + saLabel(sa) + ".",
                        labels, Math.max(0, min), Math.min(num, possible.size()));
                if (picks == null || picks.size() < min)
                    return aiDelegate("modes(unparsed)", super.chooseModeForAbility(sa, possible, min, num, allowRepeat));
                List<AbilitySub> out = new ArrayList<>();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(possible.get(i)); if (log.length() > 0) log.append(" + "); log.append(labels.get(i)); }
                p("[ROUTER] modes: " + log);
                return out;
            } catch (Exception e) {
                return aiDelegate("modes(err)", super.chooseModeForAbility(sa, possible, min, num, allowRepeat));
            }
        }

        // ---------------- budget + parse helpers ----------------

        // Pure budget PEEKS — no side effect. Counters are bumped in ask(), so [TALLY] counts
        // actual model calls, not budget checks (paths that check then dedupe/delegate don't inflate it).
        private boolean smartOk() { return smartCalls < SMART_BUDGET; }
        private boolean fastOk()  { return fastCalls  < FAST_BUDGET;  }
        /** Every model call funnels through here. */
        private String ask(ClaudeBridge b, String prompt) {
            if (b == smart) smartCalls++; else fastCalls++;
            return b.ask(prompt);
        }

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

        /** "{2}{B}{B}"-style cost, or null if none. getShortString() already includes braces. */
        private static String costStr(Card c) {
            try {
                if (c.getManaCost() == null) return null;
                String mc = c.getManaCost().getShortString();
                if (mc == null || mc.isEmpty() || "-1".equals(mc) || "0".equals(mc)) return null;
                return mc.startsWith("{") ? mc : "{" + mc + "}";
            } catch (Exception e) { return null; }
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
            String cost = "";
            try {
                String mc = sa.getHostCard() != null ? costStr(sa.getHostCard()) : null;
                if (mc != null) cost = " " + mc;
            } catch (Exception ignore) { }
            String desc = sa.getDescription();
            if (desc == null || desc.isEmpty()) desc = String.valueOf(sa);
            desc = desc.replaceAll("\\s+", " ").trim();
            if (desc.length() > 70) desc = desc.substring(0, 70);
            return host + cost + (desc.isEmpty() ? "" : " [" + desc + "]");
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
            // Opt-in autonomous window recording (no macOS Screen-Recording permission needed).
            String rec = System.getenv("RECORD");
            if (rec != null && !rec.trim().isEmpty() && !"0".equals(rec.trim())) {
                try { Thread.sleep(5000); } catch (InterruptedException ignored) {} // let the match window render
                WindowRecorder recorder = WindowRecorder.startFromEnv();
                if (recorder != null) Runtime.getRuntime().addShutdownHook(new Thread(recorder::stop, "recorder-stop"));
            }
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

    /**
     * Autonomous window recorder. Paints the Forge {@link FFrame} to a BufferedImage on a fixed-fps timer
     * and pipes PNG frames straight into an ffmpeg subprocess (image2pipe -> H.264 mp4). Because the Swing
     * app renders ITSELF into the image, this needs NO macOS Screen-Recording (TCC) permission and captures
     * exactly the Forge window — nothing else on screen. Opt-in via env RECORD=1; RECORD_OUT sets the output
     * path (default ~/Downloads/forge-game-<ts>.mp4), RECORD_FPS the frame rate (default 8, clamped 1..30).
     */
    static final class WindowRecorder {
        private final int fps;
        private final String outPath;
        private volatile boolean running;
        private Process ff;
        private OutputStream ffIn;
        private Thread thread;
        private int w, h;

        private WindowRecorder(int fps, String outPath) { this.fps = fps; this.outPath = outPath; }

        static WindowRecorder startFromEnv() {
            int fps = 8;
            try { String f = System.getenv("RECORD_FPS"); if (f != null) fps = Math.max(1, Math.min(30, Integer.parseInt(f.trim()))); }
            catch (Exception ignore) {}
            String out = System.getenv("RECORD_OUT");
            if (out == null || out.isEmpty())
                out = System.getProperty("user.home") + "/Downloads/forge-game-" + System.currentTimeMillis() + ".mp4";
            WindowRecorder r = new WindowRecorder(fps, out);
            return r.start() ? r : null;
        }

        private boolean start() {
            try {
                FFrame frame = waitForFrame(20000);
                if (frame == null) { System.out.println("[REC] no Forge frame appeared; recording disabled"); return false; }
                int[] wh = sizeOf(frame);
                w = wh[0]; h = wh[1];
                ProcessBuilder pb = new ProcessBuilder("ffmpeg", "-y",
                        "-f", "image2pipe", "-framerate", String.valueOf(fps), "-i", "-",
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", outPath);
                pb.redirectErrorStream(true);
                pb.redirectOutput(ProcessBuilder.Redirect.appendTo(new java.io.File(outPath + ".ffmpeg.log")));
                ff = pb.start();
                ffIn = ff.getOutputStream();
                running = true;
                thread = new Thread(this::loop, "window-recorder");
                thread.setDaemon(true);
                thread.start();
                System.out.println("[REC] recording Forge window " + w + "x" + h + " @ " + fps + "fps -> " + outPath);
                return true;
            } catch (Exception e) {
                System.out.println("[REC] failed to start: " + e);
                return false;
            }
        }

        private void loop() {
            long frameNs = 1_000_000_000L / fps;
            while (running && ff.isAlive()) {
                long t0 = System.nanoTime();
                try {
                    BufferedImage img = capture();
                    if (img != null) { ImageIO.write(img, "png", ffIn); ffIn.flush(); }
                } catch (Exception e) { break; } // broken pipe / ffmpeg gone
                long restMs = (frameNs - (System.nanoTime() - t0)) / 1_000_000L;
                if (restMs > 0) { try { Thread.sleep(restMs); } catch (InterruptedException ie) { break; } }
            }
        }

        private BufferedImage capture() throws Exception {
            final FFrame frame = FView.SINGLETON_INSTANCE.getFrame();
            if (frame == null || !frame.isShowing()) return null;
            final BufferedImage[] out = new BufferedImage[1];
            Runnable r = () -> {
                try {
                    int cw = Math.max(2, frame.getWidth() & ~1), ch = Math.max(2, frame.getHeight() & ~1);
                    BufferedImage bi = new BufferedImage(cw, ch, BufferedImage.TYPE_INT_RGB);
                    Graphics2D g = bi.createGraphics();
                    frame.printAll(g); // render the whole frame (skinned title bar + match view) into the image
                    g.dispose();
                    out[0] = bi;
                } catch (Exception ignore) {}
            };
            if (SwingUtilities.isEventDispatchThread()) r.run(); else SwingUtilities.invokeAndWait(r);
            BufferedImage bi = out[0];
            if (bi == null) return null;
            if (bi.getWidth() != w || bi.getHeight() != h) { // window resized -> scale to the fixed encode size
                BufferedImage fixed = new BufferedImage(w, h, BufferedImage.TYPE_INT_RGB);
                Graphics2D g = fixed.createGraphics();
                g.drawImage(bi, 0, 0, w, h, null);
                g.dispose();
                return fixed;
            }
            return bi;
        }

        void stop() {
            if (!running && ff == null) return;
            running = false;
            try { if (thread != null) thread.join(3000); } catch (Exception ignore) {}
            try { if (ffIn != null) { ffIn.flush(); ffIn.close(); } } catch (Exception ignore) {} // EOF -> ffmpeg finalizes moov
            try { if (ff != null) ff.waitFor(); } catch (Exception ignore) {}
            System.out.println("[REC] recording finalized -> " + outPath);
        }

        private static int[] sizeOf(FFrame frame) throws Exception {
            final int[] wh = new int[2];
            Runnable r = () -> { wh[0] = Math.max(2, frame.getWidth() & ~1); wh[1] = Math.max(2, frame.getHeight() & ~1); };
            if (SwingUtilities.isEventDispatchThread()) r.run(); else SwingUtilities.invokeAndWait(r);
            return wh;
        }

        private static FFrame waitForFrame(long timeoutMs) {
            long deadline = System.currentTimeMillis() + timeoutMs;
            while (System.currentTimeMillis() < deadline) {
                try {
                    FFrame f = FView.SINGLETON_INSTANCE.getFrame();
                    if (f != null && f.isShowing() && f.getWidth() > 0) return f;
                } catch (Exception ignore) {}
                try { Thread.sleep(200); } catch (InterruptedException ie) { return null; }
            }
            return null;
        }
    }
}
