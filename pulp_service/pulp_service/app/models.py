from django.conf import settings

from django.db import models
from pulpcore.plugin.models import Domain, Group


class DomainOrg(models.Model):
    """
    One-to-many relationship between org ids and Domains.
    """
    org_id = models.CharField(null=True, db_index=True)
    domain = models.ForeignKey(Domain, related_name="domains", on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="users", on_delete=models.CASCADE, null=True
    )
