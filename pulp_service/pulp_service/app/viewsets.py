import json
import logging

from base64 import b64decode
from binascii import Error as Base64DecodeError

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.query import QuerySet
from django.http.response import Http404
from django.shortcuts import redirect

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from pulpcore.app.response import OperationPostponedResponse
from pulpcore.app.viewsets import ContentGuardViewSet, RolesMixin, TaskViewSet
from pulpcore.plugin.tasking import dispatch
from pulpcore.plugin.util import get_domain

from pulp_service.app.authentication import RHServiceAccountCertAuthentication
from pulp_service.app.models import FeatureContentGuard
from pulp_service.app.models import VulnerabilityReport as VulnReport
from pulp_service.app.serializers import (
    ContentScanSerializer,
    FeatureContentGuardSerializer,
    VulnerabilityReportSerializer,
)
from pulp_service.app.tasks.package_scan import check_npm_package, check_content_from_repo_version

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

    def retrieve(self, request):
        queryset = VulnReport.objects.filter(pulp_domain=get_domain())
        serializer = VulnerabilityReportSerializer(queryset, many=True)
        return Response(serializer.data)

    def list(self, request, uuid):
        try:
            queryset = VulnReport.objects.get(id=uuid, pulp_domain=get_domain())
        except ObjectDoesNotExist:
            raise Http404(f"No VulnerabilityReport found with uuid {uuid}")
        serializer = VulnerabilityReportSerializer(queryset, many=False)
        return Response(serializer.data)

    def create(self, request):
        serialized_data = ContentScanSerializer(data=request.data)
        serialized_data.is_valid(raise_exception=True)

        """Dispatch a task to scan the Content Units from a Repository"""
        if repo_version_pk := serialized_data.data.get("repo_version", None):
            dispatch_task, kwargs = check_content_from_repo_version, {
                "repo_version_pk": repo_version_pk
            }

        """Dispatch a task to scan the npm dependencies' vulnerabilities"""
        if serialized_data.data.get("package_json", None):
            temp_file_pk = serialized_data.verify_file()
            dispatch_task, kwargs = check_npm_package, {"npm_package": temp_file_pk}

        task = dispatch(dispatch_task, kwargs=kwargs)
        return OperationPostponedResponse(task, request)
