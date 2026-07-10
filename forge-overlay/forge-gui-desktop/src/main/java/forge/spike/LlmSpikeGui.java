package forge.spike;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

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
import forge.deck.DeckgenUtil;
import forge.game.Game;
import forge.game.GameType;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardCollectionView;
import forge.game.card.CardLists;
import forge.game.card.CardPredicates;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.phase.PhaseHandler;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.gamemodes.match.HostedMatch;
import forge.gui.GuiBase;
import forge.util.ITriggerEvent;

/**
 * SPIKE (throwaway): launch the real Forge desktop window and WATCH our custom
 * PlayerController play a game vs the built-in AI, in spectator mode.
 *
 * Same injection seam proven headless in LlmSpikeTest, now rendered in the Swing GUI:
 * both seats are non-human, so HostedMatch (humanCount==0) auto-opens a spectator view.
 *
 * Run with working dir = forge-gui-desktop/ so assets resolve to ../forge-gui/res.
 */
public final class LlmSpikeGui {
    private LlmSpikeGui() {}

    static final AtomicInteger cbMulligan  = new AtomicInteger();
    static final AtomicInteger cbAttackers = new AtomicInteger();
    static final AtomicInteger cbBlockers  = new AtomicInteger();

    /** Our agent's controller: extend the AI, announce each decision, delegate to super. */
    static class PlayerControllerLLM extends PlayerControllerAi {
        private static final int MAX_PLAY_DECISIONS_PER_TURN = 25; // hard cap so Opus can never hang a turn
        private static int lastPlayTurn = -1;
        private static int playDecisionsThisTurn = 0;
        private static String lastAskSig = "";      // dedupe: last spot we asked Opus about
        private static boolean lastWasPass = false; // ...and whether it passed there

        PlayerControllerLLM(Game game, Player p, LobbyPlayer lp) { super(game, p, lp); }

        @Override
        public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
            cbMulligan.incrementAndGet();
            CardCollectionView hand = player.getCardsIn(ZoneType.Hand);
            int lands = CardLists.filter(hand, CardPredicates.LANDS).size();
            int size = hand.size();

            // TIER 1 (heuristic): a 3-4 land opener is a routine snap-keep. Decide locally, no LLM.
            if (lands >= 3 && lands <= 4) {
                System.out.println("[ROUTER] mulligan: heuristic KEEP (" + lands + "/" + size + " lands) - no LLM call");
                return true;
            }

            // TIER 3 (reasoner): extreme hand (flood/screw) is genuinely ambiguous -> ask Opus.
            StringBuilder names = new StringBuilder();
            for (Card c : hand) { if (names.length() > 0) names.append(", "); names.append(c.getName()); }
            String prompt = "You are playing Magic: The Gathering (Constructed, 60-card deck). "
                    + "Opening hand of " + size + " cards with " + lands + " lands: " + names + ". "
                    + "Under the London mulligan you would put " + cardsToReturn + " card(s) on the bottom if you keep. "
                    + "Should you keep this hand? Reply with EXACTLY one word: KEEP or MULLIGAN.";
            System.out.println("[ROUTER] mulligan: heuristic abstains (" + lands + "/" + size + " lands) -> asking Opus...");
            String reply = ClaudeBridge.ask(prompt);

            if (reply == null) {
                System.out.println("[ROUTER] mulligan: Opus unavailable -> fallback to Forge AI heuristic");
                return super.mulliganKeepHand(firstPlayer, cardsToReturn);
            }
            String r = reply.trim().toUpperCase();
            int keepAt = r.indexOf("KEEP");
            int mullAt = r.indexOf("MULLIGAN");
            boolean keep = keepAt >= 0 && (mullAt < 0 || keepAt < mullAt);
            System.out.println("[ROUTER] mulligan: Opus decided -> " + (keep ? "KEEP" : "MULLIGAN")
                    + "  (raw: \"" + reply.trim().replaceAll("\\s+", " ") + "\")");
            return keep;
        }
        @Override
        public SpellAbility getAbilityToPlay(Card hostCard, List<SpellAbility> abilities, ITriggerEvent triggerEvent) {
            return super.getAbilityToPlay(hostCard, abilities, triggerEvent);
        }
        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            cbAttackers.incrementAndGet();
            // Resolve the defending opponent.
            Player defender = null;
            for (Player p : combat.getDefendingPlayers()) { defender = p; break; }
            if (defender == null) { super.declareAttackers(attacker, combat); return; }

            // Only engine-legal attackers are ever offered or declared.
            CardCollection legal = new CardCollection();
            for (Card c : attacker.getCreaturesInPlay()) {
                if (CombatUtil.canAttack(c, defender)) legal.add(c);
            }
            if (legal.isEmpty()) { return; } // nothing can attack -> no decision to make

            StringBuilder mine = new StringBuilder();
            for (Card c : legal) {
                if (mine.length() > 0) mine.append(", ");
                mine.append(c.getName()).append(" (").append(c.getNetPower()).append('/').append(c.getNetToughness()).append(')');
            }
            StringBuilder theirs = new StringBuilder();
            for (Card c : defender.getCreaturesInPlay()) {
                if (theirs.length() > 0) theirs.append(", ");
                theirs.append(c.getName()).append(" (").append(c.getNetPower()).append('/').append(c.getNetToughness()).append(')');
                if (c.isTapped()) theirs.append("[tapped]");
            }
            String prompt = "Magic: The Gathering combat, declare attackers. Your life " + attacker.getLife()
                    + ", opponent life " + defender.getLife() + ". "
                    + "Your creatures that can attack: " + mine + ". "
                    + "Opponent creatures (potential blockers): " + (theirs.length() == 0 ? "none" : theirs) + ". "
                    + "Decide which creatures should attack. You may reason in one short sentence, "
                    + "but you MUST end your reply with a final line in EXACTLY this format:\n"
                    + "ATTACKERS: <comma-separated attacker names, or NONE>\n"
                    + "Put nothing after that line.";
            System.out.println("[ROUTER] attackers: " + legal.size() + " can attack -> asking Opus...");
            String reply = ClaudeBridge.ask(prompt);
            if (reply == null) {
                System.out.println("[ROUTER] attackers: Opus unavailable -> Forge AI");
                super.declareAttackers(attacker, combat);
                return;
            }
            // Parse ONLY the structured decision line so the model's free-form reasoning
            // (which often names creatures it decided NOT to attack with) can't leak in.
            String decision = null;
            for (String line : reply.split("\\R")) {
                int idx = line.toUpperCase().indexOf("ATTACKERS:");
                if (idx >= 0) decision = line.substring(idx + "ATTACKERS:".length());
            }
            if (decision == null) decision = ""; // no structured line -> treat as no attack
            String d = decision.toUpperCase();
            int declared = 0;
            StringBuilder chosen = new StringBuilder();
            if (!d.contains("NONE")) {
                for (Card c : legal) {
                    if (d.contains(c.getName().toUpperCase())) {
                        combat.addAttacker(c, defender);
                        declared++;
                        if (chosen.length() > 0) chosen.append(", ");
                        chosen.append(c.getName());
                    }
                }
            }
            System.out.println("[ROUTER] attackers: Opus attacks with " + declared
                    + (declared == 0 ? " (none)" : " -> " + chosen)
                    + "  (decision line: \"" + decision.trim() + "\")");
        }
        @Override
        public void declareBlockers(Player defender, Combat combat) {
            cbBlockers.incrementAndGet();
            try {
                CardCollection attackers = combat.getAttackers();
                if (attackers == null || attackers.isEmpty()) { return; }
                // Only creatures that can legally block at least one attacker.
                CardCollection blockers = new CardCollection();
                for (Card c : defender.getCreaturesInPlay()) {
                    for (Card atk : attackers) {
                        if (CombatUtil.canBlock(atk, c, combat)) { blockers.add(c); break; }
                    }
                }
                if (blockers.isEmpty()) { return; } // can't block anything

                StringBuilder atkStr = new StringBuilder();
                for (Card a : attackers) {
                    if (atkStr.length() > 0) atkStr.append(", ");
                    atkStr.append(a.getName()).append(" (").append(a.getNetPower()).append('/').append(a.getNetToughness()).append(')');
                }
                StringBuilder blkStr = new StringBuilder();
                for (Card b : blockers) {
                    if (blkStr.length() > 0) blkStr.append(", ");
                    blkStr.append(b.getName()).append(" (").append(b.getNetPower()).append('/').append(b.getNetToughness()).append(')');
                }
                String prompt = "Magic: The Gathering, declare blockers. Your life " + defender.getLife()
                        + ". Incoming attackers: " + atkStr + ". Your creatures that can block: " + blkStr + ". "
                        + "Assign blocks (each blocker blocks at most one attacker). Reason briefly, then end with a "
                        + "final line EXACTLY like:\nBLOCKS: blocker->attacker, blocker->attacker   (or BLOCKS: NONE)";
                System.out.println("[ROUTER] blockers: " + attackers.size() + " attacker(s), " + blockers.size() + " can block -> asking Opus...");
                String reply = ClaudeBridge.ask(prompt);
                if (reply == null) {
                    System.out.println("[ROUTER] blockers: Opus unavailable -> Forge AI");
                    super.declareBlockers(defender, combat);
                    return;
                }
                String line = lastLineWith(reply, "BLOCKS:");
                if (line == null || line.toUpperCase().contains("NONE")) {
                    System.out.println("[ROUTER] blockers: Opus blocks NONE");
                    return;
                }
                int assigned = 0;
                StringBuilder made = new StringBuilder();
                for (String pair : line.split(",")) {
                    int arrow = pair.indexOf("->");
                    if (arrow < 0) continue;
                    Card b = matchByName(blockers, pair.substring(0, arrow).toUpperCase());
                    Card a = matchByName(attackers, pair.substring(arrow + 2).toUpperCase());
                    if (a != null && b != null && CombatUtil.canBlock(a, b, combat)) {
                        combat.addBlocker(a, b);
                        assigned++;
                        if (made.length() > 0) made.append(", ");
                        made.append(b.getName()).append("->").append(a.getName());
                        blockers.remove(b); // don't double-assign the same blocker
                    }
                }
                System.out.println("[ROUTER] blockers: Opus assigns " + assigned + (assigned == 0 ? " (none)" : " -> " + made));
            } catch (Exception e) {
                System.out.println("[ROUTER] blockers: error " + e + " -> Forge AI");
                super.declareBlockers(defender, combat);
            }
        }

        /** What to play on priority. The AI's own play loop is here (not getAbilityToPlay).
         *  We offer Opus ONLY castable options + a land + PASS, with a per-turn cap so it can't hang. */
        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            try {
                Player me = getPlayer();
                PhaseHandler phh = me.getGame().getPhaseHandler();
                // TIER-1 gate: only spend Opus on real develop decisions (our own main phases).
                // Every other priority (combat steps, opponent's turn, upkeep...) -> AI, which just passes.
                if (!(phh.isPlayerTurn(me) && (phh.is(PhaseType.MAIN1) || phh.is(PhaseType.MAIN2)))) {
                    return super.chooseSpellAbilityToPlay();
                }
                int turn = phh.getTurn();
                if (turn != lastPlayTurn) { lastPlayTurn = turn; playDecisionsThisTurn = 0; }
                if (playDecisionsThisTurn >= MAX_PLAY_DECISIONS_PER_TURN) {
                    return super.chooseSpellAbilityToPlay(); // safety valve -> AI finishes the turn
                }

                // Castable non-land, non-mana abilities the engine+mana actually allow right now.
                List<SpellAbility> options = new ArrayList<>();
                for (SpellAbility sa : ComputerUtilAbility.getSpellAbilities(
                        ComputerUtilAbility.getAvailableCards(me.getGame(), me), me)) {
                    if (sa.isLandAbility() || sa.isManaAbility()) continue;
                    if (!sa.canPlay()) continue;
                    if (!ComputerUtilMana.canPayManaCost(sa, me, 0, false)) continue;
                    options.add(sa);
                    if (options.size() >= 12) break;
                }
                // One land option (only one land can be played per turn anyway).
                CardCollection lands = ComputerUtilAbility.getAvailableLandsToPlay(me.getGame(), me);
                SpellAbility landSa = null;
                Card landCard = null;
                if (lands != null && !lands.isEmpty()) {
                    landCard = lands.get(0);
                    for (SpellAbility sa : landCard.getAllPossibleAbilities(me, true)) {
                        if (sa.isLandAbility()) { landSa = sa; break; }
                    }
                }
                if (options.isEmpty() && landSa == null) {
                    return super.chooseSpellAbilityToPlay(); // nothing meaningful -> let AI pass/handle
                }

                StringBuilder menu = new StringBuilder();
                int idx = 1;
                for (SpellAbility sa : options) {
                    menu.append("  ").append(idx++).append(") CAST ").append(saLabel(sa)).append('\n');
                }
                int landIdx = -1;
                if (landSa != null) { landIdx = idx; menu.append("  ").append(idx++).append(") PLAY LAND ").append(landCard.getName()).append('\n'); }
                int passIdx = idx;
                menu.append("  ").append(idx).append(") PASS (do nothing, hold priority)\n");

                // Dedupe: if this is the exact same spot where Opus just passed, pass again without re-asking.
                String sig = turn + "|" + (phh.is(PhaseType.MAIN1) ? "M1" : "M2") + "|" + menu;
                if (sig.equals(lastAskSig) && lastWasPass) {
                    return java.util.Collections.emptyList();
                }
                lastAskSig = sig;

                String prompt = "Magic: The Gathering, you have priority (" + me.getGame().getPhaseHandler().getPhase()
                        + "). Your life " + me.getLife() + ", opponent life " + firstOppLife(me) + ". "
                        + "Your hand: " + handStr(me) + ". Your battlefield creatures: " + creatureStr(me) + ". "
                        + "Choose ONE action:\n" + menu
                        + "Reason briefly, then end with a final line EXACTLY: CHOICE: <number>";
                playDecisionsThisTurn++;
                System.out.println("[ROUTER] play: " + options.size() + " castable + " + (landSa != null ? "1 land" : "0 land") + " -> asking Opus...");
                String reply = ClaudeBridge.ask(prompt);
                if (reply == null) { System.out.println("[ROUTER] play: Opus unavailable -> Forge AI"); return super.chooseSpellAbilityToPlay(); }

                int choice = parseChoice(reply);
                if (choice == passIdx || choice < 1 || choice > passIdx) {
                    lastWasPass = true;
                    System.out.println("[ROUTER] play: Opus PASSES (choice " + choice + ")");
                    return java.util.Collections.emptyList();
                }
                lastWasPass = false;
                if (landIdx > 0 && choice == landIdx) {
                    System.out.println("[ROUTER] play: Opus plays land " + landCard.getName());
                    return Lists.newArrayList(landSa);
                }
                SpellAbility chosen = options.get(choice - 1);
                System.out.println("[ROUTER] play: Opus casts " + saLabel(chosen));
                return Lists.newArrayList(chosen);
            } catch (Exception e) {
                System.out.println("[ROUTER] play: error " + e + " -> Forge AI");
                return super.chooseSpellAbilityToPlay();
            }
        }

        // ---- serialization + parse helpers ----
        private static String saLabel(SpellAbility sa) {
            String host = sa.getHostCard() != null ? sa.getHostCard().getName() : "?";
            String desc = sa.getDescription();
            if (desc == null || desc.isEmpty()) desc = String.valueOf(sa);
            desc = desc.replaceAll("\\s+", " ").trim();
            if (desc.length() > 80) desc = desc.substring(0, 80);
            return host + " [" + desc + "]";
        }
        private static String handStr(Player p) {
            StringBuilder b = new StringBuilder();
            for (Card c : p.getCardsIn(ZoneType.Hand)) { if (b.length() > 0) b.append(", "); b.append(c.getName()); }
            return b.length() == 0 ? "(empty)" : b.toString();
        }
        private static String creatureStr(Player p) {
            StringBuilder b = new StringBuilder();
            for (Card c : p.getCreaturesInPlay()) {
                if (b.length() > 0) b.append(", ");
                b.append(c.getName()).append(" (").append(c.getNetPower()).append('/').append(c.getNetToughness()).append(')');
            }
            return b.length() == 0 ? "(none)" : b.toString();
        }
        private static int firstOppLife(Player p) {
            for (Player o : p.getOpponents()) return o.getLife();
            return 0;
        }
        private static String lastLineWith(String reply, String key) {
            String found = null;
            for (String ln : reply.split("\\R")) {
                int i = ln.toUpperCase().indexOf(key.toUpperCase());
                if (i >= 0) found = ln.substring(i + key.length());
            }
            return found;
        }
        private static int parseChoice(String reply) {
            String seg = lastLineWith(reply, "CHOICE:");
            if (seg == null) return -1;
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("\\d+").matcher(seg);
            return m.find() ? Integer.parseInt(m.group()) : -1;
        }
        private static Card matchByName(Iterable<Card> cards, String upperSegment) {
            for (Card c : cards) { if (upperSegment.contains(c.getName().toUpperCase())) return c; }
            return null;
        }
    }

    /** LobbyPlayer whose in-game Player uses OUR controller. */
    static class LobbyPlayerLLM extends LobbyPlayerAi {
        LobbyPlayerLLM(String name) { super(name, null); }
        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player p = new Player(getName(), game, id);
            p.setFirstController(new PlayerControllerLLM(game, p, this));
            return p;
        }
    }

    /**
     * PERSISTENT bridge to Claude via the CLI's stream-json mode. One long-lived
     * `claude -p --model opus --input-format stream-json --output-format stream-json` process
     * is kept warm and reused for every decision, so the ~5s CLI/session cold-start is paid
     * ONCE instead of per call (warm turns are ~2-3s vs 6-28s for a fresh `claude -p`).
     * Blocking on the game thread is safe: Forge decision callbacks are synchronous.
     * One conversation is reused, so the model also accumulates memory of the game.
     */
    static final class ClaudeBridge {
        private static final String MODEL = System.getenv().getOrDefault("CLAUDE_MODEL", "opus");
        private static final String BIN   = System.getenv().getOrDefault("CLAUDE_BIN", "claude");
        private static Process proc;
        private static BufferedReader out;
        private static BufferedWriter in;

        private static void ensureStarted() throws Exception {
            if (proc != null && proc.isAlive()) return;
            System.out.println("[CLAUDE] starting persistent " + MODEL + " session (warm reuse)...");
            ProcessBuilder pb = new ProcessBuilder(BIN, "-p", "--model", MODEL,
                    "--input-format", "stream-json", "--output-format", "stream-json", "--verbose");
            pb.redirectErrorStream(false); // stdout must stay clean JSON
            proc = pb.start();
            out = new BufferedReader(new InputStreamReader(proc.getInputStream(), StandardCharsets.UTF_8));
            in  = new BufferedWriter(new OutputStreamWriter(proc.getOutputStream(), StandardCharsets.UTF_8));
            // Drain stderr so its buffer can't fill and stall the process.
            Thread errDrain = new Thread(() -> {
                try (BufferedReader er = new BufferedReader(
                        new InputStreamReader(proc.getErrorStream(), StandardCharsets.UTF_8))) {
                    while (er.readLine() != null) { /* discard */ }
                } catch (Exception ignore) { /* process ended */ }
            }, "claude-stderr");
            errDrain.setDaemon(true);
            errDrain.start();
        }

        static synchronized String ask(String prompt) {
            long t0 = System.currentTimeMillis();
            try {
                ensureStarted();
                in.write("{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\""
                        + jsonEscape(prompt) + "\"}]}}");
                in.write("\n");
                in.flush();

                long deadline = System.currentTimeMillis() + 90000;
                while (System.currentTimeMillis() < deadline) {
                    if (!out.ready()) {
                        if (!proc.isAlive()) { System.out.println("[CLAUDE] process exited"); reset(); return null; }
                        Thread.sleep(20);
                        continue;
                    }
                    String line = out.readLine();
                    if (line == null) { reset(); return null; }
                    if (line.contains("\"type\":\"result\"")) {
                        String r = extractJsonString(line, "\"result\":\"");
                        System.out.println("[CLAUDE] " + MODEL + " replied in " + (System.currentTimeMillis() - t0) + " ms");
                        return r;
                    }
                    // else: system/init/assistant/rate_limit event -> skip
                }
                System.out.println("[CLAUDE] timed out after 90s");
                return null;
            } catch (Exception e) {
                System.out.println("[CLAUDE] error: " + e);
                reset();
                return null;
            }
        }

        private static void reset() {
            try { if (proc != null) proc.destroyForcibly(); } catch (Exception ignore) { }
            proc = null; out = null; in = null;
        }

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
                    default:
                        if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
                        else b.append(c);
                }
            }
            return b.toString();
        }

        /** Extract + unescape the JSON string value following key (e.g. "\"result\":\"") in line. */
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
                        case 'u':
                            if (i + 4 <= line.length()) {
                                b.append((char) Integer.parseInt(line.substring(i, i + 4), 16));
                                i += 4;
                            }
                            break;
                        default: b.append(n);
                    }
                } else if (c == '"') {
                    break;
                } else {
                    b.append(c);
                }
            }
            return b.toString();
        }
    }

    public static void main(String[] args) {
        // Boot the desktop framework (this also does FModel.initialize with the splash bar).
        GuiBase.setInterface(new GuiDesktop());
        Singletons.initializeOnce(true);
        Singletons.getControl().initialize();

        // Give the home screen / view a moment to come up on the EDT, then start the match.
        new Thread(() -> {
            try { Thread.sleep(3500); } catch (InterruptedException ignored) {}
            SwingUtilities.invokeLater(LlmSpikeGui::startSpectatorMatch);
        }, "llm-spike-launcher").start();
    }

    private static void startSpectatorMatch() {
        System.out.println("[LLM-SEAM] building LLM-Agent vs Forge-AI spectator match...");
        Deck deckLLM = DeckgenUtil.getRandomColorDeck(true);
        Deck deckAI  = DeckgenUtil.getRandomColorDeck(true);

        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(new RegisteredPlayer(deckLLM).setPlayer(new LobbyPlayerLLM("LLM-Agent")));
        players.add(new RegisteredPlayer(deckAI).setPlayer(new LobbyPlayerAi("Forge-AI", null)));

        HostedMatch hosted = GuiBase.getInterface().hostMatch();
        // 4-arg overload => guis==null; humanCount stays 0 => HostedMatch opens a spectator view.
        hosted.startMatch(GameType.Constructed, EnumSet.of(GameType.Constructed), players, null);
        System.out.println("[LLM-SEAM] match started; watch the Forge window.");
    }
}
