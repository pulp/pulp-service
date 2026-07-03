import json
import logging
from base64 import b64decode
from binascii import Error as Base64DecodeError
from contextvars import ContextVar

import jq
from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from pulpcore.plugin.models import Domain
from pulpcore.plugin.util import extract_pk, get_domain_pk

from pulp_service.app.models import DomainOrg, FeatureContentGuard

_logger = logging.getLogger(__name__)
org_id_var = ContextVar("org_id")
org_id_json_path = jq.compile(".identity.internal.org_id")

user_id_var = ContextVar("user_id")
group_var = ContextVar("group")

# The domain whose PyPI views require the lightwell-network feature entitlement (see
# has_permission()). Other domains' PyPI views keep the pre-existing "any SAFE_METHOD
# request is allowed" behavior.
LIGHTWELL_DOMAIN_NAME = "lightwell"
# Feature entitlement required to read the lightwell domain's PyPI views (simple API, etc.)
# for orgs that don't have a DomainOrg association with the domain. See has_permission().
LIGHTWELL_NETWORK_FEATURE = "lightwell-network"
# Hardcoded group whose members get read-only (SAFE_METHODS) access to the lightwell
# domain's non-PyPI endpoints (repository/content/artifact listing, Pulp REST API, Maven
# content API), independent of any DomainOrg association. This is a plain Group -- there is
# no DomainOrg row backing this access, unlike the normal group-based access model. It does
# NOT grant write access, and it does NOT apply to PyPI views, which are still gated
# exclusively by the lightwell-network feature check (see _check_pypi_safe_method_access()).
LIGHTWELL_READONLY_GROUP_NAME = "Lightwell-ReadOnly"


class DomainBasedPermission(BasePermission):
    """
    A Permission Class that grants permission to users who's org_id matches the requested Domain's org_id.
    """

    def _has_domain_access(self, domain_pk, org_id, user):
        """
        Checks if a user has access to a domain based on user, group membership, or org_id.
        """
        query = Q(domains__pk=domain_pk, user=user)

        group_pks = list(user.groups.values_list("pk", flat=True))
        if group_pks:
            query |= Q(domains__pk=domain_pk, group_id__in=group_pks)

        if org_id is not None:
            query |= Q(domains__pk=domain_pk, org_id=org_id)

        return DomainOrg.objects.filter(query).exists()

    def _has_pypi_read_access(self, request, domain):
        """
        Checks SAFE_METHOD access to the lightwell domain's PyPI views.

        Users with a DomainOrg association bypass the feature check entirely (existing
        permission model). Everyone else -- including unauthenticated users -- must belong
        to an org that has the lightwell-network feature entitlement.
        """
        user = request.user
        domain_pk = domain.pk if domain is not None else get_domain_pk()

        decoded_header_content = self.get_decoded_identity_header(request)
        org_id = self.get_org_id(decoded_header_content)

        if user.is_authenticated and self._has_domain_access(domain_pk, org_id, user):
            _logger.info("lightwell PyPI access GRANTED via DomainOrg: user=%s org_id=%s", user, org_id)
            return True

        if org_id is None:
            _logger.info(
                "lightwell PyPI access DENIED: no org_id in identity header and no DomainOrg match (user=%s)",
                user,
            )
            return False

        allowed = self._has_lightwell_network_feature(org_id)
        _logger.info(
            "lightwell PyPI access %s via %s feature check: org_id=%s user=%s",
            "GRANTED" if allowed else "DENIED",
            LIGHTWELL_NETWORK_FEATURE,
            org_id,
            user,
        )
        return allowed

    def _has_lightwell_network_feature(self, org_id):
        """
        Checks (with caching) whether `org_id` has the lightwell-network feature, via the
        same cache/lookup mechanism as FeatureContentGuard.
        """
        guard = FeatureContentGuard(features=[LIGHTWELL_NETWORK_FEATURE], pulp_domain=None)
        try:
            return guard.check_feature(org_id)
        except PermissionError:
            return False

    def _has_lightwell_readonly_group_access(self, user):
        """
        Members of the hardcoded LIGHTWELL_READONLY_GROUP_NAME group get read-only
        (SAFE_METHODS) access to the lightwell domain's non-PyPI endpoints, independent of
        any DomainOrg association. Unlike normal groups (linked to a domain via DomainOrg,
        which grant unrestricted read+write access), this is a standalone check based purely
        on group membership + the hardcoded lightwell domain name -- see has_permission() and
        scope_queryset() for where this is applied.
        """
        return user.is_authenticated and user.groups.filter(name=LIGHTWELL_READONLY_GROUP_NAME).exists()

    def _check_pypi_safe_method_access(self, request, view, domain):
        """
        Returns True/False for a SAFE_METHOD request to a PyPI `view`, or None if `view`
        isn't a PyPI view (the caller should then fall through to the standard
        DomainOrg-based checks below).

        Only the lightwell domain's PyPI views require a DomainOrg association or the
        lightwell-network feature entitlement. Other domains' PyPI views keep the
        pre-existing "any SAFE_METHOD request is allowed" behavior; non-PyPI endpoints
        (Maven, repository listing, Pulp REST API, ...) never hit this method.
        """
        from pulp_python.app.pypi.views import PyPIMixin

        if not isinstance(view, PyPIMixin):
            return None

        if domain and domain.name == LIGHTWELL_DOMAIN_NAME:
            return self._has_pypi_read_access(request, domain)
        return True

    def _check_safe_method_access(self, request, view, domain, user):
        """
        Returns True/False for a SAFE_METHOD request, or None if none of the SAFE_METHOD
        shortcuts (public domains, PyPI views, the lightwell read-only group) apply -- the
        caller should then fall through to the standard DomainOrg-based checks below.
        """
        if domain and domain.name.startswith("public-"):
            return True

        pypi_access = self._check_pypi_safe_method_access(request, view, domain)
        if pypi_access is not None:
            return pypi_access

        if domain and domain.name == LIGHTWELL_DOMAIN_NAME and self._has_lightwell_readonly_group_access(user):
            return True

        return None

    def has_permission(self, request, view):  # noqa: PLR0911
        # Admins have all permissions
        if request.user.is_superuser:
            return True

        user = request.user

        if request.method in SAFE_METHODS:
            domain = getattr(request, "pulp_domain", None)
            safe_method_access = self._check_safe_method_access(request, view, domain, user)
            if safe_method_access is not None:
                return safe_method_access

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
        if action == "domain_list":
            return True
        if action == "domain_update" or action == "domain_delete":
            # The PK is part of the URL
            domain_pk = extract_pk(request.META["PATH_INFO"])
            return self._has_domain_access(domain_pk, org_id, user)
        # User has permission if the org_id matches the domain's org_id
        # The user that created the domain has permission to access that domain
        # The domain name is part of the URL, not the PK.
        domain_pk = get_domain_pk()
        return self._has_domain_access(domain_pk, org_id, user)

    def get_user_action(self, request):  # noqa: PLR0911
        view_name = request.resolver_match.view_name
        method = request.META["REQUEST_METHOD"]

        # Map view names to actions
        if view_name in ["domains-list"]:
            if method == "POST":
                return "domain_create"
            if method == "GET":
                return "domain_list"
        elif view_name in ["domains-set-label", "domains-unset-label"]:
            return "domain_update"
        elif view_name == "domains-detail":
            if method in ["PATCH", "DELETE"]:
                return "domain_update" if method == "PATCH" else "domain_delete"
        elif view_name in ["create-domain", "pulp_service.app.viewsets.CreateDomainView"]:
            return "domain_create"
        elif view_name in ["migrate-domain", "pulp_service.app.viewsets.MigrateDomainView"]:
            return "domain_update"
        else:
            return "domain_operation"
        return None

    def get_decoded_identity_header(self, request):
        try:
            header_content = request.META.get("HTTP_X_RH_IDENTITY")
            if header_content:
                header_decoded_content = b64decode(header_content)
                return header_decoded_content
        except Base64DecodeError:
            return None

    def get_org_id(self, decoded_header_content):
        if decoded_header_content:
            try:
                header_value = json.loads(decoded_header_content)
                return org_id_json_path.input_value(header_value).first()
            except json.JSONDecodeError:
                return None
        return None

    def scope_queryset(self, view, qs):
        """
        Filter Domain querysets to only show domains the user has DomainOrg access to.
        """
        if qs.model is not Domain:
            return qs

        request = view.request
        user = request.user

        if user.is_superuser:
            return qs

        if not user.is_authenticated:
            return qs.none()

        decoded_header = self.get_decoded_identity_header(request)
        org_id = self.get_org_id(decoded_header)

        query = Q(domain_orgs__user=user)

        group_pks = list(user.groups.values_list("pk", flat=True))
        if group_pks:
            query |= Q(domain_orgs__group_id__in=group_pks)

        if org_id is not None:
            query |= Q(domain_orgs__org_id=org_id)

        if self._has_lightwell_readonly_group_access(user):
            query |= Q(name=LIGHTWELL_DOMAIN_NAME)

        return qs.filter(query).distinct()
