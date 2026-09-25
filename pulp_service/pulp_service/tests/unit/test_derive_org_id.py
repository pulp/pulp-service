"""Unit tests for signals._derive_org_id_from_user (create-time fallback).

Mirror the existing unit-test style (mocked ORM, no live Pulp stack) used by
test_create_domain_view.py.
"""

from unittest.mock import MagicMock

from pulp_service.app.signals import _derive_org_id_from_user


def _user_in_org_groups(*group_names):
    """Build a mock user whose rh-org-* group filter yields the given names."""
    user = MagicMock()
    user.groups.filter.return_value.values_list.return_value = list(group_names)
    return user


def test_single_org_group_derives_org_id():
    user = _user_in_org_groups("rh-org-15322645")
    assert _derive_org_id_from_user(user) == "15322645"


def test_no_org_groups_returns_none():
    user = _user_in_org_groups()
    assert _derive_org_id_from_user(user) is None


def test_ambiguous_multi_org_returns_none():
    user = _user_in_org_groups("rh-org-111", "rh-org-222")
    assert _derive_org_id_from_user(user) is None
