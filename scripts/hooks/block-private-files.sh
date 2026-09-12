#!/usr/bin/env bash
# Refuse to commit files that must never reach the public/portfolio history.
#
# .gitignore already excludes these, but `git add -f` silently defeats it and a
# rename can move a file out of an ignore pattern. This hook is the backstop.
#
# Invoked by pre-commit with the staged filenames as arguments.

set -uo pipefail

# Paths that are always permitted, even if they match a pattern below.
# Matched against the basename.
ALLOWED_BASENAMES=(
  '.gitkeep'    # empty placeholders that preserve the directory skeleton
  '.env.example'  # committed template, contains no real values
)

# Each entry is an extended regex matched against the repository-relative path.
PATTERNS=(
  '(^|/)\.env(\..+)?$'
  '(^|/)\.scratch/'
  '(^|/)sandbox/'
  '(^|/)archive/'
  '(^|/)data/'
  '(^|/)outputs/'
  '[Aa][Kk][Rr]'
  '(^|/)share_check_report.*\.txt$'
  '(^|/)PROMPTS_IMPROVED.*\.md$'
  '(^|/)PROMPTS_ISSUE_TRACKER\.md$'
  '(^|/)tasks_and_learning_queue\.md$'
  '\.zip$'
  '(^|/)\.DS_Store$'
)

violations=()

for file in "$@"; do
  basename=$(basename "$file")
  allowed=0
  for allow in "${ALLOWED_BASENAMES[@]}"; do
    if [ "$basename" = "$allow" ]; then
      allowed=1
      break
    fi
  done
  [ "$allowed" -eq 1 ] && continue

  for pattern in "${PATTERNS[@]}"; do
    if printf '%s' "$file" | grep -Eq "$pattern"; then
      violations+=("$file  (matched /$pattern/)")
      break
    fi
  done
done

if [ ${#violations[@]} -gt 0 ]; then
  echo "ERROR: private or portfolio-excluded files are staged:" >&2
  printf '  %s\n' "${violations[@]}" >&2
  cat >&2 <<'EOF'

These paths are excluded from this repository on purpose (see .gitignore).

To unstage them:
  git restore --staged <file>

If a file is genuinely safe to publish, rename it so it no longer matches, or
add an explicit allowance to scripts/hooks/block-private-files.sh.
EOF
  exit 1
fi

exit 0
