package forge.spike;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import forge.GuiDesktop;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.card.CardView;
import forge.gamemodes.match.HostedMatch;
import forge.gui.interfaces.IGuiGame;

/** The demo's highlights drawn on Forge's own match window (docs/harness/DEMO.md): the cards the model is thinking
 *  about at its latest decision get Forge's highlight frame.
 *
 *  Display-only and one-way. demo_server.py computes the highlights (demo_highlights.py, from the model's own calls
 *  and words) and writes the engine card ids to {@code DEMO_HIGHLIGHT_FILE}; this watcher reads that file and calls
 *  the spectator window's {@code setHighlighted}. It never reads the seat, never writes anything the seat reads, runs
 *  on its own daemon thread, and catches everything, so a failure costs the window a frame and never the game.
 *  Off unless DEMO_HIGHLIGHT_FILE is set; headless runs never construct it. */
final class DemoHighlight {
    private static volatile IGuiGame spectator;

    private DemoHighlight() { }

    static String file() {
        String f = System.getenv("DEMO_HIGHLIGHT_FILE");
        return f == null || f.trim().isEmpty() ? null : f.trim();
    }

    /** The desktop interface, remembering the match window it opens (the spectator's, when no human plays). */
    static GuiDesktop capturingGui() {
        return new GuiDesktop() {
            @Override
            public IGuiGame getNewGuiGame() {
                IGuiGame g = super.getNewGuiGame();
                spectator = g;
                return g;
            }
        };
    }

    static void watch(HostedMatch hosted, String path) {
        Thread t = new Thread(() -> {
            File f = new File(path);
            long seen = -1;
            List<CardView> shown = new ArrayList<>();
            while (true) {
                try {
                    Thread.sleep(250);
                    long m = f.lastModified();
                    if (m == 0 || m == seen) continue;
                    seen = m;
                    IGuiGame gui = spectator;
                    Game game = hosted.getGame();
                    if (gui == null || game == null) continue;
                    JsonObject o = JsonParser.parseString(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8)).getAsJsonObject();
                    List<CardView> next = new ArrayList<>();
                    JsonArray ids = o.has("ids") ? o.getAsJsonArray("ids") : new JsonArray();
                    for (JsonElement e : ids) {
                        Card c = game.findById(e.getAsInt());
                        if (c != null) next.add(c.getView());
                    }
                    if (!shown.isEmpty()) gui.setHighlighted(shown, false);
                    if (!next.isEmpty()) gui.setHighlighted(next, true);
                    List<CardView> repaint = new ArrayList<>(shown);
                    repaint.addAll(next);
                    if (!repaint.isEmpty()) gui.updateCards(repaint);
                    shown = next;
                } catch (InterruptedException e) {
                    return;
                } catch (Throwable ignore) {
                    // The window misses this frame; the game goes on.
                }
            }
        }, "demo-highlight");
        t.setDaemon(true);
        t.start();
        System.out.println("[DEMO-HIGHLIGHT] watching " + path + " (display only; the seat never reads it)");
    }
}
