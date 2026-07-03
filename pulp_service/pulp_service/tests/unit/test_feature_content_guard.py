"""
Unit tests for FeatureContentGuard's caching and Features Service call behavior.

These cover the regression behind the "content-guarded downloads take 7-10s" latency bug:
the functional test in test_feature_service.py only asserts on HTTP status codes for a
single request, so it can't detect whether the 24h cache is actually being honored on
repeat requests, or whether a slow/unavailable Features Service is bounded by a timeout.
These tests mock the Features Service HTTP call and the Redis-backed cache directly so
they don't require a live Redis or network connection.
"""

import json
import time
from base64 import b64encode
from unittest.mock import MagicMock, patch

import pytest
import requests

from pulp_service.app.models import FeatureContentGuard, FeatureContentGuardCache

FEATURES = ["lightwell-network"]
ACCOUNT_ID = "1979710"


def _make_guard(features=FEATURES):
    # `pulp_domain` must be passed explicitly: its field default (`get_domain_pk`) runs a
    # raw SQL query, which isn't available/desired in a DB-less unit test.
    return FeatureContentGuard(
        name="test",
        header_name="x-rh-identity",
        jq_filter=".identity.org_id",
        features=features,
        pulp_domain=None,
    )


def _make_request(account_id=ACCOUNT_ID):
    identity = json.dumps({"identity": {"org_id": account_id}}).encode()
    request = MagicMock()
    request.headers = {"x-rh-identity": b64encode(identity).decode()}
    return request


def _feature_response(feature_names):
    response = MagicMock(spec=requests.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"features": [{"name": name} for name in feature_names]}
    return response


@pytest.fixture(autouse=True)
def _reset_shared_session():
    # `_session` is a class-level singleton; make sure tests don't share mocked state.
    FeatureContentGuard._session = None
    yield
    FeatureContentGuard._session = None


class TestFeatureContentGuardCaching:
    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_cache_hit_skips_feature_service_call(self, mock_get, mock_set):
        entry = json.dumps({"allowed": True, "expires_at": time.time() + 3600})
        mock_get.return_value = entry.encode()
        guard = _make_guard()
        guard._get_session = MagicMock()

        guard.permit(_make_request())

        guard._get_session.assert_not_called()
        mock_set.assert_not_called()

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_cache_hit_denied_skips_feature_service_call_and_raises(self, mock_get, mock_set):
        entry = json.dumps({"allowed": False, "expires_at": time.time() + 3600})
        mock_get.return_value = entry.encode()
        guard = _make_guard()
        guard._get_session = MagicMock()

        with pytest.raises(PermissionError):
            guard.permit(_make_request())

        guard._get_session.assert_not_called()
        mock_set.assert_not_called()

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_cache_miss_calls_feature_service_and_populates_cache(self, mock_get, mock_set):
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(FEATURES)
        guard._get_session = MagicMock(return_value=session)

        guard.permit(_make_request())

        session.get.assert_called_once()
        mock_set.assert_called_once()
        cached_key, cached_value = mock_set.call_args.args[0], mock_set.call_args.args[1]
        assert isinstance(cached_key, str)
        payload = json.loads(cached_value)
        assert payload["allowed"] is True
        assert payload["expires_at"] > 0

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_expired_cache_entry_is_treated_as_a_miss(self, mock_get, mock_set):
        stale_entry = json.dumps({"allowed": True, "expires_at": time.time() - 1})
        mock_get.return_value = stale_entry.encode()
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(FEATURES)
        guard._get_session = MagicMock(return_value=session)

        guard.permit(_make_request())

        session.get.assert_called_once()
        mock_set.assert_called_once()

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_legacy_cache_format_does_not_crash_and_is_treated_as_a_miss(self, mock_get, mock_set):
        # Cached values written by the pre-fix code were bare "true"/"false" strings, not
        # a {"allowed": ..., "expires_at": ...} object. Confirm the new reader degrades
        # gracefully instead of raising.
        mock_get.return_value = b"true"
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(FEATURES)
        guard._get_session = MagicMock(return_value=session)

        guard.permit(_make_request())

        session.get.assert_called_once()

    @pytest.mark.parametrize("corrupted_value", [b"{not-json", b"{}", b"[]", b"null"])
    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_corrupted_cache_entry_does_not_crash_and_is_treated_as_a_miss(self, mock_get, mock_set, corrupted_value):
        # Malformed or structurally invalid payloads (truncated writes, unrelated data,
        # a schema change, etc.) must degrade to a cache miss rather than raising out of
        # permit() and turning a caching bug into an outage.
        mock_get.return_value = corrupted_value
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(FEATURES)
        guard._get_session = MagicMock(return_value=session)

        guard.permit(_make_request())

        session.get.assert_called_once()
        mock_set.assert_called_once()
        payload = json.loads(mock_set.call_args.args[1])
        assert set(payload.keys()) == {"allowed", "expires_at"}
        assert payload["allowed"] is True

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_account_without_feature_is_denied_and_result_is_cached(self, mock_get, mock_set):
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(["some-other-feature"])
        guard._get_session = MagicMock(return_value=session)

        with pytest.raises(PermissionError):
            guard.permit(_make_request())

        payload = json.loads(mock_set.call_args.args[1])
        assert payload["allowed"] is False


class TestFeatureContentGuardFeatureServiceCall:
    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_feature_service_timeout_fails_closed_without_hanging(self, mock_get, mock_set):
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.side_effect = requests.Timeout("Features Service did not respond")
        guard._get_session = MagicMock(return_value=session)

        with pytest.raises(PermissionError):
            guard.permit(_make_request())

        session.get.assert_called_once()
        # A denial caused by a Features Service outage must not be cached as a permanent
        # "not entitled" result -- that would keep denying the account for 24h after the
        # outage clears.
        mock_set.assert_not_called()

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_feature_service_call_passes_a_bounded_timeout(self, mock_get, mock_set):
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(FEATURES)
        guard._get_session = MagicMock(return_value=session)

        guard.permit(_make_request())

        _, kwargs = session.get.call_args
        assert "timeout" in kwargs
        assert kwargs["timeout"] is not None

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_feature_service_timeout_is_a_real_tuple_not_a_list(self, mock_get, mock_set):
        # Regression test: `requests`/`urllib3` only splits a *tuple* into (connect, read)
        # timeouts -- a list is treated as a single scalar value and raises a ValueError.
        # Pulp's settings pipeline can silently turn a tuple *setting* into a list, which is
        # exactly what happened in production, so the (connect, read) pair must be
        # constructed as a plain tuple in code rather than passed through from settings as-is.
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(FEATURES)
        guard._get_session = MagicMock(return_value=session)

        guard.permit(_make_request())

        _, kwargs = session.get.call_args
        assert type(kwargs["timeout"]) is tuple

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_unexpected_error_from_feature_service_fails_closed_without_crashing(self, mock_get, mock_set):
        # Regression test: `permit()` must only ever return normally or raise
        # `PermissionError`. pulpcore's `Handler.auth_cached` wraps the guard call in a
        # try/except that only handles `HTTPForbidden`; any other exception type leaves its
        # `guard` variable unassigned and crashes with an unrelated `UnboundLocalError` in
        # its `finally` block, masking the real error. This previously happened for real:
        # a misconfigured timeout setting raised a bare `ValueError` that escaped uncaught.
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.side_effect = ValueError("Timeout value connect was [2, 5]...")
        guard._get_session = MagicMock(return_value=session)

        with pytest.raises(PermissionError):
            guard.permit(_make_request())

        mock_set.assert_not_called()

    def test_session_is_a_process_wide_singleton(self):
        guard_a = FeatureContentGuard(name="a", header_name="x-rh-identity", features=FEATURES, pulp_domain=None)
        guard_b = FeatureContentGuard(name="b", header_name="x-rh-identity", features=FEATURES, pulp_domain=None)

        with patch("pulp_service.app.models.requests.Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            first = guard_a._get_session()
            second = guard_b._get_session()

        assert first is second
        mock_session_cls.assert_called_once()


class TestFeatureContentGuardCheckFeature:
    """Unit tests for `check_feature()`, the caching entry point reused by `permit()` and by
    `DomainBasedPermission` (for the lightwell-network feature check on PyPI views)."""

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_cache_hit_skips_feature_service_call(self, mock_get, mock_set):
        entry = json.dumps({"allowed": True, "expires_at": time.time() + 3600})
        mock_get.return_value = entry.encode()
        guard = _make_guard()
        guard._get_session = MagicMock()

        assert guard.check_feature(ACCOUNT_ID) is True

        guard._get_session.assert_not_called()
        mock_set.assert_not_called()

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_cache_miss_calls_feature_service_and_populates_cache(self, mock_get, mock_set):
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(FEATURES)
        guard._get_session = MagicMock(return_value=session)

        assert guard.check_feature(ACCOUNT_ID) is True

        session.get.assert_called_once()
        mock_set.assert_called_once()

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_account_without_feature_returns_false_without_raising(self, mock_get, mock_set):
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(["some-other-feature"])
        guard._get_session = MagicMock(return_value=session)

        assert guard.check_feature(ACCOUNT_ID) is False

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_feature_service_failure_raises_permission_error(self, mock_get, mock_set):
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.side_effect = requests.Timeout("Features Service did not respond")
        guard._get_session = MagicMock(return_value=session)

        with pytest.raises(PermissionError):
            guard.check_feature(ACCOUNT_ID)

        mock_set.assert_not_called()

    @patch.object(FeatureContentGuardCache, "set")
    @patch.object(FeatureContentGuardCache, "get")
    def test_permit_and_check_feature_share_the_same_cache_key(self, mock_get, mock_set):
        """`permit()` and `check_feature()` must compute identical cache keys for the same
        (account_id, features) pair, so a lookup made through one path is reused by the
        other and the Features Service isn't hit twice for the same account/feature."""
        mock_get.return_value = None
        guard = _make_guard()
        session = MagicMock()
        session.get.return_value = _feature_response(FEATURES)
        guard._get_session = MagicMock(return_value=session)

        guard.permit(_make_request(account_id=ACCOUNT_ID))
        permit_cache_key = mock_set.call_args.args[0]

        mock_set.reset_mock()
        assert guard.check_feature(ACCOUNT_ID) is True
        check_feature_cache_key = mock_set.call_args.args[0]

        assert permit_cache_key == check_feature_cache_key
