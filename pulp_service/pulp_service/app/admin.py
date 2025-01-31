from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.contrib import admin

from .models import DomainOrg

from pulpcore.app.models import Domain


class PulpAdminSite(admin.AdminSite):
    site_header = "Pulp administration"


class ContentSourceDomainFilter(admin.SimpleListFilter):
    title = 'ContentSource Domains'
    parameter_name = 'content_source_filter'

    def lookups(self, request, model_admin):
        return [
            ("cs-domains", "Content Source Domains"),
            ("non-cs-domains", "Non Content Source Domains"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "cs-domains":
            return queryset.filter(
                description__contains="content-sources"
            )
        if self.value() == "non-cs-domains":
            return queryset.exclude(
                description__contains="content-sources"
            )
        return queryset


class DomainOrgAdmin(admin.ModelAdmin):
    list_display = ["domain", "user", "org_id"]
    list_editable = ["user", "org_id"]
    list_filter = ["user", "org_id"]


class DomainAdmin(admin.ModelAdmin):
    list_display = ["name", "description", "storage_class"]
    list_filter = ["name", "description", "storage_class", ContentSourceDomainFilter]


admin_site = PulpAdminSite(name="myadmin")

admin_site.register(DomainOrg, DomainOrgAdmin)
admin_site.register(User, UserAdmin)
admin_site.register(Domain, DomainAdmin)
