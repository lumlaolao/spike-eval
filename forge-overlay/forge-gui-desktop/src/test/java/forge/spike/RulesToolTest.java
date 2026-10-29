package forge.spike;

import java.util.concurrent.atomic.AtomicInteger;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * The RULES tool: an index over the public Comprehensive Rules. Two layers of coverage —
 * a small in-memory fixture that pins PARSER + COMMAND semantics deterministically, and (when the
 * real CR file resolves) the official text's shape and spot rules. Corpus provenance (sha, counts)
 * is gated separately by eval/tests/test_rules_corpus.py, which runs from the repo root.
 */
public class RulesToolTest {
    private static final String NB = "\u00a0"; // the CR's blank-line filler

    private static String fixture() {
        return String.join("\n",
                "Magic: The Gathering Comprehensive Rules",
                NB,
                "These rules are effective as of Testuary 1, 2026.",
                NB,
                "1. Game Concepts",
                NB,
                "100. General",
                NB,
                "100.1. These rules apply to any game. See rule 101.1.",
                NB,
                "100.1a A two-player game has two players.",
                NB,
                "100.2. Each player needs a deck.",
                "Example: A continuation line that belongs to 100.2.",
                NB,
                "101. The Magic Golden Rules",
                NB,
                "101.1. Cards win over rules.",
                NB,
                "Glossary",
                NB,
                "Ability",
                "Text on an object. See rule 100.1.",
                NB,
                "Credits",
                "");
    }

    private static RulesTool tool() { return RulesTool.ofText(fixture(), "fixture"); }

    @Test
    public void parsesRulesSectionsExamplesAndGlossary() {
        RulesTool t = tool();
        Assert.assertTrue(t.available());
        Assert.assertEquals(t.ruleCount(), 4);
        Assert.assertEquals(t.glossaryCount(), 1);
        Assert.assertEquals(t.effectiveDate(), "Testuary 1, 2026");
    }

    @Test
    public void readExactRuleReturnsFullTextAndCrossReferences() {
        RulesTool t = tool();
        String out = t.lookup("RULES READ 100.1");
        Assert.assertTrue(out.contains("These rules apply to any game"), out);
        Assert.assertTrue(out.contains("CROSS-REFERENCES"), out);
        Assert.assertTrue(out.contains("101.1"), out);
        // An "Example:" continuation line belongs to its rule, and READ gives the FULL text.
        Assert.assertTrue(t.lookup("RULES READ 100.2").contains("A continuation line that belongs to 100.2"));
    }

    @Test
    public void sectionReadIsABoundedDigestNotFullText() {
        RulesTool t = tool();
        String out = t.lookup("RULES SECTION 100");
        Assert.assertTrue(out.contains("Digest"), out);
        Assert.assertTrue(out.contains("100.1a"), out);
        Assert.assertTrue(out.contains("READ <id> for full text"), out);
        // The digest lists every rule id in the section…
        Assert.assertTrue(out.contains("100.1") && out.contains("100.1a") && out.contains("100.2"), out);
        // READ on a section number is the same digest.
        Assert.assertTrue(t.lookup("RULES READ 100").contains("Digest"));
        // A one-digit SECTION reaches every chapter in it (1 -> 100, 101).
        String s1 = t.lookup("RULES SECTION 1");
        Assert.assertTrue(s1.contains("100.1") && s1.contains("101.1"), s1);
    }

    @Test
    public void searchAndTocAndTermAreCompact() {
        RulesTool t = tool();
        String hit = t.lookup("RULES SEARCH 0 two players");
        Assert.assertTrue(hit.contains("100.1a"), hit);
        String toc = t.lookup("RULES TOC 0");
        Assert.assertTrue(toc.contains("100. General") && toc.contains("101. The Magic Golden Rules"), toc);
        String term = t.lookup("RULES TERM Ability");
        Assert.assertTrue(term.contains("Text on an object"), term);
        Assert.assertTrue(t.lookup("RULES TERM nonsense").startsWith("ERROR"));
    }

    @Test
    public void paginationAndErrorsAreExplicit() {
        RulesTool t = tool();
        String toc = t.lookup("RULES TOC 0");
        Assert.assertTrue(toc.contains("next="), toc);
        Assert.assertTrue(t.lookup("RULES READ 999.9").startsWith("ERROR"));
        Assert.assertTrue(t.lookup("RULES READ 100.1 999999").startsWith("ERROR"));
        Assert.assertTrue(t.lookup("RULES FOO").startsWith("ERROR"));
        Assert.assertTrue(t.lookup("RULES READ 100.1 abc").startsWith("ERROR"));
        Assert.assertFalse(RulesTool.ofText("nothing here", "x").available());
    }

    @Test
    public void rulesIsAToolRequestAndGatedInContextMemory() {
        Assert.assertTrue(ContextMemory.isToolRequest("RULES TOC 0"));
        ContextMemory mem = new ContextMemory();
        // Unwired: a clear ERROR, never a silent no-op.
        Assert.assertTrue(mem.execute("RULES TOC 0").startsWith("ERROR"));
        Assert.assertFalse(mem.toolInstructions().contains("RULES TOOL"));
        RulesTool t = tool();
        mem.setRulesLookup(RulesTool.instructions(t.effectiveDate()), t::lookup);
        Assert.assertTrue(mem.toolInstructions().contains("RULES TOOL " + RulesTool.VERSION));
        Assert.assertTrue(mem.execute("RULES TOC 0").contains("100. General"));
        // End to end through the ask loop: one rules round trip, then the action, with the RULES
        // envelope labelled as public rules text (never "historical data").
        AtomicInteger calls = new AtomicInteger();
        String reply = mem.ask("DECISION: pick one", prompt -> calls.incrementAndGet() == 1 ? "RULES TOC 0" : "CHOICE: 1");
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertEquals(calls.get(), 2);
    }

    /** When the bundled CR resolves (the acceptance passes -Dharness.rules.file=$REPO/data/rules/…),
     *  assert the official text's shape and spot rules. Absent elsewhere, the fixture tests still run. */
    @Test
    public void realCorpusWhenPresentHasTheOfficialShape() {
        String path = realRulesPath();
        if (path == null) { System.out.println("[RulesToolTest] no bundled CR found; fixture coverage only"); return; }
        RulesTool t = RulesTool.load(path);
        Assert.assertTrue(t.available(), "bundled CR must load: " + t.loadError());
        Assert.assertTrue(t.ruleCount() >= 3000, "rule count " + t.ruleCount());
        Assert.assertTrue(t.glossaryCount() >= 200, "glossary " + t.glossaryCount());
        Assert.assertEquals(t.effectiveDate(), "September 25, 2026");
        Assert.assertTrue(t.lookup("RULES READ 509.1a").contains("defending player chooses which creatures"));
        Assert.assertTrue(t.lookup("RULES READ 509.1").contains("declares blockers"));
        Assert.assertTrue(t.lookup("RULES SEARCH 0 state-based actions").contains("704"), "");
        Assert.assertTrue(t.lookup("RULES TERM Vigilance").contains("attack without tapping"));
        // READ carries the chapter title; the TOC is paginated (page 0 holds the first chapters).
        Assert.assertTrue(t.lookup("RULES READ 509.1").contains("509. Declare Blockers Step"));
        Assert.assertTrue(t.lookup("RULES TOC 0").contains("100. General"));
    }

    private static String realRulesPath() {
        for (String p : new String[] { System.getProperty("harness.rules.file"), System.getenv("RULES_FILE") }) {
            if (p != null && !p.trim().isEmpty() && new java.io.File(p.trim()).isFile()) return p.trim();
        }
        java.io.File d = new java.io.File(System.getProperty("user.dir", ".")).getAbsoluteFile();
        for (int i = 0; i < 6 && d != null; i++, d = d.getParentFile()) {
            java.io.File f = new java.io.File(d, "data/rules/MagicCompRules.txt");
            if (f.isFile()) return f.getAbsolutePath();
        }
        return null;
    }
}
