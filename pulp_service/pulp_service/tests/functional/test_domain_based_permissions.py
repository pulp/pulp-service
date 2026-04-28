import json
import pytest

from uuid import uuid4
from base64 import b64encode


def test_user_domain_repo_creation(pulpcore_bindings, file_bindings, anonymous_user, gen_object_with_cleanup):

    user1_orgid1 = {
        "identity": {
            "org_id": 1,
            "internal": {
                "org_id": 1
            },
            "user": {
                "username": "user1"
            }
        }
    }

    user2_orgid1 = {
        "identity": {
            "org_id": 1,
            "internal": {
                "org_id": 1
            },
            "user": {
                "username": "user2"
            }
        }
    }

    with anonymous_user:
        header_user1_orgid1 = json.dumps(user1_orgid1)
        auth_user1_orgid1 = b64encode(bytes(header_user1_orgid1, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = (
            auth_user1_orgid1
        )

        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # Use User1 OrgID1 auth credentials
        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = (
            auth_user1_orgid1
        )

        # User1 from OrgID1 create a repo on his domain
        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )

        header_user2_orgid1 = json.dumps(user2_orgid1)
        auth_user2_orgid1 = b64encode(bytes(header_user2_orgid1, "ascii"))

        # Use User2 OrgID1 auth credentials
        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = (
            auth_user2_orgid1
        )

        # User 2 from OrgID2 create a repo on his domain
        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )

        # Users are not allowed to create pulp object outside of their domains
        with pytest.raises(file_bindings.ApiException) as exp:
            gen_object_with_cleanup(
                file_bindings.RepositoriesFileApi, {"name": str(uuid4())}
            )

        assert exp.value.status == 403


def test_user_list_domain_permissions(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):

    user1_orgid1 = {
        "identity": {
            "org_id": 1,
            "internal": {
                "org_id": 1
            },
            "user": {
                "username": "user1"
            }
        }
    }

    with anonymous_user:
        # Clear any authentication header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop(
            "x-rh-identity", None
        )
        with pytest.raises(pulpcore_bindings.ApiException) as exp:
            pulpcore_bindings.DomainsApi.list()

        assert exp.value.status == 401

    with anonymous_user:
        header_user1_orgid1 = json.dumps(user1_orgid1)
        auth_user1_orgid1 = b64encode(bytes(header_user1_orgid1, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = (
            auth_user1_orgid1
        )

        # Create a domain for user1
        domain_name = str(uuid4())
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # List domains and verify the user sees exactly their domain
        response = pulpcore_bindings.DomainsApi.list()
        assert response.count == 1
        assert response.results[0].name == domain_name


def test_only_owners_can_delete_domain(pulpcore_bindings, anonymous_user, gen_object_with_cleanup, monitor_task):
    user1_orgid1 = {
        "identity": {
            "org_id": 1,
            "internal": {
                "org_id": 1
            },
            "user": {
                "username": "user1"
            }
        }
    }

    user2_orgid2 = {
        "identity": {
            "org_id": 2,
            "internal": {
                "org_id": 2
            },
            "user": {
                "username": "user2"
            }
        }
    }

    with anonymous_user:
        # Clear any authentication header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop(
            "x-rh-identity", None
        )

        header_user1_orgid1 = json.dumps(user1_orgid1)
        auth_user1_orgid1 = b64encode(bytes(header_user1_orgid1, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = (
            auth_user1_orgid1
        )

        # User 1 creates a domain
        domain_name = str(uuid4())
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # User 2 tries to delete the domain
        header_user2_orgid2 = json.dumps(user2_orgid2)
        auth_user2_orgid2 = b64encode(bytes(header_user2_orgid2, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = (
            auth_user2_orgid2
        )

        with pytest.raises(pulpcore_bindings.ApiException) as exp:
            pulpcore_bindings.DomainsApi.delete(domain.pulp_href)

        assert exp.value.status == 403

        # Check if User 1 can delete his own domain
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = (
            auth_user1_orgid1
        )
        pulpcore_bindings.DomainsApi.delete(domain.pulp_href)


def test_operations_using_basic_auth(pulpcore_bindings, file_bindings, gen_user, gen_object_with_cleanup):
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop(
        "x-rh-identity", None
    )

    somebody = gen_user(username="somebody")

    with somebody:
        # Clear any authentication header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop(
            "x-rh-identity", None
        )
        file_bindings.RepositoriesFileApi.api_client.default_headers.pop(
            "x-rh-identity", None
        )

        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )


def test_user_permissions_without_orgId(pulpcore_bindings, file_bindings, anonymous_user, gen_object_with_cleanup, monitor_task):
    user1 = {
        "identity": {
            "org_id": 1,
            "user": {
                "username": "user1"
            },
            "internal": {
                "org_id": 1
            },
        }
    }

    with anonymous_user:
        # Clear any authentication header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop(
            "x-rh-identity", None
        )

        header_user1 = json.dumps(user1)
        auth_user1 = b64encode(bytes(header_user1, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = (
            auth_user1
        )

        domain_name = str(uuid4())

        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = (
            auth_user1
        )

        repo = gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )

        monitor_task(file_bindings.RepositoriesFileApi.delete(repo.pulp_href).task)
        pulpcore_bindings.DomainsApi.delete(domain.pulp_href)


def test_admin_user_with_header_auth(pulpcore_bindings, file_bindings, bindings_cfg, anonymous_user, gen_object_with_cleanup, monitor_task):
    username = bindings_cfg.username

    admin = {
        "identity": {
            "org_id": 1,
            "user": {
                "username": username
            },
            "internal": {
                "org_id": 1
            },
        }
    }

    auth_header = json.dumps(admin)
    admin_auth_header = b64encode(bytes(auth_header, "ascii"))

    pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = (
        admin_auth_header
    )

    domain_name = str(uuid4())
    with anonymous_user:
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = (
            admin_auth_header
        )

        repo = gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )

        monitor_task(file_bindings.RepositoriesFileApi.delete(repo.pulp_href).task)
        pulpcore_bindings.DomainsApi.delete(domain.pulp_href)


def test_user_sees_only_their_domains(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Test that User A and User B each see only their own domains."""
    user_a = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user_a"}
        }
    }

    user_b = {
        "identity": {
            "org_id": 2,
            "internal": {"org_id": 2},
            "user": {"username": "user_b"}
        }
    }

    with anonymous_user:
        # User A creates a domain
        header_user_a = json.dumps(user_a)
        auth_user_a = b64encode(bytes(header_user_a, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_a

        domain_a_name = str(uuid4())
        domain_a = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_a_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # User B creates a domain
        header_user_b = json.dumps(user_b)
        auth_user_b = b64encode(bytes(header_user_b, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_b

        domain_b_name = str(uuid4())
        domain_b = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_b_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # User A should only see their domain
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_a
        response_a = pulpcore_bindings.DomainsApi.list()
        assert response_a.count == 1
        assert response_a.results[0].name == domain_a_name

        # User B should only see their domain
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_b
        response_b = pulpcore_bindings.DomainsApi.list()
        assert response_b.count == 1
        assert response_b.results[0].name == domain_b_name


def test_cross_org_isolation(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Test that users in different orgs each see only their own domains."""
    user_org1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user_org1"}
        }
    }

    user_org2 = {
        "identity": {
            "org_id": 2,
            "internal": {"org_id": 2},
            "user": {"username": "user_org2"}
        }
    }

    with anonymous_user:
        # User from org 1 creates a domain
        header_user_org1 = json.dumps(user_org1)
        auth_user_org1 = b64encode(bytes(header_user_org1, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_org1

        domain_org1_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_org1_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # User from org 2 creates a domain
        header_user_org2 = json.dumps(user_org2)
        auth_user_org2 = b64encode(bytes(header_user_org2, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_org2

        domain_org2_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_org2_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # User from org 1 should only see their domain
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_org1
        response_org1 = pulpcore_bindings.DomainsApi.list()
        assert response_org1.count == 1
        assert response_org1.results[0].name == domain_org1_name

        # User from org 2 should only see their domain
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_org2
        response_org2 = pulpcore_bindings.DomainsApi.list()
        assert response_org2.count == 1
        assert response_org2.results[0].name == domain_org2_name


def test_org_based_visibility(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Test that two users with the same org_id both see each other's domains."""
    user1_org1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user1_org1"}
        }
    }

    user2_org1 = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user2_org1"}
        }
    }

    with anonymous_user:
        # User 1 from org 1 creates a domain
        header_user1_org1 = json.dumps(user1_org1)
        auth_user1_org1 = b64encode(bytes(header_user1_org1, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user1_org1

        domain1_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain1_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # User 2 from org 1 creates a domain
        header_user2_org1 = json.dumps(user2_org1)
        auth_user2_org1 = b64encode(bytes(header_user2_org1, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user2_org1

        domain2_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain2_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # Both users should see both domains since they're in the same org
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user1_org1
        response1 = pulpcore_bindings.DomainsApi.list()
        assert response1.count == 2
        domain_names = {domain.name for domain in response1.results}
        assert domain1_name in domain_names
        assert domain2_name in domain_names

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user2_org1
        response2 = pulpcore_bindings.DomainsApi.list()
        assert response2.count == 2
        domain_names = {domain.name for domain in response2.results}
        assert domain1_name in domain_names
        assert domain2_name in domain_names


def test_group_based_domain_visibility(pulpcore_bindings, anonymous_user, gen_user, gen_object_with_cleanup):
    """Test that user added to another user's group sees their domains."""
    user_a = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user_a_group_test"}
        }
    }

    user_b = {
        "identity": {
            "org_id": 2,
            "internal": {"org_id": 2},
            "user": {"username": "user_b_group_test"}
        }
    }

    with anonymous_user:
        # Create Django users for the test
        django_user_a = gen_user(username="user_a_group_test")
        django_user_b = gen_user(username="user_b_group_test")

        # User A creates a domain
        header_user_a = json.dumps(user_a)
        auth_user_a = b64encode(bytes(header_user_a, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_a

        domain_a_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_a_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # User B initially cannot see User A's domain
        header_user_b = json.dumps(user_b)
        auth_user_b = b64encode(bytes(header_user_b, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_b

        response_before = pulpcore_bindings.DomainsApi.list()
        assert response_before.count == 0

        # Add User B to User A's default group (through Django admin)
        with django_user_a:
            from django.contrib.auth.models import Group

            # Get or create a group for User A
            group_name = f"group_for_{django_user_a.username}"
            group, created = Group.objects.get_or_create(name=group_name)
            django_user_a.groups.add(group)
            django_user_b.groups.add(group)

        # Now User B should see User A's domain due to group membership
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user_b
        response_after = pulpcore_bindings.DomainsApi.list()
        assert response_after.count == 1
        assert response_after.results[0].name == domain_a_name


def test_superuser_sees_all_domains(pulpcore_bindings, bindings_cfg, anonymous_user, gen_object_with_cleanup):
    """Test that superuser sees all domains including default."""
    user_normal = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "normal_user"}
        }
    }

    admin_user = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": bindings_cfg.username}  # This is the superuser
        }
    }

    with anonymous_user:
        # Normal user creates a domain
        header_normal = json.dumps(user_normal)
        auth_normal = b64encode(bytes(header_normal, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_normal

        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # Normal user should only see their domain
        response_normal = pulpcore_bindings.DomainsApi.list()
        assert response_normal.count == 1
        assert response_normal.results[0].name == domain_name

        # Superuser should see all domains (including default and the user's domain)
        header_admin = json.dumps(admin_user)
        auth_admin = b64encode(bytes(header_admin, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_admin

        response_admin = pulpcore_bindings.DomainsApi.list()
        assert response_admin.count >= 2  # At least default and the user's domain
        domain_names = {domain.name for domain in response_admin.results}
        assert "default" in domain_names
        assert domain_name in domain_names


def test_default_domain_invisible_to_regular_users(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Test that default domain (no DomainOrg) is not visible to regular users."""
    user_normal = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "normal_user_default_test"}
        }
    }

    with anonymous_user:
        # Normal user creates a domain
        header_normal = json.dumps(user_normal)
        auth_normal = b64encode(bytes(header_normal, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_normal

        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # Normal user should only see their domain, not the default domain
        response = pulpcore_bindings.DomainsApi.list()
        assert response.count == 1
        assert response.results[0].name == domain_name
        # Ensure default domain is not visible
        domain_names = {domain.name for domain in response.results}
        assert "default" not in domain_names


def test_domain_deduplication(pulpcore_bindings, anonymous_user, gen_user, gen_object_with_cleanup):
    """Test that domains appear only once despite multiple DomainOrg entries (user + group)."""
    user_test = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "user_dedup_test"}
        }
    }

    with anonymous_user:
        # Create Django user for the test
        django_user = gen_user(username="user_dedup_test")

        # Create a domain
        header_user = json.dumps(user_test)
        auth_user = b64encode(bytes(header_user, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user

        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # Add user to a group that also has access to the domain
        with django_user:
            from django.contrib.auth.models import Group

            # Get or create a group
            group_name = f"group_for_dedup_{django_user.username}"
            group, created = Group.objects.get_or_create(name=group_name)
            django_user.groups.add(group)

        # List domains - should see the domain exactly once despite user + group access
        response = pulpcore_bindings.DomainsApi.list()
        assert response.count == 1
        assert response.results[0].name == domain_name


def test_basic_auth_user_domain_visibility(pulpcore_bindings, gen_user, gen_object_with_cleanup):
    """Test that basic auth user (no X-RH-IDENTITY) sees only their domains."""
    # Create a basic auth user
    basic_user = gen_user(username="basic_auth_user")

    with basic_user:
        # Clear any X-RH-IDENTITY header
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

        # Create a domain using basic auth
        domain_name = str(uuid4())
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # Basic auth user should see only their domain
        response = pulpcore_bindings.DomainsApi.list()
        assert response.count == 1
        assert response.results[0].name == domain_name


def test_scope_queryset_model_guard(pulpcore_bindings, file_bindings, anonymous_user, gen_object_with_cleanup):
    """Unit test: verify that non-Domain queryset passes through unchanged."""
    user_test = {
        "identity": {
            "org_id": 1,
            "internal": {"org_id": 1},
            "user": {"username": "model_guard_test"}
        }
    }

    with anonymous_user:
        # Create a domain
        header_user = json.dumps(user_test)
        auth_user = b64encode(bytes(header_user, "ascii"))
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_user

        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi, {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

        # Test that non-Domain models (like File repositories) work normally
        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = auth_user

        # Create a file repository - this should work normally (non-Domain model)
        repo_name = str(uuid4())
        repo = gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": repo_name}, pulp_domain=domain_name
        )

        # List repositories - should see the created repository
        response = file_bindings.RepositoriesFileApi.list()
        assert response.count >= 1
        repo_names = {repo.name for repo in response.results}
        assert repo_name in repo_names
