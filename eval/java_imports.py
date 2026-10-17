#!/usr/bin/env python3
"""Unused and duplicate imports in Java sources.

Why it exists: the Forge checkout build runs checkstyle and rejects both; the acceptance suite's
isolated `javac` accepts both. Twice a change passed acceptance and then failed `mvn install` in the
checkout (PR #60, PR #80). This closes that gap for the two rules that bit.

What it cannot see: anything else checkstyle checks. As checkstyle's UnusedImports does, a name only in a comment
or a string is not a use, but one in a javadoc {@link}, {@linkplain}, @see or @throws is.

  python3 eval/java_imports.py forge-overlay            # exit 1 and one line per problem
"""
import os
import re
import sys

IMPORT = re.compile(r"^\s*import\s+(static\s+)?([\w.]+)(\.\*)?\s*;", re.M)
# Comments and string or char literals, in one pass so a quote inside a comment (or "//" in a string) is read right.
NOISE = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
JAVADOC_REF = re.compile(r"\{@link(?:plain)?\s+([\w.#]+)|@(?:see|throws|exception)\s+([\w.#]+)")


def code_and_doc_refs(body):
    """The source with comments and literals blanked, plus the names javadoc references (which checkstyle counts)."""
    refs = []
    def blank(m):
        t = m.group(0)
        if t.startswith("/**"):
            refs.extend(a or b for a, b in JAVADOC_REF.findall(t))
        return " "
    return NOISE.sub(blank, body) + "\n" + " ".join(refs)


def problems(source):
    out, seen = [], set()
    body = code_and_doc_refs(IMPORT.sub("", source))
    for m in IMPORT.finditer(source):
        static, name, star = m.group(1), m.group(2), m.group(3)
        line = source.count("\n", 0, m.start()) + 1
        key = (bool(static), name, bool(star))
        if key in seen:
            out.append((line, "duplicate import %s" % name))
            continue
        seen.add(key)
        if star:
            continue                                     # a wildcard cannot be judged without the classpath
        simple = name.rsplit(".", 1)[-1]
        if not re.search(r"(?<![\w.])%s\b" % re.escape(simple), body):
            out.append((line, "unused import %s" % name))
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    bad = 0
    for root in argv:
        for d, _, files in os.walk(root):
            for f in sorted(files):
                if f.endswith(".java"):
                    path = os.path.join(d, f)
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        for line, what in problems(fh.read()):
                            print("%s:%d: %s" % (path, line, what))
                            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
