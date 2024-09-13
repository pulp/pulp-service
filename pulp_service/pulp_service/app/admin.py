from django.contrib import admin

from .models import DomainOrg


class PulpAdminSite(admin.AdminSite):
    site_header = "Pulp administration"


admin_site = PulpAdminSite(name="myadmin")
admin_site.register(DomainOrg)