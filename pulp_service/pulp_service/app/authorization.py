import json
import logging
from base64 import b64decode
from binascii import Error as Base64DecodeError
from contextvars import ContextVar

import jq
from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, BasePermission

_logger = logging.getLogger(__name__)
org_id_var = ContextVar("org_id")
org_id_json_path = jq.compile(".identity.internal.org_id")

user_id_var = ContextVar("user_id")
group_var = ContextVar("group")


def set_domain_create_context(request):
    """
    Populate the ContextVars the post_create_domain signal (signals.py) consumes to assign
    the RBAC domain roles.

    Called for both domain-create paths: CreateDomainView (self-service) and the generic
    pulpcore DomainViewSet create (from PulpServiceAccessPolicy.has_permission).
    """
    header = request.META.get("HTTP_X_RH_IDENTITY")
    org_id = None
    if header:
        try:
            org_id = org_id_json_path.input_value(json.loads(b64decode(header))).first()
        except (Base64DecodeError, json.JSONDecodeError):
            org_id = None
    # Set unconditionally: a basic-auth create with no X-RH-IDENTITY must clear org_id_var, not
    # inherit a stale value left in this worker's context by an earlier request.
    org_id_var.set(org_id)
    user_id_var.set(request.user.pk)


class IsAdminOrAdminReadOnly(BasePermission):
    """
    Full access for superusers/staff; GET/HEAD/OPTIONS-only for members of the
    admin-readonly group (configured via ADMIN_READONLY_GROUP setting).
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_staff or request.user.is_superuser:
            return True

        if request.method in SAFE_METHODS:
            group_name = settings.ADMIN_READONLY_GROUP
            if group_name and request.user.groups.filter(name=group_name).exists():
                return True

        return False
