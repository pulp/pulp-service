from django.urls import path

from .admin import admin_site
from .viewsets import RedirectCheck, InternalServerErrorCheck, InternalServerErrorCheckWithException


urlpatterns = [
    path("api/pulp-admin/", admin_site.urls),
    path("api/redirect-check/", RedirectCheck.as_view()),
    path("api/internal-server-error-check/", InternalServerErrorCheck.as_view()),
    path("api/raise-exception-check/", InternalServerErrorCheckWithException.as_view()),
]
