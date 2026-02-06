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


def test_get_requests_to_public_domains_without_auth(
    anonymous_user,
    cleanup_auth_headers,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
):
    """Test that public- domains allow unauthenticated GET/HEAD/OPTIONS but block other methods."""
    setup_user = {
        "identity": {"internal": {"org_id": 33333}, "user": {"username": "publicdomainuser"}}
    }

    with anonymous_user:
        header_content = json.dumps(setup_user)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        # Create a PUBLIC domain (with public- prefix)
        public_domain_name = f"public-{str(uuid4())}"
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": public_domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Create a Python repository in the public domain
        python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = (
            auth_header
        )
        repo = gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi,
            {"name": str(uuid4())},
            pulp_domain=public_domain_name
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
            pulp_domain=public_domain_name,
        )

        api_host = python_bindings.DistributionsPypiApi.api_client.configuration.host
        public_simple_url = f"{api_host}/api/pypi/{public_domain_name}/{base_path}/simple/"

        # Remove auth headers for testing unauthenticated access
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)

        # Test 1: GET should succeed for public- domain without auth
        response = requests.get(public_simple_url)
        assert response.status_code == 200, \
            f"Expected 200 for GET on public- domain, got {response.status_code}"

        # Test 2: HEAD should succeed for public- domain without auth
        response = requests.head(public_simple_url)
        assert response.status_code == 200, \
            f"Expected 200 for HEAD on public- domain, got {response.status_code}"

        # Test 3: OPTIONS should succeed for public- domain without auth
        response = requests.options(public_simple_url)
        assert response.status_code == 200, \
            f"Expected 200 for OPTIONS on public- domain, got {response.status_code}"

        # Test 4: POST should be blocked without auth (even on public- domain)
        response = requests.post(public_simple_url, data={})
        assert response.status_code in [401, 403], \
            f"Expected 401/403 for POST on public- domain without auth, got {response.status_code}"

        # Test 5: PUT should be blocked without auth
        response = requests.put(public_simple_url, data={})
        assert response.status_code in [401, 403], \
            f"Expected 401/403 for PUT on public- domain without auth, got {response.status_code}"

        # Test 6: DELETE should be blocked without auth
        response = requests.delete(public_simple_url)
        assert response.status_code in [401, 403], \
            f"Expected 401/403 for DELETE on public- domain without auth, got {response.status_code}"

        # Test 7: PATCH should be blocked without auth
        response = requests.patch(public_simple_url, data={})
        assert response.status_code in [401, 403], \
            f"Expected 401/403 for PATCH on public- domain without auth, got {response.status_code}"


def test_get_requests_to_private_domains_require_auth(
    anonymous_user,
    cleanup_auth_headers,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
):
    """Test that non-public domains require authentication for all requests including GET."""
    setup_user = {
        "identity": {"internal": {"org_id": 44444}, "user": {"username": "privatedomainuser"}}
    }

    with anonymous_user:
        header_content = json.dumps(setup_user)
        auth_header = b64encode(bytes(header_content, "ascii"))

        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

        # Create a PRIVATE domain (without public- prefix)
        private_domain_name = str(uuid4())  # No "public-" prefix
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": private_domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        # Create a Python repository in the private domain
        python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = (
            auth_header
        )
        repo = gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi,
            {"name": str(uuid4())},
            pulp_domain=private_domain_name
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
            pulp_domain=private_domain_name,
        )

        api_host = python_bindings.DistributionsPypiApi.api_client.configuration.host
        private_simple_url = f"{api_host}/api/pypi/{private_domain_name}/{base_path}/simple/"

        # Remove auth headers
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
        python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)

        # Test 1: GET should be BLOCKED for private domain without auth
        response = requests.get(private_simple_url)
        assert response.status_code in [401, 403], \
            f"Expected 401/403 for GET on private domain without auth, got {response.status_code}"

        # Test 2: With auth, GET should succeed
        response = requests.get(
            private_simple_url,
            headers={"x-rh-identity": auth_header.decode()}
        )
        assert response.status_code == 200, \
            f"Expected 200 for GET on private domain WITH auth, got {response.status_code}"


def test_case_insensitive_public_domain_matching(
    anonymous_user,
    cleanup_auth_headers,
    pulpcore_bindings,
    python_bindings,
    gen_object_with_cleanup,
):
    """Test that Public-, PUBLIC-, and public- all match as public domains."""
    setup_user = {
        "identity": {"internal": {"org_id": 55555}, "user": {"username": "casetest"}}
    }

    test_cases = [
        f"public-{str(uuid4())}",
        f"Public-{str(uuid4())}",
        f"PUBLIC-{str(uuid4())}",
    ]

    with anonymous_user:
        header_content = json.dumps(setup_user)
        auth_header = b64encode(bytes(header_content, "ascii"))

        for domain_name in test_cases:
            pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header

            gen_object_with_cleanup(
                pulpcore_bindings.DomainsApi,
                {
                    "name": domain_name,
                    "storage_class": "pulpcore.app.models.storage.FileSystem",
                    "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
                },
            )

            python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = (
                auth_header
            )
            repo = gen_object_with_cleanup(
                python_bindings.RepositoriesPythonApi,
                {"name": str(uuid4())},
                pulp_domain=domain_name
            )

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
            test_url = f"{api_host}/api/pypi/{domain_name}/{base_path}/simple/"

            # Remove auth
            pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
            python_bindings.RepositoriesPythonApi.api_client.default_headers.pop(
                "x-rh-identity", None
            )
            python_bindings.DistributionsPypiApi.api_client.default_headers.pop(
                "x-rh-identity", None
            )

            # All case variants should allow unauthenticated GET
            response = requests.get(test_url)
            assert response.status_code == 200, \
                f"Expected 200 for GET on {domain_name}, got {response.status_code}"
 