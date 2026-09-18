# public-trusted-libraries 404 — Simulation Procedure (re-runnable)

## TLDR

- **Symptom:** 464 production `GET /api/pypi/public-trusted-libraries/main/simple/<pkg>/` 404s
  (`org_id = -`) — mostly benign `fromager` cache-miss checks during Konflux wheel builds.
- **What this branch fixes:** an *authenticated* org member is scoped out of an org-owned
  `public-*` domain when `DomainOrg.org_id` is NULL and the `rh-org` group is role-less → 404 on
  scoped reads and 400 on writes (idempotency pre-check hides duplicates). Migration `0022`
  backfills the role and re-derives `org_id` → 200.
- **What it does NOT fix:** (a) anonymous / role-less scoped reads stay 404 — those need copr
  "Fix A", which is absent here; (b) the literal `simple/` path is served *unscoped* by
  pulp_python's `SimpleView`, so its 404s are pulp_python-level, not RBAC.
- **Verdict:** fix confirmed for the authenticated scope-out; the residual `simple/` 404s are out
  of scope. Details in `simulation-results-public-trusted-libraries.md`.

A **third, separate** simulation (do not confuse with the sibling docs below). It blends the
PULP-2120 null-`org_id` calunga shape with the copr `public-*` domain shape to reproduce the
production `public-trusted-libraries` 404s and answer, for branch `fix/backfill-domainorg-org-id`,
**what it fixes and what it does not.**

Sibling docs (leave untouched):
- `simulation-procedure.md` / `simulation-results.md` — PULP-2120 / calunga (authenticated org-member scoped read).
- `simulation-procedure-copr-public-domain.md` / `simulation-results-copr-public-domain.md` — copr `public-*` read 404 + "Fix A".

Verdict lives in `simulation-results-public-trusted-libraries.md`.

## The production shape (from `public-trusted-libraries-404s.md`)

All 464 logged 404s are:

```
GET /api/pypi/public-trusted-libraries/main/simple/<pkg>/     org_id = -
```

reads of the **PyPI `simple/` index** on a `public-*` domain. Many rows show a doubled
`.../main/simple/simple/<pkg>/` — a client base-URL misconfiguration.

**Confirmed caller (from the Calunga repos at `~/work/rhtl/`):** these are **not** anonymous
browser traffic. They are **`fromager`** build-cache checks during Konflux wheel builds
(`plumbing/builder/scripts/build-wheels` → `fromager ... --cache-wheel-server-url
.../public-trusted-libraries/main/simple/`): before building a package from source, fromager asks
the simple index whether an already-built wheel is cached. Auth is **HTTP Basic** via `~/.netrc`
with the `calunga-internal` service account (org `15322645`) — no `x-rh-identity`, hence the log's
`org_id = -` — or genuinely anonymous when the SA creds are unset. Because `SimpleView` is
`principal:"*"` + unscoped, the auth mode does not change the status code; a per-package 404 is
fromager's normal **cache-miss** signal, so much of the 464 is likely benign. Distribution
creation and the doubled-URL source are **not** in the client repos (Pulp-service/admin +
external/manual config). See `simulation-results-public-trusted-libraries.md` for the full
client-behavior findings.

This shape is two problems layered together, and only one of them is RBAC:

- **Layer 1 (RBAC scope-out — what this branch fixes):** an *authenticated* org member (present
  only in `rh-org-<org>`) is scoped out of an org-owned `public-*` domain when
  `DomainOrg.org_id` is NULL and the `rh-org` group is role-less → 404 on scoped endpoints
  (repo-by-href, content list). Migration `0022` backfills the role and re-derives `org_id` → 200.
  Root cause and calunga evidence: `calunga-rbac-400-fixes.md`.

  The **same scope-out also breaks writes** — this is the failed Konflux release in
  `calunga-failed-push.log` (twine `POST .../main/simple/` → `400 Bad Request` for 23 of 26
  files, SA `15322645|calunga-internal`). The push client (`~/work/rhtl/plumbing/utils/scripts/pulp-upload`)
  runs an **idempotency pre-check** (a *scoped* content-list filtered by filename) before each
  upload; scoped out, that list returns 0 → the client concludes "not present" → re-uploads a
  dependency already synced into `main` → pulp_python's **unscoped** duplicate guard
  (`pypi/views.py:219`) returns `400 "Package … already exists in index"`. The genuinely-new
  release package uploads fine (200), which is why only the already-present deps 400. `0022`
  restores the role → the pre-check sees the dep → the client skips it → no 400. The 400 itself
  is correct duplicate detection; the bug is that RBAC *hid the duplicates from the client's own
  check*.

- **Layer 2 (what this branch does NOT fix):**
  - **(a) anonymous / role-less scoped reads** on the `public-*` domain stay **404 even after
    `0022`** — an anonymous caller has no org and no role, so role-based `scope_queryset`
    returns empty. Closing this needs the copr **"Fix A"** (`scope_queryset` `public-*` bypass
    for non-`Domain` models, on `feat/rbac-orphaned-content-access`), which is **absent here**.
  - **(b) the literal `pypi/.../simple/` path** is served by pulp_python's `SimpleView`, whose
    `DEFAULT_ACCESS_POLICY` allows `list`/`retrieve` for `principal:"*"` and reads content
    **unscoped**. RBAC never gates it (`has_permission` passes via the `public-*` bypass *and*
    the `*`-allow policy). Its 404s are **pulp_python-level** — no distribution for the
    base_path, no repository version, package absent, or the doubled `simple/simple/` client
    misconfig — **not** the RBAC scope-out this branch touches.

## Environment under test — "2120 + this fix"

This branch (`fix/backfill-domainorg-org-id`) diverges from `feat/pulp-2120`:
`AccessPolicyFromSettings` (here) vs `AccessPolicyFromDB` (2120), and
`DomainBasedPermission`-on-CreateDomainView (here) vs `set_domain_create_context` (2120). To run
the sim representative of where this fix will actually land, the running `pulp-dev` container is
staged as **feat/pulp-2120 RBAC code + this branch's fix**:

- Overlaid from `feat/pulp-2120` (backed up as `*.bak-fixbranch`): `access_policy.py`,
  `authorization.py`, `viewsets.py`, `settings.py`, `content.py`.
- Kept / synced from this branch HEAD: `signals.py` (`_derive_org_id_from_user`) and
  `migrations/0022_backfill_org_group_roles.py`.

Confirm the staged env:

```bash
podman exec pulp-dev bash -lc 'runuser -u pulp -- pulpcore-manager shell -c \
  "from django.conf import settings; print(settings.REST_FRAMEWORK[\"DEFAULT_PERMISSION_CLASSES\"])"'
# -> ['pulp_service.app.access_policy.PulpServiceAccessPolicy']

podman exec pulp-dev bash -lc 'grep -n "AccessPolicyFromDB\|set_domain_create_context" \
  /plugins/pulp_service/pulp_service/app/access_policy.py /plugins/pulp_service/pulp_service/app/viewsets.py | head'
# -> access_policy.py: class PulpServiceAccessPolicy(AccessPolicyFromDB)  (2120 base)
# -> viewsets.py: set_domain_create_context wired into CreateDomainView   (2120 create-context)

podman exec pulp-dev bash -lc 'runuser -u pulp -- pulpcore-manager showmigrations service | tail -3'
# -> [X] 0022_backfill_org_group_roles   (this branch's fix)
```

> **Restore afterwards:** `for f in access_policy authorization viewsets settings content; do
> podman exec pulp-dev bash -lc "cp /plugins/pulp_service/pulp_service/app/$f.bak-fixbranch
> /plugins/pulp_service/pulp_service/app/$f.py"; done` then `podman exec pulp-dev pulp-restart`.

## The two layers under test

| Layer | Caller | Endpoint | Broken | After `0022` | What owns it |
|-------|--------|----------|--------|--------------|--------------|
| **L1 (read)** | authenticated org member (`rh-org` only) | scoped repo-by-href / content list | 404 | **200** | this branch (`0022` + `signals` derive) |
| **L1 (write)** | authenticated SA / member (`rh-org` only) | idempotency pre-check (scoped content-list by filename) | 0 results (blinded) | **1 result** | this branch — restores the pre-check so the client skips existing deps |
| **L1 (write)** | uploader that holds the role | re-upload of an existing filename | 400 "already exists" | 400 (client no longer re-uploads) | pulp_python **unscoped** dup guard (`pypi/views.py:219`) — correct; branch removes the *reason* it re-uploads |
| **L2a** | anonymous / role-less | scoped repo-by-href | 404 | **still 404** | needs copr "Fix A" (absent here) |
| **L2b** | fromager SA / anon | `pypi/.../simple/` (dist present) | 200 | 200 | `SimpleView` `*`-allow + unscoped — not RBAC |
| **L2b** | fromager SA / anon | `pypi/.../simple/<pkg>/` (pkg absent) | 404 | 404 | fromager cache-miss (benign) — not RBAC |
| **L2b** | fromager SA / anon | `pypi/.../simple/` (no dist / doubled) | 404 | 404 | pulp_python-level (`Http404`), not RBAC |

## The simulation script (`pub_trusted_sim.py`)

Django ORM for state + `requests` for real HTTP, run through `pulpcore-manager shell`. Reuses the
helper shape from `simulation-procedure.md` (`hdr`, `GET`, `check`, `rh_role_count`); `GET` also
supports an **anonymous** call (no `x-rh-identity`) via `ident=None`.

```python
"""public-trusted-libraries 404 simulation.

Environment under test: feat/pulp-2120 RBAC (AccessPolicyFromDB + set_domain_create_context)
overlaid on branch fix/backfill-domainorg-org-id (migration 0022 backfill + signals derive).

Reproduces BOTH layers of the production `public-trusted-libraries` 404 scenario
(public_trusted_libraries_404s.md -- 464x anonymous GET .../simple/<pkg>/ on a public-* domain):

  Layer 1 (what THIS branch fixes): an authenticated org member (only in rh-org-<org>) is
    scoped out of an org-owned public-* domain when DomainOrg.org_id is NULL and the rh-org
    group is role-less -> 404 on the scoped repo-by-href read. Migration 0022 backfills the
    role (and re-derives org_id) -> 200. The SAME scope-out also breaks WRITES (the Konflux
    release, calunga-failed-push.log): the push client's idempotency pre-check (a scoped
    content-list) is blinded -> 0 results -> it re-uploads a dep already in `main` ->
    pulp_python's UNSCOPED duplicate guard returns 400 "already exists". 0022 restores the
    role -> the pre-check sees the dep -> the client skips it -> no 400.

  Layer 2 (what this branch does NOT fix):
    (a) an anonymous / role-less caller reading a *scoped* endpoint on the public-* domain
        stays 404 even after 0022 (no org -> no role; needs copr "Fix A" scope_queryset
        public-* bypass, absent here).
    (b) the raw pypi .../simple/ viewset is principal:"*" + reads content UNSCOPED, so RBAC
        never denies it. Its 404s are pulp_python-level (no distribution / no repo version),
        matching the AWS logs -- NOT the RBAC scope-out this branch touches.

Run via `pulpcore-manager shell` (Django ORM for state + `requests` for real HTTP).
"""

import base64, hashlib, importlib, io, json, tarfile, uuid
import requests
from django.apps import apps as A
from django.conf import settings  # noqa: F401
from pulpcore.app.contexts import _current_domain

BASE = "http://localhost:24817"


def hdr(org, user):
    ident = {"identity": {"org_id": org, "internal": {"org_id": org}, "user": {"username": user}}}
    return base64.b64encode(json.dumps(ident).encode()).decode()


def line(phase, msg):
    print(f"[{phase}] {msg}", flush=True)


org = str(uuid.uuid4().int % 90000000 + 10000000)
outsider_org = str(uuid.uuid4().int % 90000000 + 10000000)
owner = hdr(org, f"owner-{uuid.uuid4().hex[:6]}")
member = hdr(org, f"member-{uuid.uuid4().hex[:6]}")
outsider = hdr(outsider_org, f"outsider-{uuid.uuid4().hex[:6]}")
domain_name = f"public-ptlsim-{uuid.uuid4().hex[:8]}"[:24]
team_name = f"ptl-team-{uuid.uuid4().hex[:6]}"
base_path = "main"

Group = A.get_model("core", "Group")
GroupRole = A.get_model("core", "GroupRole")
Domain = A.get_model("core", "Domain")
DomainOrg = A.get_model("service", "DomainOrg")
PythonDistribution = A.get_model("python", "PythonDistribution")
PythonRepository = A.get_model("python", "PythonRepository")
org_group_name = f"rh-org-{org}"
overall_ok = True


def rh_role_count():
    g = Group.objects.filter(name=org_group_name).first()
    return GroupRole.objects.filter(group=g).count() if g else "NO-GROUP"


def GET(path, ident=None):
    url = f"{BASE}{path}" if path.startswith("/") else path
    headers = {"x-rh-identity": ident} if ident else {}
    return requests.get(url, headers=headers, timeout=60)


def check(cond, desc):
    global overall_ok
    if not cond:
        overall_ok = False
    line("CHECK", f"{'PASS' if cond else 'FAIL'}: {desc}")


# ---- Setup: org-owned public-* domain + python repo + distribution ----
r = requests.post(f"{BASE}/api/pulp/create-domain/", headers={"x-rh-identity": owner},
                  json={"name": domain_name, "group_name": team_name}, timeout=60)
assert r.status_code == 201, r.text
line("SETUP", f"created org-owned public domain '{domain_name}' (team '{team_name}', org {org})")

r = requests.post(f"{BASE}/api/pulp/{domain_name}/api/v3/repositories/python/python/",
                  headers={"x-rh-identity": owner, "Content-Type": "application/json"},
                  json={"name": f"repo-{uuid.uuid4().hex[:6]}"}, timeout=60)
assert r.status_code == 201, r.text
repo_href = r.json()["pulp_href"]
content_path = f"/api/pulp/{domain_name}/api/v3/content/python/packages/"

d = Domain.objects.get(name=domain_name)
repo = PythonRepository.objects.get(pulp_domain=d)
dist = PythonDistribution.objects.create(
    name=f"dist-{uuid.uuid4().hex[:6]}", base_path=base_path, pulp_domain=d, repository=repo,
    allow_uploads=True,
)
simple_url = f"/api/pypi/{domain_name}/{base_path}/simple/"
line("SETUP", f"created distribution base_path='{base_path}' -> simple index at {simple_url}")


# ---- Upload helpers: real HTTP twine-style upload + the push client's idempotency pre-check ----
def make_sdist(name, version):
    """Smallest sdist the upload serializer accepts (valid .tar.gz + PKG-INFO)."""
    buf = io.BytesIO()
    body = f"Metadata-Version: 1.0\nName: {name}\nVersion: {version}\n".encode()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo(f"{name}-{version}/PKG-INFO")
        ti.size = len(body)
        tf.addfile(ti, io.BytesIO(body))
    return buf.getvalue()


def upload(filename, blob, ident):
    """A real twine-style POST to the pypi `simple/` upload endpoint (SimpleView.create)."""
    headers = {"x-rh-identity": ident} if ident else {}
    return requests.post(
        f"{BASE}{simple_url}", headers=headers,
        files={"content": (filename, blob, "application/octet-stream")},
        data={":action": "file_upload", "sha256_digest": hashlib.sha256(blob).hexdigest()},
        timeout=120,
    )


def idem_count(ident):
    """The push client's (`pulp-upload`) idempotency pre-check: an RBAC-*scoped* content-list
    filtered by filename. Returns the result count -- 0 means the client concludes 'not present'
    and re-uploads."""
    r = GET(f"{content_path}?filename={dep_file}", ident)
    try:
        return r.json().get("count", -1)
    except Exception:  # noqa: BLE001
        return -1


# Seed one dependency directly into the repo version via the ORM so it is genuinely present --
# the calunga case is re-uploading deps already synced into `main`. (ORM, not an HTTP upload:
# the `simple/` upload runs an async group-upload task; the *duplicate guard* we care about
# fires synchronously in SimpleView.upload BEFORE that task, so seeding via ORM is enough and
# avoids the session-based task machinery.)
dep_pkg, dep_ver = "trusted-dep", "1.0"
dep_file = f"{dep_pkg}-{dep_ver}.tar.gz"
dep_blob = make_sdist(dep_pkg, dep_ver)
PythonPackageContent = A.get_model("python", "PythonPackageContent")
_char_defaults = {c: "" for c in (
    "author", "author_email", "description", "home_page", "keywords", "license",
    "metadata_version", "platform", "summary", "download_url", "maintainer", "maintainer_email",
    "project_url", "requires_python", "description_content_type", "license_expression",
    "python_version",
)}
_tok = _current_domain.set(d)
try:
    _c = PythonPackageContent.objects.create(
        name=dep_pkg, version=dep_ver, filename=dep_file,
        sha256=hashlib.sha256(dep_blob).hexdigest(), packagetype="sdist",
        supported_platform=False, pulp_domain=d, **_char_defaults,
    )
    with repo.new_version() as _nv:
        _nv.add_content(PythonPackageContent.objects.filter(pk=_c.pk))
finally:
    _current_domain.reset(_tok)
assert idem_count(owner) == 1, "seed package never landed in the repo version"
line("SETUP", f"seeded existing dependency '{dep_file}' into the repo (repo version {repo.latest_version().number})")

# ---- Layer 1 reproduce: null org_id + role-less rh-org group + member left only in rh-org ----
org_g = Group.objects.get(name=org_group_name)
team_g = Group.objects.get(name=team_name)
team_g.user_set.add(org_g.user_set.first())  # keep an rh-org member on the team (0022 derive source)
do = DomainOrg.objects.filter(domains__name=domain_name).first()
do.org_id = None
do.save(update_fields=["org_id"])
GroupRole.objects.filter(group=org_g).delete()

check(rh_role_count() == 0, "L1 broken: rh-org group has zero roles")
check(GET(repo_href, member).status_code == 404, "L1 broken: authenticated org member gets 404 on scoped repo read")

# ---- Layer 1 (WRITE path): the Konflux release 400 chain (calunga-failed-push.log) ----
# Same root cause as the read 404s. The push client runs an idempotency pre-check (a *scoped*
# content-list) before each upload; scoped out, it returns 0 -> client believes the dep is
# absent -> re-uploads a dep already in `main` -> pulp_python's UNSCOPED duplicate guard 400s.
check(idem_count(member) == 0,
      "L1(write) broken: push-client idempotency pre-check BLINDED -- scoped content-list for an "
      "existing filename returns 0 for the role-less member -> client would re-upload")
r_dup = upload(dep_file, dep_blob, owner)  # owner still holds the role -> reaches the dup guard
check(r_dup.status_code == 400 and "already exists" in (r_dup.reason or ""),
      f"L1(write): re-upload of an existing filename -> {r_dup.status_code} {r_dup.reason!r} "
      "(pulp_python UNSCOPED duplicate guard, pypi/views.py:219) = the 400 in calunga-failed-push.log")

# ---- Layer 1 fix: migration 0022 backfill ----
mod = importlib.import_module("pulp_service.app.migrations.0022_backfill_org_group_roles")
mod.backfill_org_group_roles(A, None)
do.refresh_from_db()
check(str(do.org_id) == org, "L1 fix: 0022 re-derived and stored org_id from team members")
check(isinstance(rh_role_count(), int) and rh_role_count() > 0, "L1 fix: 0022 granted roles to rh-org group")
check(GET(repo_href, member).status_code == 200, "L1 fix: org member reads scoped repo -> 200")
check(GET(content_path, member).status_code == 200, "L1 fix: org member lists scoped content -> 200")
check(GET(repo_href, outsider).status_code in (403, 404), "L1 fix: isolation intact -- unrelated org scoped out")
check(idem_count(member) == 1,
      "L1(write) fix: 0022 restores the rh-org role -> idempotency pre-check now SEES the package "
      "(count=1) -> client skips the re-upload -> no 400")

# ---- Layer 2a: anonymous scoped read stays 404 even AFTER 0022 (branch does NOT fix) ----
check(GET(repo_href, None).status_code == 404,
      "L2a: anonymous scoped repo read stays 404 after 0022 (needs copr 'Fix A', absent here)")

# ---- Layer 2b: raw simple/ path is *-allow + unscoped -> not an RBAC denial ----
# NOTE ON THE REAL CALLER: in production these simple/ GETs come from `fromager` during Konflux
# wheel builds (--cache-wheel-server-url), authenticated with the calunga-internal service account
# over HTTP Basic auth (no x-rh-identity -> logged org_id=-), or anonymous when creds are unset.
# SimpleView is principal:"*" + reads content UNSCOPED, so the auth mode does NOT change the
# outcome -- we exercise it anonymously here; a Basic-auth SA sees identical status codes.
r_simple = GET(simple_url, None)
check(r_simple.status_code in (200, 301, 302),
      f"L2b: GET simple/ index (dist present) is NOT a permission denial -> {r_simple.status_code} (has_permission passed, *-allow)")

# The EXACT production request shape: GET .../main/simple/<pkg>/ for a package not (yet) in the
# index. This is fromager's build-cache check -- a 404 here is the normal "not built, build from
# source" signal, i.e. most of the 464 logged 404s are benign cache misses, not a service defect.
r_pkg_absent = GET(f"/api/pypi/{domain_name}/{base_path}/simple/some-unbuilt-pkg-{uuid.uuid4().hex[:6]}/", None)
check(r_pkg_absent.status_code == 404,
      f"L2b: GET simple/<pkg>/ (dist present, pkg absent) -> {r_pkg_absent.status_code} = fromager cache-miss (benign), pulp_python-level not RBAC")

# A base_path with NO distribution reproduces the AWS 404 as a pulp_python-level cause.
# (Use a never-cached base_path rather than deleting `dist`: SimpleView.list is wrapped in
#  @PythonApiCache, so deleting the dist would still serve a stale cached 200 for its path.)
missing_url = f"/api/pypi/{domain_name}/no-such-index-{uuid.uuid4().hex[:6]}/simple/"
r_missing = GET(missing_url, None)
sig = ""
try:
    sig = r_missing.json().get("detail", "")
except Exception:
    sig = r_missing.text[:120]
check(r_missing.status_code == 404,
      f"L2b: anonymous GET simple/ (no distribution) -> 404, pulp_python-level (detail={sig!r})")

# The AWS logs also show doubled `simple/simple/<pkg>/` -- a client base-URL misconfig; also 404.
dbl_url = f"/api/pypi/{domain_name}/{base_path}/simple/simple/"
r_dbl = GET(dbl_url, None)
check(r_dbl.status_code == 404,
      f"L2b: anonymous GET doubled simple/simple/ (client misconfig, per AWS logs) -> {r_dbl.status_code}")

# ---- Cleanup (throwaway dev DB) ----
try:
    PythonDistribution.objects.filter(pulp_domain=d).delete()
    PythonRepository.objects.filter(pulp_domain=d).delete()
    d.delete()
    team_g.delete()
    org_g.delete()
    Group.objects.filter(name=f"rh-org-{outsider_org}").delete()
except Exception as e:  # noqa: BLE001
    line("CLEANUP", f"partial cleanup ({e!r}) -- harmless for a throwaway dev DB")

print("\n=== SIMULATION RESULT:", "ALL CHECKS PASSED" if overall_ok else "FAILURES PRESENT", "===", flush=True)
```

## How to run

```bash
podman cp pub_trusted_sim.py pulp-dev:/tmp/pub_trusted_sim.py
podman exec pulp-dev bash -lc \
  "runuser -u pulp -- pulpcore-manager shell -c \"exec(open('/tmp/pub_trusted_sim.py').read())\""
```

Expected tail: `=== SIMULATION RESULT: ALL CHECKS PASSED ===`.

Notes:
- `SimpleView.list` is wrapped in `@PythonApiCache`, so a base_path whose index was already fetched
  serves a cached response. The L2b "no distribution" check uses a fresh, never-cached base_path so
  the 404 reflects live resolution, not stale cache.
- Domain names MUST start with `public-` (the scenario is a `public-*` domain); the sim uses a
  random `public-ptlsim-<hex>` per run and cleans up after itself.
