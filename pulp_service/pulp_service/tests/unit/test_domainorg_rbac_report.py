"""Unit tests for the shared DomainOrg RBAC report logic (mirrors test_domainorg_backfill.py:
no DB). Covers the pure tier classifier, reason decision, and formatters."""

import json

from pulp_service.app.domainorg_rbac_report import (
    REASON_NO_PRINCIPAL,
    REASON_NO_ROLES,
    REASON_OK,
    REASON_VIEWER_ONLY,
    TIER_FULL,
    TIER_NONE,
    TIER_VIEWER,
    RbacStatus,
    classify_reason,
    classify_tier,
    format_json,
    format_table,
)

# --- classify_tier (pure) ---


def test_classify_tier_full_when_domain_admin():
    tier, can_get, can_push, has_obj = classify_tier(["service.domain_admin"], ["core.domain_owner"])
    assert (tier, can_get, can_push, has_obj) == (TIER_FULL, True, True, True)


def test_classify_tier_viewer_when_only_domain_viewer():
    tier, can_get, can_push, has_obj = classify_tier(["service.domain_viewer"], ["core.domain_viewer"])
    assert (tier, can_get, can_push, has_obj) == (TIER_VIEWER, True, False, True)


def test_classify_tier_none_when_no_roles():
    assert classify_tier([], []) == (TIER_NONE, False, False, False)


def test_classify_tier_admin_beats_viewer():
    tier, can_get, can_push, _ = classify_tier(["service.domain_viewer", "service.domain_admin"], [])
    assert (tier, can_get, can_push) == (TIER_FULL, True, True)


def test_classify_tier_admin_without_object_role_detected():
    _, _, _, has_obj = classify_tier(["service.domain_admin"], [])
    assert has_obj is False


# --- classify_reason (pure) ---


def test_classify_reason_no_principal():
    assert classify_reason(TIER_NONE, has_principal=False) == REASON_NO_PRINCIPAL


def test_classify_reason_locked_out():
    assert classify_reason(TIER_NONE) == REASON_NO_ROLES


def test_classify_reason_viewer_only():
    assert classify_reason(TIER_VIEWER) == REASON_VIEWER_ONLY


def test_classify_reason_full_ok():
    assert classify_reason(TIER_FULL) == REASON_OK


# --- formatters ---


def _status(**kw):
    base = {
        "domain_org_pk": 1,
        "domain_name": "d1",
        "principal_type": "group",
        "principal_name": "rh-org-11111111",
        "principal_source": "derived-org-group",
        "tier": TIER_FULL,
        "can_get": True,
        "can_push": True,
        "has_domain_object_role": True,
        "roles": ["core.domain_owner", "service.domain_admin"],
        "flagged": False,
        "reason": REASON_OK,
    }
    base.update(kw)
    return RbacStatus(**base)


def test_format_json_roundtrips_all_fields():
    statuses = [
        _status(),
        _status(
            domain_org_pk=2,
            tier=TIER_NONE,
            can_get=False,
            can_push=False,
            has_domain_object_role=False,
            roles=[],
            flagged=True,
            reason=REASON_NO_ROLES,
        ),
    ]
    parsed = json.loads(format_json(statuses))
    assert [r["domain_org_pk"] for r in parsed] == [1, 2]
    assert parsed[0]["can_push"] is True
    assert parsed[1]["reason"] == REASON_NO_ROLES
    assert parsed[1]["roles"] == []


def test_format_table_shows_rows_and_summary():
    out = format_table(
        [
            _status(),
            _status(domain_org_pk=2, tier=TIER_NONE, can_push=False, flagged=True, reason=REASON_NO_ROLES),
            _status(domain_org_pk=3, tier=TIER_VIEWER, can_push=False, flagged=True, reason=REASON_VIEWER_ONLY),
        ]
    )
    assert TIER_FULL in out
    assert TIER_NONE in out
    assert "full=1 viewer=1 none=1 flagged=2" in out
    assert f"{REASON_NO_ROLES}=1" in out


def test_format_table_empty():
    assert "No DomainOrg" in format_table([])
