from django.urls import path
from .admin import admin_site
from .viewsets import (
    DebugAuthenticationHeadersView,
    InternalServerErrorCheck,
    InternalServerErrorCheckWithException,
    RedirectCheck,
    TaskViewSet,
    VulnerabilityReport,
    AnsibleLogReport,
)

urlpatterns = [
    path("api/pulp-admin/", admin_site.urls),
    path("api/pulp/redirect-check/", RedirectCheck.as_view()),
    path("api/pulp/internal-server-error-check/", InternalServerErrorCheck.as_view()),
    path("api/pulp/raise-exception-check/", InternalServerErrorCheckWithException.as_view()),
    path("api/pulp/debug_auth_header/", DebugAuthenticationHeadersView.as_view()),
    path("api/pulp/admin/tasks/", TaskViewSet.as_view({"get": "list"})),
    path("api/pulp/vuln_report/", VulnerabilityReport.as_view({"get": "list", "post": "post"})),
    path("api/pulp/vuln_report/<uuid:uuid>/", VulnerabilityReport.as_view({"get": "get"})),
    path("api/pulp/<slug:domain_name>/ansible-logs/<uuid:id>/",AnsibleLogReport.as_view({"get": "retrieve"})),
    path("api/pulp/<slug:domain_name>/ansible-logs/",AnsibleLogReport.as_view({"post": "create", "get": "list"})),
]
