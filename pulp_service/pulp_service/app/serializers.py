from gettext import gettext as _
from rest_framework import serializers

from pulpcore.app.serializers import (
    ContentGuardSerializer,
    GetOrCreateSerializerMixin,
    ModelSerializer,
)
from pulpcore.app.viewsets.custom_filters import RepoVersionHrefPrnFilter

from pulp_service.app.models import FeatureContentGuard, VulnerabilityReport, AnsibleLogReport


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

    id = serializers.UUIDField()
    vulns = serializers.JSONField()

    class Meta:
        model = VulnerabilityReport
        fields = ["id", "vulns"]


class ContentScanSerializer(serializers.Serializer):
    """
    A serializer for package scan.
    """

    repo_version = serializers.CharField()

    def validate_repo_version(self, value):
        try:
            repo_version = RepoVersionHrefPrnFilter.get_repository_version(value)
        except:
            raise serializers.ValidationError(_("No matching RepositoryVersion instance found."))
        return repo_version.pk
class AnsibleLogAnalysisSerializer(serializers.Serializer):
    """
    A serializer for Ansible log analysis requests.
    """
    url = serializers.URLField(
        help_text=_("URL to the Ansible log file to be analyzed")
    )
    role = serializers.ListField(
        child=serializers.CharField(),
        help_text=_("List of roles to filter by, or ['ALL'] for all roles"),
        default=["ALL"]
    )

class AnsibleLogReportSerializer(ModelSerializer):
    """
    A serializer for the AnsibleLogReport model.
    """
    pulp_href = IdentityField(view_name="ansible-logs-detail")
    log_url = serializers.URLField(read_only=True)
    errors = serializers.JSONField(read_only=True)
    error_count = serializers.IntegerField(read_only=True)
    role_filter = serializers.JSONField(read_only=True)

    class Meta:
        model = AnsibleLogReport
        fields = ModelSerializer.Meta.fields + ('pulp_domain', 'log_url', 'errors', 'error_count', 'role_filter')