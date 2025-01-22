from contextvars import ContextVar
from base64 import b64decode
from binascii import Error as Base64DecodeError
from django.db.models import Q
import json
import jq
from pulpcore.app.util import get_domain
from pulpcore.plugin.util import extract_pk
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

        user = request.user

        # Anonymous users have no permissions
        if not user.is_authenticated:
            return False

        # Check if user is creating a domain or creating a resource within a domain
        action = self.get_user_action(request)

        # Decode the Red Hat Identity header
        decoded_header_content = self.get_decoded_identity_header(request)

        # Get the Org ID from the Red Hat Identity header
        org_id = self.get_org_id(decoded_header_content)

        # Anyone can create a domain
        if action == "domain_create":
            if decoded_header_content:
                org_id_var.set(org_id)
            user_id_var.set(request.user.pk)
            return True
        # Anyone can list domains
        elif action == "domain_list":
            return True
        elif action == "domain_update":
            domain_pk = extract_pk(request.META['PATH_INFO'])
            return DomainOrg.objects.filter(Q(domain__pk=domain_pk, org_id=org_id) | Q(domain__pk=domain_pk, user=user)).exists()
        elif action == "domain_delete":
            domain_pk = extract_pk(request.META['PATH_INFO'])
            return DomainOrg.objects.filter(Q(domain__pk=domain_pk, org_id=org_id) | Q(domain__pk=domain_pk, user=user)).exists()
        # User has permission if the org_id matches the domain's org_id
        # The user that created the domain has permission to access that domain
        return DomainOrg.objects.filter(Q(domain=get_domain(), org_id=org_id) | Q(domain=get_domain(), user=user)).exists()

    def get_user_action(self, request):
        if request.META['PATH_INFO'].endswith('/default/api/v3/domains/'):
            if request.META['REQUEST_METHOD'] == 'POST':
                return "domain_create"
            elif request.META['REQUEST_METHOD'] == 'GET':
                return "domain_list"
        elif request.META['PATH_INFO'].startswith('/api/pulp/default/api/v3/domains/'):
            if request.META['REQUEST_METHOD'] == 'PATCH':
                return "domain_update"
            elif request.META['REQUEST_METHOD'] == 'DELETE':
                return "domain_delete"
        else:
            return "domain_operation"

    def get_decoded_identity_header(self, request):
        try:
            header_content = request.META.get('HTTP_X_RH_IDENTITY')
            if header_content:
                header_decoded_content = b64decode(header_content)
                # Temporarily log the header for debugging purposes
                _logger.info(header_decoded_content)
                return header_decoded_content
        except Base64DecodeError:
            return

    def get_org_id(self, decoded_header_content):
        if decoded_header_content:
            try:
                header_value = json.loads(decoded_header_content)
                return org_id_json_path.input_value(header_value).first()
            except json.JSONDecodeError:
                return
