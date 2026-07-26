#!/usr/bin/env python3
"""Local tracking dashboard for mtg-forge-llm-agent.

Stdlib-only (http.server + threading). Start with:

    python3 cloud/dashboard.py          # serves http://localhost:8377

Panels: DAgger run status, corpus stats, training log tail, AWS budget
(ledger + optional Cost Explorer), incident watch. Every panel renders even
when its data source is missing — "no data", never a traceback.

Optional config (JSON, same directory, dashboard_config.json):
    runs_dir, dagger_dir, ledger, training_log, budget_monthly,
    target_games, aws_ce (bool), port
"""
import glob
import json
import os
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "runs_dir": os.path.expanduser("~/code/mtg-forge-llm-agent/runs/d4-dagger-r1"),
    "dagger_dir": os.path.expanduser("~/.claude/jobs/f9e4d46b/tmp/dagger"),
    "ledger": os.path.join(HERE, "aws_ledger.jsonl"),
    "training_log": None,
    "budget_monthly": 15.0,
    "target_games": 192,
    # Opt-in: each Cost Explorer query is a real (if tiny) AWS charge
    # ($0.01/request, cached 1h here). Consent-gate convention: costed calls
    # are never on by default — enable in dashboard_config.json.
    "aws_ce": False,
    "port": 8377,
}


def load_config(path=None):
    cfg = dict(DEFAULTS)
    path = path or os.path.join(HERE, "dashboard_config.json")
    try:
        with open(path) as f:
            user = json.load(f)
        if isinstance(user, dict):
            for k, v in user.items():
                if k in cfg and v is not None:
                    cfg[k] = os.path.expanduser(v) if k in (
                        "runs_dir", "dagger_dir", "ledger", "training_log") else v
    except (OSError, ValueError):
        pass  # no config file / bad JSON -> defaults
    return cfg


# --------------------------------------------------------------------------
# Corpus scanning (pure-ish; unit-tested). Cache keyed on (mtime, size) so a
# refresh never re-parses unchanged files.
# --------------------------------------------------------------------------

def scan_jsonl_file(path):
    """Parse one game jsonl -> per-file stats dict. Never raises."""
    st = {"games": 0, "decisions": 0, "labels": 0, "wins": 0, "losses": 0,
          "draws": 0, "by_type": {}, "last_game": None}
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                t = row.get("t")
                if t == "decision":
                    st["decisions"] += 1
                    if row.get("shadow_choice") is not None:
                        st["labels"] += 1
                        dt = str(row.get("type") or "unknown")
                        st["by_type"][dt] = st["by_type"].get(dt, 0) + 1
                elif t == "game":
                    st["games"] += 1
                    w = row.get("winner")
                    if w == "A":
                        st["wins"] += 1
                    elif w == "B":
                        st["losses"] += 1
                    else:
                        st["draws"] += 1
                    st["last_game"] = {"seed": row.get("seed"),
                                       "swap": row.get("swap"),
                                       "winner": w,
                                       "turns": row.get("turns")}
    except OSError:
        pass
    return st


def scan_corpus(runs_dir, cache=None):
    """Aggregate stats over runs_dir/*/*.jsonl. `cache` is a dict the caller
    keeps between calls: {path: (mtime, size, stats)}."""
    agg = {"files": 0, "games": 0, "decisions": 0, "labels": 0, "wins": 0,
           "losses": 0, "draws": 0, "by_type": {}, "cells": 0,
           "label_yield_pct": 0.0, "ok": False, "note": ""}
    if not runs_dir or not os.path.isdir(runs_dir):
        agg["note"] = "runs dir not found: %s" % runs_dir
        return agg
    if cache is None:
        cache = {}
    paths = sorted(glob.glob(os.path.join(runs_dir, "*", "*.jsonl")))
    agg["cells"] = len({os.path.dirname(p) for p in paths})
    seen = set()
    for p in paths:
        seen.add(p)
        try:
            s = os.stat(p)
            key = (s.st_mtime, s.st_size)
        except OSError:
            continue
        hit = cache.get(p)
        if hit is None or hit[0] != key:
            cache[p] = (key, scan_jsonl_file(p))
        st = cache[p][1]
        agg["files"] += 1
        for k in ("games", "decisions", "labels", "wins", "losses", "draws"):
            agg[k] += st[k]
        for k, v in st["by_type"].items():
            agg["by_type"][k] = agg["by_type"].get(k, 0) + v
    for stale in set(cache) - seen:   # files deleted/quarantined since last scan
        del cache[stale]
    if agg["decisions"]:
        agg["label_yield_pct"] = round(100.0 * agg["labels"] / agg["decisions"], 1)
    agg["ok"] = True
    return agg


# --------------------------------------------------------------------------
# DAgger run status from driver.log / heartbeat.log / batch.log
# --------------------------------------------------------------------------

CELL_RE = re.compile(r"\[(\d\d:\d\d:\d\d)\] cell (\S+) \(offset (\d+), attempt (\d+)\)")
VALIDATE_RE = re.compile(r"\[(\d\d:\d\d:\d\d)\] cell (\S+) validate: (OK|BAD) (\d+)")
HB_RE = re.compile(r"\[(\d\d:\d\d:\d\d)\] (ok|UNHEALTHY strike=\d+|RESTART.*?)(?: rss=(\S+))?$")


def _read_lines(path, max_bytes=2_000_000):
    """Tail-read a log defensively; returns [] when missing."""
    try:
        size = os.path.getsize(path)
        with open(path, "r", errors="replace") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # drop partial line
            return f.read().splitlines()
    except OSError:
        return []


def parse_driver_log(lines):
    out = {"current_cell": None, "current_attempt": None, "last_cell_ts": None,
           "cells_validated_ok": 0, "games_validated": 0, "server_starts": 0,
           "finished": False}
    best = {}  # cell -> max validated-OK count
    for ln in lines:
        m = CELL_RE.search(ln)
        if m:
            out["last_cell_ts"], out["current_cell"] = m.group(1), m.group(2)
            out["current_attempt"] = int(m.group(4))
        m = VALIDATE_RE.search(ln)
        if m and m.group(3) == "OK":
            c, n = m.group(2), int(m.group(4))
            best[c] = max(best.get(c, 0), n)
        if "server up" in ln:
            out["server_starts"] += 1
        if "all cells done" in ln:
            out["finished"] = True
    out["cells_validated_ok"] = sum(1 for n in best.values() if n >= 6)
    out["games_validated"] = sum(min(n, 6) for n in best.values())
    return out


def parse_heartbeat(lines):
    out = {"last_status": None, "last_ts": None, "last_rss": None,
           "unhealthy_count": 0, "restart_count": 0, "incidents": []}
    for ln in lines:
        m = HB_RE.search(ln)
        if not m:
            continue
        ts, what, rss = m.group(1), m.group(2), m.group(3)
        out["last_ts"], out["last_rss"] = ts, rss
        out["last_status"] = "ok" if what == "ok" else what
        if what != "ok":
            if what.startswith("RESTART"):
                out["restart_count"] += 1
            else:
                out["unhealthy_count"] += 1
            out["incidents"].append({"ts": ts, "what": what, "rss": rss})
    out["incidents"] = out["incidents"][-5:][::-1]  # last 5, newest first
    return out


def _pgrep_count(pattern, exact=False):
    try:
        cmd = ["pgrep", "-x" if exact else "-f", pattern]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return len([l for l in r.stdout.splitlines() if l.strip()])
    except Exception:
        return None


def dagger_panel(cfg):
    d = cfg["dagger_dir"]
    panel = {"available": os.path.isdir(d), "dir": d}
    if not panel["available"]:
        panel["note"] = "dagger scratch dir not found"
        return panel
    drv = parse_driver_log(_read_lines(os.path.join(d, "driver.log")))
    hb = parse_heartbeat(_read_lines(os.path.join(d, "heartbeat.log")))
    panel.update(drv)
    panel["heartbeat"] = hb
    panel["target_games"] = cfg["target_games"]
    for name in ("driver.log", "heartbeat.log", "batch.log"):
        try:
            panel.setdefault("log_age_s", {})[name] = int(
                time.time() - os.path.getmtime(os.path.join(d, name)))
        except OSError:
            panel.setdefault("log_age_s", {})[name] = None
    hb_age = panel["log_age_s"].get("heartbeat.log")
    if hb.get("last_status") == "ok" and hb_age is not None and hb_age < 120:
        panel["health"] = "ok"
    elif hb_age is None:
        panel["health"] = "unknown"
    elif hb_age >= 120:
        panel["health"] = "stale"          # heartbeat stopped writing
    else:
        panel["health"] = "unhealthy"
    panel["java_procs"] = _pgrep_count("java", exact=True)
    panel["mlx_procs"] = _pgrep_count("serve_capped")
    return panel


# --------------------------------------------------------------------------
# Live event feed: completed matches (corpus) merged with heartbeat events
# --------------------------------------------------------------------------

FNAME_SEED_RE = re.compile(r"game-(\d+)-")


def parse_hb_time(hhmmss, ref_epoch):
    """Map a bare HH:MM:SS log time to an epoch: the most recent occurrence
    of that time-of-day at or before ref_epoch (the log file's mtime)."""
    try:
        h, m, s = (int(x) for x in hhmmss.split(":"))
        ref = datetime.fromtimestamp(ref_epoch)
        cand = ref.replace(hour=h, minute=m, second=s, microsecond=0)
        if cand > ref + timedelta(seconds=60):   # time-of-day after the file's
            cand -= timedelta(days=1)            # last write -> previous day
        return cand.timestamp()
    except (ValueError, OSError, OverflowError):
        return ref_epoch


def match_events(cache):
    """One event per corpus file that has its final game row. Works off the
    scan cache, so a file that gains its game row minutes after appearing
    shows up as soon as the (mtime,size)-keyed rescan picks the row up."""
    events = []
    for path, (key, st) in list(cache.items()):
        g = st.get("last_game")
        if not st.get("games") or not g:
            continue          # in-flight file: game row not written yet
        mtime = key[0]
        w = g.get("winner")
        if w == "A":
            kind, outcome = "win", "WIN (A)"
        elif w == "B":
            kind, outcome = "loss", "LOSS (B)"
        else:
            kind, outcome = "draw", "DRAW/CAPPED (%s)" % w
        seed = g.get("seed")
        if seed is None:
            m = FNAME_SEED_RE.match(os.path.basename(path))
            seed = m.group(1) if m else "?"
        swap = g.get("swap")
        swap = "?" if swap is None else (1 if swap else 0)
        events.append({
            "ts": mtime, "kind": kind,
            "text": "%s seed %s swap %s — %s in %s turns · %d labels" % (
                os.path.basename(os.path.dirname(path)), seed, swap,
                outcome, g.get("turns", "?"), st.get("labels", 0)),
        })
    return events


def heartbeat_feed(lines, ref_epoch):
    """Condense heartbeat lines: every UNHEALTHY/RESTART is an event, ok
    beats are NOT (one per 45s would drown the feed) — instead a status
    summary plus a discrete 'recovered' event when ok follows a strike."""
    status = {"state": None, "last_ts": None, "consecutive_ok": 0, "text": "no heartbeat data"}
    events = []
    prev_bad = False
    consec_ok = 0
    for ln in lines:
        m = HB_RE.search(ln)
        if not m:
            continue
        ts, what, rss = m.group(1), m.group(2), m.group(3)
        ep = parse_hb_time(ts, ref_epoch)
        status["last_ts"] = ts
        if what == "ok":
            if prev_bad:
                events.append({"ts": ep, "kind": "recovered",
                               "text": "heartbeat recovered (rss %s)" % (rss or "?")})
            prev_bad = False
            consec_ok += 1
            status["state"] = "ok"
        else:
            prev_bad = True
            consec_ok = 0
            kind = "restart" if what.startswith("RESTART") else "unhealthy"
            events.append({"ts": ep, "kind": kind,
                           "text": "heartbeat %s%s" % (what, " · rss %s" % rss if rss else "")})
            status["state"] = what
    status["consecutive_ok"] = consec_ok
    if status["state"] == "ok":
        status["text"] = "heartbeat: ok, last beat %s (%d consecutive)" % (
            status["last_ts"], consec_ok)
    elif status["state"]:
        status["text"] = "heartbeat: %s, last beat %s" % (status["state"], status["last_ts"])
    return status, events


def _fmt_event_time(ts, now=None):
    now = now or time.time()
    try:
        dt = datetime.fromtimestamp(ts)
    except (OSError, OverflowError, ValueError):
        return "?"
    return dt.strftime("%H:%M:%S") if now - ts < 20 * 3600 else dt.strftime("%b %d %H:%M")


def build_feed(cfg, cache, max_events=40):
    """Merged chronological feed (newest first). Degrades per-source: either
    side can be missing and the other still renders."""
    panel = {"ok": False, "events": [], "hb_status": None, "note": ""}
    sources = 0
    try:
        agg = scan_corpus(cfg.get("runs_dir"), cache)   # cheap: mtime-cached
        if agg["ok"]:
            sources += 1
        panel["events"].extend(match_events(cache))
    except Exception as e:  # noqa: BLE001
        panel["note"] += "corpus: %s. " % str(e)[:120]
    hb_path = os.path.join(cfg.get("dagger_dir") or "", "heartbeat.log")
    try:
        ref = os.path.getmtime(hb_path)
        status, hb_events = heartbeat_feed(_read_lines(hb_path), ref)
        panel["hb_status"] = status
        panel["events"].extend(hb_events)
        sources += 1
    except OSError:
        panel["hb_status"] = {"state": None, "text": "no heartbeat log", "consecutive_ok": 0}
    except Exception as e:  # noqa: BLE001
        panel["note"] += "heartbeat: %s. " % str(e)[:120]
    panel["events"].sort(key=lambda e: e.get("ts") or 0, reverse=True)
    panel["events"] = panel["events"][:max_events]
    now = time.time()
    for e in panel["events"]:
        e["time"] = _fmt_event_time(e.get("ts") or 0, now)
    panel["ok"] = sources > 0
    if not panel["ok"] and not panel["note"]:
        panel["note"] = "no data (corpus and heartbeat both unavailable)"
    return panel


# --------------------------------------------------------------------------
# AWS budget: local ledger + optional Cost Explorer (cached; degrades)
# --------------------------------------------------------------------------

def parse_ledger_ts(ts):
    """Accept epoch seconds (int/float) or ISO date/datetime string."""
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(ts, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(ts[:19], fmt)
            except ValueError:
                continue
    return None


def summarize_ledger(path, now=None):
    """Sum hours*rate over ledger rows; total and current-month."""
    now = now or datetime.now()
    out = {"total": 0.0, "month": 0.0, "rows": 0, "bad_rows": 0, "recent": []}
    try:
        f = open(path)
    except OSError:
        out["note"] = "no ledger file yet"
        return out
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                cost = float(row["hours"]) * float(row["rate"])
            except (ValueError, KeyError, TypeError):
                out["bad_rows"] += 1
                continue
            out["rows"] += 1
            out["total"] += cost
            dt = parse_ledger_ts(row.get("ts"))
            if dt and (dt.year, dt.month) == (now.year, now.month):
                out["month"] += cost
            out["recent"].append({"what": row.get("what", "?"),
                                  "hours": row.get("hours"),
                                  "rate": row.get("rate"),
                                  "cost": round(cost, 2)})
    out["total"] = round(out["total"], 2)
    out["month"] = round(out["month"], 2)
    out["recent"] = out["recent"][-5:][::-1]
    return out


class CostExplorer:
    """Background month-to-date fetch via `aws ce get-cost-and-usage`.
    Cached for an hour (each CE API request costs $0.01). Any failure ->
    state carries an error string and the panel degrades to ledger-only."""

    CACHE_S = 3600

    def __init__(self, enabled):
        self.enabled = enabled
        self.lock = threading.Lock()
        self.state = {"mtd": None, "fetched_at": None,
                      "error": None if enabled else
                      'disabled — set "aws_ce": true in dashboard_config.json '
                      "to fetch real month-to-date cost ($0.01/query, cached 1h)"}
        self._last_try = 0.0

    def maybe_refresh_async(self):
        if not self.enabled:
            return
        with self.lock:
            if time.time() - self._last_try < self.CACHE_S:
                return
            self._last_try = time.time()
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        err, mtd = None, None
        try:
            r = subprocess.run(["aws", "sts", "get-caller-identity"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                raise RuntimeError("no working AWS credentials")
            start = date.today().replace(day=1).isoformat()
            end = (date.today() + timedelta(days=1)).isoformat()
            r = subprocess.run(
                ["aws", "ce", "get-cost-and-usage",
                 "--time-period", "Start=%s,End=%s" % (start, end),
                 "--granularity", "MONTHLY", "--metrics", "UnblendedCost",
                 "--output", "json"],
                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or "ce call failed").strip()[:200])
            data = json.loads(r.stdout)
            amt = data["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]
            mtd = round(float(amt), 2)
        except FileNotFoundError:
            err = "aws CLI not installed"
        except Exception as e:  # noqa: BLE001 — panel must never crash
            err = str(e)[:200]
        with self.lock:
            self.state = {"mtd": mtd, "fetched_at": time.time(), "error": err}

    def snapshot(self):
        with self.lock:
            return dict(self.state)


# --------------------------------------------------------------------------
# Training panel
# --------------------------------------------------------------------------

TRAIN_RE = re.compile(r"(iter|step|epoch|val[ _-]?loss|train[ _-]?loss)", re.I)


def training_panel(cfg):
    path = cfg.get("training_log")
    if not path:
        return {"active": False, "note": "no training active"}
    lines = _read_lines(path, max_bytes=200_000)
    if not lines:
        return {"active": False, "note": "training log not found: %s" % path}
    hits = [ln.strip() for ln in lines if TRAIN_RE.search(ln)][-10:]
    try:
        age = int(time.time() - os.path.getmtime(path))
    except OSError:
        age = None
    return {"active": bool(hits), "log": path, "age_s": age,
            "lines": hits or ["(no iteration/val-loss lines yet)"]}


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

CFG = load_config()
CORPUS_CACHE = {}
CE = CostExplorer(bool(CFG.get("aws_ce")))


def build_status():
    status = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    for name, fn in (
        ("dagger", lambda: dagger_panel(CFG)),
        ("corpus", lambda: scan_corpus(CFG["runs_dir"], CORPUS_CACHE)),
        ("feed", lambda: build_feed(CFG, CORPUS_CACHE)),
        ("training", lambda: training_panel(CFG)),
        ("ledger", lambda: summarize_ledger(CFG["ledger"])),
    ):
        try:
            status[name] = fn()
        except Exception as e:  # noqa: BLE001 — a panel bug must not 500 the page
            status[name] = {"error": str(e)[:300]}
    CE.maybe_refresh_async()
    status["aws_ce"] = CE.snapshot()
    status["budget_monthly"] = CFG["budget_monthly"]
    return status


PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mtg-forge-llm-agent — run dashboard</title>
<style>
  :root { color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --border:rgba(255,255,255,0.10);
    --blue:#3987e5; --good:#0ca30c; --warn:#fab219; --serious:#ec835a;
    --crit:#d03b3b; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--page); color:var(--ink2);
    font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; padding:16px; }
  h1 { color:var(--ink); font-size:17px; font-weight:650; }
  header { display:flex; justify-content:space-between; align-items:baseline;
    flex-wrap:wrap; gap:8px; margin-bottom:14px; }
  .stamp { color:var(--muted); font-size:12px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
    gap:12px; }
  .card { background:var(--surface); border:1px solid var(--border);
    border-radius:10px; padding:14px 16px; }
  .card h2 { color:var(--ink); font-size:13px; font-weight:650;
    text-transform:uppercase; letter-spacing:.04em; margin-bottom:10px; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(88px,1fr));
    gap:10px; margin-bottom:10px; }
  .tile .v { color:var(--ink); font-size:22px; font-weight:650; }
  .tile .l { color:var(--muted); font-size:11px; }
  .bar { background:var(--grid); border-radius:4px; height:10px;
    overflow:hidden; margin:6px 0 4px; }
  .bar > div { height:100%; border-radius:4px; background:var(--blue);
    min-width:2px; transition:width .4s; }
  .rows td { padding:2px 10px 2px 0; font-variant-numeric:tabular-nums; }
  .rows td:first-child { color:var(--ink2); }
  .rows td:last-child { color:var(--ink); text-align:right; }
  table { border-collapse:collapse; width:100%; }
  .status { display:inline-flex; align-items:center; gap:6px;
    font-weight:600; color:var(--ink); }
  .dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
  .mini { color:var(--muted); font-size:12px; margin-top:6px; }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px;
    color:var(--ink2); white-space:pre-wrap; word-break:break-word; }
  ul.inc { list-style:none; } ul.inc li { padding:3px 0;
    border-bottom:1px solid var(--grid); font-variant-numeric:tabular-nums; }
  ul.inc li:last-child { border-bottom:none; }
  ul.feed { list-style:none; max-height:340px; overflow-y:auto; }
  ul.feed li { display:flex; gap:8px; align-items:baseline; padding:3px 0;
    border-bottom:1px solid var(--grid); font-size:12.5px; }
  ul.feed li:last-child { border-bottom:none; }
  ul.feed .t { color:var(--muted); flex:0 0 62px;
    font-variant-numeric:tabular-nums; font-size:11.5px; }
  ul.feed .dot { flex:0 0 9px; position:relative; top:-1px; }
  .ev-win { color:var(--ink); font-weight:600; }
  .ev-loss { color:var(--muted); }
  .ev-draw { color:var(--ink2); font-style:italic; }
  .ev-unhealthy { color:var(--serious); font-weight:600; }
  .ev-restart { color:var(--crit); font-weight:600; }
  .ev-recovered { color:var(--good); }
  .hbline { display:flex; gap:8px; align-items:center; padding:6px 8px;
    background:var(--page); border:1px solid var(--border); border-radius:8px;
    margin-bottom:8px; font-variant-numeric:tabular-nums; }
  .empty { color:var(--muted); font-style:italic; }
  .typebar { display:flex; align-items:center; gap:8px; margin:3px 0; }
  .typebar .name { flex:0 0 110px; font-size:12px; }
  .typebar .track { flex:1; background:var(--grid); border-radius:4px; height:8px; }
  .typebar .fill { height:100%; border-radius:4px; background:var(--blue); min-width:2px; }
  .typebar .n { flex:0 0 52px; text-align:right; color:var(--ink);
    font-variant-numeric:tabular-nums; font-size:12px; }
</style></head><body>
<header>
  <h1>mtg-forge-llm-agent — run dashboard</h1>
  <span class="stamp" id="stamp">loading…</span>
</header>
<div class="grid" id="grid"><div class="card"><span class="empty">loading…</span></div></div>
<script>
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num = (x, d) => (x === null || x === undefined) ? '—' : Number(x).toLocaleString(undefined, {maximumFractionDigits: d ?? 0});
function dot(color, label) {
  return `<span class="status"><span class="dot" style="background:${color}"></span>${esc(label)}</span>`;
}
function tile(v, l) { return `<div class="tile"><div class="v">${v}</div><div class="l">${esc(l)}</div></div>`; }
function bar(frac, color) {
  const pct = Math.max(0, Math.min(100, frac * 100));
  return `<div class="bar"><div style="width:${pct}%;background:${color || 'var(--blue)'}"></div></div>`;
}
function card(title, body) { return `<div class="card"><h2>${esc(title)}</h2>${body}</div>`; }
function empty(msg) { return `<span class="empty">${esc(msg)}</span>`; }

function daggerCard(d) {
  if (!d || d.error) return card('DAgger run', empty('error: ' + (d && d.error || '?')));
  if (!d.available) return card('DAgger run', empty(d.note || 'no data'));
  const hb = d.heartbeat || {};
  const healthColor = {ok:'var(--good)', stale:'var(--warn)', unhealthy:'var(--crit)', unknown:'var(--muted)'}[d.health] || 'var(--muted)';
  const healthText = {ok:'healthy', stale:'heartbeat stale (>2 min)', unhealthy:'UNHEALTHY', unknown:'unknown'}[d.health] || d.health;
  const done = d.games_validated || 0, tgt = d.target_games || 192;
  let b = `<div style="margin-bottom:8px">${dot(healthColor, healthText)}
    <span class="mini">last beat ${esc(hb.last_ts || '—')} · rss ${esc(hb.last_rss || '—')}</span></div>`;
  b += `<div class="mini">validated games</div>${bar(done / tgt)}
    <div class="mini">${num(done)} / ${num(tgt)} (${num(100 * done / tgt, 1)}%) · ${num(d.cells_validated_ok)}/32 cells complete</div>`;
  b += `<div class="tiles" style="margin-top:10px">` +
    tile(d.current_cell ? `<span style="font-size:13px">${esc(d.current_cell)}</span>` : '—', 'current cell (attempt ' + (d.current_attempt ?? '—') + ')') +
    tile(num(d.server_starts), 'server starts') +
    tile(num(hb.restart_count), 'watchdog restarts') +
    tile(num(d.java_procs), 'java JVMs') + tile(num(d.mlx_procs), 'mlx server') + `</div>`;
  if (d.finished) b += `<div class="mini">driver reports: all cells done</div>`;
  return card('DAgger run — d4-dagger-r1', b);
}

function corpusCard(c) {
  if (!c || c.error) return card('Corpus', empty('error: ' + (c && c.error || '?')));
  if (!c.ok) return card('Corpus', empty(c.note || 'no data'));
  let b = `<div class="tiles">` +
    tile(num(c.games), 'games') + tile(num(c.decisions), 'decisions') +
    tile(num(c.labels), 'labels') + tile(num(c.label_yield_pct, 1) + '%', 'label yield') + `</div>`;
  b += `<table class="rows"><tr><td>W / L / D (identity A)</td><td>${num(c.wins)} / ${num(c.losses)} / ${num(c.draws)}</td></tr>` +
    `<tr><td>cells / files</td><td>${num(c.cells)} / ${num(c.files)}</td></tr></table>`;
  const types = Object.entries(c.by_type || {}).sort((a, z) => z[1] - a[1]);
  if (types.length) {
    const max = types[0][1];
    b += `<div class="mini" style="margin-top:8px">labels by decision type</div>`;
    for (const [name, n] of types.slice(0, 8)) {
      b += `<div class="typebar"><span class="name">${esc(name)}</span>
        <span class="track"><span class="fill" style="display:block;width:${Math.max(2, 100 * n / max)}%"></span></span>
        <span class="n">${num(n)}</span></div>`;
    }
  }
  return card('Corpus — runs/d4-dagger-r1', b);
}

function trainingCard(t) {
  if (!t || t.error) return card('Training', empty('error: ' + (t && t.error || '?')));
  if (!t.active) return card('Training', empty(t.note || 'no training active'));
  return card('Training — ' + t.log,
    `<div class="mini">updated ${num(t.age_s)}s ago</div><div class="mono">${esc(t.lines.join('\\n'))}</div>`);
}

function budgetCard(s) {
  const led = s.ledger || {}, ce = s.aws_ce || {}, budget = s.budget_monthly || 15;
  if (led.error) return card('AWS budget', empty('error: ' + led.error));
  const month = led.month || 0;
  const frac = month / budget;
  const color = frac >= 1 ? 'var(--crit)' : frac >= 0.75 ? 'var(--warn)' : 'var(--blue)';
  const flag = frac >= 1 ? ' · OVER BUDGET' : frac >= 0.75 ? ' · nearing budget' : '';
  let b = `<div class="tiles">` +
    tile('$' + num(month, 2), 'this month (ledger)') +
    tile('$' + num(led.total, 2), 'all-time (ledger)') +
    tile(ce.mtd === null || ce.mtd === undefined ? '—' : '$' + num(ce.mtd, 2), 'AWS actual MTD') + `</div>`;
  b += `<div class="mini">monthly budget $${num(budget, 2)}${flag}</div>${bar(frac, color)}`;
  if (ce.error) b += `<div class="mini">Cost Explorer: ${esc(ce.error)} (ledger-only mode)</div>`;
  else if (ce.mtd !== null && ce.mtd !== undefined) b += `<div class="mini">Cost Explorer fetched, cached ≤1h ($0.01/query)</div>`;
  else b += `<div class="mini">Cost Explorer: fetching…</div>`;
  if (led.recent && led.recent.length) {
    b += `<table class="rows" style="margin-top:8px">`;
    for (const r of led.recent) b += `<tr><td>${esc(r.what)} · ${num(r.hours, 2)}h @ $${num(r.rate, 2)}</td><td>$${num(r.cost, 2)}</td></tr>`;
    b += `</table>`;
  } else if (!led.rows) b += `<div class="mini">${esc(led.note || 'ledger empty')} — append sessions to cloud/aws_ledger.jsonl</div>`;
  return card('AWS budget', b);
}

function feedCard(f) {
  if (!f || f.error) return card('Live feed', empty('error: ' + (f && f.error || '?')));
  if (!f.ok) return card('Live feed', empty(f.note || 'no data'));
  const dots = {win:'var(--good)', loss:'var(--grid)', draw:'var(--muted)',
    unhealthy:'var(--serious)', restart:'var(--crit)', recovered:'var(--good)'};
  const hb = f.hb_status || {};
  const hbColor = hb.state === 'ok' ? 'var(--good)' : hb.state ? 'var(--serious)' : 'var(--muted)';
  let b = `<div class="hbline">${dot(hbColor, '')}<span>${esc(hb.text || 'no heartbeat data')}</span></div>`;
  if (!f.events.length) b += empty('no events yet');
  else {
    b += `<ul class="feed">`;
    for (const e of f.events) {
      b += `<li><span class="t">${esc(e.time)}</span>
        <span class="dot" style="background:${dots[e.kind] || 'var(--muted)'}"></span>
        <span class="ev-${esc(e.kind)}">${esc(e.text)}</span></li>`;
    }
    b += `</ul>`;
  }
  if (f.note) b += `<div class="mini">${esc(f.note)}</div>`;
  return card('Live feed — matches + heartbeat', b);
}

function incidentCard(d) {
  const hb = (d && d.heartbeat) || {};
  const inc = hb.incidents || [];
  let b;
  if (!d || !d.available) b = empty('no heartbeat log');
  else if (!inc.length) b = dot('var(--good)', 'no incidents in log') + `<div class="mini">${num(hb.unhealthy_count)} UNHEALTHY · ${num(hb.restart_count)} RESTART total</div>`;
  else {
    b = `<div class="mini">${num(hb.unhealthy_count)} UNHEALTHY strikes · ${num(hb.restart_count)} watchdog restarts (total in log)</div><ul class="inc">`;
    for (const i of inc) {
      const sev = i.what.startsWith('RESTART') ? 'var(--crit)' : 'var(--serious)';
      b += `<li>${dot(sev, i.ts + '  ' + i.what)}${i.rss ? ` <span class="mini">rss ${esc(i.rss)}</span>` : ''}</li>`;
    }
    b += `</ul>`;
  }
  return card('Incident watch', b);
}

async function refresh() {
  try {
    const r = await fetch('/api/status', {cache: 'no-store'});
    const s = await r.json();
    document.getElementById('stamp').textContent = 'updated ' + s.generated_at + ' · refreshes every 10s';
    document.getElementById('grid').innerHTML =
      daggerCard(s.dagger) + feedCard(s.feed) + corpusCard(s.corpus) +
      budgetCard(s) + incidentCard(s.dagger) + trainingCard(s.training);
  } catch (e) {
    document.getElementById('stamp').textContent = 'refresh failed: ' + e + ' (retrying)';
  }
}
refresh();
setInterval(refresh, 10000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/status"):
            body = json.dumps(build_status()).encode()
            ctype = "application/json"
        elif self.path in ("/", "/index.html"):
            body = PAGE.encode()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the terminal quiet
        pass


def main():
    port = int(CFG.get("port") or 8377)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("dashboard: http://localhost:%d  (Ctrl-C to stop)" % port)
    print("  runs_dir   %s" % CFG["runs_dir"])
    print("  dagger_dir %s" % CFG["dagger_dir"])
    print("  ledger     %s" % CFG["ledger"])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
