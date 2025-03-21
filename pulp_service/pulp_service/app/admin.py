from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from django.contrib import admin

from django import forms
from django.core.validators import RegexValidator 

from .models import DomainOrg
import re

from pulpcore.plugin.models import Domain

USERNAME_PATTERN = r'^[\w.@+=/-]+$'
USERNAME_ERROR_MSG = "Username can only contain letters, numbers, and these special characters: @, ., +, -, =, /, _"
USERNAME_HELP_TEXT = "Required. 150 characters or fewer. Letters, numbers, and these special characters: @, ., +, -, =, /, _"

# Override Django's username validator
pulp_username_validator = RegexValidator(
    USERNAME_PATTERN,
    USERNAME_ERROR_MSG,
    'invalid'
)


# Apply the new validator to the User model
User._meta.get_field('username').validators = [pulp_username_validator]
# Update the help_text as well
User._meta.get_field('username').help_text = USERNAME_HELP_TEXT

# Custom/Pulp forms to allow additional characters
class PulpUserCreationForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override help_text in the form field
        self.fields['username'].help_text = USERNAME_HELP_TEXT

    def clean_username(self):
        username = self.cleaned_data['username']
        if not re.match(USERNAME_PATTERN, username):
            raise forms.ValidationError(USERNAME_ERROR_MSG)
        return username


class PulpUserChangeForm(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override help_text in the form field
        self.fields['username'].help_text = USERNAME_HELP_TEXT
        
    def clean_username(self):
        username = self.cleaned_data['username']
        if not re.match(r'^[\w.@+=/-]+$', username):
            raise forms.ValidationError(
                "Username can only contain letters, numbers, and the characters @/./+/-/=/_"
            )
        return username


class PulpUserAdmin(UserAdmin):
    form = PulpUserChangeForm
    add_form = PulpUserCreationForm


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
#We are replacing the default UserAdmin with our PulpUserAdmin
admin_site.register(User, PulpUserAdmin) 
admin_site.register(Domain, DomainAdmin)
