#!/bin/sh
# make check-secrets: fail if a secret is in the tree or .env is tracked by git. Run before every push.
#
# Checks (excluding .git, .venv, node_modules, data/, and .env itself):
#   1. Anything shaped like an Anthropic key: sk-ant- followed by 20+ key characters.
#   2. The literal value of SMTP_PASSWORD from .env (if .env exists and the value is set).
#   3. The literal value of every other secret-looking .env entry (*PASSWORD*, *SECRET*, *KEY*, *TOKEN*)
#      that is at least 8 characters long.
#   4. .env (or any .env.* other than .env.example) tracked by git.
# Usage: sh scripts/check_secrets.sh [tree_root]   (default: repo root)
set -eu
ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"
fail=0
EXCLUDES="--exclude-dir=.git --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=data --exclude-dir=__pycache__ --exclude=.env"

if grep -rIlE $EXCLUDES 'sk-ant-[A-Za-z0-9_-]{20,}' . 2>/dev/null; then
  echo "FAIL: Anthropic API key (sk-ant-...) found in the files above"; fail=1
fi

if [ -f .env ]; then
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    key="${line%%=*}"; value="${line#*=}"
    value="$(printf '%s' "$value" | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
    case "$key" in
      SMTP_PASSWORD) min=1 ;;
      *PASSWORD*|*SECRET*|*KEY*|*TOKEN*) min=8 ;;
      *) continue ;;
    esac
    [ "${#value}" -ge "$min" ] || continue
    if grep -rIlF $EXCLUDES -- "$value" . 2>/dev/null; then
      echo "FAIL: the value of $key from .env appears in the files above"; fail=1
    fi
  done < .env
fi

if [ -d .git ] && command -v git >/dev/null 2>&1; then
  tracked="$(git ls-files | grep -E '(^|/)\.env(\.|$)' | grep -v '\.env\.example$' || true)"
  if [ -n "$tracked" ]; then
    echo "FAIL: env file(s) tracked by git: $tracked"; fail=1
  fi
fi

[ "$fail" -eq 0 ] && echo "check-secrets: ok"
exit "$fail"
