from django.db import models
from pulpcore.plugin.models import Domain
class DomainOrg(models.Model):
    """
    One-to-many relationship between org ids and Domains.
    """

    org_id = models.CharField(db_index=True)
    domain = models.ForeignKey(Domain, related_name="domains", on_delete=models.CASCADE, db_index=True)

    class Meta:
        unique_together = ("org_id", "domain")
