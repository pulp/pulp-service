"""
Kessel client used by KesselContentGuard.

Mirrors content-sources-backend: look up the org's default workspace via RBAC v2, then ask
Kessel Inventory (v1beta2 ``Check``) whether ``redhat/<user_id>`` has ``permission`` on it.
"""

from functools import lru_cache

from django.conf import settings
from kessel.auth import OAuth2ClientCredentials, oauth2_auth_request
from kessel.inventory.v1beta2 import ClientBuilder
from kessel.inventory.v1beta2.allowed_pb2 import ALLOWED_TRUE
from kessel.inventory.v1beta2.check_request_pb2 import CheckRequest
from kessel.rbac.v2 import fetch_default_workspace, principal_subject, workspace_resource
from requests.adapters import HTTPAdapter

_TIMEOUT = 5  # seconds, for the token fetch, the RBAC lookup and the gRPC call


class _TimeoutAdapter(HTTPAdapter):
    """The SDK fetches tokens without a timeout; default one so a hung SSO can't block forever."""

    def send(self, request, **kwargs):
        kwargs["timeout"] = kwargs.get("timeout") or _TIMEOUT
        return super().send(request, **kwargs)


@lru_cache(maxsize=1)
def _credentials():
    """OAuth2 client-credentials token source, or None when no credentials are configured (dev)."""
    if not (settings.KESSEL_CLIENT_ID and settings.KESSEL_CLIENT_SECRET):
        return None
    creds = OAuth2ClientCredentials(
        client_id=settings.KESSEL_CLIENT_ID,
        client_secret=settings.KESSEL_CLIENT_SECRET,
        token_endpoint=settings.KESSEL_TOKEN_URL,
        retry={"max_retries": 1},
    )
    # ponytail: reaches into the SDK's private OAuth2Session; drop once kessel-sdk accepts a timeout.
    creds._session.mount("https://", _TimeoutAdapter())
    creds._session.mount("http://", _TimeoutAdapter())
    return creds


@lru_cache(maxsize=1)
def _stub():
    creds = _credentials()
    builder = ClientBuilder(settings.KESSEL_URL)
    builder = builder.oauth2_client_authenticated(creds) if creds else builder.insecure()
    stub, _channel = builder.build()
    return stub


# Workspace ids don't change, so cache per org. lru_cache does not cache exceptions.
@lru_cache(maxsize=1024)
def _default_workspace_id(org_id):
    creds = _credentials()
    workspace = fetch_default_workspace(
        settings.KESSEL_RBAC_URL,
        org_id,
        auth=oauth2_auth_request(creds) if creds else None,
        timeout=_TIMEOUT,
    )
    return workspace.id


def check_permission(org_id, user_id, permission):
    """
    Return True only if Kessel answers ALLOWED_TRUE for ``permission`` on the org's default workspace.

    Raises on any transport/config error; the caller decides how to fail.
    """
    request = CheckRequest(
        object=workspace_resource(_default_workspace_id(org_id)),
        relation=permission,
        subject=principal_subject(user_id, "redhat"),
    )
    response = _stub().Check(request, timeout=_TIMEOUT)
    return response.allowed == ALLOWED_TRUE
