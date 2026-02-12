import json
import pytest
import requests

from uuid import uuid4
from base64 import b64encode


def test_authentication_with_username_and_org_id(
    anonymous_user,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
    cleanup_auth_headers,
):
    """Test that requests with both username and org_id in x-rh-identity header work."""
    user_with_orgid = {"identity": {"org_id": 12345, "user": {"username": "testuser"}}}

    # ensure bindings are not previously authenticated
    with anonymous_user:
        header_content = json.dumps(user_with_orgid)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        domain_name = str(uuid4())
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        assert domain is not None
        assert domain.name == domain_name

        # Create a repository in the domain to verify permissions work
        python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = (
            auth_header
        )

        repo = gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )

        assert repo is not None


def test_authentication_with_org_id_and_username(
    anonymous_user,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
    cleanup_auth_headers,
):
    """Test that requests with org_id and username in x-rh-identity header work."""
    user_with_orgid = {"identity": {"org_id": 67890, "user": {"username": "testuser2"}}}
    with anonymous_user:
        header_content = json.dumps(user_with_orgid)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        domain_name = str(uuid4())
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        assert domain is not None
        assert domain.name == domain_name

        # Create a repository in the domain to verify permissions work
        python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = (
            auth_header
        )

        repo = gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )

        assert repo is not None


def test_authentication_with_only_username(
    anonymous_user,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
    cleanup_auth_headers,
):
    """Test that requests with only username in x-rh-identity header work."""
    only_username = {"identity": {"user": {"username": "anotheruser"}}}

    with anonymous_user:
        header_content = json.dumps(only_username)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        domain_name = str(uuid4())
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        assert domain is not None
        assert domain.name == domain_name

        # Create a repository in the domain to verify permissions work
        python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = (
            auth_header
        )

        repo = gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )

        assert repo is not None


def test_get_requests_without_auth_to_simple_api(
    anonymous_user,
    cleanup_auth_headers,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
):
    """Test that all domains allow GET requests without authentication but block other methods."""
    # Create a user with credentials to set up the domain
    setup_user = {
        "identity": {"internal": {"org_id": 33333}, "user": {"username": "publicdomainuser"}}
    }

    with anonymous_user:
        header_content = json.dumps(setup_user)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        # Create a domain (any domain, not necessarily public-)
        domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Create a Python repository
        python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = (
            auth_header
        )

        repo = gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi, {"name": str(uuid4())}, pulp_domain=domain_name
        )

        # Create a Python distribution
        python_bindings.DistributionsPypiApi.api_client.default_headers["x-rh-identity"] = (
            auth_header
        )
        base_path = str(uuid4())
        gen_object_with_cleanup(
            python_bindings.DistributionsPypiApi,
            {
                "name": str(uuid4()),
                "base_path": base_path,
                "repository": repo.pulp_href,
            },
            pulp_domain=domain_name,
        )

        api_host = python_bindings.DistributionsPypiApi.api_client.configuration.host
        simple_url = f"{api_host}/api/pypi/{domain_name}/{base_path}/simple/"

        # GET request should succeed for any domain without auth
        response = requests.get(simple_url)
        assert (
            response.status_code == 200
        ), f"Expected 200 for GET on domain, got {response.status_code}"

        # POST request should be blocked (401/403) without auth
        response = requests.post(simple_url, data={})
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for POST without auth, got {response.status_code}"

        # PUT request should be blocked (401/403) without auth
        response = requests.put(simple_url, data={})
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for PUT without auth, got {response.status_code}"

        # DELETE request should be blocked (401/403) without auth
        response = requests.delete(simple_url)
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for DELETE without auth, got {response.status_code}"

        # PATCH request should be blocked (401/403) without auth
        response = requests.patch(simple_url, data={})
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for PATCH without auth, got {response.status_code}"

        # some bindings has scope=session, so we need to remove the headers to avoid
        # affecting the other tests
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)


def test_public_domain_allows_unauthenticated_get(
    anonymous_user,
    cleanup_auth_headers,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
):
    """Test that domains with 'public-' in the name allow GET requests without authentication
    on the standard API, while non-public domains block unauthenticated GET requests."""
    setup_user = {
        "identity": {"internal": {"org_id": 44444}, "user": {"username": "publicdomaintestuser"}}
    }

    with anonymous_user:
        header_content = json.dumps(setup_user)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        # Create a public domain (name contains "public-")
        public_domain_name = f"public-{uuid4()}"
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": public_domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Create a non-public domain
        private_domain_name = str(uuid4())
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": private_domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Create a repository in the public domain
        python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = (
            auth_header
        )
        gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi,
            {"name": str(uuid4())},
            pulp_domain=public_domain_name,
        )

        # Create a repository in the private domain
        gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi,
            {"name": str(uuid4())},
            pulp_domain=private_domain_name,
        )

        api_host = pulpcore_bindings.DomainsApi.api_client.configuration.host
        public_repos_url = (
            f"{api_host}/api/pulp/{public_domain_name}/api/v3/repositories/python/python/"
        )
        private_repos_url = (
            f"{api_host}/api/pulp/{private_domain_name}/api/v3/repositories/python/python/"
        )

        # GET request on public domain should succeed without auth
        response = requests.get(public_repos_url)
        assert (
            response.status_code == 200
        ), f"Expected 200 for GET on public domain, got {response.status_code}"

        # GET request on non-public domain should be blocked without auth
        response = requests.get(private_repos_url)
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for GET on non-public domain without auth, got {response.status_code}"

        # POST request on public domain should still be blocked without auth
        response = requests.post(public_repos_url, json={"name": "should-fail"})
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for POST on public domain without auth, got {response.status_code}"

        # PUT request on public domain should be blocked without auth
        response = requests.put(public_repos_url, json={})
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for PUT on public domain without auth, got {response.status_code}"

        # DELETE request on public domain should be blocked without auth
        response = requests.delete(public_repos_url)
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for DELETE on public domain without auth, got {response.status_code}"

        # PATCH request on public domain should be blocked without auth
        response = requests.patch(public_repos_url, json={})
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for PATCH on public domain without auth, got {response.status_code}"

        # Clean up headers
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
 