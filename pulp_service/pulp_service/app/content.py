import json
from aiohttp import web
from base64 import b64decode
from frozenlist import FrozenList

from pulpcore.plugin.content import app


@web.middleware
async def add_true_client_ip_to_forwarded_for(request, handler):
    """
    Prepends True-Client-IP header value to X-Forwarded-For for logging.

    Akamai CDN sends the original client IP in True-Client-IP header.
    This middleware adds it to the beginning of X-Forwarded-For chain
    so it appears in access logs without changing the log format.
    """
    true_client_ip = request.headers.get("True-Client-IP")
    if true_client_ip:
        x_forwarded_for = request.headers.get("X-Forwarded-For", "")
        if x_forwarded_for:
            # Prepend True-Client-IP to existing X-Forwarded-For chain
            # Note: aiohttp request.headers is immutable, but we can modify via internal dict
            request._headers["X-Forwarded-For"] = f"{true_client_ip}, {x_forwarded_for}"
        else:
            # Set X-Forwarded-For to True-Client-IP if it doesn't exist
            request._headers["X-Forwarded-For"] = true_client_ip

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
