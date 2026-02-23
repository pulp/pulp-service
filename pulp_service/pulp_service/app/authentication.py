import jq
import json
import logging

from base64 import b64decode
from binascii import Error as Base64DecodeError
from django.contrib.auth import get_user_model
from gettext import gettext as _
from pulpcore.app.authentication import JSONHeaderRemoteAuthentication
from rest_framework.exceptions import AuthenticationFailed


_logger = logging.getLogger(__name__)


class RHServiceAccountCertAuthentication(JSONHeaderRemoteAuthentication):

    header = "HTTP_X_RH_IDENTITY"
    jq_filter = ".identity.x509.subject_dn"

    def authenticate_header(self, request):
        return "Bearer"


class RHTermsBasedRegistryAuthentication(JSONHeaderRemoteAuthentication):

    header = "HTTP_X_RH_IDENTITY"
    # Combines org_id with username - returns null if either is missing
    jq_filter = '.identity | if .org_id and .user.username then "\(.org_id)|\(.user.username)" else null end'

    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        if self.header not in request.META:
            _logger.debug(f"Header {self.header} not present in request")
            return None

        header_content = request.META.get(self.header)
        _logger.debug(f"Raw header content (base64): {header_content}")

        try:
            header_decoded_content = b64decode(header_content)
            _logger.debug(f"Decoded header content: {header_decoded_content.decode('utf-8')}")
        except Base64DecodeError:
            _logger.debug(_("Access not allowed - Header content is not Base64 encoded."))
            raise AuthenticationFailed(_("Access denied."))

        # Call parent authenticate to continue with the standard flow
        return super().authenticate(request)


class TurnpikeTermsBasedRegistryAuthentication(JSONHeaderRemoteAuthentication):
    """
    Authenticate users from Turnpike registry-auth X-RH-IDENTITY headers.

    Turnpike passes credentials in a different identity format than the standard
    RH identity header used by RHTermsBasedRegistryAuthentication:

        {"identity": {"type": "Registry", "auth_type": "registry-auth",
                      "registry": {"org_id": "...", "username": "..."}}}

    Returns null for other identity formats, letting DRF fall through to
    the next authentication class.
    """

    header = "HTTP_X_RH_IDENTITY"
    jq_filter = (
        'if (.identity.auth_type // empty) == "registry-auth" '
        'then "\(.identity.registry.org_id)|\(.identity.registry.username)" '
        'else null end'
    )

    def authenticate_header(self, request):
        return "Bearer"


class RHSamlAuthentication(JSONHeaderRemoteAuthentication):
    """
    Authenticate users via SAML email from RH Identity header.
    Used for pulp-mgmt admin interface with session support.
    """

    header = "HTTP_X_RH_IDENTITY"
    jq_filter = ".identity.associate.email"

    def get_user(self, user_id):
        """
        Required method for Django authentication backends.
        Returns a user instance given a user_id (primary key).
        """
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            _logger.warning(f"User with id {user_id} not found in get_user()")
            return None
