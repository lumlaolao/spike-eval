package forge.harnessenv.toys;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import forge.ai.ComputerUtilAbility;
import forge.ai.ComputerUtilCost;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.game.Game;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.harnessenv.ModelEndpoint;

/**
 * The toy candidates' common seat, written from eval/harness_env/SPEC.md alone (no class of the real harness): a
 * controller that inherits Forge's AI and routes some decisions to the model over the SPEC's wire. Each decision
 * is one request with a numbered list and the `answer` function; the call's `option` is the answer. What a toy does
 * with a reply that calls nothing is the toy's flaw: {@link #parseText} reads a number out of the prose.
 */
abstract class ToySeat extends PlayerControllerAi {
    private static final HttpClient HTTP = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
    private static final Pattern NUMBER = Pattern.compile("\\b(\\d+)\\b");
    private final ModelEndpoint model;
    /** The text parser's flaw: a reply with no call is read for a number. */
    final boolean parseText;

    ToySeat(Game g, Player p, ModelEndpoint model, boolean parseText) {
        super(g, p, new LobbyPlayerAi(p.getName(), null));
        this.model = model;
        this.parseText = parseText;
    }

    /** The 0-based option the model picks among {@code labels}, or -1 when it picks none. */
    int ask(String question, List<String> labels) {
        StringBuilder b = new StringBuilder(question).append("\n");
        for (int i = 0; i < labels.size(); i++) b.append("  ").append(i + 1).append(") ").append(labels.get(i)).append('\n');
        b.append("Answer with the answer function: option = the number.");
        JsonObject req = new JsonObject();
        req.addProperty("model", model.model);
        JsonArray msgs = new JsonArray();
        JsonObject user = new JsonObject();
        user.addProperty("role", "user");
        user.addProperty("content", b.toString());
        msgs.add(user);
        req.add("messages", msgs);
        JsonObject props = new JsonObject();
        JsonObject opt = new JsonObject();
        opt.addProperty("type", "integer");
        props.add("option", opt);
        JsonObject params = new JsonObject();
        params.addProperty("type", "object");
        params.add("properties", props);
        JsonObject fn = new JsonObject();
        fn.addProperty("name", "answer");
        fn.add("parameters", params);
        JsonObject tool = new JsonObject();
        tool.addProperty("type", "function");
        tool.add("function", fn);
        JsonArray tools = new JsonArray();
        tools.add(tool);
        req.add("tools", tools);
        req.addProperty("tool_choice", "auto");
        try {
            HttpResponse<String> resp = HTTP.send(HttpRequest.newBuilder(URI.create(model.baseUrl + "/chat/completions"))
                    .timeout(Duration.ofSeconds(20)).header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(req.toString())).build(), HttpResponse.BodyHandlers.ofString());
            JsonObject msg = JsonParser.parseString(resp.body()).getAsJsonObject().getAsJsonArray("choices").get(0)
                    .getAsJsonObject().getAsJsonObject("message");
            if (msg.has("tool_calls") && msg.get("tool_calls").isJsonArray())
                for (JsonElement call : msg.getAsJsonArray("tool_calls")) {
                    JsonObject f = call.getAsJsonObject().getAsJsonObject("function");
                    if (!"answer".equals(f.get("name").getAsString())) continue;
                    JsonObject args = JsonParser.parseString(f.get("arguments").getAsString()).getAsJsonObject();
                    if (args.has("option") && args.get("option").isJsonPrimitive()) {
                        int o = args.get("option").getAsInt();
                        if (o >= 1 && o <= labels.size()) return o - 1;
                    }
                }
            if (parseText && msg.has("content") && msg.get("content").isJsonPrimitive()) {
                Matcher m = NUMBER.matcher(msg.get("content").getAsString());
                while (m.find()) {
                    int o = Integer.parseInt(m.group(1));
                    if (o >= 1 && o <= labels.size()) return o - 1;
                }
            }
        } catch (Exception e) {
            return -1;
        }
        return -1;
    }

    /** The play menu: every non-mana ability the engine lists for the seat's cards that it can pay for. */
    List<SpellAbility> playable() {
        Player me = getPlayer();
        List<SpellAbility> menu = new ArrayList<>();
        for (SpellAbility sa : ComputerUtilAbility.getSpellAbilities(ComputerUtilAbility.getAvailableCards(getGame(), me), me)) {
            sa.setActivatingPlayer(me);
            if (sa.isManaAbility()) continue;
            if (sa.isLandAbility() ? me.canPlayLand(sa.getHostCard(), false, sa) : sa.canPlay() && ComputerUtilCost.canPayCost(sa, me, false))
                menu.add(sa);
        }
        return menu;
    }

    /** Priority to the model: a play from the menu, or PASS. Forge's AI fills in the targets the cast path needs. */
    @Override public List<SpellAbility> chooseSpellAbilityToPlay() {
        List<SpellAbility> menu = playable();
        List<String> labels = new ArrayList<>();
        for (SpellAbility sa : menu) labels.add(sa.getHostCard().getName() + ": " + sa.getDescription());
        labels.add("PASS");
        int o = ask("You have priority. What do you do?", labels);
        if (o < 0 || o >= menu.size()) return null;
        SpellAbility sa = menu.get(o);
        if (!sa.isLandAbility()) getAi().canPlaySa(sa);
        List<SpellAbility> out = new ArrayList<>();
        out.add(sa);
        return out;
    }
}
