import json
import pytest

from uuid import uuid4
from base64 import b64encode


def test_user_domain_repo_creation(pulpcore_bindings, file_bindings, anonymous_user, gen_object_with_cleanup):

    user1_orgid1 = {
        "identity": {
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
        pulpcore_bindings.DomainsApi.create({
            "name": domain_name,
            "description": "string",
            "storage_class": "pulpcore.app.models.storage.FileSystem",
            "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            "redirect_to_object_storage": "true",
            "hide_guarded_distributions": "false"
        })

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
