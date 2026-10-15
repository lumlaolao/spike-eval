package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A scripted test model that answers by function call, as a model on the default protocol does. It
 * wraps a text-scripted bridge (the golden checks' Script/Recorder): each ask's first user message goes
 * to the script, and the script's own reply line (CHOICE / PICKS / KEEP / NUMBER / CARD / ALLOC) is
 * turned into an `answer` call. It reads only the script's replies, never a model's prose.
 *
 * Every ask it sees is checked: a tool ask that still offers a text answer is recorded in {@link #leaks}.
 */
final class ToolAnswerBridge implements LLMBridge {
    private final LLMBridge script;
    final List<String> leaks = new ArrayList<>();
    /** Asks answered by a call through this bridge; a text ask would go to {@link #ask} instead. */
    int chats;
    int texts;
    private int seq;

    ToolAnswerBridge(LLMBridge script) { this.script = script; }

    @Override public boolean supportsNativeTools() { return true; }
    @Override public String ask(String prompt) { texts++; return script.ask(prompt); }
    @Override public String getLabel() { return script.getLabel(); }
    @Override public String getModel() { return script.getModel(); }
    @Override public long getTotalMs() { return 0; }
    @Override public int getCalls() { return script.getCalls(); }

    @Override
    public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
        chats++;
        JsonArray msgs = JsonParser.parseString(messagesJson).getAsJsonArray();
        String prompt = msgs.get(0).getAsJsonObject().get("content").getAsString();
        String leak = AnswerTool.textFormatLeak(prompt);
        if (leak != null && msgs.size() == 1) leaks.add(leak);
        String args = toArgs(script.ask(prompt), prompt);   // the script itself, not counted as a text ask
        NativeTools.Call call = new NativeTools.Call("t" + (++seq), AnswerTool.NAME, args);
        JsonObject msg = new JsonObject();
        msg.addProperty("role", "assistant");
        JsonArray tcs = new JsonArray();
        JsonObject tc = new JsonObject();
        tc.addProperty("id", call.id);
        tc.addProperty("type", "function");
        JsonObject f = new JsonObject();
        f.addProperty("name", call.name);
        f.addProperty("arguments", call.arguments);
        tc.add("function", f);
        tcs.add(tc);
        msg.add("tool_calls", tcs);
        List<NativeTools.Call> calls = new ArrayList<>();
        calls.add(call);
        return new NativeTools.Turn(null, msg.toString(), calls);
    }

    private static final Pattern TAG = Pattern.compile("(?m)^\\s*(CHOICE|PICKS|NUMBER|CARD|ALLOC|BLOCKS):\\s*(.*)$");

    /** The script's reply as `answer` arguments: its last tagged line, or KEEP / MULLIGAN. */
    static String toArgs(String reply, String prompt) {
        JsonObject a = new JsonObject();
        if (reply == null) return a.toString();
        String tag = null, value = null;
        Matcher m = TAG.matcher(reply);
        while (m.find()) { tag = m.group(1); value = m.group(2).trim(); }
        if (tag == null) {
            String t = reply.trim().toUpperCase();
            if (t.endsWith("MULLIGAN")) a.addProperty("keep", false);
            else if (t.endsWith("KEEP")) a.addProperty("keep", true);
            return a.toString();
        }
        switch (tag) {
            case "CHOICE": a.addProperty("option", firstInt(value)); break;
            case "NUMBER": a.addProperty("number", firstInt(value)); break;
            case "CARD": a.addProperty("card", value); break;
            case "PICKS": a.add("options", ints(value.equalsIgnoreCase("NONE") ? "" : value)); break;
            case "ALLOC": a.add("amounts", ints(value)); break;
            case "BLOCKS": a.add("blocks", blocks(value, prompt)); break;
            default: break;
        }
        return a.toString();
    }

    /** "Grizzly Bears->Savannah Lions, ..." as blocker/attacker numbers from the ask's two numbered lists. */
    private static JsonArray blocks(String value, String prompt) {
        JsonArray out = new JsonArray();
        if (value.equalsIgnoreCase("NONE")) return out;
        for (String pair : value.split(",")) {
            String[] ab = pair.split("->");
            if (ab.length != 2) continue;
            JsonObject o = new JsonObject();
            o.addProperty("blocker", numberOf(prompt, "YOUR BLOCKERS:", ab[0].trim()));
            o.addProperty("attacker", numberOf(prompt, "ATTACKERS:", ab[1].trim()));
            out.add(o);
        }
        return out;
    }

    private static int numberOf(String prompt, String header, String name) {
        int i = prompt.lastIndexOf(header);
        if (i < 0) return -1;
        int end = prompt.indexOf('\n', i);
        Matcher m = Pattern.compile("(\\d+) = " + Pattern.quote(name)).matcher(prompt.substring(i, end < 0 ? prompt.length() : end));
        return m.find() ? Integer.parseInt(m.group(1)) : -1;
    }

    private static int firstInt(String s) {
        Matcher m = Pattern.compile("-?\\d+").matcher(s);
        return m.find() ? Integer.parseInt(m.group()) : -1;
    }

    private static JsonArray ints(String s) {
        JsonArray out = new JsonArray();
        Matcher m = Pattern.compile("-?\\d+").matcher(s);
        while (m.find()) out.add(Integer.parseInt(m.group()));
        return out;
    }

}
