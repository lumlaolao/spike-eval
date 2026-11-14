# Sourced by the game launchers (bash 3.2 and up): refuse a game whose harness classes are older than the sources
# installed for them. install.sh writes the sources, and only a build (mvn install) turns them into the classes a game
# loads: the seat (forge/spike) from forge-gui-desktop/target/classes, each engine patch ("HARNESS PATCH") from the
# module jar the Maven classpath names (~/.m2), which the launcher's classpath puts first for those modules. A source
# newer than what will load means the game would play another harness than the one its overlay_sha names.
# A module jar in ~/.m2 must also be this checkout's own build: the same bytes as <module>/target/<module>-*.jar
# (cmp). Their times are not compared: mvn install writes the copy's time to the millisecond, so the checkout's jar
# is always a fraction of a second "newer" than its own copy.
# Usage: harness_classes_check <forge home> <classpath> <separator>; returns 0, or prints why and returns 4.
# What it cannot see:
#   - an engine patch removed from engine-patches/ whose patched file stays in the checkout (it is still built);
#   - a seat class whose source install.sh removed (the stale .class stays in target/classes);
#   - a module named by an entry it cannot read (a Windows C:\ path): it prints "[launcher] not checked: <module>".
HARNESS_PATCH_MODULES="forge-ai forge-game forge-core forge-gui"

harness_classes_check() {
  _home="$1"; _cp="$2"; _sep="$3"; _stale=""
  for _src in "$_home"/forge-gui-desktop/src/main/java/forge/spike/*.java; do
    [ -f "$_src" ] || continue
    case "$_src" in */package-info.java) continue ;; esac
    _cls="$_home/forge-gui-desktop/target/classes/forge/spike/$(basename "$_src" .java).class"
    if [ ! -f "$_cls" ] || [ -n "$(find "$_src" -newer "$_cls" 2>/dev/null)" ]; then
      _stale="$_stale
    ${_src#$_home/}"
    fi
  done
  _dirs=""; for _m in $HARNESS_PATCH_MODULES; do _dirs="$_dirs $_m/src/main/java"; done
  for _rel in $(cd "$_home" && grep -rlF "HARNESS PATCH" $_dirs 2>/dev/null); do
    _mod="${_rel%%/*}"; _art=""
    _old="$IFS"; IFS="$_sep"
    for _e in $_cp; do
      case "$_e" in
        */"$_mod"/target/classes|*/"$_mod"/target/classes/)
          _art="$_e/${_rel#*/src/main/java/}"; _art="${_art%.java}.class"; break ;;
        */"$_mod"/*/"$_mod"-*.jar) _art="$_e"; break ;;
      esac
    done
    IFS="$_old"
    if [ -z "$_art" ]; then
      echo "[launcher] not checked: $_mod" >&2
      continue
    fi
    if [ ! -f "$_art" ] || [ -n "$(find "$_home/$_rel" -newer "$_art" 2>/dev/null)" ]; then
      _stale="$_stale
    $_rel"
      continue
    fi
    case "$_art" in
      *.jar)
        _own=""
        for _j in "$_home/$_mod"/target/"$_mod"-*.jar; do
          case "$_j" in *-sources.jar|*-javadoc.jar|*-tests.jar) continue ;; esac
          [ -f "$_j" ] && { _own="$_j"; break; }
        done
        if [ -z "$_own" ] || ! cmp -s "$_own" "$_art"; then
          _stale="$_stale
    $_rel (the ~/.m2 jar of $_mod is not this checkout's own build)"
        fi ;;
    esac
  done
  [ -z "$_stale" ] && return 0
  echo "[launcher] REFUSING: these installed harness sources are newer than the classes the game would load:" >&2
  echo "${_stale#?}" >&2
  echo "[launcher] build them first, with no game running:" >&2
  echo "    cd $_home && mvn -q -pl forge-gui-desktop -am install -o -DskipTests -DskipLaunch4j" >&2
  return 4
}
