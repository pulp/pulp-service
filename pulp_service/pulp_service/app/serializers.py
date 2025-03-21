import json

from gettext import gettext as _
from jsonschema import validate, ValidationError
from rest_framework import serializers

from pulpcore.plugin.serializers import (
    ContentGuardSerializer,
    GetOrCreateSerializerMixin,
    ModelSerializer,
    ValidateFieldsMixin,
)
from pulpcore.plugin.models import PulpTemporaryFile
from pulpcore.plugin.serializers import IdentityField, RepositoryVersionRelatedField

from pulp_service.app.models import FeatureContentGuard, VulnerabilityReport
from pulp_service.app.constants import NPM_PACKAGE_LOCK_SCHEMA


class FeatureContentGuardSerializer(ContentGuardSerializer, GetOrCreateSerializerMixin):
    """
    A serializer for FeatureContentGuard.
    """

    features = serializers.ListField(
        child=serializers.CharField(),
        help_text=_("The list of features required to access the content."),
    )

    class Meta(ContentGuardSerializer.Meta):
        model = FeatureContentGuard
        fields = ContentGuardSerializer.Meta.fields + ("header_name", "jq_filter", "features")


class VulnerabilityReportSerializer(ModelSerializer):
    """
    A serializer for the VulnerabilityReport Model.
    """

    vulns = serializers.JSONField()
    pulp_href = IdentityField(view_name="vuln_report-detail")

    class Meta:
        model = VulnerabilityReport
        fields = ModelSerializer.Meta.fields + ("vulns",)


class ContentScanSerializer(serializers.Serializer, ValidateFieldsMixin):
    """
    A serializer for package scan.
    """

    repo_version = RepositoryVersionRelatedField(
        required=False,
        allow_null=True,
        help_text=_("RepositoryVersion HREF with the packages to be checked."),
    )
    package_json = serializers.FileField(
        required=False,
        allow_null=True,
        help_text=_("package-lock.json file with the definition of dependencies to be checked."),
    )

    def validate(self, data):
        data = super().validate(data)
        if bool(data.get("repo_version", None)) == bool(data.get("package_json", None)):
            raise serializers.ValidationError(
                _("Exactly one of 'repo_version' or 'package_json' must be specified.")
            )
        return data

    def verify_file(self):
        uploaded_file = self.validated_data["package_json"]
        temp_file = PulpTemporaryFile.init_and_validate(uploaded_file, None)
        try:
            lock_file_content_json = json.load(uploaded_file)
            validate(instance=lock_file_content_json, schema=NPM_PACKAGE_LOCK_SCHEMA)
            temp_file.save()
        except ValidationError as e:
            raise serializers.ValidationError(_(f"Invalid package-lock.json: {e.message}"))
        except json.JSONDecodeError:
            raise serializers.ValidationError(_("Invalid JSON format."))

        return temp_file.pk
