import ipaddress
import json
from base64 import b64decode

from aiohttp import web
from frozenlist import FrozenList

from pulpcore.plugin.content import app


def _is_valid_ip(ip_str):
    """
    Validate that a string is a valid IPv4 or IPv6 address.

    Returns True if valid, False otherwise.
    """
    try:
        ipaddress.ip_address(ip_str.strip())
        return True
    except ValueError:
        return False


class HeadersWithModifiedXForwardedFor:
    """
    Wrapper around aiohttp headers that returns modified X-Forwarded-For.

    This allows us to prepend True-Client-IP to X-Forwarded-For without
    modifying the immutable headers object or using private APIs.
    """

    def __init__(self, original_headers, modified_xff):
        self._original = original_headers
        self._modified_xff = modified_xff

    def get(self, key, default=None):
        if key.lower() == "x-forwarded-for":
            return self._modified_xff
        return self._original.get(key, default)

    def __getitem__(self, key):
        if key.lower() == "x-forwarded-for":
            return self._modified_xff
        return self._original[key]

    def __contains__(self, key):
        return key in self._original

    def __iter__(self):
        return iter(self._original)

    def __len__(self):
        return len(self._original)

    def getall(self, key, default=None):
        if key.lower() == "x-forwarded-for":
            return [self._modified_xff]
        return self._original.getall(key, default)

    def getone(self, key):
        if key.lower() == "x-forwarded-for":
            return self._modified_xff
        return self._original.getone(key)

    def keys(self):
        return self._original.keys()

    def values(self):
        return self._original.values()

    def items(self):
        return self._original.items()


@web.middleware
async def add_true_client_ip_to_forwarded_for(request, handler):
    """
    Prepends True-Client-IP header value to X-Forwarded-For for logging.

    Akamai CDN sends the original client IP in True-Client-IP header.
    This middleware adds it to the beginning of X-Forwarded-For chain
    so it appears in access logs without changing the log format.

    Validates True-Client-IP is a valid IP address before using it.
    """
    true_client_ip = request.headers.get("True-Client-IP")
    if true_client_ip and _is_valid_ip(true_client_ip):
        x_forwarded_for = request.headers.get("X-Forwarded-For", "")
        if x_forwarded_for:
            # Prepend True-Client-IP to existing X-Forwarded-For chain
            modified_xff = f"{true_client_ip.strip()}, {x_forwarded_for}"
        else:
            # Set X-Forwarded-For to True-Client-IP if it doesn't exist
            modified_xff = true_client_ip.strip()

        # Replace request.headers with wrapper that returns modified X-Forwarded-For
        request._cache["headers"] = HeadersWithModifiedXForwardedFor(request.headers, modified_xff)

    return await handler(request)


def _set_org_id_header(request, response):
    """Extract org_id from x-rh-identity and set it on the response header."""
    rh_identity_header = request.headers.get("x-rh-identity")
    if not rh_identity_header:
        return

    try:
        identity = json.loads(b64decode(rh_identity_header))
    except Exception:
        return

    # non-entitlement certs (x509, SAML) have a different structure without identity.org_id
    if "identity" in identity and "org_id" in identity["identity"]:
        response.headers["X-RH-ORG-ID"] = identity["identity"]["org_id"]


@web.middleware
async def add_rh_org_id_resp_header(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        _set_org_id_header(request, exc)
        return exc

    _set_org_id_header(request, response)
    return response


app._middlewares = FrozenList([add_true_client_ip_to_forwarded_for, add_rh_org_id_resp_header, *app.middlewares])
