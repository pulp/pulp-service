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
from django.shortcuts import redirect

from drf_spectacular.utils import extend_schema, extend_schema_view

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.mixins import DestroyModelMixin, ListModelMixin, RetrieveModelMixin

from pulpcore.plugin.viewsets import OperationPostponedResponse
from pulpcore.plugin.viewsets import ContentGuardViewSet, NamedModelViewSet, RolesMixin, TaskViewSet
from pulpcore.plugin.serializers import AsyncOperationResponseSerializer
from pulpcore.plugin.tasking import dispatch
from pulpcore.app.models import Domain, Group
from pulpcore.app.serializers import DomainSerializer


from pulp_service.app.authentication import RHServiceAccountCertAuthentication

from pulp_service.app.authorization import DomainBasedPermission
from pulp_service.app.models import FeatureContentGuard
from pulp_service.app.models import VulnerabilityReport as VulnReport
from pulp_service.app.serializers import (
    ContentScanSerializer,
    FeatureContentGuardSerializer,
    VulnerabilityReportSerializer,
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
    Returns the content of the authentication headers.
    """

    authentication_classes = [RHServiceAccountCertAuthentication]
    permission_classes = []

    def get(self, request=None, path=None, pk=None):
        if not settings.AUTHENTICATION_HEADER_DEBUG:
            raise PermissionError("Access denied.")
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

        json_header_value = json.loads(header_decoded_content)
        return Response(data=json_header_value)


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
            dispatch(
                'pulp_service.app.tasks.util.no_op_task',
                exclusive_resources=[str(uuid4())]
            )
                
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
                'pulp_service.app.tasks.util.no_op_task',
                exclusive_resources=[random.choice(exclusive_resources_list)]
            )
                
            task_count = task_count + 1

        return Response({"tasks_executed": task_count})


class RDSConnectionTestDispatcherView(APIView):
    """
    Endpoint to dispatch RDS Proxy connection timeout tests remotely.

    POST body format:
    {
        "tests": ["test_1_idle_connection", "test_2_active_heartbeat"],
        "run_sequentially": true  // optional, default false
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
        if not settings.DEBUG or not settings.RDS_CONNECTION_TESTS_ENABLED:
            _logger.warning(
                f"Unauthorized RDS test access attempt from {request.META.get('REMOTE_ADDR', 'unknown')}"
            )
            return Response(
                {
                    "error": "RDS connection tests are not enabled.",
                    "hint": "Set RDS_CONNECTION_TESTS_ENABLED=True in settings or enable DEBUG mode."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        tests = request.data.get('tests', [])
        run_sequentially = request.data.get('run_sequentially', False)

        if not tests:
            return Response(
                {"error": "No tests specified. Provide a list of test names."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate test names
        if invalid_tests := [t for t in tests if t not in self.AVAILABLE_TESTS]:
            return Response(
                {
                    "error": f"Invalid test names: {invalid_tests}",
                    "available_tests": list(self.AVAILABLE_TESTS.keys())
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        dispatched_tasks = []

        # Check if domain support is enabled
        domain_enabled = getattr(settings, 'DOMAIN_ENABLED', False)

        # For sequential execution, use a shared lock resource
        # This forces tasks to run one at a time
        sequential_lock = []
        if run_sequentially:
            from uuid import uuid4
            sequential_lock = [f"rds-test-sequential-{uuid4()}"]

        for test_name in tests:
            task_func = self.AVAILABLE_TESTS[test_name]

            # Dispatch the task
            task = dispatch(
                task_func,
                exclusive_resources=sequential_lock,  # Empty list for parallel, shared lock for sequential
            )

            # Get task ID - use current_id() if available, fallback to pk
            task_id = task.current_id() or task.pk

            # Build task href based on domain support
            if domain_enabled:
                # Domain-aware path: /pulp/{domain}/api/v3/tasks/{task_id}/
                domain_name = getattr(task.pulp_domain, 'name', 'default')
                task_href = f"/pulp/{domain_name}/api/v3/tasks/{task_id}/"
            else:
                # Standard path: /pulp/api/v3/tasks/{task_id}/
                task_href = f"/pulp/api/v3/tasks/{task_id}/"

            dispatched_tasks.append({
                "test_name": test_name,
                "task_id": str(task_id),
                "task_href": task_href,
            })

        return Response({
            "message": f"Dispatched {len(dispatched_tasks)} test(s)",
            "tasks": dispatched_tasks,
            "run_sequentially": run_sequentially,
            "note": "Each test runs for approximately 50 minutes. Monitor task status via task_href."
        }, status=status.HTTP_202_ACCEPTED)

    def get(self, request):
        """
        Get available tests and their descriptions.

        This endpoint is always accessible for documentation purposes.
        """
        return Response({
            "available_tests": list(self.AVAILABLE_TESTS.keys()),
            "descriptions": {
                "test_1_idle_connection": "Idle connection test (50 min) - baseline timeout test",
                "test_2_active_heartbeat": "Active heartbeat test (50 min) - periodic queries",
                "test_3_long_transaction": "Long transaction test (50 min) - idle transaction",
                "test_4_transaction_with_work": "Transaction with work test (50 min) - active transaction",
                "test_5_session_variable": "Session variable test (50 min) - connection pinning via SET",
                "test_6_listen_notify": "LISTEN/NOTIFY test (50 min) - CRITICAL: real worker behavior",
                "test_7_listen_with_activity": "LISTEN with activity test (50 min) - periodic notifications",
            },
            "usage": {
                "endpoint": "/api/pulp/rds-connection-tests/",
                "method": "POST",
                "body": {
                    "tests": ["test_1_idle_connection", "test_2_active_heartbeat"],
                    "run_sequentially": False
                }
            }
        })


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
        domain_name = request.data.get('name')
        
        if not domain_name:
            return Response(
                {"error": "Domain name is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
                
        if not user.groups.exists():
            # User has no groups, create one with a unique name or reuse existing
            group_name = f"domain-{domain_name}"
            _logger.info(f"User {user.username} has no groups. Creating or finding group '{group_name}' for domain creation.")
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
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        else:
            _logger.info(f"User {user.username} already belongs to a group.")
        
        # Prepare data with defaults from default domain if needed
        data = request.data.copy()
        
        # Always get storage settings from model domain (ignore user input)
        try:
            model_domain = Domain.objects.get(name='template-domain-s3')
            data['storage_settings'] = model_domain.storage_settings
            data['storage_class'] = model_domain.storage_class
            data['pulp_labels'] = model_domain.pulp_labels
        except Domain.DoesNotExist:
            _logger.error("Model domain 'template-domain-s3' not found")
            return Response(
                {"error": "Model domain 'template-domain-s3' not found. Please create it first with correct storage settings."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        serializer = DomainSerializer(data=data)
        serializer.is_valid(raise_exception=True)
                
        # Perform the creation with validated data
        with transaction.atomic():
            domain = serializer.save()
            
        response_data = DomainSerializer(domain, context={'request': request}).data
        
        return Response(response_data, status=status.HTTP_201_CREATED)
