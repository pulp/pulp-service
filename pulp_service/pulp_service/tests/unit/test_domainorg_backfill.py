"""Unit tests for the shared DomainOrg backfill logic (mirrors tests/unit/test_derive_org_id.py:
MagicMock, no DB). Covers normalization, the pure classifier, derivation, and formatters."""

import json
from unittest.mock import MagicMock

from pulp_service.app.domainorg_backfill import (
    BackfillStatus,
    classify_reason,
    derive_org_id,
    format_json,
    format_table,
    normalize_org_id,
)


def _user_in_org_groups(*group_names):
    """A user whose rh-org-* group names resolve to the given list (see test_derive_org_id.py)."""
    user = MagicMock()
    user.groups.filter.return_value.values_list.return_value = list(group_names)
    return user


def _domain_org(org_id=None, group_id=1, users=()):
    do = MagicMock()
    do.org_id = org_id
    do.group_id = group_id
    if group_id is None:
        do.group = None
    else:
        do.group.user_set.all.return_value = list(users)
    return do


# --- normalize_org_id ---


def test_normalize_none_is_missing():
    assert normalize_org_id(None) is None


def test_normalize_blank_and_whitespace_are_missing():
    assert normalize_org_id("") is None
    assert normalize_org_id("   ") is None


def test_normalize_sentinel_strings_are_missing():
    assert normalize_org_id("null") is None
    assert normalize_org_id("None") is None


def test_normalize_real_value_is_trimmed():
    assert normalize_org_id("  123  ") == "123"


# --- classify_reason (pure) ---


def test_classify_reason_no_group():
    assert classify_reason(None, 0, []) == (False, None, "no-team-group")


def test_classify_reason_empty_group():
    assert classify_reason(5, 0, []) == (False, None, "empty-team-group")


def test_classify_reason_members_but_no_org():
    assert classify_reason(5, 2, []) == (False, None, "no-org-membership-in-team")


def test_classify_reason_mixed_org():
    assert classify_reason(5, 3, ["11111111", "22222222"]) == (False, None, "mixed-org-team")


def test_classify_reason_single_org_resolvable():
    assert classify_reason(5, 2, ["11111111"]) == (True, "11111111", "derived-from-team")


# --- derive_org_id (what 0022 will store) ---


def test_derive_returns_stored_when_present():
    assert derive_org_id(_domain_org(org_id="99999999")) == "99999999"


def test_derive_ignores_sentinel_stored_and_falls_back_to_team():
    do = _domain_org(org_id="null", group_id=7, users=[_user_in_org_groups("rh-org-12345678")])
    assert derive_org_id(do) == "12345678"


def test_derive_none_when_no_group():
    assert derive_org_id(_domain_org(org_id=None, group_id=None)) is None


def test_derive_single_org_from_team():
    do = _domain_org(group_id=7, users=[_user_in_org_groups("rh-org-12345678")])
    assert derive_org_id(do) == "12345678"


def test_derive_none_when_mixed_orgs():
    do = _domain_org(
        group_id=7,
        users=[_user_in_org_groups("rh-org-11111111"), _user_in_org_groups("rh-org-22222222")],
    )
    assert derive_org_id(do) is None


def test_derive_none_when_no_org_membership():
    do = _domain_org(group_id=7, users=[_user_in_org_groups()])
    assert derive_org_id(do) is None


# --- formatters ---


def _status(**kw):
    base = {
        "domain_org_pk": 1,
        "domain_names": ["d1"],
        "group_name": "team-a",
        "group_id": 7,
        "member_count": 2,
        "distinct_orgs": ["11111111"],
        "resolvable": True,
        "derived_org_id": "11111111",
        "reason": "derived-from-team",
    }
    base.update(kw)
    return BackfillStatus(**base)


def test_format_json_roundtrips_all_fields():
    statuses = [
        _status(),
        _status(
            domain_org_pk=2,
            resolvable=False,
            derived_org_id=None,
            distinct_orgs=[],
            reason="empty-team-group",
            member_count=0,
        ),
    ]
    parsed = json.loads(format_json(statuses))
    assert [r["domain_org_pk"] for r in parsed] == [1, 2]
    assert parsed[0]["derived_org_id"] == "11111111"
    assert parsed[1]["reason"] == "empty-team-group"


def test_format_table_shows_rows_and_summary():
    out = format_table(
        [_status(), _status(domain_org_pk=2, resolvable=False, derived_org_id=None, reason="mixed-org-team")]
    )
    assert "RESOLVABLE" in out
    assert "UNRESOLVABLE" in out
    assert "resolvable=1 unresolvable=1" in out
    assert "mixed-org-team=1" in out


def test_format_table_empty():
    assert "Nothing" in format_table([])
