#!/bin/bash
# Starts the server on a fixture tree and checks listing, rendering, redaction and input checks.
set -u
D=$(mktemp -d); X=$(mktemp -d); DU=$(mktemp -d); trap 'kill $P $P4 $P5 $PU $PUF $PS 2>/dev/null; rm -rf $D $X $DU' EXIT
P4= P5= PU= PUF= PS=
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
PORT=18090 USAGE_PORT=0 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets TRANSCRIPTS_REQUIRE_TOKEN=false python3 "$SRV" & P=$!
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
PORT=18091 USAGE_PORT=0 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets TRANSCRIPTS_TOKEN_FILE=$TOK python3 "$SRV" & P2=$!
for i in $(seq 1 30); do curl -s localhost:18091/healthz >/dev/null && break; sleep 0.2; done
check "healthz needs no token even when the check is on" '[ "$(code localhost:18091/healthz)" = 200 ]'
check "a request without a token is 401" '[ "$(code "localhost:18091/sessions?hours=1")" = 401 ]'
check "a request with a wrong token is 401" '[ "$(code -H "$AUTH: Bearer wrong-$TOKV" "localhost:18091/sessions?hours=1")" = 401 ]'
check "a non-Bearer Authorization header is 401" '[ "$(code -H "$AUTH: token $TOKV" "localhost:18091/sessions?hours=1")" = 401 ]'
check "the right token is accepted" '[ "$(code -H "$AUTH: Bearer $TOKV" "localhost:18091/sessions?hours=1")" = 200 ]'
kill $P2 2>/dev/null

# Fail closed: REQUIRE_TOKEN=true with no token file means 503 for everything but /healthz.
PORT=18092 USAGE_PORT=0 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets TRANSCRIPTS_TOKEN_FILE=$D/no-such-token TRANSCRIPTS_REQUIRE_TOKEN=true python3 "$SRV" & P3=$!
for i in $(seq 1 30); do curl -s localhost:18092/healthz >/dev/null && break; sleep 0.2; done
check "fail closed: healthz still answers when the token file is missing" '[ "$(code localhost:18092/healthz)" = 200 ]'
check "fail closed: a request is 503 when the required token file is missing" '[ "$(code "localhost:18092/sessions?hours=1")" = 503 ]'
kill $P3 2>/dev/null

# Fail closed by default: no token file and no TRANSCRIPTS_REQUIRE_TOKEN at all.
PORT=18093 USAGE_PORT=0 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets python3 "$SRV" & P4=$!
for i in $(seq 1 30); do curl -s localhost:18093/healthz >/dev/null && break; sleep 0.2; done
check "default: healthz answers without a token file" '[ "$(code localhost:18093/healthz)" = 200 ]'
check "default: transcripts are 503 without a token file" '[ "$(code "localhost:18093/show?hours=1")" = 503 ]'
kill $P4 2>/dev/null

# Limits: sessions listed, bytes read per transcript, bytes per response.
PORT=18094 USAGE_PORT=0 TRANSCRIPTS_ROOT=$D5 SECRETS_DIR=$D/secrets TRANSCRIPTS_REQUIRE_TOKEN=false TRANSCRIPTS_MAX_SESSIONS=2 \
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
PORT=18095 USAGE_PORT=0 TRANSCRIPTS_ROOT=$D SECRETS_DIR=$D/secrets python3 "$SRV" & P6=$!
for i in $(seq 1 30); do curl -s localhost:18095/healthz >/dev/null && break; sleep 0.2; done
kill -TERM $P6; for i in $(seq 1 15); do kill -0 $P6 2>/dev/null || break; sleep 0.2; done
wait $P6 2>/dev/null; RC6=$?
check "exits 0 on SIGTERM within 3s" '[ "$RC6" = 0 ]'

# Usage listener: per day, per model token counts on its own port, behind its own token,
# with the two tokens refusing each other. Timestamps come from python so the fixture lands
# in the window on any platform.
NOWTS=$(python3 -c 'import datetime;print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))')
TODAY=${NOWTS%%T*}
OLDTS=$(python3 -c 'import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z"))')
OLDDAY=${OLDTS%%T*}
UTOKV="us4ge-""token-value-xyz"; TTOKV="tr4nscript-""token-value-xyz"
mkdir -p "$DU/rh/claude/projects/-state-work" "$DU/secrets"
cat > "$DU/rh/claude/projects/-state-work/$S.jsonl" <<J
{"timestamp":"$NOWTS","message":{"role":"assistant","model":"claude-opus-4-8","usage":{"input_tokens":10,"cache_creation_input_tokens":100,"cache_read_input_tokens":1000,"output_tokens":5}}}
{"timestamp":"$NOWTS","message":{"role":"user","content":"a user turn carries no usage and must not count"}}
{"timestamp":"$NOWTS","message":{"role":"assistant","model":"claude-opus-4-8","usage":{"input_tokens":20,"cache_creation_input_tokens":200,"cache_read_input_tokens":2000,"output_tokens":7}}}
{"timestamp":"$NOWTS","message":{"role":"assistant","model":"mdl-$TTOKV-end","usage":{"input_tokens":1,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":0}}}
{"timestamp":"$NOWTS","message":{"role":"assistant","model":{"evil":"object"},"usage":{"input_tokens":1,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":0}}}
{"timestamp":"$OLDTS","message":{"role":"assistant","model":"claude-opus-4-8","usage":{"input_tokens":99999,"cache_creation_input_tokens":99999,"cache_read_input_tokens":99999,"output_tokens":99999}}}
J
UTOK=$DU/secrets/usage-token; printf '%s' "$UTOKV" > "$UTOK"
TTOK=$DU/secrets/transcripts-token; printf '%s' "$TTOKV" > "$TTOK"
PORT=18096 USAGE_PORT=18097 TRANSCRIPTS_ROOT=$DU SECRETS_DIR=$DU/secrets \
  TRANSCRIPTS_TOKEN_FILE=$TTOK USAGE_TOKEN_FILE=$UTOK python3 "$SRV" & PU=$!
for i in $(seq 1 30); do curl -s localhost:18097/healthz >/dev/null && break; sleep 0.2; done
# Content: the two assistant turns in the window sum per day and model; the user turn and the
# turn two days old are not in a 1h window.
U=$(curl -s -H "$AUTH: Bearer $UTOKV" "localhost:18097/usage?hours=1")
RU=$(printf '%s' "$U" | python3 -c 'import sys,json
d=json.load(sys.stdin)
b=d["days"]["'$TODAY'"]["claude-opus-4-8"]
exp={"input_tokens":30,"cache_creation_input_tokens":300,"cache_read_input_tokens":3000,"output_tokens":12,"turns":2}
ok = b==exp and "'$OLDDAY'" not in d["days"] and set(d)=={"generated","hours","root","days"}
print("OK" if ok else "BAD "+repr(d))')
check "usage sums the four counts per day and model, window and roles respected" '[ "$RU" = OK ]'
# A wider window reaches the older day without double counting today.
U2=$(curl -s -H "$AUTH: Bearer $UTOKV" "localhost:18097/usage?hours=96")
RU2=$(printf '%s' "$U2" | python3 -c 'import sys,json
d=json.load(sys.stdin)["days"]
ok = d["'$OLDDAY'"]["claude-opus-4-8"]["input_tokens"]==99999 and d["'$TODAY'"]["claude-opus-4-8"]["input_tokens"]==30
print("OK" if ok else "BAD "+repr(d))')
check "a wider usage window reaches the older day and keeps today exact" '[ "$RU2" = OK ]'
# The model id is the one free-text field in the counts; a value injected into it is redacted
# the same way transcript text is, so it cannot reach the counts reader, and a non-string model
# is coerced to a string key rather than crashing the per-model bucketing.
check "usage redacts a secret injected into the model id" '[[ "$U" != *"$TTOKV"* ]] && [[ "$U" == *"mdl-[REDACTED]-end"* ]]'
check "usage coerces a non-string model to a string key without crashing" '[ "$(code -H "$AUTH: Bearer $UTOKV" "localhost:18097/usage?hours=1")" = 200 ] && [[ "$U" == *evil* ]]'
check "usage rejects a zero hours window" '[ "$(code -H "$AUTH: Bearer $UTOKV" "localhost:18097/usage?hours=0")" = 400 ]'
check "usage healthz needs no token" '[ "$(code localhost:18097/healthz)" = 200 ]'
check "usage without a token is 401" '[ "$(code "localhost:18097/usage?hours=1")" = 401 ]'
# Cross rejection: each listener accepts only its own token, so a leak stays bounded.
check "usage listener refuses the transcript token" '[ "$(code -H "$AUTH: Bearer $TTOKV" "localhost:18097/usage?hours=1")" = 401 ]'
check "usage listener serves no transcript route even with its own token" '[ "$(code -H "$AUTH: Bearer $UTOKV" "localhost:18097/sessions?hours=1")" = 404 ] && [ "$(code -H "$AUTH: Bearer $UTOKV" "localhost:18097/show?hours=1")" = 404 ] && [ "$(code -H "$AUTH: Bearer $UTOKV" "localhost:18097/session?id=$S")" = 404 ]'
check "transcript listener accepts its own token" '[ "$(code -H "$AUTH: Bearer $TTOKV" "localhost:18096/sessions?hours=1")" = 200 ]'
check "transcript listener refuses the usage token" '[ "$(code -H "$AUTH: Bearer $UTOKV" "localhost:18096/sessions?hours=1")" = 401 ]'
check "transcript listener serves no /usage route" '[ "$(code -H "$AUTH: Bearer $TTOKV" "localhost:18096/usage?hours=1")" = 404 ]'
kill $PU 2>/dev/null

# Usage fails closed on its own: a missing usage token file is 503 on /usage while the
# transcript listener on the same process keeps serving with its own token.
PORT=18098 USAGE_PORT=18099 TRANSCRIPTS_ROOT=$DU SECRETS_DIR=$DU/secrets \
  TRANSCRIPTS_TOKEN_FILE=$TTOK USAGE_TOKEN_FILE=$DU/no-such-usage-token python3 "$SRV" & PUF=$!
for i in $(seq 1 30); do curl -s localhost:18099/healthz >/dev/null && break; sleep 0.2; done
check "fail closed: usage is 503 when its token file is missing" '[ "$(code "localhost:18099/usage?hours=1")" = 503 ]'
check "fail closed: usage healthz still answers" '[ "$(code localhost:18099/healthz)" = 200 ]'
check "the transcript listener is unaffected by the missing usage token" '[ "$(code -H "$AUTH: Bearer $TTOKV" "localhost:18098/sessions?hours=1")" = 200 ]'
kill $PUF 2>/dev/null

# The two listeners are only as separate as their tokens: if both files hold the same value the
# process refuses to start, so a single leaked token can never open both listeners at once.
SAME=$DU/secrets/same-token; printf '%s' "same-token-value-abc" > "$SAME"
PORT=18100 USAGE_PORT=18101 TRANSCRIPTS_ROOT=$DU SECRETS_DIR=$DU/secrets \
  TRANSCRIPTS_TOKEN_FILE=$SAME USAGE_TOKEN_FILE=$SAME python3 "$SRV" & PS=$!
sleep 1
check "refuses to start when the transcript and usage tokens are identical" '! curl -s localhost:18101/healthz >/dev/null && ! curl -s localhost:18100/healthz >/dev/null'
kill $PS 2>/dev/null; PS=

echo "transcripts server tests: $pass passed, $fail failed"; [ $fail -eq 0 ]
