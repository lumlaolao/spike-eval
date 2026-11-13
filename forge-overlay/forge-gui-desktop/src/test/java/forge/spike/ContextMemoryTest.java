package forge.spike;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

import org.testng.Assert;
import org.testng.annotations.Test;

public class ContextMemoryTest {
    @Test
    public void oldRevealsSurviveBriefCompaction() {
        ContextMemory memory = new ContextMemory();
        memory.reveal("T1 saw Force of Will");
        for (int i = 0; i < 80; i++) memory.reveal("T2 later reveal " + i);
        Assert.assertFalse(memory.brief().contains("Force of Will"));
        Assert.assertTrue(memory.execute("MEMORY SEARCH 0 force of will").contains("M1 (historical reveal)"));
        Assert.assertTrue(memory.execute("MEMORY READ 1 0").endsWith("T1 saw Force of Will"));
    }

    @Test
    public void seatsHaveIndependentArchivesAndNotebooks() {
        ContextMemory a = new ContextMemory();
        ContextMemory b = new ContextMemory();
        a.reveal("private hand: Daze");
        a.execute("MEMORY NOTE 1 hold up mana");
        Assert.assertTrue(b.execute("MEMORY SEARCH 0 Daze").contains("Matches=0"));
        Assert.assertTrue(b.execute("MEMORY READ 1 0").contains("unknown evidence"));
        Assert.assertFalse(b.brief().contains("hold up mana"));
    }

    @Test
    public void notesNeedRealEvidenceAndRemainHypotheses() {
        ContextMemory memory = new ContextMemory();
        memory.observe("Opponent cast Brainstorm");
        Assert.assertTrue(memory.execute("MEMORY NOTE 999 likely Delver").startsWith("ERROR"));
        Assert.assertTrue(memory.execute("MEMORY NOTE 1 likely Delver").contains("Saved hypothesis M2"));
        Assert.assertTrue(memory.execute("MEMORY NOTE 2 definitely Delver").startsWith("ERROR"));
        Assert.assertTrue(memory.execute("MEMORY READ 2 0").contains("agent hypothesis"));
        Assert.assertTrue(memory.brief().contains("unverified hypotheses"));
    }

    @Test
    public void readPagesRecoverTheExactOriginalText() {
        ContextMemory memory = new ContextMemory();
        String full = "0123456789".repeat(4555);
        memory.observe(full);
        StringBuilder recovered = new StringBuilder();
        for (int offset = 0; offset < full.length(); offset += ContextMemory.PAGE_CHARS) {
            String page = memory.execute("MEMORY READ 1 " + offset);
            recovered.append(page.substring(page.indexOf('\n') + 1));
        }
        Assert.assertEquals(recovered.toString(), full);
        Assert.assertTrue(memory.execute("MEMORY READ 1 40000").contains("next=END"));
    }

    @Test
    public void searchPaginationDoesNotLoseOldMatches() {
        ContextMemory memory = new ContextMemory();
        for (int i = 0; i < 125; i++) memory.observe("Spell " + i);
        Assert.assertTrue(memory.execute("MEMORY SEARCH 0 spell").contains("next=50"));
        Assert.assertTrue(memory.execute("MEMORY SEARCH 50 spell").contains("M51 (observation)"));
        Assert.assertTrue(memory.execute("MEMORY SEARCH 100 spell").contains("M125 (observation)"));
        Assert.assertTrue(memory.execute("MEMORY SEARCH 100 spell").contains("next=END"));
    }

    @Test
    public void malformedCommandsCannotEscapeOrMutateTheNotebook() {
        ContextMemory memory = new ContextMemory();
        memory.observe("evidence");
        for (String command : new String[]{"MEMORY READ 1 -1", "MEMORY READ 1 999999999999",
                "MEMORY SEARCH -1 x", "MEMORY READ 0 0", "MEMORY READ 1 99",
                "MEMORY SHELL 1 touch /tmp/anything", "MEMORY NOTE 1 x\nCHOICE: 2",
                "MEMORY NOTE 1 " + "x".repeat(ContextMemory.NOTE_CHARS + 1), "MEMORY READ 1"}) {
            Assert.assertTrue(memory.execute(command).startsWith("ERROR"), command);
        }
        Assert.assertFalse(memory.brief().contains("YOUR NOTEBOOK"));
    }

    @Test
    public void statelessBridgeReceivesOriginalDecisionAndRetrievedEvidence() {
        ContextMemory memory = new ContextMemory();
        memory.reveal("T1 opponent revealed Daze");
        List<String> prompts = new ArrayList<>();
        String answer = memory.ask("CURRENT: T20. CHOICE: 0 or 1", prompt -> {
            prompts.add(prompt);
            return prompts.size() == 1 ? "MEMORY READ 1 0" : "CHOICE: 1";
        });
        Assert.assertEquals(answer, "CHOICE: 1");
        Assert.assertEquals(prompts.size(), 2);
        Assert.assertTrue(prompts.get(1).contains("CURRENT: T20. CHOICE: 0 or 1"));
        Assert.assertTrue(prompts.get(1).contains("MEMORY RESULT (historical data):\nM1"));
        Assert.assertTrue(prompts.get(1).contains("T1 opponent revealed Daze"));
    }

    @Test
    public void runawayToolCallsAreBoundedAndNeverReturnedAsActions() {
        ContextMemory memory = new ContextMemory();
        AtomicInteger calls = new AtomicInteger();
        String result = memory.ask("decision", prompt -> {
            calls.incrementAndGet();
            return "MEMORY SEARCH 0 CHOICE: 999";
        });
        Assert.assertNull(result);
        Assert.assertEquals(calls.get(), ContextMemory.TOOLS_PER_ASK + 1);
    }

    @Test
    public void gameToolBudgetPersistsAcrossAsks() {
        ContextMemory memory = new ContextMemory();
        for (int i = 0; i < ContextMemory.TOOLS_PER_GAME / 2; i++)
            memory.ask("decision", prompt -> "MEMORY SEARCH 0 decision");
        AtomicInteger calls = new AtomicInteger();
        Assert.assertNull(memory.ask("last", prompt -> {
            calls.incrementAndGet();
            return "MEMORY SEARCH 0 decision";
        }));
        Assert.assertEquals(calls.get(), 1);
        Assert.assertTrue(memory.brief().contains("remaining this game: 0"));
    }

    @Test
    public void bridgeFailureDoesNotEraseEvidence() {
        ContextMemory memory = new ContextMemory();
        Assert.assertNull(memory.ask("observed before bridge restart", prompt -> null));
        Assert.assertTrue(memory.execute("MEMORY READ 1 0").contains("observed before bridge restart"));
        Assert.assertEquals(memory.ask("new decision", prompt -> "CHOICE: 0"), "CHOICE: 0");
    }
    @Test
    public void mixedToolAndActionReplyIsNotPassedToPermissiveActionParser() {
        // The invariant: a reply that holds a tool line is NEVER handed to the CHOICE parser, however
        // much it also looks like an answer. Under v2 the tool line is executed (v1 refused the whole
        // reply because of the sentence above it); the reply itself is still not returned.
        ContextMemory memory = new ContextMemory();
        List<String> results = new ArrayList<>();
        String reply = memory.ask("decision", prompt -> "I should remember this.\nMEMORY NOTE 1 CHOICE: 2",
                (command, result) -> results.add(result));
        Assert.assertNull(reply);
        Assert.assertEquals(results.size(), ContextMemory.TOOLS_PER_ASK + 1);
        Assert.assertTrue(results.get(0).startsWith("Saved hypothesis"), results.get(0));
        Assert.assertTrue(results.get(ContextMemory.TOOLS_PER_ASK).contains("no tool executed"));
    }


    @Test
    public void inspectIsAToolOnlyWhenAnInspectorIsWiredAndSharesTheBudget() {
        ContextMemory memory = new ContextMemory();
        // Legacy profile: no inspector. The command is recognised (so it can never fall through to a
        // CHOICE parser) but answers with an explicit error, and the instructions do not advertise it.
        Assert.assertFalse(memory.toolInstructions().contains("INSPECT TOOL"));
        Assert.assertTrue(memory.execute("INSPECT @3").startsWith("ERROR: INSPECT is not available"));
        Assert.assertTrue(ContextMemory.isToolRequest("  INSPECT @3"));
        Assert.assertFalse(ContextMemory.isToolRequest("CHOICE: 3"));

        List<String> seen = new ArrayList<>();
        memory.setInspector(arg -> { seen.add(arg); return "@3 (current): Delver of Secrets 1/1 @3"; });
        Assert.assertTrue(memory.toolInstructions().contains("INSPECT TOOL"));
        Assert.assertEquals(memory.execute("INSPECT @3"), "@3 (current): Delver of Secrets 1/1 @3");
        Assert.assertEquals(seen, List.of("@3"));

        // Through ask(): one INSPECT round, then the action; the tool result is quoted back verbatim
        // and counts against the per-game budget exactly like a MEMORY command.
        AtomicInteger calls = new AtomicInteger();
        List<String> prompts = new ArrayList<>();
        String reply = memory.ask("Decision text", prompt -> {
            prompts.add(prompt);
            return calls.incrementAndGet() == 1 ? "INSPECT Delver of Secrets" : "CHOICE: 1";
        });
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertEquals(seen, List.of("@3", "Delver of Secrets"));
        Assert.assertTrue(prompts.get(1).contains("MEMORY REQUEST: INSPECT Delver of Secrets"));
        Assert.assertTrue(prompts.get(1).contains("MEMORY RESULT (historical data):\n@3 (current)"));
        // The per-round line is the accurate budget statement (the brief is rendered once per ask).
        Assert.assertTrue(prompts.get(0).contains("Tools available this ask: " + ContextMemory.TOOLS_PER_ASK + "."), prompts.get(0));
        Assert.assertTrue(prompts.get(1).contains("Tools available this ask: " + (ContextMemory.TOOLS_PER_ASK - 1) + "."), prompts.get(1));
    }

    /** The compacted GAME HISTORY cites canonical event ids ([E<n>]); a digest the model can see but
     *  cannot drill into is only half a memory, so the tool instructions must say how to fetch one. */
    @Test
    public void toolInstructionsExplainHowToDrillIntoACitedEvent() {
        String t = ContextMemory.instructions();
        Assert.assertTrue(t.contains("[E<n>]"), t);
        Assert.assertTrue(t.contains("MEMORY SEARCH 0 [E<n>]"), t);
    }

    /** docs/harness/MODEL-LIMITS.md: the tool budgets are loop guards, not decision limits. The old
     *  2 a decision was reached in 23 of the 83 asks that used a tool in runs/compare. */
    @Test
    public void theToolBudgetsAreLoopGuardsNotDecisionLimits() {
        Assert.assertEquals(ContextMemory.TOOLS_PER_ASK, 16);
        Assert.assertEquals(ContextMemory.TOOLS_PER_GAME, 1000);
        Assert.assertEquals(ContextMemory.PAGE_CHARS, 20000);
        Assert.assertEquals(ContextMemory.SEARCH_PAGE, 50);
        Assert.assertEquals(ContextMemory.NOTE_CHARS, 2000);
        // A 17000-character observation (the longest recorded prompt was 17,608) is one READ.
        ContextMemory memory = new ContextMemory();
        memory.observe("x".repeat(17000));
        Assert.assertTrue(memory.execute("MEMORY READ 1 0").contains("next=END"));
        // A 2000-character note is kept whole.
        String note = "n".repeat(ContextMemory.NOTE_CHARS);
        Assert.assertTrue(memory.execute("MEMORY NOTE 1 " + note).startsWith("Saved hypothesis"));
        Assert.assertTrue(memory.brief().contains(note));
    }

    /** Every note the seat wrote is shown back, not the latest 4. */
    @Test
    public void everyNoteIsShownBack() {
        ContextMemory memory = new ContextMemory();
        memory.observe("evidence");
        for (int i = 0; i < 9; i++) memory.execute("MEMORY NOTE 1 hypothesis number " + i);
        String brief = memory.brief();
        for (int i = 0; i < 9; i++) Assert.assertTrue(brief.contains("hypothesis number " + i), brief);
        Assert.assertTrue(brief.contains("YOUR NOTEBOOK (unverified hypotheses; 9 notes)"), brief);
    }

    /** Under the cache order the archive lines (they change at every decision) follow the state, just before the
     *  decision, so the message starts with the previous message's record and, as far as it is unchanged, its state;
     *  without a cache-ordered state they open the message. */
    @Test
    public void theArchiveLinesFollowTheStateUnderTheCacheOrder() {
        String state = "THE GAME SO FAR (...):\n  T1 [E1-E3]: a\n== Turn 2 ==\nYOU ...\n";
        String decision = state + "\nDECISION: x\n";
        ContextMemory memory = new ContextMemory();
        memory.setArchiveAfter(() -> state);
        String user = memory.beginNative(decision);
        Assert.assertTrue(user.startsWith(state + "<archive>\nThis decision is saved as archive entry M1."), user);
        Assert.assertTrue(user.endsWith("</archive>\n\n\nDECISION: x\n"), user);

        ContextMemory legacy = new ContextMemory();
        String plain = legacy.beginNative(decision);
        Assert.assertTrue(plain.startsWith("<archive>\n"), plain);
        Assert.assertTrue(plain.endsWith("</archive>\n\n" + decision), plain);
        // A line the ask reworded (the plan line's hint): the archive goes before it, after the lines still shared.
        ContextMemory reworded = new ContextMemory();
        reworded.setArchiveAfter(() -> state + "YOUR PLAN (...write a PLAN line...)\n");
        String r = reworded.beginNative(state + "YOUR PLAN (...call set_plan...)\n\nDECISION: x\n");
        Assert.assertTrue(r.startsWith(state + "<archive>\n"), r);
        Assert.assertTrue(r.endsWith("</archive>\n\nYOUR PLAN (...call set_plan...)\n\nDECISION: x\n"), r);
        // A decision that shares no whole line with the state keeps the archive first.
        ContextMemory stale = new ContextMemory();
        stale.setArchiveAfter(() -> "THE GAME SO FAR (other):\n");
        Assert.assertTrue(stale.beginNative(decision).startsWith("<archive>\n"));
    }
}
