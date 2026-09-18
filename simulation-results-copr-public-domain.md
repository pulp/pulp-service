# COPR public-* domain read failure — Simulation Results: does the branch fix it?

**Branch:** `feat/rbac-orphaned-content-access`
**Date:** 2026-09-16
**Environment:** dev container `pulp-dev` (`pulp-service-dev:latest`), RBAC active
(`DEFAULT_PERMISSION_CLASSES = ['pulp_service.app.access_policy.PulpServiceAccessPolicy']`),
`pulp_rpm` installed, API on `http://localhost:24817`.

## TL;DR

**Yes — this branch fixes the reported copr-stage `404`.** The failure was a read regression on
a `public-*` domain: `has_permission` allowed the role-less copr backend to read
`public-copr-stage`, but pre-fix `scope_queryset` filtered the package back out, so
`get_object_or_404` raised `404` — exactly where the copr build crashed
(`add_content -> try_lock -> _unique_nevras -> get_by_href`). The branch's **Fix A** carries the
`public-*` bypass into `scope_queryset`, so the read resolves to `200`.

I reproduced the copr shape over real HTTP (`public-copr-*` domain, orphan RPM, role-less
`rh-org-<org>` caller) and confirmed `GET package-by-href -> 200`, while a **non-public control
domain with the same shape stays `404`** — proving the `200` is specifically the `public-*`
bypass and not a blanket grant or a cross-domain leak.

> Note: RBAC is **already reverted in production** — this is a post-mortem verifying the
> re-enable branch, not a live bug.

## One caveat about the client's `201` upload

The client reported the copr backend **uploads fine (`201`)** and only the subsequent **`GET`
fails (`404`)**. In my simulation a *purely* role-less caller's own upload returns **`403`**, not
`201`. That difference is informative, not contradictory: it means the client's copr account has
**write permission on the domain via some path my synthetic 0-role caller lacks** (a direct user
role, a token principal, or a group other than the `rh-org-12492573` they inspected). That
account can *write* but was blocked on *read* — which is precisely the bug this branch fixes.
The read fix does **not** depend on how upload is authorized; the two gates are independent.

**Implication for the fix verdict:** because the client's account already writes successfully
(`201` upload, and by extension the `add_content` repo-modify that follows the failing `GET`),
the *only* thing blocking the copr build was the `GET -> 404`. Fix A resolves that. **No further
changes are needed for the copr read path.**

## Symptom → check mapping

| Reported symptom (copr-stage) | How reproduced | Result on branch |
|---|---|---|
| `GET .../content/rpm/packages/<uuid>/` in `public-copr-stage` → **404** | role-less caller GET orphan RPM by href in a `public-*` domain | **200** ✓ |
| copr build crash in `_unique_nevras` (the `get_by_href` above) | same GET | resolves ✓ |
| Content list not visible | role-less caller LIST content in `public-*` domain | **200**, package present ✓ |
| (control) no-access read must stay blocked | same caller, **non-public** domain, same orphan RPM | **404** ✓ |
| (isolation) public bypass must not expose non-public data | role-less caller GET the control Domain object | **404** ✓ |

## Actual simulation output

```
[SETUP] created public domain 'public-copr-3241f246' and control domain 'coprctl-5cac0349' (owner org 99257967)
[CHECK] PASS: dual-write: DomainOrg.org_id stamped from X-RH-IDENTITY == 99257967 (proves set_domain_create_context ran); got '99257967'
[CHECK] PASS: dual-write: rh-org-99257967 group granted domain roles on create; got 1
[SETUP] uploaded orphan RPM -> public pkg /api/pulp/public-copr-3241f246/api/v3/content/rpm/packages/01a0ab25-7e1d-7560-bca3-019348b6b59b/
[SETUP] uploaded orphan RPM -> control pkg /api/pulp/coprctl-5cac0349/api/v3/content/rpm/packages/01a0ab25-7f1f-78c1-bab2-152d0f851c73/
[CHECK] PASS: uploaded RPM is orphan content (not a member of any repository version)
[CHECK] PASS: caller's only group rh-org-16249568 has zero roles (copr shape); got 0
[REPORT] public-* GET package-by-href -> 200 (client saw 404 pre-fix)
[CHECK] PASS: FIX A: role-less caller GETs orphan RPM in public-* domain -> 200
[CONTROL] non-public GET package-by-href -> 404 (must stay 404: no access, no leak)
[CHECK] PASS: CONTROL: same role-less caller in NON-public domain -> 404 (pre-fix symptom / bypass is the differentiator)
[CHECK] PASS: FIX A: role-less caller lists orphan RPM in public-* domain (200, package present)
[PROBE] role-less caller RPM upload to public-* domain -> 403 (client reported 201; upload gate is independent of the read bug)
[ISOLATION] role-less caller GET control domain object -> 404

=== SIMULATION RESULT: ALL CHECKS PASSED ===
```

## Code analysis (why the fix is correct, not just observed)

`PulpServiceAccessPolicy` (`pulp_service/app/access_policy.py`):

- `has_permission` returns `True` for any safe method on a `public-*` domain (lines 43-47).
- Pre-fix, `scope_queryset` did **not** mirror that: it called `super().scope_queryset`, which
  scopes non-`Domain` querysets by the caller's role-held view permissions. A 0-role caller's
  queryset came back empty for the target object → detail read `get_object_or_404` → **404**.
- Fix A adds `_is_public_domain_read` and short-circuits `scope_queryset` to return the
  already-domain-filtered queryset for safe-method reads on a `public-*` domain (lines 55-74).
  `base.py` has already filtered `qs` to `request.pulp_domain`, so returning it unscoped exposes
  **only that public domain's rows** — no cross-domain leak. The control-domain `404` above is
  the empirical proof of that boundary.

The fix lives in the **generic** access policy inherited by every typed content viewset, so it
applies to RPM content exactly as to python repos (which the client's sibling failure hit) —
the simulation confirms the RPM path specifically.

## Tests run in the dev container

| Test | Result |
|---|---|
| `test_public_domain_read.py` (Fix A: `public-*` repo read `404 -> 200`) | **2 passed** |
| `test_content_view_after_upload.py` (Fix B: member sees own orphan content) | **3 passed** |
| `test_access_policy.py` (`_is_public_domain_read` / `scope_queryset` unit coverage) | **26 passed** |
| Ad-hoc end-to-end copr simulation (`copr_sim.py`) | **ALL CHECKS PASSED** |

> The functional tests default to bindings host `pulp:443` (CI). In the dev container they must
> be run with `API_PROTOCOL=http API_HOST=localhost API_PORT=24817`; otherwise they *error at
> fixture setup* on DNS (`Failed to resolve 'pulp'`) — an environment quirk, not a code result.

## What this proves / does not prove

- **Proves**: on this branch, a role-less caller (copr shape) reading orphan RPM content by
  href in a `public-*` domain gets `200` (was `404`), lists it (`200`), and a non-public domain
  with the identical shape stays `404` — the copr `_unique_nevras` crash is resolved, with
  isolation intact.
- **Does not prove**: the full copr build end to end (upload → `add_content` repo version).
  That path is gated by *write* permission, which my synthetic 0-role caller lacks (`403`
  upload) but the client's account demonstrably has (`201` upload). The verified read fix is the
  step that was actually failing for the client.
- **If the client's build still fails after this branch**, it would be on a *write* gate
  (`add_content`/repo-modify), not the read — check the copr account's effective roles on the
  domain (not just the `rh-org-<org>` group). That is a separate permissioning question, not a
  gap in this fix.

## Branch fidelity: RBAC viewset changes ported from `feat/pulp-2120`

The production RBAC the copr client actually ran was merge #1452 (reverted by #1457), whose
approach is identical to `feat/pulp-2120`. This branch's RBAC re-enable commit (`2ed9ea3`)
flipped the *default* permission class to `PulpServiceAccessPolicy` but left three views/viewsets
pinned to `DomainBasedPermission`, so the branch was a hybrid that did not match production. To
run the simulation against a faithful RBAC surface, the following `feat/pulp-2120` changes were
ported into this branch (working tree only, **not committed**):

- `authorization.py`: added `set_domain_create_context(request)` (sets the dual-write ContextVars).
- `viewsets.py`:
  - `CreateDomainView`: `DomainBasedPermission` → `IsAuthenticated`, calls
    `set_domain_create_context(request)` and resets the ContextVars in a `finally` (dropped the
    inert `DEFAULT_ACCESS_POLICY`).
  - `MigrateDomainView`: `DomainBasedPermission` → `IsAuthenticated`, enforces
    `core.change_domain` explicitly in `post()` (dropped the inert `DEFAULT_ACCESS_POLICY`).
  - `PyPIYankMonitorViewSet`: dropped the `DomainBasedPermission` override (uses default RBAC).

Everything else that differs between the branches was left alone — this branch is *ahead* of
`feat/pulp-2120` there (newer main features, per-plugin role seeding, and Fix A/B), so nothing
was back-ported.

**Re-verified after the port (`=== SIMULATION RESULT: ALL CHECKS PASSED ===`):**

- The copr simulation now asserts the ported dual-write directly: after create-domain (through
  the new `IsAuthenticated` + `set_domain_create_context` path), `DomainOrg.org_id` is stamped
  from `X-RH-IDENTITY` and the owner's `rh-org-<org>` group is granted its domain role. Both
  pass — if `set_domain_create_context` were a no-op, `org_id` would be `None` and both would
  fail. So the sim itself, not just `test_domain_dual_write`, proves the port is wired correctly.
- Tests: `test_public_domain_read` (2) + `test_content_view_after_upload` (3) +
  `test_access_policy` (26) + `test_domain_dual_write` (3) = **34 passed**.

The port does not change the copr verdict — the read fix lives in the default access policy,
which was already active — but the simulation now runs against the exact RBAC viewset surface
the client had in production.

> Open item for the author: if these ported viewset changes are meant to ship as part of this
> branch's PR (not just the local simulation), they need the `feat/pulp-2120` changelog fragment
> (`CHANGES/2120.feature`) or an equivalent. Not added here per the no-commit constraint.

## Reproduce

See `simulation-procedure-copr-public-domain.md` for the exact, re-runnable steps.
