import json
import logging
from base64 import b64decode
from binascii import Error as Base64DecodeError

from django.contrib.auth import get_user_model

from pulpcore.app.authentication import JSONHeaderRemoteAuthentication
from pulpcore.plugin.models import Group

from pulp_service.app.constants import ORG_GROUP_PREFIX

_logger = logging.getLogger(__name__)


def _extract_org_id_from_header(request):
    """
    Extract org_id from X-RH-IDENTITY header

    Returns the org_id as a string or None if not present or invalid.
    """
    header_content = request.META.get("HTTP_X_RH_IDENTITY")
    if not header_content:
        return None

    try:
        header_decoded = b64decode(header_content)
        header_json = json.loads(header_decoded)
        identity = header_json.get("identity") or {}
        internal = identity.get("internal") or {}
        org_id = internal.get("org_id")
        if org_id is None:
            _logger.debug("X-RH-IDENTITY header did not contain identity.internal.org_id")
            return None
        return str(org_id)
    except (Base64DecodeError, json.JSONDecodeError, AttributeError, ValueError) as exc:
        _logger.debug("Could not extract org_id from X-RH-IDENTITY header: %s", exc)
        return None


def _assign_user_to_org_group(user, org_id):
    """
    Add user to rh-org-<org_id> group, creating the group if needed.

    This is idempotent: calling it multiple times for the same user+group
    is safe and adds no overhead after the first call.
    """
    if not org_id or not user or not user.is_authenticated:
        return

    # Normalize org_id to avoid accidental group proliferation from trivial variations.
    org_id = str(org_id).strip()
    if not org_id:
        return

    group_name = f"{ORG_GROUP_PREFIX}{org_id}"
    group, _ = Group.objects.get_or_create(name=group_name)

    # add is a no-op if the user is already in the group.
    user.groups.add(group)


class OrgGroupAssignmentMixin:
    """
    Mixin that auto-assigns an authenticated user to their rh-org-<org_id> group.

    Wraps the parent authentication backend's authenticate() so that a successful
    authentication also adds the user to the group derived from the X-RH-IDENTITY
    header. Keeps the org-group assignment logic in one place across auth classes.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            user, _ = result
            org_id = _extract_org_id_from_header(request)
            _assign_user_to_org_group(user, org_id)
        return result

    def authenticate_header(self, request):
        return "Bearer"


class RHServiceAccountCertAuthentication(OrgGroupAssignmentMixin, JSONHeaderRemoteAuthentication):
    header = "HTTP_X_RH_IDENTITY"
    jq_filter = ".identity.x509.subject_dn"


class RHTermsBasedRegistryAuthentication(OrgGroupAssignmentMixin, JSONHeaderRemoteAuthentication):
    header = "HTTP_X_RH_IDENTITY"
    # Combines org_id with username - falls back to "|username" if org_id is missing
    jq_filter = r'.identity | if .user.username then "\(.org_id // "")|\(.user.username)" else null end'


class TurnpikeTermsBasedRegistryAuthentication(OrgGroupAssignmentMixin, JSONHeaderRemoteAuthentication):
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
        'if .identity.auth_type == "registry-auth" and .identity.registry.username '
        r'then "\(.identity.registry.org_id // "")|\(.identity.registry.username)" '
        "else null end"
    )


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
        User = get_user_model()  # noqa: N806
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            _logger.warning("User with id %s not found in get_user()", user_id)
            return None
