#!/usr/bin/env bash
set -uo pipefail

# Lint only lines changed compared to a base branch.
# Existing violations are ignored; only new violations in added/modified lines are reported.
#
# Usage:
#   scripts/ruff-diff-check.sh              # compare against upstream/main
#   scripts/ruff-diff-check.sh main         # compare against main
#   scripts/ruff-diff-check.sh HEAD~1       # compare against previous commit

BASE_REF="${1:-upstream/main}"
REPO_ROOT=$(git rev-parse --show-toplevel)

cd "$REPO_ROOT"

changed_files=$(git diff --name-only --diff-filter=ACM "$BASE_REF" -- '*.py' \
    | grep '^pulp_service/' \
    | grep -v '/migrations/' || true)

if [ -z "$changed_files" ]; then
    echo "No Python files changed (excluding migrations). All clean."
    exit 0
fi

changed_lines_file=$(mktemp)
ruff_json_file=$(mktemp)
trap 'rm -f "$changed_lines_file" "$ruff_json_file"' EXIT

git diff --unified=0 "$BASE_REF" -- $changed_files | awk '
    /^--- /    { next }
    /^\+\+\+ / { file = substr($2, 3); next }
    /^@@ /     {
        # $3 is the new-file hunk like +47 or +47,3
        hunk = $3
        sub(/^\+/, "", hunk)
        split(hunk, range, ",")
        start = range[1] + 0
        count = (length(range) > 1) ? range[2] + 0 : 1
        if (count == 0) next
        for (line_nr = start; line_nr < start + count; line_nr++) {
            print file ":" line_nr
        }
    }
' > "$changed_lines_file"

if [ ! -s "$changed_lines_file" ]; then
    echo "No added/modified lines found. All clean."
    exit 0
fi

ruff check --config pulp_service/pyproject.toml --output-format json $changed_files > "$ruff_json_file" 2>/dev/null || true

if [ ! -s "$ruff_json_file" ]; then
    echo "No ruff violations in changed files. All clean."
    exit 0
fi

python3 - "$REPO_ROOT" "$changed_lines_file" "$ruff_json_file" <<'PYEOF'
import json, os, sys

repo_root = sys.argv[1]
changed_lines_path = sys.argv[2]
ruff_json_path = sys.argv[3]

lookup = set()
with open(changed_lines_path) as fh:
    for raw_line in fh:
        lookup.add(raw_line.strip())

with open(ruff_json_path) as fh:
    violations = json.load(fh)

new_found = []
for violation in violations:
    filename = violation["filename"]
    rel_path = os.path.relpath(filename, repo_root)
    row = violation["location"]["row"]
    key = f"{rel_path}:{row}"
    if key in lookup:
        new_found.append((rel_path, violation))

if not new_found:
    lines_checked = len(lookup)
    print(f"No NEW ruff violations in changed lines. All clean.")
    print(f"({lines_checked} lines checked)")
    sys.exit(0)

print()
for rel_path, violation in new_found:
    row = violation["location"]["row"]
    col = violation["location"]["column"]
    code = violation["code"]
    msg = violation["message"]
    print(f"{rel_path}:{row}:{col}: {code} {msg}")

print()
print(f"Found {len(new_found)} new violation(s) in changed lines.")
print("Fix these before committing. Run 'ruff check --fix pulp_service/' to auto-fix safe violations.")
sys.exit(1)
PYEOF
