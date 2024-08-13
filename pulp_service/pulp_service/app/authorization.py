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


class DomainBasedPermission(BasePermission):
    """
    A Permission Class that grants permission to users who's org_id matches the requested Domain's org_id.
    """

    def has_permission(self, request, view):
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
            return True
        # User has permission if the org_id matches the domain's org_id
        else:
            domain_name = request.META['PATH_INFO'].removeprefix(settings.API_ROOT).split("/")[0]
            try:
                DomainOrg.objects.get(domain__name=domain_name, org_id=org_id)
                return True
            except DomainOrg.DoesNotExist:
                return False
