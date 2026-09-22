import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from pulpcore.plugin.serializers import AsyncOperationResponseSerializer
from pulpcore.plugin.tasking import dispatch
from pulpcore.plugin.viewsets import OperationPostponedResponse

_logger = logging.getLogger(__name__)


class FlushContentCacheView(APIView):
    """
    Admin-only endpoint that dispatches a task to flush the content app's Redis
    cache for all distributions while preserving worker and task locks.

    The content app caches distribution responses in Redis in a hash keyed by
    the distribution's ``base_path`` (domain-prefixed as ``"<domain>:<base_path>"``
    when domains are enabled). Worker and task locks live in the SAME Redis
    instance under separate key namespaces (``pulp:resource_lock:*``, ``task:*``,
    ``pulp:owner_locks:*``, ``pulp:active_owners``, ...). A blanket ``FLUSHALL`` /
    ``FLUSHDB`` would destroy those locks and disrupt task execution.

    The dispatched task deletes ONLY the per-distribution content-cache hashes, so
    lock keys are never touched. Enumerating and deleting potentially many keys can
    take time at scale, so the work runs on a worker rather than in the request
    thread; the caller polls the returned task for the result summary.

    POST /api/pulp/debug/flush-content-cache/
    """

    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Flush the content app's distribution cache (preserves locks)",
        description=(
            "Dispatch a task that deletes the content-cache hash of every distribution "
            "from Redis while leaving worker and task locks intact."
        ),
        request=None,
        responses={202: AsyncOperationResponseSerializer},
    )
    def post(self, request):
        if not settings.CACHE_ENABLED:
            return Response(
                {"error": "The content cache is not enabled (CACHE_ENABLED is False)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task = dispatch("pulp_service.app.tasks.cache.flush_content_cache")
        return OperationPostponedResponse(task, request)
