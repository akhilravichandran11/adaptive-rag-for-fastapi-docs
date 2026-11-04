#!/usr/bin/env bash
# Refuse to commit staged content that contains a word from a local, untracked
# list. The list lives in the git common dir (`info/reserved-words`), which is
# never cloned, committed or pushed, so neither the words nor the exemptions
# leave this machine. When the file is absent the hook passes silently.
#
# List format, one entry per line (blank lines and `#` comments ignored):
#   <word>              a word to block, matched case-insensitively
#   allow <path>        a repo-relative path exempt from the check
#
# Reads each file's content from the git index (`git show :path`), not the
# working tree, so the check reflects exactly what will be committed.
# Invoked by pre-commit with the staged filenames as arguments.

set -uo pipefail

list="$(git rev-parse --git-common-dir)/info/reserved-words"
[ -r "$list" ] || exit 0

words=()
allowed=()
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [ -z "$line" ] && continue
  case "$line" in
    allow\ *) allowed+=("${line#allow }") ;;
    *) words+=("$line") ;;
  esac
done < "$list"
[ ${#words[@]} -gt 0 ] || exit 0

is_allowed() {
  local path="$1" a
  for a in "${allowed[@]+"${allowed[@]}"}"; do
    [ "$path" = "$a" ] && return 0
  done
  return 1
}

violations=()
for file in "$@"; do
  is_allowed "$file" && continue
  # Skip paths with no staged blob (for example a file staged for deletion).
  content=$(git show ":$file" 2>/dev/null) || continue
  for word in "${words[@]}"; do
    # -I: treat binary files as non-matching rather than scanning garbage.
    if printf '%s' "$content" | grep -Iiq -- "$word"; then
      violations+=("$file  (contains \"$word\")")
      break
    fi
  done
done

if [ ${#violations[@]} -gt 0 ]; then
  echo "ERROR: staged files contain a reserved word:" >&2
  printf '  %s\n' "${violations[@]}" >&2
  cat >&2 <<MSG

To unstage them:
  git restore --staged <file>

If a match is a false positive, add \`allow <path>\` to $list.
MSG
  exit 1
fi

exit 0
