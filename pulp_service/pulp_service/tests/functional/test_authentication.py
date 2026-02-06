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


def test_public_domain_allows_unauthenticated_read(
    anonymous_user,
    cleanup_auth_headers,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
):
    """Test that public- domains allow GET/HEAD/OPTIONS requests without authentication."""
    # Create a user with credentials to set up the domain
    setup_user = {
        "identity": {"internal": {"org_id": 44444}, "user": {"username": "publictestuser"}}
    }

    with anonymous_user:
        header_content = json.dumps(setup_user)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        # Create a domain with 'public-' prefix
        domain_name = f"public-{uuid4()}"
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

        # GET request should succeed for public- domain without auth
        response = requests.get(simple_url)
        assert (
            response.status_code == 200
        ), f"Expected 200 for GET on public- domain, got {response.status_code}"

        # HEAD request should succeed for public- domain without auth
        response = requests.head(simple_url)
        assert (
            response.status_code == 200
        ), f"Expected 200 for HEAD on public- domain, got {response.status_code}"

        # OPTIONS request should succeed for public- domain without auth
        response = requests.options(simple_url)
        assert response.status_code in [
            200,
            204,
        ], f"Expected 200/204 for OPTIONS on public- domain, got {response.status_code}"

        # Cleanup
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)


def test_non_public_domain_blocks_unauthenticated_read(
    anonymous_user,
    cleanup_auth_headers,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
):
    """Test that non-public domains block GET requests without authentication."""
    # Create a user with credentials to set up the domain
    setup_user = {
        "identity": {"internal": {"org_id": 55555}, "user": {"username": "privatetestuser"}}
    }

    with anonymous_user:
        header_content = json.dumps(setup_user)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        # Create a domain without 'public-' prefix
        domain_name = f"private-{uuid4()}"
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

        # GET request should be blocked (401/403) for non-public domain without auth
        response = requests.get(simple_url)
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for GET on non-public domain, got {response.status_code}"

        # HEAD request should be blocked (401/403) for non-public domain without auth
        response = requests.head(simple_url)
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for HEAD on non-public domain, got {response.status_code}"

        # Cleanup
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)


def test_public_domain_blocks_unsafe_methods_without_auth(
    anonymous_user,
    cleanup_auth_headers,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
):
    """Test that public- domains block POST/PUT/DELETE/PATCH requests without authentication."""
    # Create a user with credentials to set up the domain
    setup_user = {
        "identity": {"internal": {"org_id": 66666}, "user": {"username": "publicunsafetestuser"}}
    }

    with anonymous_user:
        header_content = json.dumps(setup_user)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        # Create a domain with 'public-' prefix
        domain_name = f"public-{uuid4()}"
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

        # POST request should be blocked (401/403/405) without auth
        response = requests.post(simple_url, data={})
        assert response.status_code in [
            401,
            403,
            405,
        ], f"Expected 401/403/405 for POST without auth, got {response.status_code}"

        # PUT request should be blocked (401/403/405) without auth
        response = requests.put(simple_url, data={})
        assert response.status_code in [
            401,
            403,
            405,
        ], f"Expected 401/403/405 for PUT without auth, got {response.status_code}"

        # DELETE request should be blocked (401/403/405) without auth
        response = requests.delete(simple_url)
        assert response.status_code in [
            401,
            403,
            405,
        ], f"Expected 401/403/405 for DELETE without auth, got {response.status_code}"

        # PATCH request should be blocked (401/403/405) without auth
        response = requests.patch(simple_url, data={})
        assert response.status_code in [
            401,
            403,
            405,
        ], f"Expected 401/403/405 for PATCH without auth, got {response.status_code}"

        # Cleanup
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)


def test_public_domain_case_sensitivity(
    anonymous_user,
    cleanup_auth_headers,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
):
    """Test that only lowercase 'public-' prefix works (case-sensitive check)."""
    # Create a user with credentials to set up the domain
    setup_user = {
        "identity": {"internal": {"org_id": 77777}, "user": {"username": "publiccasetestuser"}}
    }

    with anonymous_user:
        header_content = json.dumps(setup_user)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        # Create a domain with 'Public-' prefix (capital P)
        domain_name = f"Public-{uuid4()}"
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

        # GET request should be blocked (401/403) for 'Public-' domain without auth
        # This confirms that the check is case-sensitive
        response = requests.get(simple_url)
        assert response.status_code in [
            401,
            403,
        ], f"Expected 401/403 for GET on Public- domain (case-sensitive), got {response.status_code}"

        # Cleanup
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)
 