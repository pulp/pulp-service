import json
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests


def test_user_domain_repo_creation(pulpcore_bindings, file_bindings, anonymous_user, gen_object_with_cleanup):

    user1_orgid1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user1"},
        }
    }

    user2_orgid1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user2"},
        }
    }

    with anonymous_user:
        header_user1_orgid1 = json.dumps(user1_orgid1)
        auth_user1_orgid1 = b64encode(bytes(header_user1_orgid1, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user1_orgid1

        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Use User1 OrgID1 auth credentials
        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = auth_user1_orgid1

        # User1 from OrgID1 create a repo on his domain
        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi,
            {"name": str(uuid4())},
            pulp_domain=domain_name,
        )

        header_user2_orgid1 = json.dumps(user2_orgid1)
        auth_user2_orgid1 = b64encode(bytes(header_user2_orgid1, "ascii"))

        # Use User2 OrgID1 auth credentials
        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = auth_user2_orgid1

        # User 2 from OrgID2 create a repo on his domain
        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi,
            {"name": str(uuid4())},
            pulp_domain=domain_name,
        )

        # Users are not allowed to create pulp object outside of their domains
        with pytest.raises(file_bindings.ApiException) as exp:
            gen_object_with_cleanup(file_bindings.RepositoriesFileApi, {"name": str(uuid4())})

        assert exp.value.status == 403


def test_user_list_domain_permissions(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):

    user1_orgid1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user1"},
        }
    }

    with anonymous_user:
        # Clear any authentication header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        with pytest.raises(pulpcore_bindings.ApiException) as exp:
            pulpcore_bindings.DomainsApi.list()

        assert exp.value.status == 401

    with anonymous_user:
        header_user1_orgid1 = json.dumps(user1_orgid1)
        auth_user1_orgid1 = b64encode(bytes(header_user1_orgid1, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user1_orgid1

        # Create a domain for the user
        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # List domains and verify the user sees exactly their domain
        response = pulpcore_bindings.DomainsApi.list()
        assert response.count == 1
        assert response.results[0].name == domain_name


def test_only_owners_can_delete_domain(pulpcore_bindings, anonymous_user, gen_object_with_cleanup, monitor_task):  # noqa: ARG001
    user1_orgid1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user1"},
        }
    }

    user2_orgid2 = {
        "identity": {
            "org_id": 2,
            "internal": {"org_id": 2},
            "user": {"username": "user2"},
        }
    }

    with anonymous_user:
        # Clear any authentication header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

        header_user1_orgid1 = json.dumps(user1_orgid1)
        auth_user1_orgid1 = b64encode(bytes(header_user1_orgid1, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user1_orgid1

        # User 1 creates a domain
        domain_name = str(uuid4())
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # User 2 tries to delete the domain
        header_user2_orgid2 = json.dumps(user2_orgid2)
        auth_user2_orgid2 = b64encode(bytes(header_user2_orgid2, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user2_orgid2

        with pytest.raises(pulpcore_bindings.ApiException) as exp:
            pulpcore_bindings.DomainsApi.delete(domain.pulp_href)

        assert exp.value.status == 403

        # Check if User 1 can delete his own domain
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user1_orgid1
        pulpcore_bindings.DomainsApi.delete(domain.pulp_href)


def test_operations_using_basic_auth(pulpcore_bindings, file_bindings, gen_user, gen_object_with_cleanup):
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

    somebody = gen_user(username="somebody")

    with somebody:
        # Clear any authentication header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)

        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi,
            {"name": str(uuid4())},
            pulp_domain=domain_name,
        )


def test_user_permissions_without_org_id(
    pulpcore_bindings,
    file_bindings,
    anonymous_user,
    gen_object_with_cleanup,
    monitor_task,
):
    user1 = {
        "identity": {
            "org_id": 1,
            "user": {"username": "user1"},
            "internal": {"org_id": 1},
        }
    }

    with anonymous_user:
        # Clear any authentication header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

        header_user1 = json.dumps(user1)
        auth_user1 = b64encode(bytes(header_user1, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user1

        domain_name = str(uuid4())

        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = auth_user1

        repo = gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi,
            {"name": str(uuid4())},
            pulp_domain=domain_name,
        )

        monitor_task(file_bindings.RepositoriesFileApi.delete(repo.pulp_href).task)
        pulpcore_bindings.DomainsApi.delete(domain.pulp_href)


def test_admin_user_with_header_auth(
    pulpcore_bindings,
    file_bindings,
    bindings_cfg,
    anonymous_user,
    gen_object_with_cleanup,
    monitor_task,
):
    username = bindings_cfg.username

    admin = {
        "identity": {
            "org_id": 1,
            "user": {"username": username},
            "internal": {"org_id": 1},
        }
    }

    auth_header = json.dumps(admin)
    admin_auth_header = b64encode(bytes(auth_header, "ascii"))

    pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = admin_auth_header

    domain_name = str(uuid4())
    with anonymous_user:
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = admin_auth_header

        repo = gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi,
            {"name": str(uuid4())},
            pulp_domain=domain_name,
        )

        monitor_task(file_bindings.RepositoriesFileApi.delete(repo.pulp_href).task)
        pulpcore_bindings.DomainsApi.delete(domain.pulp_href)


def test_user_sees_only_their_domains(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Test that User A and User B each create domains and only see their own."""
    user_a = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "userA"},
        }
    }

    user_b = {
        "identity": {
            "org_id": 2,
            "internal": {"org_id": 2},
            "user": {"username": "userB"},
        }
    }

    with anonymous_user:
        # User A creates domain A
        header_user_a = json.dumps(user_a)
        auth_user_a = b64encode(bytes(header_user_a, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_a

        domain_a_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_a_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # User B creates domain B
        header_user_b = json.dumps(user_b)
        auth_user_b = b64encode(bytes(header_user_b, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_b

        domain_b_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_b_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # User A lists domains → sees only domain A
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_a
        response_a = pulpcore_bindings.DomainsApi.list()
        assert response_a.count == 1
        assert response_a.results[0].name == domain_a_name

        # User B lists domains → sees only domain B
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_b
        response_b = pulpcore_bindings.DomainsApi.list()
        assert response_b.count == 1
        assert response_b.results[0].name == domain_b_name


def test_cross_org_isolation(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Test that users in different orgs see only their own domains."""
    user_org1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user_org1"},
        }
    }

    user_org2 = {
        "identity": {
            "org_id": 2,
            "internal": {"org_id": 2},
            "user": {"username": "user_org2"},
        }
    }

    with anonymous_user:
        # User from org 1 creates domain
        header_user_org1 = json.dumps(user_org1)
        auth_user_org1 = b64encode(bytes(header_user_org1, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_org1

        domain_org1_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_org1_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # User from org 2 creates domain
        header_user_org2 = json.dumps(user_org2)
        auth_user_org2 = b64encode(bytes(header_user_org2, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_org2

        domain_org2_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_org2_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Each user only sees their own domain, not the other org's domain
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_org1
        response_org1 = pulpcore_bindings.DomainsApi.list()
        assert response_org1.count == 1
        assert response_org1.results[0].name == domain_org1_name

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_org2
        response_org2 = pulpcore_bindings.DomainsApi.list()
        assert response_org2.count == 1
        assert response_org2.results[0].name == domain_org2_name


def test_org_based_visibility(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Test that users with same org_id both see each other's domains."""
    user_a_org1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "userA_org1"},
        }
    }

    user_b_org1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "userB_org1"},
        }
    }

    with anonymous_user:
        # User A (org_id 1) creates domain A
        header_user_a_org1 = json.dumps(user_a_org1)
        auth_user_a_org1 = b64encode(bytes(header_user_a_org1, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_a_org1

        domain_a_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_a_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # User B (org_id 1) creates domain B
        header_user_b_org1 = json.dumps(user_b_org1)
        auth_user_b_org1 = b64encode(bytes(header_user_b_org1, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_b_org1

        domain_b_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_b_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Both users see both domains due to shared org_id
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_a_org1
        response_a = pulpcore_bindings.DomainsApi.list()
        assert response_a.count == 2
        domain_names_a = {domain.name for domain in response_a.results}
        assert domain_names_a == {domain_a_name, domain_b_name}

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_b_org1
        response_b = pulpcore_bindings.DomainsApi.list()
        assert response_b.count == 2
        domain_names_b = {domain.name for domain in response_b.results}
        assert domain_names_b == {domain_a_name, domain_b_name}


def test_group_based_domain_visibility(
    pulpcore_bindings, anonymous_user, gen_object_with_cleanup, gen_group, domain_factory
):
    """Test that user added to a group sees domains created by another group member."""
    user_a_name = f"user-a-group-{uuid4()}"
    user_a_combined = f"1|{user_a_name}"
    user_b_name = f"user-b-group-{uuid4()}"
    user_b_combined = f"2|{user_b_name}"

    test_group = gen_group()

    gen_object_with_cleanup(pulpcore_bindings.UsersApi, {"username": user_a_combined})
    gen_object_with_cleanup(
        pulpcore_bindings.GroupsUsersApi,
        group_href=test_group.pulp_href,
        group_user={"username": user_a_combined},
    )

    gen_object_with_cleanup(pulpcore_bindings.UsersApi, {"username": user_b_combined})
    gen_object_with_cleanup(
        pulpcore_bindings.GroupsUsersApi,
        group_href=test_group.pulp_href,
        group_user={"username": user_b_combined},
    )

    with anonymous_user:
        # User A creates a domain (signal auto-creates DomainOrg with the group)
        user_a_data = {
            "identity": {
                "org_id": 1,
                "internal": {"org_id": 1},
                "user": {"username": user_a_name},
            }
        }
        header_user_a = json.dumps(user_a_data)
        auth_user_a = b64encode(bytes(header_user_a, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_a

        domain = domain_factory()

        # User B (different org, same group) lists domains and sees the group-linked domain
        user_b_data = {
            "identity": {
                "org_id": 2,
                "internal": {"org_id": 2},
                "user": {"username": user_b_name},
            }
        }
        header_user_b = json.dumps(user_b_data)
        auth_user_b = b64encode(bytes(header_user_b, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_b

        response_b = pulpcore_bindings.DomainsApi.list()
        assert response_b.count == 1
        assert response_b.results[0].name == domain.name


def test_superuser_sees_all_domains(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Test that superuser sees all domains including default."""
    regular_user_data = {
        "identity": {
            "org_id": 2,
            "internal": {"org_id": 2},
            "user": {"username": "regular_user"},
        }
    }

    with anonymous_user:
        # Regular user creates domain
        header_regular = json.dumps(regular_user_data)
        auth_regular = b64encode(bytes(header_regular, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_regular

        domain_regular_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_regular_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

    # Superuser lists domains via basic auth (is_superuser=True)
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    response = pulpcore_bindings.DomainsApi.list()
    assert response.count >= 2
    domain_names = {domain.name for domain in response.results}
    assert "default" in domain_names
    assert domain_regular_name in domain_names


def test_default_domain_invisible_to_regular_users(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Test that regular users cannot see default domain (has no DomainOrg)."""
    user_data = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "regular_user_default_test"},
        }
    }

    with anonymous_user:
        header_user = json.dumps(user_data)
        auth_user = b64encode(bytes(header_user, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user

        # Create a domain so we have something in the list
        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Regular user lists domains and should not see default domain
        response = pulpcore_bindings.DomainsApi.list()
        assert response.count == 1
        assert response.results[0].name == domain_name
        # Only domains with DomainOrg associations are visible
        domain_names = {domain.name for domain in response.results}
        assert "default" not in domain_names


def test_domain_deduplication(pulpcore_bindings, anonymous_user, gen_object_with_cleanup, gen_group, domain_factory):
    """Test that a domain matching via both group and org_id appears only once."""
    user_name = f"dedup-user-{uuid4()}"
    user_combined = f"1|{user_name}"

    test_group = gen_group()

    gen_object_with_cleanup(pulpcore_bindings.UsersApi, {"username": user_combined})
    gen_object_with_cleanup(
        pulpcore_bindings.GroupsUsersApi,
        group_href=test_group.pulp_href,
        group_user={"username": user_combined},
    )

    with anonymous_user:
        # User in a group creates domain (signal creates DomainOrg with org_id + group).
        # scope_queryset matches via both group_id and org_id — domain must appear once.
        user_data = {
            "identity": {
                "org_id": 1,
                "internal": {"org_id": 1},
                "user": {"username": user_name},
            }
        }
        header_user = json.dumps(user_data)
        auth_user = b64encode(bytes(header_user, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user

        domain = domain_factory()

        response = pulpcore_bindings.DomainsApi.list()
        assert response.count == 1
        assert response.results[0].name == domain.name


def test_basic_auth_user_domain_visibility(pulpcore_bindings, gen_user, gen_object_with_cleanup):
    """Test that basic auth user (no X-RH-IDENTITY) sees only their domains."""
    # Create a basic auth user
    basic_user = gen_user(username="basic_auth_user")

    with basic_user:
        # Clear any X-RH-IDENTITY header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

        # Basic auth user creates domain
        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Lists domains and sees only their domain (no org_id filtering applies)
        response = pulpcore_bindings.DomainsApi.list()
        assert response.count == 1
        assert response.results[0].name == domain_name


def test_scope_queryset_model_guard():
    """Test that non-Domain queryset passes through unchanged."""
    from types import SimpleNamespace

    from django.contrib.auth.models import Group

    from pulp_service.app.authorization import DomainBasedPermission

    group_qs = Group.objects.all()

    permission = DomainBasedPermission()
    view = SimpleNamespace(request=SimpleNamespace())

    result_qs = permission.scope_queryset(view, group_qs)

    assert result_qs is group_qs


def test_authenticated_user_can_read_public_domain(
    pulpcore_bindings, python_bindings, anonymous_user, gen_object_with_cleanup, bindings_cfg
):
    """Test that an authenticated user from a different org can GET a public- domain's PyPI view.

    Regression test for https://github.com/pulp/pulp-service/pull/1231
    Before the fix, authenticated users got 403 on public domains because
    DomainBasedPermission only allowed anonymous safe requests to bypass
    the domain access check.
    """
    owner_identity = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "public_domain_owner"},
        }
    }

    other_org_identity = {
        "identity": {
            "org_id": 9999,
            "internal": {"org_id": 9999},
            "user": {"username": "other_org_user"},
        }
    }

    with anonymous_user:
        header_owner = json.dumps(owner_identity)
        auth_owner = b64encode(bytes(header_owner, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_owner

        domain_name = f"public-test-{uuid4()}"
        try:
            gen_object_with_cleanup(
                pulpcore_bindings.DomainsApi,
                {
                    "name": domain_name,
                    "storage_class": "pulpcore.app.models.storage.FileSystem",
                    "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
                },
            )

            python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = auth_owner
            repo = gen_object_with_cleanup(
                python_bindings.RepositoriesPythonApi, {"name": str(uuid4())}, pulp_domain=domain_name
            )

            python_bindings.DistributionsPypiApi.api_client.default_headers["x-rh-identity"] = auth_owner
            base_path = str(uuid4())
            gen_object_with_cleanup(
                python_bindings.DistributionsPypiApi,
                {"name": str(uuid4()), "base_path": base_path, "repository": repo.pulp_href},
                pulp_domain=domain_name,
            )

            pypi_url = urljoin(bindings_cfg.host, f"/api/pypi/{domain_name}/{base_path}/simple/")

            # Anonymous GET should succeed
            anon_response = requests.get(pypi_url, timeout=30)
            assert anon_response.status_code == 200

            # Authenticated GET from a different org (no DomainOrg entry) should also succeed
            header_other = json.dumps(other_org_identity)
            auth_other = b64encode(bytes(header_other, "ascii")).decode("ascii")
            auth_response = requests.get(pypi_url, headers={"x-rh-identity": auth_other}, timeout=30)
            assert auth_response.status_code == 200
        finally:
            pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
            python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
            python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)
