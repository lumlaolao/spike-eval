#!/bin/bash
# Reproducible harness acceptance suite (docs/harness/ACCEPTANCE.md; completion gate G10).
#
#   bash eval/harness_acceptance.sh                 # every stage
#   STAGES="compile javatests" bash eval/harness_acceptance.sh
#   OUT=/path bash eval/harness_acceptance.sh        # where evidence goes (default runs/harness-acceptance/<utc>)
#
# Stages, cheapest first (each writes its evidence under $OUT and a PASS/FAIL line to $OUT/summary.txt):
#   preflight  no live game JVMs, JDK 17, Forge checkout at the FORGE_COMMIT pin, decks present
#   classpath  offline Maven dependency classpath (module target/classes FIRST, then jars); Forge tree untouched
#   compile    overlay main+test sources -> $OUT/classes with javac --release 17 (the checkout is never rebuilt)
#   javatests  every *Test.java in the overlay via TestNG, MEMORY_TOOLS=1; fails on any failure OR skip, and
#              when the number of tests RUN differs from the number DECLARED (a runner that matches no tests
#              reports a green nothing -- that happened once with `make jtest`)
#   pytests    python3 -m unittest discover -s eval; eval/test_script_suites.py runs the script-style files
#              (module-level test_ functions, no TestCase: test_aggregate, test_make_sft_data, ...) in it
#   canary     seed-42 floor/floor and random/random games on the freshly compiled classes; asserts the JVM
#              actually loaded THIS build (class-load log), the pinned winner/turns/decision counts, and the
#              unchanged eval/defect_analyzer.py hard gates (ai=0, silent drops=0)
#   gaming     empty-reply and garbage-reply LLM seats vs Forge AI must not beat the floor seat (seed 42)
#   faketool   one MEMORY_TOOLS=1 game with the LLM seat on a loopback fake model (eval/fake_model_server.py):
#              real OpenRouter transport + parsing, memory tool round trips, one forced request timeout, no
#              Authorization header on the wire, analyzer gates, provenance stamped as loopback
#   faithful   the same loopback game under CONTEXT_PROFILE=v4-faithful: viewer-aware renderer in a real game,
#              no per-message legend (v24), menu options and state sharing seat handles, no legacy engine-id leak
#   nativetool a loopback game on the STANDARD tool protocol (native function calling, on by default):
#              tools sent as function definitions, real tool-call turns served through the same executor
#              and budget, the text grammar never sent. faketool/faithful/rules pin NATIVE_TOOLS=0: they
#              are the regression tests of the text-grammar fallback.
#   nativerefuse a loopback game whose provider answers 404 "no endpoints support tool use" to any request
#              carrying tools: each bridge drops to the text grammar after ONE refusal, the game line says
#              so, the seat keeps its lookups through the text loop, and nothing falls back to the Forge AI
#   answertool a loopback game on the default DECISION protocol (AnswerTool): every decision is answered
#              by calling `answer` (schema built from its legal values), lookups and `set_plan` share the
#              conversation, one out-of-range answer is corrected in the same conversation; no decision
#              read from text, none lost, tool_choice always auto, the game line stamps answer_tool
#   lookahead  a loopback game with LOOKAHEAD=1 and the fake server in --policy simulate: the real
#              PlayerLookahead.previewCast path runs (preview + re-ask), at least one targeting preview is
#              produced, and no AI fallback occurs
#   rules      a loopback game with RULES_TOOL=1 + MEMORY_TOOLS=1: the real RulesTool lookup runs end to
#              end (fake server asks `RULES SEARCH`), the game stamps rules_tool provenance, every prompt
#              carries the RULES grammar, and no single result exceeds the page cap (context-bound)
#
# SCRIPTING RULES (a broken gate reports PASS, which is worse than no gate): absolute paths; no `declare -A`
# (macOS bash 3.2); never `grep -c X f || echo 0`; assert the build under test is the build that ran.
# ALSO: the verdict heredocs live inside `$( ... )`, and bash 3.2 scans for the closing paren BEFORE it
# parses the heredoc, so an unbalanced quote — including one apostrophe in a body comment — breaks the
# WHOLE file. Keep heredoc bodies free of `'`. (Cost me a debugging session; do not "fix" it by quoting.)
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
FORGE="${FORGE_HOME:-$HOME/code/forge}"
JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}"
# Off the Mac the Homebrew default does not exist: take the JDK that javac on PATH belongs to.
if [ ! -x "$JAVA_HOME/bin/javac" ] && command -v javac >/dev/null 2>&1; then
  JAVA_HOME="$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")"
fi
JAVA="$JAVA_HOME/bin/java"; JAVAC="$JAVA_HOME/bin/javac"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT:-$REPO/runs/harness-acceptance/$STAMP}"
ALL_STAGES="preflight classpath compile javatests pytests canary gaming faketool faithful lookahead nativetool nativerefuse answertool rules"
STAGES="${STAGES:-$ALL_STAGES}"
EXPECTED="$REPO/eval/harness_acceptance.expected"
OVERLAY="$REPO/forge-overlay/forge-gui-desktop/src"
LEDGER="$REPO/docs/harness/controller-ownership.tsv"
DECK_A_FILE="$REPO/eval/decks/grixis-reanimator.txt"
DECK_B_FILE="$REPO/eval/decks/mardu-energy.txt"
GAME_CAP_MS="${GAME_CAP_MS:-900000}"
mkdir -p "$OUT"
# The JVM resolves symlinks (notably macOS /tmp -> /private/tmp) in class-load
# sources. Compare physical paths so a valid isolated build is recognized.
OUT="$(cd "$OUT" && pwd -P)"
SUMMARY="$OUT/summary.txt"
: > "$SUMMARY"
FAILED=0

say()  { echo "[$(date -u +%H:%M:%S)] $*"; }
pass() { echo "PASS $1: $2" | tee -a "$SUMMARY"; }
fail() { echo "FAIL $1: $2" | tee -a "$SUMMARY"; FAILED=1; }
# Canary pins can differ by OS: the same commit plays seed 42 to a different count on Linux than on
# the reference Mac (see the expectations file). A "<os>.<key>" line wins over the plain "<key>".
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')"
want() { # $1 key -> value from the expectations file
  # A key written twice prints DUPLICATE, which matches no outcome: a re-pin appended under the old
  # line must fail the run, not be silently ignored.
  awk -F= -v k="$1" -v pk="$PLATFORM.$1" '$1==pk {p=$2; np++} $1==k {v=$2; n++}
    END {if (np) print (np>1 ? "DUPLICATE" : p); else print (n>1 ? "DUPLICATE" : v)}' "$EXPECTED"
}
pinset() { # $1 arm -> which pins the canary is checked against
  # want() looks each key up on its own, so an OS pin set missing one outcome key would be checked
  # against a mix of OS and plain values while the label said "<os> pins" (#113): that is PARTIAL.
  local k have="" miss=""
  for k in winner turns decisions; do
    if grep -q "^$PLATFORM\.$1\.$k=" "$EXPECTED"; then have="$have $k"; else miss="$miss $k"; fi
  done
  if [ -z "$have" ]; then echo "plain (Mac) pins"
  elif [ -z "$miss" ]; then echo "$PLATFORM pins"
  else echo "PARTIAL $PLATFORM pins (has:$have; missing:$miss)"; fi
}
# md5 is macOS, md5sum is Linux. With neither, both sides of a comparison would hash to "" and the
# gate would pass on anything; an exit inside $( ) does not stop this script, so the caller checks.
digest() {
  if command -v md5 >/dev/null 2>&1; then md5
  elif command -v md5sum >/dev/null 2>&1; then md5sum | cut -d" " -f1; fi
}
has_stage() { case " $STAGES " in *" $1 "*) return 0;; *) return 1;; esac; }
# A stage list that names no real stage ran nothing and still printed PASS (`STAGES=compile,javatests`
# is one unknown name). Every name must be a stage this script runs.
for _s in $STAGES; do
  case " $ALL_STAGES " in
    *" $_s "*) ;;
    *) echo "unknown stage '$_s' (STAGES is space-separated: STAGES=\"compile javatests\")" >&2; exit 2;;
  esac
done

ADDOPENS="--add-opens java.desktop/java.beans=ALL-UNNAMED --add-opens java.desktop/javax.swing.border=ALL-UNNAMED \
--add-opens java.desktop/javax.swing.event=ALL-UNNAMED --add-opens java.desktop/sun.swing=ALL-UNNAMED \
--add-opens java.desktop/java.awt.image=ALL-UNNAMED --add-opens java.desktop/java.awt.color=ALL-UNNAMED \
--add-opens java.desktop/sun.awt.image=ALL-UNNAMED --add-opens java.desktop/javax.swing=ALL-UNNAMED \
--add-opens java.desktop/java.awt=ALL-UNNAMED --add-opens java.desktop/java.awt.font=ALL-UNNAMED \
--add-opens java.base/java.util=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED \
--add-opens java.base/java.lang.reflect=ALL-UNNAMED --add-opens java.base/java.text=ALL-UNNAMED \
--add-opens java.base/jdk.internal.misc=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED \
--add-opens java.base/java.nio=ALL-UNNAMED --add-opens java.base/java.math=ALL-UNNAMED \
--add-opens java.base/java.util.concurrent=ALL-UNNAMED --add-opens java.base/java.net=ALL-UNNAMED"

# ---------------------------------------------------------------- preflight
if has_stage preflight; then
  ok=1
  # Match only the HARNESS's own game JVMs (forge.spike.LlmSpike*), not every Java process on the
  # machine: the old `pgrep -x java` also flagged the user's IDE, Gradle daemon or Minecraft and
  # failed a perfectly idle machine.
  if pgrep -f 'forge\.spike\.LlmSpike' >/dev/null 2>&1; then fail preflight "harness game JVMs are live; never build or measure while games run"; ok=0; fi
  [ -x "$JAVA" ] && [ -x "$JAVAC" ] || { fail preflight "JDK 17 not found at $JAVA_HOME"; ok=0; }
  [ -d "$FORGE/forge-gui-desktop/target/classes" ] || { fail preflight "engine classes missing: $FORGE/forge-gui-desktop/target/classes (build the pinned checkout once)"; ok=0; }
  PIN="$(cat "$REPO/FORGE_COMMIT" 2>/dev/null)"; CUR="$(git -C "$FORGE" rev-parse HEAD 2>/dev/null || echo unknown)"
  if [ "$PIN" != "$CUR" ]; then fail preflight "Forge checkout $CUR is not the pinned engine $PIN (results would not be comparable)"; ok=0; fi
  for f in "$DECK_A_FILE" "$DECK_B_FILE" "$EXPECTED" "$LEDGER"; do [ -f "$f" ] || { fail preflight "missing $f"; ok=0; }; done
  command -v python3 >/dev/null || { fail preflight "python3 missing"; ok=0; }
  if [ "$ok" = 1 ]; then
    JV="$("$JAVA" -version 2>&1 | head -1)"
    pass preflight "engine $CUR, $JV, decks + ledger + expectations present, no live JVMs"
  fi
fi

# ---------------------------------------------------------------- classpath
CP_FILE="$OUT/classpath.txt"
if has_stage classpath; then
  DEPS="$OUT/deps.txt"
  if [ -n "${HARNESS_DEPS_FILE:-}" ] && [ -f "$HARNESS_DEPS_FILE" ]; then
    cp "$HARNESS_DEPS_FILE" "$DEPS"
  else
    # Two guards, because target/ is gitignored in the checkout: git status catches source edits,
    # and a mtime marker catches ANY file Maven writes under the engine's target/ trees (a rebuild
    # of the pinned classes would be invisible to git and must fail this stage).
    BEFORE="$(git -C "$FORGE" status --porcelain 2>/dev/null | digest)"
    MARK="$OUT/.mvn-marker"; touch "$MARK"; sleep 1
    ( cd "$FORGE" && mvn -o -q -pl forge-gui-desktop -am dependency:build-classpath -Dmdep.outputFile="$DEPS" -Dmdep.includeScope=test ) >"$OUT/mvn-classpath.log" 2>&1
    AFTER="$(git -C "$FORGE" status --porcelain 2>/dev/null | digest)"
    if [ -z "$BEFORE" ]; then fail classpath "no md5 or md5sum on PATH: cannot check the Forge working tree"
    elif [ "$BEFORE" != "$AFTER" ]; then fail classpath "mvn dependency:build-classpath changed the Forge working tree"; fi
    TOUCHED="$(find "$FORGE"/forge-*/target -type f -newer "$MARK" 2>/dev/null | head -3)"
    [ -z "$TOUCHED" ] || fail classpath "Maven wrote into the engine's target/ trees (pinned classes may have been rebuilt): $TOUCHED"
  fi
  if [ -s "$DEPS" ]; then
    MODS=""
    for m in forge-gui-desktop forge-core forge-game forge-ai forge-gui; do MODS="$MODS$FORGE/$m/target/classes:"; done
    printf '%s%s' "$MODS" "$(cat "$DEPS")" > "$CP_FILE"
    N="$(tr ':' '\n' < "$CP_FILE" | wc -l | tr -d ' ')"
    if tr ':' '\n' < "$CP_FILE" | grep -q 'testng'; then pass classpath "$N entries (module target/classes first), TestNG present"
    else fail classpath "TestNG not on the dependency classpath"; fi
  else
    fail classpath "could not build the offline dependency classpath (see $OUT/mvn-classpath.log)"
  fi
fi
[ -f "$CP_FILE" ] || CP_FILE="${HARNESS_CLASSPATH_FILE:-$CP_FILE}"

# ---------------------------------------------------------------- compile
CLASSES="$OUT/classes"
if has_stage compile; then
  if [ ! -f "$CP_FILE" ]; then fail compile "no classpath file ($CP_FILE)"; else
    rm -rf "$CLASSES"; mkdir -p "$CLASSES"
    "$JAVAC" --release 17 -encoding UTF-8 -proc:none -nowarn -cp "$(cat "$CP_FILE")" -d "$CLASSES" \
      "$OVERLAY"/main/java/forge/spike/*.java "$OVERLAY"/test/java/forge/spike/*.java >"$OUT/javac.log" 2>&1
    RC=$?
    NC="$(find "$CLASSES" -name '*.class' | wc -l | tr -d ' ')"
    # The checkout build (checkstyle) rejects unused and duplicate imports; this javac does not.
    python3 "$REPO/eval/java_imports.py" "$OVERLAY" >"$OUT/java_imports.log" 2>&1; IRC=$?
    if [ $RC -ne 0 ] || [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail compile "javac rc=$RC (see $OUT/javac.log)"
    elif [ $IRC -ne 0 ]; then fail compile "imports the Forge checkout build rejects: $(head -3 "$OUT/java_imports.log" | tr '\n' ' ')"
    else pass compile "$NC classes from overlay sources (isolated dir; checkout untouched); imports clean"; fi
  fi
fi

# ---------------------------------------------------------------- javatests
if has_stage javatests; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail javatests "no compiled classes"; else
    TCLASSES="$(ls "$OVERLAY"/test/java/forge/spike/*Test.java | xargs -n1 basename | sed 's/\.java$//;s/^/forge.spike./' | paste -sd, -)"
    # A test class that extends another runs the parent's tests too (GoldenChecksToolTest reruns every golden
    # check on the tool protocol), so its parent's @Test methods are declared once more for it.
    DECLARED="$(python3 - "$OVERLAY"/test/java/forge/spike/*Test.java <<'PYEOF'
import re,sys,os
count={}; parent={}
for f in sys.argv[1:]:
    t=open(f,encoding="utf-8").read(); n=os.path.basename(f)[:-5]
    count[n]=len(re.findall(r"(?m)^[ \t]+@Test", t))
    m=re.search(r"class "+n+r" extends (\w+Test)\b", t)
    if m: parent[n]=m.group(1)
def total(n, seen=()):
    return count.get(n,0)+(total(parent[n], seen+(n,)) if n in parent and parent[n] not in seen else 0)
print(sum(total(n) for n in count))
PYEOF
)"
    ( cd "$FORGE/forge-gui-desktop" && MEMORY_TOOLS=1 "$JAVA" -Dapple.awt.UIElement=true \
        -Dharness.ownership.tsv="$LEDGER" -Dharness.rules.file="$REPO/data/rules/MagicCompRules.txt" -Dharness.decks.dir="$REPO/eval/decks" -Dharness.tooluse.json="$REPO/eval/tooluse/tools.json" -cp "$CLASSES:$(cat "$CP_FILE")" \
        org.testng.TestNG -testclass "$TCLASSES" -d "$OUT/testng" ) >"$OUT/javatests.log" 2>&1
    RES="$(python3 - "$OUT/testng/testng-results.xml" <<'EOF'
import re,sys
try: x=open(sys.argv[1]).read()
except Exception as e: print("ERR no results:", e); sys.exit(0)
m=re.search(r'<testng-results[^>]*\btotal="(\d+)"[^>]*\bpassed="(\d+)"[^>]*\bfailed="(\d+)"[^>]*\bskipped="(\d+)"', x)
if not m:
    m2=re.search(r'skipped="(\d+)"[^>]*failed="(\d+)"[^>]*ignored="(\d+)"[^>]*total="(\d+)"[^>]*passed="(\d+)"', x)
    if not m2: print("ERR unparsed"); sys.exit(0)
    sk,f,ig,t,p=map(int,m2.groups())
else:
    t,p,f,sk=map(int,m.groups())
fails=re.findall(r'<test-method [^>]*name="([^"]+)"[^>]*status="FAIL"', x)
print("total=%d passed=%d failed=%d skipped=%d fails=%s"%(t,p,f,sk,",".join(fails)))
EOF
)"
    echo "$RES" > "$OUT/javatests.summary"
    T="$(echo "$RES" | sed -n 's/.*total=\([0-9]*\).*/\1/p')"; F="$(echo "$RES" | sed -n 's/.*failed=\([0-9]*\).*/\1/p')"; S="$(echo "$RES" | sed -n 's/.*skipped=\([0-9]*\).*/\1/p')"
    OWN="$(grep -o '\[OWNERSHIP\].*' "$OUT/javatests.log" | head -1)"
    # Enforce the pinned ownership baseline. This key used to be decorative (never read); a pin the
    # script ignores is not a pin. Both the test (<=) and this (==) must be updated deliberately.
    OWN_N="$(echo "$OWN" | sed -n 's/.*: \([0-9]*\)$/\1/p')"
    OWN_EXP="$(sed -n 's/^ownership\.silent_ai_strategic_max=\([0-9]*\).*/\1/p' "$EXPECTED" | head -1)"
    if [ -z "$T" ]; then fail javatests "$RES"
    elif [ "$F" != 0 ] || [ "$S" != 0 ]; then fail javatests "$RES"
    elif [ "$T" != "$DECLARED" ]; then fail javatests "ran $T tests but $DECLARED @Test methods are declared (discovery gap)"
    elif [ -n "$OWN_EXP" ] && [ "$OWN_N" != "$OWN_EXP" ]; then
      fail javatests "ownership count $OWN_N != pinned $OWN_EXP (update harness_acceptance.expected AND ControllerOwnershipTest deliberately)"
    else pass javatests "$T/$DECLARED declared tests passed, 0 skipped. $OWN"; fi
  fi
fi

# ---------------------------------------------------------------- pytests
if has_stage pytests; then
  ( cd "$REPO" && python3 -m unittest discover -s eval -p 'test_*.py' ) >"$OUT/pytests.log" 2>&1; RC=$?
  RAN="$(grep -o 'Ran [0-9]* tests' "$OUT/pytests.log" | tail -1)"
  if [ $RC -eq 0 ] && [ -n "$RAN" ]; then pass pytests "$RAN OK (script-style files included)"
  else fail pytests "unittest rc=$RC ($RAN)"; fi
fi

# ---------------------------------------------------------------- game runner
play() { # $1 tag  $2 seed  $3.. extra env (VAR=value)
  local tag="$1" seed="$2"; shift 2
  local dir="$OUT/$tag"; rm -rf "$dir"; mkdir -p "$dir"
  ( cd "$FORGE/forge-gui-desktop" && env GAME_SEED="$seed" EVAL_OUT="$dir" DECK_A="$DECK_A_FILE" DECK_B="$DECK_B_FILE" \
      GAME_CAP_MS="$GAME_CAP_MS" "$@" "$JAVA" $ADDOPENS -Xmx4096m -Dio.netty.tryReflectionSetAccessible=true \
      -Dfile.encoding=UTF-8 -Djava.util.Arrays.useLegacyMergeSort=true -Dsun.java2d.d3d=false -Dapple.awt.UIElement=true \
      -Xlog:class+load=info:file="$dir/classload.log" -cp "$CLASSES:$(cat "$CP_FILE")" forge.spike.LlmSpikeHeadless ) >"$dir/game.log" 2>&1
  echo $? > "$dir/exit_code"
}
check_build_loaded() { # $1 dir -> 0 if the JVM loaded LlmSpikeGui from $CLASSES
  grep -Fq "forge.spike.LlmSpikeGui source: file:$CLASSES/" "$1/classload.log" 2>/dev/null
}
game_facts() { # $1 dir -> "winner turns decisions capped" from the jsonl (or ERR)
  python3 - "$1" <<'EOF'
import glob,json,sys
files=glob.glob(sys.argv[1]+"/game-*.jsonl")
if len(files)!=1: print("ERR jsonl files:",len(files)); sys.exit(0)
game=None; dec=0; rows=[]
for line in open(files[0]):
    try: d=json.loads(line)
    except Exception: continue
    if d.get("t")=="game": game=d
    elif d.get("t")=="decision": dec+=1
if game is None: print("ERR no game row"); sys.exit(0)
print(game.get("winner"), game.get("turns"), dec, game.get("capped", game.get("valid","?")))
EOF
}

# ---------------------------------------------------------------- canary
if has_stage canary; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail canary "no compiled classes"; else
    for arm in floor random; do
      seed="$(want $arm.seed)"
      case "$seed" in ''|*[!0-9]*) fail "canary-$arm" "seed pin '$seed' is missing, duplicated or not a number in $EXPECTED"; continue;; esac
      case "$(pinset $arm)" in PARTIAL*) fail "canary-$arm" "$(pinset $arm) in $EXPECTED: pin all three outcome keys for this OS or none"; continue;; esac
      say "canary $arm/$arm seed $seed"
      play "canary-$arm" "$seed" CONTROLLER_A=$arm CONTROLLER_B=$arm
      dir="$OUT/canary-$arm"
      if ! check_build_loaded "$dir"; then fail "canary-$arm" "JVM did not load LlmSpikeGui from $CLASSES (build-swap; see $dir/classload.log)"; continue; fi
      facts="$(game_facts "$dir")"; set -- $facts
      if [ "$1" = ERR ]; then fail "canary-$arm" "$facts (exit $(cat "$dir/exit_code"))"; continue; fi
      W="$1"; T="$2"; D="$3"
      ( cd "$REPO" && python3 eval/defect_analyzer.py "$dir" --json "$dir/defects.json" ) >"$dir/defects.txt" 2>&1; ARC=$?
      AI="$(grep -A1 'tier=ai hard invariant' "$dir/defects.txt" | grep -o 'count=[0-9]*')"
      DROP="$(grep -o 'total silent drops=[0-9]*' "$dir/defects.txt")"
      EW="$(want $arm.winner)"; ET="$(want $arm.turns)"; ED="$(want $arm.decisions)"
      if [ $ARC -ne 0 ]; then fail "canary-$arm" "defect_analyzer rc=$ARC ($AI, $DROP; see $dir/defects.txt)"
      elif [ "$W" != "$EW" ] || [ "$T" != "$ET" ] || [ "$D" != "$ED" ]; then
        fail "canary-$arm" "outcome winner=$W turns=$T decisions=$D differs from pinned winner=$EW turns=$ET decisions=$ED, $(pinset $arm) (re-pin deliberately if an auto/floor path changed)"
      else pass "canary-$arm" "seed $seed: winner=$W turns=$T decisions=$D as pinned ($(pinset $arm)); $AI $DROP; build verified via class-load log"; fi
    done
  fi
fi

# ---------------------------------------------------------------- gaming
# A seat whose model never answers (empty replies, then garbage) must not score above the floor seat
# against the same opponent on the same seed: the harness's fallbacks must not play for it
# (eval/BENCHMARK-RESEARCH.md, point 10). Also: no Forge-AI decisions and no harness-decided choice.
if has_stage gaming; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail gaming "no compiled classes"; else
    say "gaming: floor seat vs Forge AI, then empty-reply and garbage-reply LLM seats vs Forge AI (seed 42)"
    # MULLIGAN_HEURISTIC=0 SKIP_HEURISTICS=0: the recipe scored games use, so nothing is decided for the seat.
    play gaming-floor 42 CONTROLLER_A=floor CONTROLLER_B=ai MULLIGAN_HEURISTIC=0 SKIP_HEURISTICS=0
    for pol in empty garbage; do
      sdir="$OUT/gaming-$pol-server"; mkdir -p "$sdir"; : > "$sdir/fake_requests.jsonl"
      python3 "$REPO/eval/fake_model_server.py" --port 0 --policy $pol --log "$sdir/fake_requests.jsonl" >"$sdir/fake_server.out" 2>&1 &
      FAKE_PID=$!
      PORT=""; for i in 1 2 3 4 5 6 7 8 9 10; do PORT="$(awk '/^LISTENING/{print $2}' "$sdir/fake_server.out" 2>/dev/null)"; [ -n "$PORT" ] && break; sleep 0.5; done
      play gaming-$pol 42 CONTROLLER_A=llm CONTROLLER_B=ai SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
          OPENROUTER_BASE_URL="http://127.0.0.1:$PORT/v1" OPENROUTER_TIMEOUT_MS=4000 CONTEXT_PROFILE=v4-faithful SMART_BUDGET=400 FAST_BUDGET=400 MULLIGAN_HEURISTIC=0 SKIP_HEURISTICS=0
      kill $FAKE_PID 2>/dev/null; wait $FAKE_PID 2>/dev/null
    done
    GVERDICT="$(cd "$REPO/eval" && python3 - "$OUT" <<'PYEOF'
import glob, json, sys
import match_review
out = sys.argv[1]; problems = []; facts = []
def load(tag):
    files = glob.glob("%s/%s/game-*.jsonl" % (out, tag))
    if len(files) != 1: return None, []
    rows = []
    for l in open(files[0]):
        try: rows.append(json.loads(l))
        except ValueError: pass
    return next((r for r in rows if r.get("t") == "game"), None), rows
floor, _ = load("gaming-floor")
if floor is None: problems.append("floor game has no game row")
for pol in ("empty", "garbage"):
    g, rows = load("gaming-" + pol)
    if g is None: problems.append("%s: no game row" % pol); continue
    dec = [r for r in rows if r.get("t") == "decision"]
    model = sum(1 for r in dec if r.get("tier") in ("smart", "fast"))
    ai = sum(1 for r in dec if r.get("tier") == "ai")
    taken = sum(1 for r in dec if r.get("tier") == "auto" and match_review.classify_auto(r) == "TAKEN")
    if floor is not None and g.get("winner") == "A" and floor.get("winner") != "A":
        problems.append("%s seat won where the floor seat lost" % pol)
    if ai: problems.append("%s: %d Forge-AI decisions" % (pol, ai))
    if taken: problems.append("%s: %d harness-decided (TAKEN) choices" % (pol, taken))
    asked = sum(1 for _ in open("%s/gaming-%s-server/fake_requests.jsonl" % (out, pol)))
    if asked < 3: problems.append("%s: the fake model was asked %d times (was the seat asked?)" % (pol, asked))
    played = [r.get("fallback") for r in dec if r.get("tier") == "auto" and r.get("type") in ("play", "attackers")
              and r.get("fallback") and "->PASS" not in r["fallback"] and "->none" not in r["fallback"]]
    if played: problems.append("%s: the harness acted for the seat: %s" % (pol, sorted(set(played))[:3]))
    log = open("%s/gaming-%s/game.log" % (out, pol), errors="replace").read()
    if "[FALLBACK->AI]" in log: problems.append("%s: [FALLBACK->AI] in log" % pol)
    facts.append("%s winner=%s turns=%s asked=%d model_rows=%d" % (pol, g.get("winner"), g.get("turns"), asked, model))
if floor is not None: facts.insert(0, "floor winner=%s turns=%s" % (floor.get("winner"), floor.get("turns")))
print(("FAIL " if problems else "PASS ") + "; ".join(problems + facts))
PYEOF
)"
    case "$GVERDICT" in PASS*) pass gaming "${GVERDICT#PASS }";; *) fail gaming "${GVERDICT#FAIL }";; esac
  fi
fi

# ---------------------------------------------------------------- faketool
if has_stage faketool; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail faketool "no compiled classes"; else
    dir="$OUT/faketool"; sdir="$OUT/faketool-server"; mkdir -p "$dir" "$sdir"
    : > "$sdir/fake_requests.jsonl"
    # Server files live OUTSIDE the game dir: play() recreates the game dir, which would unlink the log.
    python3 "$REPO/eval/fake_model_server.py" --port 0 --policy pass --log "$sdir/fake_requests.jsonl" \
        --stall-call 3 --stall-ms 9000 --memory-call 6 >"$sdir/fake_server.out" 2>&1 &
    FAKE_PID=$!
    PORT=""; for i in 1 2 3 4 5 6 7 8 9 10; do PORT="$(awk '/^LISTENING/{print $2}' "$sdir/fake_server.out" 2>/dev/null)"; [ -n "$PORT" ] && break; sleep 0.5; done
    if [ -z "$PORT" ]; then fail faketool "fake model server did not start"; kill $FAKE_PID 2>/dev/null; else
      say "faketool: LLM seat A on loopback fake model at port $PORT (B = floor), MEMORY_TOOLS=1, one 9s stall vs 4s deadline"
      play faketool 42 CONTROLLER_A=llm CONTROLLER_B=floor SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
          OPENROUTER_BASE_URL="http://127.0.0.1:$PORT/v1" OPENROUTER_TIMEOUT_MS=4000 MEMORY_TOOLS=1 NATIVE_TOOLS=0 SMART_BUDGET=60 FAST_BUDGET=60
      kill $FAKE_PID 2>/dev/null; wait $FAKE_PID 2>/dev/null
      if ! check_build_loaded "$dir"; then fail faketool "JVM did not load LlmSpikeGui from $CLASSES"; else
        ( cd "$REPO" && python3 eval/defect_analyzer.py "$dir" --json "$dir/defects.json" ) >"$dir/defects.txt" 2>&1; ARC=$?
        VERDICT="$(python3 - "$dir" "$ARC" "$sdir" <<'EOF'
import glob,json,sys,re
d=sys.argv[1]; arc=int(sys.argv[2]); sdir=sys.argv[3]; problems=[]; facts=[]
files=glob.glob(d+"/game-*.jsonl")
if len(files)!=1: print("FAIL jsonl files=%d"%len(files)); sys.exit(0)
game=None; tiers={}; calls=0; tools=[]; ai_rows=0; retried=0
for line in open(files[0]):
    try: r=json.loads(line)
    except Exception: continue
    t=r.get("t")
    if t=="game": game=r
    elif t=="decision":
        tiers[r.get("tier")]=tiers.get(r.get("tier"),0)+1
        if r.get("tier")=="ai": ai_rows+=1
        if (r.get("attempts") or 0)>1 and (r.get("retry_ms") or 0)>0: retried+=1
    elif t=="context_call": calls+=1
    elif t=="context_tool": tools.append(r)
if game is None: problems.append("no game row")
else:
    if "127.0.0.1" not in str(game.get("openrouter_base_url")): problems.append("openrouter_base_url not loopback: %s"%game.get("openrouter_base_url"))
    if game.get("served_smart_a")!="fake/harness-model": problems.append("served_smart_a=%s (fake model identity not stamped)"%game.get("served_smart_a"))
    if game.get("memory_tools") is not True: problems.append("memory_tools not stamped true")
    facts.append("winner=%s turns=%s"%(game.get("winner"),game.get("turns")))
model_rows=tiers.get("smart",0)+tiers.get("fast",0)
if model_rows<5: problems.append("only %d model-tier decision rows"%model_rows)
if ai_rows: problems.append("%d tier=ai rows"%ai_rows)
if calls<5: problems.append("only %d context_call rows"%calls)
ok_tools=[x for x in tools if not str(x.get("result","")).startswith("ERROR")]
if len(ok_tools)<2: problems.append("expected >=2 successful context_tool rows, got %d (of %d)"%(len(ok_tools),len(tools)))
reqs=[json.loads(l) for l in open(sdir+"/fake_requests.jsonl")] if glob.glob(sdir+"/fake_requests.jsonl") else []
if not reqs: problems.append("fake server saw no requests")
if any(r.get("has_authorization") for r in reqs): problems.append("Authorization header sent to loopback endpoint")
if not any(r.get("stalled") for r in reqs): problems.append("stall never injected")
log=open(d+"/game.log",errors="replace").read()
# Either deadline is a real bound: the header timeout (server never answered) or the wall-clock one.
if "wall-clock timeout" not in log and "HttpTimeoutException" not in log: problems.append("bridge deadline never fired despite the 9s stall")
if retried<1: problems.append("no decision row records a retried attempt (attempts>1, retry_ms>0) after the stall")
if "[FALLBACK->AI]" in log: problems.append("[FALLBACK->AI] present")
if arc!=0: problems.append("defect_analyzer rc=%d"%arc)
facts.append("model_rows=%d context_calls=%d tool_rows=%d/%d fake_requests=%d stalled=%d retried_rows=%d"%(model_rows,calls,len(ok_tools),len(tools),len(reqs),sum(1 for r in reqs if r.get("stalled")),retried))
print(("FAIL " if problems else "PASS ")+"; ".join(problems+facts))
EOF
)"
        case "$VERDICT" in PASS*) pass faketool "${VERDICT#PASS }";; *) fail faketool "${VERDICT#FAIL }";; esac
      fi
    fi
  fi
fi

# ---------------------------------------------------------------- lookahead (G8)
if has_stage lookahead; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail lookahead "no compiled classes"; else
    dir="$OUT/lookahead"; sdir="$OUT/lookahead-server"; mkdir -p "$dir" "$sdir"
    # --policy simulate makes the fake model ask for a preview on every play prompt that advertises
    # SIMULATE, so the real preview path (previewCast -> re-ask) is exercised without any egress.
    # ANSWER_TOOL=0: this is the text protocol's preview (the Claude CLI lane and the opt-out); with the
    # answer tool on, the preview is a function call (nativetool stage).
    python3 "$REPO/eval/fake_model_server.py" --port 0 --policy simulate --log "$sdir/fake_requests.jsonl" \
        >"$sdir/fake_server.out" 2>&1 &
    FAKE_PID=$!
    PORT=""; for i in 1 2 3 4 5 6 7 8 9 10; do PORT="$(awk '/^LISTENING/{print $2}' "$sdir/fake_server.out" 2>/dev/null)"; [ -n "$PORT" ] && break; sleep 0.5; done
    if [ -z "$PORT" ]; then fail lookahead "fake model server did not start"; kill $FAKE_PID 2>/dev/null; else
      say "lookahead: LLM seat A on loopback fake model (policy simulate), LOOKAHEAD=1 (burn mirror)"
      play lookahead 42 CONTROLLER_A=llm CONTROLLER_B=floor SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
          OPENROUTER_BASE_URL="http://127.0.0.1:$PORT/v1" LOOKAHEAD=1 ANSWER_TOOL=0 GAME_CAP_MS=120000 \
          DECK_A="$REPO/eval/decks/burn.txt" DECK_B="$REPO/eval/decks/burn.txt" DECK_A_NAME=Burn DECK_B_NAME=BurnB
      kill $FAKE_PID 2>/dev/null; wait $FAKE_PID 2>/dev/null
      if ! check_build_loaded "$dir"; then fail lookahead "JVM did not load LlmSpikeGui from $CLASSES"; else
        LOOK="$(grep -c '\[ROUTER\] lookahead' "$dir/game.log" 2>/dev/null)"; LOOK="${LOOK:-0}"
        TGT="$(grep -c 'assuming target:' "$dir/game.log" 2>/dev/null)"; TGT="${TGT:-0}"
        AILOG="$(grep -c '\[FALLBACK->AI\]' "$dir/game.log" 2>/dev/null)"; AILOG="${AILOG:-0}"
        PFAIL="$(grep -c '\[ROUTER\] lookahead.*preview failed' "$dir/game.log" 2>/dev/null)"; PFAIL="${PFAIL:-0}"
        # At most one model controller. (This stage's profile prints no banner, so the count is 0 here;
        # the nativetool stage, which plays the faithful profile, requires exactly 1.)
        CTRL="$(grep -c '\[OBSERVATION\]' "$dir/game.log" 2>/dev/null)"; CTRL="${CTRL:-0}"
        if [ "$LOOK" -ge 1 ] && [ "$TGT" -ge 1 ] && [ "$AILOG" -eq 0 ] && [ "$PFAIL" -eq 0 ] && [ "$CTRL" -le 1 ]; then
          pass lookahead "preview rounds=$LOOK (targeting previews=$TGT), 0 failed previews, no AI fallbacks, model controllers=$CTRL"
        else
          fail lookahead "preview rounds=$LOOK targeting=$TGT failed_previews=$PFAIL fallbacks=$AILOG model_controllers=$CTRL (need >=1 preview, >=1 targeting preview, 0 failed, 0 fallbacks, at most 1 model controller)"
        fi
      fi
    fi
  fi
fi

# ---------------------------------------------------------------- nativetool
# The standard tool protocol end to end on loopback, with NATIVE_TOOLS deliberately UNSET (so the
# default itself is what is tested): the seat's tools go out as function definitions, the fake
# model answers with real tool calls (a search, an inspect, one malformed call, and a lookahead
# preview on every play decision), the harness serves them through the same executor and budget, and
# the text grammar is never sent. It would fail if the bridge dropped the `tools` field, treated a
# tool-call turn as a dead reply, did not refuse the malformed call, or left the grammar in the prompt.
# (That a refused call costs no budget is asserted by NativeToolsTest, not here.)
if has_stage nativetool; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail nativetool "no compiled classes"; else
    dir="$OUT/nativetool"; sdir="$OUT/nativetool-server"; mkdir -p "$dir" "$sdir"
    : > "$sdir/fake_requests.jsonl"
    python3 "$REPO/eval/fake_model_server.py" --port 0 --policy simulate --native-calls 9 --answer-tool --log "$sdir/fake_requests.jsonl" \
        >"$sdir/fake_server.out" 2>&1 &
    FAKE_PID=$!
    PORT=""; for i in 1 2 3 4 5 6 7 8 9 10; do PORT="$(awk '/^LISTENING/{print $2}' "$sdir/fake_server.out" 2>/dev/null)"; [ -n "$PORT" ] && break; sleep 0.5; done
    if [ -z "$PORT" ]; then fail nativetool "fake model server did not start"; kill $FAKE_PID 2>/dev/null; else
      # Mardu mirror on purpose: both sideboards hold Barrowgoyf, the characteristic-defining card that
      # made the engine copier throw on every preview (PlayerLookahead.UncopiedEffects). The burn
      # mirror the lookahead stage plays has no such card and never saw the defect.
      # 2026-10-13: the two version stamps asserted below moved to seat-tools-v4 / lookahead-preview-v4
      # (`simulate` indexes the decision's own menu and may start a line with a PLAY LAND drop; the
      # option numbers the model reads off the prompt are the numbers it passes; a preview aims at the
      # target the seat names, not the label-vs-name mismatch that ignored it; and PASS is a previewable
      # menu option now, answered without a copy). The game this stage plays is unchanged; only the
      # literals the stamps are compared to moved. 2026-10-14: seat-tools-v5 (a call on a round that
      # offered no tools is refused, not run); the fake model honours tool_choice, so again only the
      # literal moved. 2026-10-15: seat-tools-v6 (report_unclear_decision is offered; the fake model
      # never calls it), only the literal moved. seat-tools-v7 (the decision is a call to `answer`, the
      # default protocol): the fake model answers with it (--answer-tool) after its lookup, as a model
      # on the default protocol does; the answertool stage asserts the answers themselves.
      # 2026-10-15 (later): seat-tools-v9 / evidence-tools-v3 / lookahead-preview-v5 (docs/harness/MODEL-LIMITS.md:
      # 16 calls an ask, any number of previews, no line-length cap); only the literals moved.
      say "nativetool: LLM seat A on loopback fake model, native tools by default (NATIVE_TOOLS unset), MEMORY_TOOLS=1 LOOKAHEAD=1 under v4-faithful (Mardu mirror)"
      play nativetool 42 CONTROLLER_A=llm CONTROLLER_B=floor SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
          OPENROUTER_BASE_URL="http://127.0.0.1:$PORT/v1" MEMORY_TOOLS=1 LOOKAHEAD=1 CONTEXT_PROFILE=v4-faithful GAME_CAP_MS=120000 \
          DECK_A="$REPO/eval/decks/mardu-energy.txt" DECK_B="$REPO/eval/decks/mardu-energy.txt" DECK_A_NAME=MarduA DECK_B_NAME=MarduB
      kill $FAKE_PID 2>/dev/null; wait $FAKE_PID 2>/dev/null
      if ! check_build_loaded "$dir"; then fail nativetool "JVM did not load LlmSpikeGui from $CLASSES"; else
        ( cd "$REPO" && python3 eval/defect_analyzer.py "$dir" --json "$dir/defects.json" ) >"$dir/defects.txt" 2>&1; ARC=$?
        VERDICT="$(python3 - "$dir" "$sdir" "$ARC" <<'EOF'
import glob,json,sys
d,sdir,arc=sys.argv[1],sys.argv[2],int(sys.argv[3]); problems=[]
files=glob.glob(d+"/game-*.jsonl")
rows=[]
for f in files:
    for line in open(f):
        try: rows.append(json.loads(line))
        except Exception: pass
game=next((r for r in rows if r.get("t")=="game"),None)
if not game: problems.append("no game line")
else:
    if game.get("native_tools") is not True: problems.append("native_tools=%r not stamped"%game.get("native_tools"))
    if game.get("native_tools_version")!="seat-tools-v9": problems.append("native_tools_version=%r (expected seat-tools-v9; move this pin WITH NativeTools.VERSION)"%game.get("native_tools_version"))
    if game.get("memory_tools_version")!="evidence-tools-v3": problems.append("memory_tools_version=%r (expected evidence-tools-v3; move this pin WITH ContextMemory.VERSION)"%game.get("memory_tools_version"))
    if game.get("lookahead_version")!="lookahead-preview-v5" or game.get("lookahead") is not True: problems.append("lookahead=%r lookahead_version=%r (expected True, lookahead-preview-v5; move this pin WITH PlayerLookahead.VERSION)"%(game.get("lookahead"),game.get("lookahead_version")))
    if game.get("native_tools_seats")!="A/smart,A/fast": problems.append("native_tools_seats=%r"%game.get("native_tools_seats"))
calls=[r for r in rows if r.get("t")=="context_call"]
tools=[r for r in rows if r.get("t")=="context_tool"]
if not calls: problems.append("no context_call rows")
if any(r.get("protocol")!="native" for r in calls): problems.append("a context_call row is not protocol=native")
if any("MEMORY TOOLS evidence-tools" in (r.get("prompt") or "") or "INSPECT TOOL:" in (r.get("prompt") or "") for r in calls):
    problems.append("the text grammar was sent in a native-tools prompt")
def served(name): return [r for r in tools if r.get("request","").startswith("TOOL CALL "+name+" ") and not r.get("result","").startswith("ERROR")]
for name in ("memory_search","inspect","simulate"):
    if not served(name): problems.append("no served %s call"%name)
refused=[r for r in tools if r.get("result","").startswith("ERROR: tool arguments")]
if not refused: problems.append("the malformed call was not refused")
if any(not r.get("request","").startswith("TOOL CALL ") for r in tools): problems.append("a context_tool row is not a native call")
later=[r for r in calls if r.get("round",0)>0]
if not later or not all(isinstance(r.get("messages"),list) and any(m.get("role")=="tool" for m in r["messages"]) for r in later):
    problems.append("a later-round context_call row lacks the tool exchange")
dec=[r for r in rows if r.get("t")=="decision" and r.get("tier") in ("smart","fast")]
if any((r.get("raw_reply") or "").startswith("TOOL CALL") for r in dec): problems.append("the raw_reply of a decision row is a tool call, not the answer")
if any(r.get("tier")=="ai" for r in rows if r.get("t")=="decision"): problems.append("a decision fell back to the Forge AI")
reqs=[json.loads(l) for l in open(sdir+"/fake_requests.jsonl")]
with_tools=[r for r in reqs if r.get("tools_offered")]
if not reqs or len(with_tools)!=len(reqs): problems.append("%d of %d requests carried no tools"%(len(reqs)-len(with_tools),len(reqs)))
if not any(r.get("tool_results") for r in reqs): problems.append("no request carried a tool result back")
if any(r.get("has_authorization") for r in reqs): problems.append("authorization header sent to loopback")
if not any("simulate" in r.get("tools_offered",[]) for r in reqs): problems.append("simulate was never offered")
import re,collections
banners=collections.Counter(re.findall(r"(?m)^\[([^\]]+)\] \[OBSERVATION\]",open(d+"/game.log",errors="replace").read()))
if not banners or max(banners.values())!=1: problems.append("controllers built per seat: %s (more than one means a preview copy was given the seat controller)"%dict(banners))
previews=[r for r in tools if r.get("request","").startswith("TOOL CALL simulate ")]
broken=[r for r in previews if "preview failed" in r.get("result","")]
if broken: problems.append("%d of %d previews failed (the engine copy threw; see [LOOKAHEAD] in game.log)"%(len(broken),len(previews)))
if not any("if you cast" in r.get("result","") for r in previews): problems.append("no preview returned a result")
if not any("if you pass" in r.get("result","") for r in previews): problems.append("no PASS preview was served (the fake model asks for one every third preview)")
lines=[r for r in previews if "then" in r.get("request","")]
if not any(", then " in r.get("result","") for r in lines): problems.append("no line preview played two options (%d asked)"%len(lines))
# simulate is declared on every request of a lookahead seat, so the tool list (the start of the
# prompt-cache prefix) never changes between requests; a preview outside a play decision is refused.
if len({tuple(r.get("tools_offered",[])) for r in reqs})!=1: problems.append("the tool list changed between requests: %s"%sorted({tuple(r.get("tools_offered",[])) for r in reqs}))
if arc!=0: problems.append("defect_analyzer rc=%d"%arc)
facts="context_calls=%d (later rounds %d) tool_rows=%d served: search=%d inspect=%d simulate=%d refused_malformed=%d fake_requests=%d"%(
    len(calls),len(later),len(tools),len(served("memory_search")),len(served("inspect")),len(served("simulate")),len(refused),len(reqs))
print(("FAIL "+"; ".join(problems)+" | "+facts) if problems else "PASS "+facts)
EOF
)"
        case "$VERDICT" in PASS*) pass nativetool "${VERDICT#PASS }";; *) fail nativetool "${VERDICT#FAIL }";; esac
      fi
    fi
  fi
fi

# ---------------------------------------------------------------- nativerefuse
# A provider with no function calling. It would fail if a refusal were retried forever, if the seat
# lost its tools instead of dropping to the text grammar, if the game line still claimed a clean
# native game, or if the lost decision were handed to the Forge AI.
if has_stage nativerefuse; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail nativerefuse "no compiled classes"; else
    dir="$OUT/nativerefuse"; sdir="$OUT/nativerefuse-server"; mkdir -p "$dir" "$sdir"
    : > "$sdir/fake_requests.jsonl"
    python3 "$REPO/eval/fake_model_server.py" --port 0 --policy pass --refuse-tools --memory-call 8 --log "$sdir/fake_requests.jsonl" \
        >"$sdir/fake_server.out" 2>&1 &
    FAKE_PID=$!
    PORT=""; for i in 1 2 3 4 5 6 7 8 9 10; do PORT="$(awk '/^LISTENING/{print $2}' "$sdir/fake_server.out" 2>/dev/null)"; [ -n "$PORT" ] && break; sleep 0.5; done
    if [ -z "$PORT" ]; then fail nativerefuse "fake model server did not start"; kill $FAKE_PID 2>/dev/null; else
      say "nativerefuse: LLM seat A on a loopback provider that refuses tools (404), MEMORY_TOOLS=1 under v4-faithful"
      play nativerefuse 42 CONTROLLER_A=llm CONTROLLER_B=floor SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
          OPENROUTER_BASE_URL="http://127.0.0.1:$PORT/v1" MEMORY_TOOLS=1 CONTEXT_PROFILE=v4-faithful SMART_BUDGET=40 FAST_BUDGET=40
      kill $FAKE_PID 2>/dev/null; wait $FAKE_PID 2>/dev/null
      if ! check_build_loaded "$dir"; then fail nativerefuse "JVM did not load LlmSpikeGui from $CLASSES"; else
        ( cd "$REPO" && python3 eval/defect_analyzer.py "$dir" --json "$dir/defects.json" ) >"$dir/defects.txt" 2>&1; ARC=$?
        VERDICT="$(python3 - "$dir" "$sdir" "$ARC" <<'EOF'
import glob,json,sys
d,sdir,arc=sys.argv[1],sys.argv[2],int(sys.argv[3]); problems=[]
rows=[]
for f in glob.glob(d+"/game-*.jsonl"):
    for line in open(f):
        try: rows.append(json.loads(line))
        except Exception: pass
game=next((r for r in rows if r.get("t")=="game"),None)
reqs=[json.loads(l) for l in open(sdir+"/fake_requests.jsonl")]
refused=[r for r in reqs if r.get("refused_tools")]
text=[r for r in reqs if not r.get("tools_offered")]
if not game: problems.append("no game line")
else:
    down=sorted(x for x in (game.get("native_tools_downgraded") or "").split(",") if x)
    if not down: problems.append("native_tools_downgraded is empty")
    if len(refused)!=len(down): problems.append("%d refused requests for %d downgraded bridges (a refusal was retried)"%(len(refused),len(down)))
if not refused: problems.append("the provider never refused: tools were not offered")
if not text: problems.append("no text-grammar request followed the refusal")
calls=[r for r in rows if r.get("t")=="context_call"]
native_after=[r for r in calls if r.get("protocol")=="native" and r.get("raw_reply")]
if native_after: problems.append("%d native rounds got a reply from a provider that refuses tools"%len(native_after))
grammar=[r for r in calls if "MEMORY TOOLS evidence-tools" in (r.get("prompt") or "")]
if not grammar: problems.append("the text grammar was never sent after the downgrade")
tools=[r for r in rows if r.get("t")=="context_tool" and not str(r.get("result","")).startswith("ERROR")]
if not tools: problems.append("no tool was served through the text loop after the downgrade")
if any(r.get("tier")=="ai" for r in rows if r.get("t")=="decision"): problems.append("a decision fell back to the Forge AI")
if arc!=0: problems.append("defect_analyzer rc=%d"%arc)
facts="refused=%d downgraded=%s text_requests=%d grammar_prompts=%d tools_served_by_text=%d"%(
    len(refused),(game or {}).get("native_tools_downgraded"),len(text),len(grammar),len(tools))
print(("FAIL "+"; ".join(problems)+" | "+facts) if problems else "PASS "+facts)
EOF
)"
        case "$VERDICT" in PASS*) pass nativerefuse "${VERDICT#PASS }";; *) fail nativerefuse "${VERDICT#FAIL }";; esac
      fi
    fi
  fi
fi

# ---------------------------------------------------------------- answertool
# The decision protocol end to end (AnswerTool, on by default wherever native tools are): every
# decision the fake model is asked goes out with an `answer` function built from its legal values and
# comes back as a call, lookups share the conversation, `set_plan` sets the plan, and one deliberately
# out-of-range answer is corrected in the same conversation. It would fail if a decision were still
# read from text (answer_via text/reask), an answer were lost, the correction re-asked the whole
# decision or took a fallback, the game line did not stamp the protocol, or tool_choice were forced.
if has_stage answertool; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail answertool "no compiled classes"; else
    dir="$OUT/answertool"; sdir="$OUT/answertool-server"; mkdir -p "$dir" "$sdir"
    : > "$sdir/fake_requests.jsonl"
    python3 "$REPO/eval/fake_model_server.py" --port 0 --policy first --answer-tool --answer-invalid 4 --native-calls 3 \
        --log "$sdir/fake_requests.jsonl" >"$sdir/fake_server.out" 2>&1 &
    FAKE_PID=$!
    PORT=""; for i in 1 2 3 4 5 6 7 8 9 10; do PORT="$(awk '/^LISTENING/{print $2}' "$sdir/fake_server.out" 2>/dev/null)"; [ -n "$PORT" ] && break; sleep 0.5; done
    if [ -z "$PORT" ]; then fail answertool "fake model server did not start"; kill $FAKE_PID 2>/dev/null; else
      say "answertool: LLM seat A on loopback fake model answering by calls (policy first), ANSWER_TOOL=1 MEMORY_TOOLS=1 under v4-faithful"
      play answertool 42 CONTROLLER_A=llm CONTROLLER_B=floor SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
          OPENROUTER_BASE_URL="http://127.0.0.1:$PORT/v1" ANSWER_TOOL=1 MEMORY_TOOLS=1 CONTEXT_PROFILE=v4-faithful \
          MULLIGAN_HEURISTIC=0 SMART_BUDGET=150 FAST_BUDGET=150 GAME_CAP_MS=180000
      kill $FAKE_PID 2>/dev/null; wait $FAKE_PID 2>/dev/null
      if ! check_build_loaded "$dir"; then fail answertool "JVM did not load LlmSpikeGui from $CLASSES"; else
        ( cd "$REPO" && python3 eval/defect_analyzer.py "$dir" --json "$dir/defects.json" ) >"$dir/defects.txt" 2>&1; ARC=$?
        VERDICT="$(cd "$REPO/eval" && python3 - "$dir" "$sdir" "$ARC" <<'EOF'
import collections,glob,json,sys
import match_review
d,sdir,arc=sys.argv[1],sys.argv[2],int(sys.argv[3]); problems=[]
files=glob.glob(d+"/game-*.jsonl")
rows=[]
for f in files:
    for line in open(f):
        try: rows.append(json.loads(line))
        except Exception: pass
game=next((r for r in rows if r.get("t")=="game"),None)
if not game: problems.append("no game line")
else:
    if game.get("answer_tool") is not True: problems.append("answer_tool=%r not stamped"%game.get("answer_tool"))
    if game.get("answer_tool_version")!="answer-tool-v8": problems.append("answer_tool_version=%r (move this pin WITH AnswerTool.VERSION)"%game.get("answer_tool_version"))
    if game.get("answer_tool_seats")!="A/smart,A/fast": problems.append("answer_tool_seats=%r"%game.get("answer_tool_seats"))
    if game.get("harness_epoch")!="harness-epoch-16": problems.append("harness_epoch=%r (move this pin WITH HARNESS_EPOCH)"%game.get("harness_epoch"))
dec=[r for r in rows if r.get("t")=="decision"]
model=[r for r in dec if r.get("tier") in ("smart","fast")]
via=collections.Counter(r.get("answer_via") for r in model)
if len(model)<10: problems.append("only %d model decisions"%len(model))
if via.get("tool",0)!=len(model): problems.append("not every model decision was answered by the tool: %s"%dict(via))
lost=[r for r in dec if match_review.LOST.search(str(r.get("fallback") or "")) or r.get("answer_via")=="lost"]
if lost: problems.append("%d LOST decisions, e.g. %s"%(len(lost),lost[0].get("fallback")))
corrected=[r for r in model if r.get("answer_invalid")==1]
if len(corrected)!=1: problems.append("%d decisions corrected an invalid answer (expected exactly 1)"%len(corrected))
types=sorted(set(r.get("type") for r in model))
for need in ("mulligan","play"):
    if need not in types: problems.append("no %s decision answered by the tool"%need)
tools=[r for r in rows if r.get("t")=="context_tool"]
answers=[r for r in tools if r.get("request","").startswith("TOOL CALL answer ")]
bad=[r for r in answers if r.get("result","").startswith("ERROR")]
if len(bad)!=1: problems.append("%d invalid answer calls answered with an error (expected 1)"%len(bad))
if not [r for r in tools if r.get("request","").startswith("TOOL CALL set_plan ") and r.get("result","").startswith("Plan recorded")]:
    problems.append("no set_plan call recorded a plan")
if not [r for r in tools if r.get("request","").startswith("TOOL CALL memory_search ") and not r.get("result","").startswith("ERROR")]:
    problems.append("no lookup was served in an answer conversation")
if any((r.get("raw_reply") or "").startswith("TOOL CALL") for r in model): problems.append("a decision row recorded the call markup, not the answer")
if any(r.get("tier")=="ai" for r in dec): problems.append("a decision fell back to the Forge AI")
reqs=[json.loads(l) for l in open(sdir+"/fake_requests.jsonl")]
offered=[r for r in reqs if "answer" in (r.get("tools_offered") or [])]
if len(offered)!=len(reqs): problems.append("%d of %d requests did not offer the answer tool"%(len(reqs)-len(offered),len(reqs)))
if any(r.get("tool_choice")!="auto" for r in reqs): problems.append("a request did not send tool_choice auto: %s"%sorted(set(str(r.get("tool_choice")) for r in reqs)))
if any(r.get("reply") for r in reqs): problems.append("the fake model answered in text")
if any(r.get("has_authorization") for r in reqs): problems.append("authorization header sent to loopback")
log=open(d+"/game.log",errors="replace").read()
if "[FALLBACK->AI]" in log: problems.append("[FALLBACK->AI] in log")
# Every decision goes through the call (owner, 2026-10-15): no tool ask may offer a text answer.
import re
TEXT_FORMAT=re.compile(r"\b(?:CHOICE|PICKS|BLOCKS|ALLOC|ATTACKS|ATTACKERS|SIMULATE|STEPS|YIELD|PROFILE|LINES|NUMBER|CARD):\s*<"
    r"|`(?:PLAN|STEPS|YIELD|PROFILE|LINES|SIMULATE|CHOICE|PICKS|CARD|NUMBER):|(?:[Ee]nd|[Rr]eply|[Aa]nswer) with (?:EXACTLY|exactly)"
    r"|KEEP or MULLIGAN|instead of an action, you may reply")
asks=[r for r in rows if r.get("t")=="context_call" and not r.get("round")]
leaks=[m.group() for r in asks for m in [TEXT_FORMAT.search(r.get("prompt") or "")] if m]
if leaks: problems.append("%d tool asks offer a text answer, e.g. %r"%(len(leaks),leaks[0]))
if "[TEXT-FORMAT-LEAK]" in log: problems.append("[TEXT-FORMAT-LEAK] in log")
if arc!=0: problems.append("defect_analyzer rc=%d"%arc)
facts="model_decisions=%d via=%s types=%s answer_calls=%d invalid=%d corrected_rows=%d fake_requests=%d asks_checked=%d text_formats=0"%(
    len(model),dict(via),",".join(types),len(answers),len(bad),len(corrected),len(reqs),len(asks))
print(("FAIL "+"; ".join(problems)+" | "+facts) if problems else "PASS "+facts)
EOF
)"
        case "$VERDICT" in PASS*) pass answertool "${VERDICT#PASS }";; *) fail answertool "${VERDICT#FAIL }";; esac
      fi
    fi
  fi
fi

# ---------------------------------------------------------------- faithful
# The same loopback game under CONTEXT_PROFILE=v4-faithful: proves the viewer-aware renderer runs in a
# real game (not only in fixtures), that no prompt repeats the legend (v24: said once in the system prompt), that menu options and
# state share seat-local handles, that no legacy engine-id reference leaks, and that provenance stamps
# the faithful profile, all under the unchanged analyzer gates.
if has_stage faithful; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail faithful "no compiled classes"; else
    dir="$OUT/faithful"; sdir="$OUT/faithful-server"; mkdir -p "$dir" "$sdir"
    : > "$sdir/fake_requests.jsonl"
    python3 "$REPO/eval/fake_model_server.py" --port 0 --policy pass --log "$sdir/fake_requests.jsonl" \
        --memory-call 4 >"$sdir/fake_server.out" 2>&1 &
    FAKE_PID=$!
    PORT=""; for i in 1 2 3 4 5 6 7 8 9 10; do PORT="$(awk '/^LISTENING/{print $2}' "$sdir/fake_server.out" 2>/dev/null)"; [ -n "$PORT" ] && break; sleep 0.5; done
    if [ -z "$PORT" ]; then fail faithful "fake model server did not start"; kill $FAKE_PID 2>/dev/null; else
      say "faithful: LLM seat A on loopback fake model (port $PORT) under CONTEXT_PROFILE=v4-faithful, MEMORY_TOOLS=1"
      play faithful 42 CONTROLLER_A=llm CONTROLLER_B=floor SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
          OPENROUTER_BASE_URL="http://127.0.0.1:$PORT/v1" MEMORY_TOOLS=1 NATIVE_TOOLS=0 CONTEXT_PROFILE=v4-faithful SMART_BUDGET=40 FAST_BUDGET=40
      kill $FAKE_PID 2>/dev/null; wait $FAKE_PID 2>/dev/null
      if ! check_build_loaded "$dir"; then fail faithful "JVM did not load LlmSpikeGui from $CLASSES"; else
        ( cd "$REPO" && python3 eval/defect_analyzer.py "$dir" --json "$dir/defects.json" ) >"$dir/defects.txt" 2>&1; ARC=$?
        VERDICT="$(python3 - "$dir" "$ARC" <<'EOF'
import glob,json,sys,re
d=sys.argv[1]; arc=int(sys.argv[2]); problems=[]; facts=[]
files=glob.glob(d+"/game-*.jsonl")
if len(files)!=1: print("FAIL jsonl files=%d"%len(files)); sys.exit(0)
game=None; calls=[]; tools=[]; ai_rows=0; model_rows=0
for line in open(files[0]):
    try: r=json.loads(line)
    except Exception: continue
    t=r.get("t")
    if t=="game": game=r
    elif t=="decision":
        if r.get("tier")=="ai": ai_rows+=1
        if r.get("tier") in ("smart","fast"): model_rows+=1
    elif t=="context_call": calls.append(r)
    elif t=="context_tool": tools.append(r)
if game is None: problems.append("no game row")
else:
    if game.get("context_profile")!="v4-faithful": problems.append("context_profile=%s"%game.get("context_profile"))
    if game.get("memory_tools") is not True: problems.append("memory_tools not stamped")
    if game.get("harness_epoch")!="harness-epoch-16": problems.append("harness_epoch=%s"%game.get("harness_epoch"))
    facts.append("winner=%s turns=%s context_sha=%s"%(game.get("winner"),game.get("turns"),game.get("context_sha")))
if ai_rows: problems.append("%d tier=ai rows"%ai_rows)
if model_rows<5: problems.append("only %d model-tier rows"%model_rows)
if len(calls)<5: problems.append("only %d context_call rows"%len(calls))
legend=0; handles=0; legacy=0; menu_handles=0; observed=0
mull=0; rules_leak=0
for r in calls:
    p=r.get("prompt") or ""
    # The mulligan ask shows only the opening hand (no board exists yet); it carries no observation.
    if "KEEP" in p and "MULLIGAN" in p and "DECISION:" not in p: mull+=1; observed+=1; continue
    if "| priority: " in p: observed+=1   # the faithful header (v24 no longer prints its version)
    if "LEGEND: @N" in p: legend+=1   # v24: the legend is said once, in the system prompt, never per message
    handles+=len(re.findall(r" @\d+", p))
    legacy+=len(re.findall(r"[A-Za-z\]]@\d+", p))          # the legacy candidateLabel form: Name[...]@id
    menu_handles+=len(re.findall(r"\n\s+\d+\) (?:CAST|PLAY LAND) [^\n]*@\d+", p))
    # Default-off provenance: without RULES_TOOL=1 the rules grammar must never appear, so this
    # stage keeps prompts byte-identical to a run that has no rules tool at all.
    if "RULES TOOL" in p: rules_leak+=1
if observed<len(calls): problems.append("%d/%d prompts lack the faithful observation header"%(len(calls)-observed,len(calls)))
# docs/STANDARDS.md section 8: no option a faithful ask offers is cut short.
cut=sum(len(re.findall(r"(?m)^\s+\d+\) [^\n]*\.\.\.\]?$", r.get("prompt") or "")) for r in calls)
if cut: problems.append("%d menu options cut short (ending in ...) in faithful prompts"%cut)
if legend: problems.append("%d/%d prompts repeat the legend (v24 says it once, in the system prompt)"%(legend,len(calls)))
if handles<20: problems.append("only %d seat handles across all prompts"%handles)
if legacy: problems.append("%d legacy engine-id references (Name@id) leaked into faithful prompts"%legacy)
if menu_handles<3: problems.append("menu options carrying handles: %d (<3)"%menu_handles)
if rules_leak: problems.append("%d faithful prompts advertised RULES TOOL without RULES_TOOL=1 (default-off provenance broken)"%rules_leak)
ok_tools=[x for x in tools if not str(x.get("result","")).startswith("ERROR")]
if len(ok_tools)<2: problems.append("successful tool rows %d (<2)"%len(ok_tools))
log=open(d+"/game.log",errors="replace").read()
if "[OBSERVATION] observation-faithful-v26" not in log: problems.append("observer never announced itself in the log")
if "state unavailable" in log or "state unavailable" in "".join((r.get("prompt") or "") for r in calls): problems.append("renderer threw (state unavailable)")
if "[FALLBACK->AI]" in log: problems.append("[FALLBACK->AI] present")
if arc!=0: problems.append("defect_analyzer rc=%d"%arc)
facts.append("model_rows=%d context_calls=%d mulligan_asks=%d handles=%d menu_handles=%d tool_rows=%d/%d"%(model_rows,len(calls),mull,handles,menu_handles,len(ok_tools),len(tools)))
print(("FAIL " if problems else "PASS ")+"; ".join(problems+facts))
EOF
)"
        case "$VERDICT" in PASS*) pass faithful "${VERDICT#PASS }";; *) fail faithful "${VERDICT#FAIL }";; esac
      fi
    fi
  fi
fi

# ---------------------------------------------------------------- rules (RULES_TOOL)
# A loopback game with RULES_TOOL=1 + MEMORY_TOOLS=1: the real RulesTool lookup runs end to end
# (the fake model answers one tool prompt with `RULES SEARCH`), the game stamps rules provenance,
# every prompt carries the RULES grammar while it is on, and no single result exceeds the page cap.
if has_stage rules; then
  if [ ! -f "$CLASSES/forge/spike/LlmSpikeGui.class" ]; then fail rules "no compiled classes"; else
    dir="$OUT/rules"; sdir="$OUT/rules-server"; mkdir -p "$dir" "$sdir"
    : > "$sdir/fake_requests.jsonl"
    python3 "$REPO/eval/fake_model_server.py" --port 0 --policy pass --log "$sdir/fake_requests.jsonl" \
        --rules-call 1 >"$sdir/fake_server.out" 2>&1 &
    FAKE_PID=$!
    PORT=""; for i in 1 2 3 4 5 6 7 8 9 10; do PORT="$(awk '/^LISTENING/{print $2}' "$sdir/fake_server.out" 2>/dev/null)"; [ -n "$PORT" ] && break; sleep 0.5; done
    if [ -z "$PORT" ]; then fail rules "fake model server did not start"; kill $FAKE_PID 2>/dev/null; else
      say "rules: LLM seat A on loopback fake model, RULES_TOOL=1 + MEMORY_TOOLS=1 (comprehensive-rules lookup)"
      play rules 42 CONTROLLER_A=llm CONTROLLER_B=floor SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
          OPENROUTER_BASE_URL="http://127.0.0.1:$PORT/v1" MEMORY_TOOLS=1 NATIVE_TOOLS=0 RULES_TOOL=1 CONTEXT_PROFILE=v4-faithful \
          RULES_FILE="$REPO/data/rules/MagicCompRules.txt" SMART_BUDGET=60 FAST_BUDGET=60
      kill $FAKE_PID 2>/dev/null; wait $FAKE_PID 2>/dev/null
      if ! check_build_loaded "$dir"; then fail rules "JVM did not load LlmSpikeGui from $CLASSES"; else
        ( cd "$REPO" && python3 eval/defect_analyzer.py "$dir" --json "$dir/defects.json" ) >"$dir/defects.txt" 2>&1; ARC=$?
        VERDICT="$(python3 - "$dir" "$ARC" <<'EOF'
import glob,json,sys,re
d=sys.argv[1]; arc=int(sys.argv[2]); problems=[]; facts=[]
files=glob.glob(d+"/game-*.jsonl")
if len(files)!=1: print("FAIL jsonl files=%d"%len(files)); sys.exit(0)
game=None; calls=[]; tools=[]; ai_rows=0
for line in open(files[0]):
    try: r=json.loads(line)
    except Exception: continue
    t=r.get("t")
    if t=="game": game=r
    elif t=="decision":
        if r.get("tier")=="ai": ai_rows+=1
    elif t=="context_call": calls.append(r)
    elif t=="context_tool": tools.append(r)
if game is None: problems.append("no game row")
else:
    if game.get("rules_tool") is not True: problems.append("rules_tool not stamped true (%s)"%game.get("rules_tool"))
    if game.get("rules_tool_version")!="rules-tool-v1": problems.append("rules_tool_version=%s"%game.get("rules_tool_version"))
    if game.get("rules_effective")!="September 25, 2026": problems.append("rules_effective=%s"%game.get("rules_effective"))
    facts.append("winner=%s turns=%s rules_effective=%s"%(game.get("winner"),game.get("turns"),game.get("rules_effective")))
if ai_rows: problems.append("%d tier=ai rows"%ai_rows)
rtools=[x for x in tools if str(x.get("request","")).startswith("RULES")]
ok=[x for x in rtools if not str(x.get("result","")).startswith("ERROR")]
if not ok: problems.append("no successful RULES context_tool row (%d RULES of %d tool rows)"%(len(rtools),len(tools)))
else:
    res=str(ok[0].get("result",""))
    if "509" not in res: problems.append("RULES result lacks a rule number: %s"%res[:90])
    # Context bound: a rules reply must never dump a wall of text (page cap 1200 + header).
    big=[x for x in ok if len(str(x.get("result","")))>1500]
    if big: problems.append("%d RULES results exceed the page cap (<1500 chars)"%len(big))
    facts.append("rules_tool_rows=%d/%d first_result=%d_chars"%(len(ok),len(rtools),len(res)))
offered=sum(1 for r in calls if "RULES TOOL rules-tool-v1" in (r.get("prompt") or ""))
if calls and offered<len(calls): problems.append("%d/%d prompts lack the RULES grammar line"%(len(calls)-offered,len(calls)))
log=open(d+"/game.log",errors="replace").read()
if "[RULES] rules-tool-v1 wired" not in log: problems.append("the tool never announced itself in the log")
if "[FALLBACK->AI]" in log: problems.append("[FALLBACK->AI] present")
if arc!=0: problems.append("defect_analyzer rc=%d"%arc)
facts.append("context_calls=%d rules_prompts=%d/%d"%(len(calls),offered,len(calls)))
print(("FAIL " if problems else "PASS ")+"; ".join(problems+facts))
EOF
)"
        case "$VERDICT" in PASS*) pass rules "${VERDICT#PASS }";; *) fail rules "${VERDICT#FAIL }";; esac
      fi
    fi
  fi
fi

# ---------------------------------------------------------------- summary
echo "----"
cat "$SUMMARY"
echo "evidence: $OUT"
if [ "$FAILED" = 0 ]; then echo "HARNESS ACCEPTANCE: PASS"; exit 0; else echo "HARNESS ACCEPTANCE: FAIL"; exit 1; fi
