"""Unit tests for EnvVarHeaderContentGuard permit() behavior."""

from base64 import b64encode
from unittest.mock import MagicMock, patch

import pytest

from pulp_service.app.models import EnvVarHeaderContentGuard

ENV_VAR = "ENVVAR_HEADER_GUARD_TEST_SECRET"
HEADER_NAME = "X-Test-Content-Guard-Header"
SECRET = "super-secret-value"  # noqa: S105
NON_ASCII_SECRET = "café"  # noqa: S105


def _encode_secret(secret):
    return b64encode(secret.encode("utf-8")).decode("ascii")


def _make_guard():
    return EnvVarHeaderContentGuard(
        name="envvar-header-guard-test",
        header_name=HEADER_NAME,
        env_var=ENV_VAR,
        pulp_domain=None,
    )


def _make_request(header_value=None, header_name=HEADER_NAME):
    request = MagicMock()
    headers = {}
    if header_value is not None:
        headers[header_name] = header_value
    request.headers = headers
    return request


class TestEnvVarHeaderContentGuardPermit:
    @patch.dict("os.environ", {ENV_VAR: SECRET}, clear=False)
    def test_permit_allows_matching_header(self):
        guard = _make_guard()
        guard.permit(_make_request(_encode_secret(SECRET)))

    @patch.dict("os.environ", {ENV_VAR: SECRET}, clear=False)
    def test_permit_allows_matching_header_with_env_trailing_newline(self):
        guard = _make_guard()
        with patch.dict("os.environ", {ENV_VAR: f"{SECRET}\n"}, clear=False):
            guard.permit(_make_request(_encode_secret(SECRET)))

    @patch.dict("os.environ", {ENV_VAR: NON_ASCII_SECRET}, clear=False)
    def test_permit_allows_non_ascii_secret_when_header_is_base64_encoded(self):
        guard = _make_guard()
        guard.permit(_make_request(_encode_secret(NON_ASCII_SECRET)))

    def test_permit_denies_missing_header(self):
        guard = _make_guard()
        with patch.dict("os.environ", {ENV_VAR: SECRET}, clear=False), pytest.raises(PermissionError):
            guard.permit(_make_request())

    @patch.dict("os.environ", {ENV_VAR: SECRET}, clear=False)
    def test_permit_denies_wrong_header_name(self):
        guard = _make_guard()
        with pytest.raises(PermissionError):
            guard.permit(_make_request(_encode_secret(SECRET), header_name="X-Other-Header"))

    @patch.dict("os.environ", {ENV_VAR: SECRET}, clear=False)
    def test_permit_denies_non_base64_header(self):
        guard = _make_guard()
        with pytest.raises(PermissionError):
            guard.permit(_make_request(SECRET))

    @patch.dict("os.environ", {ENV_VAR: SECRET}, clear=False)
    def test_permit_denies_wrong_value(self):
        guard = _make_guard()
        with pytest.raises(PermissionError):
            guard.permit(_make_request(_encode_secret("wrong-value")))

    def test_permit_denies_when_env_var_unset(self):
        guard = _make_guard()
        with patch.dict("os.environ", {}, clear=True), pytest.raises(PermissionError):
            guard.permit(_make_request(_encode_secret(SECRET)))

    def test_permit_denies_when_env_var_empty(self):
        guard = _make_guard()
        with patch.dict("os.environ", {ENV_VAR: "   "}, clear=False), pytest.raises(PermissionError):
            guard.permit(_make_request(_encode_secret(SECRET)))
