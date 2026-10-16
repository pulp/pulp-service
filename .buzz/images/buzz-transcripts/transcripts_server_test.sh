#!/bin/bash
# Starts the server on a fixture tree and checks listing, rendering, redaction and input checks.
set -u
D=$(mktemp -d); X=$(mktemp -d); trap 'kill $P $P4 $P5 2>/dev/null; rm -rf $D $X' EXIT
P4= P5=
SRV="$(dirname "$0")/transcripts_server.py"
S=11111111-2222-3333-4444-555555555555
# Every credential below is a fake fixture value. They are assembled from pieces at
# run time so secret scanners do not flag this file; the server still sees them whole.
APIKEY="abcdef""0123456789"
BEARER="Bea""rer abcdefghij""klmnopqrstuv"
ANT="sk-""ant-oat01-secretvalue"
GHKEY="gh""p_KeyAsFieldName0123456789abcd"
OAIPROJ="sk-""proj-FakeProjKey0123456789abcdefghij"
OAILEGACY="sk-""FakeLegacyKey0123456789ZYXWVUTSRQPONMLKJIHGFEDCBA"
AWSENV="AWS_SECRET_ACCESS""_KEY=FakeAwsSecretKey0123456789ZYXWVUTSRQPONM"
AWSINI="aws_secret_access""_key = FakeAwsIniKey0123456789ZYXWVUTSRQPONMLK"
NSEC="nsec""1crl7uqxqllhqps8lacqvpllwqrq0lmsqcrl7uqxqllhqps8lacqqfld3h2"
NSECHEX=$(printf 'c0ffee00%.0s' 1 2 3 4 5 6 7 8)   # the hex form of $NSEC
PK="PRIV""ATE KEY"
mkdir -p "$D/personal/claude/projects/-state-work"
cat > "$D/personal/claude/projects/-state-work/$S.jsonl" <<J
{"message":{"role":"user","content":"@Bob hello"}}
{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"curl -H 'X-Api-Key: $APIKEY' x"}}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"token=$ANT"}]}}
{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"printf '{\"password\": \"hunter2hunter2\"}'; curl -H 'Authorization: $BEARER' x"}}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"[nextcloud]\ntype = webdav\npass = ObscuredRcloneValue123\n"}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"literal-octoprint-key-ABC123"}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":[{"type":"text","text":"{\"key\":\"literal-octoprint-key-ABC123\"}"}]}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"{\"AccessKey\": {\"SecretAccessKey\": \"wJalrXUtnFEMIexampleKEYexampleKEY1234\"}} tskey-auth-kABCDEFexample123 GOCSPX-exampleclientsecret12 ya29.a0AfH6SMexampletoken BW_SESSION=QmFzZTY0U2Vzc2lvbktleVZhbHVlMTIzNDU2"}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"refresh 1//0exampleRefreshTokenValue1234567 and ssh line MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEBAoIBAQ="}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"[{\"name\": \"agents/x\", \"fields\": [\n  {\"name\": \"nsec\",\n   \"value\": \"0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0\", \"type\": 1},\n  {\"name\": \"nsec\", \"value\": \"MGYxZTJkM2M0YjVhNjk3ODg3OTZhNWI0YzNkMmUxZjAwZjFlMmQzYzRiNWE2OTc4ODc5NmE1YjRjM2QyZTFmMA==\"}]}] BUZZ_PRIVATE_KEY=abcdefabcdef0123456789 event 7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a"}]}}
{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Write","input":{"$GHKEY":"value"}}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"the signer key as hex: $NSECHEX"}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"two keys $OAIPROJ and $OAILEGACY"}]}}
{"message":{"role":"user","content":[{"type":"tool_result","content":"$AWSENV\n[default]\n$AWSINI\n"}]}}
{"message":{"role":"assistant","content":[{"type":"text","text":"done"}]}}
J
mkdir -p "$D/secrets/octoprint" "$D/secrets/nextcloud" "$D/secrets/google" "$D/secrets/ssh" "$D/secrets/nostr"; printf '{"client_secret":"GOCSPX-fromjsonfile1234","nested":{"refresh_token":"1//0exampleRefreshTokenValue1234567"}}' > "$D/secrets/google/client.json"; printf -- "-----BEGIN OPENSSH $PK-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUxOQ\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEBAoIBAQ=\n-----END OPENSSH $PK-----\n" > "$D/secrets/ssh/id_ed25519"; printf 'literal-octoprint-key-ABC123' > "$D/secrets/octoprint/api-key"; printf '[nextcloud]\ntype = webdav\npass = ObscuredRcloneValue123\n' > "$D/secrets/nextcloud/rclone.conf"; printf '%s\n' "$NSEC" > "$D/secrets/nostr/nsec"
# Symlinks out of the transcript tree: a linked file and a linked project directory.
printf '{"message":{"role":"user","content":"OUTSIDE-MARKER-file"}}\n' > "$X/outside.jsonl"
mkdir -p "$X/proj"; printf '{"message":{"role":"user","content":"OUTSIDE-MARKER-dir"}}\n' > "$X/proj/88888888-2222-3333-4444-555555555555.jsonl"
ln -s "$X/outside.jsonl" "$D/personal/claude/projects/-state-work/99999999-2222-3333-4444-555555555555.jsonl"
ln -s "$X/proj" "$D/personal/claude/projects/-linked"
# A copy of the plain fixture for the limits server, before anything is appended.
D5=$X/limits; mkdir -p "$D5/personal/claude/projects/-state-work"
cp "$D/personal/claude/projects/-state-work/$S.jsonl" "$D5/personal/claude/projects/-state-work/$S.jsonl"
cp "$D/personal/claude/projects/-state-work/$S.jsonl" "$D5/personal/claude/projects/-state-work/22222222-2222-3333-4444-555555555555.jsonl"
cp "$D/personal/claude/projects/-state-work/$S.jsonl" "$D5/personal/claude/projects/-state-work/33333333-2222-3333-4444-555555555555.jsonl"

# The main server opts out of the token check (local tests only).
PORT=18090 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets TRANSCRIPTS_REQUIRE_TOKEN=false python3 "$SRV" & P=$!
for i in $(seq 1 30); do curl -s localhost:18090/healthz >/dev/null && break; sleep 0.2; done
pass=0 fail=0
check() { if eval "$2"; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $1"; fi; }
code() { curl -s -o /dev/null -w %{http_code} "$@"; }
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
check "redacts a credential used as a JSON key" '[[ "$O" != *KeyAsFieldName* ]]'
check "redacts the hex form of the mounted nsec" '[[ "$O" != *c0ffee00c0ffee00* && "$O" == *"the signer key as hex"* ]]'
check "redacts an OpenAI project key" '[[ "$O" != *FakeProjKey* ]]'
check "redacts a legacy OpenAI key" '[[ "$O" != *FakeLegacyKey* ]]'
check "redacts an AWS_SECRET_ACCESS_KEY env value" '[[ "$O" != *FakeAwsSecretKey* ]]'
check "redacts an aws_secret_access_key ini value" '[[ "$O" != *FakeAwsIniKey* ]]'
check "one session by id" '[[ "$(curl -s "localhost:18090/session?id=$S")" == *"[AGENT SAYS] done"* ]]'
check "rejects a non uuid id" '[ "$(code "localhost:18090/session?id=../../etc")" = 400 ]'
check "does not list a symlinked transcript or project dir" '[[ "$L" != *99999999-* && "$L" != *88888888-* ]]'
check "does not render a symlinked transcript" '[[ "$O" != *OUTSIDE-MARKER* ]]'
check "a symlinked session by id is 404" '[ "$(code "localhost:18090/session?id=99999999-2222-3333-4444-555555555555")" = 404 ]'
check "rejects an infinite hours window" '[ "$(code "localhost:18090/show?hours=1e309")" = 400 ]'
check "rejects a NaN hours window" '[ "$(code "localhost:18090/show?hours=nan")" = 400 ]'
check "rejects a negative hours window" '[ "$(code "localhost:18090/sessions?hours=-1")" = 400 ]'
check "rejects an over-limit hours window" '[ "$(code "localhost:18090/sessions?hours=100000")" = 400 ]'
# A rotated credential is redacted at once, not after a cache expires.
printf 'rotated-credential-XYZ789' > "$D/secrets/octoprint/api-key"
printf '{"message":{"role":"user","content":[{"type":"tool_result","content":"rotated-credential-XYZ789"}]}}\n' >> "$D/personal/claude/projects/-state-work/$S.jsonl"
check "redacts a rotated mounted credential immediately" '[[ "$(curl -s "localhost:18090/show?hours=1")" != *rotated-credential-XYZ789* ]]'
check "old sessions drop out of the window" '[ -z "$(touch -d "-3 hours" "$D/personal/claude/projects/-state-work/$S.jsonl" 2>/dev/null || touch -A -030000 "$D/personal/claude/projects/-state-work/$S.jsonl"; curl -s "localhost:18090/sessions?hours=1")" ]'
# Bearer-token check: a second server with TRANSCRIPTS_TOKEN_FILE set requires the token.
TOKV="s3cret-""transcripts-token"; AUTH="Author""ization"
TOK=$D/secrets/transcripts-token; printf '%s' "$TOKV" > "$TOK"
PORT=18091 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets TRANSCRIPTS_TOKEN_FILE=$TOK python3 "$SRV" & P2=$!
for i in $(seq 1 30); do curl -s localhost:18091/healthz >/dev/null && break; sleep 0.2; done
check "healthz needs no token even when the check is on" '[ "$(code localhost:18091/healthz)" = 200 ]'
check "a request without a token is 401" '[ "$(code "localhost:18091/sessions?hours=1")" = 401 ]'
check "a request with a wrong token is 401" '[ "$(code -H "$AUTH: Bearer wrong-$TOKV" "localhost:18091/sessions?hours=1")" = 401 ]'
check "a non-Bearer Authorization header is 401" '[ "$(code -H "$AUTH: token $TOKV" "localhost:18091/sessions?hours=1")" = 401 ]'
check "the right token is accepted" '[ "$(code -H "$AUTH: Bearer $TOKV" "localhost:18091/sessions?hours=1")" = 200 ]'
kill $P2 2>/dev/null

# Fail closed: REQUIRE_TOKEN=true with no token file means 503 for everything but /healthz.
PORT=18092 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets TRANSCRIPTS_TOKEN_FILE=$D/no-such-token TRANSCRIPTS_REQUIRE_TOKEN=true python3 "$SRV" & P3=$!
for i in $(seq 1 30); do curl -s localhost:18092/healthz >/dev/null && break; sleep 0.2; done
check "fail closed: healthz still answers when the token file is missing" '[ "$(code localhost:18092/healthz)" = 200 ]'
check "fail closed: a request is 503 when the required token file is missing" '[ "$(code "localhost:18092/sessions?hours=1")" = 503 ]'
kill $P3 2>/dev/null

# Fail closed by default: no token file and no TRANSCRIPTS_REQUIRE_TOKEN at all.
PORT=18093 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets python3 "$SRV" & P4=$!
for i in $(seq 1 30); do curl -s localhost:18093/healthz >/dev/null && break; sleep 0.2; done
check "default: healthz answers without a token file" '[ "$(code localhost:18093/healthz)" = 200 ]'
check "default: transcripts are 503 without a token file" '[ "$(code "localhost:18093/show?hours=1")" = 503 ]'
kill $P4 2>/dev/null

# Limits: sessions listed, bytes read per transcript, bytes per response.
PORT=18094 TRANSCRIPTS_ROOT=$D5 SECRETS_DIR=$D/secrets TRANSCRIPTS_REQUIRE_TOKEN=false TRANSCRIPTS_MAX_SESSIONS=2 \
  TRANSCRIPTS_MAX_READ_BYTES=400 TRANSCRIPTS_MAX_RESPONSE_BYTES=300 python3 "$SRV" & P5=$!
for i in $(seq 1 30); do curl -s localhost:18094/healthz >/dev/null && break; sleep 0.2; done
check "lists at most MAX_SESSIONS sessions" '[ "$(curl -s "localhost:18094/sessions?hours=1" | grep -c .)" = 2 ]'
O5=$(curl -s "localhost:18094/session?id=$S")
check "reads only the tail of a large transcript and says so" '[[ "$O5" == *"[AGENT SAYS] done"* && "$O5" != *"@Bob hello"* && "$O5" == *"earlier bytes skipped"* ]]'
O5=$(curl -s "localhost:18094/show?hours=1")
check "caps the response size and says so" '[ ${#O5} -lt 450 ] && [[ "$O5" == *truncated* ]]'
kill $P5 2>/dev/null

# SIGTERM: the server handles it itself and exits 0. As a container's PID 1 a
# process with no handler ignores SIGTERM, which holds the pod for its whole
# termination grace period.
PORT=18095 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets python3 "$SRV" & P6=$!
for i in $(seq 1 30); do curl -s localhost:18095/healthz >/dev/null && break; sleep 0.2; done
kill -TERM $P6; for i in $(seq 1 15); do kill -0 $P6 2>/dev/null || break; sleep 0.2; done
wait $P6 2>/dev/null; RC6=$?
check "exits 0 on SIGTERM within 3s" '[ "$RC6" = 0 ]'

echo "transcripts server tests: $pass passed, $fail failed"; [ $fail -eq 0 ]
