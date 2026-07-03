import json
import logging
import threading
import time
from base64 import b64decode
from binascii import Error as Base64DecodeError
from datetime import UTC, datetime
from gettext import gettext as _
from hashlib import sha256

import jq
import requests
from django.conf import settings
from django.contrib.postgres.fields import ArrayField, HStoreField
from django.db import models

from pulpcore.app.models import HeaderContentGuard
from pulpcore.cache import Cache
from pulpcore.plugin.models import AutoAddObjPermsMixin, BaseModel, Domain, Group
from pulpcore.plugin.util import get_domain_pk

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


class FeatureContentGuardCache(Cache):
    default_base_key = "PULP_FEATURE_CONTENTGUARD_CACHE"
    default_expires_ttl = 86400  # The key expires in one day.


class FeatureContentGuard(HeaderContentGuard, AutoAddObjPermsMixin):
    features = ArrayField(models.TextField())

    _session_lock = threading.Lock()
    _session = None

    @classmethod
    def _get_session(cls):
        """
        Returns a process-wide ``requests.Session`` configured with the client certificate.

        The previous implementation built a brand new ``ssl.SSLContext`` (re-reading and
        re-parsing the certificate off disk), a new ``aiohttp.ClientSession``, and a brand
        new asyncio event loop (via ``asyncio.run``) on *every single content request* that
        missed the cache. Reusing a session lets urllib3 keep a warm, pooled TLS connection
        to the Features Service and avoids that repeated setup cost.
        """
        if cls._session is None:
            with cls._session_lock:
                if cls._session is None:
                    session = requests.Session()
                    session.cert = settings.FEATURE_SERVICE_API_CERT_PATH
                    cls._session = session
        return cls._session

    def _check_for_feature(self, account_id):
        session = self._get_session()
        params = [("accountId", account_id), *(("features", feature) for feature in self.features)]
        # permit() runs synchronously on the content app's shared sync-to-async worker thread
        # (pulpcore.content.handler.Handler._permit is invoked via sync_to_async). A slow or
        # hanging call here doesn't just delay this request -- it can stall every other
        # request being served through that same thread, so this must be bounded.
        #
        # This must be a real Python tuple built here, not a settings value passed through
        # as-is: `requests` only splits a *tuple* into (connect, read) timeouts, and Pulp's
        # settings pipeline can silently turn a tuple default into a list, which `requests`
        # then treats as a single (invalid) timeout value and raises a ValueError.
        timeout = (settings.FEATURE_SERVICE_API_CONNECT_TIMEOUT, settings.FEATURE_SERVICE_API_READ_TIMEOUT)

        try:
            _logger.info("[%s] Making a request to feature service API ...", datetime.now(tz=UTC))
            response = session.get(
                settings.FEATURE_SERVICE_API_URL,
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()
            _logger.info("[%s] Got a response from feature service API!", datetime.now(tz=UTC))

            features_available = {feature["name"] for feature in response.json()["features"]}
            return features_available == set(self.features)
        except requests.Timeout as err:
            _logger.warning(
                "Failed to fetch the Subscription feature information for a user. "
                "The Features Service API did not respond within %s seconds.",
                timeout,
            )
            raise PermissionError(_("Access denied.")) from err
        except requests.HTTPError as err:
            status_code = err.response.status_code if err.response is not None else None
            if status_code == 400:
                _logger.exception("Failed to request information for a user. BadRequest. URL: %s", err.request.url)

            if status_code == 403:
                _logger.exception(
                    "Failed to request information for a user. "
                    "Permission Denied. Verify if the certificate is still valid."
                )

            _logger.warning(_("Failed to fetch the Subscription feature information for a user."))
            raise PermissionError(_("Access denied.")) from err
        except requests.RequestException as err:
            _logger.warning("Failed to reach the Features Service API: %s", err)
            raise PermissionError(_("Access denied.")) from err
        except Exception as err:
            # `permit()` is only ever allowed to return normally or raise `PermissionError`
            # (pulpcore.content.handler.Handler.auth_cached assumes exactly that contract).
            # Anything else -- a bug here, a misconfigured setting, an unexpected response
            # shape -- would otherwise escape as-is and crash with a confusing, unrelated
            # `UnboundLocalError` in pulpcore's guard-caching `finally` block, masking the
            # real error entirely. Fail closed and log loudly instead.
            _logger.exception("Unexpected error while checking the Features Service.")
            raise PermissionError(_("Access denied.")) from err

    @staticmethod
    def _get_cached_result(feature_cache, key):
        """
        Returns the cached True/False result for `key`, or None if there is no live entry.

        `Cache.set`'s `expires` argument calls Redis' `EXPIRE` on the *entire* hash the
        entry lives under, not on the individual field (Redis hash fields don't carry their
        own TTL). Every account sharing `FeatureContentGuardCache`'s single base key would
        therefore have its cached result's lifetime reset by any *other* account's write,
        which can prematurely evict still-valid entries. Instead, the expiration is embedded
        in the cached value itself and validated on read here, matching the pattern
        `pulpcore.cache.AsyncContentCache` already uses for the same reason.
        """
        raw = feature_cache.get(key)
        if not raw:
            return None
        try:
            entry = json.loads(raw)
            if entry["expires_at"] < time.time():
                return None
            return entry["allowed"]
        except (TypeError, ValueError, KeyError):
            return None

    @staticmethod
    def _set_cached_result(feature_cache, key, allowed):
        entry = {"allowed": allowed, "expires_at": time.time() + feature_cache.default_expires_ttl}
        feature_cache.set(key, json.dumps(entry), expires=feature_cache.default_expires_ttl)

    def check_feature(self, account_id):
        """
        Returns whether `account_id` has all of `self.features`, per the Features Service.

        Reuses the same `FeatureContentGuardCache` cache key scheme as `permit()` so that
        callers checking the same (account_id, features) pair -- e.g. `DomainBasedPermission`
        checking the `lightwell-network` feature -- share cache entries with this guard and
        avoid redundant Features Service calls. May raise `PermissionError` if the Features
        Service call fails (see `_check_for_feature`).
        """
        cache_key = f"{account_id}-{','.join(self.features)}"
        cache_key_digest = sha256(bytes(cache_key, "utf8")).hexdigest()
        feature_cache = FeatureContentGuardCache()
        account_allowed = self._get_cached_result(feature_cache, cache_key_digest)

        if account_allowed is None:
            _logger.debug("Feature cache MISS for key %s", cache_key_digest)
            account_allowed = self._check_for_feature(account_id)
            self._set_cached_result(feature_cache, cache_key_digest, account_allowed)
        else:
            _logger.debug("Feature cache HIT for key %s", cache_key_digest)

        return account_allowed

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
