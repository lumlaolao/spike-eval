#!/usr/bin/env python3
"""The hidden grader's entry point for the harness-building environment, version surface-v0
(docs/harness/HARNESS-ENV-FRAMEWORK.md, section 4).

Given a submitted overlay (laid out like forge-overlay/: <module>/src/main/java/...), it
  1. takes it in: counts its Java, reads which interface it implements (`harness-env.properties` names a
     `seat.factory`: harness-env-abi/1; our own layout, `forge/spike/LlmSpikeGui.java`: v0-internal), lists engine
     files it patches and whether each is marked, and which files reference Forge's AI;
  2. assembles a grading workspace OUTSIDE the submission: the grader's own copy of the acceptance machinery, the
     ownership ledger, gap files that list nothing (the perfect harness has no gap), the submission's main sources,
     and the hidden tests (the surface sweep, the golden checks, the Surface*Test classes, the ownership and
     prompt-fidelity tests, and the helpers they need). The submission's own tests are dropped: they earn nothing;
  3. runs the acceptance stages `preflight classpath compile javatests surfaces` in that workspace (opt-in,
     `--run`: a JVM run of about 15 to 25 minutes, refused while any `--busy` marker exists);
  4. scores the evidence: hooks asked by family, ways to play a card played, hidden tests passed, gated on a clean
     compile and a proven zero count of decisions left to Forge's AI.

What v0 cannot do, and why the framework calls it a validation grader only: the hidden tests call our classes and
read our prompt format, so they compile only against a submission that keeps our class names and prompts (our
overlay, a mutant of it, a commit from its history). A from-scratch harness implements harness-env-abi/1, which
v0 refuses to score (score null, with the reason) rather than score 0: that needs the black-box grader of pilot
step 2. And the record of which hooks were asked comes from the submission's own recorder (v0 trusts it; the v1
grader counts asks in a grader-side engine patch).

`score` reads any acceptance OUT directory, so our own recorded runs can be scored without a JVM. It scores against
today's ledger and hidden classes: a run from another epoch whose test classes differ shows them as missing.

Usage:
  harness_env_grade.py intake <submission>
  harness_env_grade.py score <acceptance OUT> [--ledger <tsv>]
  harness_env_grade.py table <env-grade.tsv>      (the instance report EnvGraderSuite writes: per family, per seat)
  harness_env_grade.py build <submission overlay> <classpath.txt> <forge> <out>
      compiles the submission's main sources, the grader and its Forge-AI counter patch into <out>/classes
  harness_env_grade.py instances <classes> <classpath.txt> <forge> <out> [--only <id part>] [--seats name=class,...]
      [--set <generated set.json>] [--busy <marker>]...   runs the instance grader (GraderSuite; by default the six
      validation seats on the hand catalog) on built classes and prints the table
  harness_env_grade.py grade <submission> <out> [--forge <checkout>] [--run] [--busy <marker>]...
Prints JSON. Exit 0 when a score was produced, 1 when the submission was not scorable, 2 on usage.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import harness_env_aicount
import surface_coverage

VERSION = "surface-v0"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TEST_DIR = os.path.join("forge-overlay", "forge-gui-desktop", "src", "test", "java", "forge", "spike")
LEDGER = os.path.join("docs", "harness", "controller-ownership.tsv")
STAGES = "preflight classpath compile javatests surfaces"
# A hang guard far above a real run (javatests took 3m47s in a 2026-10-20 acceptance run, compile minutes more).
RUN_TIMEOUT_S = 3 * 3600
# What the grading workspace takes from the grader's repository (never from the submission).
GRADER_FILES = ("FORGE_COMMIT", "eval/harness_acceptance.sh", "eval/harness_acceptance.expected", "eval/java_imports.py",
                "eval/surface_coverage.py", "eval/tooluse/tools.json", "eval/decks", "data/rules/MagicCompRules.txt",
                LEDGER)
FIXED_ROOTS = ("KeywordSurfaceSweepTest", "GoldenChecksTest", "GoldenChecksToolTest", "ControllerOwnershipTest",
               "PromptFidelityTest", "PlanAnytimeTest")
WEIGHTS = {"hooks": 0.5, "plays": 0.2, "tests": 0.3}
FLOOR = 0.001  # a family at zero counts as this in the benchmark's geometric mean (calibrated)
ENGINE_MODULES = ("forge-core", "forge-game", "forge-ai", "forge-gui")
PATCH_MARKERS = ("ENGINE PATCH:", "HARNESS PATCH")
FORGE_AI = re.compile(r"\bforge\.ai\.(PlayerControllerAi|AiController|ComputerUtil\w*|AiPlayDecision|AiCostDecision)\b")

# Hook families (provisional weights: equal; HARNESS-ENV.md section 3.4 derives them from recorded play).
FAMILIES = {
    "play": ("chooseSpellAbilityToPlay", "getAbilityToPlay", "playSaFromPlayEffect", "playSpellAbilityNoStack",
             "playTrigger", "chooseSaToActivateFromOpeningHand", "orderAndPlaySimultaneousSa"),
    "combat": ("declareAttackers", "declareBlockers", "assignCombatDamage", "orderAttackers", "orderBlocker",
               "orderBlockers", "enlistAttackers", "exertAttackers", "payCombatCost"),
    "targets": ("chooseTarget", "chooseTargetsFor", "chooseNewTargetsFor", "divideShield"),
    "costs": ("addKeywordCost", "announceRequirements", "chooseCardsForConvokeOrImprovise", "chooseCardsToDelve",
              "chooseNumberForCostReduction", "chooseNumberForKeywordCost", "chooseOptionalCosts",
              "choosePlayerToAssistPayment", "confirmPayment", "getCostDecisionMaker", "helpPayForAssistSpell",
              "payCostToPreventEffect", "payManaCost", "chooseManaFromPool", "chooseCardsForSplice"),
    "triggers-and-replacements": ("confirmTrigger", "orderSimultaneousSa", "chooseSingleReplacementEffect",
                                  "confirmReplacementEffect", "confirmStaticApplication", "chooseSingleStaticAbility",
                                  "orderMoveToZoneList"),
    "game-start": ("mulliganKeepHand", "tuckCardsViaMulligan", "chooseStartingHand", "chooseStartingPlayer"),
    "library": ("arrangeForScry", "arrangeForSurveil", "willPutCardOnTop"),
    "variants": (),
    "choices": (),
}
VARIANTS = re.compile(r"Roll|Dice|Sticker|Sprocket|Contraption|Sector|FlipResult")


class Busy(RuntimeError):
    pass


def family(hook):
    for name, hooks in FAMILIES.items():
        if hook in hooks:
            return name
    return "variants" if VARIANTS.search(hook) else "choices"


def hidden_roots(test_dir):
    surface = sorted(f[:-5] for f in os.listdir(test_dir) if f.startswith("Surface") and f.endswith("Test.java"))
    return list(FIXED_ROOTS) + surface


def test_closure(test_dir, roots):
    """The test-directory classes `roots` need to compile: every other file there they name, transitively."""
    names = sorted(f[:-5] for f in os.listdir(test_dir) if f.endswith(".java"))
    named = re.compile(r"\b(%s)\b" % "|".join(map(re.escape, names)))
    need, todo = set(), list(roots)
    while todo:
        n = todo.pop()
        if n in need or n not in names:
            continue
        need.add(n)
        with open(os.path.join(test_dir, n + ".java"), encoding="utf-8") as f:
            todo.extend(set(named.findall(f.read())) - need)
    return sorted(need)


def hidden_classes(test_dir):
    """The hidden test classes whose results count: the closure's classes that are tests."""
    return [n for n in test_closure(test_dir, hidden_roots(test_dir)) if n.endswith("Test")]


def _java_files(root):
    out = []
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "target")]
        out.extend(os.path.relpath(os.path.join(r, f), root).replace(os.sep, "/") for f in files if f.endswith(".java"))
    return sorted(out)


def intake(submission, forge=None):
    """What the submission is, read without compiling it."""
    java = _java_files(submission)
    props = os.path.join(submission, "harness-env.properties")
    factory = None
    if os.path.isfile(props):
        with open(props, encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition("=")
                if k.strip() == "seat.factory" and v.strip():
                    factory = v.strip()
    abi = "harness-env-abi/1" if factory else (
        "v0-internal" if "forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java" in java else None)
    main = [j for j in java if "/src/main/java/" in j]

    # An engine file is one the engine commits: a working checkout also holds what install.sh copied into it.
    committed = None
    if forge:
        ls = subprocess.run(["git", "-C", forge, "ls-tree", "-r", "--name-only", "HEAD"], capture_output=True, text=True)
        committed = set(ls.stdout.splitlines()) if ls.returncode == 0 else None

    def is_engine(path):
        if committed is not None:
            return path in committed
        return path.split("/", 1)[0] in ENGINE_MODULES

    patches, unmarked, ai = [], [], []
    for j in main:
        with open(os.path.join(submission, j), encoding="utf-8", errors="replace") as f:
            text = f.read()
        if is_engine(j):
            patches.append(j)
            if not any(m in text for m in PATCH_MARKERS):
                unmarked.append(j)
        elif FORGE_AI.search(text):
            ai.append(j)
    return {"java_files": len(java), "main_files": len(main), "own_test_files": len(java) - len(main), "abi": abi,
            "factory": factory, "engine_patches": patches, "unmarked_engine_patches": unmarked,
            "forge_ai_references": ai}


def assemble(submission, grader_repo, ws):
    """Build the grading workspace at `ws` (must not exist): grader files, submission main, hidden tests."""
    os.makedirs(ws)
    for f in GRADER_FILES:
        src, dst = os.path.join(grader_repo, f), os.path.join(ws, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        (shutil.copytree if os.path.isdir(src) else shutil.copyfile)(src, dst)
    for gaps in ("surface_gaps.txt", "play_gaps.txt"):
        with open(os.path.join(ws, "eval", gaps), "w", encoding="utf-8") as f:
            f.write("# The grader's gap file lists nothing: the perfect harness asks every hook and plays every way.\n")
    overlay = os.path.join(ws, "forge-overlay")
    for j in _java_files(submission):
        if "/src/main/" in j:
            dst = os.path.join(overlay, j)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(submission, j), dst)
    tests_src = os.path.join(grader_repo, TEST_DIR)
    tests_dst = os.path.join(ws, TEST_DIR)
    os.makedirs(tests_dst)
    names = test_closure(tests_src, hidden_roots(tests_src))
    for n in names:
        shutil.copyfile(os.path.join(tests_src, n + ".java"), os.path.join(tests_dst, n + ".java"))
    return {"workspace": ws, "hidden_test_files": names}


def _summary_line(evidence, stage):
    path = os.path.join(evidence, "summary.txt")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.split(" ", 2)[1:2] == [stage + ":"]:
                return line.strip()
    return None


def _ownership(evidence):
    path = os.path.join(evidence, "javatests.log")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r"\[OWNERSHIP\].*: (\d+)\s*$", line)
            if m:
                return int(m.group(1))
    return None


def _test_results(evidence, classes):
    path = os.path.join(evidence, "testng", "testng-results.xml")
    by = {}
    if os.path.isfile(path):
        for cls in ET.parse(path).getroot().iter("class"):
            simple = cls.get("name", "").rsplit(".", 1)[-1]
            if simple not in classes:
                continue
            for m in cls.iter("test-method"):
                if m.get("is-config") == "true":
                    continue
                c = by.setdefault(simple, {"passed": 0, "total": 0})
                c["total"] += 1
                c["passed"] += m.get("status") == "PASS"
    return by


def score_evidence(evidence, ledger, classes):
    """Score an acceptance OUT directory against the hidden ledger and the hidden test classes."""
    hooks = sorted(surface_coverage.decision_hooks(ledger))
    report = surface_coverage.read_report(os.path.join(evidence, "surfaces.tsv")) \
        if os.path.isfile(os.path.join(evidence, "surfaces.tsv")) else {"known": set()}
    families = {}
    for h in hooks:
        f = families.setdefault(family(h), {"hooks": 0, "asked": 0, "unasked": []})
        f["hooks"] += 1
        if "asked" in report.get(h, ()):
            f["asked"] += 1
        else:
            f["unasked"].append(h)
    known = report.get("known", set())
    played = [k for k in known if k in report]
    tests = _test_results(evidence, classes)
    missing = sorted(set(classes) - set(tests))
    per_class = [tests[c]["passed"] / tests[c]["total"] if c in tests and tests[c]["total"] else 0.0 for c in classes]
    components = {
        "hooks": sum(f["asked"] / f["hooks"] for f in families.values()) / len(families) if families else 0.0,
        "plays": len(played) / len(known) if known else 0.0,
        "tests": sum(per_class) / len(per_class) if per_class else 0.0,
    }
    compile_line = _summary_line(evidence, "compile")
    silent = _ownership(evidence)
    gates = {"compiled": bool(compile_line and compile_line.startswith("PASS")), "no_forge_ai": silent == 0}
    reasons = []
    if not gates["compiled"]:
        reasons.append("did not compile against the hidden tests: %s" % (compile_line or "no compile record"))
    if not gates["no_forge_ai"]:
        reasons.append("Forge's AI decides for the seat (silent strategic hooks: %s)"
                       % ("not proven" if silent is None else silent))
    raw = sum(WEIGHTS[k] * v for k, v in components.items())
    gate = 1.0 if all(gates.values()) else 0.0
    cal = calibrated({k: f["asked"] / f["hooks"] for k, f in families.items()})
    bench = cal["benchmark"] * components["plays"] * components["tests"]
    return {"grader": VERSION, "score": round(gate * bench, 6), "dense": round(gate * raw, 6), "raw": round(raw, 6),
            "components": components, "weights": WEIGHTS, "gates": gates, "reasons": reasons,
            "families": families, "families_at_zero": cal["families_at_zero"],
            "plays": {"known": len(known), "played": len(played)}, "tests": {"classes": tests, "missing": missing}}


def calibrated(family_scores):
    """The two numbers a family profile makes (owner, 2026-10-22).

    `dense`: the plain mean over families, the RL reward between checkpoints, where every bit of progress counts.
    `benchmark`: the geometric mean over families with each family floored at FLOOR, so a family at zero costs
    FLOOR ** (1 / number of families): with nine families and FLOOR 0.001 one missing family leaves 0.46 of the
    score, two leave 0.22, a seat that maps only plays about 0.002. Equal family weights until corpus weights
    exist (HARNESS-ENV.md section 3.4)."""
    if not family_scores:
        return {"dense": 0.0, "benchmark": 0.0, "families_at_zero": []}
    n = len(family_scores)
    product = 1.0
    for s in family_scores.values():
        product *= max(FLOOR, s) ** (1.0 / n)
    return {"dense": round(sum(family_scores.values()) / n, 6), "benchmark": round(product, 6),
            "families_at_zero": sorted(k for k, s in family_scores.items() if s <= 0)}


# The instance report EnvGraderSuite writes (forge-overlay/.../EnvGrader.java): one row per seat and instance.
ENV_COLUMNS = ("seat", "instance", "family", "hook", "legal", "reached", "min_requests", "asked", "offered",
               "carried", "shown", "noparse", "pass", "ai", "ai_payment", "note")
SEAT_ORDER = ("ours", "default", "empty", "ours-minus-combat", "ours-minus-costs")


def read_env_report(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            if line.strip():
                rows.append(dict(zip(head, line.rstrip("\n").split("\t"))))
    return rows


def env_table(rows, by="surface"):
    """{seat: {family: {"passed", "total"}}}; an instance passes when all five checks pass. `by="surface"` groups by
    the report's family (the owner's grouping: a combat trick, a damage prevention split sit with combat);
    `by="hook"` by the ledger family of the hook the instance asks (`family`), the grouping the benchmark gates on,
    so "never asks a combat hook" is a zero family whatever surface group the hook was tested in."""
    t = {}
    for r in rows:
        fam = family(r["hook"]) if by == "hook" else r["family"]
        # One legal answer tests only that the seat does nothing illegal: failing it costs (an illegal block stands),
        # passing it earns nothing, so a forced pass cannot keep a family whose real choices all failed off zero
        # (measured 2026-10-22: one forced block kept a seat that never asked combat at 1/17, benchmark 0.72).
        if int(r.get("legal") or 2) < 2 and r.get("pass") == "1":
            continue
        c = t.setdefault(r["seat"], {}).setdefault(fam, {"passed": 0, "total": 0})
        c["total"] += 1
        c["passed"] += r.get("pass") == "1"
    return t


def _seats(table):
    seen = list(table) + ["empty"]
    return sorted(set(seen), key=lambda s: (SEAT_ORDER.index(s) if s in SEAT_ORDER else len(SEAT_ORDER), s))


def ai_gated(rows):
    """Seats with any decision handed to Forge's AI (the counter's `ai` column; the payment executor, `ai_payment`,
    is reported apart and does not gate)."""
    return {r["seat"] for r in rows if int(r.get("ai") or 0) > 0}


def env_scores(table, gated=()):
    """Per seat: the calibrated numbers over the families the report covers. A seat in `gated` (any Forge-AI decision,
    ai_gated) has its benchmark set to 0, its dense reward kept. `empty` (no harness: nothing compiles, intake scores
    0) is added with every family at zero."""
    fams = sorted({f for seat in table.values() for f in seat})
    out = {}
    for seat in _seats(table):
        cells = table.get(seat, {})
        scores = {f: (cells[f]["passed"] / cells[f]["total"]) if cells.get(f, {}).get("total") else 0.0 for f in fams}
        out[seat] = calibrated(scores) if seat != "empty" else {"dense": 0.0, "benchmark": 0.0, "families_at_zero": fams}
        out[seat]["forge_ai_gate"] = seat in gated
        if seat in gated:
            out[seat]["benchmark"] = 0.0
    return out


GRADER_TESTS = os.path.join("forge-overlay", "forge-gui-desktop", "src", "test", "java", "forge", "harnessenv")
OURS_ADAPTER = os.path.join("forge-overlay", "forge-gui-desktop", "src", "test", "java", "forge", "spike", "HarnessEnvOurs.java")


def compile_command(javac, classpath_file, submission, grader_repo, ai_patch, out_classes):
    """argv that compiles, into one directory first on the classpath: the submission's main sources (never its tests),
    the grader (harness-env-abi/1, the scripted model, the instances, the toys), the grader's Forge-AI counter patch
    of PlayerControllerAi, and, when the submission is our own overlay, its ABI adapter (HarnessEnvOurs)."""
    files = [os.path.join(submission, j) for j in _java_files(submission) if "/src/main/java/" in j]
    grader = os.path.join(grader_repo, GRADER_TESTS)
    files += [os.path.join(grader, j) for j in _java_files(grader)]
    files.append(ai_patch)
    if any(f.endswith("forge/spike/LlmSpikeGui.java") for f in files) and not any(f.endswith("/HarnessEnvOurs.java") for f in files):
        files.append(os.path.join(grader_repo, OURS_ADAPTER))
    cp = ""
    if os.path.isfile(classpath_file):
        with open(classpath_file, encoding="utf-8") as f:
            cp = f.read().strip()
    return [javac, "--release", "17", "-encoding", "UTF-8", "-proc:none", "-nowarn", "-cp", cp, "-d", out_classes] + files


def instances_command(java, classes, classpath_file, repo, report, only=None, seats=None, set_file=None):
    """argv that runs the instance grader (GraderSuite) on compiled classes, with the system properties the
    acceptance javatests stage gives a seat. Run it from the engine's forge-gui-desktop directory."""
    cp = ""
    if os.path.isfile(classpath_file):
        with open(classpath_file, encoding="utf-8") as f:
            cp = f.read().strip()
    argv = [java, "-Dapple.awt.UIElement=true",
            "-Dharness.ownership.tsv=" + os.path.join(repo, LEDGER),
            "-Dharness.rules.file=" + os.path.join(repo, "data", "rules", "MagicCompRules.txt"),
            "-Dharness.decks.dir=" + os.path.join(repo, "eval", "decks"),
            "-Dharness.tooluse.json=" + os.path.join(repo, "eval", "tooluse", "tools.json"),
            "-Dharness.envgrade.report=" + report]
    if only:
        argv.append("-Dharness.envgrade.only=" + only)
    if seats:
        argv.append("-Dharness.envgrade.seats=" + seats)
    if set_file:
        argv.append("-Dharness.envgrade.set=" + set_file)
    return argv + ["-cp", classes + ":" + cp, "org.testng.TestNG", "-testclass", "forge.harnessenv.grader.GraderSuite",
                   "-d", os.path.join(os.path.dirname(report), "testng")]


def env_markdown(table, gated=()):
    """The per-family table, seats as columns, then the dense and benchmark rows."""
    seats = _seats(table)
    fams = sorted({f for seat in table.values() for f in seat})
    lines = ["| family | instances | " + " | ".join(seats) + " |", "|---|---|" + "---|" * len(seats)]
    for f in fams:
        n = max(table.get(s, {}).get(f, {}).get("total", 0) for s in seats)
        cells = []
        for s in seats:
            c = table.get(s, {}).get(f)
            cells.append("%d/%d" % (c["passed"], c["total"]) if c else "0/%d" % n)
        lines.append("| %s | %d | %s |" % (f, n, " | ".join(cells)))
    scores = env_scores(table, gated)
    for k in ("dense", "benchmark"):
        lines.append("| %s | | %s |" % (k, " | ".join("%.3f" % scores[s][k] for s in seats)))
    return "\n".join(lines)


def grade(submission, grader_repo, out, forge=None, run_jvm=False, busy_markers=()):
    """Intake, and with `run_jvm` assemble, run and score. Returns the verdict (also written to out/verdict.json)."""
    info = intake(submission, forge)
    verdict = {"grader": VERSION, "intake": info, "score": None, "reasons": []}
    if info["java_files"] == 0:
        verdict.update(score=0.0, reasons=["no Java in the submission: nothing to grade"])
    elif info["abi"] != "v0-internal":
        verdict["reasons"].append("surface-v0 drives our own classes only; %s needs the black-box grader "
                                  "(framework pilot step 2)" % (info["abi"] or "an unknown layout"))
    elif run_jvm:
        busy = [m for m in busy_markers if os.path.exists(m)]
        if busy:
            raise Busy("refusing a JVM run while %s exists" % ", ".join(busy))
        ws = os.path.join(out, "workspace")
        verdict["workspace"] = assemble(submission, grader_repo, ws)
        acc = os.path.join(out, "acceptance")
        env = dict(os.environ, STAGES=STAGES, OUT=acc, FORGE_HOME=forge or os.environ.get("FORGE_HOME", ""))
        hung = None
        with open(os.path.join(out, "acceptance.log"), "w", encoding="utf-8") as log:
            try:
                subprocess.run(["bash", os.path.join(ws, "eval", "harness_acceptance.sh")], env=env, stdout=log,
                               stderr=subprocess.STDOUT, timeout=RUN_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                hung = "the grading run passed its %d s time limit (a hang fails the submission)" % RUN_TIMEOUT_S
        tests = os.path.join(grader_repo, TEST_DIR)
        verdict.update(score_evidence(acc, os.path.join(grader_repo, LEDGER), hidden_classes(tests)))
        verdict["intake"] = info
        if hung:
            verdict.update(score=0.0, reasons=[hung] + verdict["reasons"])
    else:
        verdict["reasons"].append("not run: pass --run for the JVM stages")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "verdict.json"), "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=1, sort_keys=True, default=sorted)
    return verdict


def main(argv):
    args = argv[1:]
    if len(args) == 2 and args[0] == "intake":
        print(json.dumps(intake(args[1]), indent=1))
        return 0
    if len(args) in (2, 4) and args[0] == "score":
        ledger = args[3] if len(args) == 4 and args[2] == "--ledger" else os.path.join(REPO, LEDGER)
        s = score_evidence(args[1], ledger, hidden_classes(os.path.join(REPO, TEST_DIR)))
        print(json.dumps(s, indent=1, sort_keys=True, default=sorted))
        return 0
    if len(args) == 5 and args[0] == "build":
        submission, cpfile, forge, out = args[1:]
        patch, counted = harness_env_aicount.write(forge, os.path.join(REPO, LEDGER), os.path.join(out, "aipatch"))
        classes = os.path.join(out, "classes")
        os.makedirs(classes, exist_ok=True)
        javac = os.path.join(os.environ.get("JAVA_HOME", "/usr"), "bin", "javac")
        with open(os.path.join(out, "javac.log"), "w", encoding="utf-8") as log:
            rc = subprocess.run(compile_command(javac, cpfile, submission, REPO, patch, classes), stdout=log, stderr=subprocess.STDOUT).returncode
        print("build %s: javac rc=%d, Forge-AI counter in %d decision methods, classes %s" % (submission, rc, len(counted), classes))
        return 0 if rc == 0 else 1
    if len(args) >= 5 and args[0] == "instances":
        classes, cpfile, forge, out, rest = args[1], args[2], args[3], args[4], args[5:]
        only, busy, seats, set_file = None, [], None, None
        while rest:
            flag = rest.pop(0)
            if flag == "--only" and rest:
                only = rest.pop(0)
            elif flag == "--seats" and rest:
                seats = rest.pop(0)
            elif flag == "--set" and rest:
                set_file = rest.pop(0)
            elif flag == "--busy" and rest:
                busy.append(rest.pop(0))
            else:
                print(__doc__)
                return 2
        if any(os.path.exists(m) for m in busy):
            print("refusing a JVM run while a --busy marker exists")
            return 1
        os.makedirs(out, exist_ok=True)
        report = os.path.join(out, "env-grade.tsv")
        java = os.path.join(os.environ.get("JAVA_HOME", "/usr"), "bin", "java")
        with open(os.path.join(out, "run.log"), "w", encoding="utf-8") as log:
            subprocess.run(instances_command(java, classes, cpfile, REPO, report, only, seats, set_file), cwd=os.path.join(forge, "forge-gui-desktop"),
                           stdout=log, stderr=subprocess.STDOUT, timeout=RUN_TIMEOUT_S)
        rows = read_env_report(report)
        print(env_markdown(env_table(rows, by="hook"), ai_gated(rows)))
        return 0
    if len(args) == 2 and args[0] == "table":
        rows = read_env_report(args[1])
        gated = ai_gated(rows)
        print("By surface (the owner's grouping):\n" + env_markdown(env_table(rows), gated))
        print("\nBy hook family (the ledger's; the benchmark gates on this one):\n" + env_markdown(env_table(rows, by="hook"), gated))
        print(json.dumps(env_scores(env_table(rows, by="hook"), gated), indent=1, sort_keys=True))
        return 0
    if len(args) >= 3 and args[0] == "grade":
        forge, run, busy, rest = None, False, [], args[3:]
        while rest:
            flag = rest.pop(0)
            if flag == "--run":
                run = True
            elif flag == "--forge" and rest:
                forge = rest.pop(0)
            elif flag == "--busy" and rest:
                busy.append(rest.pop(0))
            else:
                print(__doc__)
                return 2
        v = grade(args[1], REPO, args[2], forge, run, busy)
        print(json.dumps(v, indent=1, sort_keys=True, default=sorted))
        return 0 if v["score"] is not None else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
