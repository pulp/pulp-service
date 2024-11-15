from gettext import gettext as _
from rest_framework import serializers

from pulpcore.app.serializers import ContentGuardSerializer, GetOrCreateSerializerMixin

from pulp_service.app.models import FeatureContentGuard


class FeatureContentGuardSerializer(ContentGuardSerializer, GetOrCreateSerializerMixin):
    """
    A serializer for FeatureContentGuard.
    """

    features = serializers.ListField(
        child=serializers.CharField(),
        help_text=_("The list of features required to access the content.")
    )

    class Meta(ContentGuardSerializer.Meta):
        model = FeatureContentGuard
        fields = ContentGuardSerializer.Meta.fields + ("header_name", "jq_filter", "features")
