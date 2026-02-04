from django.urls import path, include

from .admin import admin_site
from .viewsets import (
    CreateDomainView,
    DatabaseTriggersView,
    DebugAuthenticationHeadersView,
    InternalServerErrorCheck,
    InternalServerErrorCheckWithException,
    MemoryHeapSnapshotView,
    MemoryObjectTypesView,
    MemrayProfileView,
    MemrayProxyProfileView,
    PodInfoView,
    ProxyMemoryProfileView,
    RDSConnectionTestDispatcherView,
    RedirectCheck,
    ReleaseTaskLocksView,
    TaskViewSet,
    TaskIngestionDispatcherView,
    TaskIngestionRandomResourceLockDispatcherView,
)


urlpatterns = [
    path("api/pulp-mgmt/", admin_site.urls),
    path("api/pulp/hijack/", include("hijack.urls")),
    path("api/pulp/redirect-check/", RedirectCheck.as_view()),
    path("api/pulp/internal-server-error-check/", InternalServerErrorCheck.as_view()),
    path("api/pulp/raise-exception-check/", InternalServerErrorCheckWithException.as_view()),
    path("api/pulp/debug_auth_header/", DebugAuthenticationHeadersView.as_view()),
    path("api/pulp/debug/database-triggers/", DatabaseTriggersView.as_view()),
    path("api/pulp/debug/release-task-locks/", ReleaseTaskLocksView.as_view()),
    path("api/pulp/debug/pod-info/", PodInfoView.as_view()),
    path("api/pulp/debug/memory/heap/", MemoryHeapSnapshotView.as_view()),
    path("api/pulp/debug/memory/types/", MemoryObjectTypesView.as_view()),
    path("api/pulp/debug/memory/proxy/", ProxyMemoryProfileView.as_view()),
    path("api/pulp/debug/memory/memray/start/", MemrayProfileView.as_view()),
    path("api/pulp/debug/memory/memray/stop/", MemrayProfileView.as_view()),
    path("api/pulp/debug/memory/memray/status/", MemrayProfileView.as_view()),
    path("api/pulp/debug/memory/memray/files/", MemrayProfileView.as_view()),
    path("api/pulp/debug/memory/memray/proxy/", MemrayProxyProfileView.as_view()),
    path("api/pulp/admin/tasks/", TaskViewSet.as_view({"get": "list"})),
    path("api/pulp/test/tasks/", TaskIngestionDispatcherView.as_view()),
    path("api/pulp/test/random_lock_tasks/", TaskIngestionRandomResourceLockDispatcherView.as_view()),
    path("api/pulp/rds-connection-tests/", RDSConnectionTestDispatcherView.as_view()),
    path("api/pulp/create-domain/", CreateDomainView.as_view()),
]
