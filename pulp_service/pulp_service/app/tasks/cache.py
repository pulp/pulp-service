"""
Background task that flushes the content app's Redis cache for all
distributions while preserving worker and task locks.

The content app caches distribution responses in Redis in a hash keyed by the
distribution's ``base_path`` (domain-prefixed as ``"<domain>:<base_path>"`` when
domains are enabled). Worker and task locks live in the SAME Redis instance under
separate key namespaces (``pulp:resource_lock:*``, ``task:*``, ``pulp:owner_locks:*``,
``pulp:active_owners``, ...). A blanket ``FLUSHALL`` / ``FLUSHDB`` would destroy those
locks and disrupt task execution, so this task deletes ONLY the per-distribution
content-cache hashes.

Dispatched by ``FlushContentCacheView`` (POST /api/pulp/debug/flush-content-cache/).
"""

import logging

from django.conf import settings

from pulpcore.cache import Cache
from pulpcore.plugin.models import Distribution

_logger = logging.getLogger(__name__)

# Bound the size of each Redis DEL so a very large number of distributions does
# not produce a single unbounded command.
DELETE_CHUNK_SIZE = 1000


def flush_content_cache():
    """
    Delete the content-cache hash of every distribution, leaving lock keys intact.

    Each distribution's OWN domain is used to build the cache key (not the ambient
    request domain), so the flush spans every domain. Because it enumerates
    distributions and never issues a database flush or targets a lock namespace,
    worker and task locks are never touched.

    Returns:
        A summary dict (stored as ``task.result``) with the number of
        distributions targeted and cache hashes actually removed from Redis.
    """
    if not settings.CACHE_ENABLED:
        _logger.warning("Content cache flush requested but CACHE_ENABLED is False; nothing to do.")
        return {"cache_enabled": False, "distributions": 0, "cache_keys_deleted": 0}

    cache = Cache()
    if not cache.redis:
        _logger.error("Redis connection not available -- cannot flush content cache.")
        return {"error": "Redis connection not available"}

    def _delete(keys):
        return cache.delete(base_key=keys) if keys else 0

    # Stream distributions from the DB (server-side cursor) and delete each batch of
    # cache keys immediately, so worker memory never holds the full key list even when
    # the installation has a very large number of distributions.
    distributions = 0
    deleted = 0
    batch = []
    rows = Distribution.objects.values_list("base_path", "pulp_domain__name").iterator(chunk_size=DELETE_CHUNK_SIZE)
    for base_path, domain_name in rows:
        distributions += 1
        batch.append(f"{domain_name}:{base_path}" if settings.DOMAIN_ENABLED else base_path)
        if len(batch) >= DELETE_CHUNK_SIZE:
            deleted += _delete(batch)
            batch = []
    deleted += _delete(batch)

    _logger.info(
        "Flushed content cache for %s distribution(s); %s cache key(s) deleted.",
        distributions,
        deleted,
    )
    return {"distributions": distributions, "cache_keys_deleted": deleted}
