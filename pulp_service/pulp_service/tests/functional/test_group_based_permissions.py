
import pytest
from uuid import uuid4

def test_group_domain_permission(pulpcore_bindings, file_bindings, gen_user, gen_group, gen_object_with_cleanup):
    """
    Tests that a user can access a domain created by another user in the same group.
    """
    # Ensure we're using basic auth, not header-based auth
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)

    # 1. Create a group
    with gen_group(name="test-team") as group_context:
        test_group = group_context.group

        # 2. Create user_a, add to the group, and have them create a domain
        user_a = gen_user(username="user-a-in-team", groups=[test_group])
        domain_name = str(uuid4())
        with user_a:
            gen_object_with_cleanup(
                pulpcore_bindings.DomainsApi,
                {
                    "name": domain_name,
                    "storage_class": "pulpcore.app.models.storage.FileSystem",
                    "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
                },
            )

        # 3. Create user_b and add them to the same group
        user_b = gen_user(username="user-b-in-team", groups=[test_group])

        # 4. Verify user_b can create a repository in the domain created by user_a
        with user_b:
            gen_object_with_cleanup(
                file_bindings.RepositoriesFileApi,
                {"name": str(uuid4())},
                pulp_domain=domain_name,
            )
