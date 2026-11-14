"""archive_run.py packs a finished puzzle run whole and proves it: every file's bytes come back out, a changed byte is
caught, a live run, a gap in the record or a key in any file is refused. The fixture has the shape puzzle_solve.py
and Transcript.java write (a run root with puzzle-run.json and suite.json, a puzzle directory with its manifest,
.pzl copy, result row, game rows, jvm.log and transcripts/)."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import module_tmp  # noqa: E402
import archive_run  # noqa: E402

PZL = "[metadata]\nName:equip_1\nGoal:Win\n[state]\nhumanlife=20\n"


def setUpModule():
    module_tmp.isolate(__name__)


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


HTTP = "http-1792886060000-4242-1.jsonl"
GAME = "game-1-5.jsonl"
REQ = "{\"model\":\"m\",\"messages\":[{\"role\":\"system\",\"content\":\"\u00e9t\u00e9\"}]}"
RESP = "\n  \n{\"id\":\"gen-1\",\"choices\":[]}"


def sent(seq, game=GAME):
    return json.dumps({"t": "http", "phase": "sent", "seq": seq, "label": "Solver/smart", "game_file": game,
                       "request_body": REQ, "request_sha256": sha(REQ),
                       "request_headers": {"Content-Type": ["application/json"]}}) + "\n"


def response(seq, game=GAME, status=200):
    return json.dumps({"t": "http", "phase": "response", "seq": seq, "label": "Solver/smart", "game_file": game,
                       "status": status, "response_body": RESP, "response_sha256": sha(RESP)}) + "\n"


def make_run(finished=True):
    root = Path(tempfile.mkdtemp()).resolve() / "frontier-1"   # pack() resolves; macOS's /var is /private/var
    d = root / "hard-equip-1"
    t = d / "transcripts"
    t.mkdir(parents=True)
    (root / "puzzle-run.json").write_text(json.dumps({"ids": ["hard-equip-1"], "k": 1, "finished": finished,
                                                      "suite_file": "suite.json"}))
    (root / "suite.json").write_text("{\"suite_sha256\": \"s\"}")
    (d / "equip_1.pzl").write_text(PZL)
    (d / "batch-manifest.json").write_text(json.dumps({"condition": {}, "record": {
        "transcript_version": "transcript-v1", "pzl_file": "equip_1.pzl", "pzl_sha256": sha(PZL)}}))
    (t / HTTP).write_text(sent(1) + response(1))
    (t / "engine-equip_1-seed1-r0-3.jsonl").write_text(json.dumps({"t": "engine", "seq": 1, "type": "TURN",
                                                                   "message": "Turn 1"}) + "\n")
    (d / "puzzle-results.jsonl").write_text(json.dumps({
        "schema": "pzl-result-v1", "seed": 1, "transcript_version": "transcript-v1",
        "transcript_http_files": ["transcripts/" + HTTP], "model_calls": 1,
        "transcript_engine_file": "transcripts/engine-equip_1-seed1-r0-3.jsonl", "transcript_errors": 0}) + "\n")
    (d / GAME).write_text("{\"t\":\"context_call\",\"raw_reply\":\"TOOL CALL answer {}\"}\n")
    (d / "jvm.log").write_bytes(b"[HEADLESS] start\n\xff raw byte kept\n")
    return root


def pack(root, **kw):
    out = root.parent / "out" / "frontier-1.tar.gz"
    with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-0123456789abcdef0123"}), \
            mock.patch.object(archive_run, "REPO", root):
        return out, archive_run.pack(root, out, **kw)


class RoundTripTest(unittest.TestCase):
    def test_every_file_comes_back_byte_for_byte_and_the_archive_verifies(self):
        root = make_run()
        out, (paths, counts, gaps, problems) = pack(root)
        self.assertEqual((gaps, problems), ([], []))
        self.assertEqual(counts["http_calls"], 1)
        self.assertEqual(counts["engine_files"], 1)
        with tarfile.open(str(out)) as tar:
            back = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
        for p in paths:
            self.assertEqual(back[p.relative_to(root.parent).as_posix()], p.read_bytes(), str(p))
        self.assertIn("frontier-1/" + archive_run.MANIFEST, back)
        self.assertEqual(len(back), len(paths) + 1, "the files and the manifest, nothing else")
        self.assertEqual(archive_run.verify(out), [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(archive_run.main(["--verify", str(out)]), 0)
        man = archive_run.parse_manifest(Path(str(out) + ".manifest.sha256").read_text())
        self.assertEqual(len(man), len(paths), "one manifest line per file")

    def test_a_changed_byte_is_caught(self):
        root = make_run()
        out, _ = pack(root)
        bad = root.parent / "bad.tar.gz"
        with tarfile.open(str(out)) as src, tarfile.open(str(bad), "w:gz") as dst:
            for m in src.getmembers():
                data = src.extractfile(m).read() if m.isfile() else None
                if m.name.endswith("jvm.log"):
                    data = data.replace(b"start", b"START")
                    m.size = len(data)
                dst.addfile(m, io.BytesIO(data) if data is not None else None)
        self.assertTrue(any("jvm.log" in p and "sha256" in p for p in archive_run.verify(bad)))

    def test_a_file_missing_from_the_archive_is_caught(self):
        root = make_run()
        out, _ = pack(root)
        bad = root.parent / "short.tar.gz"
        with tarfile.open(str(out)) as src, tarfile.open(str(bad), "w:gz") as dst:
            for m in src.getmembers():
                if not m.name.endswith("suite.json"):
                    dst.addfile(m, src.extractfile(m) if m.isfile() else None)
        self.assertTrue(any("not in the archive" in p for p in archive_run.verify(bad)))


class RefusalTest(unittest.TestCase):
    def test_a_live_run_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            pack(make_run(finished=False))
        self.assertIn("not finished", str(cm.exception))

    def test_a_key_in_any_file_is_refused_whatever_the_flags(self):
        root = make_run()
        with open(root / "hard-equip-1" / "jvm.log", "a") as f:
            f.write("Authorization: Bearer sk-or-v1-0123456789abcdef0123\n")
        with self.assertRaises(SystemExit) as cm:
            pack(root, allow_unfinished=True, allow_gaps=True)
        self.assertIn("jvm.log", str(cm.exception))
        self.assertFalse((root.parent / "out").exists(), "nothing written")

    def test_a_missing_transcript_file_is_a_gap(self):
        root = make_run()
        (root / "hard-equip-1" / "transcripts" / "engine-equip_1-seed1-r0-3.jsonl").unlink()
        with self.assertRaises(SystemExit) as cm:
            pack(root)
        self.assertIn("missing", str(cm.exception))
        out, (_, _, gaps, problems) = pack(root, allow_gaps=True)
        self.assertEqual(problems, [])
        self.assertIn("# GAP", Path(str(out) + ".manifest.sha256").read_text())

    def test_a_body_that_does_not_match_its_hash_or_a_cut_row_is_a_gap(self):
        root = make_run()
        f = root / "hard-equip-1" / "transcripts" / HTTP
        row = json.loads(response(1))
        row["response_body"] = row["response_body"][:-3]
        f.write_text(sent(1) + json.dumps(row) + "\n" + "{\"t\":\"http\",\"request_bo\n")
        problems, _ = archive_run.check_record(root)
        self.assertTrue(any("response_body does not match" in p for p in problems), problems)
        self.assertTrue(any("not JSON" in p for p in problems), problems)

    def test_a_pzl_copy_that_is_not_the_frozen_position_is_a_gap(self):
        root = make_run()
        (root / "hard-equip-1" / "equip_1.pzl").write_text(PZL + "humanhand=Shock\n")
        problems, _ = archive_run.check_record(root)
        self.assertTrue(any("not the position" in p for p in problems), problems)

    def test_a_row_that_could_not_be_written_is_a_gap(self):
        root = make_run()
        f = root / "hard-equip-1" / "puzzle-results.jsonl"
        row = json.loads(f.read_text())
        row["transcript_errors"] = 2
        f.write_text(json.dumps(row) + "\n")
        problems, _ = archive_run.check_record(root)
        self.assertTrue(any("could not be written" in p for p in problems), problems)

    def test_the_check_alone_writes_nothing(self):
        root = make_run()
        before = sorted(str(p) for p in root.parent.rglob("*"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(archive_run.main([str(root), "--check"]), 0)
        self.assertEqual(sorted(str(p) for p in root.parent.rglob("*")), before)
        shutil.rmtree(str(root.parent))


class RecordAskedForTest(unittest.TestCase):
    """A puzzle whose manifest asked for the record (record.transcript_version) must have it: a build without
    Transcript (an old build let run stale) writes rows with no record and no files, and that is a gap, not 0 gaps."""

    def test_a_row_without_the_record_the_manifest_asked_for_is_a_gap(self):
        root = make_run()
        d = root / "hard-equip-1"
        shutil.rmtree(str(d / "transcripts"))
        (d / "puzzle-results.jsonl").write_text(json.dumps({"schema": "pzl-result-v1", "seed": 1, "model_calls": 25})
                                                + "\n")
        problems, _ = archive_run.check_record(root)
        self.assertTrue(any("no transcript-v1 record" in p for p in problems), problems)

    def test_a_row_that_billed_calls_must_name_an_http_file_that_exists(self):
        root = make_run()
        (root / "hard-equip-1" / "transcripts" / HTTP).unlink()
        problems, _ = archive_run.check_record(root)
        self.assertTrue(any("http file" in p for p in problems), problems)

    def test_fewer_answered_calls_than_the_games_replies_is_a_gap(self):
        root = make_run()
        d = root / "hard-equip-1"
        with open(d / GAME, "a") as f:
            f.write("{\"t\":\"context_call\",\"raw_reply\":\"TOOL CALL answer {}\"}\n")
            f.write("{\"t\":\"context_call\",\"raw_reply\":null}\n")  # no reply: maybe nothing was sent
        problems, counts = archive_run.check_record(root)
        self.assertTrue(any("1 answered http calls for 2 model replies" in p for p in problems), problems)
        with open(d / "transcripts" / HTTP, "a") as f:
            f.write(sent(2) + response(2))
        self.assertEqual(archive_run.check_record(root)[0], [])

    def test_a_call_sent_and_never_answered_is_listed_not_hidden(self):
        root = make_run()
        with open(root / "hard-equip-1" / "transcripts" / HTTP, "a") as f:
            f.write(sent(2))
        problems, counts = archive_run.check_record(root)
        self.assertEqual(problems, [])
        self.assertEqual(len(counts["unanswered"]), 1, counts)
        out, _ = pack(root)
        self.assertIn("# UNANSWERED", Path(str(out) + ".manifest.sha256").read_text())


class WhereAndWhatTest(unittest.TestCase):
    def test_a_puzzle_directory_of_a_live_run_is_refused(self):
        root = make_run(finished=False)
        with self.assertRaises(SystemExit) as cm:
            with mock.patch.object(archive_run, "REPO", root):
                archive_run.pack(root / "hard-equip-1", root.parent / "one.tar.gz")
        self.assertIn("not finished", str(cm.exception))

    def test_a_side_manifest_that_differs_from_the_one_inside_is_caught(self):
        root = make_run()
        out, _ = pack(root)
        side = Path(str(out) + ".manifest.sha256")
        side.write_text(side.read_text().replace("suite.json", "suite.jsn"))
        self.assertTrue(any("manifest.sha256" in p for p in archive_run.verify(out)), archive_run.verify(out))


if __name__ == "__main__":
    unittest.main()
