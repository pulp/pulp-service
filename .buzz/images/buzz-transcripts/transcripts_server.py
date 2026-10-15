#!/usr/local/bin/python3
"""Read-only HTTP view of one agent's Claude Code sessions, for the chief of staff.

The agent's state volumes are mounted read only at /state/<community>; each holds
claude/projects/*/<session>.jsonl. Values that look like credentials are redacted
here, before anything leaves the pod.

    GET /sessions?hours=N          one line per session active in the last N hours
    GET /show?hours=N              every such session, condensed
    GET /session?id=<uuid>         one session in full
    GET /healthz

When TRANSCRIPTS_TOKEN_FILE names a readable, non-empty file, every request except
/healthz must carry Authorization: Bearer <that token> or it is answered 401; the
compare is constant time. When the file is absent or empty the check is off, so a
pod without the token behaves exactly as before, unless TRANSCRIPTS_REQUIRE_TOKEN
is "true": then a missing or empty token file fails closed, every request but
/healthz is answered 503 and the reason is logged, so a key dropped from the secret
later cannot silently turn auth off.
"""
import glob, hmac, json, os, re, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.environ.get("TRANSCRIPTS_ROOT", "/state")
UUID = re.compile(r"^[0-9a-f-]{36}$")
SECRET = re.compile(r"(sha256~[\w-]+|ASIA[A-Z0-9]{8,}|AKIA[A-Z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|glpat-[\w-]{10,}|"
                    r"sk-ant-[\w-]+|nsec1[02-9ac-hj-np-z]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----[^-]*|"
                    r"eyJ[\w-]{20,}\.[\w-]+\.[\w-]+|(?i:bearer\s+)[A-Za-z0-9._~+/=-]{16,}|(?i:^|\n)\s*pass\s*=\s*\S+|\b[0-9a-f]{40}\b|"
                    r"(?i:(?:token|secret|password|passwd|apikey|api_key|api-key|x-api-key|secretaccesskey|bw_session)[\"']?\s*[:=]\s*[\"']?)[^\s\"',]{8,}|"
                    r"(?i:private[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\s\"',]{8,}|"
                    r"tskey-[\w-]{10,}|GOCSPX-[\w-]{10,}|ya29\.[\w.-]{20,}|1//[\w-]{20,})")
# A Nostr private key stored as hex (or base64 of it) next to the word nsec, as in a
# Vaultwarden field {"name": "nsec", "value": "<hex>"}; only the value is redacted.
NSEC_FIELD = re.compile(r"(?is)(nsec.{0,80}?)(?<![0-9a-f])(?:[0-9a-f]{64}(?![0-9a-f])|[A-Za-z0-9+/]{86}==)")
SECRETS_DIR = os.environ.get("SECRETS_DIR", "/run/secrets")
TOKEN_FILE = os.environ.get("TRANSCRIPTS_TOKEN_FILE", "")
REQUIRE_TOKEN = os.environ.get("TRANSCRIPTS_REQUIRE_TOKEN", "false").lower() == "true"
_literals: list[str] = []
_literals_at = 0.0


def literals() -> list[str]:
    """The pod's own credential values (mounted read only), in the forms they could appear in a transcript."""
    global _literals, _literals_at
    if time.time() - _literals_at < 60:
        return _literals
    found = set()
    for dirpath, _, files in os.walk(SECRETS_DIR):
        for n in files:
            try:
                raw = open(os.path.join(dirpath, n), "rb").read()
            except OSError:
                continue
            text = raw.decode("utf-8", "replace")
            vals = {text.strip()}
            # Files with several lines: the value of each "key = value" line (rclone.conf),
            # and every long line of a file without such lines (an SSH key's body; a base64
            # line ending in "=" padding is not a key=value line).
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            kv = re.compile(r"^[\w.-]+\s*=\s*\S")
            if any(kv.match(l) for l in lines):
                for l in lines:
                    if kv.match(l):
                        vals.add(l.split("=", 1)[1].strip())
            else:
                vals.update(l for l in lines if len(l) >= 16)
            # A JSON credential file (an OAuth client, a token cache): every string value in it.
            try:
                def walk(o):
                    if isinstance(o, dict):
                        for v in o.values():
                            walk(v)
                    elif isinstance(o, list):
                        for v in o:
                            walk(v)
                    elif isinstance(o, str):
                        vals.add(o)
                walk(json.loads(text))
            except ValueError:
                pass
            import base64
            for v in list(vals):
                if len(v) >= 8:
                    found.add(v)
                    found.add(json.dumps(v)[1:-1])  # JSON escaped
                    found.add(base64.b64encode(v.encode()).decode())
    _literals = sorted(found, key=len, reverse=True)
    _literals_at = time.time()
    return _literals


def clean(s, n):
    for v in literals():
        s = s.replace(v, "[REDACTED]")
    s = NSEC_FIELD.sub(lambda m: m.group(1) + "[REDACTED]", s)
    s = SECRET.sub("[REDACTED]", s)
    return s if len(s) <= n else s[:n] + f" …[{len(s) - n} more chars]"


def clean_value(v):
    """Redact every string inside a JSON value before it is dumped, so escaping cannot hide a match."""
    if isinstance(v, str):
        return clean(v, 10**9)
    if isinstance(v, list):
        return [clean_value(x) for x in v]
    if isinstance(v, dict):
        return {k: clean_value(x) for k, x in v.items()}
    return v


def sessions(since):
    out = []
    for f in glob.glob(f"{ROOT}/*/claude/projects/*/*.jsonl"):
        mt = os.path.getmtime(f)
        if mt >= since:
            community = f[len(ROOT) + 1:].split("/", 1)[0]
            out.append((mt, os.path.basename(f)[:-6], community, f))
    return sorted(out)


def render(path, full):
    lim = 100000 if full else 1500
    lines = []
    for line in open(path, errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            c = [{"type": "text", "text": c}]
        for b in c or []:
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                who = "AGENT SAYS" if m.get("role") == "assistant" else "IN"
                lines.append(f"[{who}] {clean(b['text'], lim if who == 'IN' else 3000)}")
            elif t == "tool_use":
                lines.append(f"[TOOL {b.get('name')}] {clean(json.dumps(clean_value(b.get('input'))), lim)}")
            elif t == "tool_result":
                r = b.get("content")
                r = r if isinstance(r, str) else json.dumps(clean_value(r))
                lines.append(f"[RESULT{' ERROR' if b.get('is_error') else ''}] {clean(r, lim if full else 600)}")
    return lines


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t))


def required_token():
    """The bearer token requests must carry, or None when the check is off (no readable, non-empty token file)."""
    if not TOKEN_FILE:
        return None
    try:
        tok = open(TOKEN_FILE).read().strip()
    except OSError:
        return None
    return tok or None


class Handler(BaseHTTPRequestHandler):
    def auth_error(self):
        """None if the request may proceed, else (code, body) to answer instead of serving."""
        want = required_token()
        if want is None:
            if REQUIRE_TOKEN:
                print("transcripts: TRANSCRIPTS_REQUIRE_TOKEN=true but the token file is missing or empty; "
                      "refusing with 503", file=sys.stderr, flush=True)
                return 503, "transcripts token required but not configured\n"
            return None
        got = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if got.startswith(prefix) and hmac.compare_digest(got[len(prefix):], want):
            return None
        return 401, "unauthorized\n"

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            hours = float(q.get("hours", ["24"])[0])
        except ValueError:
            return self.reply(400, "hours must be a number\n")
        since = time.time() - hours * 3600
        if u.path == "/healthz":
            return self.reply(200, "ok\n")
        err = self.auth_error()
        if err:
            return self.reply(*err)
        if u.path == "/sessions":
            return self.reply(200, "".join(f"{stamp(mt)}  {sid}  {c}\n" for mt, sid, c, _ in sessions(since)))
        if u.path == "/show":
            out = []
            for mt, sid, c, f in sessions(since):
                out.append(f"\n===== session {sid} community {c} last active {stamp(mt)}")
                out += render(f, False)
            return self.reply(200, "\n".join(out) + "\n")
        if u.path == "/session":
            sid = q.get("id", [""])[0]
            if not UUID.match(sid):
                return self.reply(400, "id must be a session uuid\n")
            for mt, s, c, f in sessions(0):
                if s == sid:
                    return self.reply(200, f"===== session {sid} community {c} last active {stamp(mt)}\n" + "\n".join(render(f, True)) + "\n")
            return self.reply(404, "no such session\n")
        return self.reply(404, "see / for usage\n" if u.path != "/" else __doc__)

    def reply(self, code, body):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8090"))), Handler).serve_forever()
