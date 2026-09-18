# public-trusted-libraries 404 — Simulation Results & Verdict

Companion to `simulation-procedure-public-trusted-libraries.md`. Run against `pulp-dev` staged as
**feat/pulp-2120 RBAC + branch `fix/backfill-domainorg-org-id` fix** (see "Environment" below).

## TL;DR verdict

This branch **fixes the authenticated org-member scoped-read 404** on a `public-*` domain — the
Layer-1 calunga shape (`DomainOrg.org_id` NULL → role-less `rh-org` group → org member scoped out)
— via migration `0022_backfill_org_group_roles` (backfill + re-derive `org_id`) and
`signals._derive_org_id_from_user` (create-time fallback). Simulated: 404 → **200**.

It also **fixes the failed Konflux release** (`calunga-failed-push.log`: twine `POST
.../main/simple/` → `400 Bad Request` for 23 of 26 files, SA `15322645|calunga-internal`). That
400 is the **same Layer-1 scope-out on the write path**: the push client
(`~/work/rhtl/plumbing/utils/scripts/pulp-upload`) runs an **idempotency pre-check** — a *scoped*
content-list filtered by filename — before each upload; scoped out, it returns **0 results**, so
the client re-uploads a dependency already in `main`, and pulp_python's **unscoped** duplicate
guard (`pypi/views.py:219`) rejects it `400 "Package … already exists in index"`. Only the
already-present deps 400; the genuinely-new release package uploads fine (200). Migration `0022`
restores the `rh-org` role → the pre-check sees the dep → the client skips it → no 400. The 400 is
correct duplicate detection; RBAC is what *hid the duplicates from the client's own check*.
Simulated: pre-check `0` → **`1`** across `0022`. (This is distinct from the `simple/` **GET**
404s below, which are `fromager` cache-checks, not RBAC.)

It does **NOT** fix the actual production `public-trusted-libraries` 404s, which are the
`GET /api/pypi/public-trusted-libraries/main/simple/<pkg>/` requests in
`public-trusted-libraries-404s.md`. **These are not anonymous** (see "How Calunga issues these
requests" below): they are **`fromager` cache-checks during Konflux wheel builds**, authenticated
with the `calunga-internal` service account (org `15322645`) over **HTTP Basic auth** — which
carries no `internal.org_id` identity header, so the access log records `org_id = -`. Those 404s
split into two causes, neither closed by this branch:

- **(a) Anonymous / role-less scoped reads** stay **404 even after `0022`**. An anonymous caller
  has no org and no role, so role-based `scope_queryset` returns empty. Closing this needs the
  copr **"Fix A"** (`scope_queryset` `public-*` bypass for non-`Domain` models,
  `feat/rbac-orphaned-content-access`) — **absent from this branch.**
- **(b) The literal `pypi/.../simple/` path** is served by pulp_python's `SimpleView`
  (`principal:"*"`, reads content **unscoped**). RBAC never gates it — authenticated or not. Its
  404s are **pulp_python-level**: package not in the index, no distribution for the base_path, no
  repository version, or the doubled `simple/simple/` client misconfig seen in the logs. **Not**
  an RBAC scope-out. **Confirmed:** with a distribution present but the package absent,
  `GET .../main/simple/<pkg>/` returns 404 while `GET .../main/simple/` (the index) returns 200 —
  i.e. a per-package 404 is `fromager`'s normal **cache-miss** signal ("not built yet → build from
  source"), so a large share of the 464 logged 404s are likely **benign build-cache misses**, not
  a service defect.

**More changes are needed** to close the RBAC gaps, but the logged `simple/` 404s are largely a
`fromager` cache-check artifact + data/config, not something an RBAC change fixes. This branch is
one necessary RBAC piece (authenticated org members via the identity header), not the whole
picture.

## How Calunga issues these requests (confirmed from `~/work/rhtl/`)

Traced the client repos (`index`, `plumbing`, `wheel-patcher`, `check-source-origin`,
`ui-packages.redhat.com`):

- **Who / when:** the `simple/<pkg>/` GETs come from **`fromager`** during Konflux wheel builds
  (`plumbing/builder/scripts/build-wheels` → `fromager bootstrap` / `build-sequence
  --cache-wheel-server-url <pulp simple url>`). Before building a package from source, fromager
  checks the Pulp simple index for an already-built (cached) wheel. A 404 there just means "not
  cached — build it." Base URL is hardcoded at
  `index/.tekton/build-pipeline.yaml` = `https://packages.redhat.com/api/pypi/public-trusted-libraries/main/simple/`.
- **Auth:** HTTP **Basic auth** via `~/.netrc` (`machine packages.redhat.com login
  $SERVICE_ACCOUNT_USERNAME password $SERVICE_ACCOUNT_PASSWORD`), written only when both creds are
  set — otherwise the request is genuinely anonymous. The SA is `15322645|calunga-internal`
  (`~/work/rhtl/service-acc-name.txt`). **No `x-rh-identity`** on any client path → the log's
  `org_id = -`. This is exactly the calunga org whose `DomainOrg.org_id` was NULL: the SA
  authenticates and is auto-added to `rh-org-15322645` only.
- **The doubled `simple/simple/`:** **not** produced by any code in these repos. The configured
  base URL already ends in `/simple/`, and fromager/`pypi_simple` append only `<pkg>/` (no second
  `simple/`). The double most likely comes from external/manual use (a tool pointed at the
  already-`/simple/`-terminated URL as an index *root*) or a distribution `base_path` — needs
  investigation outside the client repos.
- **Distribution creation:** **not** in any client repo. Onboarding only writes JSON under
  `index/onboarded_packages/`; upload/read paths assume a pre-existing repo/distribution and fail
  if absent. So a missing/misconfigured `public-trusted-libraries/main` distribution would be a
  Pulp-service / admin-side concern, not fixable from these repos.

**Bearing on RBAC:** because these are Basic-auth SA (or anonymous) requests to the `*`-allow,
unscoped `SimpleView`, neither this branch, nor the copr "Fix A" (`feat/rbac-orphaned-content-access`),
nor the 2120 backend flip changes their outcome. The RBAC scope-out this branch fixes only bites
requests that (a) hit a **role-scoped** endpoint (repo-by-href / content list) and (b) carry the
`x-rh-identity` header — i.e. console/API traffic, not fromager's `simple/` build-cache checks.

## Environment

Branch `fix/backfill-domainorg-org-id` diverges from `feat/pulp-2120`, so the sim was run with
2120's real RBAC code in place and this branch's fix layered on:

| Component | Source | Effect |
|-----------|--------|--------|
| `access_policy.py` | feat/pulp-2120 | `PulpServiceAccessPolicy(AccessPolicyFromDB)` — DB-backed RBAC |
| `authorization.py` | feat/pulp-2120 | adds `set_domain_create_context()` |
| `viewsets.py` | feat/pulp-2120 | `IsAuthenticated` + `set_domain_create_context` on Create/MigrateDomainView; drops `DomainBasedPermission`; explicit migrate object-perm check |
| `settings.py` | feat/pulp-2120 | drops the `ACCESS_POLICIES`/`_CONTENT_LIST_POLICY` block (inert under `AccessPolicyFromDB`) |
| `content.py` | **this branch** | kept — its only delta to 2120 is OTEL active-connections tracking (not RBAC) |
| `signals.py` | **this branch** | `_derive_org_id_from_user` create-time `org_id` fallback |
| `migrations/0022_*` | **this branch** | `backfill_org_group_roles` (backfill + derive) |

The four RBAC files were applied to the **branch working tree** (uncommitted — to be committed
manually) and synced into the container; `content.py` was kept as this branch's version in both.

```
$ grep -n 'AccessPolicyFrom' pulp_service/pulp_service/app/access_policy.py
11:from pulpcore.app.access_policy import AccessPolicyFromDB
21:class PulpServiceAccessPolicy(AccessPolicyFromDB):
$ showmigrations service | tail -1
 [X] 0022_backfill_org_group_roles
```

## Observed transcript

```
[SETUP] created org-owned public domain 'public-ptlsim-a0b60c4d' (team 'ptl-team-2aa3e5', org 25905154)
[SETUP] created distribution base_path='main' -> simple index at /api/pypi/public-ptlsim-a0b60c4d/main/simple/
[SETUP] seeded existing dependency 'trusted-dep-1.0.tar.gz' into the repo (repo version 1)
[CHECK] PASS: L1 broken: rh-org group has zero roles
[CHECK] PASS: L1 broken: authenticated org member gets 404 on scoped repo read
[CHECK] PASS: L1(write) broken: push-client idempotency pre-check BLINDED -- scoped content-list for an existing filename returns 0 for the role-less member -> client would re-upload
[CHECK] PASS: L1(write): re-upload of an existing filename -> 400 'Package trusted-dep-1.0.tar.gz already exists in index' (pulp_python UNSCOPED duplicate guard, pypi/views.py:219) = the 400 in calunga-failed-push.log
pulp_service.app.migrations.0022_backfill_org_group_roles:INFO: org-group role backfill: repaired=11 skipped=1
[CHECK] PASS: L1 fix: 0022 re-derived and stored org_id from team members
[CHECK] PASS: L1 fix: 0022 granted roles to rh-org group
[CHECK] PASS: L1 fix: org member reads scoped repo -> 200
[CHECK] PASS: L1 fix: org member lists scoped content -> 200
[CHECK] PASS: L1 fix: isolation intact -- unrelated org scoped out
[CHECK] PASS: L1(write) fix: 0022 restores the rh-org role -> idempotency pre-check now SEES the package (count=1) -> client skips the re-upload -> no 400
[CHECK] PASS: L2a: anonymous scoped repo read stays 404 after 0022 (needs copr 'Fix A', absent here)
[CHECK] PASS: L2b: GET simple/ index (dist present) is NOT a permission denial -> 200 (has_permission passed, *-allow)
[CHECK] PASS: L2b: GET simple/<pkg>/ (dist present, pkg absent) -> 404 = fromager cache-miss (benign), pulp_python-level not RBAC
[CHECK] PASS: L2b: anonymous GET simple/ (no distribution) -> 404, pulp_python-level (detail='404 Not Found')
[CHECK] PASS: L2b: anonymous GET doubled simple/simple/ (client misconfig, per AWS logs) -> 404

=== SIMULATION RESULT: ALL CHECKS PASSED ===
```

Every `check` in the sim is an assertion about *observed* behavior; "ALL CHECKS PASSED" means the
system behaved exactly as the per-layer analysis below predicts — including the checks that
confirm the branch does **not** fix Layer 2.

## Per-layer analysis

| Caller | Endpoint | Broken state | After `0022` | Mechanism |
|--------|----------|--------------|--------------|-----------|
| superuser | anything | 200 | 200 | `has_permission` superuser bypass (`access_policy.py:40`) |
| **org member** (`rh-org` only) | scoped repo-by-href | **404** | **200** | role-based `scope_queryset`; `0022` grants `rh-org` group its role → in scope |
| org member (`rh-org` only) | scoped content list | empty/404 | **200** | same — role restored by `0022` |
| **SA / member** (`rh-org` only) | idempotency pre-check (scoped content-list by filename) | **0 results (blinded)** | **1 result** | client believes dep absent → re-uploads; `0022` restores role → pre-check sees it → client skips. **This branch fixes the failed release.** |
| uploader holding the role | re-upload of an existing filename (`POST .../simple/`) | **400 "already exists"** | 400 (but client no longer re-uploads) | pulp_python **unscoped** dup guard (`pypi/views.py:219`) — correct behavior; branch removes the *reason* the client re-uploads |
| unrelated org | scoped repo-by-href | 403/404 | 403/404 | no matching role — isolation intact (correct) |
| **anonymous** (`org_id -`) | scoped repo-by-href | 404 | **still 404** | `public-*` `has_permission` bypass passes, but `scope_queryset` for a role-less user returns empty → 404. **Needs copr "Fix A" (absent).** |
| fromager SA / anon (`org_id -`) | `pypi/.../simple/` (dist + pkg present) | 200 | 200 | `SimpleView.DEFAULT_ACCESS_POLICY` allows `list`/`retrieve` for `principal:"*"`; `get_content` reads `PythonPackageContent` **unscoped**. RBAC not the gate. |
| fromager SA / anon (`org_id -`) | `pypi/.../simple/<pkg>/` (pkg absent) | 404 | 404 | package not in index → 404 = fromager's normal **cache-miss** ("build from source"). pulp_python-level, **not RBAC**. |
| fromager SA / anon (`org_id -`) | `pypi/.../simple/` (no dist / doubled path) | 404 | 404 | pulp_python `PyPIMixin.get_distribution` → `Http404("No PythonDistribution found …")`. **pulp_python-level, not RBAC.** |

The production log rows are these last three: `fromager` build-cache `simple/` checks (Basic-auth
SA `15322645|calunga-internal`, or anonymous; `org_id -` because Basic auth carries no identity
header), including the doubled `.../main/simple/simple/<pkg>/`. This branch — and any RBAC change —
changes none of them.

## What must change for a full `public-trusted-libraries` fix

1. **Have (this branch):** `0022` backfill + `signals` derive — fixes authenticated org-member
   scoped reads on `public-*` domains (Layer 1). Necessary, not sufficient.
2. **Need:** copr **"Fix A"** — a `scope_queryset` `public-*` bypass for non-`Domain` models
   (repos/content), so anonymous/role-less callers can read a `public-*` domain's scoped
   endpoints (Layer 2a). Lives on `feat/rbac-orphaned-content-access`; not on this branch. See
   `simulation-procedure-copr-public-domain.md`.
3. **Investigate separately (not RBAC):** the `pypi/.../simple/` 404s (Layer 2b). These are
   `fromager` build-cache checks (Basic-auth SA / anonymous) against the `*`-allow, unscoped
   `SimpleView`. Steps: (a) recognize that a per-package 404 is fromager's normal cache-miss and
   likely benign — quantify how many of the 464 are for not-yet-built packages vs. a real outage;
   (b) confirm the `public-trusted-libraries/main` `PythonDistribution` exists and is bound to a
   repo/publication (creation is Pulp-service/admin side, not in the client repos); (c) trace the
   source of the doubled `.../simple/simple/` URLs (not generated by the client repos — likely
   external/manual use or a distribution `base_path`). No RBAC change addresses any of these.

## Branch-delta note

The sim was deliberately run with `feat/pulp-2120`'s RBAC code (`AccessPolicyFromDB`;
`set_domain_create_context` replacing `DomainBasedPermission` on Create/MigrateDomainView) so the
result reflects where this fix will actually land, not this branch's interim
`AccessPolicyFromSettings` state. Those four RBAC files now sit in the working tree as uncommitted
changes for a manual commit; `signals.py` + `0022` are this branch's fix on top.

Sibling scenarios: `simulation-results.md` (PULP-2120/calunga authenticated path),
`simulation-results-copr-public-domain.md` (copr `public-*` "Fix A").
