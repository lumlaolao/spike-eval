#!/usr/bin/env python3
"""Descriptive statistics over recorded runs and frozen suites (no model, no JVM; docs/STANDARDS.md section 6).

    python3 eval/research_stats.py menu <board.jsonl> [...]            # menu-position-at-regret-steps
    python3 eval/research_stats.py loss-awareness <board.jsonl> [...]  # loss-awareness-regex-fails
    python3 eval/research_stats.py invalid-answers <runs dir> [...]    # JSON on stdout
    python3 eval/research_stats.py puzzle-bands <suite.json> [...]     # JSON on stdout

`menu` and `loss-awareness` read the rows `eval/puzzles/step_regret_board.py` / `board_drain.py` write (git-ignored under
`runs/`); a row whose `source_game` is not on this machine is skipped and counted. `invalid-answers` reads every
`game-*.jsonl` under the folders; a tally never pools populations, and a row that holds more than one harness epoch, or
an epoch that is unknown, is marked `pooled` and refuses a verdict. `puzzle-bands` reads the zero-model arms stored in
frozen suites. Method, results and caveats: eval/research/2026-11-08-invalid-answers-and-puzzle-bands.md.
"""
import json
import os
import re
import sys

from aggregate import wilson_ci
from fact_citations import read_rows

CLAIMS_WIN = re.compile(
    r"lethal|finish (them|the opponent)|win (this|the game)|enough (damage|drain|to (kill|win))|exactly enough"
    r"|reach(es)? (0|zero)|deal(s)? the (final|remaining)|for the win", re.I)
CONCEDES = re.compile(
    r"cannot|can't|no (way|line|remaining)|not enough|out of reach|impossible|short of|unreachable", re.I)
WHY = re.compile(r"WHY:\s*(.*)")


def decisions(game_rows):
    return [r for r in game_rows if r.get("t") == "decision"]


def menu_row(floor, regret_k, decision_rows):
    """The decision row that is the regret step: every floor option on its menu, an integer pick equal to the
    model's label; of several, the one whose index is nearest `regret_k`."""
    found = []
    for i, row in enumerate(decision_rows):
        options = row.get("options") or []
        try:
            pick = int(row.get("choice"))
        except (TypeError, ValueError):
            continue
        if all(o in options for o in floor["options"]) and 0 <= pick < len(options) \
                and options[pick] == floor["model_label"]:
            found.append((abs(i - regret_k), i, row))
    found.sort(key=lambda t: (t[0], t[1]))
    return (found[0][2], len(found)) if found else (None, 0)


def menu_step(floor, row):
    """(menu size, kept positions, losing positions, pick position) for one matched step."""
    options = row["options"]
    kept = [i for i, o in enumerate(options) if floor["options"].get(o) is True]
    lost = [i for i, o in enumerate(options) if floor["options"].get(o) is False]
    return len(options), kept, lost, options.index(floor["model_label"])


def menu_stats(steps):
    """Counts over matched steps; the chance values are the expectation for a uniform pick among the losing options."""
    n = len(steps)
    out = {"steps": n, "earliest_losing": 0, "earliest_losing_chance": 0.0, "before_every_kept": 0,
           "before_every_kept_chance": 0.0, "top_pick": 0, "top_pick_chance": 0.0, "pick_pos": 0.0, "kept_pos": 0.0}
    for size, kept, lost, pick in steps:
        out["earliest_losing"] += pick == min(lost)
        out["earliest_losing_chance"] += 1 / len(lost)
        out["before_every_kept"] += pick < min(kept)
        out["before_every_kept_chance"] += sum(1 for p in lost if p < min(kept)) / len(lost)
        out["top_pick"] += pick == 0
        out["top_pick_chance"] += 1 / size
        out["pick_pos"] += pick / (size - 1)
        out["kept_pos"] += sum(kept) / len(kept) / (size - 1)
    if n:
        out["pick_pos"] /= n
        out["kept_pos"] /= n
    return {k: round(v, 2) if isinstance(v, float) else v for k, v in out.items()}


def menu(board_rows):
    steps, skipped, unmatched, multi = [], 0, 0, 0
    for r in board_rows:
        if not r.get("floor"):
            continue
        if not os.path.exists(r["source_game"]):
            skipped += 1
            continue
        row, n = menu_row(r["floor"], r["regret_k"], decisions(read_rows(r["source_game"])))
        if row is None:
            unmatched += 1
            continue
        multi += n > 1
        steps.append(menu_step(r["floor"], row))
    return dict(menu_stats(steps), game_missing=skipped, unmatched=unmatched, several_rows=multi)


def loss_awareness(board_rows):
    """Per position (joined to the k-th non-auto decision row) the WHY sentence against the two patterns; and, per
    failed attempt, whether no WHY after the regret step matches CONCEDES."""
    cells = {True: [0, 0, 0], False: [0, 0, 0]}
    attempts = failed = never = skipped = 0
    for r in board_rows:
        if not r.get("positions"):
            continue
        if not os.path.exists(r["source_game"]):
            skipped += 1
            continue
        attempts += 1
        auto = [x for x in decisions(read_rows(r["source_game"])) if x.get("fallback") in (None, "None")]
        concede_after = False
        for q in r["positions"]:
            if q["k"] >= len(auto):
                continue
            m = WHY.search(str(auto[q["k"]].get("raw_reply") or ""))
            if not m:
                continue
            c = cells[bool(q["winnable"])]
            c[0] += 1
            c[1] += bool(CLAIMS_WIN.search(m.group(1)))
            c[2] += bool(CONCEDES.search(m.group(1)))
            if r.get("regret_k") is not None and q["k"] > r["regret_k"] and CONCEDES.search(m.group(1)):
                concede_after = True
        if r.get("regret_k") is not None:
            failed += 1
            never += not concede_after
    return {"winnable": cells[True], "lost": cells[False], "cell_is": "[replies with a reason, claims a win, concedes]",
            "attempts": attempts, "failed_with_regret_step": failed, "never_concede_after_step": never,
            "game_missing": skipped}


# --- invalid answers ---------------------------------------------------------------------------------------------

SHAPE = ("index", "type", "count", "schema")      # what a per-decision JSON Schema could forbid
# Message fragments of AnswerTool.validate / converse, by class; the first class with a match wins.
FAILURE_PATTERNS = (
    ("engine", ("ILLEGAL (", "are not legal together", "cannot block attacker")),
    ("index", ("is not on the list", "is out of range", "is not a CAST/ACTIVATE option")),
    ("count", ("options; you picked", "give exactly", "must sum to", "must be the only number", "must get at least",
               "may be picked once", "amounts cannot be negative", "times; it can block", "blocks attacker ")),
    ("schema", ("this decision is answered with", "unknown field", "FORMAT: this play targets")),
    ("type", ("not a JSON object", " value must be ", "is a list of handles", "each block is an object",
              "each block needs", "value is none")),
)
UNATTRIBUTED = ("", "unknown", "mixed")      # a log that does not name one model: neither a fixture nor a score
NO_MODEL = UNATTRIBUTED + ("random",)
# The retired bare-name text lane of the Claude CLI (owner, 2026-10-25): a development lane, never a menu score.
RETIRED_CLI_LANE = ("opus", "sonnet", "haiku")
EPOCH_UNKNOWN = "unknown"      # no game line, results file or manifest stamps the harness epoch


def classify_failure(text):
    """The class of one harness message about an answer call: `no_call` for a reminder or a loss, else the first
    FAILURE_PATTERNS class it matches, else `other`."""
    text = str(text or "")
    if text.startswith(("NUDGE", "LOST")):
        return "no_call"
    for name, fragments in FAILURE_PATTERNS:
        if any(f in text for f in fragments):
            return name
    return "other"


def failed_decisions(rows):
    """One record per decision row the answer tool did not take on the first call: lost, corrected (an invalid call
    that was then answered) or nudged (a reply with no call, then answered). `cause` is why the decision was lost or
    first failed, `events` the classes of the refused calls in order. The messages are the `context_tool` rows since
    the previous decision row; a lookup's error is not one of them."""
    out, pending = [], []
    for r in rows:
        if r.get("t") == "context_tool":
            res, req = str(r.get("result") or ""), str(r.get("request") or "")
            if res.startswith(("NUDGE", "LOST")) or (res.startswith("ERROR") and req.startswith("TOOL CALL answer")):
                pending.append(res)
            continue
        if r.get("t") != "decision":
            continue
        messages, pending = pending, []
        via, invalid = r.get("answer_via"), r.get("answer_invalid") or 0
        if via == "lost":
            outcome = "lost"
        elif invalid > 0:
            outcome = "corrected"
        elif via == "nudged":
            outcome = "nudged"
        else:
            continue
        events = [classify_failure(m) for m in messages if m.startswith("ERROR")]
        if outcome == "nudged" or any(m.startswith("LOST") for m in messages):
            cause = "no_call"
        elif not events:
            cause = "no_call" if outcome == "lost" and invalid == 0 else "unrecorded"
        else:
            cause = events[-1] if outcome == "lost" else events[0]
        out.append({"outcome": outcome, "cause": cause, "events": events, "type": r.get("type")})
    return out


def model_name(spec):
    return re.sub(r"^openrouter:", "", str(spec or ""))


def is_model(spec):
    """False for the stand-ins a run uses in place of a model (a loopback fake, a script, a floor policy)."""
    spec = model_name(spec)
    return spec not in NO_MODEL and not spec.startswith(("script:", "greedy", "floor", "random")) \
        and "fake" not in spec and "garbage" not in spec


def _json_lines(path):
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _manifest(folder):
    """The condition of the batch-manifest.json in this folder or the one above, else {}."""
    for d in (folder, os.path.dirname(folder)):
        try:
            with open(os.path.join(d, "batch-manifest.json")) as f:
                return json.load(f).get("condition") or {}
        except (OSError, ValueError, AttributeError):
            continue
    return {}


def _folder_context(folder, cache):
    """(models, epochs) a folder names beside its game files: puzzle-results.jsonl, else the manifest. `cache` maps
    folder to the answer, so a folder of many games is read once."""
    if folder not in cache:
        models, epochs = set(), set()
        for r in _json_lines(os.path.join(folder, "puzzle-results.jsonl")):
            models.add(model_name(r.get("solver_model")))
            epochs.add(r.get("harness_epoch"))
        if not models:
            models.add(model_name(_manifest(folder).get("smart_a")))
        cache[folder] = (models - {""}, epochs - {None, ""})
    return cache[folder]


def run_context(path, rows=None, cache=None):
    """(model, harness epoch) of a game log: its game line, else its folder's results or manifest. `mixed` when the
    source names several, `unknown` when it names none."""
    models, epochs = set(), set()
    for r in list(_json_lines(path)) if rows is None else rows:
        if r.get("t") == "game":
            seat = "b" if r.get("controller_a") not in (None, "llm") else "a"
            models.add(model_name(r.get("smart_" + seat)))
            epochs.add(r.get("harness_epoch"))
    if not models:
        models, epochs = _folder_context(os.path.dirname(path), {} if cache is None else cache)
    models, epochs = models - {""}, epochs - {None, ""}
    return (models.pop() if len(models) == 1 else "mixed" if models else "unknown",
            epochs.pop() if len(epochs) == 1 else "mixed" if epochs else EPOCH_UNKNOWN)


def run_arm(path, rows=None):
    """`propose` for a game played under PROPOSE_ACTIONS (the game line's stamp, else the manifest's pinned env), else
    `menu`: the arm that hides the play menu is a condition that never pools with the others."""
    for r in list(_json_lines(path)) if rows is None else rows:
        if r.get("t") == "game" and "propose_actions" in r:
            return "propose" if str(r["propose_actions"]).lower() in ("true", "1") else "menu"
    env = _manifest(os.path.dirname(path)).get("env") or {}
    return "propose" if env.get("PROPOSE_ACTIONS") == "1" else "menu"


def population(model, arm):
    """The pool a game's decisions are counted in: unnamed models, stand-ins, the propose arm and the Claude CLI
    development lane (owner, 2026-10-24: never a score; its retired bare names too) are each their own; `menu` is the play menu shown to a named
    API model."""
    if model in UNATTRIBUTED:
        return "unattributed"
    if not is_model(model):
        return "fixtures"
    return "propose" if arm == "propose" else "dev-lane" if model.startswith("claude-cli:") or model in RETIRED_CLI_LANE \
        else "menu"


def _tally():
    return {"epochs": set(), "sources": {}, "asked": 0, "failed": 0, "lost": 0, "corrected": 0, "nudged": 0, "unrecorded": 0, "lost_shape": 0,
            "lost_causes": {}, "failed_causes": {}}


def _add(tally, asked, failures, epoch, source=None):
    tally["epochs"].add(str(epoch))
    if source is not None:
        tally["sources"][source] = tally["sources"].get(source, 0) + 1
    tally["asked"] += asked
    for f in failures:
        tally["failed"] += 1
        tally[f["outcome"]] += 1
        tally["unrecorded"] += f["cause"] == "unrecorded"
        tally["failed_causes"][f["cause"]] = tally["failed_causes"].get(f["cause"], 0) + 1
        if f["outcome"] == "lost":
            tally["lost_causes"][f["cause"]] = tally["lost_causes"].get(f["cause"], 0) + 1
            tally["lost_shape"] += f["cause"] in SHAPE


def _finish(tally):
    lost = tally["lost"]
    tally["epochs"] = sorted(tally["epochs"])
    tally["sources"] = dict(sorted(tally["sources"].items()))
    tally["pooled"] = len(tally["epochs"]) > 1 or EPOCH_UNKNOWN in tally["epochs"]
    tally["lost_rate_ci"] = [round(x, 4) for x in wilson_ci(lost, tally["asked"])]
    tally["failed_rate_ci"] = [round(x, 4) for x in wilson_ci(tally["failed"], tally["asked"])]
    tally["lost_shape_ci"] = [round(x, 4) for x in wilson_ci(tally["lost_shape"], lost)]
    return tally


# The author's choice for this note (2026-11-08), not repository or owner policy.
SHAPE_RULE = "shape errors above half of the lost decisions"
MIN_LOST_FOR_RULE = 10     # the author's choice: fewer lost decisions than this is too few for a share to decide


def _verdict(tally):
    """The decision rule on one tally's lost decisions: `build_schema` is None (with a note) when the rule has too few
    lost decisions to apply to, or when the row is pooled (several epochs, or an epoch that is unknown)."""
    lost = tally["lost"]
    share = tally["lost_shape"] / lost if lost else 0.0
    out = {"epochs": tally["epochs"], "asked": tally["asked"], "lost": lost, "lost_shape": tally["lost_shape"],
           "shape_share": share, "shape_share_ci": tally["lost_shape_ci"], "build_schema": share > 0.5}
    if tally["pooled"]:
        out.update(build_schema=None, note="pooled over epochs %s; conditions do not pool" % tally["epochs"])
    elif lost < MIN_LOST_FOR_RULE:
        out.update(build_schema=None, note="no lost decisions; rule not applicable" if not lost
                   else "too few lost decisions (%d < %d); rule not applicable" % (lost, MIN_LOST_FOR_RULE))
    return out


def invalid_answers(roots):
    """The report for every `game-*.jsonl` under `roots`: a tally per population, the `menu` tally by model, epoch and
    decision type, and the decision-rule verdict on the lost decisions of the `menu` population."""
    files = []
    for root in roots:
        for dirpath, _, names in os.walk(root):
            files += [(os.path.join(dirpath, n), root) for n in names if n.startswith("game-") and n.endswith(".jsonl")]
    pops = {name: _tally() for name in ("menu", "propose", "dev-lane", "fixtures", "unattributed")}
    by = {"by_model": {}, "by_epoch": {}, "by_type": {}}
    cache = {}
    for path, root in sorted(files):
        rows = list(_json_lines(path))
        model, epoch = run_context(path, rows, cache)
        pop = population(model, run_arm(path, rows))
        failures = failed_decisions(rows)
        asked = {}
        for r in rows:
            if r.get("t") == "decision" and r.get("answer_via") is not None:
                asked[r.get("type")] = asked.get(r.get("type"), 0) + 1
        if not asked and not failures:
            continue        # a game with no tool-asked decision belongs to no epoch's tally
        n_asked = sum(asked.values())
        source = "/".join(os.path.relpath(os.path.dirname(path), root).split(os.sep)[:2])
        _add(pops[pop], n_asked, failures, epoch, source)
        if pop != "menu":
            continue
        _add(by["by_model"].setdefault(model, _tally()), n_asked, failures, epoch)
        _add(by["by_epoch"].setdefault(epoch, _tally()), n_asked, failures, epoch, source)
        for kind in set(asked) | {f["type"] for f in failures}:
            _add(by["by_type"].setdefault(kind, _tally()), asked.get(kind, 0),
                 [f for f in failures if f["type"] == kind], epoch)
    out = {"files": len(files), "populations": {name: _finish(t) for name, t in pops.items()}}
    for name, tallies in by.items():
        out[name] = {key: _finish(t) for key, t in sorted(tallies.items(), key=str)}
    out["verdict"] = {"rule": SHAPE_RULE, "min_lost": MIN_LOST_FOR_RULE,
                      "per_epoch": {e: _verdict(t) for e, t in out["by_epoch"].items()},
                      "pooled": _verdict(out["populations"]["menu"])}
    return out


# --- puzzle informativeness bands --------------------------------------------------------------------------------

LIVE_MAX = 0.20          # docs/eval/PUZZLES.md: random must solve a puzzle at most 20% of the time
ZERO_MODEL_ARMS = ("first", "greedy", "ai")
# Both thresholds are the author's choice for the 2026-11-08 note, not repository or owner policy.
INFORMATIVE_MAX_TRIVIAL = 0.10    # "not saturated" if at most this share of active puzzles a zero-model arm solves
GRADIENT_MIN_LIVE = 0.25          # an RL start from random sees reward if at least this share is in the live band


def band(random_rate):
    return "unreachable" if random_rate == 0 else "live" if random_rate <= LIVE_MAX else "easy"


def _suite_label(path):
    m = re.match(r"puzzles-(?:hard-)?(.+)-v\d+\.json$", os.path.basename(path))
    return "rules" if m and m.group(1) == "rules-sample" else m.group(1) if m else os.path.basename(path)


def _bands_row(puzzles):
    row = {"n": 0, "unreachable": 0, "live": 0, "easy": 0, "unscoreable": 0, "trivial_any": 0,
           "active": 0, "active_trivial_any": 0, "arms": {}}
    rollouts = []
    for p in puzzles:
        base = p.get("baselines") or {}
        if "unscoreable" in (p.get("flags") or []) or "random" not in base:
            row["unscoreable"] += 1
            continue
        row["n"] += 1
        row[band(base["random"]["rate"])] += 1
        solved = {a: base[a]["solved"] > 0 for a in ZERO_MODEL_ARMS if a in base}
        if p.get("status") == "active":     # `arms` counts the active puzzles, like `active_trivial_any`
            for a, hit in solved.items():
                row["arms"][a] = row["arms"].get(a, 0) + hit
        row["trivial_any"] += any(solved.values())
        row["active"] += p.get("status") == "active"
        row["active_trivial_any"] += p.get("status") == "active" and any(solved.values())
        rollouts.append(base["random"]["n"])
    row["random_rollouts"] = [min(rollouts), max(rollouts)] if rollouts else []
    for key in ("unreachable", "live", "easy", "trivial_any"):
        row[key + "_ci"] = [round(x, 4) for x in wilson_ci(row[key], row["n"])]
    row["active_trivial_any_ci"] = [round(x, 4) for x in wilson_ci(row["active_trivial_any"], row["active"])]
    row["label"], row["rl_gradient"] = informative(row)
    return row


def informative(row):
    """(label, whether an RL start from random sees reward): `not saturated by zero-model arms` when at most 10% of
    the active puzzles are solved by a zero-model arm (a dropped puzzle is not scored); an RL gradient when at least
    25% of the measured puzzles are in the live band. The label rests on the floors only: no model was run."""
    ok = row["active_trivial_any"] / (row["active"] or 1) <= INFORMATIVE_MAX_TRIVIAL
    label = "not saturated by zero-model arms" if ok else "saturated by zero-model arms"
    return label, row["live"] / (row["n"] or 1) >= GRADIENT_MIN_LIVE


def puzzle_bands(suite_paths):
    """Per suite, per family inside a suite and pooled over the hard suites, the share of puzzles unreachable by
    random, in the live band, too easy for random and solved by a zero-model arm (Wilson intervals over puzzles)."""
    suites, families, hard = {}, {}, []
    for path in suite_paths:
        with open(path, encoding="utf-8") as f:
            puzzles = json.load(f)["puzzles"]
        label = _suite_label(path)
        suites[label] = _bands_row(puzzles)
        if os.path.basename(path).startswith("puzzles-hard-"):
            hard += puzzles
        names = {(p.get("source") or {}).get("family") for p in puzzles} - {None}
        for name in sorted(names):
            families["%s/%s" % (label, name)] = _bands_row([p for p in puzzles if p["source"].get("family") == name])
    if hard:
        suites["hard-*"] = _bands_row(hard)
    return {"suites": suites, "families": families}


def main(argv):
    commands = {"menu": menu, "loss-awareness": loss_awareness, "invalid-answers": invalid_answers,
                "puzzle-bands": puzzle_bands}
    if len(argv) < 2 or argv[0] not in commands:
        print(__doc__)
        return 2
    if argv[0] in ("invalid-answers", "puzzle-bands"):
        print(json.dumps(commands[argv[0]](argv[1:]), sort_keys=True, indent=1))
        return 0
    for path in argv[1:]:
        print(path, json.dumps(commands[argv[0]](read_rows(path)), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
