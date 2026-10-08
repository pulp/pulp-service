import json
import uuid
from base64 import b64encode


def _identity_header(org_id, username):
    identity = {
        "identity": {
            "org_id": org_id,
            "internal": {"org_id": org_id},
            "user": {"username": username},
        }
    }
    return b64encode(json.dumps(identity).encode("ascii")).decode("ascii")


def test_auto_created_user_can_create_domain_via_generic_endpoint(
    pulpcore_bindings,
    anonymous_user,
    monitor_task,
    cleanup_auth_headers,  # noqa: ARG001
):
    """A freshly auto-created, non-admin service account can create a domain.

    The Pulp CLI ``domain create`` hits the generic pulpcore ``DomainsApi`` whose create is
    gated by ``has_model_perms:core.add_domain``. New users are granted the
    ``core.domain_creator`` role on creation, so this must succeed instead of returning
    "Operation domains_create is not authorized" (PULP-2526).
    """
    org_id = uuid.uuid4().int % 1_000_000_000
    username = f"svc-{uuid.uuid4().hex[:12]}"
    domain_name = f"test{uuid.uuid4().hex[:16]}"
    header = _identity_header(org_id, username)

    with anonymous_user:
        # Drop admin basic auth; authenticate only as the brand-new service account so the
        # generic DomainsApi create is evaluated against that user's permissions.
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = header
        domain = pulpcore_bindings.DomainsApi.create(
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            }
        )

    # Clean up as admin (anonymous_user exited -> admin basic auth restored).
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    monitor_task(pulpcore_bindings.DomainsApi.delete(domain.pulp_href).task)
