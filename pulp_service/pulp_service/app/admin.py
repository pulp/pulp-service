from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.contrib import admin

from .models import DomainOrg


class PulpAdminSite(admin.AdminSite):
    site_header = "Pulp administration"


admin_site = PulpAdminSite(name="myadmin")

admin_site.register(DomainOrg)
admin_site.register(User, UserAdmin)
