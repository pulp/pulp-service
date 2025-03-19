import json
import logging
from base64 import b64decode
from binascii import Error as Base64DecodeError
from gettext import gettext as _

from django.conf import settings
from django.db.models.query import QuerySet
from django.http import Http404
from django.shortcuts import redirect

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from pulpcore.app.util import get_domain


from pulpcore.app.models import Domain
from pulpcore.app.response import OperationPostponedResponse
from pulpcore.app.viewsets import (
    ContentGuardViewSet,
    NamedModelViewSet,
    RolesMixin,
    TaskViewSet,
)
from pulpcore.plugin.tasking import dispatch

from pulp_service.app.authentication import RHServiceAccountCertAuthentication
from pulp_service.app.models import (
    AnsibleLogReport,
    FeatureContentGuard,
    VulnerabilityReport as VulnReport,
)
from pulp_service.app.serializers import (
    AnsibleLogAnalysisSerializer,
    AnsibleLogReportSerializer,
    ContentScanSerializer,
    FeatureContentGuardSerializer,
    VulnerabilityReportSerializer,
)
from pulp_service.app.tasks.ansible_log_parser import dispatch_ansible_log_analysis
from pulp_service.app.tasks.package_scan import check_content

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


class VulnerabilityReport(ViewSet):

    def list(self, request):
        queryset = VulnReport.objects.all()
        serializer = VulnerabilityReportSerializer(queryset, many=True)
        return Response(serializer.data)

    def get(self, request, uuid):
        queryset = VulnReport.objects.get(id=uuid)
        serializer = VulnerabilityReportSerializer(queryset, many=False)
        return Response(serializer.data)

    def post(self, request):
        serialized_data = ContentScanSerializer(data=request.data)
        serialized_data.is_valid(raise_exception=True)
        repo_version_pk = serialized_data.data["repo_version"]
        task = dispatch(check_content, kwargs={"repo_version_pk": repo_version_pk})
        return OperationPostponedResponse(task, request)

class AnsibleLogReportViewset(NamedModelViewSet, ListModelMixin, RetrieveModelMixin):
    """
    ViewSet for analyzing Ansible logs for errors.
    """
    queryset = AnsibleLogReport.objects.all()
    endpoint_name = 'ansible-logs'
    serializer_class = AnsibleLogReportSerializer
    
    def create(self, request):
        """
        Analyze an Ansible log file for errors asynchronously.
        
        Expects JSON with 'url' field.
        Returns a task that can be used to retrieve results.
        """
        serializer = AnsibleLogAnalysisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        log_url = serializer.validated_data['url']
        role_filter = serializer.validated_data.get('role')
        domain = get_domain()
    
        try:
            task = dispatch_ansible_log_analysis(log_url, role_filter)
            return OperationPostponedResponse(task, request)
        except Exception as e:
            _logger.exception("Failed to dispatch log analysis task")
            return Response(
                {"error": _("Failed to analyze log: %(error)s") % {"error": str(e)}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def list(self, request):
        """List all reports in the domain."""
        domain = get_domain()
        
        if domain:
            queryset = AnsibleLogReport.objects.filter(pulp_domain=domain).order_by('-pulp_created')
        else:
            return Response(
                {"error": _("Missing domain information.")},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = AnsibleLogReportSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk):
        """Get a specific report within the domain."""
        domain = get_domain()
        
        try:
            if domain:
                report = AnsibleLogReport.objects.get(pulp_id=pk, pulp_domain=domain)
            else:
                return Response(
                    {"error": _("Missing domain information.")},
                    status=status.HTTP_400_BAD_REQUEST
                )
            serializer = AnsibleLogReportSerializer(report)
            return Response(serializer.data)
        except AnsibleLogReport.DoesNotExist:
            _logger.warning(f"Report {pk} not found")
            return Response({"error": _("Report not found")}, status=status.HTTP_404_NOT_FOUND)