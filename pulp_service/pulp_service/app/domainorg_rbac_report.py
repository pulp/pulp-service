"""Shared logic for the DomainOrg RBAC access report.

Reports whether each DomainOrg's principals (its user, its team group, and its derived
``rh-org-<org_id>`` group) actually hold the roles that let them GET and PUSH content on the
domains they own. This is the RBAC-state complement to ``domainorg_backfill_report`` (which
reports whether org_id can be backfilled): here we report whether the *roles* landed.

The access tier reflects content access only. Holding ``service.domain_admin`` (domain-scoped)
is FULL (can GET and PUSH content); holding only ``service.domain_viewer`` is VIEWER (GET, no
PUSH); holding neither is the "locked out" shape (the calunga null-org_id failure mode). The
object-level ``core.domain_owner`` role (which lets a principal manage the Domain object) is
not required to GET or PUSH content, so it is not part of the tier; it is reported separately
as ``has_domain_object_role`` for visibility.

This module is model-agnostic (it takes already-resolved role-name collections) and imports no
Django models, so its logic and formatters are unit-testable without a database.
"""

import json
from dataclasses import asdict, dataclass

# Role names assigned by signals.py / app.__init__._populate_service_roles. Kept here as the
# single reference for the classifier; signals.py assigns these exact strings.
ROLE_DOMAIN_ADMIN = "service.domain_admin"  # domain-scoped: GET + PUSH content in the domain
ROLE_DOMAIN_VIEWER = "service.domain_viewer"  # domain-scoped: GET (view/download) only
ROLE_DOMAIN_OWNER = "core.domain_owner"  # object-level on the Domain: manage the domain object
ROLE_DOMAIN_OBJ_VIEWER = "core.domain_viewer"  # object-level on the Domain: see the domain object

# Access tiers, most-capable first.
TIER_FULL = "FULL"  # can GET and PUSH
TIER_VIEWER = "VIEWER"  # can GET only
TIER_NONE = "NONE"  # locked out

# Reason codes reported per (principal, domain).
REASON_OK = "ok"
REASON_VIEWER_ONLY = "viewer-only-cannot-push"
REASON_NO_ROLES = "no-roles-locked-out"
REASON_NO_PRINCIPAL = "no-resolvable-principal"


def classify_tier(scoped_role_names, objlevel_role_names):
    """Pure tier decision. Returns (tier, can_get, can_push, has_domain_object_role).

    ``scoped_role_names`` are the domain-scoped roles the principal holds on the domain;
    ``objlevel_role_names`` are the roles held object-level on the Domain row itself.
    """
    scoped = set(scoped_role_names)
    objlevel = set(objlevel_role_names)
    can_push = ROLE_DOMAIN_ADMIN in scoped
    can_get = can_push or ROLE_DOMAIN_VIEWER in scoped
    has_domain_object_role = bool(objlevel & {ROLE_DOMAIN_OWNER, ROLE_DOMAIN_OBJ_VIEWER})
    if can_push:
        tier = TIER_FULL
    elif can_get:
        tier = TIER_VIEWER
    else:
        tier = TIER_NONE
    return tier, can_get, can_push, has_domain_object_role


def classify_reason(tier, has_principal=True):
    """Pure reason decision for a (principal, domain). Returns a REASON_* code."""
    if not has_principal:
        return REASON_NO_PRINCIPAL
    if tier == TIER_NONE:
        return REASON_NO_ROLES
    if tier == TIER_VIEWER:
        return REASON_VIEWER_ONLY
    return REASON_OK


@dataclass(frozen=True)
class RbacStatus:
    domain_org_pk: int
    domain_name: str
    principal_type: str
    principal_name: str
    principal_source: str
    tier: str
    can_get: bool
    can_push: bool
    has_domain_object_role: bool
    roles: list
    flagged: bool
    reason: str


def format_json(statuses):
    """Structured records for machine consumption (--format json)."""
    return json.dumps([asdict(s) for s in statuses], indent=2, sort_keys=True)


def format_table(statuses):
    """Aligned human-readable table plus a summary line."""
    if not statuses:
        return "No DomainOrg rows matched. Nothing to report."

    headers = ["PK", "DOMAIN", "PRINCIPAL", "SOURCE", "TIER", "GET", "PUSH", "OBJ_ROLE", "REASON"]
    rows = [
        [
            str(s.domain_org_pk),
            s.domain_name or "-",
            s.principal_name or "-",
            s.principal_source,
            s.tier,
            "yes" if s.can_get else "no",
            "yes" if s.can_push else "no",
            "yes" if s.has_domain_object_role else "no",
            s.reason,
        ]
        for s in statuses
    ]
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]
    line = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [line.format(*headers), line.format(*("-" * w for w in widths))]
    out += [line.format(*r) for r in rows]

    full = sum(1 for s in statuses if s.tier == TIER_FULL)
    viewer = sum(1 for s in statuses if s.tier == TIER_VIEWER)
    none = sum(1 for s in statuses if s.tier == TIER_NONE)
    flagged = sum(1 for s in statuses if s.flagged)
    reasons = {}
    for s in statuses:
        if s.flagged:
            reasons[s.reason] = reasons.get(s.reason, 0) + 1
    summary = f"full={full} viewer={viewer} none={none} flagged={flagged}"
    if reasons:
        summary += " (" + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())) + ")"
    return "\n".join(out) + "\n\n" + summary
