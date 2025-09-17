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
from pulpcore.app.models import Domain
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
