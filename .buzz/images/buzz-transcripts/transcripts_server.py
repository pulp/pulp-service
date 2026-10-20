#!/usr/local/bin/python3
"""Read-only HTTP view of one agent's Claude Code sessions, for the chief of staff.

The agent's state volumes are mounted read only at /state/<community>; each holds
claude/projects/*/<session>.jsonl. Values that look like credentials are redacted
here, before anything leaves the pod.

Two listeners, each with its own bearer token, so a token leak in either direction
stays bounded. The transcript listener (PORT, default 8090) serves the auditor:

    GET /sessions?hours=N          one line per session active in the last N hours
    GET /show?hours=N              every such session, condensed
    GET /session?id=<uuid>         one session in full
    GET /healthz

The usage listener (USAGE_PORT, default 8091) serves the chief of staff's metering,
and serves counts only, never transcript text:

    GET /usage?hours=N             per day, per model, this pod's summed token counts
    GET /healthz

Each listener accepts only its own token: the transcript listener the content of
TRANSCRIPTS_TOKEN_FILE, the usage listener the content of USAGE_TOKEN_FILE. The two
tokens differ, so the transcript token never opens the usage listener and the usage
token never opens the transcript listener; the /usage listener has no transcript
route at all. Every request except /healthz must carry Authorization: Bearer
<token> or it is answered 401; the compare is constant time. Each listener fails
closed: when its token file is missing or empty, every request but /healthz is
answered 503 and the reason is logged, so a key dropped from the secret cannot
silently turn auth off. TRANSCRIPTS_REQUIRE_TOKEN=false turns the check off on both
listeners when no token file is readable; it exists for local tests only.

Only regular files inside TRANSCRIPTS_ROOT are served: a transcript or project
directory that is a symlink resolving outside the root is skipped. Work is bounded:
hours must be finite, positive and at most MAX_HOURS; at most MAX_SESSIONS (newest)
sessions are considered; only the last MAX_READ_BYTES of a transcript are read; and
a response stops at MAX_RESPONSE_BYTES (each TRANSCRIPTS_<NAME> in the environment).
"""
import base64, glob, hmac, json, math, os, re, signal, sys, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.environ.get("TRANSCRIPTS_ROOT", "/state")
UUID = re.compile(r"^[0-9a-f-]{36}$")
SECRET = re.compile(r"(sha256~[\w-]+|ASIA[A-Z0-9]{8,}|AKIA[A-Z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|glpat-[\w-]{10,}|"
                    r"sk-ant-[\w-]+|\bsk-(?:proj-|svcacct-|admin-)?[\w-]{20,}|nsec1[02-9ac-hj-np-z]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----[^-]*|"
                    r"eyJ[\w-]{20,}\.[\w-]+\.[\w-]+|(?i:bearer\s+)[A-Za-z0-9._~+/=-]{16,}|(?i:^|\n)\s*pass\s*=\s*\S+|\b[0-9a-f]{40}\b|"
                    r"(?i:(?:token|secret|password|passwd|apikey|api_key|api-key|x-api-key|secret[_-]?access[_-]?key|bw_session)[\"']?\s*[:=]\s*[\"']?)[^\s\"',]{8,}|"
                    r"(?i:private[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\s\"',]{8,}|"
                    r"tskey-[\w-]{10,}|GOCSPX-[\w-]{10,}|ya29\.[\w.-]{20,}|1//[\w-]{20,})")
# A Nostr private key stored as hex (or base64 of it) next to the word nsec, as in a
# Vaultwarden field {"name": "nsec", "value": "<hex>"}; only the value is redacted.
NSEC_FIELD = re.compile(r"(?is)(nsec.{0,80}?)(?<![0-9a-f])(?:[0-9a-f]{64}(?![0-9a-f])|[A-Za-z0-9+/]{86}==)")
SECRETS_DIR = os.environ.get("SECRETS_DIR", "/run/secrets")
TOKEN_FILE = os.environ.get("TRANSCRIPTS_TOKEN_FILE", "")
USAGE_TOKEN_FILE = os.environ.get("USAGE_TOKEN_FILE", "")
# Fail closed unless explicitly turned off (local tests only).
REQUIRE_TOKEN = os.environ.get("TRANSCRIPTS_REQUIRE_TOKEN", "true").lower() != "false"
MAX_HOURS = float(os.environ.get("TRANSCRIPTS_MAX_HOURS", str(24 * 31)))
MAX_SESSIONS = int(os.environ.get("TRANSCRIPTS_MAX_SESSIONS", "500"))
MAX_READ_BYTES = int(os.environ.get("TRANSCRIPTS_MAX_READ_BYTES", str(32 << 20)))
MAX_RESPONSE_BYTES = int(os.environ.get("TRANSCRIPTS_MAX_RESPONSE_BYTES", str(16 << 20)))
_literals: list[str] = []
_literals_sig = None
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def nsec_hex(v):
    """The 64 character hex key inside a bech32 nsec1 string, or None. The checksum is not verified."""
    if not v.lower().startswith("nsec1"):
        return None
    try:
        data = [BECH32.index(c) for c in v.lower()[5:]][:-6]
    except ValueError:
        return None
    acc, bits, out = 0, 0, bytearray()
    for d in data:
        acc, bits = (acc << 5) | d, bits + 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return out.hex() if len(out) == 32 else None


def secrets_signature():
    """Path, inode, size and mtime of every mounted secret file, so a rotation is seen at once."""
    sig = []
    for dirpath, _, files in os.walk(SECRETS_DIR):
        for n in files:
            try:
                st = os.stat(os.path.join(dirpath, n))
            except OSError:
                continue
            sig.append((dirpath, n, st.st_ino, st.st_size, st.st_mtime_ns))
    return tuple(sorted(sig))


def literals() -> list[str]:
    """The pod's own credential values (mounted read only), in the forms they could appear in a transcript."""
    global _literals, _literals_sig
    sig = secrets_signature()
    if sig == _literals_sig:
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
            for v in list(vals):
                h = nsec_hex(v)
                if h:  # a Nostr key also turns up as hex, without the word nsec nearby
                    found.update((h, h.upper()))
                if len(v) >= 8:
                    found.add(v)
                    found.add(json.dumps(v)[1:-1])  # JSON escaped
                    found.add(base64.b64encode(v.encode()).decode())
    _literals = sorted(found, key=len, reverse=True)
    _literals_sig = sig
    return _literals


def clean(s, n, lits=None):
    for v in literals() if lits is None else lits:
        s = s.replace(v, "[REDACTED]")
    s = NSEC_FIELD.sub(lambda m: m.group(1) + "[REDACTED]", s)
    s = SECRET.sub("[REDACTED]", s)
    return s if len(s) <= n else s[:n] + f" …[{len(s) - n} more chars]"


def clean_value(v, lits=None):
    """Redact every string inside a JSON value, keys included, before it is dumped, so escaping cannot hide a match."""
    lits = literals() if lits is None else lits
    if isinstance(v, str):
        return clean(v, 10**9, lits)
    if isinstance(v, list):
        return [clean_value(x, lits) for x in v]
    if isinstance(v, dict):
        return {clean(k, 10**9, lits): clean_value(x, lits) for k, x in v.items()}
    return v


def inside_root(f):
    """True when f is a regular file (not a symlink) whose real path stays under ROOT."""
    root = os.path.realpath(ROOT) + os.sep
    return not os.path.islink(f) and os.path.isfile(f) and os.path.realpath(f).startswith(root)


def sessions(since, cap=True):
    """The transcripts active since a time, oldest first; with cap, only the newest MAX_SESSIONS."""
    out = []
    for f in glob.glob(f"{ROOT}/*/claude/projects/*/*.jsonl"):
        if not inside_root(f):
            continue
        try:
            mt = os.path.getmtime(f)
        except OSError:
            continue
        if mt >= since:
            community = f[len(ROOT) + 1:].split("/", 1)[0]
            out.append((mt, os.path.basename(f)[:-6], community, f))
    out.sort()
    if not cap:
        return out
    return out[-MAX_SESSIONS:] if MAX_SESSIONS > 0 else []


def tail_lines(path):
    """The lines of the last MAX_READ_BYTES of a transcript, opened without following a symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:  # gone since it was listed, or swapped for a symlink
        return
    with os.fdopen(fd, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        skipped = max(0, size - MAX_READ_BYTES)
        fh.seek(skipped)
        data = fh.read(MAX_READ_BYTES)
    if skipped:
        data = data.split(b"\n", 1)[1] if b"\n" in data else b""
        yield f"[... {skipped} earlier bytes skipped]"
    for line in data.decode("utf-8", "replace").splitlines():
        yield line


def render(path, full):
    lim = 100000 if full else 1500
    lits = literals()
    lines = []
    for line in tail_lines(path):
        if line.startswith("[... "):
            lines.append(line)
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message") if isinstance(d, dict) else None
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            c = [{"type": "text", "text": c}]
        for b in c or []:
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                who = "AGENT SAYS" if m.get("role") == "assistant" else "IN"
                lines.append(f"[{who}] {clean(b['text'], lim if who == 'IN' else 3000, lits)}")
            elif t == "tool_use":
                lines.append(f"[TOOL {b.get('name')}] {clean(json.dumps(clean_value(b.get('input'), lits)), lim, lits)}")
            elif t == "tool_result":
                r = b.get("content")
                r = r if isinstance(r, str) else json.dumps(clean_value(r, lits))
                lines.append(f"[RESULT{' ERROR' if b.get('is_error') else ''}] {clean(r, lim if full else 600, lits)}")
    return lines


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t))


USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def turn_epoch(ts):
    """Epoch seconds for an ISO 8601 transcript timestamp (e.g. 2026-10-20T14:17:07.464Z), or None."""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A timestamp without a zone is read as UTC, so day bucketing never depends on the pod's
    # local time (gmtime below is UTC too). Claude Code always stamps with Z, so this is defence.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def usage_since(since):
    """This pod's token usage as {day: {model: {field: sum, "turns": n}}}.

    Each assistant turn carries message.usage (the four USAGE_FIELDS) and message.model; it is
    counted under the UTC day of its own timestamp, and only when that timestamp is at or after
    `since`, so a long lived session is split across the days it actually ran and a turn older
    than the window is not counted. The counts are numbers and day strings; the one free-text
    field, the model id, is coerced to a string and redacted the same way transcript text is (see
    clean), so a value injected into message.model cannot leak through or break the per-model
    bucketing. Work is bounded the same way the transcript endpoints are: only
    the newest MAX_SESSIONS sessions active since `since`, and only the last MAX_READ_BYTES of
    each are read."""
    days = {}
    for mt, sid, community, f in sessions(since):
        for line in tail_lines(f):
            if line.startswith("[... "):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") if isinstance(d, dict) else None
            if not isinstance(m, dict):
                continue
            u = m.get("usage")
            if not isinstance(u, dict):
                continue
            ep = turn_epoch(d.get("timestamp"))
            if ep is None or ep < since:
                continue
            day = time.strftime("%Y-%m-%d", time.gmtime(ep))
            # The model id is the one free-text field here; coerce to a string (so a non-string
            # value cannot become an unhashable or mixed-type dict key) and redact it the same way
            # transcript text is, so a value injected into message.model cannot reach the counts
            # reader.
            model = clean(str(m.get("model") or "unknown"), 128)
            bucket = days.setdefault(day, {}).setdefault(model, {k: 0 for k in USAGE_FIELDS} | {"turns": 0})
            for k in USAGE_FIELDS:
                v = u.get(k)
                if isinstance(v, (int, float)):
                    bucket[k] += int(v)
            bucket["turns"] += 1
    return days


def required_token(path):
    """The bearer token requests must carry, or None when there is no readable, non-empty token file."""
    if not path:
        return None
    try:
        tok = open(path).read().strip()
    except OSError:
        return None
    return tok or None


def capped(lines):
    """Join lines into a response body of at most MAX_RESPONSE_BYTES, saying when it was cut."""
    out, used = [], 0
    for line in lines:
        n = len(line.encode()) + 1
        if used + n > MAX_RESPONSE_BYTES:
            out.append(f"[... response truncated at {MAX_RESPONSE_BYTES} bytes; narrow the hours window or ask for one session]")
            break
        out.append(line)
        used += n
    return "".join(l + "\n" for l in out)


class BaseHandler(BaseHTTPRequestHandler):
    # Each listener accepts only its own token file. TOKEN_PATH and TOKEN_LABEL are set by
    # the subclass, so the transcript token never opens the usage listener and the reverse.
    TOKEN_PATH = ""
    TOKEN_LABEL = ""

    def auth_error(self):
        """None if the request may proceed, else (code, body) to answer instead of serving."""
        want = required_token(self.TOKEN_PATH)
        if want is None:
            if REQUIRE_TOKEN:
                print(f"{self.TOKEN_LABEL}: a token is required but its token file is unset, missing or empty; "
                      "refusing with 503 (TRANSCRIPTS_REQUIRE_TOKEN=false turns this off, for local tests only)",
                      file=sys.stderr, flush=True)
                return 503, f"{self.TOKEN_LABEL} token required but not configured\n"
            return None
        got = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if got.startswith(prefix) and hmac.compare_digest(got[len(prefix):], want):
            return None
        return 401, "unauthorized\n"

    def reply(self, code, body, content_type="text/plain; charset=utf-8"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


class TranscriptHandler(BaseHandler):
    """The auditor's listener: the full redacted transcript text. Never serves /usage."""
    TOKEN_PATH = TOKEN_FILE
    TOKEN_LABEL = "transcripts"

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/healthz":
            return self.reply(200, "ok\n")
        err = self.auth_error()
        if err:
            return self.reply(*err)
        if u.path in ("/sessions", "/show"):
            try:
                hours = float(q.get("hours", ["24"])[0])
            except ValueError:
                hours = math.nan
            if not (math.isfinite(hours) and 0 < hours <= MAX_HOURS):
                return self.reply(400, f"hours must be a number above 0 and at most {MAX_HOURS:g}\n")
            since = time.time() - hours * 3600
        if u.path == "/sessions":
            return self.reply(200, capped(f"{stamp(mt)}  {sid}  {c}" for mt, sid, c, _ in sessions(since)))
        if u.path == "/show":
            def show():
                for mt, sid, c, f in sessions(since):
                    yield f"\n===== session {sid} community {c} last active {stamp(mt)}"
                    yield from render(f, False)
            return self.reply(200, capped(show()))
        if u.path == "/session":
            sid = q.get("id", [""])[0]
            if not UUID.match(sid):
                return self.reply(400, "id must be a session uuid\n")
            for mt, s, c, f in sessions(0, cap=False):
                if s == sid:
                    head = [f"===== session {sid} community {c} last active {stamp(mt)}"]
                    return self.reply(200, capped(head + render(f, True)))
            return self.reply(404, "no such session\n")
        return self.reply(404, "see / for usage\n" if u.path != "/" else __doc__)


class UsageHandler(BaseHandler):
    """The chief of staff's listener: per day, per model token counts only. No transcript route."""
    TOKEN_PATH = USAGE_TOKEN_FILE
    TOKEN_LABEL = "usage"

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/healthz":
            return self.reply(200, "ok\n")
        err = self.auth_error()
        if err:
            return self.reply(*err)
        if u.path == "/usage":
            try:
                hours = float(q.get("hours", ["24"])[0])
            except ValueError:
                hours = math.nan
            if not (math.isfinite(hours) and 0 < hours <= MAX_HOURS):
                return self.reply(400, f"hours must be a number above 0 and at most {MAX_HOURS:g}\n")
            since = time.time() - hours * 3600
            body = json.dumps({"generated": stamp(time.time()), "hours": hours, "root": ROOT,
                               "days": usage_since(since)}, separators=(",", ":"), sort_keys=True)
            if len(body.encode()) > MAX_RESPONSE_BYTES:
                return self.reply(400, "usage response too large; narrow the hours window\n")
            return self.reply(200, body + "\n", "application/json; charset=utf-8")
        return self.reply(404, "only /usage and /healthz are served here\n")


if __name__ == "__main__":
    # As the container's PID 1 this process ignores SIGTERM unless it handles it, which
    # would hold the pod for its whole termination grace period. Exit at once instead.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    port = int(os.environ.get("PORT", "8090"))
    uv = os.environ.get("USAGE_PORT", "8091").strip()
    usage_port = int(uv) if uv else 0
    # The usage listener runs in a daemon thread; the transcript listener holds the main
    # thread. SIGTERM exits the process, which drops the daemon thread with it. USAGE_PORT
    # empty or 0 runs the transcript listener alone (used by the tests).
    if usage_port > 0:
        # The two listeners are only as separate as their tokens. If both files are present and
        # hold the same value, a leak of either would open both, so refuse to start rather than
        # serve a guarantee that does not hold. A missing or empty token is the normal fail-closed
        # case (that listener answers 503), not a misconfiguration, so it is not caught here.
        t_tok, u_tok = required_token(TOKEN_FILE), required_token(USAGE_TOKEN_FILE)
        if t_tok and u_tok and hmac.compare_digest(t_tok, u_tok):
            sys.exit("transcript and usage tokens must differ; refusing to start")
        usage = ThreadingHTTPServer(("0.0.0.0", usage_port), UsageHandler)
        threading.Thread(target=usage.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", port), TranscriptHandler).serve_forever()
