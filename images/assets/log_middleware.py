import base64
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(process)d] [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)


class UserExtractionMiddleware:
    """
    WSGI middleware to extract user from X-RH-IDENTITY header and set REMOTE_USER.
    This runs before gunicorn logs, so the user will be available in access logs.
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        # Extract user from X-RH-IDENTITY header if present
        rh_identity = environ.get("HTTP_X_RH_IDENTITY")
        username = None
        org_id = None

        if rh_identity:
            try:
                decoded = base64.b64decode(rh_identity)
                identity_data = json.loads(decoded)

                if "identity" in identity_data:
                    identity = identity_data["identity"]
                    # User details (highest priority - most specific)
                    if "user" in identity and "username" in identity["user"]:
                        username = identity["user"]["username"]
                    # Service account (x509 certificate)
                    elif "x509" in identity and "subject_dn" in identity["x509"]:
                        username = identity["x509"]["subject_dn"]
                    # SAML user
                    elif "associate" in identity and "email" in identity["associate"]:
                        username = identity["associate"]["email"]

                    # Org ID
                    if "org_id" in identity:
                        org_id = f"{identity['org_id']}"

                    if not username and not org_id:
                        log.warning(
                            "X-RH-IDENTITY present but neither username nor org_id could be derived from identity header."
                        )

            except Exception as e:
                log.error(
                    f"Failed to extract user from RH Identity header: {e}",
                    exc_info=True,
                )

        if username:
            environ["REMOTE_USER"] = username
        if org_id:
            environ["ORG_ID"] = org_id

        return self.app(environ, start_response)


def post_worker_init(worker):
    """
    Gunicorn hook to wrap the WSGI application after worker initialization.
    This is called after the worker has been initialized but before it starts serving requests.
    """
    log.info("Wrapping WSGI application with UserExtractionMiddleware")
    worker.wsgi = UserExtractionMiddleware(worker.wsgi)
