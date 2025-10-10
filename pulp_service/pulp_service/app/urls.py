from django.urls import path

from .admin import admin_site
from .viewsets import (
    CreateDomainView,
    DebugAuthenticationHeadersView,
    InternalServerErrorCheck,
    InternalServerErrorCheckWithException,
    RedirectCheck,
    TaskViewSet,
    TaskIngestionDispatcherView,
    TaskIngestionRandomResourceLockDispatcherView,
)


urlpatterns = [
    path("api/pulp-mgmt/", admin_site.urls),
    path("api/pulp/redirect-check/", RedirectCheck.as_view()),
    path("api/pulp/internal-server-error-check/", InternalServerErrorCheck.as_view()),
    path("api/pulp/raise-exception-check/", InternalServerErrorCheckWithException.as_view()),
    path("api/pulp/debug_auth_header/", DebugAuthenticationHeadersView.as_view()),
    path("api/pulp/admin/tasks/", TaskViewSet.as_view({"get": "list"})),
    path("api/pulp/test/tasks/", TaskIngestionDispatcherView.as_view()),
    path("api/pulp/test/random_lock_tasks/", TaskIngestionRandomResourceLockDispatcherView.as_view()),
    path("api/pulp/create-domain/", CreateDomainView.as_view()),
]
