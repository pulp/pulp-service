#!/bin/bash
# Runs agent-entrypoint against a fake buzz-acp and checks the stop path: the
# pod's SIGTERM reaches buzz-acp only once its turn has gone quiet (or at the
# hold cap), and the replay floor is the moment it was passed on. Needs bash,
# git, jq, python3 with PyYAML and coreutils timeout, as the agent image has:
#   podman run --rm -v "$PWD/.buzz/images/agent-base:/t:ro" alpine:3.20 sh -c \
#     'apk add -q bash git jq python3 py3-yaml coreutils && bash /t/agent-entrypoint_stop_test.sh'
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
mkdir -p "$W/bin" "$W/sec"
cp "$HERE/agent-entrypoint" "$HERE/definition-env" "$HERE/agent-stop-hold" "$W/bin/"
chmod +x "$W/bin/"*
export PATH="$W/bin:$PATH" HOME="$W/home"; mkdir -p "$HOME"
git config --global init.defaultBranch main
git config --global user.email t@example.com; git config --global user.name t
echo fake-nsec > "$W/sec/nsec"

# A definition repo with one agent, t.
R="$W/repo"; mkdir -p "$R/.buzz/agents/t"
printf 'display_name: T\nparallelism: 1\nrelay: wss://relay.invalid\n' > "$R/.buzz/agents/t/agent.yaml"
echo "# t" > "$R/.buzz/agents/t/charter.md"
git -C "$R" init -q && git -C "$R" add -A && git -C "$R" commit -qm def

# The fake buzz-acp logs a turn the way buzz-acp does ("turn starting for" /
# "turn complete for"). The turn writes its transcript once a second for TURN
# seconds (forever when TURN is 0), then stays silent SILENT more seconds, as a
# long tool call does, before it ends. It records when SIGTERM arrives; its
# sleeps run as "sleep & wait" because bash runs a trap only once a foreground
# command finishes, and wait is interrupted at once.
cat > "$W/bin/fake-acp" <<'EOF'
#!/bin/bash
trap 'date +%s > "$OUT/term-at"; exit 0' TERM
mkdir -p "$CLAUDE_CONFIG_DIR/projects/-w"
echo "INFO pool::prompt: turn starting for channel t"
i=0
while [ "$TURN" = 0 ] || [ $i -lt "$TURN" ]; do echo x >> "$CLAUDE_CONFIG_DIR/projects/-w/s.jsonl"; sleep 1 & wait $!; i=$((i+1)); done
sleep "${SILENT:-0}" & wait $!
echo "INFO pool::prompt: turn complete for channel t: end_turn"
while true; do sleep 1 & wait $!; done
EOF
chmod +x "$W/bin/fake-acp"
# git, slowed down for fetches while $W/slow-fetch exists, like a hung remote.
REALGIT=$(command -v git)
cat > "$W/bin/git" <<EOF
#!/bin/bash
case " \$* " in *" fetch "*) [ -e "$W/slow-fetch" ] && sleep 20;; esac
exec $REALGIT "\$@"
EOF
chmod +x "$W/bin/git"

pass=0 fail=0
check() { if eval "$2"; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $1"; fi; }

# run TURN HOLD: start the entrypoint, SIGTERM it 3s in, report when the fake
# got SIGTERM (seconds after the pod's SIGTERM) and the entrypoint's exit code.
run() {
  local S="$W/state-$1-$2"; export OUT="$W/out-$1-$2"; mkdir -p "$S" "$OUT"
  AGENT_SLUG=t AGENT_DEFINITION_REPO="file://$R" AGENT_DEFINITION_REF=main AGENT_REVISION=test \
  AGENT_STATE_DIR="$S" AGENT_SECRET_DIR="$W/sec" BUZZ_ACP_BIN=fake-acp TURN="$1" \
  SILENT="${SILENT:-0}" AGENT_STOP_IDLE_SECONDS=2 AGENT_STOP_HOLD_SECONDS="$2" agent-entrypoint > "$OUT/log" 2>&1 &
  local pid=$!
  sleep 3; local sent; sent=$(date +%s); kill -TERM $pid
  wait $pid; RC=$?
  GOT=$(( $(cat "$OUT/term-at" 2>/dev/null || echo 0) - sent ))
  FLOOR=$(( $(cat "$S/restart/stopping-at" 2>/dev/null || echo 0) - sent ))
}

run 8 60      # a turn still writing for about 5s after the pod's SIGTERM
check "SIGTERM waits for the turn to go quiet (got it after ${GOT}s)" '[ $GOT -ge 6 ] && [ $GOT -le 10 ]'
check "replay floor is when SIGTERM was passed on (${FLOOR}s)" '[ $FLOOR -ge 6 ] && [ $FLOOR -le $GOT ]'
check "entrypoint exits 0 (exit $RC)" '[ $RC = 0 ]'

run 1 60      # already idle when the pod's SIGTERM arrives
check "an idle agent gets SIGTERM at once (after ${GOT}s)" '[ $GOT -ge 0 ] && [ $GOT -le 3 ]'

run 0 5       # a turn that never ends: the hold stops at its cap
check "the hold stops at the cap (got it after ${GOT}s)" '[ $GOT -ge 5 ] && [ $GOT -le 8 ]'

# A definition change while a turn runs: buzz-acp is restarted in the same pod,
# and gets SIGTERM only once the turn has gone quiet.
S="$W/state-reload"; export OUT="$W/out-reload"; mkdir -p "$S" "$OUT"
AGENT_SLUG=t AGENT_DEFINITION_REPO="file://$R" AGENT_DEFINITION_REF=main AGENT_REVISION=test \
AGENT_STATE_DIR="$S" AGENT_SECRET_DIR="$W/sec" BUZZ_ACP_BIN=fake-acp TURN=15 \
AGENT_DEFINITION_WATCH_SECONDS=5 AGENT_STOP_IDLE_SECONDS=2 AGENT_STOP_HOLD_SECONDS=60 agent-entrypoint > "$OUT/log" 2>&1 &
pid=$!
sleep 1; echo "# t, changed" > "$R/.buzz/agents/t/charter.md"; git -C "$R" commit -qam change; changed=$(date +%s)
for i in $(seq 1 40); do [ -s "$OUT/term-at" ] && break; sleep 1; done
GOT=$(( $(cat "$OUT/term-at" 2>/dev/null || echo 0) - changed ))
check "a reload waits for the turn to go quiet (got SIGTERM ${GOT}s after the change)" '[ $GOT -ge 15 ] && [ $GOT -le 25 ]'
kill -TERM $pid; wait $pid 2>/dev/null

# A long tool call: the transcript goes quiet mid turn, but the turn has not
# ended in buzz-acp's log, so SIGTERM waits for its end line.
SILENT=15 run 1 60
check "a quiet transcript mid turn still holds (got SIGTERM after ${GOT}s)" '[ $GOT -ge 12 ] && [ $GOT -le 20 ]'

# The pod's SIGTERM lands during a slow definition fetch: the hold counts from
# the signal, so buzz-acp still gets SIGTERM within the hold of it.
touch "$W/slow-fetch"
S="$W/state-slow"; export OUT="$W/out-slow"; mkdir -p "$S" "$OUT"
AGENT_SLUG=t AGENT_DEFINITION_REPO="file://$R" AGENT_DEFINITION_REF=main AGENT_REVISION=test \
AGENT_STATE_DIR="$S" AGENT_SECRET_DIR="$W/sec" BUZZ_ACP_BIN=fake-acp TURN=0 \
AGENT_DEFINITION_WATCH_SECONDS=5 AGENT_STOP_IDLE_SECONDS=2 AGENT_STOP_HOLD_SECONDS=10 agent-entrypoint > "$OUT/log" 2>&1 &
pid=$!
sleep 8; sent=$(date +%s); kill -TERM $pid; wait $pid; rm -f "$W/slow-fetch"
GOT=$(( $(cat "$OUT/term-at" 2>/dev/null || echo 0) - sent ))
check "the hold counts from the signal, not from after a slow fetch (got SIGTERM after ${GOT}s)" '[ $GOT -ge 0 ] && [ $GOT -le 21 ]'

echo "agent-entrypoint stop tests: $pass passed, $fail failed"; [ $fail -eq 0 ]
