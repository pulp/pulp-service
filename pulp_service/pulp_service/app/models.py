import json
import logging

import jq

from base64 import b64decode
from binascii import Error as Base64DecodeError
from gettext import gettext as _

from django.conf import settings
from django.db import models

from django.contrib.postgres.fields import ArrayField

from pulpcore.plugin.models import Domain
from pulpcore.app.models import AutoAddObjPermsMixin, ContentGuard
from pulpcore.cache import Cache

_logger = logging.getLogger(__name__)


class DomainOrg(models.Model):
    """
    One-to-many relationship between org ids and Domains.
    """
    org_id = models.CharField(null=True, db_index=True)
    domain = models.OneToOneField(Domain, related_name="domains", on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="users", on_delete=models.CASCADE, null=True
    )


class FeatureContentGuardCache(Cache):
    default_base_key = "PULP_FEATURE_CONTENTGUARD_CACHE"
    default_expires_ttl = 86400  # They key expires in one day.


class FeatureContentGuard(ContentGuard, AutoAddObjPermsMixin):
    features = ArrayField(models.TextField())

    def permit(self, request):
        header_content = request.headers.get(self.header_name)
        if not header_content:
            _logger.debug(
                "Access not allowed. Header {header_name} not found.".format(
                    header_name=self.header_name
                )
            )
            raise PermissionError(_("Access denied."))

        try:
            header_decoded_content = b64decode(header_content)
        except Base64DecodeError:
            _logger.debug("Access not allowed - Header content is not Base64 encoded.")
            raise PermissionError(_("Access denied."))

        try:
            header_value = json.loads(header_decoded_content)
            json_path = jq.compile(self.jq_filter)

            header_value = json_path.input_value(header_value).first()

        except json.JSONDecodeError:
            _logger.debug("Access not allowed - Invalid JSON or Path not found.")
            raise PermissionError(_("Access denied."))

        try:
            feature_cache = FeatureContentGuardCache()
            feature_cache.get()
        except:
            pass

        if header_value != self.header_value:
            _logger.debug("Access not allowed - Wrong header value.")
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
