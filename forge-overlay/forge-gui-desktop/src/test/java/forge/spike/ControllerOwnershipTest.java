package forge.spike;

import java.io.IOException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.stream.Stream;

import org.testng.Assert;
import org.testng.annotations.Test;

import forge.GuiDesktop;
import forge.ai.AiProps;
import forge.ai.PlayerControllerAi;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.player.PlayerController;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

/**
 * Controller ownership ledger (harness gate G7, first stage).
 *
 * docs/harness/controller-ownership.tsv classifies every public overridable PlayerController hook
 * by WHO decides it on the LLM seat: the model, a deterministic fallback, Forge's AI (observed or
 * silent), or the base class. This test pins that ledger to the compiled controller by reflection:
 *
 *  - the set of hooks and their resolved implementation class must match the ledger EXACTLY, in
 *    both directions, so any new override, removed override, or engine-side hook change forces a
 *    reviewed ledger edit instead of silently changing who plays the game;
 *  - every ledger row carries a valid kind / ownership / reach, and the ownership claim is
 *    consistent with the implementation class it resolves to (a hook that resolves to
 *    PlayerControllerAi cannot be labelled "model");
 *  - the AI cheat-shuffle hook the LLM seat inherits stays inert on both launch paths.
 *
 * What would make this fail: overriding a hook in PlayerControllerLLM without updating the ledger;
 * deleting an override; an engine upgrade that adds or removes a hook; enabling AI cheats in the
 * preferences or in an AI profile. Passing does NOT mean the model owns every strategic decision --
 * the ledger's 'ai-inherited' rows are the open G7 work and are reported, not hidden.
 *
 * The ledger path comes from -Dharness.ownership.tsv=... or HARNESS_OWNERSHIP_TSV. It is a hard
 * failure (not a skip) when unset: a gate that can quietly not run is not a gate.
 */
public class ControllerOwnershipTest {
    static final Set<String> KINDS = new HashSet<>(Arrays.asList(
            "strategic", "payment", "information", "mechanics", "ui", "match", "exotic"));
    static final Set<String> OWNERSHIP = new HashSet<>(Arrays.asList(
            "model", "model+ai-fallback", "ai-observed", "ai-inherited", "base-inherited", "deterministic", "wrapper"));
    static final Set<String> REACH = new HashSet<>(Arrays.asList("core", "rare", "none"));

    static String shortImpl(Class<?> c) {
        if (c == LlmSpikeGui.PlayerControllerLLM.class) return "LLM";
        if (c == PlayerControllerAi.class) return "AI";
        if (c == PlayerController.class) return "BASE";
        return c.getName();
    }

    /** Same construction as docs/harness/controller-inventory.tsv: every public, non-static,
     *  non-final hook of PlayerController, resolved to its declaring class on the LLM controller. */
    static Map<String, String> liveInventory() {
        Map<String, String> out = new TreeMap<>();
        for (Method m : PlayerController.class.getMethods()) {
            int mod = m.getModifiers();
            if (Modifier.isStatic(mod) || Modifier.isFinal(mod)) continue;
            if (m.getDeclaringClass() == Object.class) continue;
            String sig = m.getName() + Arrays.toString(m.getParameterTypes());
            try {
                Method impl = LlmSpikeGui.PlayerControllerLLM.class.getMethod(m.getName(), m.getParameterTypes());
                out.put(sig, shortImpl(impl.getDeclaringClass()));
            } catch (NoSuchMethodException e) {
                out.put(sig, "UNRESOLVED");
            }
        }
        return out;
    }

    static Path ledgerPath() {
        String p = System.getProperty("harness.ownership.tsv");
        if (p == null || p.isEmpty()) p = System.getenv("HARNESS_OWNERSHIP_TSV");
        Assert.assertTrue(p != null && !p.isEmpty(),
                "ledger path not configured: pass -Dharness.ownership.tsv=<repo>/docs/harness/controller-ownership.tsv "
                + "(or HARNESS_OWNERSHIP_TSV). This gate must not be able to run against nothing.");
        Path path = Paths.get(p);
        Assert.assertTrue(Files.isRegularFile(path), "ledger not found: " + path);
        return path;
    }

    static List<String[]> readLedger(Path path) throws IOException {
        List<String[]> rows = new ArrayList<>();
        List<String> lines = Files.readAllLines(path, StandardCharsets.UTF_8);
        Assert.assertEquals(lines.get(0), "signature\timpl\tkind\townership\tlegacy_reach\tnotes", "ledger header");
        for (String line : lines.subList(1, lines.size())) {
            if (line.trim().isEmpty()) continue;
            String[] f = line.split("\t", -1);
            Assert.assertEquals(f.length, 6, "ledger row must have 6 columns: " + line);
            rows.add(f);
        }
        return rows;
    }

    @Test
    public void ledgerMatchesCompiledControllerExactly() throws IOException {
        Map<String, String> live = liveInventory();
        Assert.assertFalse(live.containsValue("UNRESOLVED"), "hooks that failed to resolve: " + live);
        Map<String, String> ledger = new TreeMap<>();
        for (String[] f : readLedger(ledgerPath())) {
            Assert.assertNull(ledger.put(f[0], f[1]), "duplicate ledger row: " + f[0]);
        }
        List<String> problems = new ArrayList<>();
        for (Map.Entry<String, String> e : live.entrySet()) {
            String l = ledger.get(e.getKey());
            if (l == null) problems.add("MISSING FROM LEDGER: " + e.getKey() + " -> " + e.getValue());
            else if (!l.equals(e.getValue())) problems.add("IMPL CHANGED: " + e.getKey() + " ledger=" + l + " live=" + e.getValue());
        }
        for (String sig : ledger.keySet()) {
            if (!live.containsKey(sig)) problems.add("STALE LEDGER ROW (no such hook): " + sig);
        }
        Assert.assertTrue(problems.isEmpty(),
                "ownership ledger out of date -- edit docs/harness/controller-ownership.tsv deliberately:\n  "
                + String.join("\n  ", problems));
        Assert.assertEquals(live.size(), 130, "hook count drifted from the inventory baseline; re-audit");
    }

    @Test
    public void ledgerRowsAreWellFormedAndConsistent() throws IOException {
        List<String> problems = new ArrayList<>();
        int aiStrategicCore = 0;
        for (String[] f : readLedger(ledgerPath())) {
            String sig = f[0], impl = f[1], kind = f[2], own = f[3], reach = f[4], notes = f[5];
            if (!KINDS.contains(kind)) problems.add("bad kind '" + kind + "': " + sig);
            if (!OWNERSHIP.contains(own)) problems.add("bad ownership '" + own + "': " + sig);
            if (!REACH.contains(reach)) problems.add("bad reach '" + reach + "': " + sig);
            if (notes.trim().isEmpty()) problems.add("empty notes: " + sig);
            boolean ok;
            switch (impl) {
                case "AI":   ok = own.equals("ai-inherited") || own.equals("wrapper"); break;
                case "BASE": ok = own.equals("base-inherited") || own.equals("wrapper"); break;
                case "LLM":  ok = Stream.of("model", "model+ai-fallback", "ai-observed", "deterministic").anyMatch(own::equals); break;
                default:     ok = false;
            }
            if (!ok) problems.add("ownership '" + own + "' impossible for impl " + impl + ": " + sig);
            if (impl.equals("AI") && own.equals("ai-inherited") && reach.equals("core")
                    && (kind.equals("strategic") || kind.equals("payment"))) aiStrategicCore++;
        }
        Assert.assertTrue(problems.isEmpty(), String.join("\n  ", problems));
        // Reported, not hidden: this number is the open G7 backlog. It may only go DOWN; raising it
        // means a strategic decision moved from the model to Forge AI and needs an explicit review.
        System.out.println("[OWNERSHIP] silent Forge-AI strategic/payment hooks reachable in Legacy play: " + aiStrategicCore);
        Assert.assertTrue(aiStrategicCore <= 3,
                "silent AI strategic hooks grew to " + aiStrategicCore + " (baseline 3) -- a decision left the model");
    }

    @Test
    public void inheritedCheatShuffleStaysInert() {
        // GameType/GameRules class-init needs the model (localization, static card data), so
        // initialize first; LlmSpikeTest does the same in its @BeforeClass.
        // Probe via GuiBase, never via FModel.getPreferences(): touching preferences before the GUI
        // interface is set fails ForgeConstants' class-init and poisons every later test in the JVM.
        if (GuiBase.getInterface() == null) {
            GuiBase.setInterface(new GuiDesktop());
            FModel.initialize(null, preferences -> {
                preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
                preferences.setPref(FPref.UI_LANGUAGE, "en-US");
                return null;
            });
        }
        // Headless path: LlmSpikeHeadless builds GameRules directly; the engine default is off.
        Assert.assertFalse(new GameRules(GameType.Constructed).isAllowCheatShuffle(),
                "engine default allows AI cheat shuffle");
        // AI profile default for the property that gates AiController.cheatShuffle.
        Assert.assertEquals(AiProps.CHEAT_WITH_MANA_ON_SHUFFLE.getDefault(), "false");
        // GUI path: HostedMatch copies UI_ENABLE_AI_CHEATS into the rules. Pin the preference.
        Assert.assertFalse(FModel.getPreferences().getPrefBoolean(FPref.UI_ENABLE_AI_CHEATS),
                "UI_ENABLE_AI_CHEATS is on: HostedMatch would let the LLM seat's inherited cheatShuffle rig its library");
        // No shipped AI profile turns the property on either (profiles live next to the engine assets;
        // tests run with cwd forge-gui-desktop, so ../forge-gui/res/ai is the pinned checkout's copy).
        Path profiles = Paths.get("..", "forge-gui", "res", "ai");
        Assert.assertTrue(Files.isDirectory(profiles), "AI profile dir not found from cwd: " + profiles.toAbsolutePath());
        try (Stream<Path> files = Files.list(profiles)) {
            for (Path p : (Iterable<Path>) files::iterator) {
                if (!p.toString().endsWith(".ai")) continue;
                for (String line : Files.readAllLines(p, StandardCharsets.UTF_8)) {
                    String t = line.trim().replace(" ", "");
                    Assert.assertFalse(t.equalsIgnoreCase("CheatWithManaOnShuffle=true"), "AI profile enables cheat shuffle: " + p);
                }
            }
        } catch (IOException e) {
            Assert.fail("could not read AI profiles: " + e);
        }
    }
}
