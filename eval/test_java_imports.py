"""java_imports: the unused-import fixture is the one that broke the checkout build in PR #80
(NativeTools.java kept `import java.util.Map;` after its only user was deleted)."""
import os
import unittest

import java_imports as ji

BROKEN = """package forge.spike;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

final class NativeTools {
    static final java.util.Set<String> DOWNGRADED = java.util.concurrent.ConcurrentHashMap.newKeySet();
    List<String> seats() { return new ArrayList<>(); }
}
"""


class JavaImportsTest(unittest.TestCase):
    def test_the_import_that_broke_the_checkout_build_is_reported(self):
        # ConcurrentHashMap and "Map" inside it must not count as a use of java.util.Map.
        self.assertEqual(ji.problems(BROKEN), [(5, "unused import java.util.Map")])

    def test_duplicates_are_reported_and_used_imports_are_not(self):
        src = "import java.util.List;\nimport java.util.List;\nimport static org.junit.Assert.assertTrue;\n" \
              "import java.io.*;\nclass A { List<String> x; void t() { assertTrue(true); } }\n"
        self.assertEqual(ji.problems(src), [(2, "duplicate import java.util.List")])

    def test_a_qualified_use_of_another_class_is_not_a_use(self):
        self.assertEqual(ji.problems("import java.util.Map;\nclass A { Object o = com.x.Map.of(); }\n"),
                         [(1, "unused import java.util.Map")])
        self.assertEqual(ji.problems("import java.util.Map;\nclass A { Map.Entry<String, String> e; }\n"), [])

    def test_the_overlay_is_clean(self):
        overlay = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "forge-overlay")
        self.assertEqual(ji.main([overlay]), 0)


if __name__ == "__main__":
    unittest.main()
