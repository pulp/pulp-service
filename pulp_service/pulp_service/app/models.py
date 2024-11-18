import asyncio
import json
import logging
import ssl

import aiohttp
import jq

from base64 import b64decode
from binascii import Error as Base64DecodeError
from hashlib import sha256
from gettext import gettext as _

from django.conf import settings
from django.db import models

from django.contrib.postgres.fields import ArrayField

from pulpcore.plugin.models import Domain
from pulpcore.app.models import AutoAddObjPermsMixin, HeaderContentGuard
from pulpcore.cache import Cache

_logger = logging.getLogger(__name__)


class DomainOrg(models.Model):
    """
    One-to-many relationship between org ids and Domains.
    """

    org_id = models.CharField(null=True, db_index=True)
    domain = models.OneToOneField(
        Domain, related_name="domains", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="users",
        on_delete=models.CASCADE,
        null=True,
    )


class FeatureContentGuardCache(Cache):
    default_base_key = "PULP_FEATURE_CONTENTGUARD_CACHE"
    default_expires_ttl = 86400  # The key expires in one day.


class FeatureContentGuard(HeaderContentGuard, AutoAddObjPermsMixin):
    features = ArrayField(models.TextField())

    def _check_for_feature(self, account_id):
        cert_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        cert_context.load_cert_chain(certfile=settings.SUBSCRIPTION_API_CERT)

        account_id_query_param = f"accountId={account_id}"
        features_query_param = "&".join(
            f"features={feature}" for feature in self.features
        )
        subscription_api_url = f"{settings.SUBSCRIPTION_API_URL}?{account_id_query_param}&{features_query_param}"

        async def fetch_feature():
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    subscription_api_url, ssl=cert_context, raise_for_status=True
                ) as response:
                    return await response.json()

        try:
            response = asyncio.run(fetch_feature())
        except aiohttp.ClientError as err:
            _logger.debug(
                _("Failed to fetch the Subscription feature information for a user.")
            )
            raise PermissionError(_("Access denied."))

        features_available = {
            feature["name"]
            for feature in response["features"]
            if feature["entitled"] is True
        }
        return features_available == set(self.features)

    def permit(self, request):
        try:
            header_content = request.headers[self.header_name]
        except KeyError:
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
            cache_key = f"{header_value}-{','.join(self.features)}"
            cache_key_digest = sha256(bytes(cache_key, "utf8")).hexdigest()
            feature_cache = FeatureContentGuardCache()
            account_allowed = feature_cache.get(cache_key_digest)

            if not account_allowed:
                account_allowed = self._check_for_feature(header_value)
                feature_cache.set(cache_key_digest, account_allowed, expires=feature_cache.default_expires_ttl)
        except aiohttp.ClientResponseError:
            _logger.debug("Access not allowed - Failed to check for features.")
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
