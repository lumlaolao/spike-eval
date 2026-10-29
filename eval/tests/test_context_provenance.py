"""Context and tool availability are experimental conditions, not cosmetic metadata."""
import unittest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
from aggregate import MISSING, summarize_provenance


class ContextProvenanceTest(unittest.TestCase):
    def test_each_changed_context_condition_is_reported_as_mixed(self):
        for field in ('context_profile', 'context_sha', 'memory_tools', 'memory_tools_version'):
            with self.subTest(field=field):
                summary = summarize_provenance([{field: 'one'}, {field: 'two'}])
                self.assertTrue(summary['mixed'])
                self.assertIn(field, summary['mixed_fields'])

    def test_missing_is_distinct_from_explicitly_disabled(self):
        summary = summarize_provenance([{}, {'memory_tools': False}])
        self.assertEqual(summary['fields']['memory_tools'], [MISSING, False])
        self.assertIn('memory_tools', summary['mixed_fields'])
