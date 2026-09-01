from django.contrib.postgres.fields import HStoreField
from django.db import models

from pulpcore.plugin.models import AutoAddObjPermsMixin, BaseModel
from pulpcore.plugin.util import get_domain_pk


class ContentView(BaseModel, AutoAddObjPermsMixin):
    """
    A named, persistable scope composed of Distributions, searchable across domains.

    A ContentView lets API clients search across the content served by many Distributions --
    which may span domains other than the ContentView's own -- without passing raw lists of
    repository version hrefs on every request, and without bypassing Pulp's RBAC by querying
    the database directly. Each linked Distribution already carries version-tracking semantics
    (it can point to a Repository to track its latest version, a pinned RepositoryVersion, or a
    Publication), so the ContentView itself only needs to store *which* Distributions are in
    scope; resolving them to concrete RepositoryVersions happens at query time.

    Fields:
        name (models.TextField): The content view's name, unique within its domain.
        description (models.TextField): Optional human-readable description.
        pulp_labels (HStoreField): Dictionary of string values.

    Relations:
        pulp_domain (models.ForeignKey): The domain this ContentView is stored in. Standard
            domain-scoped resource: read/update/delete is governed by RBAC on the ContentView
            itself, same as any other Pulp resource.
        distributions (models.ManyToManyField): Distributions this ContentView searches across.
            These may belong to any domain the referencing user has read access to at the time
            they are added -- not just the ContentView's own domain -- which is what makes
            cross-domain search possible.
    """

    name = models.TextField(db_index=True)
    description = models.TextField(null=True)  # noqa: DJ001
    pulp_labels = HStoreField(default=dict)
    pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)
    distributions = models.ManyToManyField("core.Distribution", related_name="service_content_views")

    class Meta:
        unique_together = ("name", "pulp_domain")
        permissions = [
            ("manage_roles_contentview", "Can manage role assignments on content view"),
        ]


class ContentViewSearchScope(models.Model):
    """
    Internal placeholder model -- never queried, never migrated to a real table.

    The nested Content View search viewsets (``content-views/{pk}/search/rpm/...``) each build
    their own querysets dynamically, per-request, against whatever content models and domains
    the parent ContentView's Distributions resolve to. They still need *some* ``queryset`` class
    attribute to satisfy ``NamedModelViewSet``'s registration/routing machinery, but they must
    NOT reuse a real content model's queryset (e.g. pulp_rpm's Package/UpdateRecord): doing so
    would make pulpcore's ``get_viewset_for_model`` ambiguous for that model wherever it's
    otherwise relied on (e.g. RepositoryVersion content-summary href generation), since it
    would then find more than one registered viewset for it. Pointing every search viewset at
    this single, harmless, unmanaged model instead avoids that collision entirely -- nothing
    else in Pulp ever looks up a viewset *for this model*.
    """

    class Meta:
        managed = False

    def __str__(self):
        return "ContentViewSearchScope"
