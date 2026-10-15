#!/bin/bash
# Starts the server on a fixture tree and checks listing, rendering, redaction and input checks.
set -u
D=$(mktemp -d); trap 'kill $P 2>/dev/null; rm -rf $D' EXIT
S=11111111-2222-3333-4444-555555555555
mkdir -p "$D/personal/claude/projects/-state-work"
cat > "$D/personal/claude/projects/-state-work/$S.jsonl" <<'J'
{"message":{"role":"user","content":"@Bob hello"}}
{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"curl -H 'X-Api-Key: abcdef0123456789' x"}}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"token=sk-ant-oat01-secretvalue"}]}}
{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"printf '{\"password\": \"hunter2hunter2\"}'; curl -H 'Authorization: Bearer abcdefghijklmnopqrstuv' x"}}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"[nextcloud]\ntype = webdav\npass = ObscuredRcloneValue123\n"}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"literal-octoprint-key-ABC123"}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":[{"type":"text","text":"{\"key\":\"literal-octoprint-key-ABC123\"}"}]}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"{\"AccessKey\": {\"SecretAccessKey\": \"wJalrXUtnFEMIexampleKEYexampleKEY1234\"}} tskey-auth-kABCDEFexample123 GOCSPX-exampleclientsecret12 ya29.a0AfH6SMexampletoken BW_SESSION=QmFzZTY0U2Vzc2lvbktleVZhbHVlMTIzNDU2"}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"refresh 1//0exampleRefreshTokenValue1234567 and ssh line MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEBAoIBAQ="}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"[{\"name\": \"agents/x\", \"fields\": [\n  {\"name\": \"nsec\",\n   \"value\": \"0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0\", \"type\": 1},\n  {\"name\": \"nsec\", \"value\": \"MGYxZTJkM2M0YjVhNjk3ODg3OTZhNWI0YzNkMmUxZjAwZjFlMmQzYzRiNWE2OTc4ODc5NmE1YjRjM2QyZTFmMA==\"}]}] BUZZ_PRIVATE_KEY=abcdefabcdef0123456789 event 7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a"}]}}
{"message":{"role":"assistant","content":[{"type":"text","text":"done"}]}}
J
mkdir -p "$D/secrets/octoprint" "$D/secrets/nextcloud" "$D/secrets/google" "$D/secrets/ssh"; printf '{"client_secret":"GOCSPX-fromjsonfile1234","nested":{"refresh_token":"1//0exampleRefreshTokenValue1234567"}}' > "$D/secrets/google/client.json"; printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUxOQ\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEBAoIBAQ=\n-----END OPENSSH PRIVATE KEY-----\n' > "$D/secrets/ssh/id_ed25519"; printf 'literal-octoprint-key-ABC123' > "$D/secrets/octoprint/api-key"; printf '[nextcloud]\ntype = webdav\npass = ObscuredRcloneValue123\n' > "$D/secrets/nextcloud/rclone.conf"
PORT=18090 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets python3 "$(dirname "$0")/transcripts_server.py" & P=$!
for i in $(seq 1 30); do curl -s localhost:18090/healthz >/dev/null && break; sleep 0.2; done
pass=0 fail=0
check() { if eval "$2"; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $1"; fi; }
L=$(curl -s "localhost:18090/sessions?hours=1")
check "lists the session with its community" '[[ "$L" == *"$S  personal"* ]]'
O=$(curl -s "localhost:18090/show?hours=1")
check "renders messages and tools" '[[ "$O" == *"[IN] @Bob hello"* && "$O" == *"[TOOL Bash]"* && "$O" == *"[AGENT SAYS] done"* ]]'
check "redacts an API key header" '[[ "$O" != *abcdef0123456789* ]]'
check "redacts an Anthropic token" '[[ "$O" != *secretvalue* ]]'
check "redacts a JSON escaped password inside a tool input" '[[ "$O" != *hunter2* ]]'
check "redacts a bearer token" '[[ "$O" != *abcdefghijklmnopqrstuv* ]]'
check "redacts an rclone pass line" '[[ "$O" != *ObscuredRcloneValue123* ]]'
check "redacts the mounted credential value wherever it appears" '[[ "$O" != *literal-octoprint-key-ABC123* ]]'
check "keeps ordinary text" '[[ "$O" == *"[IN] @Bob hello"* && "$O" == *"[AGENT SAYS] done"* ]]'
check "a dumped credential file is redacted whole" '[[ "$O" == *"[RESULT] [REDACTED]"* ]]'
check "redacts an AWS secret access key in JSON output" '[[ "$O" != *wJalrXUtnFEMIexample* ]]'
check "redacts a Tailscale key" '[[ "$O" != *tskey-auth-kABCDEF* ]]'
check "redacts a Google client secret and access token" '[[ "$O" != *GOCSPX-exampleclientsecret12* && "$O" != *ya29.a0AfH6SMexampletoken* ]]'
check "redacts a bw session value" '[[ "$O" != *QmFzZTY0U2Vzc2lvbktleVZhbHVl* ]]'
check "redacts string values from a mounted JSON credential file" '[[ "$O" != *0exampleRefreshTokenValue1234567* ]]'
check "redacts a key body line from a mounted key whose last line ends in =" '[[ "$O" != *MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEBAoIBAQ* ]]'
check "redacts a hex nsec field value" '[[ "$O" != *0f1e2d3c4b5a6978* ]]'
check "redacts a base64 nsec field value" '[[ "$O" != *MGYxZTJkM2M0YjVh* ]]'
check "redacts a private key assignment" '[[ "$O" != *abcdefabcdef0123456789* ]]'
check "keeps a hex event id away from the word nsec" '[[ "$O" == *7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a* ]]'
check "one session by id" '[[ "$(curl -s "localhost:18090/session?id=$S")" == *"[AGENT SAYS] done"* ]]'
check "rejects a non uuid id" '[ "$(curl -s -o /dev/null -w %{http_code} "localhost:18090/session?id=../../etc")" = 400 ]'
check "old sessions drop out of the window" '[ -z "$(touch -d "-3 hours" "$D/personal/claude/projects/-state-work/$S.jsonl" 2>/dev/null || touch -A -030000 "$D/personal/claude/projects/-state-work/$S.jsonl"; curl -s "localhost:18090/sessions?hours=1")" ]'
# Bearer-token check: a second server with TRANSCRIPTS_TOKEN_FILE set requires the token.
TOK=$D/secrets/transcripts-token; printf 's3cret-transcripts-token' > "$TOK"
PORT=18091 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets TRANSCRIPTS_TOKEN_FILE=$TOK python3 "$(dirname "$0")/transcripts_server.py" & P2=$!
for i in $(seq 1 30); do curl -s localhost:18091/healthz >/dev/null && break; sleep 0.2; done
code() { curl -s -o /dev/null -w %{http_code} "$@"; }
check "healthz needs no token even when the check is on" '[ "$(code localhost:18091/healthz)" = 200 ]'
check "a request without a token is 401" '[ "$(code "localhost:18091/sessions?hours=1")" = 401 ]'
check "a request with a wrong token is 401" '[ "$(code -H "Authorization: Bearer wrong-token" "localhost:18091/sessions?hours=1")" = 401 ]'
check "a non-Bearer Authorization header is 401" '[ "$(code -H "Authorization: token s3cret-transcripts-token" "localhost:18091/sessions?hours=1")" = 401 ]'
check "the right token is accepted" '[ "$(code -H "Authorization: Bearer s3cret-transcripts-token" "localhost:18091/sessions?hours=1")" = 200 ]'
kill $P2 2>/dev/null

# Fail closed: REQUIRE_TOKEN=true with no token file means 503 for everything but /healthz.
PORT=18092 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets TRANSCRIPTS_TOKEN_FILE=$D/no-such-token TRANSCRIPTS_REQUIRE_TOKEN=true python3 "$(dirname "$0")/transcripts_server.py" & P3=$!
for i in $(seq 1 30); do curl -s localhost:18092/healthz >/dev/null && break; sleep 0.2; done
check "fail closed: healthz still answers when the token file is missing" '[ "$(code localhost:18092/healthz)" = 200 ]'
check "fail closed: a request is 503 when the required token file is missing" '[ "$(code "localhost:18092/sessions?hours=1")" = 503 ]'
kill $P3 2>/dev/null

echo "transcripts server tests: $pass passed, $fail failed"; [ $fail -eq 0 ]
