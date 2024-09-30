from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.contrib import admin

from .models import DomainOrg

from pulpcore.app.models import Domain


class PulpAdminSite(admin.AdminSite):
    site_header = "Pulp administration"


class DomainOrgAdmin(admin.ModelAdmin):
    list_display = ["domain", "user", "org_id"]
    list_editable = ["user", "org_id"]
    list_filter = ["user", "org_id"]


class DomainAdmin(admin.ModelAdmin):
    list_display = ["name", "description", "storage_class"]
    list_filter = ["name", "description", "storage_class"]


admin_site = PulpAdminSite(name="myadmin")

admin_site.register(DomainOrg, DomainOrgAdmin)
admin_site.register(User, UserAdmin)
admin_site.register(Domain, DomainAdmin)
