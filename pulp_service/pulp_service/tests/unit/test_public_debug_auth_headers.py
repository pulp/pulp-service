from django.test import override_settings
from rest_framework.test import APIRequestFactory

from pulp_service.app.viewsets import PublicDebugAuthenticationHeadersView

factory = APIRequestFactory()
view = PublicDebugAuthenticationHeadersView.as_view()


@override_settings(AUTHENTICATION_HEADER_DEBUG=False)
def test_public_debug_auth_headers_disabled():
    response = view(factory.get("/api/pulp/public-debug_auth_header/"))

    assert response.status_code == 404


@override_settings(AUTHENTICATION_HEADER_DEBUG=True)
def test_public_debug_auth_headers_returns_safe_diagnostics():
    request = factory.get(
        "/api/pulp/public-debug_auth_header/",
        HTTP_X_RH_IDENTITY="sensitive-identity",
        HTTP_X_PULP_VPN_VERIFIED="dHJ1ZQ==",
        HTTP_X_PULP_VPN_ACCESS="sensitive-secret",
        HTTP_AUTHORIZATION="Bearer sensitive-token",
        HTTP_COOKIE="sessionid=sensitive-cookie",
        HTTP_USER_AGENT="sensitive-user-agent",
    )

    response = view(request)

    assert response.status_code == 200
    assert response.data == {
        "x_rh_identity_present": True,
        "x_pulp_vpn_verified": True,
        "x_pulp_vpn_access_present": True,
    }
    assert response["Cache-Control"] == "private, no-store"
    assert "sensitive" not in str(response.data)


@override_settings(AUTHENTICATION_HEADER_DEBUG=True)
def test_public_debug_auth_headers_without_headers():
    response = view(factory.get("/api/pulp/public-debug_auth_header/"))

    assert response.status_code == 200
    assert response.data == {
        "x_rh_identity_present": False,
        "x_pulp_vpn_verified": False,
        "x_pulp_vpn_access_present": False,
    }


@override_settings(AUTHENTICATION_HEADER_DEBUG=True)
def test_public_debug_auth_headers_reports_received_vpn_assertion():
    request = factory.get(
        "/api/pulp/public-debug_auth_header/",
        HTTP_X_PULP_VPN_VERIFIED="arbitrary-value",
    )

    response = view(request)

    assert response.status_code == 200
    assert response.data["x_pulp_vpn_verified"] is True


@override_settings(AUTHENTICATION_HEADER_DEBUG=False)
def test_public_debug_auth_headers_disabled_response_is_not_cacheable():
    response = view(factory.get("/api/pulp/public-debug_auth_header/"))

    assert response.status_code == 404
    assert response["Cache-Control"] == "private, no-store"


@override_settings(AUTHENTICATION_HEADER_DEBUG=True)
def test_public_debug_auth_headers_rejects_non_get_methods():
    response = view(factory.post("/api/pulp/public-debug_auth_header/"))

    assert response.status_code == 405
