import json
import logging

from aiohttp import web
from base64 import b64decode
from frozenlist import FrozenList

from pulpcore.plugin.content import app

_logger = logging.getLogger(__name__)

@web.middleware
async def add_rh_org_id_resp_header(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        return exc
    
    _logger.info(f"REQUEST HEADERS: {request.headers}")

    if not request.headers.get("HTTP_X_RH_IDENTITY"):
        return response

    rh_identity_header = request.headers["HTTP_X_RH_IDENTITY"]
    rh_identity_header_decoded = b64decode(rh_identity_header)
    rh_identity_header_json = json.loads(rh_identity_header_decoded)

    # we need to check if the entire path exists because non-entitlement certs have a diff structure
    if "identity" in rh_identity_header_json and "org_id" in rh_identity_header_json["identity"]:
        response.headers["X-RH-ORG-ID"] = rh_identity_header_json["identity"]["org_id"]

    return response


app._middlewares = FrozenList([add_rh_org_id_resp_header, *app.middlewares])
