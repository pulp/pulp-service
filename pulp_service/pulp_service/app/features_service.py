import json
import logging
import threading
import time
from datetime import UTC, datetime
from gettext import gettext as _
from hashlib import sha256

import requests
from django.conf import settings

from pulpcore.cache import Cache

_logger = logging.getLogger(__name__)
_session_lock = threading.Lock()
_session = None


class FeatureContentGuardCache(Cache):
    default_base_key = "PULP_FEATURE_CONTENTGUARD_CACHE"
    default_expires_ttl = 86400  # The key expires in one day.


def _get_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                s = requests.Session()
                s.cert = settings.FEATURE_SERVICE_API_CERT_PATH
                _session = s
    return _session


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


def _set_cached_result(feature_cache, key, allowed):
    entry = {"allowed": allowed, "expires_at": time.time() + feature_cache.default_expires_ttl}
    feature_cache.set(key, json.dumps(entry), expires=feature_cache.default_expires_ttl)


def _call_features_service(account_id, features):
    params = [("accountId", account_id), *(("features", feature) for feature in features)]
    # This function may run synchronously on the content app's shared sync-to-async
    # worker thread (via FeatureContentGuard.permit and pulpcore's sync_to_async). A
    # slow or hanging call here doesn't just delay this request -- it can stall every
    # other request being served through that same thread, so this must be bounded.
    #
    # This must be a real Python tuple built here, not a settings value passed through
    # as-is: `requests` only splits a *tuple* into (connect, read) timeouts, and Pulp's
    # settings pipeline can silently turn a tuple default into a list, which `requests`
    # then treats as a single (invalid) timeout value and raises a ValueError.
    timeout = (settings.FEATURE_SERVICE_API_CONNECT_TIMEOUT, settings.FEATURE_SERVICE_API_READ_TIMEOUT)

    try:
        _logger.info("[%s] Making a request to feature service API ...", datetime.now(tz=UTC))
        session = _get_session()
        response = session.get(
            settings.FEATURE_SERVICE_API_URL,
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        _logger.info("[%s] Got a response from feature service API!", datetime.now(tz=UTC))

        features_available = {feature["name"] for feature in response.json()["features"]}
        return features_available == set(features)
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
                "Failed to request information for a user. Permission Denied. Verify if the certificate is still valid."
            )

        _logger.warning(_("Failed to fetch the Subscription feature information for a user."))
        raise PermissionError(_("Access denied.")) from err
    except requests.RequestException as err:
        _logger.warning("Failed to reach the Features Service API: %s", err)
        raise PermissionError(_("Access denied.")) from err
    except Exception as err:
        # Callers expect either a normal return or PermissionError -- nothing else.
        # FeatureContentGuard.permit() in particular requires this contract
        # (pulpcore.content.handler.Handler.auth_cached assumes exactly that).
        # Anything else -- a bug, a misconfigured setting, an unexpected response
        # shape -- would escape and crash with a confusing, unrelated
        # UnboundLocalError in pulpcore's guard-caching finally block, masking the
        # real error entirely. Fail closed and log loudly instead.
        _logger.exception("Unexpected error while checking the Features Service.")
        raise PermissionError(_("Access denied.")) from err


def check_subscription(account_id, features):
    cache_key = f"{account_id}-{','.join(features)}"
    cache_key_digest = sha256(bytes(cache_key, "utf8")).hexdigest()
    feature_cache = FeatureContentGuardCache()
    account_allowed = _get_cached_result(feature_cache, cache_key_digest)

    if account_allowed is None:
        _logger.debug("Feature cache MISS for key %s", cache_key_digest)
        account_allowed = _call_features_service(account_id, features)
        _set_cached_result(feature_cache, cache_key_digest, account_allowed)
    else:
        _logger.debug("Feature cache HIT for key %s", cache_key_digest)

    return account_allowed
