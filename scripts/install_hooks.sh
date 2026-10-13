#!/bin/bash
# Install the commit-msg hook that checks the subject and body format (docs/STANDARDS.md section 2).
set -e
ROOT="$(git rev-parse --show-toplevel)"
HOOK="$ROOT/.git/hooks/commit-msg"
cat > "$HOOK" <<'HOOKEOF'
#!/bin/bash
exec python3 "$(git rev-parse --show-toplevel)/scripts/pr_format.py" check-commit "$1"
HOOKEOF
chmod +x "$HOOK"
echo "installed $HOOK"
