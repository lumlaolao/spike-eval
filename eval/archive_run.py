#!/usr/bin/env python3
"""Pack a finished run directory into one tar.gz with a SHA256 manifest of every file, and prove the archive holds
exactly those bytes (eval/PUZZLES.md, "Keeping a run").

    python3 eval/archive_run.py runs/puzzles/frontier-1                 # -> runs/puzzles/frontier-1.tar.gz
    python3 eval/archive_run.py runs/puzzles/frontier-1 --out /keep/frontier-1.tar.gz
    python3 eval/archive_run.py --verify /keep/frontier-1.tar.gz        # re-hash every member against its manifest
    python3 eval/archive_run.py --check runs/puzzles/frontier-1         # the record check alone, nothing written

Packing refuses, before writing anything, when:
  - the run is not over (puzzle-run.json says finished=false; a live run is still writing): --allow-unfinished;
  - the full record has a gap (check_record: a result row that names a transcript file the directory lacks, a row
    that could not be written, an http row whose body does not match its sha256, a .pzl copy that is not the
    position the suite froze): --allow-gaps, and the gaps are then listed in the manifest's header;
  - any file holds an OpenRouter key (the OPENROUTER_API_KEY value from the environment or secrets.env, or anything
    shaped like one): never overridable;
  - a symbolic link (an archive must hold the bytes, not a pointer to them).

Written beside the archive: <out>.manifest.sha256 (the same per-file manifest as inside it, `sha256sum -c` format,
paths relative to the directory that holds the run) and <out>.sha256 (the archive's own hash). The archive is then
re-read and every member re-hashed against the manifest and against the source file; a mismatch deletes nothing but
exits 1.

What it cannot prove: that the run recorded everything (only that what the run says it recorded is present and
intact), nor anything about a model lane with no HTTP of its own (the Claude CLI).
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

MANIFEST = "ARCHIVE-MANIFEST.sha256"
RUN_FILE = "puzzle-run.json"
KEY_SHAPE = re.compile(rb"sk-or-v1-[0-9A-Za-z]{16,}")
REPO = Path(__file__).resolve().parent.parent


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def known_key():
    """The OpenRouter key this machine holds (environment, then secrets.env), or None: a file holding it is refused."""
    k = os.environ.get("OPENROUTER_API_KEY")
    if k:
        return k.encode()
    p = REPO / "secrets.env"
    if p.is_file():
        for line in p.read_text().splitlines():
            if line.strip().startswith("OPENROUTER_API_KEY=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip().encode()
    return None


def files_of(run_dir):
    """Every regular file under run_dir, sorted; a symbolic link is refused."""
    out = []
    for p in sorted(Path(run_dir).rglob("*")):
        if p.is_symlink():
            raise SystemExit("archive_run: %s is a symbolic link; an archive holds bytes, not pointers" % p)
        if p.is_file():
            out.append(p)
    return out


def secrets_in(paths, key):
    """The files holding the key or anything shaped like an OpenRouter key."""
    bad = []
    for p in paths:
        data = p.read_bytes()
        if (key and key in data) or KEY_SHAPE.search(data):
            bad.append(str(p))
    return bad


def check_record(run_dir):
    """The run's full record against what it says it holds. Returns (problems, counts)."""
    run_dir = Path(run_dir)
    problems, counts = [], {"http_rows": 0, "http_bytes": 0, "engine_files": 0, "attempts_with_record": 0}
    for res in sorted(run_dir.rglob("puzzle-results.jsonl")):
        d = res.parent
        for n, line in enumerate(res.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("transcript_version"):
                continue
            counts["attempts_with_record"] += 1
            where = "%s:%d" % (res.relative_to(run_dir), n)
            for field in ("transcript_http_file", "transcript_engine_file"):
                if row.get(field) and not (d / row[field]).is_file():
                    problems.append("%s names %s, which is missing" % (where, row[field]))
            if not row.get("transcript_engine_file"):
                problems.append("%s has no engine log" % where)
            if row.get("transcript_errors"):
                problems.append("%s: %d transcript rows could not be written" % (where, row["transcript_errors"]))
    for f in sorted(run_dir.rglob("transcripts/http-*.jsonl")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = "%s:%d" % (f.relative_to(run_dir), n)
            try:
                r = json.loads(line)
            except ValueError:
                problems.append("%s is not JSON (a cut row)" % where)
                continue
            counts["http_rows"] += 1
            counts["http_bytes"] += len(line) + 1
            for body, sha in (("request_body", "request_sha256"), ("response_body", "response_sha256")):
                if r.get(body) is not None and sha256_bytes(r[body].encode("utf-8")) != r.get(sha):
                    problems.append("%s: %s does not match its sha256" % (where, body))
            if any(h.lower() == "authorization" for h in (r.get("request_headers") or {})):
                problems.append("%s records an Authorization header" % where)
    counts["engine_files"] = len(list(run_dir.rglob("transcripts/engine-*.jsonl")))
    for m in sorted(run_dir.rglob("batch-manifest.json")):
        rec = (json.loads(m.read_text()) or {}).get("record") or {}
        if rec.get("pzl_file"):
            pzl = m.parent / rec["pzl_file"]
            if not pzl.is_file():
                problems.append("%s: the .pzl copy %s is missing" % (m.relative_to(run_dir), rec["pzl_file"]))
            elif rec.get("pzl_sha256") and sha256_file(pzl) != rec["pzl_sha256"]:
                problems.append("%s: %s is not the position the suite froze" % (m.relative_to(run_dir), rec["pzl_file"]))
    return problems, counts


def manifest_text(run_dir, paths, gaps):
    base = Path(run_dir).parent
    head = "".join("# GAP %s\n" % g for g in gaps)
    return head + "".join("%s  %s\n" % (sha256_file(p), p.relative_to(base).as_posix()) for p in paths)


def parse_manifest(text):
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            sha, name = line.split("  ", 1)
            out[name] = sha
    return out


def pack(run_dir, out, allow_unfinished=False, allow_gaps=False):
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise SystemExit("archive_run: %s is not a directory" % run_dir)
    run = run_dir / RUN_FILE
    if run.is_file() and not json.loads(run.read_text()).get("finished") and not allow_unfinished:
        raise SystemExit("archive_run: %s says the run is not finished (a live run is still writing); "
                         "--allow-unfinished to pack it as it stands" % run)
    paths = files_of(run_dir)
    bad = secrets_in(paths, known_key())
    if bad:
        raise SystemExit("archive_run: refusing, an OpenRouter key is in: %s" % ", ".join(bad))
    gaps, counts = check_record(run_dir)
    if gaps and not allow_gaps:
        raise SystemExit("archive_run: the record has %d gaps (--allow-gaps lists them in the manifest):\n  %s"
                         % (len(gaps), "\n  ".join(gaps[:20])))
    text = manifest_text(run_dir, paths, gaps)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(out), "w:gz") as tar:
        for p in paths:
            tar.add(str(p), arcname=p.relative_to(run_dir.parent).as_posix(), recursive=False)
        data = text.encode("utf-8")
        info = tarfile.TarInfo("%s/%s" % (run_dir.name, MANIFEST))
        info.size, info.mtime, info.mode = len(data), int(os.path.getmtime(run_dir)), 0o644
        tar.addfile(info, io.BytesIO(data))
    Path(str(out) + ".manifest.sha256").write_text(text)
    Path(str(out) + ".sha256").write_text("%s  %s\n" % (sha256_file(out), out.name))
    problems = verify(out, source=run_dir.parent)
    return paths, counts, gaps, problems


def verify(archive, source=None):
    """Every member re-hashed against the manifest inside the archive (and the source file, when given); the set of
    members must be exactly the manifest's. Returns the problems (empty: intact)."""
    problems = []
    with tarfile.open(str(archive), "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        man = [m for m in members if m.name.endswith("/" + MANIFEST)]
        if len(man) != 1:
            return ["%s: %d manifests (want 1)" % (archive, len(man))]
        want = parse_manifest(tar.extractfile(man[0]).read().decode("utf-8"))
        got = {}
        for m in members:
            if m is man[0]:
                continue
            got[m.name] = sha256_bytes(tar.extractfile(m).read())
    for name in sorted(set(want) | set(got)):
        if name not in got:
            problems.append("%s: in the manifest, not in the archive" % name)
        elif name not in want:
            problems.append("%s: in the archive, not in the manifest" % name)
        elif want[name] != got[name]:
            problems.append("%s: sha256 differs from the manifest" % name)
        elif source is not None and sha256_file(Path(source) / name) != got[name]:
            problems.append("%s: differs from the file it was packed from" % name)
    side = Path(str(archive) + ".sha256")
    if side.is_file() and side.read_text().split()[0] != sha256_file(archive):
        problems.append("%s: the archive's own sha256 differs from %s" % (archive, side.name))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", nargs="?")
    ap.add_argument("--out", default=None, help="the archive (default: <run_dir>.tar.gz)")
    ap.add_argument("--verify", default=None, metavar="ARCHIVE")
    ap.add_argument("--check", action="store_true", help="check the record only; write nothing")
    ap.add_argument("--allow-unfinished", action="store_true")
    ap.add_argument("--allow-gaps", action="store_true")
    a = ap.parse_args(argv)
    if a.verify:
        problems = verify(a.verify)
        print("\n".join(problems) if problems else "%s: intact" % a.verify)
        return 1 if problems else 0
    if not a.run_dir:
        ap.error("a run directory, or --verify ARCHIVE")
    if a.check:
        problems, counts = check_record(a.run_dir)
        print("\n".join(problems + [json.dumps(counts)]))
        return 1 if problems else 0
    out = a.out or str(Path(a.run_dir).resolve()) + ".tar.gz"
    paths, counts, gaps, problems = pack(a.run_dir, out, a.allow_unfinished, a.allow_gaps)
    size = sum(p.stat().st_size for p in paths)
    print("%s: %d files, %d bytes packed into %d bytes; %s; %d gaps" % (out, len(paths), size, os.path.getsize(out),
                                                                       json.dumps(counts), len(gaps)))
    print("\n".join(problems) if problems else "verified: every member matches its sha256 and its source file")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
