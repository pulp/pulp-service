"""
Unit tests for add_rh_org_id_resp_header middleware.

Verifies that X-RH-ORG-ID is injected into responses regardless of whether
the content handler returns or raises an HTTPException.
"""

import json
from base64 import b64encode
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from pulp_service.app.content import _set_org_id_header, add_rh_org_id_resp_header


def _encode_identity(identity_dict):
    return b64encode(json.dumps(identity_dict).encode()).decode()


def _make_request(identity=None):
    request = MagicMock()
    headers = {}
    if identity is not None:
        headers["x-rh-identity"] = _encode_identity(identity)
    request.headers = headers
    return request


STANDARD_IDENTITY = {"identity": {"org_id": "1979710", "user": {"username": "testuser"}}}
X509_IDENTITY = {"identity": {"x509": {"subject_dn": "CN=service-account,O=Red Hat"}}}
MINIMAL_IDENTITY = {"identity": {"user": {"username": "anotheruser"}}}


class TestSetOrgIdHeader:
    """Unit tests for the _set_org_id_header helper."""

    def test_sets_header_with_standard_identity(self):
        request = _make_request(STANDARD_IDENTITY)
        response = web.Response()

        _set_org_id_header(request, response)

        assert response.headers["X-RH-ORG-ID"] == "1979710"

    def test_skips_when_no_identity_header(self):
        request = _make_request()
        response = web.Response()

        _set_org_id_header(request, response)

        assert "X-RH-ORG-ID" not in response.headers

    def test_skips_for_x509_identity(self):
        request = _make_request(X509_IDENTITY)
        response = web.Response()

        _set_org_id_header(request, response)

        assert "X-RH-ORG-ID" not in response.headers

    def test_skips_for_identity_without_org_id(self):
        request = _make_request(MINIMAL_IDENTITY)
        response = web.Response()

        _set_org_id_header(request, response)

        assert "X-RH-ORG-ID" not in response.headers

    def test_sets_header_on_http_exception(self):
        request = _make_request(STANDARD_IDENTITY)
        response = web.HTTPNotFound()

        _set_org_id_header(request, response)

        assert response.headers["X-RH-ORG-ID"] == "1979710"

    def test_sets_header_on_redirect(self):
        request = _make_request(STANDARD_IDENTITY)
        response = web.HTTPFound(location="https://s3.example.com/artifact")

        _set_org_id_header(request, response)

        assert response.headers["X-RH-ORG-ID"] == "1979710"


class TestAddRhOrgIdRespHeader:
    """Integration tests for the full middleware."""

    @pytest.mark.asyncio
    async def test_normal_response_gets_org_id(self):
        request = _make_request(STANDARD_IDENTITY)
        handler = AsyncMock(return_value=web.Response())

        response = await add_rh_org_id_resp_header(request, handler)

        assert response.headers["X-RH-ORG-ID"] == "1979710"

    @pytest.mark.asyncio
    async def test_raised_404_gets_org_id(self):
        request = _make_request(STANDARD_IDENTITY)
        handler = AsyncMock(side_effect=web.HTTPNotFound())

        response = await add_rh_org_id_resp_header(request, handler)

        assert response.status == 404
        assert response.headers["X-RH-ORG-ID"] == "1979710"

    @pytest.mark.asyncio
    async def test_raised_302_gets_org_id(self):
        request = _make_request(STANDARD_IDENTITY)
        handler = AsyncMock(side_effect=web.HTTPFound(location="https://s3.example.com/artifact"))

        response = await add_rh_org_id_resp_header(request, handler)

        assert response.status == 302
        assert response.headers["X-RH-ORG-ID"] == "1979710"

    @pytest.mark.asyncio
    async def test_raised_301_gets_org_id(self):
        request = _make_request(STANDARD_IDENTITY)
        handler = AsyncMock(side_effect=web.HTTPMovedPermanently(location="/path/"))

        response = await add_rh_org_id_resp_header(request, handler)

        assert response.status == 301
        assert response.headers["X-RH-ORG-ID"] == "1979710"

    @pytest.mark.asyncio
    async def test_raised_403_gets_org_id(self):
        request = _make_request(STANDARD_IDENTITY)
        handler = AsyncMock(side_effect=web.HTTPForbidden())

        response = await add_rh_org_id_resp_header(request, handler)

        assert response.status == 403
        assert response.headers["X-RH-ORG-ID"] == "1979710"

    @pytest.mark.asyncio
    async def test_no_identity_header_returns_without_org_id(self):
        request = _make_request()
        handler = AsyncMock(return_value=web.Response())

        response = await add_rh_org_id_resp_header(request, handler)

        assert "X-RH-ORG-ID" not in response.headers

    @pytest.mark.asyncio
    async def test_no_identity_on_raised_exception(self):
        request = _make_request()
        handler = AsyncMock(side_effect=web.HTTPNotFound())

        response = await add_rh_org_id_resp_header(request, handler)

        assert response.status == 404
        assert "X-RH-ORG-ID" not in response.headers

    @pytest.mark.asyncio
    async def test_x509_identity_on_raised_exception(self):
        request = _make_request(X509_IDENTITY)
        handler = AsyncMock(side_effect=web.HTTPNotFound())

        response = await add_rh_org_id_resp_header(request, handler)

        assert response.status == 404
        assert "X-RH-ORG-ID" not in response.headers
