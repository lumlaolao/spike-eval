#!/usr/bin/env python3
"""Self-test for replay_match.py — fully hermetic (no GPU, no real server).

Covers the reply-parsing / matching semantics (CHOICE 1-based, PASS-as-last-
option vs logged -1, PICKS count-style, KEEP/MULLIGAN, malformed reply ->
parse-failure + non-match), split-file exclusion, deterministic sampling, and
the Wilson CI math. HTTP is exercised end-to-end against an IN-PROCESS mock
OpenAI server (http.server on localhost) that returns SCRIPTED replies keyed by
the user message — tiny and CPU-only.

Run: python3 eval/test_replay_match.py
"""

import json
import os
import threading
import http.server
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_match as rm  # noqa: E402

FAILURES = []
N_CHECKS = [0]


def check(name, cond, detail=""):
    N_CHECKS[0] += 1
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def approx(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


def dec(seat="Doomsday", dtype="play", choice=0, opts=None, n_options=None,
        prompt="STATE\n\nWhat do you do?", tier="ai-teacher", turn=1,
        phase="MAIN1", fallback=None, **extra):
    if opts is None:
        opts = ["CAST Brainstorm {U}", "CAST Ponder {U}", "PASS (do nothing)"]
    d = {"t": "decision", "seat": seat, "turn": turn, "phase": phase,
         "type": dtype, "n_options": n_options if n_options is not None else len(opts),
         "choice": choice, "tier": tier, "fallback": fallback,
         "options": opts, "prompt": prompt}
    d.update(extra)
    return d


def game_line(seed=1, swap=False, winner="A",
              deck_a="Doomsday", deck_b="Goblins",
              plan_a="Doomsday combo line", plan_b="Goblins aggro line"):
    return {"t": "game", "seed": seed, "swap": swap, "winner": winner,
            "deck_a": deck_a, "deck_b": deck_b,
            "plan_a": plan_a, "plan_b": plan_b,
            "controller_a": "ai-log", "controller_b": "ai-log",
            "smart_a": None, "smart_b": None, "turns": 6}


def write_game(path, game, decisions):
    with open(path, "w", encoding="utf-8") as f:
        for d in decisions:
            f.write(json.dumps(d) + "\n")
        if game is not None:
            f.write(json.dumps(game) + "\n")


# ------------------------------------------------------ reply-parsing unit tests

def test_strip_and_segment():
    check("stripMarkdown drops fences/bold",
          rm.strip_markdown("**CHOICE:** `2`") == "CHOICE: 2")
    check("last_segment flexible colon-optional",
          rm.last_segment_any("CHOICE 3", ["CHOICE:"]) == "3")
    check("last_segment last-wins across lines",
          rm.last_segment_any("CHOICE: 1\nCHOICE: 4", ["CHOICE:"]) == "4")
    check("last_segment None when absent",
          rm.last_segment_any("no tag here", ["PICKS:"]) is None)


def test_parse_tag_number():
    check("CHOICE 1-based number", rm.parse_tag_number("CHOICE: 3", "CHOICE:") == 3)
    check("ANSWER synonym accepted",
          rm.parse_tag_number("ANSWER: 2", "CHOICE:") == 2)
    check("first digit run wins",
          rm.parse_tag_number("CHOICE: 3 (final answer, 99% sure)", "CHOICE:") == 3)
    check("no number -> -1", rm.parse_tag_number("CHOICE: none", "CHOICE:") == -1)
    check("markdown-wrapped choice",
          rm.parse_tag_number("Reasoning...\n**CHOICE: 4**", "CHOICE:") == 4)


def test_parse_picks():
    picks, ok = rm.parse_picks("PICKS: 1,3", 4)
    check("PICKS distinct in-range", ok and picks == {1, 3})
    picks, ok = rm.parse_picks("PICKS: NONE", 4)
    check("PICKS NONE -> empty but parsed", ok and picks == set())
    picks, ok = rm.parse_picks("PICKS: 1,1,9", 4)
    check("PICKS dedup + drop out-of-range", ok and picks == {1})
    picks, ok = rm.parse_picks("I would attack with the first one.", 4)
    check("PICKS missing -> parse failure", (not ok) and picks == set())


def test_parse_mulligan():
    check("KEEP -> 0", rm.parse_mulligan("KEEP") == 0)
    check("MULLIGAN -> 1", rm.parse_mulligan("MULLIGAN") == 1)
    check("last token wins (mull after keep prose)",
          rm.parse_mulligan("I could KEEP but I will MULLIGAN") == 1)
    check("neither token -> None", rm.parse_mulligan("dunno") is None)


def test_parse_blocks():
    cnt, ok = rm.parse_blocks_count("BLOCKS: NONE")
    check("BLOCKS NONE -> 0", ok and cnt == 0)
    cnt, ok = rm.parse_blocks_count("BLOCKS: Elf->Goblin, Bear->Ogre")
    check("BLOCKS counts arrows", ok and cnt == 2)
    cnt, ok = rm.parse_blocks_count("I will not defend this turn.")
    check("BLOCKS missing -> parse failure", (not ok) and cnt == 0)


# ------------------------------------------------------ scoring / match semantics

def test_score_single_choice_1based():
    # logged choice 0 (0-based) == menu number 1.
    m, ok, _ = rm.score_reply(dec(dtype="play", choice=0), "CHOICE: 1")
    check("CHOICE 1 matches logged 0-based 0", m and ok)
    m, ok, _ = rm.score_reply(dec(dtype="play", choice=0), "CHOICE: 2")
    check("CHOICE 2 mismatches logged 0", (not m) and ok)
    m, ok, _ = rm.score_reply(dec(dtype="play", choice=1), "CHOICE: 2")
    check("CHOICE 2 matches logged 1", m and ok)


def test_score_pass_as_last_option():
    # play PASS: logged choice -1, PASS is the LAST menu option (# == n_options).
    d = dec(dtype="play", choice=-1,
            opts=["CAST A {U}", "CAST B {U}", "PASS (do nothing)"])  # n_options=3
    m, ok, _ = rm.score_reply(d, "CHOICE: 3")
    check("play PASS matches CHOICE=n_options", m and ok)
    m, ok, _ = rm.score_reply(d, "CHOICE: 1")
    check("play PASS mismatches a real option", (not m) and ok)
    check("expected_menu_number(play,-1)=n_options",
          rm.expected_menu_number(d) == 3)


def test_score_decline_non_play():
    # non-play decideOne decline: logged -1 -> menu number n_options+1.
    d = dec(dtype="zone-change", choice=-1, opts=["Top", "Bottom"])  # n_options=2
    check("expected_menu_number(non-play,-1)=n+1",
          rm.expected_menu_number(d) == 3)
    m, ok, _ = rm.score_reply(d, "CHOICE: 3")
    check("non-play decline matches CHOICE=n+1", m and ok)


def test_score_picks_count_style():
    # count types: logged choice = subset SIZE; match on cardinality.
    d = dec(dtype="attackers", choice=2, opts=["Elf(1/1)", "Bear(2/2)", "Ogre(3/3)"])
    m, ok, _ = rm.score_reply(d, "PICKS: 1,3")
    check("PICKS size 2 matches logged size 2", m and ok)
    m, ok, _ = rm.score_reply(d, "PICKS: 2")
    check("PICKS size 1 mismatches logged size 2", (not m) and ok)
    d0 = dec(dtype="attackers", choice=0, opts=["Elf(1/1)", "Bear(2/2)"])
    m, ok, _ = rm.score_reply(d0, "PICKS: NONE")
    check("PICKS NONE matches logged size 0", m and ok)


def test_score_mulligan():
    d = dec(dtype="mulligan", choice=0, opts=["KEEP", "MULLIGAN"])
    m, ok, _ = rm.score_reply(d, "KEEP")
    check("mulligan KEEP matches logged 0", m and ok)
    m, ok, _ = rm.score_reply(d, "MULLIGAN")
    check("mulligan MULLIGAN mismatches logged KEEP", (not m) and ok)
    d1 = dec(dtype="mulligan", choice=1, opts=["KEEP", "MULLIGAN"])
    m, ok, _ = rm.score_reply(d1, "MULLIGAN")
    check("mulligan MULLIGAN matches logged 1", m and ok)


def test_score_blocks_count():
    d = dec(dtype="blockers", choice=0, opts=["Elf(1/1)"])
    m, ok, _ = rm.score_reply(d, "BLOCKS: NONE")
    check("blocks NONE matches logged 0", m and ok)
    d2 = dec(dtype="blockers", choice=2, opts=["Elf(1/1)", "Bear(2/2)"])
    m, ok, _ = rm.score_reply(d2, "BLOCKS: Elf->Ogre, Bear->Troll")
    check("blocks count 2 matches logged 2", m and ok)


def test_score_malformed_parse_failure():
    d = dec(dtype="play", choice=0)
    m, ok, detail = rm.score_reply(d, "I think the best line is to cast Brainstorm.")
    check("malformed single -> non-match", not m)
    check("malformed single -> parse failure", not ok, detail)
    d2 = dec(dtype="mulligan", choice=0, opts=["KEEP", "MULLIGAN"])
    m, ok, _ = rm.score_reply(d2, "hard to say")
    check("malformed mulligan -> parse failure", (not m) and (not ok))
    d3 = dec(dtype="attackers", choice=1, opts=["Elf(1/1)", "Bear(2/2)"])
    m, ok, _ = rm.score_reply(d3, "attack with the elf")
    check("malformed picks -> parse failure", (not m) and (not ok))


# ----------------------------------------------------------- Wilson CI math

def test_wilson_ci():
    lo, hi = rm.wilson_ci(0, 0)
    check("Wilson n=0 -> (None,None)", lo is None and hi is None)
    lo, hi = rm.wilson_ci(50, 100)
    # Known Wilson 95% interval for 50/100 is ~[0.4038, 0.5962].
    check("Wilson 50/100 lo", approx(lo, 0.4038315, 1e-4), "%r" % lo)
    check("Wilson 50/100 hi", approx(hi, 0.5961685, 1e-4), "%r" % hi)
    lo, hi = rm.wilson_ci(10, 10)
    check("Wilson 10/10 hi capped <=1", hi <= 1.0 and lo < 1.0)
    lo, hi = rm.wilson_ci(0, 10)
    check("Wilson 0/10 lo floored >=0", lo >= 0.0 and hi > 0.0)


# ------------------------------------------------------ filtering / sampling

def _args(**kw):
    ns = rm.build_arg_parser().parse_args(["dummy"])
    ns.corpora = ["dummy"]
    for k, v in kw.items():
        setattr(ns, k, v)
    if ns.tiers == "":
        ns.tiers = None
    return ns


def _rows_from(tmp):
    game = game_line()
    decisions = [
        dec(dtype="play", choice=0),                       # keep
        dec(dtype="play", choice=-1, fallback="ai-choice-not-in-menu"),  # drop: fallback
        dec(dtype="confirm", choice=0, opts=["Yes"], n_options=1),        # drop: n_options<=1
        dec(dtype="mulligan", choice=1, opts=["KEEP", "MULLIGAN"]),       # keep
        dec(dtype="attackers", choice=2, opts=["A(1/1)", "B(2/2)", "C(3/3)"]),  # keep
    ]
    fp = Path(tmp) / "game-1-100.jsonl"
    write_game(fp, game, decisions)
    rows, warnings = rm.load_rows([str(tmp)])
    return rows, warnings


def test_filters_drop_degenerate():
    with tempfile.TemporaryDirectory() as tmp:
        rows, _ = _rows_from(tmp)
        kept, stats = rm.apply_filters(rows, _args(tiers="ai-teacher"))
        types = sorted(r.d.get("type") for r in kept)
        check("fallback + n_options<=1 dropped by default",
              types == ["attackers", "mulligan", "play"], str(types))
        # keep-degenerate re-admits them (still must be scoreable).
        kept2, _ = rm.apply_filters(rows, _args(keep_degenerate=True))
        check("keep-degenerate re-admits forced rows", len(kept2) >= len(kept))


def test_filter_by_type_and_deck():
    with tempfile.TemporaryDirectory() as tmp:
        rows, _ = _rows_from(tmp)
        kept, _ = rm.apply_filters(rows, _args(types="play"))
        check("type filter keeps only play",
              all(r.d.get("type") == "play" for r in kept) and len(kept) == 1)
        kept2, _ = rm.apply_filters(rows, _args(deck="Nonexistent"))
        check("deck filter with no match -> empty", len(kept2) == 0)


def test_deterministic_sampling():
    with tempfile.TemporaryDirectory() as tmp:
        game = game_line()
        decs = [dec(dtype="play", choice=0, prompt="P%d" % i) for i in range(50)]
        write_game(Path(tmp) / "game-7-1.jsonl", game, decs)
        rows, _ = rm.load_rows([str(tmp)])
        a, _ = rm.apply_filters(rows, _args(max_rows=10, sample_seed=123))
        b, _ = rm.apply_filters(rows, _args(max_rows=10, sample_seed=123))
        c, _ = rm.apply_filters(rows, _args(max_rows=10, sample_seed=999))
        ra = [r.rid() for r in a]
        rb = [r.rid() for r in b]
        rc = [r.rid() for r in c]
        check("sampling is deterministic for a seed", ra == rb)
        check("sampling honors --max-rows", len(ra) == 10)
        check("different seed -> different sample (usually)", ra != rc)


def test_split_file_exclusion():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        game_v = game_line(seed=1)
        game_t = game_line(seed=2)
        write_game(tmp / "game-1-1.jsonl", game_v, [dec(dtype="play", choice=0)])
        write_game(tmp / "game-2-2.jsonl", game_t, [dec(dtype="play", choice=0)])
        # meta.jsonl: game-1 is valid, game-2 is train.
        meta = tmp / "meta.jsonl"
        with open(meta, "w") as f:
            f.write(json.dumps({"source_file": "game-1-1.jsonl", "split": "valid"}) + "\n")
            f.write(json.dumps({"source_file": "game-2-2.jsonl", "split": "train"}) + "\n")
        rows, _ = rm.load_rows([str(tmp)])

        keep, mode = rm.read_split_files(str(meta), exclude_train=False)
        check("valid-only mode set + set", mode == "valid-only" and keep == {"game-1-1.jsonl"})
        kept, _ = rm.apply_filters(rows, _args(split_file=str(meta)))
        check("valid-only keeps only the valid game file",
              [r.source_file for r in kept] == ["game-1-1.jsonl"])

        keep2, mode2 = rm.read_split_files(str(meta), exclude_train=True)
        check("exclude-train mode set + set",
              mode2 == "exclude-train" and keep2 == {"game-2-2.jsonl"})
        kept2, _ = rm.apply_filters(
            rows, _args(split_file=str(meta), split_exclude_train=True))
        check("exclude-train drops the train game file",
              [r.source_file for r in kept2] == ["game-1-1.jsonl"])


# ------------------------------------------------- in-process mock OpenAI server

class MockHandler(http.server.BaseHTTPRequestHandler):
    SCRIPT = {}          # prompt-substring -> reply text
    DEFAULT = "CHOICE: 1"
    HEALTH = "ok"
    ERR_NEEDLES = ()     # scored requests whose user text hits one -> HTTP 500
    ERR_ALL = False      # every scored request -> HTTP 500 (health still 200)

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        user = ""
        for msg in body.get("messages", []):
            if msg.get("role") == "user":
                user = msg.get("content", "")
        max_tokens = body.get("max_tokens", 0)
        # A scored request (not the 1-token health probe) may be forced to error,
        # exercising the transport-error path without any real network failure.
        if max_tokens != 1 and (self.ERR_ALL
                                or any(nd in user for nd in self.ERR_NEEDLES)):
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        reply = self.HEALTH if max_tokens == 1 else self.DEFAULT
        if max_tokens != 1:
            for needle, out in self.SCRIPT.items():
                if needle in user:
                    reply = out
                    break
        payload = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": reply}}]
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_mock(script, default="CHOICE: 1", err_needles=(), err_all=False):
    handler = type("H", (MockHandler,),
                   {"SCRIPT": dict(script), "DEFAULT": default,
                    "ERR_NEEDLES": tuple(err_needles), "ERR_ALL": err_all})
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    endpoint = "http://127.0.0.1:%d/v1" % srv.server_address[1]
    return srv, endpoint


def test_endpoint_health_and_replay():
    # Two decisions with distinguishable prompts; the mock scripts a match for
    # one and a mismatch for the other.
    script = {
        "ROW_MATCH": "CHOICE: 1",       # logged choice 0 -> menu 1 -> match
        "ROW_MISS": "CHOICE: 2",        # logged choice 0 -> menu 1 -> mismatch
    }
    srv, endpoint = start_mock(script)
    try:
        # health probe must succeed quickly.
        health_s = rm.health_probe(endpoint, "m", timeout=5.0)
        check("health probe returns latency", health_s is not None and health_s >= 0)

        with tempfile.TemporaryDirectory() as tmp:
            game = game_line()
            decs = [
                dec(dtype="play", choice=0, prompt="ROW_MATCH state"),
                dec(dtype="play", choice=0, prompt="ROW_MISS state"),
            ]
            write_game(Path(tmp) / "game-9-9.jsonl", game, decs)
            rows, _ = rm.load_rows([str(tmp)])
            args = _args(endpoint=endpoint, model="m", workers=2,
                         mismatch_examples=5)
            kept, stats = rm.apply_filters(rows, args)
            preamble = rm.PreambleBuilder(args.java)
            results = rm.replay(kept, preamble, args)
            report = rm.build_report(results, kept, stats, args, health_s)
            check("replay scored both rows", report["n_replayed"] == 2)
            check("exactly one match", report["hits"] == 1, str(report["hits"]))
            check("match_rate 0.5", approx(report["match_rate"], 0.5))
            check("mismatch example recorded",
                  len(report["mismatch_examples"]) == 1)
            check("no transport errors", report["transport_errors"] == 0)
            check("per-type table present", "play" in report["by_type"])
    finally:
        srv.shutdown()


def test_replay_parse_failure_bucket():
    # Mock returns prose with no CHOICE line -> parse failure + non-match.
    srv, endpoint = start_mock({}, default="I would just think about it.")
    try:
        health_s = rm.health_probe(endpoint, "m", timeout=5.0)
        with tempfile.TemporaryDirectory() as tmp:
            game = game_line()
            write_game(Path(tmp) / "game-3-3.jsonl", game,
                       [dec(dtype="play", choice=0, prompt="STATE X")])
            rows, _ = rm.load_rows([str(tmp)])
            args = _args(endpoint=endpoint, model="m")
            kept, stats = rm.apply_filters(rows, args)
            preamble = rm.PreambleBuilder(args.java)
            results = rm.replay(kept, preamble, args)
            report = rm.build_report(results, kept, stats, args, health_s)
            check("parse failure counted", report["parse_failures"] == 1)
            check("parse failure is non-match", report["hits"] == 0)
    finally:
        srv.shutdown()


def test_health_probe_fails_loudly():
    # An unused port: connection refused -> health_probe raises.
    raised = False
    try:
        rm.health_probe("http://127.0.0.1:1/v1", "m", timeout=2.0)
    except Exception:
        raised = True
    check("health probe raises on dead endpoint", raised)


# ------------------------------------------------------ (1) egress gate (pure)

def test_egress_gate():
    check("localhost is local", rm.is_local_endpoint("http://localhost:8080/v1"))
    check("127.0.0.1 is local", rm.is_local_endpoint("http://127.0.0.1:9000/v1"))
    check("ipv6 [::1] is local", rm.is_local_endpoint("http://[::1]:8080/v1"))
    check("remote host is NOT local",
          not rm.is_local_endpoint("https://openrouter.ai/api/v1"))
    check("endpoint_host strips userinfo + port",
          rm.endpoint_host("http://user:pw@Example.COM:443/v1") == "example.com")
    # local always allowed with an EMPTY env (no consent needed).
    check("local allowed without consent",
          rm.egress_allowed("http://127.0.0.1:8080/v1", env={}))
    # remote refused without consent...
    check("remote refused without OPENROUTER_ALLOW_EGRESS",
          not rm.egress_allowed("https://openrouter.ai/api/v1", env={}))
    # ...and allowed once the opt-in is present.
    check("remote allowed with OPENROUTER_ALLOW_EGRESS=1",
          rm.egress_allowed("https://openrouter.ai/api/v1",
                            env={"OPENROUTER_ALLOW_EGRESS": "1"}))
    # enforce_egress_gate exits 1 for a remote host without consent (no network).
    code = None
    try:
        rm.enforce_egress_gate("https://openrouter.ai/api/v1", env={})
    except SystemExit as e:
        code = e.code
    check("enforce_egress_gate exits 1 for remote w/o consent", code == 1)
    # ...and is a no-op for a local host.
    ok = True
    try:
        rm.enforce_egress_gate("http://localhost:8080/v1", env={})
    except SystemExit:
        ok = False
    check("enforce_egress_gate allows local host", ok)
    # ...and a no-op for remote WITH consent.
    ok2 = True
    try:
        rm.enforce_egress_gate("https://openrouter.ai/api/v1",
                               env={"OPENROUTER_ALLOW_EGRESS": "1"})
    except SystemExit:
        ok2 = False
    check("enforce_egress_gate allows remote with consent", ok2)


# --------------------------------------------- (2) transport-error accounting

def test_transport_budget_threshold():
    check("25% (1/4) exceeds 20% budget", rm.transport_budget_exceeded(4, 1))
    check("exactly 20% (2/10) does NOT exceed", not rm.transport_budget_exceeded(10, 2))
    check("30% (3/10) exceeds", rm.transport_budget_exceeded(10, 3))
    check("0 attempted -> not exceeded", not rm.transport_budget_exceeded(0, 0))
    check("0 errors -> not exceeded", not rm.transport_budget_exceeded(10, 0))


def test_transport_errors_excluded_from_denominator():
    # One row is forced to a 500 -> it must leave the match denominator and the
    # Wilson CI, and be reported separately.
    srv, endpoint = start_mock({}, default="CHOICE: 1", err_needles=("BOOM",))
    try:
        health_s = rm.health_probe(endpoint, "m", timeout=5.0)
        with tempfile.TemporaryDirectory() as tmp:
            game = game_line()
            decs = [
                dec(dtype="play", choice=0, prompt="ROW_A good"),
                dec(dtype="play", choice=0, prompt="ROW_B good"),
                dec(dtype="play", choice=0, prompt="ROW_C good"),
                dec(dtype="play", choice=0, prompt="ROW_D BOOM"),   # -> 500
            ]
            write_game(Path(tmp) / "game-5-5.jsonl", game, decs)
            rows, _ = rm.load_rows([str(tmp)])
            args = _args(endpoint=endpoint, model="m", workers=2, retries=0)
            kept, stats = rm.apply_filters(rows, args)
            preamble = rm.PreambleBuilder(args.java)
            results = rm.replay(kept, preamble, args)
            report = rm.build_report(results, kept, stats, args, health_s)
            check("attempted counts every row", report["attempted"] == 4)
            check("scored excludes the transport error", report["scored"] == 3)
            check("transport_errors reported separately",
                  report["transport_errors"] == 1)
            check("denominator excludes error (3 hits / 3 scored -> 1.0)",
                  report["hits"] == 3 and approx(report["match_rate"], 1.0))
            wlo, whi = rm.wilson_ci(3, 3)   # CI is over scored, not attempted
            check("Wilson CI computed over scored not attempted",
                  approx(report["wilson95"]["lo"], wlo)
                  and approx(report["wilson95"]["hi"], whi))
            check("25% error rate trips the budget flag",
                  report["transport_budget_exceeded"] is True)
    finally:
        srv.shutdown()


def test_main_aborts_over_transport_budget():
    # Every scored request 500s -> 100% > 20% -> main exits 1, no report.
    srv, endpoint = start_mock({}, err_all=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            game = game_line()
            decs = [dec(dtype="play", choice=0, prompt="P%d" % i) for i in range(3)]
            write_game(Path(tmp) / "game-8-8.jsonl", game, decs)
            code = None
            try:
                rm.main([str(tmp), "--endpoint", endpoint, "--model", "m",
                         "--workers", "2", "--retries", "0"])
            except SystemExit as e:
                code = e.code
            check("main exits 1 when transport errors exceed budget", code == 1)
    finally:
        srv.shutdown()


# ------------------------------------------------ (3) blockers none-only default

def test_blockers_none_only_default():
    with tempfile.TemporaryDirectory() as tmp:
        game = game_line()
        decs = [
            dec(dtype="blockers", choice=0, opts=["Elf(1/1)", "Bear(2/2)"]),  # keep
            dec(dtype="blockers", choice=2, opts=["Elf(1/1)", "Bear(2/2)"]),  # drop
            dec(dtype="play", choice=0),                                      # keep
        ]
        write_game(Path(tmp) / "game-4-4.jsonl", game, decs)
        rows, _ = rm.load_rows([str(tmp)])
        kept, _ = rm.apply_filters(rows, _args())
        blk = [r for r in kept if r.d.get("type") == "blockers"]
        check("default keeps ONLY choice==0 blockers",
              len(blk) == 1 and blk[0].d.get("choice") == 0)
        check("non-blockers rows unaffected by the drop",
              any(r.d.get("type") == "play" for r in kept))
        kept2, _ = rm.apply_filters(rows, _args(keep_approx_blockers=True))
        blk2 = sorted(r.d.get("choice") for r in kept2
                      if r.d.get("type") == "blockers")
        check("--keep-approx-blockers re-admits choice>0 blockers", blk2 == [0, 2])


# --------------------------------- (4) caveats + none-only report annotation

def test_docstring_caveats():
    doc = rm.__doc__ or ""
    check("module docstring has single-shot caveat", "Single-shot" in doc)
    check("module docstring has bounded-count/truncation caveat",
          "truncat" in doc.lower())


def test_report_footer_and_none_only_annotation():
    with tempfile.TemporaryDirectory() as tmp:
        game = game_line()
        write_game(Path(tmp) / "game-6-6.jsonl", game,
                   [dec(dtype="blockers", choice=0, opts=["Elf(1/1)", "Bear(2/2)"])])
        rows, _ = rm.load_rows([str(tmp)])
        args = _args()
        kept, stats = rm.apply_filters(rows, args)
        # A synthesized scored result (no network) so we can render the report.
        results = [{"i": 0, "row": kept[0], "error": None,
                    "reply": "BLOCKS: NONE", "match": True, "parse_ok": True,
                    "detail": "blocks=0 want=0"}]
        report = rm.build_report(results, kept, stats, args, 0.01)
        text = rm.render_text(report)
        check("report footer has single-shot caveat", "Single-shot" in text)
        check("report footer has bounded-count caveat", "truncat" in text.lower())
        check("by_type marks blockers '(none-only)' when defaulted",
              "blockers (none-only)" in text)
        # Opt-in removes the annotation.
        args2 = _args(keep_approx_blockers=True)
        text2 = rm.render_text(rm.build_report(results, kept, stats, args2, 0.01))
        check("--keep-approx-blockers drops the (none-only) mark",
              "(none-only)" not in text2)


# ---------------------------------- (5) explicit empty-file resolution -> exit 1

def test_explicit_empty_file_exits():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        g = game_line(seed=1)
        # Same (seed, swap): the newer rerun wins dedup, so the older explicit
        # file resolves to zero rows.
        write_game(tmp / "game-1-100.jsonl", g, [dec(dtype="play", choice=0)])
        write_game(tmp / "game-1-200.jsonl", g, [dec(dtype="play", choice=0)])
        raised = False
        try:
            rm.load_rows([str(tmp / "game-1-100.jsonl")])
        except rm.NoRowsFromFile:
            raised = True
        check("superseded explicit file raises NoRowsFromFile", raised)
        rows, _ = rm.load_rows([str(tmp / "game-1-200.jsonl")])
        check("the winning explicit file still loads its rows", len(rows) == 1)
        code = None
        try:
            rm.main([str(tmp / "game-1-100.jsonl"), "--dry-run"])
        except SystemExit as e:
            code = e.code
        check("main exits 1 on a zero-row explicit file", code == 1)


# --------------------------------------------------------------------- runner

# ------------------------------------------------ majority-class baseline
# The 88.1%-vs-10% investigation: decision-match is accuracy over a label set
# where ~78% of targets are PASS, so a near-trivial model scores near-88.
# Every report must carry the floor it is beating.

def test_majority_class_baseline():
    import make_sft_data as _m

    class _R:
        def __init__(self, d): self.d = d

    def play(n, choice):
        return _R({"type": "play", "n_options": n, "choice": choice,
                   "options": ["CAST x"] * (n - 1) + ["PASS (do nothing)"]})

    # 8 passes (choice=-1 -> "CHOICE: n", kind='pass') + 2 real actions.
    rows = [play(3, -1)] * 8 + [play(3, 0), play(3, 1)]
    b = rm.majority_class_baseline(rows)
    check("baseline picks the pass class", b["target"] == "pass", str(b["target"]))
    check("baseline rate is the majority share", abs(b["rate"] - 0.8) < 1e-9,
          str(b["rate"]))
    check("baseline counts every scoreable row", b["n"] == 10, str(b["n"]))
    check("distribution is reported", b["distribution"].get("pass") == 8,
          str(b["distribution"]))

    # A balanced set must not report a 78%-style floor.
    rows2 = [play(3, 0)] * 5 + [play(3, -1)] * 5
    b2 = rm.majority_class_baseline(rows2)
    check("balanced labels -> 50% floor", abs(b2["rate"] - 0.5) < 1e-9, str(b2["rate"]))

    # Rows that cannot be scored contribute no floor (and don't crash).
    b3 = rm.majority_class_baseline([_R({"type": "play"})])
    check("unscoreable rows yield an empty baseline", b3["rate"] is None, str(b3))


# ==========================================================================
# AUTH + REDIRECT GATE
#
# WHAT WOULD MAKE THESE FAIL
#   * _post_chat dropping the Authorization header again (the v1 gap: the
#     egress consent was spent and the request then 401'd).
#   * a key being sent to a LOCALHOST endpoint, or reaching the report /
#     results / a log line.
#   * a missing key being discovered only after the first remote request.
#   * urllib's DEFAULT opener coming back: a permitted localhost endpoint
#     could 302 the full game snapshot to an arbitrary remote host, because
#     the egress gate only ever saw the ORIGINAL --endpoint string.
#   * the Authorization header surviving a cross-host redirect.
# ==========================================================================

class AuthEchoHandler(http.server.BaseHTTPRequestHandler):
    SEEN = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        AuthEchoHandler.SEEN.append(dict(self.headers))
        payload = json.dumps({"choices": [{"message": {
            "role": "assistant", "content": "CHOICE: 1"}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class RedirectHandler(http.server.BaseHTTPRequestHandler):
    TARGET = "https://evil.example.com/v1/chat/completions"
    SEEN = []

    def log_message(self, *a):
        pass

    def _finish_ok(self):
        payload = json.dumps({"choices": [{"message": {
            "role": "assistant", "content": "CHOICE: 3"}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        RedirectHandler.SEEN.append(("GET", self.path))
        self._finish_ok()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        RedirectHandler.SEEN.append(("POST", self.path))
        if self.path.endswith("/landed"):
            self._finish_ok()
            return
        self.send_response(302)
        self.send_header("Location", self.TARGET)
        self.send_header("Content-Length", "0")
        self.end_headers()


def _start(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def test_api_key_header_is_sent():
    AuthEchoHandler.SEEN = []
    srv, port = _start(AuthEchoHandler)
    endpoint = "http://127.0.0.1:%d/v1" % port
    try:
        rm._post_chat(endpoint, "m", [{"role": "user", "content": "x"}],
                      8, 5.0, api_key="sk-test-KEY")
        got = AuthEchoHandler.SEEN[-1]
        check("Authorization: Bearer <key> is sent when a key is given",
              got.get("Authorization") == "Bearer sk-test-KEY",
              str(got.get("Authorization")))
        check("Content-Type is still sent",
              got.get("Content-Type") == "application/json")
        rm._post_chat(endpoint, "m", [{"role": "user", "content": "x"}],
                      8, 5.0)
        check("no key -> no Authorization header at all",
              "Authorization" not in AuthEchoHandler.SEEN[-1],
              str(AuthEchoHandler.SEEN[-1]))
    finally:
        srv.shutdown()
        srv.server_close()


def test_resolve_api_key():
    with tempfile.TemporaryDirectory() as tmp:
        sec = Path(tmp) / "secrets.env"
        sec.write_text("# comment\nOPENROUTER_API_KEY=sk-from-file\n"
                       "OPENROUTER_ALLOW_EGRESS_NOT_A_FLAG=x\n",
                       encoding="utf-8")
        check("env wins over secrets.env",
              rm.resolve_api_key({"OPENROUTER_API_KEY": "sk-env"}, sec)
              == "sk-env")
        check("falls back to secrets.env",
              rm.resolve_api_key({}, sec) == "sk-from-file")
        check("blank env value falls through to the file",
              rm.resolve_api_key({"OPENROUTER_API_KEY": "   "}, sec)
              == "sk-from-file")
        check("missing everywhere -> None",
              rm.resolve_api_key({}, Path(tmp) / "nope.env") is None)
        check("reading secrets.env does not mutate os.environ",
              os.environ.get("OPENROUTER_API_KEY") != "sk-from-file")


def test_require_api_key():
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "nope.env"
        # A key is never sent to an on-box endpoint, even when one exists.
        check("localhost endpoint -> no key sent on-box",
              rm.require_api_key("http://localhost:8080/v1",
                                 {"OPENROUTER_API_KEY": "sk-env"}, empty)
              is None)
        check("remote endpoint with a key -> the key",
              rm.require_api_key("https://openrouter.ai/api/v1",
                                 {"OPENROUTER_API_KEY": "sk-env"}, empty)
              == "sk-env")
        try:
            rm.require_api_key("https://openrouter.ai/api/v1", {}, empty)
            check("remote endpoint with no key exits", False, "no SystemExit")
        except SystemExit as e:
            check("remote endpoint with no key exits 1 BEFORE any request",
                  e.code == 1, str(e.code))
        check("--no-api-key opts into an unauthenticated remote endpoint",
              rm.require_api_key("https://example.com/v1", {}, empty,
                                 allow_missing=True) is None)


def test_redirects_are_egress_rechecked():
    RedirectHandler.SEEN = []
    srv, port = _start(RedirectHandler)
    endpoint = "http://127.0.0.1:%d/v1" % port
    try:
        # A PERMITTED localhost endpoint answering 302 -> evil.example.com.
        try:
            rm._post_chat(endpoint, "m", [{"role": "user", "content": "x"}],
                          8, 5.0, api_key="sk-test-KEY")
            check("302 to a remote host is refused", False, "no exception")
        except urllib.error.HTTPError as e:
            msg = str(e)
            check("302 to a remote host is REFUSED, not followed",
                  "REFUSING to follow" in msg, msg)
            check("the refusal names the unconsented host",
                  "evil.example.com" in msg, msg)
        except urllib.error.URLError as e:
            # We got as far as trying to CONTACT the redirect target: the gate
            # is not installed and the snapshot would have left the box.
            check("302 to a remote host is REFUSED, not followed", False,
                  "the redirect was FOLLOWED off-box: %s" % e)
            check("the refusal names the unconsented host", False, str(e))
        check("nothing was re-posted anywhere after the 302",
              RedirectHandler.SEEN == [("POST", "/v1/chat/completions")],
              str(RedirectHandler.SEEN))
    finally:
        srv.shutdown()
        srv.server_close()

    # ...and a same-host redirect is still followed (we did not just break
    # every redirect).
    RedirectHandler.SEEN = []
    handler = type("H", (RedirectHandler,), {"TARGET": "/v1/landed"})
    srv, port = _start(handler)
    try:
        text = rm._post_chat("http://127.0.0.1:%d/v1" % port, "m",
                             [{"role": "user", "content": "x"}], 8, 5.0)
        check("a LOCAL redirect is still followed", text == "CHOICE: 3", text)
        check("the redirect target was actually fetched",
              any(p.endswith("/landed") for _, p in RedirectHandler.SEEN),
              str(RedirectHandler.SEEN))
    finally:
        srv.shutdown()
        srv.server_close()


def test_redirect_handler_unit():
    """redirect_request() directly: the two branches an end-to-end test
    cannot reach without granting real egress consent."""
    import http.client
    h = rm.EgressRedirectHandler()
    hdrs = http.client.HTTPMessage()

    req = urllib.request.Request("http://127.0.0.1:9/v1/chat/completions",
                                 data=b"{}",
                                 headers={"Authorization": "Bearer sk-KEY",
                                          "Content-Type": "application/json"})
    try:
        h.redirect_request(req, None, 302, "Found", hdrs,
                           "http://attacker.example/v1")
        check("non-local redirect target raises", False, "returned normally")
    except urllib.error.HTTPError as e:
        check("non-local redirect target raises HTTPError",
              "REFUSING to follow" in str(e), str(e))

    # localhost and 127.0.0.1 are BOTH local, but they are different hosts:
    # an allowed cross-host redirect must still drop the bearer token.
    new = h.redirect_request(req, None, 302, "Found", hdrs,
                             "http://localhost:9/v1/chat/completions")
    check("an allowed local redirect still returns a Request",
          new is not None)
    have_auth = any(k.lower() == "authorization" for k in new.headers)
    check("Authorization is STRIPPED across a host change", not have_auth,
          str(dict(new.headers)))

    # Same host -> the header may ride along (same origin, same key holder).
    new2 = h.redirect_request(req, None, 302, "Found", hdrs,
                              "http://127.0.0.1:9/v2/chat/completions")
    check("same-host redirect keeps the request",
          new2 is not None and new2.full_url.endswith("/v2/chat/completions"))

    check("the module default opener has the gate installed",
          any(isinstance(hh, rm.EgressRedirectHandler)
              for hh in rm._OPENER.handlers))


def test_key_never_reaches_the_report():
    with tempfile.TemporaryDirectory() as tmp:
        game = game_line()
        write_game(Path(tmp) / "game-7-7.jsonl", game,
                   [dec(dtype="play", choice=0, prompt="ROW state")])
        rows, _ = rm.load_rows([str(tmp)])
        args = _args(endpoint="http://127.0.0.1:1/v1", model="m")
        args.api_key = "sk-SECRET-KEY"
        kept, stats = rm.apply_filters(rows, args)
        results = [{"i": 0, "row": kept[0], "error": "boom", "reply": None,
                    "match": False, "parse_ok": False, "detail": "transport"}]
        report = rm.build_report(results, kept, stats, args, 0.1)
        blob = json.dumps(report)
        check("the API key is not in the report", "sk-SECRET-KEY" not in blob)
        check("only its PRESENCE is recorded",
              report["api_key_present"] is True)


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print("\n%d test groups, %d checks, %d failure(s)" % (
        len(tests), N_CHECKS[0], len(FAILURES)))
    if FAILURES:
        print("FAILED: " + ", ".join(FAILURES))
        sys.exit(1)
    print("ALL GREEN")
    sys.exit(0)


if __name__ == "__main__":
    main()
