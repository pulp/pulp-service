import json
import logging
from base64 import b64decode
from binascii import Error as Base64DecodeError

import jq
from django.db.models import Q
from django.http import Http404
from rest_framework.permissions import SAFE_METHODS

from pulpcore.app.access_policy import AccessPolicyFromDB
from pulpcore.plugin.models import Domain
from pulpcore.plugin.util import get_domain_pk

from pulp_service.app.models import DomainOrg

_logger = logging.getLogger(__name__)
_org_id_json_path = jq.compile(".identity.internal.org_id")


class PulpServiceAccessPolicy(AccessPolicyFromDB):
    """
    Access policy for pulp-service that layers cross-cutting permission checks
    on top of pulpcore's standard RBAC evaluation.

    Pre-check order:
        1. Superuser bypass
        2. Public domain anonymous read (safe methods on public-* domains)
        3. PyPi content guard delegation
        4. Fall through to standard RBAC (super().has_permission)
    """

    def has_permission(self, request, view):
        if request.user.is_superuser:
            return True

        if request.method in SAFE_METHODS:
            domain = getattr(request, "pulp_domain", None)

            if domain and domain.name.startswith("public-"):
                return True

            pypi_access = self._check_pypi_safe_method_access(request, view, domain)
            if pypi_access is not None:
                return pypi_access

        return super().has_permission(request, view)

    def scope_queryset(self, view, qs):
        qs = super().scope_queryset(view, qs)
        if qs.model is Domain:
            public_domains = Domain.objects.filter(name__startswith="public-")
            qs = (qs | public_domains).distinct()

        return qs

    def _check_pypi_safe_method_access(self, request, view, domain):
        """
        Returns True/False for a SAFE_METHOD request to a PyPi view, or
        None if the view isn't a PyPi view (caller should fall through to RBAC)
        """
        from pulp_python.app.pypi.views import PyPIMixin

        if not isinstance(view, PyPIMixin):
            return None

        try:
            distribution = view.distribution
        except Http404:
            return True
        except Exception:
            _logger.exception("Unexpected error resolving distribution for PyPI permission check")
            return False

        guard = distribution.content_guard
        if not guard:
            return True

        user = request.user
        domain_pk = domain.pk if domain is not None else get_domain_pk()
        decoded_header = self._get_decoded_identity_header(request)
        org_id = self._get_org_id(decoded_header)

        if user.is_authenticated and self._has_domain_access(domain_pk, org_id, user):
            _logger.info(
                "Content-guarded PyPI access GRANTED via DomainOrg: user=%s org_id=%s",
                user,
                org_id,
            )
            return True

        return self._evaluate_content_guard(guard, request, org_id, user)

    def _evaluate_content_guard(self, guard, request, org_id, user):
        try:
            casted_guard = guard.cast()
        except Exception:
            _logger.exception("Failed to resolve content guard type for distribution")
            return False

        try:
            casted_guard.permit(request)
            _logger.info(
                "Content-guarded PyPI access GRANTED via content guard: org_id=%s user=%s",
                org_id,
                user,
            )
            return True
        except PermissionError:
            _logger.info(
                "Content-guarded PyPI access DENIED via content guard: org_id=%s user=%s",
                org_id,
                user,
            )
            return False
        except Exception:
            _logger.exception("Unexpected error evaluating content guard permit")
            return False

    @staticmethod
    def _has_domain_access(domain_pk, org_id, user):
        query = Q(domains__pk=domain_pk, user=user)

        group_pks = list(user.groups.values_list("pk", flat=True))
        if group_pks:
            query |= Q(domains__pk=domain_pk, group_id__in=group_pks)

        if org_id is not None:
            query |= Q(domains__pk=domain_pk, org_id=org_id)

        return DomainOrg.objects.filter(query).exists()

    @staticmethod
    def _get_decoded_identity_header(request):
        try:
            header_content = request.META.get("HTTP_X_RH_IDENTITY")
            if header_content:
                return b64decode(header_content)
        except Base64DecodeError:
            _logger.warning("Failed to decode X-RH-IDENTITY header: invalid base64 content")
            return None
        return None

    @staticmethod
    def _get_org_id(decoded_header_content):
        if decoded_header_content:
            try:
                header_value = json.loads(decoded_header_content)
                return _org_id_json_path.input_value(header_value).first()
            except json.JSONDecodeError:
                return None
        return None
