# Sourced by the root scripts (setup.sh, install.sh, play-vs-llm.sh): the three things that differ between macOS,
# Linux and Windows (Git Bash, MSYS2 or Cygwin). bash 3.2. The game launchers are copied into the Forge checkout
# and cannot source this; they carry their one difference (the classpath separator) themselves.

case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) ON_WINDOWS=1 ;; *) ON_WINDOWS=0 ;; esac

# python3 under that name everywhere. python.org's Windows installer names it python (and the py launcher), and
# Windows's own python3 is a stub that prints how to install it and fails, so the name being on PATH proves
# nothing: it is run. The function is exported so the scripts these scripts start (install.sh) have it too.
if ! python3 -c "" >/dev/null 2>&1; then
  if python -c "import sys; sys.exit(sys.version_info[0] != 3)" >/dev/null 2>&1; then
    python3() { python "$@"; }
    export -f python3
  elif py -3 -c "" >/dev/null 2>&1; then
    python3() { py -3 "$@"; }
    export -f python3
  fi
fi
have_python3() { python3 -c "" >/dev/null 2>&1; }

# A path as a native program must be given it. Maven's JVM on Windows does not know bash's /tmp or /c/Users: it
# gets C:/..., which bash reads back under its own name for the same file. Unchanged everywhere else.
native_path() {
  if [ "$ON_WINDOWS" = 1 ] && command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}
