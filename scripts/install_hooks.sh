#!/bin/bash
# Install the commit-msg hook that checks the subject and body format (docs/STANDARDS.md section 2).
set -e
# --git-path honours worktrees (where .git is a file) and core.hooksPath.
HOOK="$(git rev-parse --git-path hooks)/commit-msg"
mkdir -p "$(dirname "$HOOK")"
if [ -e "$HOOK" ] && ! grep -q "scripts/pr_format.py" "$HOOK"; then
  echo "refusing to overwrite an existing commit-msg hook: $HOOK" >&2; exit 1
fi
cat > "$HOOK" <<'HOOKEOF'
#!/bin/bash
exec python3 "$(git rev-parse --show-toplevel)/scripts/pr_format.py" check-commit "$1"
HOOKEOF
chmod +x "$HOOK"
echo "installed $HOOK"
