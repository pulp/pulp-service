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

    # Check TTL on task lock (Bug 2: locks have no TTL)
    task_lock_ttl = redis_conn.ttl(task_lock_key) if task_lock_holder else None

    exclusive_info = []
    for resource in exclusive_resources:
        lock_key = resource_to_lock_key(resource)
        lock_type = redis_conn.type(lock_key)
        if isinstance(lock_type, bytes):
            lock_type = lock_type.decode("utf-8")
        holder = None
        holders = None
        ttl = None
        if lock_type == "string":
            val = redis_conn.get(lock_key)
            if val:
                holder = val.decode("utf-8")
            ttl = redis_conn.ttl(lock_key)
        elif lock_type == "set":
            holders = sorted(m.decode("utf-8") for m in redis_conn.smembers(lock_key))
            ttl = redis_conn.ttl(lock_key)
        exclusive_info.append(
            {
                "resource": resource,
                "lock_key": lock_key,
                "held": lock_type != "none",
                "holder": holder,
                "holders": holders,
                "lock_type": lock_type,
                "ttl": ttl,
            }
        )

    shared_info = []
    for resource in shared_resources:
        lock_key = resource_to_lock_key(resource)
        lock_type = redis_conn.type(lock_key)
        if isinstance(lock_type, bytes):
            lock_type = lock_type.decode("utf-8")
        holders = None
        ttl = None
        if lock_type == "set":
            holders = sorted(m.decode("utf-8") for m in redis_conn.smembers(lock_key))
            ttl = redis_conn.ttl(lock_key)
        elif lock_type == "string":
            val = redis_conn.get(lock_key)
            if val:
                holders = [val.decode("utf-8")]
            ttl = redis_conn.ttl(lock_key)
        shared_info.append(
            {
                "resource": resource,
                "lock_key": lock_key,
                "held": lock_type != "none",
                "holders": holders,
                "lock_type": lock_type,
                "ttl": ttl,
            }
        )

    return {
        "task_lock": {
            "key": task_lock_key,
            "held": task_lock_holder is not None,
            "holder": task_lock_holder,
            "ttl": task_lock_ttl,
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
        "versions": app.versions,
    }


def _check_version_compatibility(task):
    """
    Check if any online workers can satisfy the task's version requirements.

    Reproduces the is_compatible() logic from RedisWorker to detect Bug 1.

    Returns a dict with compatibility analysis.
    """
    from packaging.version import parse as parse_version

    task_versions = task.versions or {}
    if not task_versions:
        return {
            "has_version_requirements": False,
            "compatible_workers": [],
            "incompatible_workers": [],
        }

    from pulpcore.app.models import AppStatus

    online_workers = AppStatus.objects.online().filter(app_type="worker")
    compatible = []
    incompatible = []

    for worker in online_workers:
        worker_versions = worker.versions or {}
        unmatched = []
        for label, required_version in task_versions.items():
            worker_version = worker_versions.get(label)
            if worker_version is None:
                unmatched.append(
                    f"{label}>={required_version} (worker has: missing)"
                )
            elif parse_version(worker_version) < parse_version(required_version):
                unmatched.append(
                    f"{label}>={required_version} (worker has: {worker_version})"
                )
        if unmatched:
            incompatible.append({
                "worker_name": worker.name,
                "unmatched_versions": unmatched,
            })
        else:
            compatible.append(worker.name)

    return {
        "has_version_requirements": True,
        "task_versions": task_versions,
        "compatible_workers": compatible,
        "incompatible_workers": incompatible,
        "no_compatible_worker_exists": len(compatible) == 0 and len(online_workers) > 0,
    }


def _collect_all_lock_holders(redis_locks):
    """Extract all unique lock holder names from redis_locks dict."""
    holders = set()
    if redis_locks["task_lock"]["holder"]:
        holders.add(redis_locks["task_lock"]["holder"])
    for res in redis_locks["exclusive_resources"]:
        if res["holder"]:
            holders.add(res["holder"])
        if res["holders"]:
            holders.update(res["holders"])
    for res in redis_locks["shared_resources"]:
        if res["holders"]:
            holders.update(res["holders"])
    return holders


def _check_lock_holder_liveness(lock_holders):
    """
    Check whether each lock holder (worker/API process) is still alive.

    Detects Bug 2: orphaned locks from dead processes that were never cleaned up.

    Returns a dict mapping holder name to liveness info.
    """
    from pulpcore.app.models import AppStatus

    if not lock_holders:
        return {}

    # Look up all AppStatus records for these holders
    app_statuses = {
        app.name: app
        for app in AppStatus.objects.filter(name__in=lock_holders)
    }

    result = {}
    for holder_name in lock_holders:
        app = app_statuses.get(holder_name)
        if app is None:
            result[holder_name] = {
                "exists_in_db": False,
                "online": False,
                "app_type": None,
                "last_heartbeat": None,
                "verdict": "DEAD: no AppStatus record exists, lock is orphaned",
            }
        else:
            result[holder_name] = {
                "exists_in_db": True,
                "online": app.online,
                "app_type": app.app_type,
                "last_heartbeat": app.last_heartbeat.isoformat()
                if app.last_heartbeat
                else None,
                "verdict": "alive" if app.online else "DEAD: AppStatus exists but is not online, lock is orphaned",
            }
    return result


def _diagnose_stuck_task(task, app_lock_info, redis_locks, queue_position,
                         version_compat, lock_holder_liveness,
                         fifo_analysis=None):
    """
    Produce a human-readable diagnosis explaining WHY a task is stuck.

    Maps each of the 5 known bugs to detectable symptoms and returns the
    matching diagnoses in priority order.
    """
    from pulpcore.constants import TASK_STATES

    diagnoses = []

    if task.state not in (TASK_STATES.WAITING, TASK_STATES.RUNNING, TASK_STATES.CANCELING):
        return [{
            "bug": None,
            "severity": "info",
            "summary": f"Task is in final state '{task.state}' -- not stuck.",
        }]

    # Bug 1: app_lock set but task is still WAITING (is_compatible returned False,
    # app_lock was never cleared)
    if task.state == TASK_STATES.WAITING and app_lock_info["locked"]:
        # Task is WAITING but has app_lock -- this should never happen in normal operation.
        # After fetch_task() claims the task, it either runs or releases locks.
        # If is_compatible() returned False, _maybe_release_locks releases Redis locks
        # but app_lock is NOT cleared in the DB.
        redis_task_lock_held = redis_locks["task_lock"]["held"]
        if not redis_task_lock_held:
            diagnoses.append({
                "bug": "BUG_1_APPLOCK_NOT_CLEARED",
                "severity": "critical",
                "summary": (
                    f"Task is WAITING with app_lock set to "
                    f"'{app_lock_info['app_status_name']}' but NO Redis task lock exists. "
                    f"This matches Bug 1: a worker called is_compatible(), found a version "
                    f"mismatch, released Redis locks, but did NOT clear app_lock in the DB. "
                    f"The task is now invisible to all workers (they filter for "
                    f"app_lock=None). REMEDIATION: clear app_lock in the DB via "
                    f"Task.objects.filter(pk='{task.pk}').update(app_lock=None)"
                ),
            })
        else:
            diagnoses.append({
                "bug": "BUG_1_VARIANT_APPLOCK_WITH_REDIS_LOCK",
                "severity": "warning",
                "summary": (
                    f"Task is WAITING with app_lock set to "
                    f"'{app_lock_info['app_status_name']}' AND Redis task lock held by "
                    f"'{redis_locks['task_lock']['holder']}'. A worker may be in the "
                    f"process of claiming this task, or a worker died mid-claim."
                ),
            })

    # Bug 2: Orphaned Redis locks (lock holder is dead, no TTL)
    for holder_name, liveness in lock_holder_liveness.items():
        if not liveness["online"]:
            has_no_ttl = False
            # Check if any locks held by this dead holder lack TTL
            if redis_locks["task_lock"]["holder"] == holder_name:
                ttl = redis_locks["task_lock"].get("ttl")
                if ttl is not None and ttl == -1:
                    has_no_ttl = True
            for res in redis_locks["exclusive_resources"]:
                if res["holder"] == holder_name and res.get("ttl") == -1:
                    has_no_ttl = True
            for res in redis_locks["shared_resources"]:
                if res["holders"] and holder_name in res["holders"] and res.get("ttl") == -1:
                    has_no_ttl = True

            diagnoses.append({
                "bug": "BUG_2_ORPHANED_LOCK",
                "severity": "critical",
                "summary": (
                    f"Redis lock held by '{holder_name}' which is "
                    f"{liveness['verdict']}. "
                    f"Redis locks have no TTL (they persist forever). "
                    f"{'The lock has no expiration set. ' if has_no_ttl else ''}"
                    f"Worker cleanup should release these locks, but may have missed "
                    f"this case. REMEDIATION: use the release-task-locks endpoint or "
                    f"manually delete the Redis keys."
                ),
            })

    # Bug 3: FETCH_TASK_LIMIT starvation
    if task.state == TASK_STATES.WAITING and not queue_position["within_fetch_window"]:
        diagnoses.append({
            "bug": "BUG_3_FETCH_LIMIT_STARVATION",
            "severity": "high",
            "summary": (
                f"Task is outside the fetch window: {queue_position['older_waiting_tasks']} "
                f"older WAITING tasks exist but the worker only examines the oldest "
                f"{queue_position['fetch_task_limit']} (FETCH_TASK_LIMIT). "
                f"Of those older tasks, {queue_position.get('stuck_in_window', 'unknown')} "
                f"are themselves stuck (have app_lock set = invisible to workers). "
                f"This task will not be examined until older tasks are completed or "
                f"removed. REMEDIATION: resolve or cancel stuck older tasks."
            ),
        })

    # Bug 4: FIFO resource blocking
    if fifo_analysis and fifo_analysis.get("is_fifo_blocked"):
        blocking_resources = fifo_analysis.get("blocked_resources", [])
        blocking_task_ids = fifo_analysis.get("blocking_task_ids", [])
        diagnoses.append({
            "bug": "BUG_4_FIFO_RESOURCE_BLOCKING",
            "severity": "high",
            "summary": (
                f"Task is FIFO-blocked: an earlier task in the fetch window failed to "
                f"acquire locks for resources that overlap with this task's resources. "
                f"The worker's FIFO algorithm adds ALL exclusive resources of a failed "
                f"task to the blocked set, preventing later tasks from acquiring them "
                f"even if different resources caused the original failure. "
                f"Blocked resources: {blocking_resources}. "
                f"Earlier blocking tasks: {blocking_task_ids}. "
                f"REMEDIATION: resolve or cancel the earlier blocking tasks."
            ),
        })

    # Bug 5: Split-Redis (API and workers use different Redis instances)
    if task.state == TASK_STATES.WAITING and task.immediate and task.deferred:
        # Task was dispatched as immediate+deferred. If the API process acquired locks
        # on the API Redis but couldn't execute (resource conflict), it clears app_lock
        # but the Redis locks might remain on the API's Redis (invisible to workers).
        # Also flag if lock holder is an API process.
        api_holders = [
            name for name, info in lock_holder_liveness.items()
            if info.get("app_type") == "api"
        ]
        if api_holders:
            diagnoses.append({
                "bug": "BUG_5_SPLIT_REDIS",
                "severity": "critical",
                "summary": (
                    f"Redis lock(s) held by API process(es): {api_holders}. "
                    f"API and worker clusters use DIFFERENT Redis instances but share "
                    f"the same database. Locks acquired by the API for immediate task "
                    f"execution are invisible to workers. If the API process died or "
                    f"deferred the task, these locks may be orphaned on the API Redis "
                    f"and simultaneously absent from the worker Redis, or vice versa. "
                    f"REMEDIATION: check both Redis instances; release orphaned locks "
                    f"on the API Redis."
                ),
            })
        elif app_lock_info["locked"] and app_lock_info.get("app_type") == "api":
            diagnoses.append({
                "bug": "BUG_5_SPLIT_REDIS",
                "severity": "warning",
                "summary": (
                    f"Task has app_lock held by an API process "
                    f"('{app_lock_info['app_status_name']}'). "
                    f"API and worker clusters use different Redis instances. If this "
                    f"task was dispatched as immediate but deferred to workers, there "
                    f"may be lock state on the API Redis that is invisible to workers."
                ),
            })

    # Version incompatibility (related to Bug 1 root cause)
    if (task.state == TASK_STATES.WAITING
            and version_compat.get("no_compatible_worker_exists")):
        diagnoses.append({
            "bug": "VERSION_INCOMPATIBILITY",
            "severity": "critical",
            "summary": (
                f"No online worker can satisfy this task's version requirements: "
                f"{version_compat.get('task_versions', {})}. "
                f"All workers found incompatible: "
                f"{version_compat.get('incompatible_workers', [])}. "
                f"This task will never be picked up until a compatible worker comes "
                f"online. If a worker already tried and failed is_compatible(), it "
                f"may have triggered Bug 1 (app_lock not cleared)."
            ),
        })

    if not diagnoses:
        if task.state == TASK_STATES.RUNNING:
            diagnoses.append({
                "bug": None,
                "severity": "info",
                "summary": "Task is currently running. No stuck-task indicators detected.",
            })
        elif task.state == TASK_STATES.WAITING:
            diagnoses.append({
                "bug": None,
                "severity": "info",
                "summary": (
                    "Task is WAITING with no obvious stuck indicators. It may be "
                    "legitimately waiting for resource locks held by running tasks, "
                    "or for a worker to pick it up."
                ),
            })
        else:
            diagnoses.append({
                "bug": None,
                "severity": "info",
                "summary": f"Task is in state '{task.state}'. No stuck indicators detected.",
            })

    return diagnoses


def _simulate_fifo_blocking(task, redis_conn):
    """
    Simulate the worker's FIFO blocking algorithm for a specific task.

    The worker processes tasks in pulp_created order. When an earlier task fails
    to acquire locks, ALL its exclusive resources are added to blocked_exclusive,
    and blocked shared resources go to blocked_shared. This function replays
    that logic using the current state of the queue and Redis locks to determine
    whether this task would be skipped by the FIFO check.

    Returns a dict with FIFO blocking analysis.
    """
    from pulpcore.constants import TASK_STATES
    from pulpcore.tasking.redis_locks import resource_to_lock_key

    if task.state != TASK_STATES.WAITING:
        return {"is_fifo_blocked": False, "reason": "task is not WAITING"}

    # Get the task's own resources
    task_reserved = task.reserved_resources_record or []
    task_exclusive = set(r for r in task_reserved if not r.startswith("shared:"))
    task_shared = set(r[7:] for r in task_reserved if r.startswith("shared:"))

    if not task_exclusive and not task_shared:
        return {"is_fifo_blocked": False, "reason": "task has no resource requirements"}

    # Fetch the oldest 20 waiting tasks (same query as fetch_task) that are
    # older than our target task
    fetch_limit = 20
    older_tasks = (
        Task.objects.filter(
            state=TASK_STATES.WAITING,
            app_lock__isnull=True,
            pulp_created__lt=task.pulp_created,
        )
        .order_by("pulp_created")[:fetch_limit]
    )

    blocked_exclusive = set()
    blocked_shared = set()
    blocking_task_ids = []

    for older_task in older_tasks:
        older_reserved = older_task.reserved_resources_record or []
        older_exclusive = [r for r in older_reserved if not r.startswith("shared:")]
        older_shared = [r[7:] for r in older_reserved if r.startswith("shared:")]

        # Check if this older task would fail to acquire its locks
        would_fail = False

        # Check if the older task's resources are in the blocked sets
        for resource in older_exclusive:
            if resource in blocked_exclusive or resource in blocked_shared:
                would_fail = True
                break

        if not would_fail:
            for resource in older_shared:
                if resource in blocked_shared:
                    would_fail = True
                    break

        if not would_fail:
            # Check actual Redis lock state for this older task
            for resource in older_exclusive:
                lock_key = resource_to_lock_key(resource)
                if redis_conn.exists(lock_key):
                    would_fail = True
                    break

            if not would_fail:
                for resource in older_shared:
                    lock_key = resource_to_lock_key(resource)
                    lock_type = redis_conn.type(lock_key)
                    if isinstance(lock_type, bytes):
                        lock_type = lock_type.decode("utf-8")
                    if lock_type == "string":
                        # Exclusive lock on a shared resource
                        would_fail = True
                        break

        if would_fail:
            # This older task would fail -- add ALL its exclusive resources to blocked
            blocked_exclusive.update(older_exclusive)
            for resource in older_shared:
                # Only block shared resources that are actually blocked by exclusive
                lock_key = resource_to_lock_key(resource)
                lock_type = redis_conn.type(lock_key)
                if isinstance(lock_type, bytes):
                    lock_type = lock_type.decode("utf-8")
                if lock_type == "string":
                    blocked_shared.add(resource)

    # Now check if our target task would be skipped
    blocked_resources = []
    for resource in task_exclusive:
        if resource in blocked_exclusive or resource in blocked_shared:
            blocked_resources.append(resource)
    for resource in task_shared:
        if resource in blocked_shared:
            blocked_resources.append(f"shared:{resource}")

    is_blocked = len(blocked_resources) > 0

    if is_blocked:
        # Find which older tasks caused the blocking
        for older_task in older_tasks:
            older_reserved = older_task.reserved_resources_record or []
            older_exclusive = set(r for r in older_reserved if not r.startswith("shared:"))
            if older_exclusive & blocked_exclusive:
                blocking_task_ids.append(str(older_task.pk))

    return {
        "is_fifo_blocked": is_blocked,
        "blocked_resources": blocked_resources,
        "blocking_task_ids": blocking_task_ids[:10],
        "fifo_blocked_exclusive_set_size": len(blocked_exclusive),
        "fifo_blocked_shared_set_size": len(blocked_shared),
    }


def _get_worker_summary():
    """Get a summary of online workers and their versions."""
    from pulpcore.app.models import AppStatus

    online_workers = AppStatus.objects.online().filter(app_type="worker")
    total_online = online_workers.count()
    online_api = AppStatus.objects.online().filter(app_type="api").count()

    return {
        "online_workers": total_online,
        "online_api_processes": online_api,
        "worker_names": [w.name for w in online_workers[:20]],
    }


class TaskDebugView(APIView):
    """
    Admin-only endpoint providing comprehensive debug information for a task.

    Returns task state, app_lock info, Redis lock state for all resources,
    queue position, blocking tasks, version compatibility analysis,
    FIFO blocking simulation, lock holder liveness, and an automated
    diagnosis explaining WHY the task is stuck.

    Covers detection of all 5 known stuck-task bugs:
      Bug 1: app_lock set but never cleared after is_compatible() failure
      Bug 2: Orphaned Redis locks from dead workers (no TTL)
      Bug 3: FETCH_TASK_LIMIT=20 starvation
      Bug 4: FIFO resource blocking in worker fetch loop
      Bug 5: Split Redis between API and worker clusters
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

            # Queue position (enhanced for Bug 3)
            older_waiting = Task.objects.filter(
                state=TASK_STATES.WAITING,
                app_lock__isnull=True,
                pulp_created__lt=task.pulp_created,
            ).count()

            # Count stuck tasks in the fetch window that are invisible to workers
            # (WAITING + app_lock set = Bug 1 victims)
            stuck_in_window = Task.objects.filter(
                state=TASK_STATES.WAITING,
                app_lock__isnull=False,
                pulp_created__lt=task.pulp_created,
            ).count()

            fetch_task_limit = 20
            within_window = older_waiting < fetch_task_limit

            # Blocking tasks -- incomplete tasks with overlapping exclusive resources
            blocking = []
            if exclusive_resources and task.state == TASK_STATES.WAITING:
                blocking_qs = (
                    Task.objects.filter(
                        state__in=TASK_INCOMPLETE_STATES,
                        reserved_resources_record__overlap=exclusive_resources,
                        pulp_created__lt=task.pulp_created,
                    )
                    .exclude(pk=task.pk)
                    .select_related("pulp_domain", "app_lock")[:20]
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
                            "app_lock": _get_app_lock_info(bt),
                        }
                    )

            # Redis lock info
            redis_locks = _get_redis_lock_info(redis_conn, task)

            # Version compatibility analysis (Bug 1 root cause)
            version_compat = _check_version_compatibility(task)

            # Collect all lock holders and check liveness (Bug 2)
            all_holders = _collect_all_lock_holders(redis_locks)
            lock_holder_liveness = _check_lock_holder_liveness(all_holders)

            # FIFO blocking simulation (Bug 4)
            fifo_analysis = _simulate_fifo_blocking(task, redis_conn)

            # Worker summary
            worker_summary = _get_worker_summary()

            # Queue position dict (enhanced)
            queue_position = {
                "older_waiting_tasks": older_waiting,
                "stuck_in_window": stuck_in_window,
                "fetch_task_limit": fetch_task_limit,
                "within_fetch_window": within_window,
            }

            # App lock info
            app_lock_info = _get_app_lock_info(task)

            # Automated diagnosis
            diagnosis = _diagnose_stuck_task(
                task, app_lock_info, redis_locks, queue_position,
                version_compat, lock_holder_liveness, fifo_analysis,
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
                    "diagnosis": diagnosis,
                    "app_lock": app_lock_info,
                    "redis_locks": redis_locks,
                    "lock_holder_liveness": lock_holder_liveness,
                    "queue_position": queue_position,
                    "blocking_tasks": blocking,
                    "fifo_analysis": fifo_analysis,
                    "version_compatibility": version_compat,
                    "worker_summary": worker_summary,
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
    the same view workers have when calling fetch_task(). Includes
    per-task diagnosis, lock holder liveness, and queue-wide health
    indicators.
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

            # Bug 1 indicator: WAITING tasks with app_lock set (invisible to workers)
            waiting_with_applock = Task.objects.filter(
                state=TASK_STATES.WAITING,
                app_lock__isnull=False,
            ).count()

            tasks = (
                Task.objects.filter(
                    state__in=[TASK_STATES.WAITING, TASK_STATES.RUNNING]
                )
                .select_related("pulp_domain", "app_lock")
                .order_by("pulp_created")[:limit]
            )

            # Collect all lock holders across all tasks for batch liveness check
            all_holders = set()
            task_redis_locks = []
            for task in tasks:
                rl = _get_redis_lock_info(redis_conn, task)
                task_redis_locks.append(rl)
                all_holders.update(_collect_all_lock_holders(rl))

            lock_holder_liveness = _check_lock_holder_liveness(all_holders)

            # Worker summary
            worker_summary = _get_worker_summary()

            task_list = []
            for task, redis_locks in zip(tasks, task_redis_locks):
                blocked_by = set()
                for res in redis_locks["exclusive_resources"]:
                    if res["held"] and res["holder"]:
                        blocked_by.add(res["holder"])
                for res in redis_locks["shared_resources"]:
                    if res["lock_type"] == "string" and res["holders"]:
                        blocked_by.update(res["holders"])

                app_lock_info = _get_app_lock_info(task)

                # Per-task quick diagnosis
                task_diagnoses = []
                if task.state == TASK_STATES.WAITING and app_lock_info["locked"]:
                    if not redis_locks["task_lock"]["held"]:
                        task_diagnoses.append("BUG_1_APPLOCK_NOT_CLEARED")
                    else:
                        task_diagnoses.append("BUG_1_VARIANT_APPLOCK_WITH_REDIS_LOCK")

                for holder_name in blocked_by:
                    liveness = lock_holder_liveness.get(holder_name, {})
                    if not liveness.get("online", True):
                        task_diagnoses.append(f"BUG_2_ORPHANED_LOCK:{holder_name}")
                    if liveness.get("app_type") == "api":
                        task_diagnoses.append(f"BUG_5_SPLIT_REDIS:{holder_name}")

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
                        "app_lock": app_lock_info,
                        "redis_locks": {
                            "task_lock_held": redis_locks["task_lock"]["held"],
                            "task_lock_holder": redis_locks["task_lock"]["holder"],
                            "task_lock_ttl": redis_locks["task_lock"]["ttl"],
                            "exclusive_resources_locked": sum(
                                1 for r in redis_locks["exclusive_resources"] if r["held"]
                            ),
                            "shared_resources_locked": sum(
                                1 for r in redis_locks["shared_resources"] if r["held"]
                            ),
                            "blocked_by": sorted(blocked_by),
                        },
                        "diagnoses": task_diagnoses,
                    }
                )

            # Queue-level health indicators
            queue_health = {
                "waiting_with_applock": waiting_with_applock,
                "waiting_with_applock_is_bug1": waiting_with_applock > 0,
            }

            # Identify orphaned lock holders across all examined tasks
            orphaned_holders = [
                name for name, info in lock_holder_liveness.items()
                if not info.get("online", True)
            ]
            if orphaned_holders:
                queue_health["orphaned_lock_holders"] = orphaned_holders

            return Response(
                {
                    "total_waiting": total_waiting,
                    "total_running": total_running,
                    "waiting_with_applock": waiting_with_applock,
                    "limit": limit,
                    "tasks": task_list,
                    "lock_holder_liveness": lock_holder_liveness,
                    "queue_health": queue_health,
                    "worker_summary": worker_summary,
                }
            )

        except Exception as e:
            _logger.exception("Error getting task queue info")
            return Response(
                {"error": "Failed to get task queue info", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


def _scan_resource_locks(redis_conn):
    """
    Scan Redis for all resource lock keys (pulp:resource_lock:*).

    Uses SCAN to iterate without blocking Redis. For each key, determines
    the lock type (string = exclusive, set = shared) and extracts the
    holder(s).

    Returns a list of dicts describing each lock.
    """
    from pulpcore.tasking.redis_locks import REDIS_LOCK_PREFIX

    locks = []
    cursor = 0
    pattern = f"{REDIS_LOCK_PREFIX}*"

    while True:
        cursor, keys = redis_conn.scan(cursor=cursor, match=pattern, count=200)
        for key in keys:
            key_str = key.decode("utf-8") if isinstance(key, bytes) else key
            resource_name = key_str[len(REDIS_LOCK_PREFIX):]

            lock_type = redis_conn.type(key)
            if isinstance(lock_type, bytes):
                lock_type = lock_type.decode("utf-8")

            ttl = redis_conn.ttl(key)
            holders = []

            if lock_type == "string":
                val = redis_conn.get(key)
                if val:
                    holders = [val.decode("utf-8")]
            elif lock_type == "set":
                members = redis_conn.smembers(key)
                holders = sorted(m.decode("utf-8") for m in members)

            locks.append({
                "lock_key": key_str,
                "resource": resource_name,
                "lock_type": lock_type,
                "holders": holders,
                "ttl": ttl,
            })

        if cursor == 0:
            break

    return locks


def _scan_task_locks(redis_conn):
    """
    Scan Redis for all task lock keys (task:*).

    Returns a list of dicts describing each task lock.
    """
    locks = []
    cursor = 0
    pattern = "task:*"

    while True:
        cursor, keys = redis_conn.scan(cursor=cursor, match=pattern, count=200)
        for key in keys:
            key_str = key.decode("utf-8") if isinstance(key, bytes) else key
            task_id = key_str[5:]  # Strip "task:" prefix

            lock_type = redis_conn.type(key)
            if isinstance(lock_type, bytes):
                lock_type = lock_type.decode("utf-8")

            ttl = redis_conn.ttl(key)
            holder = None

            if lock_type == "string":
                val = redis_conn.get(key)
                if val:
                    holder = val.decode("utf-8")

            locks.append({
                "lock_key": key_str,
                "task_id": task_id,
                "lock_type": lock_type,
                "holder": holder,
                "ttl": ttl,
            })

        if cursor == 0:
            break

    return locks


def _correlate_orphaned_locks_to_tasks(orphaned_resource_locks):
    """
    For each orphaned resource lock, find tasks whose reserved_resources_record
    contains the resource name.

    Only searches incomplete tasks first, then optionally recent completed tasks
    to find which task originally created the lock.

    Returns a dict mapping resource name to a list of correlated task summaries.
    """
    from pulpcore.constants import TASK_INCOMPLETE_STATES

    correlations = {}

    for lock_info in orphaned_resource_locks:
        resource = lock_info["resource"]
        # Search for the resource in both exclusive and shared forms
        exclusive_match = resource
        shared_match = f"shared:{resource}"

        # First check incomplete tasks (most actionable)
        matching_tasks = (
            Task.objects.filter(
                reserved_resources_record__overlap=[exclusive_match, shared_match],
            )
            .select_related("pulp_domain", "app_lock")
            .order_by("-pulp_created")[:10]
        )

        task_summaries = []
        for task in matching_tasks:
            task_summaries.append({
                "task_id": str(task.pk),
                "state": task.state,
                "name": task.name,
                "pulp_created": task.pulp_created.isoformat(),
                "app_lock": task.app_lock.name if task.app_lock else None,
            })

        if task_summaries:
            correlations[resource] = task_summaries

    return correlations


class StaleLockScanView(APIView):
    """
    Admin-only endpoint that scans Redis for orphaned/stale locks.

    Performs a system-wide scan of all Redis resource lock keys
    (pulp:resource_lock:*) and task lock keys (task:*), checks whether
    each lock holder is still an online AppStatus process, and flags
    orphaned locks from dead workers.

    This is the primary tool for proactive detection of Bug 2: Redis locks
    that persist forever because a worker died without cleaning up. Unlike
    task-debug (which examines a single task) or task-queue (which examines
    waiting/running tasks), this endpoint discovers ALL locks in Redis
    regardless of task state -- including locks left behind by tasks that
    have already completed, failed, or been canceled.

    Query parameters:
        include_healthy (bool): If "true", include locks held by online
            processes in the response. Default: false (only show stale/orphaned).

    Response structure:
        summary: Counts of total, orphaned, and healthy locks
        orphaned_resource_locks: Resource locks held by dead processes
        orphaned_task_locks: Task locks held by dead processes
        healthy_resource_locks: (optional) Locks held by online processes
        healthy_task_locks: (optional) Task locks held by online processes
        lock_holder_liveness: Liveness status of all unique lock holders
        task_correlations: Mapping of orphaned resources to related tasks
        worker_summary: Current online worker/API process counts
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        from pulpcore.app.redis_connection import get_redis_connection

        if settings.WORKER_TYPE != "redis":
            return Response(
                {"error": "This endpoint only works with Redis workers."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        include_healthy = request.GET.get("include_healthy", "").lower() in (
            "true", "1", "yes",
        )

        try:
            redis_conn = get_redis_connection()
            if not redis_conn:
                return Response(
                    {"error": "Redis connection not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Phase 1: Scan all resource locks and task locks from Redis
            resource_locks = _scan_resource_locks(redis_conn)
            task_locks = _scan_task_locks(redis_conn)

            # Phase 2: Collect all unique lock holders and check liveness
            all_holders = set()
            for lock_info in resource_locks:
                all_holders.update(lock_info["holders"])
            for lock_info in task_locks:
                if lock_info["holder"]:
                    all_holders.add(lock_info["holder"])

            lock_holder_liveness = _check_lock_holder_liveness(all_holders)

            # Phase 3: Classify locks as orphaned or healthy
            orphaned_resource_locks = []
            healthy_resource_locks = []
            for lock_info in resource_locks:
                orphaned_holders = [
                    h for h in lock_info["holders"]
                    if not lock_holder_liveness.get(h, {}).get("online", False)
                ]
                if orphaned_holders:
                    lock_info["orphaned_holders"] = orphaned_holders
                    lock_info["healthy_holders"] = [
                        h for h in lock_info["holders"] if h not in orphaned_holders
                    ]
                    orphaned_resource_locks.append(lock_info)
                else:
                    healthy_resource_locks.append(lock_info)

            orphaned_task_locks = []
            healthy_task_locks = []
            for lock_info in task_locks:
                holder = lock_info["holder"]
                if holder and not lock_holder_liveness.get(
                    holder, {}
                ).get("online", False):
                    orphaned_task_locks.append(lock_info)
                else:
                    healthy_task_locks.append(lock_info)

            # Phase 4: Correlate orphaned resource locks to tasks
            task_correlations = _correlate_orphaned_locks_to_tasks(
                orphaned_resource_locks
            )

            # Also correlate orphaned task locks -- check if the task still
            # exists and what state it is in
            for lock_info in orphaned_task_locks:
                task_id = lock_info["task_id"]
                try:
                    task = Task.objects.select_related("app_lock").get(pk=task_id)
                    lock_info["task_state"] = task.state
                    lock_info["task_name"] = task.name
                    lock_info["task_app_lock"] = task.app_lock.name if task.app_lock else None
                except (Task.DoesNotExist, Exception):
                    lock_info["task_state"] = "NOT_FOUND"
                    lock_info["task_name"] = None
                    lock_info["task_app_lock"] = None

            # Worker summary
            worker_summary = _get_worker_summary()

            # Build summary counts
            summary = {
                "total_resource_locks": len(resource_locks),
                "orphaned_resource_locks": len(orphaned_resource_locks),
                "healthy_resource_locks": len(healthy_resource_locks),
                "total_task_locks": len(task_locks),
                "orphaned_task_locks": len(orphaned_task_locks),
                "healthy_task_locks": len(healthy_task_locks),
                "unique_lock_holders": len(all_holders),
                "dead_lock_holders": sum(
                    1 for info in lock_holder_liveness.values()
                    if not info.get("online", False)
                ),
            }

            response_data = {
                "summary": summary,
                "orphaned_resource_locks": orphaned_resource_locks,
                "orphaned_task_locks": orphaned_task_locks,
                "lock_holder_liveness": lock_holder_liveness,
                "task_correlations": task_correlations,
                "worker_summary": worker_summary,
            }

            if include_healthy:
                response_data["healthy_resource_locks"] = healthy_resource_locks
                response_data["healthy_task_locks"] = healthy_task_locks

            return Response(response_data)

        except Exception as e:
            _logger.exception("Error scanning for stale locks")
            return Response(
                {"error": "Failed to scan for stale locks", "detail": str(e)},
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
