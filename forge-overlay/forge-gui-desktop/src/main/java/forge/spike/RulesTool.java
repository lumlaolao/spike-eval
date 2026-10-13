package forge.spike;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Read-only index over the official Magic: The Gathering Comprehensive Rules text
 * ({@code data/rules/MagicCompRules.txt}, the exact Wizards .txt). Gives a seat a searchable,
 * paginated, cross-referenced view of the CR — the rules half of "the model can always look it up".
 *
 * <p>This is PUBLIC, STATIC reference text: no engine state, no hidden information, no network, no
 * per-game mutation. That is what makes it safe to expose through the same tool loop as MEMORY,
 * whose other channels are all seat-private. Enabled by {@code RULES_TOOL=1} (off by default), so a
 * run WITHOUT it has byte-identical prompts to today's — the toggle the eval comparison needs.
 *
 * <p>Grammar (one line, shares the MEMORY tool budget). Every reply is BOUNDED and paginated — a
 * lookup can never dump the rulebook into the prompt:
 * <pre>
 *   RULES SEARCH &lt;offset&gt; &lt;words&gt;       compact hits: rule id + one short line
 *   RULES READ &lt;rule&gt; [offset]        ONE rule's full text (e.g. 509.1a)
 *   RULES SECTION &lt;number&gt; [offset]   a bounded DIGEST of a whole section (e.g. 509)
 *   RULES TERM &lt;word&gt;                 a Glossary definition
 *   RULES TOC [offset]                 list chapters to find a rule number
 * </pre>
 * Offsets are character offsets into the result, like MEMORY READ; each page is ~1200 chars and
 * ends with {@code next=}. The intended flow is digest-first, then READ only the rules in question.
 */
final class RulesTool {
    static final String VERSION = "rules-tool-v1";
    /** Deliberately tighter than MEMORY's page: a rule lookup must never dump a wall of text into the
     *  prompt. Every reply is bounded, self-describing (chars a..b/N, next=), and paginated. */
    private static final int PAGE_CHARS = 1200;
    private static final int SNIPPET = 150;
    private static final int MAX_REFS = 12;

    // A rule line: "509.1. First, ..." or "509.1a First, ..." (the trailing period on subrule
    // letters is inconsistent in the official text — both forms must parse).
    private static final Pattern RULE = Pattern.compile("^(\\d+\\.\\d+[a-z]?)\\.?\\s+(\\S.*)$");
    // A chapter/section heading: "100. General", "1. Game Concepts". Never matches a rule (whose
    // first dot is followed by a digit, not a space).
    private static final Pattern HEADING = Pattern.compile("^(\\d+)\\.\\s+(\\S.*)$");
    private static final Pattern REF_FULL = Pattern.compile("\\b(\\d{3}\\.\\d+[a-z]?)\\b");
    private static final Pattern REF_BARE = Pattern.compile("(?i)\\brule\\s+(\\d{3})\\b");

    private static final class Entry {
        final String id;
        final String chapter;   // "509. Declare Blockers Step"
        final String text;
        Entry(String id, String chapter, String text) { this.id = id; this.chapter = chapter; this.text = text; }
    }

    private final List<Entry> rules = new ArrayList<>();
    private final Map<String, Integer> byId = new LinkedHashMap<>();
    private final Map<String, String> headingByNumber = new LinkedHashMap<>();
    private final List<String> toc = new ArrayList<>();
    private final Map<String, String> glossary = new LinkedHashMap<>();
    private final String source;
    private final String effective;
    private final String loadError;

    private RulesTool(String source, String effective, String loadError) {
        this.source = source; this.effective = effective; this.loadError = loadError;
    }

    /** Parse the CR text at {@code path}; on any failure returns an unavailable tool whose
     *  {@link #available()} is false (the caller then leaves the tool unwired, never half-wired). */
    static RulesTool load(String path) {
        if (path == null || path.isEmpty()) return new RulesTool(null, null, "RULES_FILE not set");
        Path p = Paths.get(path);
        String text;
        try {
            if (!Files.isReadable(p)) return new RulesTool(path, null, "not readable: " + path);
            text = new String(Files.readAllBytes(p), StandardCharsets.UTF_8);
        } catch (Exception e) {
            return new RulesTool(path, null, "read failed: " + e);
        }
        return ofText(text, path);
    }

    /** Parse in-memory rules text. Package-private seam so tests exercise the parser without the
     *  file, and {@link #load} shares the exact same path. */
    static RulesTool ofText(String text, String source) {
        RulesTool t = new RulesTool(source, effectiveDate(text), null);
        t.parse(text);
        if (t.rules.isEmpty()) return new RulesTool(source, t.effective, "no rules parsed (wrong file?)");
        return t;
    }

    boolean available() { return loadError == null && !rules.isEmpty(); }
    String loadError() { return loadError; }
    int ruleCount() { return rules.size(); }
    int glossaryCount() { return glossary.size(); }
    String effectiveDate() { return effective; }
    String sourcePath() { return source; }

    // ------------------------------------------------------------------ parsing

    private void parse(String text) {
        String[] lines = text.split("\n", -1);
        int firstRule = -1, glossaryAt = -1;
        for (int i = 0; i < lines.length; i++) {
            if (firstRule < 0 && RULE.matcher(lines[i]).matches()) firstRule = i;
            if ("Glossary".equals(lines[i].trim())) glossaryAt = i;
        }
        if (firstRule < 0) return;
        // Walk back from the first rule through the CONTIGUOUS heading block that opens the body
        // (a section heading like "1. Game Concepts" and a chapter heading like "100. General").
        // The block ends at a non-heading line — the table of contents ends with the un-numbered
        // "Glossary"/"Credits" entries, which is exactly where this stops, so the contents headings
        // are never re-added. Capped so a future file without that separator cannot walk back into
        // the whole contents list.
        int bodyStart = firstRule;
        for (int i = firstRule - 1, steps = 0; i >= 0 && steps < 40; i--, steps++) {
            String l = lines[i].replace('\u00a0', ' ').trim();
            if (l.isEmpty()) continue;
            if (HEADING.matcher(l).matches()) { bodyStart = i; continue; }
            break;
        }
        int bodyEnd = glossaryAt > bodyStart ? glossaryAt : lines.length;

        String chapter = "";
        Entry current = null;
        for (int i = bodyStart; i < bodyEnd; i++) {
            String line = clean(lines[i]);
            if (line.isBlank()) continue;
            Matcher h = HEADING.matcher(line);
            if (h.matches()) {
                String heading = h.group(1) + ". " + h.group(2).trim();
                chapter = heading;
                headingByNumber.putIfAbsent(h.group(1), heading);
                toc.add(heading);
                continue;
            }
            Matcher r = RULE.matcher(line);
            if (r.matches()) {
                String id = r.group(1).toLowerCase(Locale.ROOT);
                current = new Entry(id, chapter, r.group(2).trim());
                byId.putIfAbsent(id, rules.size());
                rules.add(current);
            } else if (current != null) {
                // Continuation (an "Example:" line, or a wrapped clause) belongs to the last rule.
                current = new Entry(current.id, current.chapter, current.text + "\n" + line.trim());
                rules.set(rules.size() - 1, current);
                byId.putIfAbsent(current.id, rules.size() - 1);
            }
        }
        if (glossaryAt >= 0) parseGlossary(lines, glossaryAt);
    }

    private void parseGlossary(String[] lines, int start) {
        String term = null;
        StringBuilder def = new StringBuilder();
        for (int i = start + 1; i < lines.length; i++) {
            String line = clean(lines[i]);
            if (line.isBlank()) {
                if (term != null && def.length() > 0) glossary.putIfAbsent(term.toLowerCase(Locale.ROOT), term + ": " + def.toString().trim());
                term = null; def.setLength(0);
                continue;
            }
            if ("Credits".equals(line.trim())) break;
            if (term == null) { term = line.trim(); }
            else def.append(' ').append(line.trim());
        }
        if (term != null && def.length() > 0) glossary.putIfAbsent(term.toLowerCase(Locale.ROOT), term + ": " + def.toString().trim());
    }

    private static String effectiveDate(String text) {
        Matcher m = Pattern.compile("These rules are effective as of ([^\\n.]+)\\.").matcher(text);
        return m.find() ? m.group(1).trim() : "unknown";
    }

    private static String clean(String s) { return s.replace('\u00a0', ' ').replace('\r', ' ').stripTrailing(); }

    // ------------------------------------------------------------------ queries

    /** Execute one RULES command line. Never throws; malformed input returns an ERROR line. */
    String lookup(String raw) {
        if (!available()) return "ERROR: the rules tool is not available (" + (loadError == null ? "not loaded" : loadError) + ").";
        String t = raw == null ? "" : raw.strip();
        if (t.length() > 300) return "ERROR: rules command too long.";
        String[] p = t.split("\\s+", 4);
        if (p.length < 2 || !"RULES".equalsIgnoreCase(p[0])) return "ERROR: malformed RULES command.";
        String sub = p[1].toUpperCase(Locale.ROOT);
        try {
            if ("SEARCH".equals(sub)) {
                if (p.length < 4) return "ERROR: RULES SEARCH <offset> <words>.";
                return search(offset(p[2]), p[3]);
            }
            if ("READ".equals(sub)) {
                if (p.length < 3) return "ERROR: RULES READ <rule-or-section> [offset].";
                int off = p.length >= 4 ? offset(p[3]) : 0;
                if (off < 0) return offsetErr();
                return read(p[2], off);
            }
            if ("SECTION".equals(sub)) {
                if (p.length < 3) return "ERROR: RULES SECTION <number> [offset].";
                int off = p.length >= 4 ? offset(p[3]) : 0;
                if (off < 0) return offsetErr();
                String key = p[2].trim().toLowerCase(Locale.ROOT);
                while (key.endsWith(".")) key = key.substring(0, key.length() - 1);
                return section(key, p[2], off);
            }
            if ("TERM".equals(sub)) {
                if (p.length < 3) return "ERROR: RULES TERM <word>.";
                int off = p.length >= 4 ? offset(p[3]) : 0;
                if (off < 0) return offsetErr();
                return term(p[2], off);
            }
            if ("TOC".equals(sub)) {
                int off = p.length >= 3 ? offset(p[2]) : 0;
                if (off < 0) return offsetErr();
                return toc(off);
            }
            return "ERROR: unknown RULES command (use SEARCH / READ / SECTION / TERM / TOC).";
        } catch (RuntimeException e) {
            return "ERROR: rules lookup failed: " + e;
        }
    }

    private static int offset(String s) {
        try { return Integer.parseInt(s.trim()); } catch (NumberFormatException e) { return -1; }
    }
    private static String offsetErr() { return "ERROR: offset must be an integer >= 0."; }

    private String search(int off, String query) {
        String needle = norm(query);
        if (needle.isBlank()) return "ERROR: nothing to search for.";
        List<Entry> hits = new ArrayList<>();
        for (Entry e : rules) if (norm(e.text).contains(needle) || e.id.startsWith(needle)) hits.add(e);
        if (hits.isEmpty()) return "No rule contains '" + query + "'. Try RULES TOC, or fewer words.";
        // A digest: id + one short line. The model picks ids and READs the ones it needs in full.
        StringBuilder b = new StringBuilder();
        for (Entry e : hits) b.append(e.id).append(". ").append(snippet(e.text)).append('\n');
        return page("RULES SEARCH \"" + query + "\" — " + hits.size()
                + " matches (READ <id> for a rule's full text);", b.toString(), off);
    }

    private String read(String query, int off) {
        String key = query.trim().toLowerCase(Locale.ROOT);
        while (key.endsWith(".")) key = key.substring(0, key.length() - 1);
        Integer idx = byId.get(key);
        if (idx != null) {
            Entry e = rules.get(idx);
            String head = "RULES " + e.id + (e.chapter.isEmpty() ? "" : " (" + e.chapter + ")");
            StringBuilder b = new StringBuilder(e.id).append(". ").append(e.text);
            String refs = refs(e.text, e.id);
            if (!refs.isEmpty()) b.append("\nCROSS-REFERENCES (rule numbers this cites): ").append(refs);
            return page(head + " — full text;", b.toString(), off);
        }
        return section(key, query, off);
    }

    /** A whole section/chapter as a bounded DIGEST (id + one line per rule), never the full text —
     *  the context-managed way to see what a section covers before reading the rules you need. */
    private String section(String key, String shown, int off) {
        List<Entry> hits = new ArrayList<>();
        for (Entry e : rules) if (isUnder(e.id, key)) hits.add(e);
        if (hits.isEmpty()) return "ERROR: no rule or section named '" + shown + "'. Try RULES TOC or RULES SEARCH.";
        String title = headingByNumber.getOrDefault(key, "");
        StringBuilder b = new StringBuilder();
        if (!title.isEmpty()) b.append(title).append('\n');
        for (Entry e : hits) b.append(e.id).append(". ").append(snippet(e.text)).append('\n');
        return page("RULES " + key + " Digest (" + hits.size() + " rules) — READ <id> for full text;", b.toString(), off);
    }

    private String term(String word, int off) {
        String key = word.trim().toLowerCase(Locale.ROOT);
        String def = glossary.get(key);
        if (def == null) {
            // Tolerate punctuation differences (curly quotes) between the CR text and model input.
            for (Map.Entry<String, String> e : glossary.entrySet())
                if (norm(e.getKey()).equals(norm(key))) { def = e.getValue(); break; }
        }
        if (def == null) return "ERROR: no Glossary term '" + word + "'. Try RULES SEARCH <words>.";
        return page("RULES GLOSSARY;", def, off);
    }

    private String toc(int off) {
        StringBuilder b = new StringBuilder();
        for (String h : toc) b.append(h).append('\n');
        return page("RULES TOC (" + toc.size() + " headings) — READ a number for its rules;", b.toString(), off);
    }

    /** An id is under a key if it IS the key, is its letter-subrule (509.1 -> 509.1a), is a deeper
     *  dotted rule (509 -> 509.1, 509.1a), or belongs to a one-digit section (1 -> 100-199). */
    private static boolean isUnder(String id, String key) {
        if (id.equals(key)) return true;
        if (id.startsWith(key)) {
            String rest = id.substring(key.length());
            if (rest.startsWith(".")) return true;
            if (rest.matches("[a-z]")) return true;
        }
        return key.length() == 1 && key.charAt(0) >= '1' && key.charAt(0) <= '9'
                && id.matches(key + "\\d{2}\\.\\d+[a-z]?");
    }

    private String refs(String text, String self) {
        Set<String> seen = new LinkedHashSet<>();
        Matcher m = REF_FULL.matcher(text);
        while (m.find() && seen.size() < MAX_REFS) if (!m.group(1).equals(self)) seen.add(m.group(1));
        Matcher b = REF_BARE.matcher(text);
        while (b.find() && seen.size() < MAX_REFS) seen.add(b.group(1));
        return String.join(", ", seen);
    }

    private static String page(String header, String body, int off) {
        if (off > body.length()) return "ERROR: offset " + off + " is beyond the end (" + body.length() + " chars).";
        int end = off + Math.min(PAGE_CHARS, body.length() - off);
        return header + " chars " + off + ".." + end + "/" + body.length()
                + " next=" + (end < body.length() ? end : "END") + "\n" + body.substring(off, end);
    }

    private static String snippet(String text) {
        String s = text.replace('\n', ' ');
        return s.length() > SNIPPET ? s.substring(0, SNIPPET) + " …" : s;
    }

    /** Case- and punctuation-insensitive form for matching (curly quotes and apostrophes differ
     *  between the CR text and what a model types). */
    private static String norm(String s) {
        return s.toLowerCase(Locale.ROOT)
                .replace('\u2019', '\'').replace('\u2018', '\'')
                .replace('\u201c', '"').replace('\u201d', '"')
                .replace('\u2014', '-').replace('\u2013', '-');
    }

    /** Prompt instructions, appended to the MEMORY tool grammar only when this tool is wired.
     *  Describes the CONTEXT-MANAGED flow: cheap digest first, full text only for the rule you need. */
    static String instructions(String effective) {
        return "RULES TOOL " + VERSION + ": to look up the official Magic Comprehensive Rules (public "
                + "reference text, effective " + (effective == null ? "unknown" : effective) + "), reply with exactly "
                + "ONE line: RULES SEARCH <offset> <words> (compact hits: rule id + one line), "
                + "RULES READ <rule> [offset] (one rule's FULL text, e.g. 509.1a), "
                + "RULES SECTION <number> [offset] (a bounded DIGEST of a whole section/chapter, e.g. 509), "
                + "RULES TERM <word> (a Glossary definition), or RULES TOC [offset] (list chapters to find a "
                + "rule number). Replies are strictly bounded: ~" + PAGE_CHARS + " characters per page, with a "
                + "next= offset to continue; prefer the digest, then READ only the rules you need, so you never "
                + "dump the rulebook into context. Shares the MEMORY tool budget. The rules are universal "
                + "reference text, NOT current game state; consult them, then answer the original action.\n";
    }
}
