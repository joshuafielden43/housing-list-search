#!/usr/bin/env bash
#
# secrets pre-commit guard — refuse to commit secret-shaped files or private keys.
#
# Catches the "a profile spawned its own .env and it got staged" class of mistake
# at the moment of staging, even if .gitignore misses the pattern or someone used
# `git add -f`. Path-based (the proven near-miss vector) plus content sweeps for
# private keys and common leaked-token shapes. Templates (.example/.sample/
# .template/...) are allowed through on purpose.
#
# Deliberate override (you know it's safe):  git commit --no-verify
#
# Lives in .husky/ (versioned, survives clones) rather than .git/hooks/, and is
# chained from .husky/pre-commit — that also means it only runs if Husky's
# core.hooksPath is intact. Reusable template: claude-config/git-hardening/
#
fail=0

staged=$(git diff --cached --name-only --diff-filter=AM)
[ -z "$staged" ] && exit 0

is_template() {
  case "$1" in
    *.example|*.sample|*.template|*.dist|*.defaults|*.default|*.tpl) return 0 ;;
    *) return 1 ;;
  esac
}

# common leaked-token shapes, checked against staged content of every file below
token_pattern='AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-(ant-)?[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'

while IFS= read -r f; do
  [ -z "$f" ] && continue
  base=$(basename "$f")
  is_template "$base" && continue

  hit=""
  case "$base" in
    .env|.env.*|*.env)                          hit="env file" ;;
    auth.json|*.secret|*secrets.json|*secrets.yaml|*secrets.yml) hit="secret store" ;;
    id_rsa|id_dsa|id_ecdsa|id_ed25519|*.pem|*.key|*.p12|*.pfx|*.keystore|*.jks) hit="private key / cert" ;;
    .netrc|.htpasswd|.pgpass|credentials|credentials.*) hit="credentials file" ;;
  esac

  if [ -n "$hit" ]; then
    printf '  \342\234\213 %s  (%s)\n' "$f" "$hit" >&2
    fail=1
    continue
  fi

  content=$(git show ":$f" 2>/dev/null)

  if printf '%s' "$content" | grep -qE -- '-----BEGIN [A-Z ]*PRIVATE KEY-----'; then
    printf '  \342\234\213 %s  (contains a PRIVATE KEY block)\n' "$f" >&2
    fail=1
    continue
  fi

  if printf '%s' "$content" | grep -qE -- "$token_pattern"; then
    printf '  \342\234\213 %s  (contains what looks like a live API token)\n' "$f" >&2
    fail=1
  fi
done <<EOF
$staged
EOF

if [ "$fail" -ne 0 ]; then
  {
    echo ""
    echo "  COMMIT BLOCKED — staged changes look like they contain secrets."
    echo "  Nothing was committed. Pick one:"
    echo "    • add the path to .gitignore (preferred), then re-commit"
    echo "    • move the secret to /Volumes/Secrets or another ignored path"
    echo "    • if it is genuinely safe (a template, or a fixture using a fake"
    echo "      key), give the file a .example/.sample suffix — or override"
    echo "      once: git commit --no-verify"
    echo "  Guard: .husky/pre-commit-secrets.sh  (claude-config/git-hardening)"
    echo ""
  } >&2
  exit 1
fi
exit 0