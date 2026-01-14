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


class RHEntitlementCertAuthentication(JSONHeaderRemoteAuthentication):

    header = "HTTP_X_RH_IDENTITY"
    # Combines org_id with username - returns null if either is missing
    jq_filter = '.identity | if .org_id and .user.username then "\(.org_id):\(.user.username)" else null end'

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

class DeprecateCRHCSA(JSONHeaderRemoteAuthentication):

    header = "HTTP_X_RH_IDENTITY"
    jq_filter = ".identity.service_account.username"

    def authenticate(self, request):
        if self.header not in request.META:
            return None

        try:
            header_content = request.META.get(self.header)
            header_decoded_content = b64decode(header_content)
        except Base64DecodeError:
            _logger.debug(_("Access not allowed - Header content is not Base64 encoded."))
            raise AuthenticationFailed(_("Access denied."))

        try:
            header_value = json.loads(header_decoded_content)
            json_path = jq.compile(self.jq_filter)

            remote_user = json_path.input_value(header_value).first()
        except json.JSONDecodeError:
            _logger.debug(_("Access not allowed - Invalid JSON."))
            raise AuthenticationFailed(_("Access denied. Invalid JSON."))

        if remote_user:
            _logger.warning(f"Deprecated c.rh.c SA authentication method attempted by user: {remote_user}")
            return None

        return None
