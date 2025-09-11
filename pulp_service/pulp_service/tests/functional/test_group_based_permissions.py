import json
from base64 import b64encode
import pytest
from uuid import uuid4

def test_group_domain_permission(pulpcore_bindings, file_bindings, gen_group, gen_object_with_cleanup, anonymous_user):
    """
    Tests that a user can access a domain created by another user in the same group.
    """
    # Ensure we're using header-based auth for this test
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("Authorization", None)
    file_bindings.RepositoriesFileApi.api_client.default_headers.pop("Authorization", None)

    # 1. Create a group
    test_group = gen_group(name=f"test-team-{uuid4()}")

    # 2. Create user_a and associate with the group
    user_a_name = f"user-a-in-team-{uuid4()}"
    user_a = gen_object_with_cleanup(
        pulpcore_bindings.UsersApi, {"username": user_a_name}
    )
    gen_object_with_cleanup(
        pulpcore_bindings.GroupsUsersApi, group_href=test_group.pulp_href, group_user={"username": user_a_name}
    )

    # 3. Create a domain as user_a
    domain_name = str(uuid4())
    user_a_identity = {"identity": {"user": {"username": user_a_name}}}
    auth_header_a = b64encode(json.dumps(user_a_identity).encode("ascii"))

    try:
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header_a
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )
    finally:
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

    # 4. Create user_b and associate with the same group
    user_b_name = f"user-b-in-team-{uuid4()}"
    user_b = gen_object_with_cleanup(
        pulpcore_bindings.UsersApi, {"username": user_b_name}
    )
    gen_object_with_cleanup(
        pulpcore_bindings.GroupsUsersApi, group_href=test_group.pulp_href, group_user={"username": user_b_name}
    )

    # 5. Verify user_b can create a repository in the domain created by user_a
    user_b_identity = {"identity": {"user": {"username": user_b_name}}}
    auth_header_b = b64encode(json.dumps(user_b_identity).encode("ascii"))
    try:
        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = auth_header_b
        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi,
            {"name": str(uuid4())},
            pulp_domain=domain_name,
        )
    finally:
        file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)

    # 6. Create user_c, not in the group
    user_c_name = f"user-c-not-in-team-{uuid4()}"
    gen_object_with_cleanup(
        pulpcore_bindings.UsersApi, {"username": user_c_name}
    )

    # 7. Verify user_c cannot create a repository in the domain
    user_c_identity = {"identity": {"user": {"username": user_c_name}}}
    auth_header_c = b64encode(json.dumps(user_c_identity).encode("ascii"))
    try:
        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = auth_header_c
        with pytest.raises(file_bindings.ApiException) as exp:
            gen_object_with_cleanup(
                file_bindings.RepositoriesFileApi,
                {"name": str(uuid4())},
                pulp_domain=domain_name,
            )
        assert exp.value.status == 403
    finally:
        file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)

def test_domain_permission_for_user_without_group(pulpcore_bindings, file_bindings, gen_object_with_cleanup, anonymous_user):
    """
    Tests that a user without a group can manage their own domain,
    and that another user cannot access it.
    """
    # Ensure we're using header-based auth for this test
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("Authorization", None)
    file_bindings.RepositoriesFileApi.api_client.default_headers.pop("Authorization", None)

    # 1. Create user_a, not in any group
    user_a_name = f"user-a-no-group-{uuid4()}"
    gen_object_with_cleanup(
        pulpcore_bindings.UsersApi, {"username": user_a_name}
    )

    # 2. Create a domain as user_a
    domain_name = str(uuid4())
    user_a_identity = {"identity": {"user": {"username": user_a_name}}}
    auth_header_a = b64encode(json.dumps(user_a_identity).encode("ascii"))

    try:
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header_a
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )
    finally:
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

    # 3. Verify user_a can create a repository in their own domain
    try:
        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = auth_header_a
        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi,
            {"name": str(uuid4())},
            pulp_domain=domain_name,
        )
    finally:
        file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)

    # 4. Create user_b, also not in any group
    user_b_name = f"user-b-no-group-{uuid4()}"
    gen_object_with_cleanup(
        pulpcore_bindings.UsersApi, {"username": user_b_name}
    )

    # 5. Verify user_b cannot create a repository in user_a's domain
    user_b_identity = {"identity": {"user": {"username": user_b_name}}}
    auth_header_b = b64encode(json.dumps(user_b_identity).encode("ascii"))
    try:
        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = auth_header_b
        with pytest.raises(file_bindings.ApiException) as exp:
            gen_object_with_cleanup(
                file_bindings.RepositoriesFileApi,
                {"name": str(uuid4())},
                pulp_domain=domain_name,
            )
        assert exp.value.status == 403
    finally:
        file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)