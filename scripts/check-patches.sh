#!/usr/bin/env bash
#
# Guard the local-patch inventory in the image build.
#
# The Dockerfile applies each patch explicitly with `patch -p1 --fuzz=0`.
# This script catches the two failure modes the build cannot:
#   1. a .patch file exists but is not wired into the Dockerfile (silently skipped);
#   2. a Dockerfile patch invocation omits --fuzz=0 (fuzzy apply can land in the
#      wrong context and still exit 0).
#
# Usage:
#   scripts/check-patches.sh
#   DOCKERFILE=/path/to/Dockerfile PATCH_DIR=/path/to/patches scripts/check-patches.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERFILE="${DOCKERFILE:-$ROOT/Dockerfile}"
PATCH_DIR="${PATCH_DIR:-$ROOT/images/assets/patches}"

if [[ ! -f "$DOCKERFILE" ]]; then
  echo "error: Dockerfile not found: $DOCKERFILE" >&2
  exit 2
fi
if [[ ! -d "$PATCH_DIR" ]]; then
  echo "error: patch directory not found: $PATCH_DIR" >&2
  exit 2
fi

mapfile -t referenced < <(
  grep -E '^[[:space:]]*COPY[[:space:]]+images/assets/patches/' "$DOCKERFILE" \
    | grep -oE '[^/[:space:]]+\.patch'
)
mapfile -t applied < <(
  grep -E '^[[:space:]]*RUN[[:space:]]+patch' "$DOCKERFILE" \
    | grep -oE '/tmp/[^[:space:]]+\.patch' \
    | sed 's#^/tmp/##'
)
mapfile -t on_disk < <(
  find "$PATCH_DIR" -maxdepth 1 -name '*.patch' -printf '%f\n' | sort
)

if (( ${#referenced[@]} == 0 )); then
  echo "error: no patch COPY lines found in $DOCKERFILE" >&2
  exit 1
fi

fail=0

duplicates="$(printf '%s\n' "${referenced[@]}" | sort | uniq -d)"
if [[ -n "$duplicates" ]]; then
  echo "error: patch referenced more than once in the Dockerfile:" >&2
  printf '  %s\n' $duplicates >&2
  fail=1
fi

missing_from_dockerfile="$(
  comm -23 <(printf '%s\n' "${on_disk[@]}" | sort -u) \
           <(printf '%s\n' "${referenced[@]}" | sort -u)
)"
if [[ -n "$missing_from_dockerfile" ]]; then
  echo "error: patch file(s) on disk but not referenced in the Dockerfile:" >&2
  printf '  %s\n' $missing_from_dockerfile >&2
  fail=1
fi

missing_on_disk="$(
  comm -13 <(printf '%s\n' "${on_disk[@]}" | sort -u) \
           <(printf '%s\n' "${referenced[@]}" | sort -u)
)"
if [[ -n "$missing_on_disk" ]]; then
  echo "error: patch file(s) referenced in the Dockerfile but missing on disk:" >&2
  printf '  %s\n' $missing_on_disk >&2
  fail=1
fi

if [[ "$(printf '%s\n' "${applied[@]}" | sort)" != "$(printf '%s\n' "${referenced[@]}" | sort)" ]]; then
  echo "error: COPY and RUN patch lists differ; a patch is wired but not applied, or vice versa:" >&2
  echo "  COPY: ${referenced[*]}" >&2
  echo "  RUN:  ${applied[*]}" >&2
  fail=1
fi

if grep -E '^[[:space:]]*RUN[[:space:]]+patch' "$DOCKERFILE" | grep -v -- '--fuzz=0' >/dev/null; then
  echo "error: every RUN patch invocation must include --fuzz=0:" >&2
  grep -nE '^[[:space:]]*RUN[[:space:]]+patch' "$DOCKERFILE" | grep -v -- '--fuzz=0' >&2
  fail=1
fi

if ! printf '%s\n' "${referenced[@]}" | sort -c 2>/dev/null; then
  echo "warning: Dockerfile patch order is not lexicographic; confirm it is intentional." >&2
fi

if (( fail )); then
  exit 1
fi

echo "patch inventory OK: ${#referenced[@]} referenced, ${#on_disk[@]} on disk; --fuzz=0 enforced."
