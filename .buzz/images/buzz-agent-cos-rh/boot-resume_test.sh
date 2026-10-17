#!/bin/bash
# Checks that a boot-resume run is visible to the stop hold: boot-resume writes
# "turn starting for boot-resume" to the turn log before its task and
# "turn complete for boot-resume" after it, even when the task fails, so
# agent-stop-hold holds a pod stop until the resumed work is done.
#   bash .buzz/images/buzz-agent-cos-rh/boot-resume_test.sh
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
BASE="$HERE/../agent-base"
W=$(mktemp -d); trap 'pkill -f "$W/bin/buzz-acp" 2>/dev/null; rm -rf "$W"' EXIT
mkdir -p "$W/bin" "$W/share" "$W/state/claude" "$W/state/work"
cp "$HERE/bin/boot-resume" "$BASE/agent-stop-hold" "$W/bin/"
echo "resume since BOOT_TIME" > "$W/share/boot-resume.md"
# agent-runtime-env stub: the real one reads the cloned definition.
printf '_agent_runtime_env() { return 0; }\n' > "$W/bin/agent-runtime-env"
# Fake buzz-acp: with no args it is the long running daemon pgrep looks for;
# "run --task" is the boot-resume task, which runs RUN_SECS then exits RUN_RC.
cat > "$W/bin/buzz-acp" <<'F'
#!/bin/bash
[ "${1:-}" = run ] || { while true; do sleep 1; done; }
sleep "${RUN_SECS:-0}"; exit "${RUN_RC:-0}"
F
chmod +x "$W/bin/"*
export PATH="$W/bin:$PATH" AGENT_BIN="$W/bin" AGENT_SHARE="$W/share" BOOT_RESUME_DELAY=0
export AGENT_STATE_DIR="$W/state" CLAUDE_CONFIG_DIR="$W/state/claude" AGENT_WORKDIR="$W/state/work" AGENT_PUBKEY=abc
TL="$W/state/turns.log"
buzz-acp & daemon=$!

pass=0 fail=0
check() { if eval "$2"; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $1"; fi; }

# A task that runs 8s: during it the hold must not call the agent idle.
: > "$TL"; RUN_SECS=8 boot-resume > "$W/out1" 2>&1 & br=$!
sleep 2
check "start line written before the task ends" 'grep -q "turn starting for boot-resume" "$TL"'
agent-stop-hold "$CLAUDE_CONFIG_DIR" 1 3 "$TL" >/dev/null 2>&1; rc=$?
check "the hold sees the resumed turn (exit $rc, want 3 at cap)" '[ $rc -eq 3 ]'
wait $br
check "complete line written after the task" 'grep -q "turn complete for boot-resume" "$TL"'
agent-stop-hold "$CLAUDE_CONFIG_DIR" 1 5 "$TL" >/dev/null 2>&1; rc=$?
check "after the task the hold sees the agent idle (exit $rc)" '[ $rc -eq 0 ]'

# A failing task still closes its turn, or every later stop would wait for the cap.
: > "$TL"; RUN_RC=1 boot-resume > "$W/out2" 2>&1
check "a failed task still writes its complete line" 'grep -q "turn complete for boot-resume" "$TL"'

kill $daemon 2>/dev/null
echo "boot-resume tests: $pass passed, $fail failed"; [ $fail -eq 0 ]
