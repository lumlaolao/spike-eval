"""One temp directory per test module, removed with everything in it when the module's tests end.

A test file that makes temp files calls `module_tmp.isolate(__name__)` from its setUpModule(): every tempfile call
in that process (mkdtemp, mkstemp, NamedTemporaryFile, TemporaryDirectory, in the test or in the code it calls)
then lands in the module's directory, so a test that forgets its own cleanup leaves nothing behind. A process the
test starts reads TMPDIR, not this: the acceptance stage's TMPDIR guard catches what such a process leaves.
"""
import shutil
import tempfile
import unittest


def isolate(name):
    """Point tempfile's default directory at a fresh one for this module; returns its path."""
    saved, tempfile.tempdir = tempfile.tempdir, tempfile.mkdtemp(prefix=name.rsplit(".", 1)[-1] + "-")
    unittest.addModuleCleanup(shutil.rmtree, tempfile.tempdir, True)
    unittest.addModuleCleanup(setattr, tempfile, "tempdir", saved)  # cleanups run last-first: restored, then removed
    return tempfile.tempdir
