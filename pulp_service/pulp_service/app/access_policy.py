import json
import logging
from base64 import b64decode
from binascii import Error as Base64DecodeError

import jq
from django.conf import settings
from django.db.models import Q
from django.http import Http404
from rest_framework.permissions import SAFE_METHODS

from pulpcore.app.access_policy import AccessPolicyFromSettings
from pulpcore.plugin.models import Content, Domain
from pulpcore.plugin.util import get_domain_pk

from pulp_service.app.authorization import set_domain_create_context
from pulp_service.app.features_service import check_subscription
from pulp_service.app.models import DomainOrg

_logger = logging.getLogger(__name__)
_org_id_json_path = jq.compile(".identity.internal.org_id")


class PulpServiceAccessPolicy(AccessPolicyFromSettings):
    """
    Access policy for pulp-service that layers cross-cutting permission checks
    on top of pulpcore's settings-based RBAC evaluation.

    Inheriting from AccessPolicyFromSettings makes get_access_policy read each
    viewset's policy from settings.ACCESS_POLICIES[<urlpattern>], falling back to
    the viewset's DEFAULT_ACCESS_POLICY. The content list-all endpoint (urlpattern
    "content") is overridden in settings to gate reads on core.view_content and drop
    repository-based queryset scoping.

    Pre-check order:
        1. Superuser bypass
        2. Public domain anonymous read (safe methods on public-* domains)
        3. PyPi content guard delegation
        4. DOMAIN_ACCESS_POLICIES grants (subscription feature / readonly group, safe methods)
        5. admin-readonly group read (safe methods)
        6. Fall through to standard RBAC (super().has_permission)
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

            # DOMAIN_ACCESS_POLICIES: subscription-feature and readonly-group read grants.
            if domain:
                policy = self._get_domain_policies().get(domain.name, {})
                if policy and self._check_domain_policy(request, domain, request.user, policy):
                    return True

            # admin-readonly members can read any endpoint the checks above did not decide.
            # Content guards still win, because a guarded PyPI view returns an explicit
            # allow/deny above.
            if self._is_admin_readonly(request.user):
                return True

        # Generic pulpcore DomainViewSet create (POST domains-list): populate the ContextVars the
        # post_create_domain signal (signals.py) consumes for the DomainOrg/RBAC dual-write.
        # CreateDomainView sets these itself; the generic endpoint used to get them from the
        # DomainBasedPermission default, which no longer exists. Without this, a domain created
        # here gets no DomainOrg row and no owner roles, so it is invisible to its own org under
        # RBAC. set_domain_create_context overwrites both vars unconditionally, so a create that
        # is denied below cannot leak this request's principal into the next create.
        if self._is_domain_create(request):
            set_domain_create_context(request)

        return super().has_permission(request, view)

    @staticmethod
    def _is_domain_create(request):
        """True for a POST to the generic pulpcore DomainViewSet create endpoint (domains-list)."""
        if request.method in SAFE_METHODS:
            return False
        match = getattr(request, "resolver_match", None)
        return bool(match and match.view_name == "domains-list")

    @staticmethod
    def _is_public_domain_read(request):
        """
        True for a safe-method read against a public-* domain (world-readable).

        Mirrors the has_permission public-* bypass so all read layers agree: without this,
        has_permission allows the read but scope_queryset filters the object out of the
        queryset, and get_object_or_404 raises 404.
        """
        if request is None or getattr(request, "method", None) not in SAFE_METHODS:
            return False
        domain = getattr(request, "pulp_domain", None)
        return bool(domain and domain.name.startswith("public-"))

    @staticmethod
    def _is_domain_content_read(view, qs):
        """
        True for a safe-method read of a Content queryset by a caller holding domain-scoped
        core.view_content.

        pulpcore's default content scope_queryset filters to content in a viewable repository,
        which hides orphan content (uploaded, not yet added to any repository) and 404s the read.
        The settings-based content-list policy drops that scoping only for the generic/file
        endpoints; this generalizes it to every typed content viewset (rpm/python/container/...)
        so a domain member can read their own not-yet-in-a-repo content.
        """
        request = getattr(view, "request", None)
        if request is None or getattr(request, "method", None) not in SAFE_METHODS:
            return False
        model = getattr(qs, "model", None)
        if model is None or not issubclass(model, Content):
            return False
        domain = getattr(request, "pulp_domain", None)
        user = getattr(request, "user", None)
        if domain is None or user is None:
            return False
        return bool(user.has_perm("core.view_content", obj=domain))

    def scope_queryset(self, view, qs):
        # Public domains are world-readable on safe methods. base.py has already filtered qs
        # to request.pulp_domain, so returning it unscoped exposes only this domain's objects
        # (no cross-domain leak) and lets detail reads resolve instead of 404ing.
        if self._is_public_domain_read(getattr(view, "request", None)):
            return qs

        # Domain members with core.view_content can read all content in their domain, including
        # orphan content not yet in a repository. base.py has already scoped qs to the request
        # domain, so returning it unscoped stays within the caller's domain (no cross-domain leak).
        if self._is_domain_content_read(view, qs):
            return qs

        request = getattr(view, "request", None)
        user = getattr(request, "user", None)
        is_safe_read = request is not None and getattr(request, "method", None) in SAFE_METHODS

        # The cross-cutting SAFE-method read grants that has_permission honours (admin-readonly
        # and DOMAIN_ACCESS_POLICIES subscription/readonly-group) must be honoured here too:
        # otherwise the grantee passes has_permission (200) but the default RBAC scoping below
        # filters the queryset to empty because they hold no per-object role. For domain-scoped
        # models base.py has already filtered the queryset to the request domain, so returning it
        # unscoped stays within that domain (no cross-domain leak).
        if is_safe_read and user is not None:
            # admin-readonly is a global read group (support/break-glass), so it reads every model.
            if self._is_admin_readonly(user):
                return qs
            # A DOMAIN_ACCESS_POLICIES grant is scoped to a single domain, so it may only return
            # the queryset unscoped for models base.py domain-scoped (those with a pulp_domain FK).
            # Global models (Domain, Group, User, Role) are NOT domain-scoped by base.py; returning
            # them unscoped would leak every tenant's rows. They fall through: Domain is handled by
            # the qs.model is Domain block below (readonly-group members see only their policy
            # domain + public-*); the rest get standard per-object RBAC scoping.
            domain = getattr(request, "pulp_domain", None)
            if domain and hasattr(qs.model, "pulp_domain"):
                policy = self._get_domain_policies().get(domain.name, {})
                if policy and self._check_domain_policy(request, domain, user, policy):
                    return qs

        qs = super().scope_queryset(view, qs)
        if qs.model is Domain:
            extra = Domain.objects.filter(name__startswith="public-")
            # DOMAIN_ACCESS_POLICIES readonly-group members see the policy's domain in listings.
            for domain_name, policy in self._get_domain_policies().items():
                group = policy.get("readonly_group")
                if group and user is not None and user.is_authenticated and user.groups.filter(name=group).exists():
                    extra = extra | Domain.objects.filter(name=domain_name)
            qs = (qs | extra).distinct()

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

    @staticmethod
    def _is_admin_readonly(user):
        """True if user is an authenticated member of the ADMIN_READONLY_GROUP."""
        group_name = settings.ADMIN_READONLY_GROUP
        return bool(group_name and user and user.is_authenticated and user.groups.filter(name=group_name).exists())

    @staticmethod
    def _get_domain_policies():
        return getattr(settings, "DOMAIN_ACCESS_POLICIES", {})

    def _check_domain_policy(self, request, domain, user, policy):
        """
        Returns True if a DOMAIN_ACCESS_POLICIES entry grants read access, else None.

        ``subscription_endpoints`` are path prefixes: a policy applies when the request path
        (after stripping the domain routing prefix) starts with one of them, and the caller's
        org holds the ``subscription_feature``. ``readonly_group`` grants read to any
        authenticated member of the named group.
        """
        subscription_feature = policy.get("subscription_feature")
        subscription_endpoints = policy.get("subscription_endpoints", [])
        if subscription_feature and subscription_endpoints:
            path = request.path_info
            if domain:
                api_root = getattr(settings, "API_ROOT", "/api/pulp/")
                domain_prefix = f"{api_root.rstrip('/')}/{domain.name}/"
                if path.startswith(domain_prefix):
                    path = "/" + path[len(domain_prefix) :]
            if any(path.startswith(endpoint) for endpoint in subscription_endpoints):
                decoded_header = self._get_decoded_identity_header(request)
                org_id = self._get_org_id(decoded_header)
                try:
                    if org_id and check_subscription(org_id, [subscription_feature]):
                        return True
                except Exception:
                    _logger.exception("Unexpected error checking %s subscription", subscription_feature)

        readonly_group = policy.get("readonly_group", "")
        if readonly_group and user.is_authenticated and user.groups.filter(name=readonly_group).exists():
            return True

        return None
