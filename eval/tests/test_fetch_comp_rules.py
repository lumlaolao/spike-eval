#!/usr/bin/env python3
"""eval/fetch_comp_rules.py writes the pinned rules text only when its sha256 is the pinned one."""

import hashlib
import io
import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import fetch_comp_rules as fcr


def opener(body):
    return lambda url, timeout=None: io.BytesIO(body)


class FetchCompRulesTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.dest = os.path.join(self.dir, "data", "rules", "MagicCompRules.txt")

    def test_the_pinned_text_is_written(self):
        body = b"the rules\n"
        fcr.fetch(self.dest, sha256=hashlib.sha256(body).hexdigest(), url="u", urlopen=opener(body))
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), body)

    def test_a_different_text_is_refused_and_nothing_is_written(self):
        with self.assertRaises(SystemExit) as e:
            fcr.fetch(self.dest, sha256="0" * 64, url="u", urlopen=opener(b"other rules\n"))
        self.assertIn("sha256", str(e.exception))
        self.assertFalse(os.path.exists(os.path.dirname(self.dest)), "a refused text left a file behind")

    def test_a_file_already_pinned_is_not_fetched_again(self):
        body = b"the rules\n"
        os.makedirs(os.path.dirname(self.dest))
        with open(self.dest, "wb") as f:
            f.write(body)

        def refuse(url, timeout=None):
            raise AssertionError("fetched although the file was already the pinned one")
        fcr.fetch(self.dest, sha256=hashlib.sha256(body).hexdigest(), url="u", urlopen=refuse)

    def test_the_readme_records_the_pinned_source_and_sha(self):
        readme = os.path.join(os.path.dirname(fcr.DEST), "README.md")
        with open(readme, encoding="utf-8") as f:
            text = f.read()
        self.assertIn(fcr.SOURCE_URL, text)
        self.assertIn(fcr.SHA256, text)


if __name__ == "__main__":
    unittest.main()
