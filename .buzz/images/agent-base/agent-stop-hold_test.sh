#!/bin/bash
# Checks agent-stop-hold: it returns once no session transcript has been written
# for the idle window, or at the cap, whichever comes first.
set -u
HOLD="$(dirname "$0")/agent-stop-hold"
D=$(mktemp -d); trap 'kill $W 2>/dev/null; rm -rf "$D"' EXIT
W=
pass=0 fail=0
check() { if eval "$2"; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $1"; fi; }
now() { python3 -c 'import time; print(time.time())'; }
since() { python3 -c "import sys,time; print(int(time.time()-float(sys.argv[1])))" "$1"; }

mkdir -p "$D/projects/-state-work"
F="$D/projects/-state-work/s.jsonl"

# No transcripts at all: idle at once. ("At once" allows 3s: interpreter start up
# alone takes over a second on some machines.)
E="$D/empty"; mkdir -p "$E"
t=$(now); python3 "$HOLD" "$E" 2 30 >/dev/null; rc=$?; s=$(since "$t")
check "no transcripts returns at once" '[ "$s" -le 3 ] && [ "$rc" = 0 ]'

# Transcripts older than the idle window: idle at once.
touch "$F"; python3 -c "import os,sys,time; t=time.time()-60; os.utime(sys.argv[1],(t,t))" "$F"
t=$(now); python3 "$HOLD" "$D" 2 30 >/dev/null; rc=$?; s=$(since "$t")
check "stale transcripts return at once" '[ "$s" -le 3 ] && [ "$rc" = 0 ]'

# A turn writing for 5s: returns about 2s after the last write, exit 0.
( for i in 1 2 3 4 5; do echo x >> "$F"; sleep 1; done ) & W=$!
t=$(now); python3 "$HOLD" "$D" 2 30 >/dev/null; rc=$?; s=$(since "$t")
check "waits for a running turn to go quiet (took ${s}s)" '[ "$s" -ge 6 ] && [ "$s" -le 10 ] && [ "$rc" = 0 ]'
wait $W 2>/dev/null

# A turn that never goes quiet: returns at the cap with exit 3.
( while true; do echo x >> "$F"; sleep 1; done ) & W=$!
t=$(now); python3 "$HOLD" "$D" 2 4 >/dev/null; rc=$?; s=$(since "$t")
check "stops at the cap (took ${s}s, exit $rc)" '[ "$s" -ge 4 ] && [ "$s" -le 6 ] && [ "$rc" = 3 ]'
kill $W 2>/dev/null; wait $W 2>/dev/null; W=

echo "agent-stop-hold tests: $pass passed, $fail failed"; [ $fail -eq 0 ]
