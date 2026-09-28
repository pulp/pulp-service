#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Apply the canonical X-RH-IDENTITY content guard to audited distributions.

The tool consumes one or more reports produced by audit-content-guards.py and
uses the hosted-pulp CLI for all Pulp API reads and mutations. It plans changes
by default; mutations require both ``--apply`` and ``--yes``.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlparse


GUARD_NAME = "hosted-pulp-default-identity-check"
HEADER_NAME = "x-rh-identity"
HEADER_VALUE = "identity-present"
JQ_FILTER = '"identity-present"'
VPN_GUARD_NAME = "hosted-pulp-default-vpn-check"
VPN_HEADER_NAME = "X-Pulp-VPN-Verified"
VPN_HEADER_VALUE = "true"
VPN_JQ_FILTER = None
COMPOSITE_GUARD_NAME = "hosted-pulp-default-identity-or-vpn"
PUBLIC_DOMAIN_PREFIX = "public-"
DEFAULT_ASSIGNMENT_TIMEOUT = 600.0
DEFAULT_POLL_MAX_INTERVAL = 5.0
READ_CONCURRENCY = 8

# These names come from the hosted-pulp OpenAPI operation path parameters, not
# necessarily from the distribution URL segments. Keep this mapping explicit
# so a plugin's URL naming cannot silently select the wrong PATCH operation.
DISTRIBUTION_COMMANDS = {
    ("container", "container"): (
        "container",
        "--container-container-distribution-href",
    ),
    ("container", "pull-through"): (
        "container-pull-through",
        "--container-container-pull-through-distribution-href",
    ),
    ("core", "openpgp"): ("core-openpgp", "--open-p-g-p-distribution-href"),
    ("file", "file"): ("file", "--file-file-distribution-href"),
    ("hugging-face", "hugging-face"): (
        "hugging-face",
        "--hugging-face-hugging-face-distribution-href",
    ),
    ("hugging_face", "hugging_face"): (
        "hugging-face",
        "--hugging-face-hugging-face-distribution-href",
    ),
    ("hugging_face", "hugging-face"): (
        "hugging-face",
        "--hugging-face-hugging-face-distribution-href",
    ),
    ("maven", "maven"): ("maven", "--maven-maven-distribution-href"),
    ("npm", "npm"): ("npm", "--npm-npm-distribution-href"),
    ("python", "pypi"): ("python-pypi", "--python-python-distribution-href"),
    ("rpm", "rpm"): ("rpm", "--rpm-rpm-distribution-href"),
}


class ToolError(RuntimeError):
    """Raised for invalid input or an unsuccessful hosted-pulp operation."""


def progress(message: str) -> None:
    """Write human-readable progress without contaminating JSON stdout."""
    print(f"progress: {message}", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Report:
    path: str
    sha256: str
    complete: bool
    errors: list[Any]
    distributions: list[dict[str, Any]]
    domains: list[dict[str, Any]]
    metadata: dict[str, Any]

    @property
    def failed_domains(self) -> set[str]:
        domains = set()
        for error in self.errors:
            if not isinstance(error, str) or ":" not in error:
                continue
            domain, _detail = error.split(":", 1)
            if domain.strip():
                domains.add(domain.strip())
        return domains


@dataclass(frozen=True)
class GuardSpec:
    name: str
    header_name: str
    header_value: str
    jq_filter: str | None


IDENTITY_GUARD = GuardSpec(GUARD_NAME, HEADER_NAME, HEADER_VALUE, JQ_FILTER)
VPN_GUARD = GuardSpec(
    VPN_GUARD_NAME,
    VPN_HEADER_NAME,
    VPN_HEADER_VALUE,
    VPN_JQ_FILTER,
)


class HostedPulp:
    """Small subprocess adapter for the hosted-pulp CLI."""

    def __init__(self, binary: str, profile: str):
        self.binary = binary
        self.profile = profile

    def run(self, domain: str, arguments: list[str], *, wait: bool = False) -> Any:
        command = [
            self.binary,
            "--profile",
            self.profile,
            "--domain",
            domain,
            "--output",
            "json",
        ]
        if wait:
            command.append("--wait")
        command.extend(arguments)

        completed = subprocess.run(  # noqa: S603 - binary and arguments are explicit
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = "\n".join(
                part.strip()
                for part in (completed.stderr, completed.stdout)
                if part.strip()
            )
            raise ToolError(
                f"hosted-pulp failed ({completed.returncode}): "
                f"{' '.join(command)}\n{detail}"
            )

        output = completed.stdout.strip()
        if not output or output == "ok":
            return {}
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise ToolError(
                f"hosted-pulp returned invalid JSON: {output[:500]}"
            ) from error


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write a state file atomically and keep it owner-readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_report(path: str) -> Report:
    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ToolError(f"unable to read report {path}: {error}") from error

    if not isinstance(payload, dict):
        raise ToolError(f"report {path} must contain a JSON object")

    distributions = payload.get("distributions")
    if not isinstance(distributions, list):
        raise ToolError(f"report {path} has no distributions list")

    for index, distribution in enumerate(distributions):
        if not isinstance(distribution, dict):
            raise ToolError(f"report {path} distribution {index} is not an object")
        for field in ("domain", "pulp_href", "pulp_classification"):
            if not isinstance(distribution.get(field), str) or not distribution[field]:
                raise ToolError(f"report {path} distribution {index} has no {field}")

    domains = payload.get("domains", [])
    if not isinstance(domains, list):
        raise ToolError(f"report {path} has no domains list")
    for index, domain in enumerate(domains):
        if not isinstance(domain, dict):
            raise ToolError(f"report {path} domain {index} is not an object")
        for field in ("name", "pulp_href"):
            if not isinstance(domain.get(field), str) or not domain[field]:
                raise ToolError(f"report {path} domain {index} has no {field}")

    errors = payload.get("errors", [])
    if not isinstance(errors, list):
        raise ToolError(f"report {path} errors must be a list")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ToolError(f"report {path} metadata must be an object")
    if payload.get("complete") is True and errors:
        raise ToolError(f"report {path} is marked complete but contains errors")

    return Report(
        path=path,
        sha256=file_sha256(report_path),
        complete=payload.get("complete") is True,
        errors=errors,
        distributions=distributions,
        domains=domains,
        metadata=metadata,
    )


def report_scope(report: Report) -> tuple[Any, ...]:
    required = (
        "environment",
        "base_url",
        "public_domains_included",
        "distribution_endpoints",
    )
    missing = [field for field in required if field not in report.metadata]
    if missing:
        raise ToolError(
            f"report {report.path} is missing scope metadata: {', '.join(missing)}"
        )
    return (
        report.metadata["environment"],
        report.metadata["base_url"],
        report.metadata["public_domains_included"],
        tuple(report.metadata["distribution_endpoints"]),
    )


def strict_failed_domains(report: Report) -> set[str]:
    domains = set()
    for error in report.errors:
        if not isinstance(error, str) or ":" not in error:
            raise ToolError(
                f"report {report.path} has an unscoped or invalid audit error: {error!r}"
            )
        domain, detail = error.split(":", 1)
        if not domain.strip() or not detail.strip():
            raise ToolError(
                f"report {report.path} has an invalid audit error: {error!r}"
            )
        path_domains = set(re.findall(r"/api/pulp/([^/]+)/", detail))
        if path_domains and path_domains != {domain.strip()}:
            raise ToolError(
                f"report {report.path} has an error domain mismatch: {error!r}"
            )
        domains.add(domain.strip())
    return domains


def validate_partial_apply_reports(reports: list[Report]) -> set[str]:
    incomplete = [report for report in reports if not report.complete or report.errors]
    if not incomplete:
        raise ToolError("--partial-apply requires at least one incomplete report")

    scopes = {report_scope(report) for report in reports}
    if len(scopes) != 1:
        raise ToolError(
            "all reports used with --partial-apply must have matching audit scope"
        )

    unresolved = set()
    for report in incomplete:
        unresolved.update(strict_failed_domains(report))
    if not unresolved:
        raise ToolError("--partial-apply requires domain-scoped audit errors")
    return unresolved


def merge_reports(
    reports: list[Report], allow_incomplete: bool, require_coverage: bool = False
) -> list[dict[str, Any]]:
    if not reports:
        raise ToolError("at least one --report is required")

    incomplete = [report for report in reports if not report.complete or report.errors]
    if incomplete and not allow_incomplete:
        paths = ", ".join(report.path for report in incomplete)
        raise ToolError(
            f"incomplete or errored reports: {paths}; "
            "pass --allow-incomplete-report only after supplying the missing-domain report"
        )
    if incomplete:
        if require_coverage:
            healthy = [
                report for report in reports if report.complete and not report.errors
            ]
            if not healthy:
                raise ToolError(
                    "apply with incomplete reports requires a complete, error-free supplemental report"
                )
            healthy_scopes = {report_scope(report) for report in healthy}
            for report in incomplete:
                if report_scope(report) not in healthy_scopes:
                    raise ToolError(
                        f"report {report.path} has incompatible audit scope for apply"
                    )
                failed_domains = strict_failed_domains(report)
                covered_domains = {
                    distribution["domain"]
                    for other in healthy
                    if other is not report
                    for distribution in other.distributions
                }
                uncovered = failed_domains - covered_domains
                if uncovered:
                    raise ToolError(
                        f"report {report.path} has uncovered failed domains: "
                        f"{', '.join(sorted(uncovered))}"
                    )
        for report in incomplete:
            failed_domains = report.failed_domains
            supplemental_domains = {
                distribution["domain"]
                for other in reports
                if other is not report
                for distribution in other.distributions
            }
            uncovered = failed_domains - supplemental_domains
            if not failed_domains or uncovered:
                missing = ", ".join(sorted(uncovered or failed_domains))
                print(
                    f"warning: report {report.path} has failed domains without "
                    f"supplemental coverage: {missing or '<unparseable>'}",
                    file=sys.stderr,
                )
        for report in incomplete:
            print(
                f"warning: accepting incomplete report {report.path} with "
                f"{len(report.errors)} recorded errors",
                file=sys.stderr,
            )

    merged: dict[str, dict[str, Any]] = {}
    for report in reports:
        for distribution in report.distributions:
            href = distribution["pulp_href"]
            existing = merged.get(href)
            if existing is None:
                merged[href] = distribution
                continue

            comparable_fields = (
                "domain",
                "effective_content_guard",
                "explicit_content_guard",
                "pulp_classification",
            )
            if any(
                existing.get(field) != distribution.get(field)
                for field in comparable_fields
            ):
                raise ToolError(f"conflicting records for distribution {href}")

    return list(merged.values())


def merge_domains(reports: list[Report]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    for report in reports:
        for domain in report.domains:
            href = domain["pulp_href"]
            name = domain["name"]
            if name in names and names[name] != href:
                raise ToolError(f"domain name maps to multiple hrefs: {name}")
            names[name] = href
            existing = merged.get(href)
            if existing is not None and existing.get("name") != name:
                raise ToolError(f"conflicting records for domain {href}")
            merged[href] = domain
    if not merged:
        raise ToolError("reports contain no domain inventory")
    return list(merged.values())


def guard_href(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        href = value.get("pulp_href")
        return href if isinstance(href, str) and href else None
    return None


def select_candidates(
    distributions: list[dict[str, Any]], domains: set[str] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = []
    excluded = []
    for distribution in distributions:
        domain = distribution["domain"]
        reason = None
        if domains and domain not in domains:
            reason = "outside_domain_filter"
        elif domain.startswith(PUBLIC_DOMAIN_PREFIX):
            reason = "public_domain"
        elif distribution["pulp_classification"] != "pulp_unguarded":
            reason = "not_pulp_unguarded"
        elif guard_href(distribution.get("effective_content_guard")):
            reason = "effective_guard_present"
        elif guard_href(distribution.get("explicit_content_guard")):
            reason = "explicit_guard_present"

        if reason:
            excluded.append({"distribution": distribution, "reason": reason})
        else:
            candidates.append(distribution)
    return candidates, excluded


def distribution_descriptor(href: str) -> tuple[str, str]:
    """Return the generated command group and path-parameter flag for a href."""
    parts = [part for part in urlparse(href).path.rstrip("/").split("/") if part]
    try:
        index = parts.index("distributions")
    except ValueError as error:
        raise ToolError(f"not a distribution href: {href}") from error

    if len(parts) <= index + 3:
        raise ToolError(f"distribution href has no typed resource: {href}")

    plugin, resource = parts[index + 1 : index + 3]
    try:
        return DISTRIBUTION_COMMANDS[(plugin, resource)]
    except KeyError as error:
        raise ToolError(
            f"unsupported distribution type {plugin}/{resource} in href {href}"
        ) from error


def same_href(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    left_path = urlparse(left).path.rstrip("/")
    right_path = urlparse(right).path.rstrip("/")
    return bool(left_path and left_path == right_path)


def preflight_candidates(distributions: list[dict[str, Any]]) -> None:
    for distribution in distributions:
        href = distribution["pulp_href"]
        distribution_descriptor(href)
        domain = distribution["domain"]
        parts = [part for part in urlparse(href).path.rstrip("/").split("/") if part]
        try:
            pulp_index = parts.index("pulp")
            href_domain = parts[pulp_index + 1]
        except (ValueError, IndexError) as error:
            raise ToolError(f"distribution href has no Pulp domain: {href}") from error
        if href_domain != domain:
            raise ToolError(
                f"distribution {href} domain mismatch: report={domain} href={href_domain}"
            )
        if (
            not isinstance(distribution.get("domain_href"), str)
            or not distribution["domain_href"]
        ):
            raise ToolError(f"distribution {href} has no domain_href")


def distribution_command(href: str, action: str, *body: str) -> list[str]:
    command_group_name, path_flag = distribution_descriptor(href)
    return [
        "api",
        "distributions",
        command_group_name,
        action,
        path_flag,
        href,
        *body,
    ]


def command_group(*segments: str) -> str:
    """Match hosted-pulp's noun-group flattening for generated commands."""
    parts = "-".join(segments).split("-")
    flattened = [parts[0]]
    for part in parts[1:]:
        if part != flattened[-1]:
            flattened.append(part)
    return "-".join(flattened)


def guard_list_command(spec: GuardSpec) -> list[str]:
    return [
        "api",
        "contentguards",
        "core-header",
        "list",
        "--name",
        spec.name,
        "--limit",
        "100",
    ]


def guard_create_command(spec: GuardSpec) -> list[str]:
    command = [
        "api",
        "contentguards",
        "core-header",
        "create",
        "--name",
        spec.name,
        "--header-name",
        spec.header_name,
    ]
    if spec.header_value == "":
        # pflag treats a following flag as the value when the empty argument
        # is passed separately. Use --header-value= to preserve an intentional
        # empty string for the identity presence-check guard.
        command.append("--header-value=")
    else:
        command.extend(["--header-value", spec.header_value])
    if spec.jq_filter is not None:
        command.extend(["--jq-filter", spec.jq_filter])
    return command


def composite_list_command() -> list[str]:
    return [
        "api",
        "contentguards",
        "core-composite",
        "list",
        "--name",
        COMPOSITE_GUARD_NAME,
        "--limit",
        "100",
    ]


def composite_create_command(identity_href: str, vpn_href: str) -> list[str]:
    return [
        "api",
        "contentguards",
        "core-composite",
        "create",
        "--name",
        COMPOSITE_GUARD_NAME,
        "--guards",
        identity_href,
        "--guards",
        vpn_href,
    ]


def compatible_guard(guard: dict[str, Any], spec: GuardSpec = IDENTITY_GUARD) -> bool:
    return (
        guard.get("name") == spec.name
        and str(guard.get("header_name", "")).lower() == spec.header_name.lower()
        and guard.get("header_value") == spec.header_value
        and guard.get("jq_filter") == spec.jq_filter
    )


def get_guard_href(result: Any) -> str | None:
    if isinstance(result, dict):
        href = result.get("pulp_href")
        if isinstance(href, str):
            return href
        results = result.get("results")
        if (
            isinstance(results, list)
            and len(results) == 1
            and isinstance(results[0], dict)
        ):
            href = results[0].get("pulp_href")
            if isinstance(href, str):
                return href
    return None


def ensure_guard(
    client: HostedPulp,
    domain: str,
    spec: GuardSpec,
    apply: bool,
    domain_results: dict[str, Any],
) -> str | None:
    result = client.run(domain, guard_list_command(spec))
    guards = result.get("results", []) if isinstance(result, dict) else []
    if not isinstance(guards, list):
        raise ToolError(f"header guard list for {domain} has an invalid results field")

    named_guards = [
        guard
        for guard in guards
        if isinstance(guard, dict) and guard.get("name") == spec.name
    ]
    if len(named_guards) > 1:
        raise ToolError(f"multiple {spec.name} guards found in domain {domain}")

    if named_guards:
        guard = named_guards[0]
        if not compatible_guard(guard, spec):
            raise ToolError(
                f"existing {spec.name} guard in {domain} has incompatible settings"
            )
        href = guard_href(guard.get("pulp_href"))
        if not href:
            raise ToolError(f"existing {spec.name} guard in {domain} has no pulp_href")
        domain_results.setdefault("guards", {})[spec.name] = {
            "action": "reused",
            "href": href,
            "settings": {
                "header_name": spec.header_name,
                "header_value": spec.header_value,
                "jq_filter": spec.jq_filter,
            },
        }
        return href

    if not apply:
        domain_results.setdefault("guards", {})[spec.name] = {
            "action": "create",
            "settings": {
                "header_name": spec.header_name,
                "header_value": spec.header_value,
                "jq_filter": spec.jq_filter,
            },
        }
        return None

    created = client.run(domain, guard_create_command(spec), wait=True)
    href = get_guard_href(created)
    if not href:
        raise ToolError(f"creating {spec.name} in {domain} returned no pulp_href")
    domain_results.setdefault("guards", {})[spec.name] = {
        "action": "created",
        "href": href,
        "settings": {
            "header_name": spec.header_name,
            "header_value": spec.header_value,
            "jq_filter": spec.jq_filter,
        },
    }
    return href


def composite_members(result: dict[str, Any]) -> list[str]:
    guards = result.get("guards")
    if not isinstance(guards, list):
        raise ToolError(f"{COMPOSITE_GUARD_NAME} has no valid guards list")
    members = [guard_href(guard) for guard in guards]
    if any(member is None for member in members):
        raise ToolError(f"{COMPOSITE_GUARD_NAME} has an invalid guard member")
    return [member for member in members if member is not None]


def compatible_composite(
    guard: dict[str, Any], identity_href: str, vpn_href: str
) -> bool:
    members = composite_members(guard)
    return (
        len(members) == 2
        and len(set(members)) == 2
        and set(members)
        == {
            identity_href,
            vpn_href,
        }
    )


def ensure_composite_guard(
    client: HostedPulp,
    domain: str,
    identity_href: str | None,
    vpn_href: str | None,
    apply: bool,
    domain_results: dict[str, Any],
) -> str | None:
    if not identity_href or not vpn_href:
        domain_results.setdefault("guards", {})[COMPOSITE_GUARD_NAME] = {
            "action": "create_after_header_guards",
            "settings": {"members": [IDENTITY_GUARD.name, VPN_GUARD.name]},
        }
        return None

    result = client.run(domain, composite_list_command())
    guards = result.get("results", []) if isinstance(result, dict) else []
    if not isinstance(guards, list):
        raise ToolError(
            f"composite guard list for {domain} has an invalid results field"
        )
    named_guards = [
        guard
        for guard in guards
        if isinstance(guard, dict) and guard.get("name") == COMPOSITE_GUARD_NAME
    ]
    if len(named_guards) > 1:
        raise ToolError(
            f"multiple {COMPOSITE_GUARD_NAME} guards found in domain {domain}"
        )

    if named_guards:
        guard = named_guards[0]
        members = composite_members(guard)
        if not compatible_composite(guard, identity_href, vpn_href):
            raise ToolError(
                f"existing {COMPOSITE_GUARD_NAME} guard in {domain} has incompatible members"
            )
        href = guard_href(guard.get("pulp_href"))
        if not href:
            raise ToolError(f"existing {COMPOSITE_GUARD_NAME} guard has no pulp_href")
        domain_results.setdefault("guards", {})[COMPOSITE_GUARD_NAME] = {
            "action": "reused",
            "href": href,
            "members": members,
        }
        return href

    if not apply:
        domain_results.setdefault("guards", {})[COMPOSITE_GUARD_NAME] = {
            "action": "create",
            "members": [identity_href, vpn_href],
        }
        return None

    created = client.run(
        domain,
        composite_create_command(identity_href, vpn_href),
        wait=True,
    )
    href = get_guard_href(created)
    if not href:
        raise ToolError(f"creating {COMPOSITE_GUARD_NAME} returned no pulp_href")
    domain_results.setdefault("guards", {})[COMPOSITE_GUARD_NAME] = {
        "action": "created",
        "href": href,
        "members": [identity_href, vpn_href],
    }
    return href


def ensure_domain_default(
    client: HostedPulp,
    domain: str,
    domain_data: dict[str, Any],
    identity_href: str | None,
    apply: bool,
    domain_results: dict[str, Any],
    assignment_timeout: float,
    poll_max_interval: float,
) -> dict[str, Any]:
    domain_href = domain_data["pulp_href"]
    current_default = guard_href(domain_data.get("default_content_guard"))
    default_state = domain_results.setdefault("default_content_guard", {})
    default_state.update(
        {
            "prior": current_default,
            "assigned": identity_href,
            "distribution_assignment_target": identity_href,
        }
    )

    if current_default:
        if not identity_href or current_default != identity_href:
            raise ToolError(
                f"domain {domain} has conflicting default_content_guard {current_default}"
            )
        default_state["action"] = "reused"
        return domain_data

    if not identity_href:
        default_state["action"] = "create_after_identity_guard"
        return domain_data

    if not apply:
        default_state["action"] = "set"
        return domain_data

    dispatch_result = client.run(
        "default",
        [
            "api",
            "domains",
            "partial-update",
            "--domain-href",
            domain_href,
            "--default-content-guard",
            identity_href,
        ],
        wait=False,
    )
    default_state.update(
        {
            "action": "dispatched_pending",
            "dispatch_result": dispatch_result,
            "task_href": (
                dispatch_result.get("task")
                if isinstance(dispatch_result, dict)
                else None
            ),
        }
    )
    updated = wait_for_domain_default(
        client,
        domain_href,
        domain,
        identity_href,
        assignment_timeout,
        poll_max_interval,
    )
    default_state["action"] = "set"
    return updated


def live_distribution(
    client: HostedPulp, distribution: dict[str, Any]
) -> dict[str, Any]:
    result = client.run(
        distribution["domain"],
        distribution_command(distribution["pulp_href"], "read"),
    )
    if not isinstance(result, dict):
        raise ToolError(
            f"distribution read returned invalid data for {distribution['pulp_href']}"
        )
    if not same_href(result.get("pulp_href"), distribution["pulp_href"]):
        raise ToolError(
            f"distribution read returned the wrong resource for {distribution['pulp_href']}"
        )
    return result


def wait_for_distribution_guard(
    client: HostedPulp,
    distribution: dict[str, Any],
    expected_guard: str,
    timeout: float,
    max_interval: float,
) -> tuple[dict[str, Any], int, float]:
    started = time.monotonic()
    deadline = started + timeout
    interval = 1.0
    attempts = 0

    while True:
        attempts += 1
        live = live_distribution(client, distribution)
        current_guard = guard_href(live.get("content_guard"))
        if current_guard == expected_guard:
            return live, attempts, time.monotonic() - started
        if current_guard:
            raise ToolError(
                f"distribution {distribution['pulp_href']} received unexpected guard "
                f"{current_guard}; expected {expected_guard}"
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError(
                f"timed out waiting for guard {expected_guard} on "
                f"{distribution['pulp_href']}"
            )
        progress(
            f"waiting for distribution {distribution['pulp_href']} to receive "
            f"guard (attempt {attempts})"
        )
        time.sleep(min(interval, max_interval, remaining))
        interval = min(interval * 2, max_interval)


def live_domain(client: HostedPulp, distribution: dict[str, Any]) -> dict[str, Any]:
    domain_href = distribution.get("domain_href")
    if not isinstance(domain_href, str) or not domain_href:
        raise ToolError(
            f"distribution {distribution['pulp_href']} has no domain_href for live validation"
        )
    return live_domain_href(client, domain_href, distribution["domain"])


def live_domain_href(
    client: HostedPulp, domain_href: str, domain_name: str
) -> dict[str, Any]:
    result = client.run(
        "default",
        ["api", "domains", "read", "--domain-href", domain_href],
    )
    if not isinstance(result, dict):
        raise ToolError(f"domain read returned invalid data for {domain_href}")
    if result.get("name") != domain_name:
        raise ToolError(
            f"domain {domain_href} changed names: "
            f"report={domain_name} live={result.get('name')}"
        )
    if not same_href(result.get("pulp_href"), domain_href):
        raise ToolError(f"domain read returned the wrong resource for {domain_href}")
    return result


def wait_for_domain_default(
    client: HostedPulp,
    domain_href: str,
    domain_name: str,
    expected_guard: str,
    timeout: float,
    max_interval: float,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout
    interval = 1.0
    while True:
        live = live_domain_href(client, domain_href, domain_name)
        if guard_href(live.get("default_content_guard")) == expected_guard:
            return live
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError(
                f"timed out waiting for domain {domain_name} default_content_guard "
                f"to become {expected_guard}"
            )
        progress(
            f"waiting for domain {domain_name} default_content_guard "
            f"(next check in {min(interval, max_interval):.1f}s)"
        )
        time.sleep(min(interval, max_interval, remaining))
        interval = min(interval * 2, max_interval)


def validate_live_distribution(
    distribution: dict[str, Any], domain: dict[str, Any], live: dict[str, Any]
) -> None:
    if not same_href(domain.get("pulp_href"), distribution.get("domain_href")):
        raise ToolError(
            f"distribution {distribution['pulp_href']} has a stale domain_href"
        )
    report_repository = guard_href(distribution.get("repository"))
    live_repository = guard_href(live.get("repository"))
    if report_repository and live_repository and report_repository != live_repository:
        raise ToolError(
            f"distribution {distribution['pulp_href']} changed repositories: "
            f"report={report_repository} live={live_repository}"
        )

    report_base_path = distribution.get("base_path")
    if (
        report_base_path
        and live.get("base_path")
        and report_base_path != live["base_path"]
    ):
        raise ToolError(
            f"distribution {distribution['pulp_href']} changed base_path: "
            f"report={report_base_path} live={live['base_path']}"
        )


def validate_live_candidate(
    client: HostedPulp, distribution: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    domain = live_domain(client, distribution)
    live = live_distribution(client, distribution)
    validate_live_distribution(distribution, domain, live)

    return domain, live


def write_state(output: Path, rollback: Path, state: dict[str, Any]) -> None:
    atomic_write_json(output, state)
    atomic_write_json(rollback, state["rollback"])


def reconcile_domain_defaults(
    args: argparse.Namespace,
    reports: list[Report],
    output: Path,
    rollback: Path,
) -> int:
    unresolved_audit_domains = set()
    if args.partial_apply:
        unresolved_audit_domains = validate_partial_apply_reports(reports)
    elif any(not report.complete or report.errors for report in reports):
        raise ToolError(
            "--domain-defaults-only requires complete, error-free audit reports"
        )
    scopes = {report_scope(report) for report in reports}
    if len(scopes) != 1:
        raise ToolError("all domain-default reports must have matching audit scope")

    domains = merge_domains(reports)
    selected = []
    excluded = []
    requested_domains = set(args.domain) if args.domain else None
    for domain in domains:
        name = domain["name"]
        reason = None
        if requested_domains and name not in requested_domains:
            reason = "outside_domain_filter"
        elif name in unresolved_audit_domains:
            reason = "unresolved_audit_domain"
        elif name.lower().startswith("public-"):
            reason = "public_domain"
        elif name == "default":
            reason = "system_default_domain"
        if reason:
            excluded.append({"domain": domain, "reason": reason})
        else:
            selected.append(domain)

    client = HostedPulp(args.hosted_pulp_bin, args.profile)
    state: dict[str, Any] = {
        "profile": args.profile,
        "mode": "domain-defaults-partial-apply"
        if args.partial_apply
        else "domain-defaults-only",
        "completion": "partial_pending_audit" if args.partial_apply else "in_progress",
        "reports": [
            {
                "path": report.path,
                "sha256": report.sha256,
                "complete": report.complete,
                "error_count": len(report.errors),
            }
            for report in reports
        ],
        "guards": {
            IDENTITY_GUARD.name: {
                "header_name": IDENTITY_GUARD.header_name,
                "header_value": IDENTITY_GUARD.header_value,
                "jq_filter": IDENTITY_GUARD.jq_filter,
            },
            VPN_GUARD.name: {
                "header_name": VPN_GUARD.header_name,
                "header_value": VPN_GUARD.header_value,
                "jq_filter": VPN_GUARD.jq_filter,
            },
            COMPOSITE_GUARD_NAME: {
                "members": [IDENTITY_GUARD.name, VPN_GUARD.name],
                "assigned_to_distributions": False,
            },
        },
        "counts": {
            "input_domains": len(domains),
            "selected_domains": len(selected),
            "excluded_domains": len(excluded),
            "audit_deferred": sum(
                item["reason"] == "unresolved_audit_domain" for item in excluded
            ),
            "unresolved_audit_domains": len(unresolved_audit_domains),
            "changed": 0,
            "skipped": 0,
            "failed": 0,
        },
        "excluded_domains": excluded,
        "unresolved_audit_domains": sorted(unresolved_audit_domains),
        "unresolved_audit_errors": [
            error
            for report in reports
            if not report.complete or report.errors
            for error in report.errors
        ],
        "domains": {},
        "rollback": [],
        "errors": [],
    }
    write_state(output, rollback, state)

    for index, domain in enumerate(selected, start=1):
        name = domain["name"]
        domain_state: dict[str, Any] = {}
        state["domains"][name] = domain_state
        progress(f"domain defaults {index}/{len(selected)}: {name}")
        try:
            domain_data = live_domain_href(client, domain["pulp_href"], name)
            identity = ensure_guard(
                client, name, IDENTITY_GUARD, args.apply, domain_state
            )
            vpn = ensure_guard(client, name, VPN_GUARD, args.apply, domain_state)
            ensure_composite_guard(
                client, name, identity, vpn, args.apply, domain_state
            )
            domain_state["default_content_guard"] = {
                "prior": guard_href(domain_data.get("default_content_guard")),
                "assigned": identity,
            }
            write_state(output, rollback, state)

            prior_default = guard_href(domain_data.get("default_content_guard"))
            if args.apply and identity and prior_default != identity:
                state["rollback"].append(
                    {
                        "domain": name,
                        "domain_href": domain["pulp_href"],
                        "prior_default_content_guard": prior_default,
                        "assigned_default_content_guard": identity,
                        "state": "pending",
                    }
                )
                write_state(output, rollback, state)
            updated_domain = ensure_domain_default(
                client,
                name,
                domain_data,
                identity,
                args.apply,
                domain_state,
                args.assignment_timeout,
                args.poll_max_interval,
            )
            resulting_default = guard_href(updated_domain.get("default_content_guard"))
            if args.apply and state["rollback"]:
                entry = state["rollback"][-1]
                if entry["domain"] == name and entry["state"] == "pending":
                    entry["state"] = "changed"
                    entry["after_default_content_guard"] = resulting_default
            if domain_state.get("default_content_guard", {}).get("action") in {
                "set",
                "reused",
            }:
                state["counts"]["changed"] += 1
            else:
                state["counts"]["skipped"] += 1
            write_state(output, rollback, state)
            progress(f"domain defaults {index}/{len(selected)}: {name} complete")
        except ToolError as error:
            domain_state["error"] = str(error)
            state["errors"].append({"domain": name, "error": str(error)})
            state["counts"]["failed"] += 1
            write_state(output, rollback, state)
            progress(f"domain defaults {name} failed: {error}")

    if state["counts"]["failed"]:
        state["completion"] = "failed"
    elif args.partial_apply:
        state["completion"] = "partial_pending_audit"
    elif args.apply:
        state["completion"] = "complete"
    else:
        state["completion"] = "plan"
    write_state(output, rollback, state)
    print(json.dumps({"output": str(output), "counts": state["counts"]}, indent=2))
    if state["counts"]["failed"]:
        return 1
    return 3 if args.partial_apply else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="append",
        required=True,
        help="audit report JSON (repeatable)",
    )
    parser.add_argument(
        "--profile",
        default="stage-tbr",
        help="hosted-pulp profile (default: stage-tbr)",
    )
    parser.add_argument(
        "--hosted-pulp-bin", default="hosted-pulp", help="hosted-pulp executable"
    )
    parser.add_argument(
        "--domain", action="append", help="limit processing to this domain (repeatable)"
    )
    parser.add_argument(
        "--max-changes",
        type=int,
        default=0,
        help="maximum distributions to change (0 means unlimited)",
    )
    parser.add_argument(
        "--assignment-timeout",
        type=float,
        default=DEFAULT_ASSIGNMENT_TIMEOUT,
        help="seconds to wait for each distribution guard assignment",
    )
    parser.add_argument(
        "--poll-max-interval",
        type=float,
        default=DEFAULT_POLL_MAX_INTERVAL,
        help="maximum seconds between assignment polls",
    )
    parser.add_argument(
        "--output",
        default="identity-content-guard-application.json",
        help="result state file",
    )
    parser.add_argument("--rollback-output", help="rollback manifest path")
    parser.add_argument(
        "--allow-incomplete-report",
        action="store_true",
        help="accept reports marked incomplete or containing errors",
    )
    parser.add_argument(
        "--apply", action="store_true", help="create guards and patch distributions"
    )
    parser.add_argument(
        "--partial-apply",
        action="store_true",
        help="apply only successfully audited candidates and leave failed domains pending",
    )
    parser.add_argument(
        "--domain-defaults-only",
        action="store_true",
        help="reconcile domain defaults without reading or changing distributions",
    )
    parser.add_argument(
        "--yes", action="store_true", help="confirm the requested mutation"
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.max_changes < 0:
        parser.error("--max-changes must be zero or greater")
    if args.assignment_timeout <= 0:
        parser.error("--assignment-timeout must be positive")
    if args.poll_max_interval <= 0:
        parser.error("--poll-max-interval must be positive")
    if args.apply and not args.yes:
        parser.error("--apply requires --yes")
    if args.partial_apply and not args.apply:
        parser.error("--partial-apply requires --apply --yes")
    if args.domain_defaults_only and args.max_changes:
        parser.error("--max-changes cannot be used with --domain-defaults-only")

    output = Path(args.output)
    rollback = Path(args.rollback_output or f"{args.output}.rollback.json")

    try:
        reports = [load_report(path) for path in args.report]
        if args.domain_defaults_only:
            return reconcile_domain_defaults(args, reports, output, rollback)
        unresolved_audit_domains = set()
        if args.partial_apply:
            unresolved_audit_domains = validate_partial_apply_reports(reports)
        distributions = merge_reports(
            reports,
            args.allow_incomplete_report or args.partial_apply,
            require_coverage=args.apply and not args.partial_apply,
        )
        all_candidates, excluded = select_candidates(
            distributions,
            set(args.domain) if args.domain else None,
        )
        candidates = all_candidates
        audit_deferred = []
        if args.partial_apply:
            audit_deferred = [
                {"distribution": candidate, "reason": "unresolved_audit_domain"}
                for candidate in candidates
                if candidate["domain"] in unresolved_audit_domains
            ]
            candidates = [
                candidate
                for candidate in candidates
                if candidate["domain"] not in unresolved_audit_domains
            ]
        candidates_before_limit = len(candidates)
        if args.max_changes:
            candidates = candidates[: args.max_changes]
        deferred_candidates = (
            len(audit_deferred) + candidates_before_limit - len(candidates)
        )
        progress(
            f"loaded {len(distributions)} distributions; "
            f"selected {len(candidates)} candidates; excluded {len(excluded)}; "
            f"deferred {deferred_candidates}"
        )

        client = HostedPulp(args.hosted_pulp_bin, args.profile)
        by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for candidate in candidates:
            by_domain[candidate["domain"]].append(candidate)

        state: dict[str, Any] = {
            "profile": args.profile,
            "reports": [
                {
                    "path": report.path,
                    "sha256": report.sha256,
                    "complete": report.complete,
                    "error_count": len(report.errors),
                }
                for report in reports
            ],
            "mode": "partial-apply"
            if args.partial_apply
            else ("apply" if args.apply else "plan"),
            "completion": "partial_pending_audit"
            if args.partial_apply
            else "in_progress",
            "guards": {
                IDENTITY_GUARD.name: {
                    "header_name": IDENTITY_GUARD.header_name,
                    "header_value": IDENTITY_GUARD.header_value,
                    "jq_filter": IDENTITY_GUARD.jq_filter,
                },
                VPN_GUARD.name: {
                    "header_name": VPN_GUARD.header_name,
                    "header_value": VPN_GUARD.header_value,
                    "jq_filter": VPN_GUARD.jq_filter,
                },
                COMPOSITE_GUARD_NAME: {
                    "members": [IDENTITY_GUARD.name, VPN_GUARD.name],
                    "assigned_to_distributions": False,
                },
            },
            "counts": {
                "input_distributions": len(distributions),
                "audited_candidates": len(all_candidates),
                "candidates": len(candidates),
                "excluded": len(excluded),
                "audit_deferred": len(audit_deferred),
                "deferred": deferred_candidates,
                "unresolved_audit_domains": len(unresolved_audit_domains),
                "changed": 0,
                "skipped": 0,
                "failed": 0,
            },
            "excluded": excluded,
            "audit_deferred": audit_deferred,
            "domains": {},
            "changes": [],
            "rollback": [],
            "errors": [],
            "unresolved_audit_domains": sorted(unresolved_audit_domains),
            "unresolved_audit_errors": [
                error
                for report in reports
                if not report.complete or report.errors
                for error in report.errors
            ],
        }
        write_state(output, rollback, state)

        try:
            preflight_candidates(candidates)
            progress(f"static preflight passed for {len(candidates)} candidates")
        except ToolError as error:
            progress(f"static preflight failed: {error}")
            state["errors"].append({"phase": "preflight", "error": str(error)})
            state["counts"]["failed"] += 1
            write_state(output, rollback, state)
            print(
                json.dumps({"output": str(output), "counts": state["counts"]}, indent=2)
            )
            return 1

        total_domains = len(by_domain)
        for domain_index, domain in enumerate(sorted(by_domain), start=1):
            domain_state: dict[str, Any] = {
                "distribution_count": len(by_domain[domain])
            }
            state["domains"][domain] = domain_state
            progress(
                f"domain {domain_index}/{total_domains}: {domain} "
                f"({len(by_domain[domain])} candidates)"
            )

            # Validate every candidate before creating a domain guard. A bad
            # supplemental report must not leave an orphan guard behind.
            validation_errors = []
            validated: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
            total_distributions = len(by_domain[domain])
            domain_error = None
            try:
                domain_data = live_domain(client, by_domain[domain][0])
            except ToolError as error:
                domain_data = None
                domain_error = str(error)

            if domain_data is None:
                validation_errors = [
                    {
                        "distribution_href": item["pulp_href"],
                        "error": domain_error,
                    }
                    for item in by_domain[domain]
                ]

            else:

                def preflight_one(item_index, item):
                    progress(
                        f"domain {domain}: live preflight "
                        f"{item_index}/{total_distributions}"
                    )
                    live_result = live_distribution(client, item)
                    validate_live_distribution(item, domain_data, live_result)
                    return item_index, item, live_result

                with ThreadPoolExecutor(
                    max_workers=min(READ_CONCURRENCY, total_distributions)
                ) as executor:
                    futures = {
                        executor.submit(preflight_one, item_index, item): item
                        for item_index, item in enumerate(by_domain[domain], start=1)
                    }
                    for future in as_completed(futures):
                        item = futures[future]
                        try:
                            item_index, item, live = future.result()
                            validated[item["pulp_href"]] = (domain_data, live)
                            progress(
                                f"domain {domain}: live preflight "
                                f"{item_index}/{total_distributions} passed"
                            )
                        except ToolError as error:
                            validation_errors.append(
                                {
                                    "distribution_href": item["pulp_href"],
                                    "error": str(error),
                                }
                            )
            if validation_errors:
                progress(
                    f"domain {domain}: live preflight failed for "
                    f"{len(validation_errors)} candidate(s)"
                )
                domain_state["validation_errors"] = validation_errors
                state["errors"].extend(
                    {"domain": domain, **error} for error in validation_errors
                )
                state["counts"]["failed"] += len(validation_errors)
                for error in validation_errors:
                    state["changes"].append(
                        {
                            "distribution_href": error["distribution_href"],
                            "domain": domain,
                            "state": "failed_preflight",
                            "error": error["error"],
                        }
                    )
                write_state(output, rollback, state)
                continue

            progress(f"domain {domain}: live preflight passed")

            try:
                guard = ensure_guard(
                    client, domain, IDENTITY_GUARD, args.apply, domain_state
                )
                vpn_guard = ensure_guard(
                    client, domain, VPN_GUARD, args.apply, domain_state
                )
                ensure_composite_guard(
                    client,
                    domain,
                    guard,
                    vpn_guard,
                    args.apply,
                    domain_state,
                )
                domain_state["default_content_guard"] = {
                    "prior": guard_href(domain_data.get("default_content_guard")),
                    "assigned": guard,
                    "distribution_assignment_target": guard,
                }
                write_state(output, rollback, state)
                domain_data = ensure_domain_default(
                    client,
                    domain,
                    domain_data,
                    guard,
                    args.apply,
                    domain_state,
                    args.assignment_timeout,
                    args.poll_max_interval,
                )
                write_state(output, rollback, state)
                guard_actions = domain_state.get("guards", {})
                progress(
                    f"domain {domain}: identity guard="
                    f"{guard_actions.get(IDENTITY_GUARD.name, {}).get('action', 'unknown')}; "
                    f"vpn guard={guard_actions.get(VPN_GUARD.name, {}).get('action', 'unknown')}; "
                    f"composite={guard_actions.get(COMPOSITE_GUARD_NAME, {}).get('action', 'unknown')}; "
                    f"domain default={domain_state.get('default_content_guard', {}).get('action', 'unknown')}"
                )
            except ToolError as error:
                progress(f"domain {domain}: guard preparation failed: {error}")
                domain_state["error"] = str(error)
                state["errors"].append({"domain": domain, "error": str(error)})
                state["counts"]["failed"] += 1
                write_state(output, rollback, state)
                continue

            total_distributions = len(by_domain[domain])
            for distribution_index, distribution in enumerate(
                by_domain[domain], start=1
            ):
                change = {
                    "distribution_href": distribution["pulp_href"],
                    "domain": domain,
                    "name": distribution.get("name"),
                    "state": "planned",
                }
                rollback_entry = None

                try:
                    if args.apply:
                        progress(
                            f"domain {domain}: distribution "
                            f"{distribution_index}/{total_distributions} revalidating"
                        )
                        live_domain_data, live = validate_live_candidate(
                            client, distribution
                        )
                    else:
                        live_domain_data, live = validated[distribution["pulp_href"]]
                    prior_explicit_guard = guard_href(live.get("content_guard"))
                    baseline_domain_data, baseline_live = validated[
                        distribution["pulp_href"]
                    ]
                    prior_effective_guard = guard_href(
                        guard_href(baseline_live.get("content_guard"))
                        or baseline_domain_data.get("default_content_guard")
                    )
                    if prior_effective_guard or prior_explicit_guard:
                        change.update(
                            state="skipped_already_guarded",
                            prior_explicit_content_guard=prior_explicit_guard,
                            prior_effective_content_guard=prior_effective_guard,
                        )
                        state["counts"]["skipped"] += 1
                        progress(
                            f"domain {domain}: distribution "
                            f"{distribution_index}/{total_distributions} skipped (already guarded)"
                        )
                    elif not args.apply:
                        change["assigned_guard"] = guard or "<created during apply>"
                        change["prior_explicit_content_guard"] = prior_explicit_guard
                        change["prior_effective_content_guard"] = prior_effective_guard
                        progress(
                            f"domain {domain}: distribution "
                            f"{distribution_index}/{total_distributions} planned"
                        )
                    else:
                        if not guard:
                            raise ToolError(
                                f"no guard href available for domain {domain}"
                            )
                        rollback_entry = {
                            "distribution_href": distribution["pulp_href"],
                            "domain": domain,
                            "prior_explicit_content_guard": prior_explicit_guard,
                            "prior_effective_content_guard": prior_effective_guard,
                            "prior_repository": live.get("repository"),
                            "prior_base_path": live.get("base_path"),
                            "assigned_guard": guard,
                            "state": "pending",
                        }
                        state["rollback"].append(rollback_entry)
                        write_state(output, rollback, state)

                        task_result = client.run(
                            domain,
                            distribution_command(
                                distribution["pulp_href"],
                                "partial-update",
                                "--content-guard",
                                guard,
                            ),
                            wait=False,
                        )
                        rollback_entry["state"] = "dispatched_pending"
                        rollback_entry["dispatch_result"] = task_result
                        rollback_entry["task_href"] = (
                            task_result.get("task")
                            if isinstance(task_result, dict)
                            else None
                        )
                        write_state(output, rollback, state)
                        after, poll_attempts, poll_elapsed = (
                            wait_for_distribution_guard(
                                client,
                                distribution,
                                guard,
                                args.assignment_timeout,
                                args.poll_max_interval,
                            )
                        )
                        after_guard = guard_href(after.get("content_guard"))
                        rollback_entry["state"] = "changed"
                        rollback_entry["after_explicit_content_guard"] = after_guard
                        rollback_entry["poll_attempts"] = poll_attempts
                        rollback_entry["poll_elapsed_seconds"] = poll_elapsed
                        change.update(
                            state="changed",
                            assigned_guard=guard,
                            prior_explicit_content_guard=prior_explicit_guard,
                            prior_effective_content_guard=prior_effective_guard,
                            after_explicit_content_guard=after_guard,
                            dispatch_result=task_result,
                            poll_attempts=poll_attempts,
                            poll_elapsed_seconds=poll_elapsed,
                        )
                        state["counts"]["changed"] += 1
                        progress(
                            f"domain {domain}: distribution "
                            f"{distribution_index}/{total_distributions} changed"
                        )
                except ToolError as error:
                    change.update(state="failed", error=str(error))
                    if (
                        rollback_entry is not None
                        and rollback_entry["state"] == "dispatched_pending"
                    ):
                        rollback_entry["state"] = "pending_review"
                        rollback_entry["error"] = str(error)
                    state["counts"]["failed"] += 1
                    progress(
                        f"domain {domain}: distribution "
                        f"{distribution_index}/{total_distributions} failed: {error}"
                    )
                    state["errors"].append(
                        {
                            "distribution_href": distribution["pulp_href"],
                            "domain": domain,
                            "error": str(error),
                        }
                    )

                state["changes"].append(change)
                write_state(output, rollback, state)

        if state["counts"]["failed"]:
            state["completion"] = "failed"
        elif args.partial_apply:
            state["completion"] = "partial_pending_audit"
        elif args.apply:
            state["completion"] = "complete"
        else:
            state["completion"] = "plan"
        write_state(output, rollback, state)
        print(json.dumps({"output": str(output), "counts": state["counts"]}, indent=2))
        if state["counts"]["failed"]:
            return 1
        return 3 if args.partial_apply else 0
    except ToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
