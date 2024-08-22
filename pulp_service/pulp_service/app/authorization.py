from contextvars import ContextVar
from base64 import b64decode
from binascii import Error as Base64DecodeError
import json
import jq
from pulpcore.app import settings
from pulp_service.app.models import DomainOrg
from rest_framework.permissions import BasePermission
import logging


_logger = logging.getLogger(__name__)
org_id_var = ContextVar('org_id')
org_id_json_path = jq.compile(".identity.internal.org_id")

user_id_var = ContextVar("user_id")


class DomainBasedPermission(BasePermission):
    """
    A Permission Class that grants permission to users who's org_id matches the requested Domain's org_id.
    """

    def has_permission(self, request, view):
        # Admins have all permissions
        if request.user.is_superuser:
            return True
        # Decode the identity header
        try:
            header_content = request.META.get('HTTP_X_RH_IDENTITY')
            if header_content:
                header_decoded_content = b64decode(header_content)
                # Temporarily log the header for debugging purposes
                _logger.info(header_decoded_content)
            else:
                return False
        except Base64DecodeError:
            return False
        # Get the Org ID from the header
        try:
            header_value = json.loads(header_decoded_content)
            org_id = org_id_json_path.input_value(header_value).first()
        except json.JSONDecodeError:
            return False

        # Anyone can create a domain
        if request.META['PATH_INFO'].endswith('/default/api/v3/domains/') and request.META['REQUEST_METHOD'] == 'POST':
            org_id_var.set(org_id)
            user_id_var.set(request.user.pk)
            return True
        # User has permission if the org_id matches the domain's org_id or the user has group perms
        else:
            checker = DomainPermissionChecker(request, org_id)
            return checker.has_permissions()


class DomainPermissionChecker:
    def __init__(self, request, org_id):
        self.user = request.user
        self.domain_name = request.META['PATH_INFO'].removeprefix(settings.API_ROOT).split("/")[0]
        self.org_id = org_id

    def has_permissions(self):
        return self.has_org_id_perms() or self.has_user_perms() or self.has_group_perms()

    def has_org_id_perms(self):
        return DomainOrg.objects.filter(domain__name=self.domain_name, org_id=self.org_id).exists()

    def has_user_perms(self):
        return DomainOrg.objects.filter(domain__name=self.domain_name, user=self.user).exists()

    def has_group_perms(self):
        return DomainOrg.objects.filter(domain_name=self.domain_name, group__in=self.user.groups).exists()
