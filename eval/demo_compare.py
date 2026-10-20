#!/usr/bin/env python3
"""Two harnesses, one game: the demo's side by side. The same model plays the same deck against Forge's AI on the
same seed, once on the current harness and once on an older commit of it, and the viewer replays both in lockstep.

  python3 eval/demo_compare.py build-now                               # isolated build of this checkout's harness
  python3 eval/demo_compare.py build-then --commit e1324cfd            # worktree + isolated build of the older harness
  python3 eval/demo_compare.py decks                                   # the fixed deck sets (eval/demo/decks.json)
  python3 eval/demo_compare.py run --seed 4201 --deck-a vintage-esper-control --deck-b pioneer-red-deck-wins \\
      --model openrouter:openai/gpt-6-luna --budget-usd 0.40 --out runs/demo/pair-4201   # both arms in turn
  python3 eval/demo_server.py --game runs/demo/pair-4201/now --game runs/demo/pair-4201/then

How the older arm runs: `git worktree add` at the commit; DemoFeed (the display-only audience feed) is copied into
that worktree's overlay with the hooks it needs (port_feed), so both panes show both hands; that worktree's own acceptance script
compiles its overlay into an isolated classes directory; a stand-in Forge home runs those classes from the real
checkout, which is never written (the acceptance `scoring` stage's recipe); and that commit's own run_batch.py
launches the game. Both arms therefore run on the same pinned engine only if the commit pins it: build-then refuses a
commit whose FORGE_COMMIT differs from the checkout's (the comparison would then be harness and engine).

What it shows: the same start (same seed, decks, hands, Forge AI's draws) and different play from the first decision
the two harnesses answer differently. One pair is an illustration, never a measurement: no rate is computed here.
Every run passes --budget-usd; --fake plays both arms on the loopback fake model (no egress, no cost) to rehearse.
Stdlib only.
"""
import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puzzle_format  # noqa: E402  (forge_home: the checkout every eval tool finds)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Homebrew's openjdk@17 on Apple Silicon and on Intel Macs (keg-only: not on PATH).
BREW_JDKS = ("/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home",
             "/usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home")
SIBLINGS = os.path.dirname(REPO)  # the default worktree and build dirs sit beside the checkout
DECKS = os.path.join(REPO, "eval", "demo", "decks.json")
SPIKE = "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
OBS_ANCHOR = "    Player viewer() { return viewer; }\n"
OBS_HOOK = OBS_ANCHOR + """
    /** handle -> engine card id, a copy, for the demo's audience feed (DemoFeed) only. Ported by demo_compare.py. */
    Map<Integer, Integer> handleMap() {
        Map<Integer, Integer> m = new java.util.TreeMap<>();
        for (Map.Entry<Integer, Tracked> e : byHandle.entrySet()) m.put(e.getKey(), e.getValue().engineId);
        return m;
    }
"""
CTRL_ANCHOR = "            if (observer != null && MEMORY_TOOLS) contextMemory.setInspector(observer::inspect);\n"
CTRL_HOOK = CTRL_ANCHOR + "            DemoFeed.attach(game, seat, observer);  // display-only, ported by demo_compare.py\n"
WRITE_ANCHOR = "        static synchronized void write(String json) { if (out != null) out.println(json); }\n"
WRITE_HOOK = """        static synchronized void write(String json) {
            if (out == null) return;
            if (json.startsWith("{\\"t\\":\\"decision\\"")) DemoFeed.beforeDecisionRow();  // display only (DEMO_FEED)
            out.println(json);
        }
"""


def port_feed(tree, source=REPO):
    """Copy DemoFeed into an older overlay and add its three hooks; idempotent. Returns the hooks' files changed."""
    spike = os.path.join(tree, SPIKE)
    changed = []
    shutil.copyfile(os.path.join(source, SPIKE, "DemoFeed.java"), os.path.join(spike, "DemoFeed.java"))
    changed.append("DemoFeed.java")
    for name, anchor, hook in (("SeatObservation.java", OBS_ANCHOR, OBS_HOOK), ("LlmSpikeGui.java", CTRL_ANCHOR, CTRL_HOOK),
                               ("LlmSpikeGui.java", WRITE_ANCHOR, WRITE_HOOK)):
        path = os.path.join(spike, name)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if hook in text:
            continue
        if text.count(anchor) != 1:
            raise SystemExit("cannot port the feed into %s: its anchor line occurs %d times" % (path, text.count(anchor)))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(anchor, hook))
        changed.append(name)
    return changed


def jdk():
    """The JDK, in the order acceptance looks: JAVA_HOME, else Homebrew's openjdk@17 (macOS), else the javac on PATH
    (Linux). None when there is no JDK at all."""
    home = os.environ.get("JAVA_HOME")
    if home and os.path.isfile(os.path.join(home, "bin", "javac")):
        return home
    for brew in BREW_JDKS:
        if os.path.isdir(brew):
            return brew
    javac = shutil.which("javac")
    return os.path.dirname(os.path.dirname(os.path.realpath(javac))) if javac else None


def default_forge():
    """$FORGE_HOME as given when it is set (a mistyped one should fail, not build against another checkout), else the
    checkout every eval tool finds (puzzle_format.forge_home: ~/code/forge, /root/forge)."""
    env = os.environ.get("FORGE_HOME")
    return os.path.expanduser(os.path.expandvars(env)) if env else str(puzzle_format.forge_home())


def jdk_tool(name):
    home = jdk()
    return os.path.join(home, "bin", name) if home else name


def port_prices(tree, source=REPO):
    """The current price list into an older tree: its run_batch refuses --budget-usd for a model its own list
    lacks (GPT-6 Luna postdates e1324cfd). Pricing data only; nothing the seat sees."""
    rel = os.path.join("eval", "pricing", "prices.json")
    shutil.copyfile(os.path.join(source, rel), os.path.join(tree, rel))
    return "prices.json"


def standin_home(home, classes, classpath, forge, java):
    """A Forge home whose run-llm-headless.sh runs `classes` against the real checkout (never written). Paths are
    shell-quoted: a macOS home folder may hold a space."""
    os.makedirs(home, exist_ok=True)
    git = os.path.join(home, ".git")
    if not os.path.exists(git):
        os.symlink(os.path.join(forge, ".git"), git)
    script = os.path.join(home, "run-llm-headless.sh")
    with open(script, "w") as f:
        f.write("#!/bin/bash\ncd %s || exit 1\nCP=%s:\"$(cat %s)\"\nexec %s --add-opens java.base/java.lang=ALL-UNNAMED "
                "-Xmx3g -Dfile.encoding=UTF-8 -Djava.util.Arrays.useLegacyMergeSort=true -Dapple.awt.UIElement=true "
                "-Djava.awt.headless=true -cp \"$CP\" forge.spike.LlmSpikeHeadless\n"
                % (shlex.quote(os.path.join(forge, "forge-gui-desktop")), shlex.quote(classes), shlex.quote(classpath),
                   shlex.quote(java)))
    os.chmod(script, 0o755)
    return script


def arm_command(repo, forge_home, seed, deck_a, deck_b, model, budget, out):
    """One arm's run_batch.py command: the llm seat on `model` against Forge's AI, with the audience feed on."""
    return [sys.executable, os.path.join(repo, "eval", "run_batch.py"), "--seeds", str(seed), "--exploratory",
           "--jobs", "1", "--budget-usd", "%g" % budget, "--forge-home", forge_home,
           "--deck-a", deck_a, "--deck-b", deck_b, "--controller-a", "llm", "--controller-b", "ai",
           "--smart-a", model, "--fast-a", model, "--memory-tools", "--lookahead", "--context-profile", "v4-faithful",
           "--env", "DEMO_FEED=1", "--out", out]


def engine_patches(tree):
    """The engine patch sources in a tree's overlay (every main source outside the harness package)."""
    root = os.path.join(tree, "forge-overlay")
    out = []
    for d, _, files in os.walk(root):
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), root)
            if f.endswith(".java") and "/src/main/java/" in rel and "/forge/spike/" not in rel:
                out.append(rel)
    return sorted(out)


def pinned_paths(worktree):
    """The engine files both arms pin: every file this checkout or the older worktree patches."""
    return sorted(set(engine_patches(REPO)) | set(engine_patches(os.path.abspath(worktree))))


def engine_sources(tree, forge, paths):
    """{path: (origin, source bytes)}: the tree's overlay copy, or the pinned engine's stock source when the tree has
    no patch for it. A path with neither is refused, never guessed."""
    out = {}
    for rel in paths:
        mine = os.path.join(tree, "forge-overlay", rel)
        if os.path.isfile(mine):
            with open(mine, "rb") as f:
                out[rel] = ("overlay", f.read())
            continue
        stock = subprocess.run(["git", "-C", forge, "show", "HEAD:" + rel], capture_output=True)
        if stock.returncode != 0:
            raise SystemExit("no stock source for %s in %s" % (rel, forge))
        out[rel] = ("stock", stock.stdout)
    return out


def pin_record(sources):
    """engine-patches.txt: one `origin<TAB>sha12<TAB>path` line a file; run compares it with what the trees hold now."""
    return "".join("%s\t%s\t%s\n" % (o, hashlib.sha256(b).hexdigest()[:12], p) for p, (o, b) in sorted(sources.items()))


def pin_engine(tree, build, forge, paths):
    """Compile, into this arm's classes (first on its classpath), its own version of every engine file either arm
    patches (engine_sources). The checkout holds whatever install.sh last installed, so neither arm may inherit it.
    Records each file's origin and hash in engine-patches.txt."""
    sources = engine_sources(tree, forge, paths)
    src = os.path.join(build, "engine-src")
    shutil.rmtree(src, ignore_errors=True)
    files = []
    for rel, (_, data) in sorted(sources.items()):
        dest = os.path.join(src, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        files.append(dest)
    if files:
        with open(os.path.join(build, "classpath.txt")) as f:
            cp = os.path.join(build, "classes") + ":" + f.read().strip()
        r = subprocess.run([jdk_tool("javac"), "--release", "17", "-proc:none", "-nowarn", "-encoding", "UTF-8", "-cp", cp,
                            "-d", os.path.join(build, "classes")] + files, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("engine files did not compile:\n" + r.stderr[-2000:])
    with open(os.path.join(build, "engine-patches.txt"), "w") as f:
        f.write(pin_record(sources))
    print("engine files:", ", ".join("%s (%s)" % (os.path.basename(p), o) for p, (o, _) in sorted(sources.items()))
          or "none")


def check_pins(arms, forge, worktree):
    """Refuse a build whose pinned engine files (list or content) differ from what its tree would pin now."""
    paths = pinned_paths(worktree)
    for name, (tree, build) in arms.items():
        path = os.path.join(build, "engine-patches.txt")
        have = None
        if os.path.isfile(path):
            with open(path) as f:
                have = f.read()
        if have != pin_record(engine_sources(tree, forge, paths)):
            raise SystemExit("the %s build's engine files are not what its tree pins now: run build-%s again"
                             % (name, name))


def compile_arm(tree, build, forge, worktree):
    """That tree's own acceptance script compiles its overlay into build/classes; then both arms' engine patches,
    in this tree's version (pin_engine); then a stand-in Forge home."""
    os.makedirs(build, exist_ok=True)
    env = dict(os.environ, STAGES="preflight classpath compile", OUT=build, FORGE_HOME=forge)
    home = jdk()
    if home:  # acceptance's own lookup misses Intel Homebrew and falls to macOS's /usr/bin/javac stub
        env["JAVA_HOME"] = home
    r = subprocess.run(["bash", os.path.join(tree, "eval", "harness_acceptance.sh")], env=env, capture_output=True, text=True)
    print("\n".join(line for line in r.stdout.splitlines() if line.startswith(("PASS", "FAIL"))))
    if not os.path.isfile(os.path.join(build, "classes", "forge", "spike", "DemoFeed.class")):
        raise SystemExit("%s did not compile (see %s/javac.log)" % (tree, build))
    pin_engine(tree, build, forge, pinned_paths(worktree))
    print("stand-in home:", standin_home(os.path.join(build, "forge-home"), os.path.join(build, "classes"),
                                         os.path.join(build, "classpath.txt"), forge, jdk_tool("java")))


def build_now(a):
    compile_arm(REPO, os.path.abspath(a.now_build), a.forge, a.worktree)
    return 0


def build_then(a):
    tree, build = os.path.abspath(a.worktree), os.path.abspath(a.build)
    if not os.path.exists(os.path.join(a.forge, ".git")):  # a directory, or a worktree's .git file
        raise SystemExit("no Forge checkout at %s (set FORGE_HOME or pass --forge)" % a.forge)
    pin = subprocess.run(["git", "-C", a.forge, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    old = subprocess.run(["git", "-C", REPO, "show", "%s:FORGE_COMMIT" % a.commit], capture_output=True, text=True)
    old_pin = (old.stdout.split() or ["?"])[0] if old.returncode == 0 else "?"
    if old_pin != pin:
        raise SystemExit("refusing: %s pins engine %s, the checkout is at %s; the pair would compare harness AND engine"
                         % (a.commit, old_pin, pin))
    if not os.path.isdir(tree):
        subprocess.run(["git", "-C", REPO, "worktree", "add", "--detach", tree, a.commit], check=True)
    if not os.path.isdir(os.path.join(tree, "eval", "pricing")):
        raise SystemExit("%s has no eval/pricing: too old for a budgeted run" % a.commit)
    print("ported:", ", ".join(port_feed(tree) + [port_prices(tree)]))
    compile_arm(tree, build, a.forge, a.worktree)
    return 0


def direct_env(seed, deck_a, deck_b, model, out, base_url):
    """The env one loopback game needs when launched without run_batch (which refuses any openrouter: spec without
    the owner's egress consent, loopback included): the same conditions arm_command pins."""
    name = lambda p: os.path.splitext(os.path.basename(p))[0]
    return {"GAME_SEED": str(seed), "DECK_A": deck_a, "DECK_B": deck_b, "DECK_A_NAME": name(deck_a),
            "DECK_B_NAME": name(deck_b), "CONTROLLER_A": "llm", "CONTROLLER_B": "ai", "SMART_MODEL_A": model,
            "FAST_MODEL_A": model, "OPENROUTER_BASE_URL": base_url, "MEMORY_TOOLS": "1", "LOOKAHEAD": "1",
            "CONTEXT_PROFILE": "v4-faithful", "GAME_CAP_MS": "1800000", "DEMO_FEED": "1", "EVAL_OUT": out}


def deck_sets(path=DECKS):
    with open(path) as f:
        d = json.load(f)
    if d.get("version") != "demo-decks-v1":
        raise SystemExit("%s: unknown version %r" % (path, d.get("version")))
    return d["agent"], d["forge_ai"]


def pick_deck(name, allowed, side):
    """eval/decks/<name>.txt for a name in the fixed set; anything else is refused, so a demo pair is always one of
    the rehearsed match-ups."""
    if name not in allowed:
        raise SystemExit("refusing %s deck %r: pick one of %s (eval/demo/decks.json)" % (side, name, ", ".join(allowed)))
    return os.path.join(REPO, "eval", "decks", name + ".txt")


def decks(a):
    agent, ai = deck_sets()
    for title, s in (("the model's seat (--deck-a)", agent), ("Forge AI (--deck-b)", ai)):
        print(title)
        for k, v in s.items():
            print("  %-24s %s" % (k, v))
    return 0


def run(a):
    if a.budget_usd is None or a.budget_usd <= 0:
        raise SystemExit("refusing: pass --budget-usd (a paid pair costs money; --fake runs cost nothing)")
    agent, ai = deck_sets()
    deck_a, deck_b = pick_deck(a.deck_a, agent, "agent"), pick_deck(a.deck_b, ai, "Forge AI")
    out = os.path.abspath(a.out)
    arms = {"now": (REPO, os.path.abspath(a.now_build)), "then": (os.path.abspath(a.worktree), os.path.abspath(a.build))}
    homes = {name: os.path.join(build, "forge-home") for name, (_, build) in arms.items()}
    for name, home in homes.items():
        if not os.path.isfile(os.path.join(home, "run-llm-headless.sh")):
            raise SystemExit("no %s build at %s: run build-%s first" % (name, home, name))
    check_pins(arms, a.forge, a.worktree)
    env = dict(os.environ)
    model = a.model
    fake = None
    if a.fake:
        # The loopback fake model: no egress, no key, no cost. Both arms answer the same way.
        log = os.path.join(out, "fake_server.out")
        os.makedirs(out, exist_ok=True)
        fake = subprocess.Popen([sys.executable, os.path.join(REPO, "eval", "fake_model_server.py"), "--port", "0",
                                 "--policy", "first", "--answer-tool", "--native-calls", "6"],
                                stdout=open(log, "w"), stderr=subprocess.STDOUT)
        port = None
        for _ in range(40):
            time.sleep(0.25)
            with open(log) as f:
                for line in f:
                    if line.startswith("LISTENING"):
                        port = line.split()[1]
            if port:
                break
        if not port:
            fake.terminate()
            raise SystemExit("the fake model server never reported its port (see %s)" % log)
        env.pop("OPENROUTER_ALLOW_EGRESS", None)
        env["OPENROUTER_BASE_URL"] = "http://127.0.0.1:%s/v1" % port
        model = "openrouter:fake/demo-model"
    try:
        for name, (repo, _) in arms.items():
            if a.arm not in ("both", name):
                continue
            dest = os.path.join(out, name)
            if a.fake:
                os.makedirs(dest, exist_ok=True)
                cmd = ["bash", os.path.join(homes[name], "run-llm-headless.sh")]
                run_env = dict(env, **direct_env(a.seed, deck_a, deck_b, model, dest, env["OPENROUTER_BASE_URL"]))
                log = os.path.join(dest, "game-%d-0.log" % a.seed)
            else:
                cmd = arm_command(repo, homes[name], a.seed, deck_a, deck_b, model,
                                  a.budget_usd / 2 if a.arm == "both" else a.budget_usd, dest)
                run_env, log = env, None
            print("[%s]" % name, " ".join(cmd), flush=True)
            if not a.dry_run:
                if log:
                    with open(log, "w") as f:
                        subprocess.run(cmd, env=run_env, cwd=repo, check=False, stdout=f, stderr=subprocess.STDOUT)
                else:
                    subprocess.run(cmd, env=run_env, cwd=repo, check=False)
    finally:
        if fake:
            fake.terminate()
    print("replay: python3 eval/demo_server.py --game %s --game %s" % (os.path.join(out, "now"), os.path.join(out, "then")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-then")
    b.add_argument("--commit", required=True)
    n = sub.add_parser("build-now")
    sub.add_parser("decks")
    r = sub.add_parser("run")
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--deck-a", required=True, help="the model's deck, a name from `decks`")
    r.add_argument("--deck-b", required=True, help="Forge AI's deck, a name from `decks`")
    r.add_argument("--model", default="openrouter:openai/gpt-6-luna")
    r.add_argument("--budget-usd", type=float, help="the pair's budget: each arm gets half; with --arm, that arm all")
    r.add_argument("--out", required=True)
    r.add_argument("--fake", action="store_true", help="both arms on the loopback fake model (free)")
    r.add_argument("--dry-run", action="store_true", help="print the commands only")
    r.add_argument("--arm", choices=("both", "now", "then"), default="both",
                   help="play one arm only (its budget is then the whole --budget-usd), e.g. to redo a failed arm")
    for p in (b, n, r):
        p.add_argument("--worktree", default=os.path.join(SIBLINGS, "harness-then"))
        p.add_argument("--build", default=os.path.join(SIBLINGS, "harness-then-build"))
        p.add_argument("--now-build", default=os.path.join(SIBLINGS, "harness-now-build"))
        p.add_argument("--forge", default=default_forge())
    a = ap.parse_args(argv)
    return {"build-then": build_then, "build-now": build_now, "run": run, "decks": decks}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
