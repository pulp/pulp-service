from django.urls import path

from .admin import admin_site
from .viewsets import RedirectCheck, InternalServerErrorCheck, InternalServerErrorCheckWithException, DebugAuthenticationHeadersView


urlpatterns = [
    path("api/pulp-admin/", admin_site.urls),
    path("api/pulp/redirect-check/", RedirectCheck.as_view()),
    path("api/pulp/internal-server-error-check/", InternalServerErrorCheck.as_view()),
    path("api/pulp/raise-exception-check/", InternalServerErrorCheckWithException.as_view()),
    path("api/pulp/debug_auth_header/", DebugAuthenticationHeadersView.as_view()),
]
