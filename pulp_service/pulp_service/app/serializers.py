from gettext import gettext as _
from rest_framework import serializers

from pulpcore.app.serializers import (
    ContentGuardSerializer,
    GetOrCreateSerializerMixin,
    ModelSerializer,
)
from pulpcore.app.viewsets.custom_filters import RepoVersionHrefPrnFilter

from pulp_service.app.models import FeatureContentGuard, VulnerabilityReport


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
