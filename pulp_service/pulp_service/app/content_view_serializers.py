from gettext import gettext as _

from rest_framework import serializers

from pulpcore.plugin.models import Distribution
from pulpcore.plugin.serializers import (
    DetailRelatedField,
    DomainUniqueValidator,
    IdentityField,
    ModelSerializer,
    RepositoryVersionRelatedField,
    pulp_labels_validator,
)

from pulp_service.app.content_view_models import ContentView
from pulp_service.app.content_view_util import resolve_content_view_distributions


class ContentViewDistributionStatusSerializer(serializers.Serializer):
    """Per-distribution resolution status, shown on the ContentView detail/list endpoints."""

    distribution = DetailRelatedField(
        read_only=True,
        view_name_pattern=r"distributions(-.*/.*)?-detail",
        help_text=_("The distribution this status entry describes."),
    )
    domain = serializers.CharField(
        source="domain.name", help_text=_("The name of the domain the distribution belongs to.")
    )
    status = serializers.ChoiceField(
        choices=["ok", "no_domain_access", "no_version"],
        help_text=_(
            "'ok' if the distribution currently resolves to a repository version the caller can "
            "search; 'no_domain_access' if the caller does not (or no longer) have read access "
            "to the distribution's domain; 'no_version' if the distribution or the repository "
            "version/publication it pointed to has been deleted."
        ),
    )
    repository_version = RepositoryVersionRelatedField(
        read_only=True,
        allow_null=True,
        queryset=None,
        help_text=_("The repository version currently resolved for this distribution, if any."),
    )


class ContentViewSerializer(ModelSerializer):
    """
    Serializer for a ContentView -- a named, persistable scope composed of Distributions that
    may span multiple domains, used to search across their content without exposing raw
    repository version hrefs on every request.
    """

    # Distributions referenced by a ContentView may legitimately live in a domain other than
    # the ContentView's own -- that's the entire point of this resource -- so the default
    # same-domain cross-field validation (ValidateFieldsMixin.check_cross_domains) must not
    # apply here.
    CHECK_SAME_DOMAIN = False

    pulp_href = IdentityField(view_name="content-views-detail")

    name = serializers.CharField(
        help_text=_("A unique name for this content view."),
        validators=[DomainUniqueValidator(queryset=ContentView.objects.all())],
    )
    description = serializers.CharField(
        help_text=_("An optional description of this content view."),
        required=False,
        allow_null=True,
    )
    pulp_labels = serializers.HStoreField(required=False, validators=[pulp_labels_validator])
    distributions = DetailRelatedField(
        many=True,
        required=False,
        queryset=Distribution.objects.all(),
        view_name_pattern=r"distributions(-.*/.*)?-detail",
        help_text=_(
            "Distributions this content view searches across. May reference distributions "
            "belonging to any domain the user has read access to, not just this content view's "
            "own domain."
        ),
    )
    distributions_status = serializers.SerializerMethodField(
        help_text=_(
            "Per-distribution resolution status: whether each linked distribution's domain is "
            "currently accessible and whether it resolves to a repository version."
        )
    )

    def get_distributions_status(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if user is None:
            return []
        resolutions = resolve_content_view_distributions(obj, user)
        return ContentViewDistributionStatusSerializer(resolutions, many=True, context=self.context).data

    class Meta:
        model = ContentView
        fields = (
            *ModelSerializer.Meta.fields,
            "name",
            "description",
            "pulp_labels",
            "distributions",
            "distributions_status",
        )


class ContentViewPackageSerializer(serializers.Serializer):
    """
    A lightweight representation of a Package for Content View search results.

    Used by the ``packages/`` (typeahead) and ``packages/list/`` search endpoints. Instances
    may originate from any domain the ContentView's Distributions span, so ``pulp_href``
    resolves per-object using each Package's own domain.
    """

    pulp_href = IdentityField(view_name="content-rpm/packages-detail")
    name = serializers.CharField(help_text=_("Name of the package"))
    epoch = serializers.CharField(help_text=_("The package's epoch"))
    version = serializers.CharField(help_text=_("The version of the package"))
    release = serializers.CharField(help_text=_("The release of the package"))
    arch = serializers.CharField(help_text=_("The target architecture for the package"))
    summary = serializers.CharField(help_text=_("Short description of the packaged software"))
    description = serializers.CharField(help_text=_("In-depth description of the package"))
    checksum_type = serializers.CharField(help_text=_("Type of checksum, e.g. 'sha256'"))
    pkgId = serializers.CharField(help_text=_("Checksum of the package file"))  # noqa: N815 -- matches pulp_rpm's Package.pkgId
    url = serializers.CharField(help_text=_("URL with more information about the package"))
    location_href = serializers.CharField(help_text=_("Relative location of the package"))
    is_modular = serializers.BooleanField(help_text=_("Whether the package is modular"))


class ContentViewPackageGroupSerializer(serializers.Serializer):
    """A representation of a PackageGroup for Content View search results."""

    pulp_href = IdentityField(view_name="content-rpm/packagegroups-detail")
    id = serializers.CharField(help_text=_("ID of the group"))
    name = serializers.CharField(help_text=_("Name of the group"))
    description = serializers.CharField(help_text=_("Description of the group"))
    packages = serializers.JSONField(help_text=_("The list of packages in this group"))


class ContentViewPackageEnvironmentSerializer(serializers.Serializer):
    """A representation of a PackageEnvironment for Content View search results."""

    pulp_href = IdentityField(view_name="content-rpm/packageenvironments-detail")
    id = serializers.CharField(help_text=_("ID of the environment"))
    name = serializers.CharField(help_text=_("The name of the environment"))
    description = serializers.CharField(help_text=_("The description of the environment"))
    group_ids = serializers.JSONField(help_text=_("A list of group ids"))


class ContentViewUpdateReferenceSerializer(serializers.Serializer):
    """A representation of an UpdateReference, used to surface CVEs on errata search results."""

    href = serializers.CharField(help_text=_("Reference URL"))
    ref_id = serializers.CharField(help_text=_("ID of the reference"))
    title = serializers.CharField(help_text=_("Title of the reference"))
    ref_type = serializers.CharField(help_text=_("Type of the reference, e.g. 'cve'"))


class ContentViewErrataSerializer(serializers.Serializer):
    """A representation of an UpdateRecord (advisory/errata) for Content View search results."""

    pulp_href = IdentityField(view_name="content-rpm/advisories-detail")
    id = serializers.CharField(help_text=_("Update id (e.g. RHEA-2013:1777)"))
    updated_date = serializers.CharField(help_text=_("Date when the update was updated"))
    issued_date = serializers.CharField(help_text=_("Date when the update was issued"))
    description = serializers.CharField(help_text=_("Update description"))
    title = serializers.CharField(help_text=_("Update name"))
    summary = serializers.CharField(help_text=_("Short summary"))
    version = serializers.CharField(help_text=_("Update version"))
    type = serializers.CharField(help_text=_("Update type ('enhancement', 'bugfix', ...)"))
    severity = serializers.CharField(help_text=_("Severity"))
    solution = serializers.CharField(help_text=_("Solution"))
    release = serializers.CharField(help_text=_("Update release"))
    rights = serializers.CharField(help_text=_("Copyrights"))
    reboot_suggested = serializers.BooleanField(help_text=_("Whether a reboot is suggested"))
    cves = serializers.SerializerMethodField(help_text=_("CVE references attached to this errata"))

    def get_cves(self, obj):
        return ContentViewUpdateReferenceSerializer(
            [ref for ref in obj.references.all() if ref.ref_type == "cve"], many=True
        ).data


class ContentViewModuleStreamSerializer(serializers.Serializer):
    """A representation of a Modulemd for Content View search results."""

    pulp_href = IdentityField(view_name="content-rpm/modulemds-detail")
    name = serializers.CharField(help_text=_("Name of the modulemd"))
    stream = serializers.CharField(help_text=_("The modulemd's stream"))
    version = serializers.CharField(help_text=_("The version of the modulemd"))
    context = serializers.CharField(help_text=_("The modulemd's context flag"))
    arch = serializers.CharField(help_text=_("Module artifact architecture"))
    description = serializers.CharField(help_text=_("A verbose description of the module"))
    profiles = serializers.JSONField(help_text=_("Package lists of installable profiles"))
    packages = serializers.SerializerMethodField(help_text=_("Names of packages in this module"))

    def get_packages(self, obj):
        return list(obj.packages.values_list("name", flat=True))
