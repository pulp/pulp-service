#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
# ]
# ///
"""Audit Pulp domains, distributions, and content guards without mutating Pulp."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import netrc
import os
import random
import ssl
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests


ENVIRONMENTS = {
    "stage": "https://packages.stage.redhat.com",
    "prod": "https://packages.redhat.com",
}
PUBLIC_DOMAIN_PREFIX = "public-"
DEFAULT_TIMEOUT = 30.0
DEFAULT_GUARD_DETAIL_TIMEOUT = 120.0
PAGE_SIZE = 100
Progress = Callable[[str], None]
MAX_RETRIES = 3
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_BACKOFF = 30.0
RETRY_MANIFEST_SCHEMA = "pulp-content-guard-audit/retry-manifest"
RETRY_MANIFEST_VERSION = 1


class AuditError(RuntimeError):
    """Raised when the audit cannot produce a complete inventory."""


def _write_json_atomically(path: str, payload: dict) -> None:
    """Write owner-readable JSON without exposing a partially written file."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as temporary_file:
            temporary_file.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, output)
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _operation_id(base_url: str, domain: dict) -> str:
    identity = {
        "base_url": base_url,
        "domain_href": domain.get("pulp_href"),
        "kind": "audit_domain",
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _audit_scope(
    environment: str,
    base_url: str,
    include_public: bool,
    probe_base_urls: list[str],
    probe_timeout: float,
    endpoints: list[dict[str, str]],
) -> dict:
    return {
        "environment": environment,
        "base_url": base_url,
        "public_domains_included": include_public,
        "probe_base_urls": sorted(probe_base_urls),
        "probe_timeout": probe_timeout,
        "distribution_endpoints": sorted(endpoint["path"] for endpoint in endpoints),
    }


def _scope_for_comparison(scope: dict) -> dict:
    """Normalize the retired generic distribution route for manifest retries."""
    normalized = dict(scope)
    normalized["distribution_endpoints"] = sorted(
        endpoint
        for endpoint in scope.get("distribution_endpoints", [])
        if endpoint != "distributions"
    )
    return normalized


def _load_retry_manifest(path: str) -> tuple[dict, str]:
    manifest_path = Path(path)
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise AuditError(
            f"Unable to read retry manifest: {type(error).__name__}"
        ) from error
    if not isinstance(manifest, dict):
        raise AuditError("Retry manifest must contain a JSON object")
    if manifest.get("schema") != RETRY_MANIFEST_SCHEMA:
        raise AuditError("Retry manifest has an unsupported schema")
    if manifest.get("version") != RETRY_MANIFEST_VERSION:
        raise AuditError("Retry manifest has an unsupported version")
    operations = manifest.get("operations")
    if not isinstance(operations, list):
        raise AuditError("Retry manifest operations must be a list")
    operation_ids = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise AuditError("Retry manifest contains an invalid operation")
        operation_id = operation.get("id")
        domain = operation.get("domain")
        failure_count = operation.get("failure_count", 0)
        if (
            not isinstance(operation_id, str)
            or operation_id in operation_ids
            or operation.get("kind") != "audit_domain"
            or not isinstance(domain, dict)
            or not isinstance(domain.get("name"), str)
            or not isinstance(domain.get("pulp_href"), str)
            or not isinstance(failure_count, int)
            or failure_count < 0
        ):
            raise AuditError(
                "Retry manifest contains an invalid or duplicate operation"
            )
        operation_ids.add(operation_id)
    return manifest, hashlib.sha256(raw).hexdigest()


class RetryManifest:
    """Checkpoint domain-level failures for a later, separately scoped retry."""

    def __init__(
        self,
        path: str,
        scope: dict,
        operations: list[dict],
        source_sha256: str | None = None,
    ):
        self.path = path
        now = datetime.now(UTC).isoformat()
        self.data = {
            "schema": RETRY_MANIFEST_SCHEMA,
            "version": RETRY_MANIFEST_VERSION,
            "state": "running",
            "created_at": now,
            "updated_at": now,
            "audit_scope": scope,
            "operations": sorted(operations, key=lambda operation: operation["id"]),
        }
        if source_sha256:
            self.data["source_manifest_sha256"] = source_sha256

    @property
    def operations(self) -> list[dict]:
        return self.data["operations"]

    def write(self, state: str | None = None) -> None:
        if state:
            self.data["state"] = state
        self.data["updated_at"] = datetime.now(UTC).isoformat()
        _write_json_atomically(self.path, self.data)

    def mark_success(self, operation_id: str) -> None:
        self.data["operations"] = [
            operation
            for operation in self.operations
            if operation["id"] != operation_id
        ]
        self.write()

    def mark_failure(self, operation_id: str, error: str) -> None:
        for operation in self.operations:
            if operation["id"] == operation_id:
                operation["state"] = "failed"
                operation["failure_count"] = operation.get("failure_count", 0) + 1
                operation["last_failed_at"] = datetime.now(UTC).isoformat()
                operation["error"] = error
                break
        self.write()

    def mark_interrupted(self) -> None:
        self.write("interrupted")

    def finish(self) -> None:
        self.write("complete" if not self.operations else "incomplete")


def _default_port(scheme: str) -> int | None:
    return {"http": 80, "https": 443}.get(scheme)


def _validate_base_url(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise AuditError("Base URL must use https")
    if parsed.username or parsed.password:
        raise AuditError("Base URL must not contain user information")
    if not parsed.hostname:
        raise AuditError(f"Invalid base URL: {base_url}")
    try:
        port = parsed.port or _default_port(parsed.scheme)
    except ValueError as error:
        raise AuditError("Base URL has an invalid port") from error
    if port is None:
        raise AuditError("Base URL must include a valid port")
    return parsed.hostname, port


def _validate_same_origin(
    url: str, expected_scheme: str, expected_host: str, expected_port: int
) -> None:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise AuditError("Authenticated URL contains user information")
    if parsed.scheme != expected_scheme:
        raise AuditError("Authenticated URL scheme mismatch")
    if parsed.hostname != expected_host:
        raise AuditError(
            f"Authenticated URL host mismatch: expected {expected_host}, got {parsed.hostname}"
        )
    try:
        port = parsed.port or _default_port(parsed.scheme)
    except ValueError as error:
        raise AuditError("Authenticated URL has an invalid port") from error
    if port != expected_port:
        raise AuditError(
            f"Authenticated URL port mismatch: expected {expected_port}, got {port}"
        )


def _ca_bundle() -> str | bool:
    """Use an explicit or system CA bundle before Requests' certifi bundle."""
    for variable in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        if configured := os.environ.get(variable):
            return configured

    paths = ssl.get_default_verify_paths()
    for candidate in (paths.cafile, paths.openssl_cafile):
        if candidate and Path(candidate).is_file():
            return candidate
    return True


def _response_error(response: requests.Response) -> AuditError:
    path = urlparse(response.url).path
    return AuditError(f"GET {path} returned HTTP {response.status_code}")


def _safe_next_url(
    next_url: str | None,
    current_url: str,
    expected_scheme: str,
    expected_host: str,
    expected_port: int,
) -> str | None:
    """Validate a Pulp pagination URL before following it with credentials."""
    if not next_url:
        return None

    absolute_url = urljoin(current_url, next_url)
    _validate_same_origin(absolute_url, expected_scheme, expected_host, expected_port)
    return absolute_url


def _guard_href(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        href = value.get("pulp_href")
        return href if isinstance(href, str) else None
    return None


def guard_type(href: str | None) -> str | None:
    """Extract the content-guard subtype from a Pulp href."""
    if not href:
        return None
    parts = [part for part in urlparse(href).path.rstrip("/").split("/") if part]
    try:
        index = parts.index("contentguards")
    except ValueError:
        return None
    if index + 2 < len(parts):
        return parts[index + 2]
    return None


def discover_distribution_endpoints(schema: dict) -> list[dict[str, str]]:
    """Find distribution collection endpoints from the installed Pulp OpenAPI schema."""
    endpoints: dict[str, dict[str, str]] = {}
    for path, operations in schema.get("paths", {}).items():
        operation = operations.get("get")
        if (
            operation is None
            or not _is_collection_operation(operation)
            or "/api/v3/" not in path
        ):
            continue

        api_path = path.split("/api/v3/", 1)[1].strip("/")
        parts = api_path.split("/") if api_path else []
        if not parts or parts[0] != "distributions":
            continue
        if any(part.startswith("{") for part in parts):
            continue

        endpoint_path = "/".join(parts)
        if endpoint_path == "distributions":
            # The generic aggregation endpoint is unreliable for malformed or
            # stale subtype records; typed plugin endpoints provide the audit set.
            continue
        endpoint_name = "/".join(parts[1:]) or "generic"
        endpoints[endpoint_path] = {
            "name": endpoint_name,
            "path": endpoint_path,
        }

    return sorted(endpoints.values(), key=lambda endpoint: endpoint["path"])


def _is_collection_operation(operation: dict) -> bool:
    """Accept only operations whose schema describes a paginated list."""
    responses = operation.get("responses")
    if not responses:
        return False

    for response in responses.values():
        schema = (
            response.get("content", {}).get("application/json", {}).get("schema", {})
        )
        reference = schema.get("$ref", "")
        if "Paginated" in reference:
            return True
        properties = schema.get("properties", {})
        if "results" in properties and "next" in properties:
            return True
    return False


def filter_domains(domains: list[dict], include_public: bool) -> list[dict]:
    if include_public:
        return domains
    return [
        domain
        for domain in domains
        if not domain.get("name", "").startswith(PUBLIC_DOMAIN_PREFIX)
    ]


def classify_pulp_access(effective_guard_href: str | None) -> str:
    return "pulp_guarded" if effective_guard_href else "pulp_unguarded"


def build_probe_url(
    probe_base_url: str, domain_name: str, base_path: str | None
) -> str | None:
    if not base_path:
        return None
    if "{domain}" in probe_base_url or "{base_path}" in probe_base_url:
        return probe_base_url.format(domain=domain_name, base_path=base_path.strip("/"))
    return f"{probe_base_url.rstrip('/')}/{domain_name}/{base_path.strip('/')}/"


def validate_probe_base_url(probe_base_url: str) -> str:
    parsed = urlparse(probe_base_url)
    if parsed.scheme != "https":
        raise AuditError("Probe base URL must use https")
    if parsed.username or parsed.password:
        raise AuditError("Probe base URL must not contain user information")
    if not parsed.hostname:
        raise AuditError("Probe base URL must include a hostname")
    if parsed.query or parsed.fragment:
        raise AuditError("Probe base URL must not contain a query or fragment")
    try:
        parsed.port
    except ValueError as error:
        raise AuditError("Probe base URL has an invalid port") from error
    return probe_base_url.rstrip("/")


def classify_probe_status(status_code: int | None, error: str | None = None) -> str:
    if error:
        return "probe_indeterminate"
    if status_code is not None and 200 <= status_code < 300:
        return "probe_allowed"
    if status_code in {401, 403}:
        return "probe_denied"
    return "probe_indeterminate"


class RequestRateLimiter:
    """Limit request starts across all worker threads and coordinate cooldowns."""

    def __init__(self, requests_per_second: float):
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.requests_per_second = requests_per_second
        self.interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next_request = 0.0
        self._cooldown_until = 0.0
        self._cooldown_generation = 0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                target = max(now, self._next_request, self._cooldown_until)
                self._next_request = target + self.interval
                generation = self._cooldown_generation
                delay = target - now
            if delay > 0:
                time.sleep(delay)
            with self._lock:
                if (
                    generation == self._cooldown_generation
                    and time.monotonic() >= self._cooldown_until
                ):
                    return

    def cooldown(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)
            self._cooldown_generation += 1


def _retry_after_seconds(response: requests.Response) -> float:
    value = response.headers.get("Retry-After")
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _backoff_seconds(attempt: int) -> float:
    return random.uniform(0.0, min(MAX_BACKOFF, 2**attempt))


def _retryable_exception(error: requests.RequestException) -> bool:
    return isinstance(
        error, (requests.ConnectionError, requests.Timeout)
    ) and not isinstance(error, requests.exceptions.SSLError)


class PulpClient:
    """Small authenticated client for the read-only Pulp API endpoints used here."""

    def __init__(
        self,
        base_url: str,
        session: requests.Session,
        timeout: float = DEFAULT_TIMEOUT,
        progress: Progress | None = None,
        request_limiter: RequestRateLimiter | None = None,
        guard_cache_lock: threading.RLock | None = None,
        guard_detail_timeout: float = DEFAULT_GUARD_DETAIL_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.timeout = timeout
        self.guard_detail_timeout = guard_detail_timeout
        self.progress = progress
        self.request_limiter = request_limiter
        self.guard_cache_lock = guard_cache_lock or threading.RLock()
        parsed = urlparse(self.base_url)
        self.expected_host, self.expected_port = _validate_base_url(self.base_url)
        self.expected_scheme = parsed.scheme

    def api_url(self, domain: str, path: str) -> str:
        encoded_domain = quote(domain, safe="")
        return f"{self.base_url}/api/pulp/{encoded_domain}/api/v3/{path.lstrip('/')}"

    def get_json(
        self, url: str, request_timeout: float | None = None, **kwargs
    ) -> dict:
        _validate_same_origin(
            url,
            self.expected_scheme,
            self.expected_host,
            self.expected_port,
        )
        kwargs["allow_redirects"] = False
        for attempt in range(MAX_RETRIES + 1):
            if self.request_limiter:
                self.request_limiter.wait()
            try:
                response = self.session.get(
                    url,
                    timeout=(
                        request_timeout if request_timeout is not None else self.timeout
                    ),
                    **kwargs,
                )
            except requests.RequestException as error:
                if not _retryable_exception(error) or attempt == MAX_RETRIES:
                    raise AuditError(
                        f"GET {urlparse(url).path} failed: {type(error).__name__}"
                    ) from error
                delay = _backoff_seconds(attempt)
                if self.request_limiter:
                    self.request_limiter.cooldown(delay)
                else:
                    time.sleep(delay)
                continue

            if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                delay = max(_retry_after_seconds(response), _backoff_seconds(attempt))
                response.close()
                if self.request_limiter:
                    self.request_limiter.cooldown(delay)
                else:
                    time.sleep(delay)
                continue

            if not 200 <= response.status_code < 300:
                error = _response_error(response)
                response.close()
                raise error
            try:
                return response.json()
            except ValueError as error:
                raise AuditError(
                    f"GET {urlparse(url).path} returned invalid JSON"
                ) from error
            finally:
                response.close()

        raise AssertionError("unreachable")

    def get_pages(
        self,
        url: str,
        params: dict[str, object] | None = None,
        label: str | None = None,
    ) -> list[dict]:
        """Return every result while validating Pulp's next-link host and scheme."""
        results: list[dict] = []
        next_url = url
        first_request = True
        page_number = 0
        seen_urls: set[str] = set()
        while next_url:
            if next_url in seen_urls:
                raise AuditError(
                    f"Pagination loop detected at {urlparse(next_url).path}"
                )
            seen_urls.add(next_url)
            page_number += 1
            if self.progress and label:
                self.progress(f"{label}: fetching page {page_number}")
            data = self.get_json(next_url, params=params if first_request else None)
            first_request = False
            page_results = data.get("results", [])
            if not isinstance(page_results, list):
                raise AuditError(
                    f"Pulp response from {urlparse(next_url).path} has invalid results"
                )
            results.extend(page_results)
            next_url = _safe_next_url(
                data.get("next"),
                next_url,
                self.expected_scheme,
                self.expected_host,
                self.expected_port,
            )
        if self.progress and label:
            self.progress(f"{label}: collected {len(results)} records")
        return results

    def list_domains(self) -> list[dict]:
        fields = "name,pulp_href,pulp_labels,default_content_guard"
        return self.get_pages(
            self.api_url("default", "domains/"),
            params={"limit": PAGE_SIZE, "fields": fields},
            label="domains",
        )

    def get_schema(self) -> dict:
        return self.get_json(self.api_url("default", "docs/api.json"))

    def list_distribution_endpoints(self) -> list[dict[str, str]]:
        endpoints = discover_distribution_endpoints(self.get_schema())
        if not endpoints:
            raise AuditError(
                "OpenAPI schema did not expose any distribution collection endpoints"
            )
        return endpoints

    def list_domain_guards(self, domain_name: str) -> list[dict]:
        return self.get_pages(
            self.api_url(domain_name, "contentguards/"),
            params={"limit": PAGE_SIZE},
            label=f"{domain_name}/content guards",
        )

    def list_distributions(self, domain_name: str, endpoint_path: str) -> list[dict]:
        collection_path = endpoint_path.rstrip("/") + "/"
        return self.get_pages(
            self.api_url(domain_name, collection_path),
            params={
                "limit": PAGE_SIZE,
                "fields": "name,pulp_href,base_path,base_url,content_guard,repository",
            },
            label=f"{domain_name}/{collection_path}",
        )

    def resolve_guard(
        self, href: str | None, cache: dict[str, dict], stack: set[str] | None = None
    ) -> dict | None:
        with self.guard_cache_lock:
            return self._resolve_guard(href, cache, stack)

    def _resolve_guard(
        self, href: str | None, cache: dict[str, dict], stack: set[str] | None = None
    ) -> dict | None:
        if not href:
            return None
        absolute_href = self.absolute_url(href)
        stack = stack or set()
        if absolute_href in stack:
            return {"pulp_href": href, "type": guard_type(href), "cycle": True}
        if absolute_href in cache:
            return cache[absolute_href]

        payload = self.get_json(
            absolute_href, request_timeout=self.guard_detail_timeout
        )
        guard = {
            "pulp_href": href,
            "name": payload.get("name"),
            "type": guard_type(href),
        }
        cache[absolute_href] = guard
        if guard["type"] == "composite":
            children = []
            for child_href in payload.get("guards") or []:
                children.append(
                    self._resolve_guard(child_href, cache, stack | {absolute_href})
                )
            guard["guards"] = children
        return guard

    def absolute_url(self, href: str) -> str:
        absolute_href = urljoin(self.base_url + "/", href)
        _validate_same_origin(
            absolute_href,
            self.expected_scheme,
            self.expected_host,
            self.expected_port,
        )
        return absolute_href


def probe_distribution(
    probe_session: requests.Session,
    url: str,
    endpoint: str,
    timeout: float,
    request_limiter: RequestRateLimiter | None = None,
) -> dict:
    result = {"endpoint": endpoint, "url": url, "method": "HEAD"}
    response = None
    try:
        if request_limiter:
            request_limiter.wait()
        response = probe_session.head(url, allow_redirects=False, timeout=timeout)
        if response.status_code == 405:
            response.close()
            if request_limiter:
                request_limiter.wait()
            response = probe_session.get(
                url, allow_redirects=False, stream=True, timeout=timeout
            )
            result["method"] = "GET"
        result["status_code"] = response.status_code
        result["classification"] = classify_probe_status(response.status_code)
    except requests.RequestException as error:
        result["error"] = type(error).__name__
        result["classification"] = classify_probe_status(None, result["error"])
    finally:
        if response is not None:
            response.close()
    return result


def create_probe_session() -> requests.Session:
    """Create a session that cannot read proxy or netrc settings from the environment."""
    session = requests.Session()
    session.auth = None
    session.trust_env = False
    session.verify = _ca_bundle()
    return session


def probe_endpoint(
    probe_session: requests.Session | None,
    url: str,
    endpoint: str,
    timeout: float,
    request_limiter: RequestRateLimiter | None = None,
) -> dict:
    if probe_session is not None:
        return probe_distribution(
            probe_session, url, endpoint, timeout, request_limiter
        )
    with create_probe_session() as safe_session:
        return probe_distribution(safe_session, url, endpoint, timeout, request_limiter)


def audit_domain(
    client: PulpClient,
    domain: dict,
    endpoints: list[dict[str, str]],
    probe_base_urls: list[str],
    probe_timeout: float,
    guard_cache: dict[str, dict],
    probe_session: requests.Session | None = None,
    progress: Progress | None = None,
) -> dict:
    probe_base_urls = [validate_probe_base_url(url) for url in probe_base_urls]
    domain_name = domain["name"]
    if progress:
        progress(f"Auditing domain {domain_name}")
    domain_default_href = _guard_href(domain.get("default_content_guard"))
    domain_guards = client.list_domain_guards(domain_name)
    domain_guard_hrefs = {
        href
        for href in (_guard_href(guard.get("pulp_href")) for guard in domain_guards)
        if href
    }
    if domain_default_href:
        domain_guard_hrefs.add(domain_default_href)
    domain_guard_details = [
        client.resolve_guard(href, guard_cache) for href in sorted(domain_guard_hrefs)
    ]

    distributions = []
    seen_distributions: set[str] = set()
    for endpoint in endpoints:
        for distribution in client.list_distributions(domain_name, endpoint["path"]):
            distribution_key = distribution.get("pulp_href") or (
                f"{endpoint['path']}:{distribution.get('name')}:{distribution.get('base_path')}"
            )
            if distribution_key in seen_distributions:
                continue
            seen_distributions.add(distribution_key)
            explicit_href = _guard_href(distribution.get("content_guard"))
            effective_href = explicit_href or domain_default_href
            item = {
                "domain": domain_name,
                "domain_href": domain.get("pulp_href"),
                "type": endpoint["name"],
                "name": distribution.get("name"),
                "pulp_href": distribution.get("pulp_href"),
                "base_path": distribution.get("base_path"),
                "base_url": distribution.get("base_url"),
                "repository": distribution.get("repository"),
                "explicit_content_guard": explicit_href,
                "effective_content_guard": effective_href,
                "explicit_guard_type": guard_type(explicit_href),
                "effective_guard_type": guard_type(effective_href),
                "pulp_classification": classify_pulp_access(effective_href),
                "gateway_assessment": "endpoint_probe_only"
                if probe_base_urls
                else "not_evaluated",
            }
            if effective_href:
                item["effective_guard"] = client.resolve_guard(
                    effective_href, guard_cache
                )

            if probe_base_urls:
                item["probes"] = []
                for probe_base_url in probe_base_urls:
                    probe_url = build_probe_url(
                        probe_base_url, domain_name, item["base_path"]
                    )
                    if probe_url is None:
                        item["probes"].append(
                            {
                                "endpoint": probe_base_url,
                                "classification": "probe_indeterminate",
                                "error": "distribution has no base_path",
                            }
                        )
                    else:
                        item["probes"].append(
                            probe_endpoint(
                                probe_session,
                                probe_url,
                                probe_base_url,
                                probe_timeout,
                                client.request_limiter,
                            )
                        )
            distributions.append(item)

    result = {
        "name": domain_name,
        "pulp_href": domain.get("pulp_href"),
        "pulp_labels": domain.get("pulp_labels", {}),
        "default_content_guard": domain_default_href,
        "default_guard": client.resolve_guard(domain_default_href, guard_cache),
        "content_guard_count": len(domain_guard_hrefs),
        "content_guards": domain_guard_details,
        "distribution_count": len(distributions),
        "has_content_guard": bool(domain_guard_hrefs),
        "distributions": distributions,
    }
    if progress:
        progress(
            f"Completed domain {domain_name}: {len(distributions)} distributions, "
            f"{len(domain_guard_hrefs)} content guards"
        )
    return result


def audit_domain_in_worker(
    base_url: str,
    timeout: float,
    guard_detail_timeout: float,
    domain: dict,
    endpoints: list[dict[str, str]],
    probe_base_urls: list[str],
    probe_timeout: float,
    guard_cache: dict[str, dict],
    guard_cache_lock: threading.RLock,
    request_limiter: RequestRateLimiter | None,
) -> dict:
    session = create_session(base_url)
    client = PulpClient(
        base_url,
        session,
        timeout=timeout,
        request_limiter=request_limiter,
        guard_cache_lock=guard_cache_lock,
        guard_detail_timeout=guard_detail_timeout,
    )
    probe_session = create_probe_session() if probe_base_urls else None
    try:
        return audit_domain(
            client,
            domain,
            endpoints,
            probe_base_urls,
            probe_timeout,
            guard_cache,
            probe_session,
        )
    finally:
        if probe_session is not None:
            probe_session.close()
        session.close()


def build_report(
    client: PulpClient,
    environment: str,
    include_public: bool,
    probe_base_urls: list[str],
    probe_timeout: float,
    progress: Progress | None = None,
    workers: int = 1,
    failed_operations_output: str | None = None,
    retry_manifest: dict | None = None,
    retry_manifest_sha256: str | None = None,
    guard_detail_timeout: float = DEFAULT_GUARD_DETAIL_TIMEOUT,
) -> dict:
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    if not math.isfinite(guard_detail_timeout) or guard_detail_timeout <= 0:
        raise ValueError("guard_detail_timeout must be a positive finite number")
    client.guard_detail_timeout = guard_detail_timeout
    probe_base_urls = [validate_probe_base_url(url) for url in probe_base_urls]
    report = {
        "metadata": {
            "environment": environment,
            "base_url": client.base_url,
            "generated_at": datetime.now(UTC).isoformat(),
            "public_domains_included": include_public,
            "probe_base_urls": probe_base_urls,
            "workers": workers,
            "request_rate": (
                client.request_limiter.requests_per_second
                if client.request_limiter
                else None
            ),
            "guard_detail_timeout": guard_detail_timeout,
            "mode": "retry" if retry_manifest else "full",
        },
        "complete": True,
        "errors": [],
        "domains_without_content_guards": [],
        "domains": [],
        "distributions": [],
    }

    try:
        domains = sorted(
            filter_domains(client.list_domains(), include_public),
            key=lambda domain: domain.get("name", ""),
        )
        endpoints = client.list_distribution_endpoints()
    except AuditError as error:
        report["complete"] = False
        report["errors"].append(str(error))
        return report

    report["metadata"]["distribution_endpoints"] = [
        endpoint["path"] for endpoint in endpoints
    ]
    scope = _audit_scope(
        environment,
        client.base_url,
        include_public,
        probe_base_urls,
        probe_timeout,
        endpoints,
    )
    if retry_manifest_sha256:
        report["metadata"]["source_manifest_sha256"] = retry_manifest_sha256
    expected_scope = retry_manifest.get("audit_scope") if retry_manifest else None
    if retry_manifest and _scope_for_comparison(
        expected_scope
    ) != _scope_for_comparison(scope):
        report["complete"] = False
        report["errors"].append(
            "Retry manifest scope does not match this audit invocation"
        )
        return report

    if retry_manifest:
        source_operations = retry_manifest["operations"]
        current_by_identity = {
            (domain.get("name"), domain.get("pulp_href")): domain for domain in domains
        }
        selected_domains = []
        manifest_operations = []
        unresolved_errors = []
        for source_operation in source_operations:
            operation_domain = source_operation["domain"]
            identity = (operation_domain["name"], operation_domain["pulp_href"])
            current_domain = current_by_identity.get(identity)
            operation = {
                "id": source_operation["id"],
                "kind": "audit_domain",
                "domain": {
                    "name": operation_domain["name"],
                    "pulp_href": operation_domain["pulp_href"],
                },
                "failure_count": source_operation.get("failure_count", 0),
                "state": "pending",
            }
            expected_operation_id = _operation_id(
                client.base_url, current_domain or operation_domain
            )
            if source_operation["id"] != expected_operation_id:
                operation["state"] = "failed"
                operation["error"] = "Operation identity does not match audit scope"
                unresolved_errors.append((operation_domain["name"], operation["error"]))
            elif current_domain is None:
                operation["state"] = "failed"
                operation["error"] = "Domain identity is missing or changed"
                unresolved_errors.append((operation_domain["name"], operation["error"]))
            else:
                selected_domains.append(current_domain)
            manifest_operations.append(operation)
        domains = sorted(selected_domains, key=lambda domain: domain.get("name", ""))
    else:
        manifest_operations = [
            {
                "id": _operation_id(client.base_url, domain),
                "kind": "audit_domain",
                "domain": {
                    "name": domain.get("name"),
                    "pulp_href": domain.get("pulp_href"),
                },
                "state": "pending",
            }
            for domain in domains
        ]
        unresolved_errors = []

    retry_state = None
    if failed_operations_output:
        retry_state = RetryManifest(
            failed_operations_output,
            scope,
            manifest_operations,
            retry_manifest_sha256,
        )
        retry_state.write()
        for operation in manifest_operations:
            if operation.get("state") == "failed":
                retry_state.mark_failure(operation["id"], operation["error"])

    report["metadata"]["selected_domain_count"] = len(domains)
    if progress:
        progress(
            f"Found {len(domains)} eligible domains and "
            f"{len(endpoints)} distribution endpoint types; using {workers} worker(s)"
        )
    guard_cache: dict[str, dict] = {}
    request_limiter = client.request_limiter
    guard_cache_lock = client.guard_cache_lock
    probe_session = create_probe_session() if probe_base_urls and workers == 1 else None
    results: dict[int, dict] = {}
    errors: dict[int, str] = {}
    operation_ids = {
        index: _operation_id(client.base_url, domain)
        for index, domain in enumerate(domains)
    }

    def audit_one(domain: dict) -> dict:
        if workers == 1:
            return audit_domain(
                client,
                domain,
                endpoints,
                probe_base_urls,
                probe_timeout,
                guard_cache,
                probe_session,
                progress,
            )
        return audit_domain_in_worker(
            client.base_url,
            client.timeout,
            guard_detail_timeout,
            domain,
            endpoints,
            probe_base_urls,
            probe_timeout,
            guard_cache,
            guard_cache_lock,
            request_limiter,
        )

    try:
        if workers == 1:
            for index, domain in enumerate(domains):
                try:
                    results[index] = audit_one(domain)
                    if retry_state:
                        retry_state.mark_success(operation_ids[index])
                except AuditError as error:
                    errors[index] = str(error)
                    if retry_state:
                        retry_state.mark_failure(operation_ids[index], str(error))
                    if progress:
                        progress(
                            f"Failed domain {domain.get('name', '<unknown>')}: {error}"
                        )
        else:
            if progress:
                progress(f"Queued {len(domains)} domains across {workers} workers")
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(audit_one, domain): index
                    for index, domain in enumerate(domains)
                }
                completed = 0
                for future in as_completed(futures):
                    index = futures[future]
                    domain_name = domains[index].get("name", "<unknown>")
                    try:
                        results[index] = future.result()
                        if retry_state:
                            retry_state.mark_success(operation_ids[index])
                    except AuditError as error:
                        errors[index] = str(error)
                        if retry_state:
                            retry_state.mark_failure(operation_ids[index], str(error))
                    except (
                        Exception
                    ) as error:  # pragma: no cover - defensive worker boundary
                        errors[index] = f"unexpected {type(error).__name__}"
                        if retry_state:
                            retry_state.mark_failure(
                                operation_ids[index], errors[index]
                            )
                    completed += 1
                    if progress:
                        state = "Completed" if index in results else "Failed"
                        progress(f"{state} [{completed}/{len(domains)}] {domain_name}")
    except KeyboardInterrupt:
        if retry_state:
            retry_state.mark_interrupted()
        raise
    finally:
        if probe_session is not None:
            probe_session.close()

    if retry_state:
        retry_state.finish()

    report["errors"].extend(
        f"{domain_name}: {error}" for domain_name, error in sorted(unresolved_errors)
    )
    if unresolved_errors:
        report["complete"] = False

    for index, domain in enumerate(domains):
        domain_name = domain.get("name", "<unknown>")
        if index in errors:
            report["complete"] = False
            report["errors"].append(f"{domain_name}: {errors[index]}")
            continue
        audited_domain = results[index]
        report["domains"].append(audited_domain)
        report["distributions"].extend(audited_domain["distributions"])
        if not audited_domain["has_content_guard"]:
            report["domains_without_content_guards"].append(
                {
                    "name": audited_domain["name"],
                    "pulp_href": audited_domain["pulp_href"],
                }
            )

    return report


def create_session(base_url: str) -> requests.Session:
    hostname, _ = _validate_base_url(base_url)
    try:
        credentials = netrc.netrc().authenticators(hostname)
    except (FileNotFoundError, netrc.NetrcParseError) as error:
        raise AuditError(f"Unable to read .netrc: {error}") from error
    if credentials is None:
        raise AuditError(f"No .netrc entry found for {hostname}")
    login, _, password = credentials
    session = requests.Session()
    session.auth = (login, password)
    session.verify = _ca_bundle()
    return session


def write_report(path: str, report: dict) -> None:
    _write_json_atomically(path, report)


def print_summary(report: dict) -> None:
    distributions = report["distributions"]
    unguarded = sum(
        item["pulp_classification"] == "pulp_unguarded" for item in distributions
    )
    guarded = sum(
        item["pulp_classification"] == "pulp_guarded" for item in distributions
    )
    print(f"Domains audited: {len(report['domains'])}")
    print(f"Distributions audited: {len(distributions)}")
    print(
        f"Domains without content guards: {len(report['domains_without_content_guards'])}"
    )
    print(f"Pulp-unguarded distributions: {unguarded}")
    print(f"Pulp-guarded distributions: {guarded}")
    print(f"Complete: {'yes' if report['complete'] else 'no'}")
    if report["errors"]:
        print("Errors:")
        for error in report["errors"]:
            print(f"  - {error}")


def print_progress(message: str) -> None:
    print(f"[audit] {message}", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--env", choices=ENVIRONMENTS, help="Target environment")
    target.add_argument("--base-url", help="Override the Pulp host")
    parser.add_argument("--output", required=True, help="Path for the JSON report")
    parser.add_argument(
        "--include-public",
        action="store_true",
        help="Include domains whose names start with public-",
    )
    parser.add_argument(
        "--probe-base-url",
        action="append",
        default=[],
        help="Optional unauthenticated content endpoint base URL; may be repeated",
    )
    parser.add_argument(
        "--probe-timeout",
        type=float,
        default=10.0,
        help="Timeout in seconds for optional endpoint probes",
    )
    parser.add_argument(
        "--guard-detail-timeout",
        type=float,
        default=DEFAULT_GUARD_DETAIL_TIMEOUT,
        help=(
            "Timeout in seconds for retrieving content-guard details "
            f"(default: {DEFAULT_GUARD_DETAIL_TIMEOUT:g})"
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages (the final summary is still printed)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        choices=range(1, 5),
        default=1,
        help="Concurrent domain workers (1-4; default: 1)",
    )
    parser.add_argument(
        "--request-rate",
        type=float,
        help="Maximum aggregate API request starts per second",
    )
    parser.add_argument(
        "--failed-operations-output",
        help="Write an atomic resumable manifest of failed domain operations",
    )
    parser.add_argument(
        "--retry-manifest",
        help="Retry remaining operations from a previous manifest",
    )
    args = parser.parse_args()

    if args.retry_manifest and not args.failed_operations_output:
        parser.error("--failed-operations-output is required with --retry-manifest")
    output_path = Path(args.output).expanduser().resolve()
    if args.failed_operations_output:
        failed_operations_path = (
            Path(args.failed_operations_output).expanduser().resolve()
        )
        if failed_operations_path == output_path:
            parser.error("--failed-operations-output must differ from --output")
    if args.retry_manifest:
        retry_manifest_path = Path(args.retry_manifest).expanduser().resolve()
        if retry_manifest_path in {output_path, failed_operations_path}:
            parser.error("Retry manifest input and outputs must be different files")

    base_url = (args.base_url or ENVIRONMENTS[args.env]).rstrip("/")
    progress = None if args.quiet else print_progress
    environment = args.env or "custom"
    request_rate = args.request_rate
    if request_rate is None:
        request_rate = 2.0 if args.env == "prod" else 4.0
    if request_rate <= 0:
        parser.error("--request-rate must be positive")
    if not math.isfinite(args.guard_detail_timeout) or args.guard_detail_timeout <= 0:
        parser.error("--guard-detail-timeout must be a positive finite number")
    session = None
    try:
        retry_manifest = None
        retry_manifest_sha256 = None
        if args.retry_manifest:
            retry_manifest, retry_manifest_sha256 = _load_retry_manifest(
                args.retry_manifest
            )
        session = create_session(base_url)
        client = PulpClient(
            base_url,
            session,
            progress=progress,
            request_limiter=RequestRateLimiter(request_rate),
            guard_detail_timeout=args.guard_detail_timeout,
        )
        report = build_report(
            client,
            environment,
            args.include_public,
            args.probe_base_url,
            args.probe_timeout,
            progress,
            args.workers,
            args.failed_operations_output,
            retry_manifest,
            retry_manifest_sha256,
            args.guard_detail_timeout,
        )
        write_report(args.output, report)
    except KeyboardInterrupt:
        print("Audit interrupted; retry manifest checkpoint preserved", file=sys.stderr)
        return 130
    except AuditError as error:
        print(f"Audit failed: {error}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()

    print_summary(report)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
