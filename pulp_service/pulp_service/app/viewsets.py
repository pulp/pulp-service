import json
import logging
import random

from base64 import b64decode
from binascii import Error as Base64DecodeError
from datetime import datetime, timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models.query import QuerySet
from django.http import Http404
from django.shortcuts import redirect

from drf_spectacular.utils import extend_schema, extend_schema_view

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, DestroyModelMixin, ListModelMixin, RetrieveModelMixin

from pulpcore.plugin.viewsets import OperationPostponedResponse
from pulpcore.plugin.viewsets import ContentGuardViewSet, NamedModelViewSet, RolesMixin, TaskViewSet
from pulpcore.plugin.serializers import AsyncOperationResponseSerializer
from pulpcore.plugin.tasking import dispatch
from pulpcore.app.models import Domain, Group, Task
from pulpcore.app.serializers import DomainSerializer
from pulpcore.filters import BaseFilterSet, HyperlinkRelatedFilter
from pulpcore.app.viewsets.base import NAME_FILTER_OPTIONS
from pulpcore.app.viewsets.custom_filters import LabelFilter


from pulp_service.app.authentication import (
    RHServiceAccountCertAuthentication,
    RHTermsBasedRegistryAuthentication,
)

from pulp_service.app.authorization import DomainBasedPermission
from pulp_service.app.models import AgentScanReport, FeatureContentGuard
from pulp_service.app.models import PyPIYankMonitor
from pulp_service.app.models import VulnerabilityReport as VulnReport
from pulp_service.app.models import YankedPackageReport
from pulp_service.app.serializers import (
    AgentScanReportSerializer,
    ContentScanSerializer,
    FeatureContentGuardSerializer,
    PyPIYankMonitorSerializer,
    VulnerabilityReportSerializer,
    YankedPackageReportSerializer,
)
from pulp_service.app.tasks.package_scan import check_npm_package, check_content_from_repo_version
from pulp_rpm.app.models import Package


_logger = logging.getLogger(__name__)


class RedirectCheck(APIView):
    """
    Handles requests to the /api/redirect-check/ endpoint.
    """

    # allow anyone to access the endpoint
    authentication_classes = []
    permission_classes = []

    def head(self, request=None, path=None, pk=None):
        """
        Responds to HEAD requests for the redirect-check endpoint.
        """
        return redirect("/api/")


# returning 500 error in a "graceful" way
class InternalServerErrorCheck(APIView):
    """
    Handles requests to the /api/internal-server-error-check/ endpoint.
    """

    # allow anyone to access the endpoint
    authentication_classes = []
    permission_classes = []

    def head(self, request=None, path=None, pk=None):
        """
        Responds to HEAD requests for the internal-server-error-check endpoint.
        """
        return Response(data=None, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# raising an exception (helpful to verify middleware's behavior, for example, otel)
class InternalServerErrorCheckWithException(APIView):
    """
    Handles requests to the /api/raise-exception-check/ endpoint.
    """

    # allow anyone to access the endpoint
    authentication_classes = []
    permission_classes = []

    def head(self, request=None, path=None, pk=None):
        """
        Responds to HEAD requests for the raise-exception-check endpoint.
        """
        # the drf APIException returns a HTTP_500_INTERNAL_SERVER_ERROR
        raise APIException()


class FeatureContentGuardViewSet(ContentGuardViewSet, RolesMixin):
    """
    Content guard to protect the content guarded by Subscription Features.
    """

    endpoint_name = "feature"
    queryset = FeatureContentGuard.objects.all()
    serializer_class = FeatureContentGuardSerializer


class DebugAuthenticationHeadersView(APIView):
    """
    Returns the content of the authentication headers and client IP information.
    """

    authentication_classes = [RHTermsBasedRegistryAuthentication]
    permission_classes = []

    def get(self, request=None, path=None, pk=None):
        if not settings.AUTHENTICATION_HEADER_DEBUG:
            raise PermissionError("Access denied.")

        response_data = {}

        # Get x-rh-identity header
        try:
            header_content = request.headers["x-rh-identity"]
        except KeyError:
            _logger.error(
                "Access not allowed. Header {header_name} not found.".format(
                    header_name=settings.AUTHENTICATION_JSON_HEADER
                )
            )
            raise PermissionError("Access denied.")

        try:
            header_decoded_content = b64decode(header_content)
        except Base64DecodeError:
            _logger.error("Access not allowed - Header content is not Base64 encoded.")
            raise PermissionError("Access denied.")

        response_data["x_rh_identity"] = json.loads(header_decoded_content)

        # Get client IP headers
        response_data["client_ip_headers"] = {
            "true_client_ip": request.headers.get("True-Client-IP"),
            "x_forwarded_for": request.headers.get("X-Forwarded-For"),
            "x_real_ip": request.headers.get("X-Real-IP"),
            "remote_addr": request.META.get("REMOTE_ADDR"),
        }

        # Get other useful headers
        response_data["other_headers"] = {
            "x_rh_edge_host": request.headers.get("X-RH-EDGE-HOST"),
            "user_agent": request.headers.get("User-Agent"),
            "host": request.headers.get("Host"),
        }

        return Response(data=response_data)


@extend_schema_view(
    get=extend_schema(operation_id="admin_tasks"),
    list=extend_schema(operation_id="admin_tasks"),
)
class TaskViewSet(TaskViewSet):

    LOCKED_ROLES = {}

    def get_queryset(self):
        qs = self.queryset
        if isinstance(qs, QuerySet):
            # Ensure queryset is re-evaluated on each request.
            qs = qs.all()

        if self.parent_lookup_kwargs and self.kwargs:
            filters = {}
            for key, lookup in self.parent_lookup_kwargs.items():
                filters[lookup] = self.kwargs[key]
            qs = qs.filter(**filters)

        return qs

    @classmethod
    def view_name(cls):
        return "admintasks"


class VulnerabilityReport(NamedModelViewSet, ListModelMixin, RetrieveModelMixin, DestroyModelMixin):

    endpoint_name = "vuln_report_service"
    queryset = VulnReport.objects.all()
    serializer_class = VulnerabilityReportSerializer

    @extend_schema(
        request=ContentScanSerializer,
        description="Trigger a task to generate the package vulnerability report",
        summary="Generate vulnerability report",
        responses={202: AsyncOperationResponseSerializer},
    )
    def create(self, request):
        serializer = ContentScanSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        shared_resources = None
        """Dispatch a task to scan the Content Units from a Repository"""
        if repo_version := serializer.validated_data.get("repo_version", None):
            shared_resources = [repo_version.repository]
            dispatch_task, kwargs = check_content_from_repo_version, {
                "repo_version_pk": repo_version.pk
            }

        """Dispatch a task to scan the npm dependencies' vulnerabilities"""
        if serializer.validated_data.get("package_json", None):
            temp_file_pk = serializer.verify_file()
            dispatch_task, kwargs = check_npm_package, {"npm_package": temp_file_pk}

        task = dispatch(dispatch_task, shared_resources=shared_resources, kwargs=kwargs)
        return OperationPostponedResponse(task, request)


class IsSuperuser(BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_superuser



class PyPIYankMonitorFilter(BaseFilterSet):
    pulp_label_select = LabelFilter()
    repository = HyperlinkRelatedFilter(allow_null=True)

    class Meta:
        model = PyPIYankMonitor
        fields = {"name": NAME_FILTER_OPTIONS}


class PyPIYankMonitorViewSet(
    NamedModelViewSet, CreateModelMixin, ListModelMixin, RetrieveModelMixin, DestroyModelMixin
):
    endpoint_name = "pypi_yank_monitor"
    queryset = PyPIYankMonitor.objects.all()
    serializer_class = PyPIYankMonitorSerializer
    filterset_class = PyPIYankMonitorFilter
    permission_classes = [DomainBasedPermission]

    @extend_schema(request=None, responses={202: AsyncOperationResponseSerializer})
    @action(detail=True, methods=["post"], url_path="check")
    def check(self, request, pk=None):
        monitor = self.get_object()
        repo = monitor.repository or monitor.repository_version.repository
        task = dispatch(
            "pulp_service.app.tasks.pypi_yank_check.check_packages_for_monitor",
            shared_resources=[repo],
            kwargs={"monitor_pk": str(monitor.pk)},
        )
        return OperationPostponedResponse(task, request)

    @extend_schema(responses=YankedPackageReportSerializer)
    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, pk=None):
        monitor = self.get_object()
        try:
            latest_report = monitor.reports.latest("pulp_created")
        except YankedPackageReport.DoesNotExist:
            raise Http404
        serializer = YankedPackageReportSerializer(latest_report, context={"request": request})
        return Response(serializer.data)


class TaskIngestionDispatcherView(APIView):

    authentication_classes = []
    permission_classes = []

    def get(self, request=None, timeout=25):
        if not settings.TEST_TASK_INGESTION:
            raise PermissionError("Access denied.")

        task_count = 0
        start_time = datetime.now()
        timeout = timedelta(seconds=timeout)

        while datetime.now() < start_time + timeout:
            dispatch("pulp_service.app.tasks.util.no_op_task", exclusive_resources=[str(uuid4())])

            task_count = task_count + 1

        return Response({"tasks_executed": task_count})


class TaskIngestionRandomResourceLockDispatcherView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request=None, timeout=25):
        if not settings.TEST_TASK_INGESTION:
            raise PermissionError("Access denied.")

        exclusive_resources_list = [str(uuid4()) for _ in range(3)]

        task_count = 0
        start_time = datetime.now()
        timeout = timedelta(seconds=timeout)

        while datetime.now() < start_time + timeout:
            dispatch(
                "pulp_service.app.tasks.util.no_op_task",
                exclusive_resources=[random.choice(exclusive_resources_list)],
            )

            task_count = task_count + 1

        return Response({"tasks_executed": task_count})


class RDSConnectionTestDispatcherView(APIView):
    """
    Endpoint to dispatch RDS Proxy connection timeout tests remotely.

    POST body format:
    {
        "tests": ["test_1_idle_connection", "test_2_active_heartbeat"],
        "run_sequentially": false,  // optional, default false
        "duration_minutes": 50       // optional, default 50 (min: 1, max: 300)
    }

    Returns task IDs for dispatched tests.

    Security: Requires staff-level authentication. Tests are long-running
    and should only be triggered by authorized personnel.
    """

    # Use same authentication pattern as other test endpoints
    authentication_classes = []
    permission_classes = []

    AVAILABLE_TESTS = {
        "test_1_idle_connection": "pulp_service.app.tasks.rds_connection_tests.test_1_idle_connection",
        "test_2_active_heartbeat": "pulp_service.app.tasks.rds_connection_tests.test_2_active_heartbeat",
        "test_3_long_transaction": "pulp_service.app.tasks.rds_connection_tests.test_3_long_transaction",
        "test_4_transaction_with_work": "pulp_service.app.tasks.rds_connection_tests.test_4_transaction_with_work",
        "test_5_session_variable": "pulp_service.app.tasks.rds_connection_tests.test_5_session_variable",
        "test_6_listen_notify": "pulp_service.app.tasks.rds_connection_tests.test_6_listen_notify",
        "test_7_listen_with_activity": "pulp_service.app.tasks.rds_connection_tests.test_7_listen_with_activity",
    }

    @extend_schema(
        description="Dispatch RDS Proxy connection timeout tests",
        summary="Dispatch RDS connection tests",
        responses={202: AsyncOperationResponseSerializer},
    )
    def post(self, request):
        """
        Dispatch one or more RDS connection tests.

        Security: Tests must be explicitly enabled via RDS_CONNECTION_TESTS_ENABLED setting.
        """
        # Check if RDS tests are enabled (similar to TEST_TASK_INGESTION check)
        if not settings.DEBUG and not settings.RDS_CONNECTION_TESTS_ENABLED:
            _logger.warning(
                f"Unauthorized RDS test access attempt from {request.META.get('REMOTE_ADDR', 'unknown')}"
            )
            return Response(
                {
                    "error": "RDS connection tests are not enabled.",
                    "hint": "Set RDS_CONNECTION_TESTS_ENABLED=True in settings or enable DEBUG mode.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        tests = request.data.get("tests", [])
        run_sequentially = request.data.get("run_sequentially", False)
        duration_minutes = request.data.get("duration_minutes", 50)

        # Validate duration
        if not isinstance(duration_minutes, int) or duration_minutes < 1 or duration_minutes > 300:
            return Response(
                {
                    "error": "Invalid duration_minutes. Must be an integer between 1 and 300 (5 hours).",
                    "provided": duration_minutes,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not tests:
            return Response(
                {"error": "No tests specified. Provide a list of test names."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate test names
        if invalid_tests := [t for t in tests if t not in self.AVAILABLE_TESTS]:
            return Response(
                {
                    "error": f"Invalid test names: {invalid_tests}",
                    "available_tests": list(self.AVAILABLE_TESTS.keys()),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        dispatched_tasks = []

        # Check if domain support is enabled
        domain_enabled = getattr(settings, "DOMAIN_ENABLED", False)

        # For sequential execution, use a shared lock resource
        # This forces tasks to run one at a time
        sequential_lock = []
        if run_sequentially:
            from uuid import uuid4

            sequential_lock = [f"rds-test-sequential-{uuid4()}"]

        for test_name in tests:
            task_func = self.AVAILABLE_TESTS[test_name]

            # Dispatch the task with duration parameter
            task = dispatch(
                task_func,
                exclusive_resources=sequential_lock,  # Empty list for parallel, shared lock for sequential
                kwargs={"duration_minutes": duration_minutes},
            )

            # Get task ID - use current_id() if available, fallback to pk
            task_id = task.current_id() or task.pk

            # Build task href based on domain support
            if domain_enabled:
                # Domain-aware path: /pulp/{domain}/api/v3/tasks/{task_id}/
                domain_name = getattr(task.pulp_domain, "name", "default")
                task_href = f"/pulp/{domain_name}/api/v3/tasks/{task_id}/"
            else:
                # Standard path: /pulp/api/v3/tasks/{task_id}/
                task_href = f"/pulp/api/v3/tasks/{task_id}/"

            dispatched_tasks.append(
                {
                    "test_name": test_name,
                    "task_id": str(task_id),
                    "task_href": task_href,
                }
            )

        return Response(
            {
                "message": f"Dispatched {len(dispatched_tasks)} test(s)",
                "tasks": dispatched_tasks,
                "run_sequentially": run_sequentially,
                "duration_minutes": duration_minutes,
                "note": f"Each test runs for approximately {duration_minutes} minutes. Monitor task status via task_href.",
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def get(self, request):
        """
        Get available tests and their descriptions.

        This endpoint is always accessible for documentation purposes.
        """
        return Response(
            {
                "available_tests": list(self.AVAILABLE_TESTS.keys()),
                "descriptions": {
                    "test_1_idle_connection": "Idle connection test - baseline timeout test",
                    "test_2_active_heartbeat": "Active heartbeat test - periodic queries",
                    "test_3_long_transaction": "Long transaction test - idle transaction",
                    "test_4_transaction_with_work": "Transaction with work test - active transaction",
                    "test_5_session_variable": "Session variable test - connection pinning via SET",
                    "test_6_listen_notify": "LISTEN/NOTIFY test - CRITICAL: real worker behavior",
                    "test_7_listen_with_activity": "LISTEN with activity test - periodic notifications",
                },
                "usage": {
                    "endpoint": "/api/pulp/rds-connection-tests/",
                    "method": "POST",
                    "body": {
                        "tests": ["test_1_idle_connection", "test_2_active_heartbeat"],
                        "run_sequentially": False,
                        "duration_minutes": 50,
                    },
                    "note": "duration_minutes is optional (default: 50, min: 1, max: 300)",
                },
            }
        )


class DatabaseTriggersView(APIView):
    """
    Returns information about database triggers on the core_task table.
    """

    # Allow anyone to access the endpoint for debugging
    authentication_classes = []
    permission_classes = []

    def get(self, request=None):
        """
        Query PostgreSQL system catalogs for triggers on core_task table.
        """
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    t.tgname AS trigger_name,
                    c.relname AS table_name,
                    CASE t.tgtype::integer & 1
                        WHEN 1 THEN 'ROW'
                        ELSE 'STATEMENT'
                    END AS trigger_level,
                    CASE t.tgtype::integer & 66
                        WHEN 2 THEN 'BEFORE'
                        WHEN 64 THEN 'INSTEAD OF'
                        ELSE 'AFTER'
                    END AS trigger_timing,
                    CASE
                        WHEN t.tgtype::integer & 4 <> 0 THEN 'INSERT'
                        WHEN t.tgtype::integer & 8 <> 0 THEN 'DELETE'
                        WHEN t.tgtype::integer & 16 <> 0 THEN 'UPDATE'
                        ELSE 'UNKNOWN'
                    END AS trigger_event,
                    p.proname AS function_name,
                    pg_get_triggerdef(t.oid) AS trigger_definition,
                    pg_get_functiondef(p.oid) AS function_definition
                FROM pg_trigger t
                JOIN pg_class c ON t.tgrelid = c.oid
                JOIN pg_proc p ON t.tgfoid = p.oid
                WHERE c.relname = 'core_task'
                AND t.tgisinternal = false
                ORDER BY t.tgname;
            """
            )

            columns = [col[0] for col in cursor.description]
            triggers = []
            for row in cursor.fetchall():
                trigger_info = dict(zip(columns, row))
                triggers.append(trigger_info)

        return Response(
            {"table": "core_task", "trigger_count": len(triggers), "triggers": triggers}
        )


class ReleaseTaskLocksView(APIView):
    """
    Admin-only endpoint to manually release Redis locks for a task.

    This endpoint is useful for debugging lock issues and cleaning up
    orphaned locks when needed. Requires admin privileges.
    """

    # Require admin authentication
    permission_classes = [IsAdminUser]

    def get(self, request):
        """
        Release all Redis locks for a given task UUID.

        Query parameters:
            task_id: UUID of the task to release locks for

        Returns:
            200: Locks released successfully
            400: Missing or invalid task_id parameter
            404: Task not found
            500: Error releasing locks
        """
        # Check if Redis worker type is enabled
        if settings.WORKER_TYPE != "redis":
            return Response(
                {
                    "error": "This endpoint only works with Redis workers.",
                    "worker_type": settings.WORKER_TYPE,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get task_id from query parameters
        task_id = request.GET.get("task_id")

        if not task_id:
            return Response(
                {"error": "Missing required query parameter: task_id"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Import Redis-specific functions
            from pulpcore.app.redis_connection import get_redis_connection
            from pulpcore.tasking.redis_locks import resource_to_lock_key

            # Get Redis connection
            redis_conn = get_redis_connection()
            if not redis_conn:
                return Response(
                    {"error": "Redis connection not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Look up the task
            try:
                task = Task.objects.select_related("pulp_domain").get(pk=task_id)
            except Task.DoesNotExist:
                return Response(
                    {"error": f"Task {task_id} not found"}, status=status.HTTP_404_NOT_FOUND
                )

            # Extract exclusive and shared resources from the task
            exclusive_resources = [
                resource
                for resource in task.reserved_resources_record or []
                if not resource.startswith("shared:")
            ]

            shared_resources = [
                resource[7:]  # Remove "shared:" prefix
                for resource in task.reserved_resources_record or []
                if resource.startswith("shared:")
            ]

            # Check who holds the task lock (for informational purposes)
            task_lock_key = f"task:{task_id}"
            task_lock_holder = redis_conn.get(task_lock_key)
            if task_lock_holder:
                task_lock_holder = task_lock_holder.decode("utf-8")

            # Delete exclusive resource locks directly (no ownership check)
            exclusive_locks_deleted = 0
            for resource in exclusive_resources:
                lock_key = resource_to_lock_key(resource)
                if redis_conn.delete(lock_key):
                    exclusive_locks_deleted += 1
                    _logger.info(f"Deleted exclusive lock for resource: {resource}")

            # Delete shared resource locks directly (delete the entire set)
            shared_locks_deleted = 0
            for resource in shared_resources:
                lock_key = resource_to_lock_key(resource)
                if redis_conn.delete(lock_key):
                    shared_locks_deleted += 1
                    _logger.info(f"Deleted shared lock set for resource: {resource}")

            # Delete the task lock
            task_lock_deleted = redis_conn.delete(task_lock_key)

            return Response(
                {
                    "message": "Successfully released locks for task",
                    "task_id": str(task_id),
                    "task_state": task.state,
                    "task_lock_holder": task_lock_holder,
                    "task_lock_deleted": bool(task_lock_deleted),
                    "exclusive_resources": exclusive_resources,
                    "exclusive_resources_count": len(exclusive_resources),
                    "exclusive_locks_deleted": exclusive_locks_deleted,
                    "shared_resources": shared_resources,
                    "shared_resources_count": len(shared_resources),
                    "shared_locks_deleted": shared_locks_deleted,
                }
            )

        except Exception as e:
            _logger.exception(f"Error releasing locks for task {task_id}")
            return Response(
                {"error": "Failed to release locks", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


def _get_redis_lock_info(redis_conn, task):
    """Get Redis lock state for a task's resources."""
    from pulpcore.tasking.redis_locks import resource_to_lock_key

    reserved = task.reserved_resources_record or []
    exclusive_resources = [r for r in reserved if not r.startswith("shared:")]
    shared_resources = [r[7:] for r in reserved if r.startswith("shared:")]

    task_lock_key = f"task:{task.pk}"
    task_lock_holder = redis_conn.get(task_lock_key)
    if task_lock_holder:
        task_lock_holder = task_lock_holder.decode("utf-8")

    exclusive_info = []
    for resource in exclusive_resources:
        lock_key = resource_to_lock_key(resource)
        holder = redis_conn.get(lock_key)
        if holder:
            holder = holder.decode("utf-8")
        exclusive_info.append(
            {
                "resource": resource,
                "lock_key": lock_key,
                "held": holder is not None,
                "holder": holder,
            }
        )

    shared_info = []
    for resource in shared_resources:
        lock_key = resource_to_lock_key(resource)
        lock_type = redis_conn.type(lock_key)
        if isinstance(lock_type, bytes):
            lock_type = lock_type.decode("utf-8")
        holders = None
        if lock_type == "set":
            holders = sorted(m.decode("utf-8") for m in redis_conn.smembers(lock_key))
        elif lock_type == "string":
            val = redis_conn.get(lock_key)
            if val:
                holders = [val.decode("utf-8")]
        shared_info.append(
            {
                "resource": resource,
                "lock_key": lock_key,
                "held": lock_type != "none",
                "holders": holders,
                "lock_type": lock_type,
            }
        )

    return {
        "task_lock": {
            "key": task_lock_key,
            "held": task_lock_holder is not None,
            "holder": task_lock_holder,
        },
        "exclusive_resources": exclusive_info,
        "shared_resources": shared_info,
    }


def _get_app_lock_info(task):
    """Get app_lock info for a task."""
    if not task.app_lock:
        return {"locked": False, "app_status_name": None}
    app = task.app_lock
    return {
        "locked": True,
        "app_status_name": app.name,
        "app_type": app.app_type,
        "last_heartbeat": app.last_heartbeat.isoformat() if app.last_heartbeat else None,
        "online": app.online,
    }


class TaskDebugView(APIView):
    """
    Admin-only endpoint providing comprehensive debug information for a task.

    Returns task state, app_lock info, Redis lock state for all resources,
    queue position, and blocking tasks.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        from pulpcore.app.redis_connection import get_redis_connection
        from pulpcore.constants import TASK_INCOMPLETE_STATES, TASK_STATES

        if settings.WORKER_TYPE != "redis":
            return Response(
                {"error": "This endpoint only works with Redis workers."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task_id = request.GET.get("task_id")
        if not task_id:
            return Response(
                {"error": "Missing required query parameter: task_id"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            redis_conn = get_redis_connection()
            if not redis_conn:
                return Response(
                    {"error": "Redis connection not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            try:
                task = Task.objects.select_related("pulp_domain", "app_lock").get(pk=task_id)
            except Task.DoesNotExist:
                return Response(
                    {"error": f"Task {task_id} not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            exclusive_resources = [
                r
                for r in task.reserved_resources_record or []
                if not r.startswith("shared:")
            ]

            # Queue position
            older_waiting = Task.objects.filter(
                state=TASK_STATES.WAITING,
                app_lock__isnull=True,
                pulp_created__lt=task.pulp_created,
            ).count()
            fetch_task_limit = 20
            within_window = older_waiting < fetch_task_limit

            # Blocking tasks — incomplete tasks with overlapping exclusive resources
            blocking = []
            if exclusive_resources and task.state == TASK_STATES.WAITING:
                blocking_qs = (
                    Task.objects.filter(
                        state__in=TASK_INCOMPLETE_STATES,
                        reserved_resources_record__overlap=exclusive_resources,
                        pulp_created__lt=task.pulp_created,
                    )
                    .exclude(pk=task.pk)
                    .select_related("pulp_domain")[:20]
                )
                for bt in blocking_qs:
                    overlap = [
                        r
                        for r in bt.reserved_resources_record or []
                        if r in exclusive_resources
                        or r.removeprefix("shared:") in exclusive_resources
                    ]
                    blocking.append(
                        {
                            "task_id": str(bt.pk),
                            "state": bt.state,
                            "name": bt.name,
                            "pulp_created": bt.pulp_created.isoformat(),
                            "overlapping_resources": overlap,
                        }
                    )

            return Response(
                {
                    "task_id": str(task.pk),
                    "task": {
                        "state": task.state,
                        "name": task.name,
                        "logging_cid": task.logging_cid,
                        "pulp_created": task.pulp_created.isoformat(),
                        "unblocked_at": task.unblocked_at.isoformat()
                        if task.unblocked_at
                        else None,
                        "started_at": task.started_at.isoformat()
                        if task.started_at
                        else None,
                        "finished_at": task.finished_at.isoformat()
                        if task.finished_at
                        else None,
                        "immediate": task.immediate,
                        "deferred": task.deferred,
                        "error": task.error,
                        "reserved_resources_record": task.reserved_resources_record,
                        "versions": task.versions,
                        "parent_task": str(task.parent_task_id)
                        if task.parent_task_id
                        else None,
                        "domain": task.pulp_domain.name if task.pulp_domain else None,
                    },
                    "app_lock": _get_app_lock_info(task),
                    "redis_locks": _get_redis_lock_info(redis_conn, task),
                    "queue_position": {
                        "older_waiting_tasks": older_waiting,
                        "fetch_task_limit": fetch_task_limit,
                        "within_fetch_window": within_window,
                    },
                    "blocking_tasks": blocking,
                }
            )

        except Exception as e:
            _logger.exception(f"Error getting debug info for task {task_id}")
            return Response(
                {"error": "Failed to get task debug info", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TaskQueueView(APIView):
    """
    Admin-only endpoint listing the oldest waiting and running tasks.

    Returns task info with Redis lock state for each, giving operators
    the same view workers have when calling fetch_task().
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        from pulpcore.app.redis_connection import get_redis_connection
        from pulpcore.constants import TASK_STATES

        if settings.WORKER_TYPE != "redis":
            return Response(
                {"error": "This endpoint only works with Redis workers."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            limit = int(request.GET.get("limit", 10))
        except (ValueError, TypeError):
            limit = 10
        limit = max(1, min(limit, 100))

        try:
            redis_conn = get_redis_connection()
            if not redis_conn:
                return Response(
                    {"error": "Redis connection not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            total_waiting = Task.objects.filter(state=TASK_STATES.WAITING).count()
            total_running = Task.objects.filter(state=TASK_STATES.RUNNING).count()

            tasks = (
                Task.objects.filter(
                    state__in=[TASK_STATES.WAITING, TASK_STATES.RUNNING]
                )
                .select_related("pulp_domain", "app_lock")
                .order_by("pulp_created")[:limit]
            )

            task_list = []
            for task in tasks:
                redis_locks = _get_redis_lock_info(redis_conn, task)

                blocked_by = set()
                for res in redis_locks["exclusive_resources"]:
                    if res["held"] and res["holder"]:
                        blocked_by.add(res["holder"])
                for res in redis_locks["shared_resources"]:
                    if res["lock_type"] == "string" and res["holders"]:
                        blocked_by.update(res["holders"])

                task_list.append(
                    {
                        "task_id": str(task.pk),
                        "state": task.state,
                        "name": task.name,
                        "logging_cid": task.logging_cid,
                        "pulp_created": task.pulp_created.isoformat(),
                        "started_at": task.started_at.isoformat()
                        if task.started_at
                        else None,
                        "immediate": task.immediate,
                        "deferred": task.deferred,
                        "domain": task.pulp_domain.name if task.pulp_domain else None,
                        "versions": task.versions,
                        "reserved_resources_record": task.reserved_resources_record,
                        "app_lock": _get_app_lock_info(task),
                        "redis_locks": {
                            "task_lock_held": redis_locks["task_lock"]["held"],
                            "task_lock_holder": redis_locks["task_lock"]["holder"],
                            "exclusive_resources_locked": sum(
                                1 for r in redis_locks["exclusive_resources"] if r["held"]
                            ),
                            "shared_resources_locked": sum(
                                1 for r in redis_locks["shared_resources"] if r["held"]
                            ),
                            "blocked_by": sorted(blocked_by),
                        },
                    }
                )

            return Response(
                {
                    "total_waiting": total_waiting,
                    "total_running": total_running,
                    "limit": limit,
                    "tasks": task_list,
                }
            )

        except Exception as e:
            _logger.exception("Error getting task queue info")
            return Response(
                {"error": "Failed to get task queue info", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CreateDomainView(APIView):

    permission_classes = [DomainBasedPermission]
    """
    Custom endpoint to create domains with service-specific logic.
    """

    @extend_schema(
        request=DomainSerializer,
        description="Create a new domain for from S3 template domain, self-service path",
        summary="Create domain",
        responses={201: DomainSerializer},
    )
    def post(self, request):
        """
        Self-service endpoint to create a new domain.
        This endpoint uses the model domain's storage settings and class,
        """

        # Check if user has a group, create one if not
        user = request.user
        domain_name = request.data.get("name")

        if not domain_name:
            return Response(
                {"error": "Domain name is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        if not user.groups.exists():
            # User has no groups, create one with a unique name or reuse existing
            group_name = f"domain-{domain_name}"
            _logger.info(
                f"User {user.username} has no groups. Creating or finding group '{group_name}' for domain creation."
            )
            try:
                # Use get_or_create to avoid duplicate group name issues
                group, created = Group.objects.get_or_create(name=group_name)
                if created:
                    _logger.info(f"Created new group '{group_name}'.")
                else:
                    _logger.info(f"Reusing existing group '{group_name}'.")

                # Add user to the group
                user.groups.add(group)
                _logger.info(f"Added user {user.username} to group '{group_name}'.")
            except Exception as e:
                _logger.error(f"Failed to create or assign group '{group_name}': {e}")
                return Response(
                    {"error": f"Failed to create group for domain: {str(e)}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
        else:
            _logger.info(f"User {user.username} already belongs to a group.")

        # Prepare data with defaults from default domain if needed
        data = request.data.copy()

        # Always get storage settings from model domain (ignore user input)
        try:
            model_domain = Domain.objects.get(name="template-domain-s3")
            data["storage_settings"] = model_domain.storage_settings
            data["storage_class"] = model_domain.storage_class
            data["pulp_labels"] = model_domain.pulp_labels
        except Domain.DoesNotExist:
            _logger.error("Model domain 'template-domain-s3' not found")
            return Response(
                {
                    "error": "Model domain 'template-domain-s3' not found. Please create it first with correct storage settings."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = DomainSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        # Perform the creation with validated data
        with transaction.atomic():
            domain = serializer.save()

        response_data = DomainSerializer(domain, context={"request": request}).data

        return Response(response_data, status=status.HTTP_201_CREATED)


class AgentScanReportView(
    NamedModelViewSet, ListModelMixin, RetrieveModelMixin, DestroyModelMixin
):

    endpoint_name = "agent_scan_report"
    queryset = AgentScanReport.objects.prefetch_related("repo_versions").select_related(
        "content"
    )
    serializer_class = AgentScanReportSerializer

    @classmethod
    def routable(cls):
        return True
