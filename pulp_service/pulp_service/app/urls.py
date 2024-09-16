from django.urls import path

from .admin import admin_site

urlpatterns = [
    path("public/pulp-admin/", admin_site.urls),
]
