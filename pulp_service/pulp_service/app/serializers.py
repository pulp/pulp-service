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
from pulp_service.app.constants import (
    NPM_PACKAGE_LOCK_SCHEMA,
    OSV_RH_ECOSYSTEM_CPES_LABEL,
    OSV_RH_ECOSYSTEM_LABEL,
)


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
        if bool(repo_ver := data.get("repo_version")) == bool(pkg_json := data.get("package_json")):
            raise serializers.ValidationError(
                _("Exactly one of 'repo_version' or 'package_json' must be specified.")
            )

        # no more validations needed for pkg_json
        if pkg_json:
            return data

        # for rpm repositories we need to verify the repository labels
        if repo_ver.repository.pulp_type == "rpm.rpm":
            if not self._validate_rpm_repo_expected_fields(repo_ver.repository):
                raise serializers.ValidationError(
                    _("Repository ecosystem not supported or does not contain the expected labels.")
                )

        return data

    def _validate_rpm_repo_expected_fields(self, repo):
        """
        verify if the label 'osv.dev ecosystem' == 'Red Hat'
        verify if len(label['osv.dev cpe']) > 0
        """
        # for now, we are only supporting 'Red Hat' for rpms
        if repo.pulp_labels.get(OSV_RH_ECOSYSTEM_LABEL) == "Red Hat":
            # 'osv.dev cpe' must be provided for Red Hat ecosystem
            if cpes := repo.pulp_labels.get(OSV_RH_ECOSYSTEM_CPES_LABEL):
                return len(cpes) > 0
        return False

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
