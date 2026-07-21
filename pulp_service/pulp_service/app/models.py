import json
import logging
from base64 import b64decode
from binascii import Error as Base64DecodeError
from gettext import gettext as _

import jq
from django.conf import settings
from django.contrib.postgres.fields import ArrayField, HStoreField
from django.db import models

from pulpcore.app.models import HeaderContentGuard
from pulpcore.plugin.models import AutoAddObjPermsMixin, BaseModel, Domain, Group
from pulpcore.plugin.util import get_domain_pk

from pulp_service.app.features_service import check_subscription

_logger = logging.getLogger(__name__)


class DomainOrg(models.Model):
    """
    One-to-many relationship between org ids and Domains.
    """

    org_id = models.CharField(null=True, db_index=True)  # noqa: DJ001 — NULL semantics needed for org lookup
    domains = models.ManyToManyField(Domain, related_name="domain_orgs")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="users",
        on_delete=models.SET_NULL,
        null=True,
    )
    group = models.ForeignKey(
        Group,
        related_name="domain_orgs",
        on_delete=models.SET_NULL,
        null=True,
    )

    def __str__(self):
        return f"DomainOrg(org_id={self.org_id})"


class FeatureContentGuard(HeaderContentGuard, AutoAddObjPermsMixin):
    features = ArrayField(models.TextField())

    def check_feature(self, account_id):
        """
        Returns whether ``account_id`` has all of ``self.features``, per the Features Service.

        Delegates to check_subscription(), which caches results so that callers checking
        the same (account_id, features) pair share cache entries. May raise PermissionError
        if the Features Service call fails.
        """
        return check_subscription(account_id, self.features)

    def permit(self, request):
        try:
            header_content = request.headers[self.header_name]
        except KeyError:
            _logger.exception("Access not allowed. Header %s not found.", self.header_name)
            raise PermissionError(_("Access denied.")) from None

        try:
            header_decoded_content = b64decode(header_content)
        except Base64DecodeError as exc:
            _logger.exception("Access not allowed - Header content is not Base64 encoded.")
            raise PermissionError(_("Access denied.")) from exc

        try:
            header_value = json.loads(header_decoded_content)
            json_path = jq.compile(self.jq_filter)

            if settings.AUTHENTICATION_HEADER_DEBUG:
                _logger.info("Authentication Header Debug enabled: %s", header_value)

            header_value = json_path.input_value(header_value).first()

        except json.JSONDecodeError as exc:
            _logger.exception("Access not allowed - Invalid JSON or Path not found.")
            raise PermissionError(_("Access denied.")) from exc

        account_allowed = self.check_feature(header_value)

        if not account_allowed:
            _logger.warning("Access not allowed - Features not available for the user.")
            raise PermissionError(_("Access denied."))

        return

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        permissions = (
            (
                "manage_roles_featurecontentguard",
                "Can manage role assignments on Feature ContentGuard",
            ),
        )


class YankedPackageReport(BaseModel):
    """
    Stores the result of a PyPI yank check for Python packages stored in Pulp.
    """

    report = models.JSONField()
    monitor = models.ForeignKey(
        "PyPIYankMonitor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    repository_name = models.TextField(null=True, blank=True)  # noqa: DJ001

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"


class PyPIYankMonitor(BaseModel):
    """
    Registers a Python repository or repository version for daily PyPI yank monitoring.
    Exactly one of repository or repository_version must be set.
    """

    name = models.TextField(db_index=True, unique=True)
    description = models.TextField(null=True, blank=True)  # noqa: DJ001
    pulp_labels = HStoreField(default=dict)
    repository = models.ForeignKey("core.Repository", on_delete=models.CASCADE, null=True, blank=True)
    repository_version = models.ForeignKey("core.RepositoryVersion", on_delete=models.CASCADE, null=True, blank=True)
    last_checked = models.DateTimeField(null=True, blank=True)

    def get_repo_version_and_name(self):
        """Return (repository_version, repository_name) for this monitor.

        If pinned to a version, return it directly. Otherwise return the latest
        complete version of the repository.
        """
        if self.repository_version:
            return self.repository_version, self.repository_version.repository.name
        latest = self.repository.versions.complete().latest("number")
        return latest, self.repository.name

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"


class VulnerabilityReport(BaseModel):
    """
    Model used in vulnerability report.
    """

    vulns = models.JSONField()
    pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.CASCADE)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
