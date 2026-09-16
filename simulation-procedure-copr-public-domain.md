# COPR public-* domain read failure — Simulation Procedure (re-runnable)

## TLDR

- **Bug:** a role-less copr backend uploads an RPM into the `public-copr-stage`
  (`public-*`) domain, then GET-by-href returns `404` instead of `200`, crashing the copr build.
- **Cause:** the `public-*` read bypass lived in `has_permission` but not in `scope_queryset`,
  so the package was filtered out of the scoped queryset and `get_object_or_404` raised `404`.
- **Fix:** `scope_queryset` now short-circuits to the domain-filtered queryset for safe reads on
  `public-*` domains (Fix A); orphan content for a domain's own members is gated on
  `core.view_content` (Fix B).
- **Verdict:** fix confirmed — 31 branch tests pass and the end-to-end sim shows the read go
  `404 -> 200` while a non-public control domain still returns `404` (no blanket grant, no leak).

How to reproduce the **copr-stage** RBAC failure and verify the fix on
`feat/rbac-orphaned-content-access`.

This is a *different* scenario from `simulation-procedure.md` (that one covers PULP-2120 /
calunga: a `DomainOrg.org_id=NULL` org-owned domain repaired by migration `0022`). This one
covers a **`public-*` domain** read regression, which is what copr-stage hit.

## The reported shape (copr-stage)

The copr backend uploads an RPM into `public-copr-stage` and then reads it back:

```
POST /api/pulp/public-copr-stage/api/v3/content/rpm/packages/upload/            -> 201
GET  /api/pulp/public-copr-stage/api/v3/content/rpm/packages/<uuid>/            -> 404   <-- bug
```

The `404` crashes the copr build inside `add_content -> try_lock -> _unique_nevras`, which
does `get_by_href(<package>)` to dedupe NEVRAs (see the client traceback). The copr backend's
only group `rh-org-12492573` holds **0 roles**.

### Why it happens

`public-copr-stage` is a `public-*` domain (world-readable on safe methods).
`PulpServiceAccessPolicy.has_permission` **allowed** the read (public-* bypass), but pre-fix
`scope_queryset` did **not** carry that bypass through, so the role-less caller passed the
permission check and then had the package filtered out of the scoped queryset —
`get_object_or_404` raised **404**. This is **Fix A** on the branch: `scope_queryset` now
short-circuits to the domain-filtered queryset for safe-method reads on a `public-*` domain
(`base.py` has already scoped to `request.pulp_domain`, so no cross-domain leak).

The uploaded package is also **orphan content** (uploaded, not yet in a repository) — the
branch's **Fix B** (`content` list gated on `core.view_content` instead of repository scoping)
covers orphan visibility for a domain's *own members*. For the copr case the domain is
`public-*`, so **Fix A** is what carries the read; Fix B is verified by its own test.

## Prerequisites

- A running dev container with the branch code and RBAC enabled. Here it was `pulp-dev`
  (`pulp-service-dev:latest`), API on `http://localhost:24817`, paths under `/api/pulp/<domain>/...`.
- `pulp_rpm` installed (it is in the dev image). Confirm state:

```bash
podman exec pulp-dev bash -lc 'runuser -u pulp -- pulpcore-manager shell -c \
  "from django.conf import settings; print(settings.REST_FRAMEWORK[\"DEFAULT_PERMISSION_CLASSES\"])"'
# -> ['pulp_service.app.access_policy.PulpServiceAccessPolicy']
```

- **Branch fidelity port** (so the RBAC surface matches production #1452 / `feat/pulp-2120`):
  `CreateDomainView`/`MigrateDomainView`/`PyPIYankMonitorViewSet` switched off
  `DomainBasedPermission`, plus `set_domain_create_context` in `authorization.py`. See the
  "Branch fidelity" section of `simulation-results-copr-public-domain.md`. If the running
  container predates this port, copy the edited `authorization.py`/`viewsets.py` into
  `/plugins/pulp_service/pulp_service/app/` and restart the api service
  (`s6-svc -r /run/service/pulpcore-api`).

- A real RPM to upload (any small RPM works; the read bug is content-type-agnostic):

```bash
podman exec pulp-dev bash -lc \
  'curl -sS -o /tmp/test.rpm https://fixtures.pulpproject.org/rpm-unsigned/bear-4.1-1.noarch.rpm'
```

## Artifact 1 — branch tests (durable)

The branch ships two regression tests that encode the pre-fix `404` and the fix. The dev
container's default bindings host is `pulp:443` (CI); point them at the local API:

```bash
podman exec pulp-dev bash -lc 'runuser -u pulp -- bash -lc \
  "cd /data && API_PROTOCOL=http API_HOST=localhost API_PORT=24817 python -m pytest \
   tests/functional/test_public_domain_read.py \
   tests/functional/test_content_view_after_upload.py \
   tests/functional/test_access_policy.py -q"'
# -> 2 + 3 + 26 = 31 passed
```

- `test_public_domain_read.py` — Fix A: role-less caller reads a `public-*` domain's repos (`404 -> 200`).
- `test_content_view_after_upload.py` — Fix B: a domain member sees its own orphan content.
- `test_access_policy.py` — unit coverage of `_is_public_domain_read` / `scope_queryset`.

## Artifact 2 — ad-hoc end-to-end simulation

Reproduces the copr shape over real HTTP: a `public-copr-*` domain with an **orphan RPM**, a
**role-less** caller (0-role `rh-org-<org>`), and a **non-public control** domain with the same
shape that must stay `404` — proving the restored `200` is specifically the `public-*` bypass
(not a blanket grant or a cross-domain leak). Save as `copr_sim.py`, copy in, run via
`pulpcore-manager shell`:

```bash
podman cp copr_sim.py pulp-dev:/tmp/copr_sim.py
podman exec pulp-dev bash -lc \
  "runuser -u pulp -- pulpcore-manager shell -c \"exec(open('/tmp/copr_sim.py').read())\""
```

The script (`/tmp/copr_sim.py`):

```python
"""COPR public-* domain RBAC read failure simulation (feat/rbac-orphaned-content-access).

Reproduces the reported copr-stage shape:
  - a public-* domain (public-copr-stage) with orphan RPM content (uploaded, not yet in a repo),
  - a caller whose only group is a zero-role rh-org-<org_id> (the copr backend service account),
  - upload -> 201, then GET package-by-href -> 404 (pre-fix).

Proves the branch's Fix A (public-* bypass carried into scope_queryset) turns that GET into 200,
using a NON-public control domain (same caller, same orphan-RPM shape) that must still 404 -- so
the 200 is specifically the public-* bypass, not a blanket grant or a cross-domain leak.

Run via `pulpcore-manager shell` (Django ORM for state assertions + requests for real HTTP).
"""

import base64, json, uuid
import requests
from django.apps import apps as A

BASE = "http://localhost:24817"
ADMIN = ("admin", "password")
RPM = "/tmp/test.rpm"

overall_ok = True


def hdr(org, user):
    ident = {"identity": {"org_id": org, "internal": {"org_id": org}, "user": {"username": user}}}
    return base64.b64encode(json.dumps(ident).encode()).decode()


def line(phase, msg):
    print(f"[{phase}] {msg}", flush=True)


def check(cond, desc):
    global overall_ok
    if not cond:
        overall_ok = False
    line("CHECK", f"{'PASS' if cond else 'FAIL'}: {desc}")


def create_domain(name, owner_ident):
    r = requests.post(f"{BASE}/api/pulp/create-domain/", headers={"x-rh-identity": owner_ident},
                      json={"name": name}, timeout=90)
    assert r.status_code == 201, f"create-domain {name}: {r.status_code} {r.text}"
    return r.json()["pulp_href"]


def upload_rpm(domain, auth=None, ident=None):
    """One-shot RPM upload (the copr flow). Returns (status_code, json_or_text)."""
    url = f"{BASE}/api/pulp/{domain}/api/v3/content/rpm/packages/upload/"
    kw = {"timeout": 120}
    if auth:
        kw["auth"] = auth
    if ident:
        kw["headers"] = {"x-rh-identity": ident}
    with open(RPM, "rb") as fh:
        r = requests.post(url, files={"file": ("bear-4.1-1.noarch.rpm", fh, "application/octet-stream")}, **kw)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def GET(path, ident):
    url = f"{BASE}{path}" if path.startswith("/") else path
    return requests.get(url, headers={"x-rh-identity": ident}, timeout=90)


# --- actors ------------------------------------------------------------------
owner_org = str(uuid.uuid4().int % 90000000 + 10000000)
caller_org = str(uuid.uuid4().int % 90000000 + 10000000)  # the copr-backend-shaped role-less org
owner = hdr(owner_org, f"owner-{uuid.uuid4().hex[:6]}")
caller = hdr(caller_org, f"copr-backend-{uuid.uuid4().hex[:6]}")

public_name = f"public-copr-{uuid.uuid4().hex[:8]}"   # public-* -> world-readable on safe methods
control_name = f"coprctl-{uuid.uuid4().hex[:8]}"       # NON-public control

Group = A.get_model("core", "Group")
GroupRole = A.get_model("core", "GroupRole")
Domain = A.get_model("core", "Domain")
DomainOrg = A.get_model("service", "DomainOrg")

# --- setup -------------------------------------------------------------------
public_href = create_domain(public_name, owner)
control_href = create_domain(control_name, owner)
line("SETUP", f"created public domain '{public_name}' and control domain '{control_name}' (owner org {owner_org})")

# --- ported dual-write (feat/pulp-2120 set_domain_create_context) -------------
# create-domain now runs through IsAuthenticated + set_domain_create_context (not the old
# DomainBasedPermission). Prove that path still populates org_id_var/user_id_var: the
# post_create_domain signal must (a) stamp DomainOrg.org_id from the X-RH-IDENTITY header and
# (b) grant the owner's rh-org-<org> group domain roles (the `if org_id:` branch). If
# set_domain_create_context were a no-op, org_id would be None and both would fail.
pub_domain_obj = Domain.objects.get(name=public_name)
owner_group = Group.objects.filter(name=f"rh-org-{owner_org}").first()
do_row = DomainOrg.objects.filter(domains__name=public_name).first()
check(do_row is not None and str(do_row.org_id) == owner_org,
      f"dual-write: DomainOrg.org_id stamped from X-RH-IDENTITY == {owner_org} "
      f"(proves set_domain_create_context ran); got {getattr(do_row, 'org_id', None)!r}")
owner_org_roles = GroupRole.objects.filter(group=owner_group, domain=pub_domain_obj).count() if owner_group else "NO-GROUP"
check(isinstance(owner_org_roles, int) and owner_org_roles > 0,
      f"dual-write: rh-org-{owner_org} group granted domain roles on create; got {owner_org_roles}")

# Upload orphan RPM into each domain as the admin superuser (reliable content creation).
sc, body = upload_rpm(public_name, auth=ADMIN)
assert sc == 201, f"admin upload to public domain: {sc} {body}"
pub_pkg_href = body["pulp_href"]
sc, body = upload_rpm(control_name, auth=ADMIN)
assert sc == 201, f"admin upload to control domain: {sc} {body}"
ctl_pkg_href = body["pulp_href"]
line("SETUP", f"uploaded orphan RPM -> public pkg {pub_pkg_href}")
line("SETUP", f"uploaded orphan RPM -> control pkg {ctl_pkg_href}")

# Confirm the uploaded package is orphan content (not in any repository version).
RepositoryContent = A.get_model("core", "RepositoryContent")
pub_pkg_pk = pub_pkg_href.rstrip("/").split("/")[-1]
in_repo = RepositoryContent.objects.filter(content_id=pub_pkg_pk).exists()
check(not in_repo, "uploaded RPM is orphan content (not a member of any repository version)")

# --- caller shape ------------------------------------------------------------
# Touch the API once so JSONHeaderRemoteAuthentication auto-creates the user + rh-org group.
GET(f"/api/pulp/{public_name}/api/v3/repositories/rpm/rpm/", caller)
caller_group = Group.objects.filter(name=f"rh-org-{caller_org}").first()
caller_roles = GroupRole.objects.filter(group=caller_group).count() if caller_group else "NO-GROUP"
check(caller_roles == 0, f"caller's only group rh-org-{caller_org} has zero roles (copr shape); got {caller_roles}")

# --- reproduce / verify fix --------------------------------------------------
r = GET(pub_pkg_href, caller)
line("REPORT", f"public-* GET package-by-href -> {r.status_code} (client saw 404 pre-fix)")
check(r.status_code == 200, "FIX A: role-less caller GETs orphan RPM in public-* domain -> 200")

ctl_pkg_path = f"/api/pulp/{control_name}/api/v3/content/rpm/packages/{ctl_pkg_href.rstrip('/').split('/')[-1]}/"
r = GET(ctl_pkg_path, caller)
line("CONTROL", f"non-public GET package-by-href -> {r.status_code} (must stay 404: no access, no leak)")
check(r.status_code == 404, "CONTROL: same role-less caller in NON-public domain -> 404 (pre-fix symptom / bypass is the differentiator)")

r = GET(f"/api/pulp/{public_name}/api/v3/content/rpm/packages/", caller)
hrefs = [x["pulp_href"] for x in r.json().get("results", [])] if r.status_code == 200 else []
check(r.status_code == 200 and pub_pkg_href in hrefs, "FIX A: role-less caller lists orphan RPM in public-* domain (200, package present)")

# --- probe: does the role-less caller's own upload 201 like the client saw? ---
sc, body = upload_rpm(public_name, ident=caller)
line("PROBE", f"role-less caller RPM upload to public-* domain -> {sc} "
      f"(client reported 201; upload gate is independent of the read bug)")

# --- isolation: public bypass must not expose a non-public domain ------------
r = GET(control_href, caller)  # GET the control Domain object itself
line("ISOLATION", f"role-less caller GET control domain object -> {r.status_code}")

# --- cleanup -----------------------------------------------------------------
for href in (public_href, control_href):
    try:
        requests.delete(f"{BASE}{href}", auth=ADMIN, timeout=90)
    except Exception as e:  # noqa: BLE001
        line("CLEANUP", f"partial cleanup ({e!r}) -- harmless for a throwaway dev DB")
try:
    Group.objects.filter(name=f"rh-org-{caller_org}").delete()
    Group.objects.filter(name=f"rh-org-{owner_org}").delete()
except Exception as e:  # noqa: BLE001
    line("CLEANUP", f"group cleanup ({e!r})")

print("\n=== SIMULATION RESULT:", "ALL CHECKS PASSED" if overall_ok else "FAILURES PRESENT", "===", flush=True)
```

Expected tail: `=== SIMULATION RESULT: ALL CHECKS PASSED ===`.

## Notes / fidelity

- The dev image has no local RPM builder, so the RPM is pulled from the pulp fixtures server.
  The read bug is content-type-agnostic (Fix A lives in the generic access-policy
  `scope_queryset`, inherited by every typed content viewset), so any content type reproduces
  it; RPM is used here to match the copr report exactly.
- The simulation uploads the orphan RPM as the **admin superuser** for reliable content
  creation, then performs the failing read as the **role-less caller** — that read is the bug.
  The `PROBE` step separately shows a purely role-less caller's *upload* is `403` in this env
  (see the results file for what that means for the client's `201`).
