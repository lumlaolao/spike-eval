#!/usr/bin/env python3
"""Self-test for analyze_corpus.py: synthetic JSONL fixtures + math checks.

Run: python3 eval/test_analyze_corpus.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_corpus  # noqa: E402

SCRIPT = str(Path(__file__).resolve().parent / "analyze_corpus.py")
FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ------------------------------------------------------------- fixtures

def game_line(seed=1, swap=False, winner="A", ctrl_a="llm", ctrl_b="floor"):
    return {"t": "game", "seed": seed, "swap": swap,
            "deck_a": "Identity-A", "deck_b": "Identity-B",
            "controller_a": ctrl_a, "controller_b": ctrl_b,
            "smart_a": "haiku", "smart_b": None,
            "fast_a": "haiku", "fast_b": "haiku",
            "winner": winner, "turns": 9, "win_condition": "life",
            "duration_ms": 60000}


def dec(seat="Identity-A", dtype="play", tier="smart", n_options=4, choice=1,
        latency=5000, fallback=None, prompt="P", raw_reply="R", turn=3,
        **extra):
    d = {"t": "decision", "seat": seat, "turn": turn, "phase": "MAIN1",
         "type": dtype, "n_options": n_options, "choice": choice,
         "tier": tier, "latency_ms": latency, "fallback": fallback,
         "options": ["a", "b", "c", "d"][:max(0, n_options)],
         "prompt": prompt, "raw_reply": raw_reply}
    d.update(extra)
    return d


def write_jsonl(d, name, lines):
    with open(Path(d) / name, "w") as f:
        for ln in lines:
            f.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")


# ----------------------------------------------------------------- tests

def test_normalize_fallback():
    n = analyze_corpus.normalize_fallback
    check("norm strips ->payload", n("mulligan(no-model)->KEEP") == "mulligan(no-model)")
    check("norm strips ': ' payload", n("play(no-budget): land Mountain") == "play(no-budget)")
    check("norm strips x-count", n("attackers(no-model)->all-in x3") == "attackers(no-model)")
    check("norm strips card names after arrow",
          n("discard(forced) -> Ajani, Swords") == "discard(forced)")
    check("norm strips bracketed host",
          n("choose-entity(err)->first [Guide of Souls]") == "choose-entity(err)")
    check("norm passthrough when plain", n("mulligan(bridge-down)") == "mulligan(bridge-down)")


def test_malformed_and_truncated_tolerance():
    """Junk lines, non-dict JSON, and a half-written trailing line must be
    skipped and counted; the valid lines around them must still parse."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [
            "{this is not json",
            json.dumps(["a", "list"]),
            dec(),
            game_line(),
            '{"t":"decision","seat":"Identity-A","turn":9,"phase":"MAIN',  # truncated
        ])
        r = analyze_corpus.parse_run_dir(d)
        check("malformed+truncated skipped and counted", r["n_skipped_lines"] == 3,
              str(r["n_skipped_lines"]))
        check("valid decision survived junk", len(r["decisions"]) == 1)
        check("valid game line survived junk", len(r["games"]) == 1)
        s = analyze_corpus.summarize([r])
        check("summary reports skipped", s["n_skipped_lines"] == 3)
        check("summary decision count", s["n_decisions"] == 1)


def test_missing_game_line():
    """A file with decisions but no game line counts as invalid (missing game
    line) yet its decisions still feed every metric."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-5-1000.jsonl", [dec(), dec(dtype="mulligan")])
        write_jsonl(d, "game-6-1000.jsonl", [dec(), game_line(seed=6)])
        r = analyze_corpus.parse_run_dir(d)
        check("missing game line flagged", r["missing_game_line_files"] == ["game-5-1000.jsonl"],
              str(r["missing_game_line_files"]))
        s = analyze_corpus.summarize([r])
        check("missing-game-line count", s["n_missing_game_line"] == 1)
        check("its decisions still counted", s["n_decisions"] == 3)
        check("volume sees both files", s["volume"]["games_with_decisions"] == 2)
        # llm annotation still works via the OTHER file's game line
        check("llm decisions counted across files", s["llm"]["n_llm_decisions"] == 3,
              str(s["llm"]))


def test_legacy_lines():
    """Lines missing options/prompt/raw_reply must parse, be counted as
    legacy, and not be treated as model-consulted."""
    with tempfile.TemporaryDirectory() as d:
        legacy = {"t": "decision", "seat": "Identity-A", "turn": 2,
                  "phase": "MAIN1", "type": "play", "n_options": 3,
                  "choice": 0, "tier": "smart", "latency_ms": 100,
                  "fallback": None}  # no options/prompt/raw_reply keys
        write_jsonl(d, "game-1-1000.jsonl", [legacy, dec(), game_line()])
        r = analyze_corpus.parse_run_dir(d)
        check("legacy line counted", r["n_legacy_lines"] == 1, str(r["n_legacy_lines"]))
        s = analyze_corpus.summarize([r])
        check("legacy line still in totals", s["n_decisions"] == 2)
        check("legacy line not model-consulted", s["coverage"]["model_consulted"] == 1,
              str(s["coverage"]))
        check("no crash on legacy in bias section",
              s["first_option_bias"]["play"]["model_n"] == 1,
              str(s["first_option_bias"]))


def test_first_option_bias_math():
    """4 model target lines (choices 0,0,0,1 of 4 options) -> P(first)=0.75,
    uniform baseline 0.25; 4 auto lines (choices 1,2,3,0) -> 0.25. Count-type
    lines (attackers) and choice==-1 lines must be excluded."""
    with tempfile.TemporaryDirectory() as d:
        lines = [game_line()]
        for ch in (0, 0, 0, 1):
            lines.append(dec(dtype="target", tier="smart", n_options=4, choice=ch))
        for ch in (1, 2, 3, 0):
            lines.append(dec(dtype="target", tier="auto", n_options=4, choice=ch,
                             prompt=None, raw_reply=None))
        # excluded: count semantics; decline; single option
        lines.append(dec(dtype="attackers", tier="smart", n_options=4, choice=0))
        lines.append(dec(dtype="target", tier="smart", n_options=4, choice=-1))
        lines.append(dec(dtype="target", tier="smart", n_options=1, choice=0))
        write_jsonl(d, "game-1-1000.jsonl", lines)
        s = analyze_corpus.summarize([analyze_corpus.parse_run_dir(d)])
        b = s["first_option_bias"]["target"]
        check("bias model n", b["model_n"] == 4, str(b))
        check("bias model P(first)=0.75", approx(b["model_first_rate"], 0.75), str(b))
        check("bias uniform baseline=0.25", approx(b["uniform_baseline"], 0.25), str(b))
        check("bias auto n", b["auto_n"] == 4, str(b))
        check("bias auto P(first)=0.25", approx(b["auto_first_rate"], 0.25), str(b))
        check("count-type excluded from bias", "attackers" not in s["first_option_bias"],
              str(list(s["first_option_bias"])))


def test_fallback_grouping():
    """Variable payloads (card names, counts, hosts) must collapse into one
    normalized group with a preserved raw example."""
    with tempfile.TemporaryDirectory() as d:
        lines = [game_line(),
                 dec(tier="auto", dtype="discard", prompt=None, raw_reply=None,
                     fallback="discard(forced) -> Mountain, Bolt", turn=4,
                     seat="Identity-B"),
                 dec(tier="auto", dtype="discard", prompt=None, raw_reply=None,
                     fallback="discard(forced) -> Swamp"),
                 dec(tier="auto", dtype="play", prompt=None, raw_reply=None,
                     fallback="play(no-budget): land Mountain"),
                 dec(tier="ai", dtype="choose-entity", prompt=None, raw_reply=None,
                     fallback="choose-entity(delayedReveal) [Grief]")]
        write_jsonl(d, "game-1-1000.jsonl", lines)
        s = analyze_corpus.summarize([analyze_corpus.parse_run_dir(d)])
        fb = s["fallbacks"]
        check("discard payloads grouped", fb.get("discard(forced)", {}).get("count") == 2,
              str(fb))
        check("play(no-budget) grouped", fb.get("play(no-budget)", {}).get("count") == 1,
              str(list(fb)))
        check("bracketed host stripped",
              fb.get("choose-entity(delayedReveal)", {}).get("count") == 1, str(list(fb)))
        ex = fb["discard(forced)"]["example"]
        check("example carries seat/turn/type/raw",
              ex["seat"] == "Identity-B" and ex["turn"] == 4
              and ex["type"] == "discard"
              and ex["raw"] == "discard(forced) -> Mountain, Bolt", str(ex))
        # ai audit picked up the ai line
        check("ai line audited", len(s["ai_lines"]) == 1
              and s["ai_lines"][0]["type"] == "choose-entity", str(s["ai_lines"]))


def test_llm_rates_and_unrouted():
    """ai/auto rates count only decisions of llm-identity seats; unrouted
    types are those never seen with smart/fast."""
    with tempfile.TemporaryDirectory() as d:
        lines = [game_line(),  # controller_a=llm, controller_b=floor
                 dec(seat="Identity-A", tier="smart"),
                 dec(seat="Identity-A", tier="auto", prompt=None, raw_reply=None),
                 dec(seat="Identity-A", tier="ai", dtype="number",
                     prompt=None, raw_reply=None, fallback="number(err)"),
                 dec(seat="Identity-A", tier="auto", dtype="choose-entity",
                     prompt=None, raw_reply=None),
                 # floor identity noise: must not enter llm rates
                 dec(seat="Identity-B", tier="auto", prompt=None, raw_reply=None),
                 dec(seat="Identity-B", tier="auto", prompt=None, raw_reply=None)]
        write_jsonl(d, "game-1-1000.jsonl", lines)
        s = analyze_corpus.summarize([analyze_corpus.parse_run_dir(d)])
        llm = s["llm"]
        check("llm identity detected", llm["llm_identities"] == ["Identity-A"], str(llm))
        check("llm denom excludes floor seat", llm["n_llm_decisions"] == 4, str(llm))
        check("ai rate 1/4", approx(llm["ai_rate"], 0.25), str(llm))
        check("auto rate 2/4", approx(llm["auto_rate"], 0.5), str(llm))
        check("unrouted = types with no smart/fast",
              s["unrouted_types"] == ["choose-entity", "number"],
              str(s["unrouted_types"]))
        check("routed type not unrouted", "play" not in s["unrouted_types"])


def test_combined_llm_annotation_is_per_dir():
    """Identity-A is llm in dir1 but floor in dir2: combined llm stats must
    only count dir1's Identity-A decisions (per-decision annotation)."""
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        write_jsonl(d1, "game-1-1000.jsonl", [game_line(), dec(seat="Identity-A")])
        write_jsonl(d2, "game-1-1000.jsonl",
                    [game_line(ctrl_a="floor", ctrl_b="random"),
                     dec(seat="Identity-A", tier="auto", prompt=None, raw_reply=None)])
        r1 = analyze_corpus.parse_run_dir(d1)
        r2 = analyze_corpus.parse_run_dir(d2)
        s = analyze_corpus.summarize([r1, r2])
        check("combined llm denom is per-dir annotated",
              s["llm"]["n_llm_decisions"] == 1, str(s["llm"]))
        check("combined total decisions", s["n_decisions"] == 2)


def test_latency_and_coverage():
    """Latency stats only over latency_ms>0; prompt-without-reply flags
    bridge death."""
    with tempfile.TemporaryDirectory() as d:
        lines = [game_line(),
                 dec(latency=1000), dec(latency=2000), dec(latency=3000),
                 dec(dtype="mulligan", latency=0),         # hardcoded-0 site
                 dec(latency=500, raw_reply=None)]         # bridge died
        write_jsonl(d, "game-1-1000.jsonl", lines)
        s = analyze_corpus.summarize([analyze_corpus.parse_run_dir(d)])
        lat = s["latency"]["smart"]
        check("latency excludes zeros", lat["n"] == 4, str(lat))
        check("latency p50", lat["p50_ms"] == 1000, str(lat))
        check("latency max", lat["max_ms"] == 3000, str(lat))
        c = s["coverage"]
        check("model-consulted counts prompt!=null", c["model_consulted"] == 5, str(c))
        check("full round-trip", c["full_round_trip"] == 4, str(c))
        check("bridge-death detector", c["prompt_without_reply_bridge_death"] == 1, str(c))


def test_wrong_shape_fields_never_crash():
    """Valid-JSON lines with wrong-shape fields (int tier/type, bool
    latency/choice/n_options, string n_options) must not crash any section
    and must not pollute latency/bias/no-menu numbers."""
    with tempfile.TemporaryDirectory() as d:
        lines = [game_line(),
                 dec(latency=1000),
                 dec(tier=5, latency=100),                    # int tier
                 dec(dtype=7),                                # int type
                 dec(latency=True),                           # bool latency
                 dec(dtype="target", choice=True, n_options=4),  # bool choice
                 dict(dec(choice=0), n_options=True),         # bool n_options
                 dict(dec(choice=0), n_options="5")]          # string n_options
        write_jsonl(d, "game-1-1000.jsonl", lines)
        s = analyze_corpus.summarize([analyze_corpus.parse_run_dir(d)])
        check("all grouping keys coerced to str",
              all(isinstance(k, str) for k in s["latency"])
              and all(isinstance(k, str) for k in s["type_tier"])
              and all(isinstance(k, str) for k in s["n_options"]),
              str((list(s["latency"]), list(s["type_tier"]))))
        # 5 smart lines carry a real latency; the latency=True line must be
        # dropped (would be n=6 if bool passed the guard as "1 ms")
        lat = s["latency"].get("smart", {})
        check("bool latency excluded", lat.get("n") == 5, str(s["latency"]))
        check("int tier bucketed as unknown",
              s["type_tier"].get("play", {}).get("unknown", 0) >= 1,
              str(s["type_tier"]))
        play = s["n_options"].get("play", {})
        check("string/bool n_options -> malformed, not no-menu",
              play.get("malformed_n_options") == 2 and play.get("no_menu") == 0,
              str(play))
        check("malformed n_options total", s["n_malformed_n_options"] == 2,
              str(s["n_malformed_n_options"]))
        bias = s["first_option_bias"].get("target")
        check("bool choice excluded from bias", bias is None, str(bias))
        # whole-script smoke: must exit 0, not TypeError
        p = subprocess.run([sys.executable, SCRIPT, d],
                           capture_output=True, text=True)
        check("script exit 0 on wrong-shape fields", p.returncode == 0,
              p.stderr[-300:])


def test_nonstring_deck_identity():
    """Non-string / mixed-type deck names must not crash parsing, text, or
    json; seat matching is via str() coercion."""
    with tempfile.TemporaryDirectory() as d:
        g1 = game_line()
        g1["deck_a"] = 42                       # int deck name, controller llm
        g2 = game_line(seed=2)                  # mixed str/int across lines
        write_jsonl(d, "game-1-1000.jsonl", [g1, dec(seat=42)])
        write_jsonl(d, "game-2-1000.jsonl", [g2, dec()])
        r = analyze_corpus.parse_run_dir(d)
        check("mixed deck names sorted without crash",
              r["llm_names"] == ["42", "Identity-A"], str(r["llm_names"]))
        s = analyze_corpus.summarize([r])
        check("int seat matched via str()", s["llm"]["n_llm_decisions"] == 2,
              str(s["llm"]))
        pt = subprocess.run([sys.executable, SCRIPT, d],
                            capture_output=True, text=True)
        pj = subprocess.run([sys.executable, SCRIPT, d, "--json"],
                            capture_output=True, text=True)
        check("text mode survives int deck name", pt.returncode == 0,
              pt.stderr[-300:])
        check("json mode survives int deck name", pj.returncode == 0,
              pj.stderr[-300:])


def test_bias_excludes_fallbacks_and_out_of_range():
    """Fallback-decided lines (harness-forced picks) and impossible
    choice>=n_options lines must be excluded from BOTH bias buckets; the
    out-of-range count must be surfaced."""
    with tempfile.TemporaryDirectory() as d:
        lines = [game_line()]
        for ch in (0, 1):   # genuine model choices
            lines.append(dec(dtype="target", n_options=4, choice=ch))
        # tier auto, prompt attached, raw_reply null: fallback pick, NOT model
        lines.append(dec(dtype="target", tier="auto", n_options=5, choice=0,
                         raw_reply=None,
                         fallback="target(unparsed-or-dead)->first"))
        # tier smart with fallback reason: forced pick, excluded
        lines.append(dec(dtype="target", tier="smart", n_options=4, choice=0,
                         fallback="targets(unparsed)->first"))
        # tier auto with fallback, no prompt: excluded from auto bucket too
        lines.append(dec(dtype="target", tier="auto", n_options=4, choice=0,
                         prompt=None, raw_reply=None,
                         fallback="target(no-model)->first"))
        # genuine auto line (no fallback) still counted
        lines.append(dec(dtype="target", tier="auto", n_options=4, choice=2,
                         prompt=None, raw_reply=None))
        # prompt set but bridge died (raw_reply null), no fallback string:
        # model never answered -> not a model choice
        lines.append(dec(dtype="target", tier="smart", n_options=4, choice=0,
                         raw_reply=None))
        # impossible index
        lines.append(dec(dtype="target", n_options=3, choice=7))
        write_jsonl(d, "game-1-1000.jsonl", lines)
        s = analyze_corpus.summarize([analyze_corpus.parse_run_dir(d)])
        b = s["first_option_bias"]["target"]
        check("model bucket excludes fallback/bridge-death lines",
              b["model_n"] == 2, str(b))
        check("model P(first) uncontaminated", approx(b["model_first_rate"], 0.5),
              str(b))
        check("auto bucket excludes fallback lines", b["auto_n"] == 1, str(b))
        check("out-of-range choice counted", s["n_choice_out_of_range"] == 1,
              str(s["n_choice_out_of_range"]))
        p = subprocess.run([sys.executable, SCRIPT, d],
                           capture_output=True, text=True)
        check("out-of-range warning rendered",
              "choice >= n_options" in p.stdout, p.stdout[-400:])


def test_play_uniform_baseline_excludes_pass_slot():
    """play menus reserve the last slot for PASS (logged as choice=-1), so
    the uniform baseline for play must be 1/(n-1); other types keep 1/n."""
    with tempfile.TemporaryDirectory() as d:
        lines = [game_line()]
        for ch in (0, 1, 2):  # n_options=4 -> 3 reachable slots
            lines.append(dec(dtype="play", n_options=4, choice=ch))
        lines.append(dec(dtype="target", n_options=4, choice=0))
        write_jsonl(d, "game-1-1000.jsonl", lines)
        s = analyze_corpus.summarize([analyze_corpus.parse_run_dir(d)])
        check("play baseline is 1/(n-1)",
              approx(s["first_option_bias"]["play"]["uniform_baseline"], 1 / 3),
              str(s["first_option_bias"]["play"]))
        check("target baseline stays 1/n",
              approx(s["first_option_bias"]["target"]["uniform_baseline"], 0.25),
              str(s["first_option_bias"]["target"]))
        p = subprocess.run([sys.executable, SCRIPT, d],
                           capture_output=True, text=True)
        check("play baseline note rendered", "1/(n-1)" in p.stdout,
              p.stdout[-500:])


def test_unidentifiable_model_arm_warning():
    """A scope with smart/fast lines but zero game lines (live batch,
    crashed games) must warn, not claim 'no llm arm'."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [dec(), dec(dtype="target")])
        s = analyze_corpus.summarize([analyze_corpus.parse_run_dir(d)])
        check("unattributed dir flagged in llm dict",
              s["llm"]["dirs_with_unattributed_model_lines"] == [d],
              str(s["llm"]["dirs_with_unattributed_model_lines"]))
        p = subprocess.run([sys.executable, SCRIPT, d],
                           capture_output=True, text=True)
        check("unidentifiable warning rendered",
              "model arm present but unidentifiable" in p.stdout,
              p.stdout[:600])
        check("'not applicable' suppressed",
              "rates not applicable" not in p.stdout)


def test_ai_partial_caveat_placement():
    """The ai-PARTIAL caveat must appear adjacent to ai rates in text and
    inside every scope's llm dict in json (parity with aggregate.py)."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [game_line(), dec()])
        s = analyze_corpus.summarize([analyze_corpus.parse_run_dir(d)])
        check("caveat in llm dict",
              s["llm"].get("ai_partial_caveat", "").startswith(
                  "NOTE (ai tier is PARTIAL)"), str(s["llm"].keys()))
        p = subprocess.run([sys.executable, SCRIPT, d],
                           capture_output=True, text=True)
        check("caveat rendered near ai rates",
              p.stdout.count("ai tier is PARTIAL") >= 1, p.stdout[:400])
        check("smart/fast-with-fallback count rendered",
              "smart/fast-with-fallback-reason=0" in p.stdout)


def test_duplicate_dirs_and_glob():
    """Passing the same dir twice (any spelling) must not double combined
    stats; non-game-*.jsonl files must be ignored."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [game_line(), dec(), dec()])
        write_jsonl(d, "notes.jsonl", [dec(), dec(), dec()])  # stray file
        r = analyze_corpus.parse_run_dir(d)
        check("stray non-game jsonl ignored", len(r["decisions"]) == 2
              and r["files"] == ["game-1-1000.jsonl"], str(r["files"]))
        alias = str(Path(d) / "." )  # second spelling of the same dir
        p = subprocess.run([sys.executable, SCRIPT, d, alias, "--json"],
                           capture_output=True, text=True)
        check("dup dirs exit 0", p.returncode == 0, p.stderr[-300:])
        j = json.loads(p.stdout)
        check("combined not double-counted",
              j["combined"]["n_decisions"] == 2, str(j["combined"]["n_decisions"]))
        check("dup dir warned on stderr", "duplicate run dir" in p.stderr,
              p.stderr)


def test_exit_codes_and_render():
    """aggregate.py's exit contract: 0 success, 1 usage/input error
    (non-directory), 2 data-quality failure (zero decision lines);
    text/json/markdown renders don't crash."""
    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [dec(), game_line()])
        p = subprocess.run([sys.executable, SCRIPT, d], capture_output=True, text=True)
        check("exit 0 on success", p.returncode == 0, p.stderr)
        check("text output has sections", "PLAY-TIGHTENING" in p.stdout)

        pj = subprocess.run([sys.executable, SCRIPT, d, "--json"],
                            capture_output=True, text=True)
        check("json mode exit 0", pj.returncode == 0, pj.stderr)
        try:
            j = json.loads(pj.stdout)
            ok = j["combined"]["n_decisions"] == 1
        except Exception as e:  # noqa: BLE001
            ok, j = False, str(e)
        check("json mode parses", ok, str(j)[:200])

        md = Path(d) / "report.md"
        pm = subprocess.run([sys.executable, SCRIPT, d, "--markdown", str(md)],
                            capture_output=True, text=True)
        check("markdown written", pm.returncode == 0 and md.exists()
              and "# Decision-corpus analysis" in md.read_text(), pm.stderr)

    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [game_line()])  # game line only
        p1 = subprocess.run([sys.executable, SCRIPT, d], capture_output=True, text=True)
        check("exit 2 on zero decisions", p1.returncode == 2, str(p1.returncode))
        check("zero-decision message on stderr", "no decision lines" in p1.stderr,
              p1.stderr)

    p2 = subprocess.run([sys.executable, SCRIPT, "/nonexistent-dir-xyz"],
                        capture_output=True, text=True)
    check("exit 1 on non-directory", p2.returncode == 1, str(p2.returncode))

    # drift guard: the standalone fallback caveat must stay byte-identical to
    # aggregate.py's canonical AI_PARTIAL_CAVEAT (verifier MEDIUM finding).
    import aggregate
    import analyze_corpus
    check("AI_PARTIAL fallback matches aggregate.py",
          analyze_corpus._AI_PARTIAL_FALLBACK == aggregate.AI_PARTIAL_CAVEAT)


def test_contamination_detector():
    """Verifier HIGH findings: the rate-limit contamination detector needs
    hermetic coverage (fixture + exit code) and a parity guard against the
    bridge-level isNonAnswer() list in LlmSpikeGui.java."""
    limit_msg = "You've hit your session limit · resets 3pm (America/Los_Angeles)"
    check("is_contaminated_reply catches session-limit text",
          analyze_corpus.is_contaminated_reply(limit_msg))
    check("is_contaminated_reply is case-insensitive",
          analyze_corpus.is_contaminated_reply("RATE LIMIT exceeded"))
    check("is_contaminated_reply rejects non-strings",
          not analyze_corpus.is_contaminated_reply(None)
          and not analyze_corpus.is_contaminated_reply(3))
    check("is_contaminated_reply passes real replies",
          not analyze_corpus.is_contaminated_reply("2 — attack with both"))

    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [
            game_line(),
            dec(raw_reply="1"),                       # clean model line
            dec(raw_reply=limit_msg, choice=1),       # the poisoned line
        ])
        rep = analyze_corpus.build_report([Path(d)])
        cov = rep["combined"]["coverage"]
        check("contaminated_replies counts poisoned line",
              cov["contaminated_replies"] == 1, str(cov))
        check("contamination attributed to session-limit pattern",
              cov["contaminated_by_pattern"].get(
                  "you've hit your session limit") == 1, str(cov))

        p = subprocess.run([sys.executable, SCRIPT, d],
                           capture_output=True, text=True)
        check("exit 2 on contaminated batch", p.returncode == 2,
              str(p.returncode))
        check("contamination message on stderr",
              "contaminated batch" in p.stderr, p.stderr[-300:])
        check("contamination WARNING in report body",
              "contaminated" in p.stdout, p.stdout[-500:])

    # Parity guard: the Python pattern list must stay in sync with the Java
    # bridge filter (isNonAnswer in LlmSpikeGui.java) — the analyzer is the
    # post-hoc detector for exactly the strings the bridge should reject live.
    java = (Path(__file__).resolve().parent.parent
            / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
            / "LlmSpikeGui.java")
    if java.exists():
        import re as _re
        src = java.read_text(encoding="utf-8")
        m = _re.search(r"private static boolean isNonAnswer.*?return false;",
                       src, _re.S)
        java_pats = set()
        if m:
            java_pats = {s.lower()
                         for s in _re.findall(r'u\.contains\("([^"]+)"\)',
                                              m.group(0))}
        check("pattern list matches Java isNonAnswer()",
              java_pats == set(analyze_corpus._NON_ANSWER_PATTERNS),
              "java=%s python=%s" % (sorted(java_pats),
                                     sorted(analyze_corpus._NON_ANSWER_PATTERNS)))
    else:
        check("LlmSpikeGui.java present for parity check", False, str(java))


def test_mulligan_inversion_detector():
    """Regression guard for the parser-inversion bug (overlay <4fe27fcead9c):
    a model mulligan whose recorded choice contradicts the final KEEP/MULLIGAN
    keyword in raw_reply. Would have caught the 10/31 haiku-vs-ai-2 inversions."""
    # KEEP is the last keyword but recorded as mulligan (choice=1) -> inverted
    inv = dec(dtype="mulligan", tier="smart", choice=1,
              raw_reply="No reason to mulligan this workable hand.\n\nKEEP")
    check("inversion: last=KEEP recorded mulligan", analyze_corpus.mulligan_inverted(inv))
    # MULLIGAN last, recorded keep (choice=0) -> inverted
    inv2 = dec(dtype="mulligan", tier="smart", choice=0,
               raw_reply="I could keep but it's too greedy.\n\nMULLIGAN")
    check("inversion: last=MULLIGAN recorded keep", analyze_corpus.mulligan_inverted(inv2))
    # correct keep: mentions mulligan in prose, ends KEEP, recorded keep(0)
    ok = dec(dtype="mulligan", tier="smart", choice=0,
             raw_reply="Only mulligan if it cannot function.\n\nKEEP")
    check("no inversion: prose 'mulligan' but KEEP kept", not analyze_corpus.mulligan_inverted(ok))
    # non-mulligan and fallback tiers are ignored
    check("inversion ignores non-mulligan",
          not analyze_corpus.mulligan_inverted(dec(dtype="play", choice=1, raw_reply="KEEP")))
    check("inversion ignores auto tier",
          not analyze_corpus.mulligan_inverted(dec(dtype="mulligan", tier="auto", choice=1, raw_reply="KEEP")))

    with tempfile.TemporaryDirectory() as d:
        write_jsonl(d, "game-1-1000.jsonl", [game_line(), inv, ok])
        rep = analyze_corpus.build_report([Path(d)])
        check("mulligan_inversions counted in coverage",
              rep["combined"]["coverage"]["mulligan_inversions"] == 1,
              str(rep["combined"]["coverage"]))
        p = subprocess.run([sys.executable, SCRIPT, d], capture_output=True, text=True)
        check("inversion WARNING in report", "CONTRADICTS" in p.stdout, p.stdout[-400:])
        # inversion is a WARNING, not a gate failure (pre-fix corpora legitimately have them)
        check("inversion does not fail exit code", p.returncode == 0, str(p.returncode))


def main():
    test_normalize_fallback()
    test_malformed_and_truncated_tolerance()
    test_missing_game_line()
    test_legacy_lines()
    test_first_option_bias_math()
    test_fallback_grouping()
    test_llm_rates_and_unrouted()
    test_combined_llm_annotation_is_per_dir()
    test_latency_and_coverage()
    test_wrong_shape_fields_never_crash()
    test_nonstring_deck_identity()
    test_bias_excludes_fallbacks_and_out_of_range()
    test_play_uniform_baseline_excludes_pass_slot()
    test_unidentifiable_model_arm_warning()
    test_ai_partial_caveat_placement()
    test_duplicate_dirs_and_glob()
    test_exit_codes_and_render()
    test_contamination_detector()
    test_mulligan_inversion_detector()
    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
