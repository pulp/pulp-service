from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .admin import admin_site
from .viewsets import (
    DebugAuthenticationHeadersView,
    InternalServerErrorCheck,
    InternalServerErrorCheckWithException,
    RedirectCheck,
    TaskViewSet,
    VulnerabilityReport,
)

router = SimpleRouter(trailing_slash=False)
router.register(
    r"^api/pulp/(?P<pulp_domain>.+)/vuln_report/(?P<uuid>.+)/",
    VulnerabilityReport,
    basename="vuln-report",
)

urlpatterns = [
    path("api/pulp-admin/", admin_site.urls),
    path("api/pulp/redirect-check/", RedirectCheck.as_view()),
    path("api/pulp/internal-server-error-check/", InternalServerErrorCheck.as_view()),
    path("api/pulp/raise-exception-check/", InternalServerErrorCheckWithException.as_view()),
    path("api/pulp/debug_auth_header/", DebugAuthenticationHeadersView.as_view()),
    path("api/pulp/admin/tasks/", TaskViewSet.as_view({"get": "list"})),
    path(
        "api/pulp/<slug:pulp_domain>/vuln_report/",
        VulnerabilityReport.as_view({"get": "retrieve", "post": "create"}),
    ),
    path("", include(router.urls)),
]
