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


def test_user_list_domain_permissions(pulpcore_bindings, anonymous_user):

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

        pulpcore_bindings.DomainsApi.list()


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
