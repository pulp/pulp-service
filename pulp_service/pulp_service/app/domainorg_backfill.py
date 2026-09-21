"""Shared logic for the DomainOrg org_id backfill.

Migration 0022 and the ``domainorg_backfill_report`` management command must agree on
exactly which rows are resolvable and to which org_id, so that derivation lives here once.
This module is model-agnostic (it takes duck-typed ``domain_org`` objects) and imports no
Django models, so its logic and formatters are unit-testable without a database.
"""

import json
from dataclasses import asdict, dataclass

from pulp_service.app.constants import ORG_GROUP_PREFIX

# org_id values that mean "missing" even though the column is non-NULL. See the CLAUDE.md jq
# gotcha: string interpolation can yield "null"/"None"; empty string is the legacy blank shape.
MISSING_ORG_SENTINELS = frozenset({"", "null", "None"})

# Reason codes reported per row.
REASON_DERIVED = "derived-from-team"
REASON_NO_GROUP = "no-team-group"
REASON_EMPTY_GROUP = "empty-team-group"
REASON_NO_ORG = "no-org-membership-in-team"
REASON_MIXED = "mixed-org-team"


def normalize_org_id(value):
    """Return a real org_id string, or None for NULL / blank / whitespace / sentinel values."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in MISSING_ORG_SENTINELS:
        return None
    return text


def _team_org_ids(domain_org):
    """Distinct org ids among the team group's members, from their rh-org-<org_id> groups."""
    org_ids = set()
    for user in domain_org.group.user_set.all():
        for name in user.groups.filter(name__startswith=ORG_GROUP_PREFIX).values_list("name", flat=True):
            org_ids.add(name[len(ORG_GROUP_PREFIX) :])
    return org_ids


def derive_org_id(domain_org):
    """The org_id migration 0022 stores for this row: the stored value if real, else the single
    unambiguous org among the team group's members, else None. Behaviour-compatible with 0022's
    original _derive_org_id plus sentinel normalization."""
    stored = normalize_org_id(domain_org.org_id)
    if stored:
        return stored
    if domain_org.group_id is None:
        return None
    org_ids = _team_org_ids(domain_org)
    return next(iter(org_ids)) if len(org_ids) == 1 else None


def classify_reason(group_id, member_count, distinct_orgs):
    """Pure decision for a missing-org row. Returns (resolvable, derived_org_id, reason_code)."""
    orgs = list(distinct_orgs)
    if group_id is None:
        return (False, None, REASON_NO_GROUP)
    if member_count == 0:
        return (False, None, REASON_EMPTY_GROUP)
    if not orgs:
        return (False, None, REASON_NO_ORG)
    if len(orgs) > 1:
        return (False, None, REASON_MIXED)
    return (True, orgs[0], REASON_DERIVED)


@dataclass(frozen=True)
class BackfillStatus:
    domain_org_pk: int
    domain_names: list
    group_name: str
    group_id: int
    member_count: int
    distinct_orgs: list
    resolvable: bool
    derived_org_id: str
    reason: str


def classify(domain_org):
    """Classify a missing-org DomainOrg into a BackfillStatus (reads the ORM relations)."""
    group = getattr(domain_org, "group", None)
    if group is None:
        member_count, distinct_orgs = 0, []
    else:
        member_count = group.user_set.count()
        distinct_orgs = sorted(_team_org_ids(domain_org))
    resolvable, derived, reason = classify_reason(domain_org.group_id, member_count, distinct_orgs)
    return BackfillStatus(
        domain_org_pk=domain_org.pk,
        domain_names=sorted(d.name for d in domain_org.domains.all()),
        group_name=group.name if group else None,
        group_id=domain_org.group_id,
        member_count=member_count,
        distinct_orgs=distinct_orgs,
        resolvable=resolvable,
        derived_org_id=derived,
        reason=reason,
    )


def format_json(statuses):
    """Structured records for machine consumption (--format json)."""
    return json.dumps([asdict(s) for s in statuses], indent=2, sort_keys=True)


def format_table(statuses):
    """Aligned human-readable table plus a summary line."""
    if not statuses:
        return "No DomainOrg rows with a missing org_id. Nothing for migration 0022 to backfill."

    headers = ["PK", "DOMAINS", "GROUP", "MEMBERS", "ORGS_FOUND", "STATUS", "DERIVED_ORG", "REASON"]
    rows = [
        [
            str(s.domain_org_pk),
            ",".join(s.domain_names) or "-",
            s.group_name or "-",
            str(s.member_count),
            ",".join(s.distinct_orgs) or "-",
            "RESOLVABLE" if s.resolvable else "UNRESOLVABLE",
            s.derived_org_id or "-",
            s.reason,
        ]
        for s in statuses
    ]
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]
    line = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [line.format(*headers), line.format(*("-" * w for w in widths))]
    out += [line.format(*r) for r in rows]

    resolvable = sum(1 for s in statuses if s.resolvable)
    reasons = {}
    for s in statuses:
        if not s.resolvable:
            reasons[s.reason] = reasons.get(s.reason, 0) + 1
    summary = f"resolvable={resolvable} unresolvable={len(statuses) - resolvable}"
    if reasons:
        summary += " (" + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())) + ")"
    return "\n".join(out) + "\n\n" + summary
