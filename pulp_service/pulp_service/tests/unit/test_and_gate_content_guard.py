"""
Unit tests for AndGateContentGuard.permit().

AndGateContentGuard aggregates sub-guards and permits access only when EVERY
sub-guard permits (logical AND). This inverts pulpcore's CompositeContentGuard,
which permits when ANY sub-guard permits (OR). An empty guard list is
fail-closed: access is denied. These tests mock the `guards` M2M so no database
is required.
"""

from unittest.mock import MagicMock, patch

import pytest

from pulp_service.app.models import AndGateContentGuard
from pulp_service.exceptions import AuthenticationError


def _subguard(permit_side_effect=None):
    """A mock sub-guard whose cast().permit() optionally raises."""
    guard = MagicMock()
    guard.cast.return_value.permit.side_effect = permit_side_effect
    return guard


def _make(subguards):
    """An AndGateContentGuard instance with its `guards.all()` mocked to `subguards`."""
    guard = AndGateContentGuard(name="t", pulp_domain=None)
    manager = MagicMock()
    manager.all.return_value = subguards
    patcher = patch.object(type(guard), "guards", manager)
    patcher.start()
    return guard, patcher


def test_all_pass_allows():
    guard, patcher = _make([_subguard(), _subguard()])
    try:
        assert guard.permit(MagicMock()) is None
    finally:
        patcher.stop()


def test_pass_then_fail_denies():
    guard, patcher = _make([_subguard(), _subguard(PermissionError("no"))])
    try:
        with pytest.raises(PermissionError):
            guard.permit(MagicMock())
    finally:
        patcher.stop()


def test_fail_then_pass_denies():
    guard, patcher = _make([_subguard(PermissionError("no")), _subguard()])
    try:
        with pytest.raises(PermissionError):
            guard.permit(MagicMock())
    finally:
        patcher.stop()


def test_empty_guards_denies_fail_closed():
    guard, patcher = _make([])
    try:
        with pytest.raises(PermissionError):
            guard.permit(MagicMock())
    finally:
        patcher.stop()


def test_authentication_error_propagates():
    # AuthenticationError (missing identity) is NOT a PermissionError; it must propagate
    # so the content app answers 401 with an auth challenge rather than a 403.
    guard, patcher = _make([_subguard(AuthenticationError("auth"))])
    try:
        with pytest.raises(AuthenticationError):
            guard.permit(MagicMock())
    finally:
        patcher.stop()


def test_authentication_error_preferred_over_permission_error_both_orders():
    # When one sub-guard needs authentication (401) and another denies (403), the
    # 401 must win regardless of guard order so the client can retry with credentials.
    for subguards in (
        [_subguard(PermissionError("no")), _subguard(AuthenticationError("auth"))],
        [_subguard(AuthenticationError("auth")), _subguard(PermissionError("no"))],
    ):
        guard, patcher = _make(subguards)
        try:
            with pytest.raises(AuthenticationError):
                guard.permit(MagicMock())
            # No short-circuit: every sub-guard must be evaluated so the denial reason
            # (and thus the 401-over-403 precedence) is independent of guard order.
            for subguard in subguards:
                subguard.cast.return_value.permit.assert_called_once()
        finally:
            patcher.stop()
