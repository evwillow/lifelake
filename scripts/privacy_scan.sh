#!/usr/bin/env bash
# Pre-publish scan of a fork. Usage: scripts/privacy_scan.sh TERMS_FILE
#
# TERMS_FILE holds one term per line (names, addresses, paths, anything that must not ship). It has to live outside
# the repo. Matching is literal and case-insensitive for ASCII letters.
#
# Output is counts and locations only. Matched text never reaches a terminal or a file: every search of content or
# metadata prints counts, or pipes straight into a filter that keeps the location and drops the rest of the line. A
# location whose path itself contains a term is hidden and counted instead.
#
# Scanned: the working tree (without .git, virtualenvs, demo_data and generated files), tracked file names, file names
# in history, the content of every commit reachable from a ref, author and committer identities, commit messages, and
# gitleaks over history and over the files git would track.
# Not scanned: binary files, ref names, tag messages and unreachable objects.
#
# LC_ALL=C makes grep treat only NUL bytes as binary, so a file in a legacy encoding is still read. The cost is that
# case folding covers ASCII only: list both spellings of a term with non-ASCII letters that can change case.
#
# Exit status: 0 clean, 1 at least one hit, 2 the scan could not run or did not finish.
set -u
export LC_ALL=C
umask 077

usage() {
  echo "usage: $0 TERMS_FILE" >&2
  exit 2
}
die() {
  echo "privacy_scan: $1" >&2
  exit 2
}

[ "$#" -eq 1 ] || usage
terms_arg=$1
[ -f "$terms_arg" ] && [ -r "$terms_arg" ] || die "terms file is not a readable file: $terms_arg"

script_dir=$(cd "$(dirname "$0")" && pwd -P) || die "cannot locate the script"
repo=$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null) || die "not inside a git repository"
repo=$(cd "$repo" && pwd -P) || die "cannot enter the repository"

inside_repo() {
  case "$1" in
    "$repo" | "$repo"/*) return 0 ;;
    *) return 1 ;;
  esac
}

# Check the path as given and its target, so neither a symlink in the repo nor a symlink to the repo gets through.
terms_dir=$(cd "$(dirname "$terms_arg")" && pwd -P) || die "cannot resolve the terms file"
terms_real=$(readlink -f "$terms_arg") || die "cannot resolve the terms file"
if inside_repo "$terms_dir/$(basename "$terms_arg")" || inside_repo "$terms_real"; then
  die "refusing to run: the terms file is inside the repo. Move it outside."
fi

tmp=$(mktemp -d) || die "cannot create a temp folder"
trap 'rm -rf "$tmp"' EXIT
trap 'exit 2' HUP INT TERM
inside_repo "$(cd "$tmp" && pwd -P)" && die "refusing to run: the temp folder is inside the repo. Check TMPDIR."

# A blank line in a grep pattern file matches every line, so blanks must go.
terms="$tmp/terms"
tr -d '\r' <"$terms_real" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d' >"$terms"
[ -s "$terms" ] || die "the terms file has no terms"
n_terms=$(wc -l <"$terms")

cd "$repo" || die "cannot enter the repository"

hits=0   # sections with at least one hit
errors=0 # sections that could not finish

# stdin: grep --null lines, which look like PATH NUL LINE ":" TEXT. stdout: PATH:LINE, and no TEXT. A line without the
# separator is never echoed, because the whole of it could be text (a grep that ignored --null, or a newline in a path).
strip_text() {
  tr '\000' '\001' | awk -F '\001' '
    NF < 2 { print "(location could not be parsed)"; next }
    { n = $2; sub(/:.*/, "", n); p = $1; sub(/^\.\//, "", p); print p ":" n }'
}

# Print the locations in a file, hiding any whose path contains a term.
show_locations() {
  grep -v -i -F -f "$terms" -- "$1"
  local hidden
  hidden=$(grep -c -i -F -f "$terms" -- "$1")
  [ "$hidden" -eq 0 ] || echo "  ($hidden more: location hidden because its path contains a term)"
}

report() { # report SECTION COUNT
  echo "$1: $2"
  [ "$2" -eq 0 ] || hits=$((hits + 1))
}

echo "privacy_scan: $n_terms terms"

# --- working tree -----------------------------------------------------------------------------------------------------
grep -rniI -F -f "$terms" --null \
  --exclude-dir=.git --exclude-dir=.venv --exclude-dir=venv --exclude-dir=demo_data --exclude-dir=__pycache__ \
  --exclude-dir=.pytest_cache --exclude-dir=.ruff_cache --exclude-dir='*.egg-info' --exclude-dir=build \
  --exclude-dir=dist \
  --exclude='*.duckdb' --exclude='*.duckdb.wal' --exclude='*.parquet' --exclude=SCHEMA.md --exclude='*.pyc' \
  . 2>/dev/null | strip_text | grep -v '^eval/results/' >"$tmp/tree"
rc=${PIPESTATUS[0]}
report "working tree, matching lines" "$(wc -l <"$tmp/tree")"
show_locations "$tmp/tree"
if [ "$rc" -gt 1 ]; then
  echo "working tree: grep could not read some files (exit $rc)"
  errors=$((errors + 1))
fi

# --- file names -------------------------------------------------------------------------------------------------------
# Counts and list positions only, since printing a matching name would print the term.
git -c core.quotePath=false ls-files | grep -n -i -F -f "$terms" | cut -d: -f1 >"$tmp/names"
report "tracked file names" "$(wc -l <"$tmp/names")"
[ -s "$tmp/names" ] && sed 's/^/  entry in git ls-files: /' "$tmp/names"

report "file names in history" \
  "$(git -c core.quotePath=false log --all --name-only --format= | sort -u | grep -c -i -F -f "$terms")"

# --- every commit -----------------------------------------------------------------------------------------------------
git rev-list --all >"$tmp/commits" || {
  echo "history: git rev-list failed"
  errors=$((errors + 1))
}
: >"$tmp/content"
: >"$tmp/meta"
while IFS= read -r c; do
  # -c prints COMMIT:PATH:COUNT and no matched text.
  git grep -c -I -i -F -f "$terms" "$c" -- >>"$tmp/content" 2>/dev/null
  rc=$?
  if [ "$rc" -gt 1 ]; then
    echo "history: git grep failed on commit $c (exit $rc)"
    errors=$((errors + 1))
  fi
  for field in author:%an%n%ae committer:%cn%n%ce message:%B; do
    n=$(git log -1 --format="${field#*:}" "$c" | grep -c -i -F -f "$terms")
    [ "$n" -eq 0 ] || echo "$c ${field%%:*}: $n" >>"$tmp/meta"
  done
done <"$tmp/commits"
echo "commits scanned: $(wc -l <"$tmp/commits")"

report "commit content, files with hits per commit" "$(wc -l <"$tmp/content")"
show_locations "$tmp/content"

report "author, committer and message hits" "$(wc -l <"$tmp/meta")"
[ -s "$tmp/meta" ] && sed 's/^/  commit /' "$tmp/meta"

# --- gitleaks ---------------------------------------------------------------------------------------------------------
# gitleaks output is discarded and its report is only counted. `gitleaks dir` runs on a copy of the files git would
# track, because demo_data and the virtualenv hold fake secrets and third-party test keys. --exit-code 0 means a
# non-zero status can only be a tool failure.
gitleaks_count() { # gitleaks_count LABEL MODE PATH
  local report_file="$tmp/gitleaks-$2.json"
  if ! gitleaks "$2" --redact --no-banner --exit-code 0 --log-level error \
    --report-format json --report-path "$report_file" "$3" >/dev/null 2>&1; then
    echo "$1: gitleaks failed"
    errors=$((errors + 1))
    return
  fi
  if [ ! -f "$report_file" ]; then
    echo "$1: gitleaks wrote no report"
    errors=$((errors + 1))
    return
  fi
  report "$1" "$(grep -c '^ *"RuleID":' "$report_file")"
}

if command -v gitleaks >/dev/null 2>&1; then
  gitleaks_count "gitleaks git, findings" git .
  copy="$tmp/copy"
  mkdir -p "$copy"
  while IFS= read -r -d '' f; do
    if [ -f "$f" ] && [ ! -L "$f" ]; then
      mkdir -p "$copy/$(dirname "$f")" && cp -p "$f" "$copy/$f"
    fi
  done < <(git ls-files -z -c -o --exclude-standard)
  gitleaks_count "gitleaks dir, findings" dir "$copy"
else
  echo "gitleaks: not installed, so this part of the scan did not run"
  errors=$((errors + 1))
fi

# --- result -----------------------------------------------------------------------------------------------------------
if [ "$hits" -gt 0 ]; then
  echo "RESULT: FAIL, sections with hits: $hits"
  exit 1
fi
if [ "$errors" -gt 0 ]; then
  echo "RESULT: INCOMPLETE, sections that could not finish: $errors"
  exit 2
fi
echo "RESULT: clean"
exit 0
