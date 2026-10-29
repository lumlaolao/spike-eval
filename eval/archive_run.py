#!/usr/bin/env python3
"""Pack a finished run directory into one tar.gz with a SHA256 manifest of every file, and prove the archive holds
exactly those bytes (docs/eval/PUZZLES.md, "Keeping a run").

    python3 eval/archive_run.py runs/puzzles/frontier-1                 # -> runs/puzzles/frontier-1.tar.gz
    python3 eval/archive_run.py runs/puzzles/frontier-1 --out /keep/frontier-1.tar.gz
    python3 eval/archive_run.py --verify /keep/frontier-1.tar.gz        # re-hash every member against its manifest
    python3 eval/archive_run.py --check runs/puzzles/frontier-1         # the record check alone, nothing written

Packing refuses, before writing anything, when:
  - the run is not over, or the directory is part of a run that is not (a puzzle-run.json at or above it says
    finished=false; a live run is still writing): --allow-unfinished;
  - the full record has a gap (check_record): a result row without the record its manifest asked for; a row that
    billed model calls but names no http file; a game with fewer answered calls than replies on its context_call rows;
    a named transcript file missing; a row that could not be written, or a cut row; a body that does not match its
    sha256; a .pzl copy that is not the position the suite froze. --allow-gaps packs it with the gaps listed in the
    manifest's header. A call sent and never answered (the JVM died in it) is listed there too, and not refused;
  - any file holds an OpenRouter key (the OPENROUTER_API_KEY value from the environment or secrets.env, or anything
    shaped like one): never overridable;
  - a symbolic link (an archive must hold the bytes, not a pointer to them).

Written beside the archive: <out>.manifest.sha256 (the same per-file manifest as inside it, `sha256sum -c` format,
paths relative to the directory that holds the run) and <out>.sha256 (the archive's own hash). The archive is then
re-read and every member re-hashed against the manifest and against the source file; --verify also compares the
manifest beside the archive with the one inside. A mismatch deletes nothing but exits 1.

What it cannot prove: that the run recorded everything (only that what the run says it recorded is present and
intact), nor anything about a model lane with no HTTP of its own (the Claude CLI).
"""
import argparse
import collections
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


def read_jsonl(path, where, problems):
    """The rows of a JSONL file; a line that is not JSON (a row cut by a failed write) is a problem, named."""
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append((n, json.loads(line)))
        except ValueError:
            problems.append("%s:%d is not JSON (a cut row)" % (where, n))
    return rows


def check_puzzle(d, run_dir, problems, counts):
    """One puzzle directory's record against its manifest, its result rows and its game rows."""
    rel = lambda p: Path(p).relative_to(run_dir).as_posix()  # noqa: E731
    m = d / "batch-manifest.json"
    rec = ((json.loads(m.read_text()) or {}).get("record") or {}) if m.is_file() else {}
    want = rec.get("transcript_version")
    if rec.get("pzl_file"):
        pzl = d / rec["pzl_file"]
        if not pzl.is_file():
            problems.append("%s: the .pzl copy %s is missing" % (rel(m), rec["pzl_file"]))
        elif rec.get("pzl_sha256") and sha256_file(pzl) != rec["pzl_sha256"]:
            problems.append("%s: %s is not the position the suite froze" % (rel(m), rec["pzl_file"]))
    res = d / "puzzle-results.jsonl"
    for n, row in (read_jsonl(res, rel(res), problems) if res.is_file() else []):
        where = "%s:%d" % (rel(res), n)
        if want and row.get("transcript_version") != want:
            problems.append("%s has no %s record, which its manifest asked for (a build without Transcript?)"
                            % (where, want))
        if not row.get("transcript_version"):
            continue
        counts["attempts_with_record"] += 1
        http = row.get("transcript_http_files") or []
        if row.get("model_calls") and not any((d / f).is_file() for f in http):
            problems.append("%s billed %d model calls but names no http file that exists" % (where, row["model_calls"]))
        for f in http + [row.get("transcript_engine_file")]:
            if f and not (d / f).is_file():
                problems.append("%s names %s, which is missing" % (where, f))
        if not row.get("transcript_engine_file"):
            problems.append("%s has no engine log" % where)
        if row.get("transcript_errors"):
            problems.append("%s: %d transcript rows could not be written" % (where, row["transcript_errors"]))
    # Calls: a sent row and a response row of one seq, per JVM (the file stem before its -<n> part).
    sent, answered, ok_by_game = {}, set(), collections.Counter()
    for f in sorted(d.glob("transcripts/http-*.jsonl")):
        jvm = f.stem.rsplit("-", 1)[0]
        counts["http_bytes"] += f.stat().st_size
        for n, r in read_jsonl(f, rel(f), problems):
            where = "%s:%d" % (rel(f), n)
            for body, sha in (("request_body", "request_sha256"), ("response_body", "response_sha256")):
                if r.get(body) is not None and sha256_bytes(r[body].encode("utf-8")) != r.get(sha):
                    problems.append("%s: %s does not match its sha256" % (where, body))
            if any(h.lower() == "authorization" for h in (r.get("request_headers") or {})):
                problems.append("%s records an Authorization header" % where)
            key = (jvm, r.get("seq"))
            if r.get("phase") == "sent":
                sent[key] = "%s seq %s (%s)" % (rel(f), r.get("seq"), r.get("label"))
            elif r.get("phase") == "response":
                answered.add(key)
                if r.get("status") == 200 and r.get("response_body") is not None:
                    ok_by_game[r.get("game_file")] += 1
    counts["http_calls"] += len(answered)
    counts["unanswered"] += [sent[k] for k in sorted(sent, key=str) if k not in answered]
    if not want:
        return
    # Every reply a game row recorded came back in an answered call (a null reply may have sent nothing).
    for g in sorted(d.glob("game-*.jsonl")):
        replies = sum(1 for _, r in read_jsonl(g, rel(g), problems)
                      if r.get("t") == "context_call" and r.get("raw_reply") is not None)
        if ok_by_game[g.name] < replies:
            problems.append("%s: %d answered http calls for %d model replies" % (rel(g), ok_by_game[g.name], replies))


def check_record(run_dir):
    """The run's full record against what it says it holds. Returns (problems, counts); counts["unanswered"] lists
    calls sent with no response row (a JVM that died in a call): listed, not a gap."""
    run_dir = Path(run_dir)
    problems = []
    counts = {"http_calls": 0, "http_bytes": 0, "engine_files": 0, "attempts_with_record": 0, "unanswered": []}
    dirs = sorted({p.parent for p in run_dir.rglob("batch-manifest.json")}
                  | {p.parent for p in run_dir.rglob("puzzle-results.jsonl")})
    for d in dirs:
        check_puzzle(d, run_dir, problems, counts)
    counts["engine_files"] = len(list(run_dir.rglob("transcripts/engine-*.jsonl")))
    return problems, counts


def manifest_text(run_dir, paths, gaps, unanswered=()):
    base = Path(run_dir).parent
    head = "".join("# GAP %s\n" % g for g in gaps) + "".join("# UNANSWERED %s\n" % u for u in unanswered)
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
    # The run's own puzzle-run.json, or that of a run this directory is part of (one puzzle of a live run).
    for d in [run_dir] + list(run_dir.parents):
        run = d / RUN_FILE
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
    text = manifest_text(run_dir, paths, gaps, counts["unanswered"])
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
    beside = Path(str(archive) + ".manifest.sha256")
    if beside.is_file() and parse_manifest(beside.read_text()) != want:
        problems.append("%s differs from the manifest inside the archive" % beside.name)
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
