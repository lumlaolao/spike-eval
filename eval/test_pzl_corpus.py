#!/usr/bin/env python3
"""Tests for eval/pzl_corpus.py — the Forge .pzl puzzle-corpus reader.

Most of these are regressions for defects that were live in the first draft
and were each silently DISCARDING PUZZLES rather than crashing, which is the
dangerous failure mode for a corpus reader: a smaller suite still looks
healthy. Specifically:

  * reading only the first `Name:` of a Forge card script rejected 35 puzzles
    that reference a split half or a DFC back face (329 usable -> 361);
  * exact-case / exact-diacritic matching rejected 3 more ('Lim-Dûl's Cohort',
    'Bartolomé del Presidio', 'Thraben foulbloods');
  * splitting metadata on every colon truncated every URL and Description.

Run: python3 eval/test_pzl_corpus.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pzl_corpus as P   # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FAILURES = []


def check(name, cond, detail=""):
    print("[%s] %s%s" % ("ok" if cond else "FAIL", name,
                         (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


SAMPLE = """[metadata]
Name:Possibility Storm - Outlaws of Thunder Junction #03
URL:https://i0.wp.com/www.possibilitystorm.com/wp-content/uploads/2024/05/latest-scaled.jpg?ssl=1
Goal:Win
Turns:1
Difficulty:Rare
Description:Win this turn. Watch out: they can respond at any point.
[state]
turn=1
activeplayer=p0
activephase=MAIN1
p0life=20
p0hand=Epic Confrontation;Gold Rush
p0battlefield=Stoic Sphinx;Ornery Tumblewagg|Counters:P1P1=2;t:Treasure;Botanical Sanctum|Tapped
p1life=10
p1battlefield=Harvester of Misery;Swamp
"""


def _write(tmp, text, name="T1.pzl"):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_parse_keeps_colons_in_values():
    """Metadata is Key:Value but URLs and prose contain colons. Splitting on
    every colon silently truncated 'https' -> the rest, which also destroyed
    the only date any .pzl carries."""
    with tempfile.TemporaryDirectory() as tmp:
        puz = P.parse_pzl(_write(tmp, SAMPLE))
    meta = puz["metadata"]
    check("url_survives_split", meta["URL"].startswith("https://i0.wp.com"),
          repr(meta.get("URL"))[:60])
    check("url_keeps_query", meta["URL"].endswith("?ssl=1"))
    check("description_keeps_colon", "Watch out:" in meta["Description"],
          repr(meta.get("Description")))
    check("state_lowercased", "p0battlefield" in puz["state"])
    check("goal_and_turns", meta["Goal"] == "Win" and meta["Turns"] == "1")


def test_strip_attrs_and_tokens():
    check("counters_stripped",
          P._strip_attrs("Ornery Tumblewagg|Counters:P1P1=2") == "Ornery Tumblewagg")
    check("tapped_stripped", P._strip_attrs("Botanical Sanctum|Tapped") == "Botanical Sanctum")
    check("plain_name", P._strip_attrs("Swamp") == "Swamp")
    # Tokens name no database card; validating them would report false gaps.
    check("token_ignored", P._strip_attrs("t:Treasure") is None)
    check("empty_ignored", P._strip_attrs("   ") is None)


def test_card_refs_skips_tokens_and_nonzones():
    with tempfile.TemporaryDirectory() as tmp:
        puz = P.parse_pzl(_write(tmp, SAMPLE))
    refs = P.card_refs(puz)
    check("zones_found", set(refs) == {"p0hand", "p0battlefield", "p1battlefield"},
          str(sorted(refs)))
    check("token_excluded", "Treasure" not in P.all_card_names(puz))
    check("nonzone_keys_excluded",
          not any(k in refs for k in ("turn", "p0life", "activephase")))
    # 2 hand + 3 battlefield (the 4th entry is a token) + 2 opponent = 7.
    check("counts_cards", len(P.all_card_names(puz)) == 7,
          str(P.all_card_names(puz)))


def test_fold_handles_diacritics_but_not_typos():
    check("fold_diacritic", P.fold("Lim-Dûl's Cohort") == P.fold("Lim-Dul's Cohort"))
    check("fold_accent", P.fold("Bartolomé del Presidio") == P.fold("Bartolome del Presidio"))
    check("fold_case", P.fold("Thraben foulbloods") == P.fold("Thraben Foulbloods"))
    # A real misspelling must still NOT resolve — Forge cannot load it either,
    # so the puzzle is genuinely broken and must stay rejected.
    check("fold_rejects_typo", P.fold("Hematatite Talisman") != P.fold("Hematite Talisman"))


def test_index_reads_every_face_of_a_script():
    """THE 35-puzzle regression. A Forge script declares every face in one
    file, so `Name:` appears more than once."""
    with tempfile.TemporaryDirectory() as tmp:
        cf = os.path.join(tmp, "forge-gui", "res", "cardsfolder", "d")
        os.makedirs(cf)
        with open(os.path.join(cf, "dusk_dawn.txt"), "w", encoding="utf-8") as fh:
            fh.write("Name:Dusk\nManaCost:2 W W\nTypes:Sorcery\n"
                     "AlternateMode:Split\nName:Dawn\nManaCost:3 W W\n")
        with open(os.path.join(cf, "delver.txt"), "w", encoding="utf-8") as fh:
            fh.write("Name:Delver of Secrets\nTypes:Creature\n"
                     "AlternateMode:DoubleFaced\nName:Insectile Aberration\n")
        idx = P.scripted_card_index(tmp)
    check("first_face", P.fold("Dusk") in idx)
    check("second_face_of_split", P.fold("Dawn") in idx)
    check("joined_split_name", P.fold("Dusk // Dawn") in idx,
          "joined form must be synthesized for split cards")
    check("dfc_back_face", P.fold("Insectile Aberration") in idx)
    check("canonical_spelling_returned", idx[P.fold("dusk")] == "Dusk")


def test_validate_rejects_only_unusable_puzzles():
    idx = {P.fold(n): n for n in
           ("Epic Confrontation", "Gold Rush", "Stoic Sphinx",
            "Ornery Tumblewagg", "Botanical Sanctum", "Harvester of Misery",
            "Swamp")}
    with tempfile.TemporaryDirectory() as tmp:
        good = P.parse_pzl(_write(tmp, SAMPLE))
        check("good_puzzle_passes", P.validate(good, idx) == [],
              str(P.validate(good, idx)))

        unbounded = P.parse_pzl(_write(tmp, SAMPLE.replace("Turns:1", "Turns:9999"), "T2.pzl"))
        check("unbounded_turns_rejected",
              any("unbounded" in p for p in P.validate(unbounded, idx)))

        noopp = P.parse_pzl(_write(tmp, SAMPLE.replace("p1life=10", "p1poison=0"), "T3.pzl"))
        check("missing_opp_life_rejected",
              any("opponent life" in p for p in P.validate(noopp, idx)))

        typo = P.parse_pzl(_write(tmp, SAMPLE.replace("Swamp", "Swampp"), "T4.pzl"))
        check("unscripted_card_rejected",
              any("unscripted" in p for p in P.validate(typo, idx)))

        # "Deal as much damage as possible" encoded as Goal:Win is
        # UNSCOREABLE: the engine checks only "did you win", which one point
        # of damage can satisfy while missing the whole challenge. A reviewer
        # caught PS_GRND; the sweep then found 5, and one of them (PS_AKH4)
        # is rated Common — so filtering on Difficulty:Special would have
        # missed it. Match the objective, not the label.
        maxi = P.parse_pzl(_write(tmp, SAMPLE.replace(
            "Description:Win this turn. Watch out: they can respond at any point.",
            "Description:Deal as much damage as possible!"), "T7.pzl"))
        check("maximize_objective_rejected",
              any("unscoreable" in p for p in P.validate(maxi, idx)),
              str(P.validate(maxi, idx)))
        # ...but a puzzle that merely says "highest mana value ... is 4" is a
        # clarification, not a maximization objective, and must be kept.
        clar = P.parse_pzl(_write(tmp, SAMPLE.replace(
            "Watch out: they can respond at any point.",
            "The highest mana value among cards in exile is 4."), "T8.pzl"))
        check("clarifying_superlative_kept", P.validate(clar, idx) == [],
              str(P.validate(clar, idx)))

        # Two turns is still a bounded decision and must NOT be rejected.
        two = P.parse_pzl(_write(tmp, SAMPLE.replace("Turns:1", "Turns:2"), "T5.pzl"))
        check("turns_2_allowed", P.validate(two, idx) == [], str(P.validate(two, idx)))


def test_miscased_reports_without_rejecting():
    idx = {P.fold("Thraben Foulbloods"): "Thraben Foulbloods"}
    with tempfile.TemporaryDirectory() as tmp:
        puz = P.parse_pzl(_write(
            tmp, "[metadata]\nGoal:Win\nTurns:1\n[state]\np1life=3\n"
                 "p0battlefield=Thraben foulbloods\n"))
    check("miscased_detected",
          P.miscased(puz, idx) == [("Thraben foulbloods", "Thraben Foulbloods")],
          str(P.miscased(puz, idx)))


def test_published_parses_upload_month():
    with tempfile.TemporaryDirectory() as tmp:
        puz = P.parse_pzl(_write(tmp, SAMPLE))
        check("published_month", P.published(puz) == "2024-05", str(P.published(puz)))
        nodate = P.parse_pzl(_write(tmp, SAMPLE.replace(
            "/2024/05/", "/latest/"), "T6.pzl"))
        # Undated must be None, never silently treated as old — that would
        # move puzzles into the "probably contaminated" bin on no evidence.
        check("undated_is_none", P.published(nodate) is None)


def _fake(fileid, difficulty, pub):
    return {"file": fileid, "difficulty": difficulty, "published": pub,
            "metadata": {}, "state": {}, "goal": "Win", "turns": 1,
            "n_cards": 1, "problems": []}


def test_stratified_pick_is_deterministic_and_ordered():
    pool = [_fake("a.pzl", "Rare", "2026-01"), _fake("b.pzl", "Mythic", "2019-03"),
            _fake("c.pzl", "Mythic", "2026-02"), _fake("d.pzl", "Common", None),
            _fake("e.pzl", "Uncommon", "2025-08")]
    got = [p["file"] for p in P.stratified_pick(pool, 3, 7)]
    # a/c/e are all 2025-06+, so the whole recent band comes first and is
    # ordered Mythic(c) > Rare(a) > Uncommon(e); the 2019 Mythic(b) is last
    # despite outranking a and e on difficulty. That is the point: recency
    # dominates, because contamination is the threat, not difficulty.
    check("recent_before_old_then_hard_first",
          got == ["c.pzl", "a.pzl", "e.pzl"],
          "recency outranks difficulty; got %s" % got)
    check("pick_is_deterministic",
          [p["file"] for p in P.stratified_pick(pool, 3, 7)] == got)
    check("seed_changes_only_ties",
          P.stratified_pick(pool, 1, 99)[0]["file"] == "c.pzl",
          "the sole recent Mythic must win under any seed")
    check("respects_n", len(P.stratified_pick(pool, 2, 7)) == 2)


def test_position_id_is_content_derived():
    with tempfile.TemporaryDirectory() as tmp:
        puz = P.parse_pzl(_write(tmp, SAMPLE))
        puz.update(difficulty="Rare", goal="Win", turns=1,
                   published=P.published(puz), n_cards=6, problems=[])
        a = P.to_position(puz)
        b = P.to_position(puz)
        check("id_stable", a["id"] == b["id"])
        check("id_len", len(a["id"]) == 16)
        puz["state"]["p1life"] = "9"
        check("id_tracks_state", P.to_position(puz)["id"] != a["id"],
              "changing the board must change the id")


def test_frozen_suite_matches_its_manifest():
    """Drift gate: the frozen manifest's sha256/n_positions must still
    describe the frozen jsonl. This is the pzl analogue of the floors check
    that caught puz-v2.1 shipping a stale always_first."""
    man_path = REPO / "eval" / "suites" / "pzl-v1.json"
    if not man_path.exists():
        check("pzl_v1_manifest_present", False, "eval/suites/pzl-v1.json missing")
        return
    with open(str(man_path), encoding="utf-8") as fh:
        man = json.load(fh)
    jsonl = REPO / man["positions_file"]
    check("positions_file_exists", jsonl.exists(), man["positions_file"])
    if not jsonl.exists():
        return
    import hashlib
    with open(str(jsonl), "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    check("suite_sha256_matches", sha == man["sha256"],
          "manifest %s vs file %s" % (man["sha256"][:16], sha[:16]))
    rows = [json.loads(l) for l in open(str(jsonl), encoding="utf-8")]
    check("n_positions_matches", len(rows) == man["n_positions"],
          "manifest %d vs file %d" % (man["n_positions"], len(rows)))
    check("ids_unique", len({r["id"] for r in rows}) == len(rows))
    check("every_row_has_state", all(r.get("state") for r in rows))
    check("category_counts_match",
          {k: sum(1 for r in rows if r["difficulty"] == k)
           for k in man["categories"]} == man["categories"])
    recent = sum(1 for r in rows if r.get("recency_band") == "recent")
    check("recency_count_matches", recent == man["n_recent"],
          "manifest %d vs file %d" % (man["n_recent"], recent))
    check("upstream_count_matches",
          sum(1 for r in rows if r.get("provenance") == "upstream-master")
          == man["n_from_upstream"])

    # Mechanic coverage: the claim must match the rows, and the shortfall must
    # be reported rather than rounded away.
    mc = man.get("mechanic_coverage") or {}
    check("coverage_block_present", bool(mc))
    if mc:
        check("coverage_not_overclaimed",
              mc["n_covered_by_this_suite"] <= mc["n_reachable_in_puzzle_corpus"]
              <= mc["n_in_forge_card_database"])
        check("unreachable_is_reported",
              mc["n_unreachable_at_any_suite_size"]
              == mc["n_in_forge_card_database"] - mc["n_reachable_in_puzzle_corpus"])
        # A quota shortfall must never be silently empty while n_below_quota>0.
        check("shortfall_matches_below_quota",
              len(mc.get("quota_shortfall") or {}) == mc["n_below_quota"],
              "%d entries vs n_below_quota=%d"
              % (len(mc.get("quota_shortfall") or {}), mc["n_below_quota"]))
        for mech, info in (mc.get("quota_shortfall") or {}).items():
            if info["max_in_corpus"] >= mc["quota_per_mechanic"]:
                check("shortfall_%s_is_real" % mech, False,
                      "listed short but corpus has %d" % info["max_in_corpus"])
                break
    # The floor rule is load-bearing for this project: a suite must never ship quotable
    # floor NUMBERS it has not measured. Which way that cuts depends on `status`, and
    # BOTH directions are asserted -- an unmeasured block must stay wordy, and a measured
    # one must actually carry the numbers plus the evidence that produced them. A single
    # unconditional "no numbers" rule would have had to be deleted the day the floors
    # were run, and a deleted rule guards nothing.
    floors = man.get("floors") or {}
    check("floors_block_present", bool(floors))
    if floors.get("status") == "MEASURED":
        check("measured_floor_is_executed", floors.get("method") == "executed",
              "the only accepted provenance is an executed run, not an estimate")
        check("measured_floor_has_a_number",
              isinstance(floors.get("quote_floor_as"), (int, float)),
              "a measured floor must publish the number to quote")
        arms = floors.get("arms") or {}
        check("measured_floor_has_arms", len(arms) >= 2, str(sorted(arms)))
        rates = [a.get("solve_rate") for a in arms.values()]
        check("every_arm_has_a_rate", all(isinstance(r, (int, float)) for r in rates),
              str(rates))
        check("quoted_floor_is_the_strongest_arm",
              floors.get("quote_floor_as") == max(rates),
              "quoting anything below the best trivial arm understates the bar")
        # Rates over a filtered denominator are only meaningful with the filter's size.
        for name, a in arms.items():
            check("arm_%s_reports_denominator" % name,
                  isinstance(a.get("n_scoreable"), int) and a["n_scoreable"] > 0, str(a))
            check("arm_%s_solved_within_denominator" % name,
                  0 <= a.get("n_solved", -1) <= a["n_scoreable"], str(a))
        check("scoreability_block_present", "scoreability" in man,
              "a measured floor must say which rows it could not score")
    else:
        check("no_unmeasured_floor_numbers",
              not any(isinstance(v, (int, float)) for v in floors.values()),
              "floors must stay non-numeric until an executed baseline exists")


def test_mechanic_index_reads_keywords_and_effects():
    """A keywords-only view reports zero coverage of Scry/Surveil/Proliferate,
    because Forge encodes keyword ACTIONS as DB$/SP$ effect tokens, not K:."""
    with tempfile.TemporaryDirectory() as tmp:
        cf = os.path.join(tmp, "forge-gui", "res", "cardsfolder", "x")
        os.makedirs(cf)
        with open(os.path.join(cf, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("Name:Test Flyer\nK:Flying\nK:Ward:2\n"
                     "A:SP$ Surveil | Amount$ 2\n")
        per_card, universe = P.mechanic_index(tmp)
    m = per_card[P.fold("Test Flyer")]
    check("kw_captured", "kw:Flying" in m)
    check("kw_param_stripped", "kw:Ward" in m, str(sorted(m)))
    check("effect_captured", "eff:Surveil" in m, str(sorted(m)))
    check("universe_counts", universe["kw:Flying"] == 1)


def test_quota_pick_fills_deficits_and_reports_the_cap():
    per_card = {
        P.fold("Aaa"): frozenset({"kw:Rare1"}),          # only source of Rare1
        P.fold("Bbb"): frozenset({"kw:Common"}),
        P.fold("Ccc"): frozenset({"kw:Common"}),
    }

    def mk(fileid, card):
        return {"file": fileid, "difficulty": "Rare", "published": "2019-01",
                "metadata": {}, "state": {"p0battlefield": card},
                "goal": "Win", "turns": 1, "n_cards": 1, "problems": []}

    pool = [mk("common1.pzl", "Bbb"), mk("common2.pzl", "Ccc"),
            mk("rare.pzl", "Aaa")]
    picked, have = P.quota_pick(pool, 2, 1, per_card, quota=5)
    files = {p["file"] for p in picked}
    # The single carrier of a scarce mechanic must be taken even though two
    # other puzzles look identical on difficulty and recency.
    check("scarce_mechanic_puzzle_is_taken", "rare.pzl" in files, str(files))
    check("quota_pick_respects_n", len(picked) == 2)
    check("counts_returned", have["kw:Common"] >= 1 and have["kw:Rare1"] == 1,
          str(dict(have)))
    # Nothing can lift a 1-puzzle mechanic to 5, and quota_pick must not
    # pretend otherwise by looping forever or duplicating.
    picked_all, have_all = P.quota_pick(pool, 99, 1, per_card, quota=5)
    check("no_duplicates_when_n_exceeds_pool",
          len(picked_all) == len(pool) == len({p["file"] for p in picked_all}))
    check("capped_mechanic_stays_short", have_all["kw:Rare1"] == 1)


def test_real_corpus_if_present():
    """Only runs when a Forge checkout is available; skipped otherwise so the
    ladder stays green on a machine without one."""
    pdir = P.puzzle_dir()
    if not os.path.isdir(pdir):
        print("[skip] real corpus — no puzzle dir at %s" % pdir)
        return
    corpus, index = P.load_corpus()
    check("corpus_nonempty", len(corpus) > 300, "found %d" % len(corpus))
    usable = [p for p in corpus if not p["problems"]]
    check("most_puzzles_usable", len(usable) >= 0.95 * len(corpus),
          "%d/%d usable — a drop means the card index regressed"
          % (len(usable), len(corpus)))
    check("index_has_split_faces", P.fold("Dusk // Dawn") in index,
          "joined split names must resolve")


def main():
    test_parse_keeps_colons_in_values()
    test_strip_attrs_and_tokens()
    test_card_refs_skips_tokens_and_nonzones()
    test_fold_handles_diacritics_but_not_typos()
    test_index_reads_every_face_of_a_script()
    test_validate_rejects_only_unusable_puzzles()
    test_miscased_reports_without_rejecting()
    test_published_parses_upload_month()
    test_stratified_pick_is_deterministic_and_ordered()
    test_position_id_is_content_derived()
    test_mechanic_index_reads_keywords_and_effects()
    test_quota_pick_fills_deficits_and_reports_the_cap()
    test_frozen_suite_matches_its_manifest()
    test_real_corpus_if_present()
    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all pzl_corpus checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
