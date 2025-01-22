import json
import pytest

from uuid import uuid4
from unittest import mock
from base64 import b64encode

from pulp_service.app.authorization import DomainBasedPermission


def test_user_domain_repo_creation(pulpcore_bindings, file_bindings, anonymous_user, gen_object_with_cleanup):

    with anonymous_user:
        header_content = json.dumps({"identity": {
            "internal": {
                "org_id": 1111
            },
            "user": {
                "username": "someone"
            }
        }})
        encoded_header_content = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = (
            encoded_header_content
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

        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = (
            encoded_header_content
        )

        repo = gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )

        with pytest.raises(file_bindings.ApiException) as exp:
            gen_object_with_cleanup(
                file_bindings.RepositoriesFileApi, {"name": str(uuid4())}
            )

        assert exp.value.status == 403
