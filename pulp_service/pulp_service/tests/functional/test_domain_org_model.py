import pytest
import uuid

from django.contrib.auth.models import Group
from pulp_service.app.models import DomainOrg


def test_domain_org_group_assignment(service_bindings, gen_group, gen_object_with_cleanup):
    """
    Test that a Group can be assigned to a DomainOrg instance.
    """

    group = gen_group(name="test_group")

    domain = gen_object_with_cleanup(
        service_bindings.ApiCreateDomainApi, {
            "name": str(uuid.uuid4()),
        }
    )
    
