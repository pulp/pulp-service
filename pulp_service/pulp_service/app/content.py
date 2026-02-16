import ipaddress
import json
from aiohttp import web
from base64 import b64decode
from frozenlist import FrozenList
from multidict import CIMultiDictProxy

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
        request._cache['headers'] = HeadersWithModifiedXForwardedFor(
            request.headers,
            modified_xff
        )

    return await handler(request)


@web.middleware
async def add_rh_org_id_resp_header(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        return exc

    if not request.headers.get("x-rh-identity"):
        return response

    rh_identity_header = request.headers["x-rh-identity"]
    rh_identity_header_decoded = b64decode(rh_identity_header)
    rh_identity_header_json = json.loads(rh_identity_header_decoded)

    # we need to check if the entire path exists because non-entitlement certs have a diff structure
    if "identity" in rh_identity_header_json and "org_id" in rh_identity_header_json["identity"]:
        response.headers["X-RH-ORG-ID"] = rh_identity_header_json["identity"]["org_id"]

    return response


app._middlewares = FrozenList([
    add_true_client_ip_to_forwarded_for,
    add_rh_org_id_resp_header,
    *app.middlewares
])
