#!/usr/bin/env python3
"""Self-test for puzzle_run.py: synthetic fixtures, no real corpus, no network.
Run: python3 eval/test_puzzle_run.py

If eval/prompt_contract.py (t1, built concurrently) is absent, a contract-
faithful stub implementing the binding render/parse semantics from the shared
interface contract is installed into sys.modules FIRST, so these tests are
real evidence about puzzle_run's scoring either way. The stub reuses the
ported harness parsers from replay_match (never reimplements them).

WHAT WOULD MAKE THIS FAIL:
  * skip buckets: a candidate/malformed-key/attackers row silently scored (or
    landing in the wrong bucket) instead of being counted separately.
  * oracle: any off-by-one between 1-based menu numbers and 0-based option
    indices, or scoring against the wrong array, drops the oracle below 10.0.
  * garbage: an unparseable reply being laundered into a wrong Magic play
    would move puz_score_excl away from None/n=0.
  * always-pass floor: the floor GENERATOR and the analytic floor COMPUTATION
    disagreeing (the 88%-lesson gate) breaks the 1e-9 equality.
  * random floor: the observed random-policy score falling outside the
    analytic random_choice ci95 means the menu->score mapping is biased.
  * pass^k: a 3-of-4-solved position counting as a pass^k success.
  * determinism: any unseeded randomness / clock / dict-order dependence
    changes results.jsonl bytes between identical runs.
  * clustering: n_clusters not tracking source.game_file, or a
    concentration-heavy clustering failing to widen the CI.
  * egress: the openrouter backend proceeding without OPENROUTER_ALLOW_EGRESS,
    or egress_allowed misjudging a remote host.
  * pinned cluster_bootstrap_ci values drifting from the shared byte-identical
    copy in the other tracks.
"""

import contextlib
import hashlib
import http.server
import io
import json
import re
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_match as rm  # noqa: E402

USING_STUB = False


def _build_stub():
    """A prompt_contract stub implementing the binding contract semantics
    for the pieces puzzle_run uses (render/parse/parse_both/stamp/constants).
    Used ONLY when the real t1 module is not on disk yet."""
    import types
    mod = types.ModuleType("prompt_contract")
    mod.CONTRACT_VERSION = "prompt-contract.v1"
    mod.PROFILES = ("puz-v1",)
    mod.STRICTNESSES = ("strict", "lenient")
    mod.PARSER_SPEC = "parser.v1"
    mod.FAMILIES = ("single", "count", "blocks", "mulligan")
    mod.SCOREABLE_INDEX_TYPES = frozenset(("play", "mulligan"))

    def contract_sha(profile="puz-v1"):
        return hashlib.sha256(
            ("stub|%s|parser.v1" % profile).encode("utf-8")).hexdigest()[:16]

    def render(row, profile="puz-v1", strictness="strict", plan_text=None,
               seat=None):
        src = row.get("source") or {}
        prompt = row.get("prompt")
        if prompt is None:
            raise ValueError("prompt missing/None")
        options = row.get("options") or []
        n = row.get("n_options")
        if not isinstance(n, int) or isinstance(n, bool):
            n = len(options)
        if n < 2:
            raise ValueError("n_options < 2")
        dtype = src.get("decision_type") or row.get("type")
        fam = rm.decision_family(dtype)
        decline = None
        if fam == "single" and dtype != "play":
            decline = n + 1
        system = "STUB SYSTEM MESSAGE (prompt-contract.v1 test stub)"
        return {
            "contract": mod.CONTRACT_VERSION,
            "contract_sha": contract_sha(profile),
            "profile": profile, "strictness": strictness,
            "decision_type": dtype, "family": fam, "n_options": n,
            "menu_numbers": list(range(1, n + 1)),
            "decline_menu_number": decline,
            "pass_option_index": n - 1 if dtype == "play" else None,
            "plan_slug": None,
            "plan_sha": hashlib.sha256(b"").hexdigest()[:16],
            "plan_source": "unresolved",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "render_sha": hashlib.sha256(
                (system + "\x00" + prompt).encode("utf-8")).hexdigest()[:16],
        }

    def parse(reply, rendered, strictness=None):
        st = strictness or rendered["strictness"]
        fam = rendered["family"]
        n = rendered["n_options"]
        dtype = rendered["decision_type"]
        out = {"outcome": "ok", "interface_error": None, "recovered_by": None,
               "menu_number": None, "option_index": None, "choice": None,
               "picks": None, "picks_size": None, "blocks_count": None,
               "raw": reply}

        def fail(err):
            out["outcome"] = "interface_failure"
            out["interface_error"] = err
            return out

        if reply is None or not str(reply).strip():
            return fail("empty_reply")
        if fam == "mulligan":
            v = rm.parse_mulligan(reply)
            if v is None:
                return fail("no_tag")
            out["menu_number"] = v + 1
            out["option_index"] = v
            out["choice"] = v
            return out
        if fam != "single":
            return fail("unsupported_family")
        num = rm.parse_tag_number(reply, "CHOICE:")
        recovered = None
        if num == -1:
            seg = rm.last_segment_any(reply, ["CHOICE:", "ANSWER:"])
            if seg is not None:
                return fail("no_number")
            if st != "lenient":
                return fail("no_tag")
            lines = [ln for ln in str(rm.strip_markdown(reply)).splitlines()
                     if ln.strip()]
            m = re.search(r"\d+", lines[-1]) if lines else None
            if not m:
                return fail("no_tag")
            num = int(m.group())
            recovered = "bare_number"
        if num in rendered["menu_numbers"]:
            out["menu_number"] = num
            out["option_index"] = num - 1
            out["choice"] = -1 if (dtype == "play" and num == n) else num - 1
            out["recovered_by"] = recovered
            return out
        if rendered.get("decline_menu_number") is not None \
                and num == rendered["decline_menu_number"]:
            out["menu_number"] = num
            out["option_index"] = None
            out["choice"] = -1
            out["recovered_by"] = recovered
            return out
        return fail("out_of_range")

    def parse_both(reply, rendered):
        return {"strict": parse(reply, rendered, "strict"),
                "lenient": parse(reply, rendered, "lenient")}

    def stamp(obj, profile="puz-v1"):
        obj["contract"] = mod.CONTRACT_VERSION
        obj["contract_sha"] = contract_sha(profile)
        return obj

    mod.contract_sha = contract_sha
    mod.render = render
    mod.parse = parse
    mod.parse_both = parse_both
    mod.stamp = stamp
    return mod


try:
    import prompt_contract  # noqa: F401  (the real t1 module, if landed)
except ImportError:
    sys.modules["prompt_contract"] = _build_stub()
    USING_STUB = True

import bedrock  # noqa: E402
import puzzle_run  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# --------------------------------------------------------------- fixtures

def make_position(pid, game_file="game-20001-0.jsonl", category="lethal-now",
                  dtype="play", scores=None, options=None, status=None,
                  split="open", seed=20001, n_options=None):
    if options is None:
        options = ["Cast Lightning Bolt -> opponent (%s)" % pid,
                   "Cast Monastery Swiftspear",
                   "Activate Sulfuric Vortex ability",
                   "PASS (do nothing)"]
    if n_options is None:
        n_options = len(options)
    row = {
        "schema": "puzzle-position.v1",
        "id": pid,
        "category": category,
        "source": {"engine_sha": "c1c64a5", "overlay_sha": "6c3b0bd",
                   "deck_a": "Burn", "deck_b": "UR Delver",
                   "controllers": "ai/ai", "seed": seed, "swap": False,
                   "decision_index": 7, "game_file": game_file,
                   "turn": 5, "decision_type": dtype, "seat": "Burn"},
        "prompt": ("== Turn 5 MAIN1 (active: Burn) ==\n"
                   "YOU [Burn] life 18 | poison 0/10\n"
                   "DECISION: You have priority (MAIN1, your main phase). "
                   "Choose ONE action:\n  1) opt\n"
                   "Reason briefly, then end with EXACTLY: CHOICE: <number>"),
        "options": options,
        "n_options": n_options,
        "split": split,
    }
    if status is not None:
        row["status"] = status
    if scores is not None:
        row["answer_key"] = {"verified_by": "exhaustive",
                             "option_scores": scores}
    return row


def main_fixture_rows(game_files=("game-20001-0.jsonl", "game-20002-0.jsonl")):
    """6 verified play positions across 2 game_files and 2 categories, plus
    1 candidate row, 1 malformed-key row, 1 attackers row."""
    g1, g2 = game_files
    rows = [
        make_position("p1", g1, "lethal-now", scores=[0, 3, 10, 0]),
        make_position("p2", g1, "lethal-now", scores=[10, 10, 0, 0]),
        make_position("p3", g1, "graded-midgame", scores=[0, 0, 5, 10]),
        make_position("p4", g2, "graded-midgame", scores=[2, 10, 0, 0],
                      seed=20002),
        make_position("p5", g2, "lethal-now", scores=[0, 0, 0, 10],
                      seed=20002),
        make_position("p6", g2, "graded-midgame", scores=[10, 0, 4, 2],
                      seed=20002),
        make_position("cand1", g1, "lethal-now", status="candidate"),
        make_position("malf1", g2, "lethal-now", scores=[10, 0, 0]),  # len 3 != 4
        make_position("att1", g1, "forced-block", dtype="attackers",
                      scores=[10, 0, 0, 0],
                      options=["Swiftspear", "Guide", "Eidolon", "Fanatic"]),
    ]
    return rows


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def run_main(argv, env=None):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = puzzle_run.main(argv, env=env if env is not None else {})
    return rc, out.getvalue(), err.getvalue()


def run_offline(tmp, rows, extra, name):
    pos_path = write_jsonl(str(Path(tmp) / ("%s.jsonl" % name)), rows)
    out_dir = Path(tmp) / ("out-%s" % name)
    rc, out, err = run_main([pos_path, "--out", str(out_dir)] + extra)
    summary = None
    summary_path = out_dir / "summary.json"
    if summary_path.exists():
        with open(summary_path, "r", encoding="utf-8") as fh:
            summary = json.load(fh)
    return rc, summary, out_dir


# ------------------------------------------------------------------ tests

def test_skip_buckets():
    with tempfile.TemporaryDirectory() as tmp:
        rc, s, _ = run_offline(tmp, main_fixture_rows(),
                               ["--offline-policy", "oracle"], "skips")
        check("skips: exit 0", rc == 0, "rc=%s" % rc)
        check("skips: 6 positions scored", s and s["n_positions"] == 6)
        sk = s["skips"] if s else {}
        check("skips: candidate -> skipped_unverified",
              sk.get("skipped_unverified") == 1, str(sk))
        check("skips: bad key len -> skipped_malformed_key",
              sk.get("skipped_malformed_key") == 1, str(sk))
        check("skips: attackers -> skipped_unscoreable_type",
              sk.get("skipped_unscoreable_type") == 1, str(sk))
        check("skips: no silent drops",
              sk.get("skipped_split") == 0 and sk.get("skipped_render_error") == 0,
              str(sk))


def test_offline_oracle_scores_ten():
    with tempfile.TemporaryDirectory() as tmp:
        rc, s, _ = run_offline(tmp, main_fixture_rows(),
                               ["--offline-policy", "oracle", "--k", "4"],
                               "oracle")
        check("oracle: exit 0", rc == 0, "rc=%s" % rc)
        m = s["metrics"]
        check("oracle: puz_score_incl est == 10.0",
              m["puz_score_incl"]["est"] == 10.0,
              str(m["puz_score_incl"]["est"]))
        check("oracle: pass_1 == 1.0", m["pass_1"]["est"] == 1.0)
        check("oracle: pass_k == 1.0", m["pass_k"]["est"] == 1.0)
        check("oracle: matches oracle_ceiling baseline",
              abs(m["puz_score_incl"]["est"]
                  - s["baselines"]["oracle_ceiling"]["est"]) < 1e-9)
        check("oracle: norm twin is 1.0",
              abs(m["puz_score_incl_norm"]["est"] - 1.0) < 1e-9)


def test_offline_garbage_is_interface_failure_not_wrong_play():
    with tempfile.TemporaryDirectory() as tmp:
        rc, s, out_dir = run_offline(tmp, main_fixture_rows(),
                                     ["--offline-policy", "garbage"],
                                     "garbage")
        check("garbage: exit 0", rc == 0, "rc=%s" % rc)
        m = s["metrics"]
        check("garbage: interface_failure_rate 1.0",
              m["interface_failure_rate"]["est"] == 1.0)
        check("garbage: puz_score_incl 0.0",
              m["puz_score_incl"]["est"] == 0.0)
        check("garbage: puz_score_excl est None, n 0",
              m["puz_score_excl"]["est"] is None
              and m["puz_score_excl"]["n"] == 0)
        with open(out_dir / "results.jsonl", "r", encoding="utf-8") as fh:
            first = json.loads(fh.readline())
        check("garbage: row outcome interface_failure, score null",
              first["outcome"] == "interface_failure" and first["score"] is None)


def test_always_pass_matches_its_own_baseline():
    with tempfile.TemporaryDirectory() as tmp:
        rc, s, _ = run_offline(tmp, main_fixture_rows(),
                               ["--offline-policy", "always-pass"], "apass")
        check("always-pass: exit 0", rc == 0, "rc=%s" % rc)
        est = s["metrics"]["puz_score_incl"]["est"]
        floor = s["baselines"]["always_pass"]["est"]
        check("always-pass: generator == analytic floor (1e-9)",
              est is not None and floor is not None and abs(est - floor) < 1e-9,
              "est=%s floor=%s" % (est, floor))
        check("always-pass: margin over own floor is 0",
              abs(s["metrics"]["puz_score_incl"]["margin_over_always_pass"]) < 1e-9)


def test_mulligan_always_pass():
    rows = [
        make_position("mull1", "game-20003-0.jsonl", "mulligan",
                      dtype="mulligan", scores=[10, 0],
                      options=["KEEP", "MULLIGAN"], seed=20003),
        make_position("play1", "game-20004-0.jsonl", "graded-midgame",
                      scores=[10, 0, 0, 4], seed=20004),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        rc, s, _ = run_offline(tmp, rows,
                               ["--offline-policy", "always-pass"], "mull")
        check("mulligan: exit 0", rc == 0, "rc=%s" % rc)
        est = s["metrics"]["puz_score_incl"]["est"]
        check("mulligan: KEEP scored as option 0 (est 7.0)",
              est is not None and abs(est - 7.0) < 1e-9, str(est))
        check("mulligan: matches analytic always_pass floor",
              abs(est - s["baselines"]["always_pass"]["est"]) < 1e-9)
        check("mulligan: by_decision_type has mulligan and play",
              set(s["by_decision_type"]) == {"mulligan", "play"})


def test_random_policy_near_random_floor():
    with tempfile.TemporaryDirectory() as tmp:
        rc, s, _ = run_offline(tmp, main_fixture_rows(),
                               ["--offline-policy", "random", "--k", "8",
                                "--sample-seed", "20260728"], "random")
        check("random: exit 0", rc == 0, "rc=%s" % rc)
        est = s["metrics"]["puz_score_incl"]["est"]
        lo, hi = s["baselines"]["random_choice"]["ci95"]
        check("random: observed within random_choice ci95",
              lo is not None and lo <= est <= hi,
              "est=%s ci=[%s,%s]" % (est, lo, hi))
        check("random: zero interface failures",
              s["metrics"]["interface_failure_rate"]["est"] == 0.0)


def test_pass_k_is_all_k():
    rows = [
        make_position("pa", "game-20005-0.jsonl", "lethal-now",
                      scores=[10, 0, 3, 0], seed=20005),
        make_position("pb", "game-20006-0.jsonl", "lethal-now",
                      scores=[10, 0, 3, 0], seed=20006),
    ]
    fixture = []
    for k in range(1, 5):
        fixture.append({"id": "pa", "k": k, "reply": "CHOICE: 1"})
    for k in range(1, 4):
        fixture.append({"id": "pb", "k": k, "reply": "CHOICE: 1"})
    fixture.append({"id": "pb", "k": 4, "reply": "CHOICE: 2"})  # solved 3 of 4
    with tempfile.TemporaryDirectory() as tmp:
        fx = write_jsonl(str(Path(tmp) / "fixture.jsonl"), fixture)
        pos_path = write_jsonl(str(Path(tmp) / "pk.jsonl"), rows)
        out_dir = Path(tmp) / "out-pk"
        rc, _, _ = run_main([pos_path, "--out", str(out_dir), "--k", "4",
                             "--offline-fixture", fx])
        with open(out_dir / "summary.json", "r", encoding="utf-8") as fh:
            s = json.load(fh)
        check("pass_k: exit 0", rc == 0, "rc=%s" % rc)
        check("pass_k: pass_1 > 0 (7/8)",
              abs(s["metrics"]["pass_1"]["est"] - 7.0 / 8.0) < 1e-9,
              str(s["metrics"]["pass_1"]["est"]))
        check("pass_k: 3-of-4 position does not count (est 0.5, n 2)",
              s["metrics"]["pass_k"]["est"] == 0.5
              and s["metrics"]["pass_k"]["n"] == 2)
        check("pass_k: nothing excluded (all samples usable)",
              s["counters"]["pass_k_excluded"] == 0)


def test_fixture_miss_is_missing_not_wrong():
    rows = [make_position("pa", scores=[10, 0, 0, 0])]
    fixture = [{"id": "pa", "k": 1, "reply": "CHOICE: 1"}]  # k=2 missing
    with tempfile.TemporaryDirectory() as tmp:
        fx = write_jsonl(str(Path(tmp) / "fx.jsonl"), fixture)
        pos_path = write_jsonl(str(Path(tmp) / "p.jsonl"), rows)
        out_dir = Path(tmp) / "out-miss"
        rc, _, _ = run_main([pos_path, "--out", str(out_dir), "--k", "2",
                             "--offline-fixture", fx])
        with open(out_dir / "summary.json", "r", encoding="utf-8") as fh:
            s = json.load(fh)
        check("miss: exit 0", rc == 0, "rc=%s" % rc)
        check("miss: counted separately",
              s["counters"]["missing_fixture_samples"] == 1)
        check("miss: not scored as wrong (incl est 10.0 over the 1 valid)",
              s["metrics"]["puz_score_incl"]["est"] == 10.0
              and s["metrics"]["puz_score_incl"]["n"] == 1)
        check("miss: position excluded from pass_k",
              s["counters"]["pass_k_excluded"] == 1
              and s["metrics"]["pass_k"]["n"] == 0)


def test_determinism():
    with tempfile.TemporaryDirectory() as tmp:
        pos_path = write_jsonl(str(Path(tmp) / "p.jsonl"), main_fixture_rows())
        args = ["--offline-policy", "random", "--k", "3",
                "--sample-seed", "20260728",
                "--generated-utc", "20260728T000000Z"]
        out1 = Path(tmp) / "out1"
        out2 = Path(tmp) / "out2"
        rc1, _, _ = run_main([pos_path, "--out", str(out1)] + args)
        rc2, _, _ = run_main([pos_path, "--out", str(out2)] + args)
        b1 = (out1 / "results.jsonl").read_bytes()
        b2 = (out2 / "results.jsonl").read_bytes()
        check("determinism: both exit 0", rc1 == 0 and rc2 == 0)
        check("determinism: results.jsonl byte-identical", b1 == b2)
        check("determinism: summary.json byte-identical",
              (out1 / "summary.json").read_bytes()
              == (out2 / "summary.json").read_bytes())


def test_clustering_uses_game_file():
    two_games = main_fixture_rows()
    one_game = main_fixture_rows(("game-20001-0.jsonl", "game-20001-0.jsonl"))
    with tempfile.TemporaryDirectory() as tmp:
        _, s2, _ = run_offline(tmp, two_games,
                               ["--offline-policy", "always-pass"], "cl2")
        _, s1, _ = run_offline(tmp, one_game,
                               ["--offline-policy", "always-pass"], "cl1")
        check("clustering: two games -> n_clusters 2", s2["n_clusters"] == 2)
        check("clustering: one game -> n_clusters 1", s1["n_clusters"] == 1)
        check("clustering: single cluster earns no interval (se None)",
              s1["metrics"]["puz_score_incl"]["se"] is None
              and s1["metrics"]["puz_score_incl"]["ci95"] == [None, None])
    # same pooled observations, different concentration: the cluster-heavy
    # split must yield a WIDER interval than the even split.
    even = puzzle_run.cluster_bootstrap_ci([[10.0, 0.0], [10.0, 0.0]])
    heavy = puzzle_run.cluster_bootstrap_ci([[10.0, 0.0, 10.0], [0.0]])
    w_even = even["ci95"][1] - even["ci95"][0]
    w_heavy = heavy["ci95"][1] - heavy["ci95"][0]
    check("clustering: heavy concentration -> wider ci95",
          w_heavy > w_even, "even=%s heavy=%s" % (w_even, w_heavy))


def test_openrouter_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        pos_path = write_jsonl(str(Path(tmp) / "p.jsonl"), main_fixture_rows())
        out_dir = Path(tmp) / "out-or"
        # env is an explicit dict WITHOUT the flag — never set it to 1 anywhere
        rc, _, err = run_main([pos_path, "--out", str(out_dir),
                               "--backend", "openrouter"], env={})
        check("openrouter: refuses with exit 2", rc == 2, "rc=%s" % rc)
        check("openrouter: message names OPENROUTER_ALLOW_EGRESS",
              "OPENROUTER_ALLOW_EGRESS" in err)
        check("openrouter: message says REFUSING", "REFUSING" in err)
        check("openrouter: no outputs written", not out_dir.exists())


def test_http_remote_refused_without_consent():
    check("egress: remote host False with empty env",
          rm.egress_allowed("https://openrouter.ai/api/v1", env={}) is False)
    check("egress: remote host True with explicit consent dict",
          rm.egress_allowed("https://openrouter.ai/api/v1",
                            env={"OPENROUTER_ALLOW_EGRESS": "1"}) is True)
    check("egress: localhost always allowed",
          rm.egress_allowed("http://localhost:8080/v1", env={}) is True)
    with tempfile.TemporaryDirectory() as tmp:
        pos_path = write_jsonl(str(Path(tmp) / "p.jsonl"), main_fixture_rows())
        rc, _, err = run_main([pos_path, "--out", str(Path(tmp) / "o"),
                               "--backend", "http",
                               "--endpoint", "https://openrouter.ai/api/v1"],
                              env={})
        check("egress: http backend refuses remote endpoint, exit 2",
              rc == 2 and "REFUSING" in err, "rc=%s" % rc)


def test_cluster_bootstrap_pinned():
    """The mandated pinned reference — detects the per-track byte-identical
    copies of cluster_bootstrap_ci drifting apart."""
    fixture = [[1.0, 1.0, 1.0, 0.0], [0.0, 0.0], [1.0, 0.0, 1.0], [1.0],
               [0.0, 0.0, 0.0, 1.0, 1.0]]
    r = puzzle_run.cluster_bootstrap_ci(fixture)
    check("pinned: n/n_clusters", r["n"] == 15 and r["n_clusters"] == 5)
    check("pinned: est", abs(r["est"] - 0.5333333333333333) < 1e-12)
    check("pinned: se", abs(r["se"] - 0.11691903702304797) < 1e-9,
          str(r["se"]))
    check("pinned: ci lo", abs(r["ci95"][0] - 0.3) < 1e-12, str(r["ci95"]))
    check("pinned: ci hi", abs(r["ci95"][1] - 0.7692307692307693) < 1e-12,
          str(r["ci95"]))
    check("pinned: single cluster se None",
          puzzle_run.cluster_bootstrap_ci([[1.0, 0.0]])["se"] is None)
    check("pinned: empty est None",
          puzzle_run.cluster_bootstrap_ci([])["est"] is None)


def test_summary_has_all_baselines():
    with tempfile.TemporaryDirectory() as tmp:
        rc, s, _ = run_offline(tmp, main_fixture_rows(),
                               ["--offline-policy", "first"], "base")
        check("baselines: exit 0", rc == 0, "rc=%s" % rc)
        b = s["baselines"]
        for key in ("random_choice", "always_pass", "majority_class",
                    "oracle_ceiling"):
            check("baselines: %s present with est/n" % key,
                  key in b and "est" in b[key] and "n" in b[key])
        check("baselines: random_choice pass_1/pass_k twins",
              "pass_1" in b["random_choice"] and "pass_k" in b["random_choice"])
        check("baselines: majority_class token reported",
              b["majority_class"].get("token") is not None)
        m = s["metrics"]
        for key in ("puz_score_incl", "puz_score_excl", "pass_1", "pass_k",
                    "interface_failure_rate", "transport_error_rate",
                    "declined_slot_rate"):
            blk = m.get(key)
            ok = (isinstance(blk, dict) and "est" in blk and "se" in blk
                  and "ci95" in blk and "n" in blk and "method" in blk)
            check("metrics: %s is a full metric block" % key, ok, str(blk))
        for key in ("puz_score_incl", "puz_score_excl", "pass_1", "pass_k"):
            has_margin = any(mk.startswith("margin_over_") for mk in m[key])
            check("metrics: %s has a margin_over_* twin" % key, has_margin)
        check("summary: 3.5 answer-key caveat present",
              any("single-deal" in c for c in s["caveats"]))
        check("summary: seed_blocks via seed_policy",
              s["seed_blocks"].get("puzzle-mining") == 6, str(s["seed_blocks"]))
        check("summary: stamped with contract + contract_sha",
              s.get("contract") == "prompt-contract.v1"
              and isinstance(s.get("contract_sha"), str)
              and len(s["contract_sha"]) == 16)
        check("summary: by_category has both categories",
              set(s["by_category"]) == {"lethal-now", "graded-midgame"})


# ======================================================================
# STREAMING SELECTION + THE DROPPED SOURCE ROW
#
# WHAT WOULD MAKE THESE FAIL
#   * read_jsonl-ing the whole corpus again (or keeping every selected row
#     when --max-positions caps the run): the weakref census below goes from
#     max_positions to the corpus size.
#   * the bounded heap picking a DIFFERENT set, or a different order, than the
#     old sort-then-slice: the equivalence check compares ids element-wise, so
#     a changed sample is a changed benchmark and must never happen silently.
#   * prep_position keeping pos["row"] again: the census fails, and so does
#     the explicit no-row assertion.
# ======================================================================

class _Row(dict):
    """A dict that supports weakref, so the test can COUNT what is retained."""


def _valid_rows(n, seed=20002):
    return [make_position("s%03d" % i, "game-%d-0.jsonl" % (i % 4),
                          "lethal-now", scores=[0, 3, 10, 0], seed=seed + i)
            for i in range(n)]


def test_selection_is_streamed_and_bounded():
    import gc
    import weakref

    rows = _valid_rows(120)
    cap = 7
    seed = 20260728

    # 1. EQUIVALENCE: the bounded heap must select exactly what the old
    #    materialize-sort-slice selected, in the same corpus order.
    expected_ids = [r["id"] for r in sorted(
        rows, key=lambda r: puzzle_run._sample_hash(seed, str(r.get("id"))))
        [:cap]]
    expected_in_corpus_order = [r["id"] for r in rows
                                if r["id"] in set(expected_ids)]
    warnings = []
    got, skips = puzzle_run.select_positions(iter(rows), "all", None, cap,
                                             seed, warnings)
    check("bounded selection keeps exactly --max-positions rows",
          len(got) == cap, str(len(got)))
    check("bounded selection picks the SAME set as sort-then-slice",
          sorted(r["id"] for r in got) == sorted(expected_ids),
          str([r["id"] for r in got]))
    check("bounded selection preserves corpus order",
          [r["id"] for r in got] == expected_in_corpus_order,
          str([r["id"] for r in got]))
    check("skipped_sampled_out counts everything dropped",
          skips["skipped_sampled_out"] == 120 - cap,
          str(skips["skipped_sampled_out"]))

    # 2. MEMORY: PEAK retention is O(selected), not O(corpus).
    #    Measured DURING the stream, not after: the old sort-then-slice
    #    version also ends up holding only `cap` rows once it returns (the
    #    filtered list is rebound), so a post-hoc census proves nothing. What
    #    is actually being fixed is the high-water mark while the corpus is
    #    being read, so that is what is sampled here.
    refs = []
    peak = [0]

    def stream(n):
        for i in range(n):
            wrapped = _Row(make_position(
                "s%03d" % i, "game-%d-0.jsonl" % (i % 4), "lethal-now",
                scores=[0, 3, 10, 0], seed=20002 + i))
            refs.append(weakref.ref(wrapped))
            yield wrapped
            # `wrapped` and the consumer's loop variable are still live here;
            # everything EARLIER should already be gone unless it is retained.
            del wrapped
            peak[0] = max(peak[0], sum(1 for r in refs if r() is not None))

    got2, _ = puzzle_run.select_positions(stream(120), "all", None, cap, seed,
                                          warnings)
    check("peak retention stays O(selected) while 120 rows stream past",
          peak[0] <= cap + 2,
          "peak %d rows held at once (expected <= %d)" % (peak[0], cap + 2))
    gc.collect()
    alive = sum(1 for ref in refs if ref() is not None)
    check("120 rows streamed, only max_positions retained at the end",
          alive == cap, "%d rows still alive (expected %d)" % (alive, cap))
    check("streamed selection matches the list-fed selection",
          [r["id"] for r in got2] == [r["id"] for r in got])

    # 3. Uncapped selection keeps everything (no silent truncation).
    got3, skips3 = puzzle_run.select_positions(iter(rows), "all", None, None,
                                               seed, warnings)
    check("no --max-positions -> every scoreable row kept", len(got3) == 120)
    check("no --max-positions -> nothing sampled out",
          skips3["skipped_sampled_out"] == 0)

    # 4. select_positions must accept a bare generator (proof it streams).
    gen = (r for r in rows)
    got4, _ = puzzle_run.select_positions(gen, "all", None, 3, seed, warnings)
    check("select_positions consumes a generator", len(got4) == 3)


def test_position_drops_the_source_row():
    import gc
    import weakref

    row = _Row(make_position("keepme", "game-1-0.jsonl", "lethal-now",
                             scores=[0, 3, 10, 0]))
    ref = weakref.ref(row)
    skips = {"skipped_render_error": 0}
    warnings = []
    pos = puzzle_run.prep_position(row, "strict", skips, warnings)
    check("prep_position renders", pos is not None)
    check("the position does NOT carry the source row",
          "row" not in pos, str(sorted(pos)))
    # Everything the run needs downstream must already be extracted.
    for key in ("id", "category", "split", "game_file", "seed", "dtype",
                "family", "options", "n_options", "menu", "scores",
                "key_caveats", "engine_sha", "overlay_sha", "rendered"):
        check("position carries %r without the row" % key, key in pos,
              str(sorted(pos)))
    check("scores were extracted, not aliased through the row",
          pos["scores"] == [0, 3, 10, 0], str(pos["scores"]))
    del row
    gc.collect()
    check("the source row is collectable once prepped", ref() is None)


# ======================================================================
# AUTH + THE BEDROCK BACKEND
# (No test here ever grants egress consent; the bedrock gate is exercised
# by its REFUSAL, and the key plumbing against a localhost mock.)
# ======================================================================

class _AuthEcho(http.server.BaseHTTPRequestHandler):
    SEEN = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        _AuthEcho.SEEN.append(dict(self.headers))
        payload = json.dumps({"choices": [{"message": {
            "role": "assistant", "content": "CHOICE: 1"}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_http_call_sends_the_api_key():
    _AuthEcho.SEEN = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _AuthEcho)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    endpoint = "http://127.0.0.1:%d/v1" % srv.server_address[1]
    try:
        reply, err, _ = puzzle_run.http_call(
            endpoint, "m", [{"role": "user", "content": "x"}], 8, 5.0, 1,
            api_key="sk-test-KEY")
        check("http_call succeeds", err is None and reply == "CHOICE: 1",
              str(err))
        check("http_call forwards the bearer token",
              _AuthEcho.SEEN[-1].get("Authorization") == "Bearer sk-test-KEY",
              str(_AuthEcho.SEEN[-1].get("Authorization")))
        puzzle_run.http_call(endpoint, "m",
                             [{"role": "user", "content": "x"}], 8, 5.0, 1)
        check("no key -> no Authorization header",
              "Authorization" not in _AuthEcho.SEEN[-1],
              str(_AuthEcho.SEEN[-1]))
        # A transport error must not carry the key into the error string.
        _, err2, _ = puzzle_run.http_call("http://127.0.0.1:1/v1", "m", [],
                                          8, 0.5, 1, api_key="sk-test-KEY")
        check("a transport error never echoes the key",
              err2 is not None and "sk-test-KEY" not in err2, str(err2))
    finally:
        srv.shutdown()
        srv.server_close()


class _TruncEcho(http.server.BaseHTTPRequestHandler):
    """finish_reason='length' + empty content below CUTOFF max_tokens; a
    valid 'CHOICE: 1' above. The reply-shape of a reasoning model whose cap
    was eaten by hidden thinking — the kimi-k3 failure mode."""
    CUTOFF = 16
    CALLS = []          # max_tokens per request, in order
    SMALL_CONTENT = ""  # what a below-cutoff request returns

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        mt = body.get("max_tokens")
        _TruncEcho.CALLS.append(mt)
        if mt is not None and mt < _TruncEcho.CUTOFF:
            payload = {"choices": [{"message": {
                "role": "assistant", "content": _TruncEcho.SMALL_CONTENT},
                "finish_reason": "length"}]}
        else:
            payload = {"choices": [{"message": {
                "role": "assistant", "content": "CHOICE: 1"},
                "finish_reason": "stop"}]}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_truncation_retry_ladder():
    """The ported Java ladder: finish_reason=length with no decision tag ->
    ONE retry at 2x tokens, adopted; a truncated reply that already carries a
    tag does NOT retry; '...CHOICE: ' with no digit DOES (the bare-substring
    bug the Java side already paid for)."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _TruncEcho)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    endpoint = "http://127.0.0.1:%d/v1" % srv.server_address[1]
    msgs = [{"role": "user", "content": "x"}]
    try:
        # (i) empty truncated reply -> retry fires at 2x and is adopted
        _TruncEcho.CALLS, _TruncEcho.SMALL_CONTENT = [], ""
        counters = {}
        reply, err, _ = puzzle_run.http_call(endpoint, "m", msgs, 8, 5.0, 1,
                                             counters=counters)
        check("trunc: retry fired and was adopted",
              err is None and reply == "CHOICE: 1", "%r %r" % (reply, err))
        check("trunc: exactly one retry, at doubled tokens",
              _TruncEcho.CALLS == [8, 16], str(_TruncEcho.CALLS))
        check("trunc: counters recorded fire + adoption",
              counters.get("truncation_retries") == 1
              and counters.get("truncation_retries_adopted") == 1,
              str(counters))
        # (ii) truncated reply ALREADY carrying a tag -> no retry
        _TruncEcho.CALLS, _TruncEcho.SMALL_CONTENT = [], "CHOICE: 2"
        counters = {}
        reply, err, _ = puzzle_run.http_call(endpoint, "m", msgs, 8, 5.0, 1,
                                             counters=counters)
        check("trunc: tagged reply kept without retry",
              reply == "CHOICE: 2" and _TruncEcho.CALLS == [8],
              "%r %s" % (reply, _TruncEcho.CALLS))
        check("trunc: no counter movement on tagged reply",
              counters.get("truncation_retries", 0) == 0, str(counters))
        # (iii) '...CHOICE: ' with NO digit -> retries (digit rule)
        _TruncEcho.CALLS, _TruncEcho.SMALL_CONTENT = [], "I pick CHOICE: "
        counters = {}
        reply, err, _ = puzzle_run.http_call(endpoint, "m", msgs, 8, 5.0, 1,
                                             counters=counters)
        check("trunc: digit-less tag retries and adopts",
              reply == "CHOICE: 1" and _TruncEcho.CALLS == [8, 16],
              "%r %s" % (reply, _TruncEcho.CALLS))
    finally:
        srv.shutdown()
        srv.server_close()


def test_truncation_counters_and_max_tokens_reach_summary():
    """(iv) the ladder's tallies and the run's max_tokens are provenance:
    they must land in summary.json for a real http run."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _TruncEcho)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    endpoint = "http://127.0.0.1:%d/v1" % srv.server_address[1]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pos_path = write_jsonl(str(Path(tmp) / "p.jsonl"),
                                   main_fixture_rows())
            out_dir = Path(tmp) / "out-trunc"
            _TruncEcho.CALLS, _TruncEcho.SMALL_CONTENT = [], ""
            rc, _, err = run_main([pos_path, "--out", str(out_dir),
                                   "--backend", "http",
                                   "--endpoint", endpoint,
                                   "--model", "m", "--max-tokens", "8"])
            check("trunc summary: run ok", rc == 0, "rc=%s %s" % (rc, err))
            s = json.loads((out_dir / "summary.json")
                           .read_text(encoding="utf-8"))
            check("trunc summary: max_tokens recorded",
                  s.get("max_tokens") == 8, str(s.get("max_tokens")))
            check("trunc summary: ladder state recorded",
                  (s.get("truncation_ladder") or {}).get("enabled") is True,
                  str(s.get("truncation_ladder")))
            c = s.get("counters") or {}
            check("trunc summary: retries counted",
                  c.get("truncation_retries", 0) >= 1
                  and c.get("truncation_retries_adopted", 0) >= 1,
                  str({k: v for k, v in c.items() if "trunc" in k}))
    finally:
        srv.shutdown()
        srv.server_close()


def test_bedrock_backend_refuses_without_consent():
    with tempfile.TemporaryDirectory() as tmp:
        pos_path = write_jsonl(str(Path(tmp) / "p.jsonl"), main_fixture_rows())
        out_dir = Path(tmp) / "out-br"
        # Explicit env dict WITHOUT the flag. Note the env also has no AWS
        # credentials: the refusal must come from the GATE, before any
        # credential resolution, so it fires anyway.
        rc, _, err = run_main([pos_path, "--out", str(out_dir),
                               "--backend", "bedrock",
                               "--model", "anthropic.claude-opus-5"], env={})
        check("bedrock: refuses with exit 2", rc == 2, "rc=%s" % rc)
        check("bedrock: refusal names the SHARED consent flag",
              "OPENROUTER_ALLOW_EGRESS" in err, err)
        check("bedrock: refusal names the bedrock host",
              "bedrock-runtime" in err, err)
        check("bedrock: says holding a key is not consent",
              "not consent" in err, err)
        check("bedrock: no outputs written", not out_dir.exists())


def test_bedrock_call_reports_errors_without_leaking():
    """bedrock_call must turn a failure into a transport error string (never
    raise, never carry secret material) and flag the empty-reply config bug."""
    warnings = []
    creds = bedrock.Credentials("AKIDEXAMPLE", "SECRET-KEY-VALUE", "TOKENVAL",
                                "test")
    cfg = bedrock.ModelCfg(max_tokens=64, timeout_s=1, retries=0)
    reply, err, _ = puzzle_run.bedrock_call(
        "anthropic.claude-opus-5", [{"role": "user", "content": "x"}],
        "us-east-2", creds, cfg, {}, warnings, "p1")
    check("no egress consent -> bedrock_call returns an error, not a reply",
          reply is None and err is not None, str(err))
    check("the error is the egress refusal", "EgressRefused" in err, err)
    check("the error carries no secret key", "SECRET-KEY-VALUE" not in err)
    check("the error carries no session token", "TOKENVAL" not in err)


def test_zero_scoreable_exits_2():
    rows = [make_position("cand1", status="candidate")]
    with tempfile.TemporaryDirectory() as tmp:
        rc, s, _ = run_offline(tmp, rows, ["--offline-policy", "first"],
                               "zero")
        check("zero: exit 2 when nothing scoreable", rc == 2, "rc=%s" % rc)
        check("zero: no summary written", s is None)


def main():
    if USING_STUB:
        print("[note] prompt_contract.py not present; using in-test "
              "contract-faithful stub")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print("--- %s ---" % t.__name__)
        t()
    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
