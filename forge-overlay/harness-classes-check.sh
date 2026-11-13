# Sourced by the game launchers (bash 3.2 and up): refuse a game whose harness classes are older than the sources
# installed for them. install.sh writes the sources, and only a build (mvn install) turns them into the classes a game
# loads: the seat (forge/spike) from forge-gui-desktop/target/classes, each engine patch ("HARNESS PATCH") from the
# module jar the Maven classpath names (~/.m2), which the launcher's classpath puts first for those modules. A source
# newer than what will load means the game would play another harness than the one its overlay_sha names.
# Usage: harness_classes_check <forge home> <classpath> <separator>; returns 0, or prints why and returns 4.
# What it cannot see: a module jar another checkout's `mvn install` replaced (newer, so it passes), and on Windows a
# module jar named by a C:\ path (not matched, so that module is not checked).

harness_classes_check() {
  _home="$1"; _cp="$2"; _sep="$3"; _stale=""
  for _src in "$_home"/forge-gui-desktop/src/main/java/forge/spike/*.java; do
    [ -f "$_src" ] || continue
    _cls="$_home/forge-gui-desktop/target/classes/forge/spike/$(basename "$_src" .java).class"
    if [ ! -f "$_cls" ] || [ -n "$(find "$_src" -newer "$_cls" 2>/dev/null)" ]; then
      _stale="$_stale ${_src#$_home/}"
    fi
  done
  for _rel in $(cd "$_home" && grep -rlF "HARNESS PATCH" forge-ai/src/main/java forge-game/src/main/java \
                  forge-core/src/main/java forge-gui/src/main/java 2>/dev/null); do
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
    [ -n "$_art" ] || continue          # a classpath this check cannot read (see above): not judged
    if [ ! -f "$_art" ] || [ -n "$(find "$_home/$_rel" -newer "$_art" 2>/dev/null)" ]; then
      _stale="$_stale $_rel"
    fi
  done
  [ -z "$_stale" ] && return 0
  echo "[launcher] REFUSING: these installed harness sources are newer than the classes the game would load:" >&2
  for _s in $_stale; do echo "    $_s" >&2; done
  echo "[launcher] build them first, with no game running:" >&2
  echo "    cd $_home && mvn -q -pl forge-gui-desktop -am install -o -DskipTests -DskipLaunch4j" >&2
  return 4
}
