import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / "apply-identity-content-guards.py"
SPEC = importlib.util.spec_from_file_location("apply_identity_content_guards", SCRIPT)
tool = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def distribution(href, **overrides):
    value = {
        "domain": "tenant-a",
        "pulp_href": href,
        "pulp_classification": "pulp_unguarded",
        "effective_content_guard": None,
        "explicit_content_guard": None,
    }
    value.update(overrides)
    return value


def test_distribution_descriptor_uses_typed_href():
    href = "/api/pulp/tenant-a/api/v3/distributions/container/pull-through/abc/"

    assert tool.distribution_descriptor(href) == (
        "container-pull-through",
        "--container-container-pull-through-distribution-href",
    )
    assert tool.distribution_command(
        href, "partial-update", "--content-guard", "/guard/"
    ) == [
        "api",
        "distributions",
        "container-pull-through",
        "partial-update",
        "--container-container-pull-through-distribution-href",
        href,
        "--content-guard",
        "/guard/",
    ]


@pytest.mark.parametrize(
    ("href_type", "expected"),
    [
        ("python/pypi", ("python-pypi", "--python-python-distribution-href")),
        (
            "container/container",
            ("container", "--container-container-distribution-href"),
        ),
        ("core/openpgp", ("core-openpgp", "--open-p-g-p-distribution-href")),
        (
            "hugging_face/hugging_face",
            ("hugging-face", "--hugging-face-hugging-face-distribution-href"),
        ),
        (
            "hugging_face/hugging-face",
            ("hugging-face", "--hugging-face-hugging-face-distribution-href"),
        ),
        ("rpm/rpm", ("rpm", "--rpm-rpm-distribution-href")),
    ],
)
def test_distribution_command_matrix(href_type, expected):
    descriptor = tool.distribution_descriptor(
        f"/api/pulp/tenant/api/v3/distributions/{href_type}/uuid/"
    )
    assert descriptor == expected


def test_command_group_matches_generated_noun_group_flattening():
    assert tool.command_group("core", "header") == "core-header"
    assert tool.command_group("rpm", "rpm") == "rpm"
    assert tool.command_group("container", "pull-through") == "container-pull-through"


def test_vpn_guard_uses_verified_header_without_jq_filter():
    assert tool.guard_list_command(tool.VPN_GUARD) == [
        "api",
        "contentguards",
        "core-header",
        "list",
        "--name",
        "hosted-pulp-default-vpn-check",
        "--limit",
        "100",
    ]
    assert tool.guard_create_command(tool.VPN_GUARD) == [
        "api",
        "contentguards",
        "core-header",
        "create",
        "--name",
        "hosted-pulp-default-vpn-check",
        "--header-name",
        "X-Pulp-VPN-Verified",
        "--header-value",
        "true",
    ]
    assert tool.compatible_guard(
        {
            "name": "hosted-pulp-default-vpn-check",
            "header_name": "X-Pulp-VPN-Verified",
            "header_value": "true",
            "jq_filter": None,
        },
        tool.VPN_GUARD,
    )


def test_identity_guard_uses_nonempty_sentinel():
    command = tool.guard_create_command(tool.IDENTITY_GUARD)
    assert command[command.index("--header-value") + 1] == "identity-present"
    assert command[-2:] == ["--jq-filter", '"identity-present"']


def test_composite_guard_uses_exact_identity_or_vpn_members():
    identity = "/guards/identity/"
    vpn = "/guards/vpn/"
    assert tool.composite_create_command(identity, vpn) == [
        "api",
        "contentguards",
        "core-composite",
        "create",
        "--name",
        "hosted-pulp-default-identity-or-vpn",
        "--guards",
        identity,
        "--guards",
        vpn,
    ]
    assert tool.compatible_composite({"guards": [identity, vpn]}, identity, vpn)
    assert not tool.compatible_composite(
        {"guards": [identity, vpn, "/guards/other/"]}, identity, vpn
    )


def test_cli_defaults_to_stage_tbr_profile():
    assert (
        tool.build_parser().parse_args(["--report", "report.json"]).profile
        == "stage-tbr"
    )


def test_select_candidates_excludes_public_and_guarded_distributions():
    candidates, excluded = tool.select_candidates(
        [
            distribution("/one/"),
            distribution("/public/", domain="public-copr"),
            distribution("/guarded/", effective_content_guard="/guard/"),
            distribution("/explicit/", explicit_content_guard="/guard/"),
        ]
    )

    assert [item["pulp_href"] for item in candidates] == ["/one/"]
    assert {item["reason"] for item in excluded} == {
        "public_domain",
        "effective_guard_present",
        "explicit_guard_present",
    }


def test_merge_reports_deduplicates_and_rejects_conflicts(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps(
            {"complete": True, "errors": [], "distributions": [distribution("/one/")]}
        )
    )
    second.write_text(
        json.dumps(
            {"complete": True, "errors": [], "distributions": [distribution("/one/")]}
        )
    )

    reports = [tool.load_report(str(first)), tool.load_report(str(second))]
    assert [item["pulp_href"] for item in tool.merge_reports(reports, False)] == [
        "/one/"
    ]

    second.write_text(
        json.dumps(
            {
                "complete": True,
                "errors": [],
                "distributions": [distribution("/one/", domain="tenant-b")],
            }
        )
    )
    with pytest.raises(tool.ToolError, match="conflicting records"):
        tool.merge_reports(
            [tool.load_report(str(first)), tool.load_report(str(second))], False
        )


def test_incomplete_reports_require_explicit_opt_in(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "complete": False,
                "errors": ["GET /api/pulp/tenant-a/api/v3/distributions/ failed"],
                "distributions": [],
            }
        )
    )
    report = tool.load_report(str(report_path))

    with pytest.raises(tool.ToolError, match="incomplete or errored"):
        tool.merge_reports([report], False)
    assert tool.merge_reports([report], True) == []

    supplemental_path = tmp_path / "supplemental.json"
    supplemental_path.write_text(
        json.dumps(
            {
                "complete": True,
                "errors": [],
                "distributions": [
                    distribution("/api/pulp/tenant-a/api/v3/distributions/rpm/rpm/1/")
                ],
            }
        )
    )
    supplemental = tool.load_report(str(supplemental_path))
    assert len(tool.merge_reports([report, supplemental], True)) == 1


def test_incomplete_apply_requires_healthy_supplemental_coverage(tmp_path):
    metadata = {
        "environment": "stage",
        "base_url": "https://packages.stage.redhat.com",
        "public_domains_included": False,
        "distribution_endpoints": ["distributions/rpm/rpm"],
    }
    incomplete_path = tmp_path / "incomplete.json"
    incomplete_path.write_text(
        json.dumps(
            {
                "complete": False,
                "errors": ["tenant-a: request failed"],
                "metadata": metadata,
                "distributions": [],
            }
        )
    )
    healthy_path = tmp_path / "healthy.json"
    healthy_path.write_text(
        json.dumps(
            {
                "complete": True,
                "errors": [],
                "metadata": metadata,
                "distributions": [
                    distribution("/api/pulp/tenant-a/api/v3/distributions/rpm/rpm/1/")
                ],
            }
        )
    )
    incomplete = tool.load_report(str(incomplete_path))
    healthy = tool.load_report(str(healthy_path))

    assert (
        len(tool.merge_reports([incomplete, healthy], True, require_coverage=True)) == 1
    )
    with pytest.raises(tool.ToolError, match="complete, error-free supplemental"):
        tool.merge_reports([incomplete], True, require_coverage=True)


def test_compatible_guard_requires_canonical_settings():
    assert tool.compatible_guard(
        {
            "name": "hosted-pulp-default-identity-check",
            "header_name": "X-RH-IDENTITY",
            "header_value": "identity-present",
            "jq_filter": '"identity-present"',
        }
    )
    assert not tool.compatible_guard(
        {
            "name": "hosted-pulp-default-identity-check",
            "header_name": "x-rh-identity",
            "header_value": "different",
            "jq_filter": '"identity-present"',
        }
    )


class FakeHostedPulp:
    instances = []
    domain_name = "tenant-a"
    domain_href = "/api/pulp/default/api/v3/domains/domain-1/"
    update_after_patch = True

    def __init__(self, binary, profile):
        self.binary = binary
        self.profile = profile
        self.calls = []
        self.assigned_guard = None
        self.default_guard = None
        self.__class__.instances.append(self)

    def run(self, domain, arguments, *, wait=False):
        self.calls.append((domain, arguments, wait))
        if arguments[:3] == ["api", "domains", "read"]:
            return {
                "name": self.domain_name,
                "pulp_href": self.domain_href,
                "default_content_guard": self.default_guard,
            }
        if arguments[:3] == ["api", "domains", "partial-update"]:
            self.default_guard = arguments[
                arguments.index("--default-content-guard") + 1
            ]
            return {"task": "/api/pulp/default/api/v3/tasks/domain-task/"}
        if arguments[:2] == ["api", "contentguards"]:
            if arguments[2] == "core-header":
                if arguments[3] == "list":
                    return {"count": 0, "results": []}
                if arguments[3] == "create":
                    name = arguments[arguments.index("--name") + 1]
                    guard_id = (
                        "identity"
                        if name == "hosted-pulp-default-identity-check"
                        else "vpn"
                    )
                    return {
                        "pulp_href": f"/api/pulp/tenant-a/api/v3/contentguards/core/header/{guard_id}-guard/"
                    }
            if arguments[2] == "core-composite":
                if arguments[3] == "list":
                    return {"count": 0, "results": []}
                if arguments[3] == "create":
                    return {
                        "pulp_href": "/api/pulp/tenant-a/api/v3/contentguards/core/composite/composite-guard/"
                    }
        if arguments[:2] == ["api", "distributions"]:
            if arguments[3] == "read":
                return {
                    "pulp_href": arguments[-1],
                    "content_guard": self.assigned_guard,
                    "base_path": "repo",
                    "repository": "/api/pulp/tenant-a/api/v3/repositories/rpm/rpm/repo/",
                }
            if arguments[3] == "partial-update":
                if self.update_after_patch:
                    self.assigned_guard = arguments[
                        arguments.index("--content-guard") + 1
                    ]
                return {"task": "/api/pulp/tenant-a/api/v3/tasks/task-1/"}
        raise AssertionError(f"unexpected hosted-pulp command: {arguments}")


def write_apply_report(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "complete": True,
                "errors": [],
                "distributions": [
                    distribution(
                        "/api/pulp/tenant-a/api/v3/distributions/rpm/rpm/dist-1/",
                        domain_href=FakeHostedPulp.domain_href,
                        base_path="repo",
                        repository="/api/pulp/tenant-a/api/v3/repositories/rpm/rpm/repo/",
                    )
                ],
            }
        )
    )
    return report_path


def invoke_apply(monkeypatch, report_path, output_path, partial=False):
    monkeypatch.setattr(tool, "HostedPulp", FakeHostedPulp)
    arguments = [
        "apply-identity-content-guards.py",
        "--report",
        str(report_path),
        "--apply",
        "--yes",
        "--output",
        str(output_path),
        "--assignment-timeout",
        "0.01",
        "--poll-max-interval",
        "0.01",
    ]
    if partial:
        arguments.append("--partial-apply")
    monkeypatch.setattr(sys, "argv", arguments)
    return tool.main()


def test_apply_preflights_before_creating_guard_and_verifies_patch(
    monkeypatch, tmp_path
):
    FakeHostedPulp.instances.clear()
    FakeHostedPulp.domain_name = "tenant-a"
    FakeHostedPulp.update_after_patch = True
    result = invoke_apply(
        monkeypatch, write_apply_report(tmp_path), tmp_path / "result.json"
    )

    assert result == 0
    client = FakeHostedPulp.instances[-1]
    first_guard_call = next(
        index
        for index, call in enumerate(client.calls)
        if call[1][1:3] == ["contentguards", "core-header"]
    )
    first_domain_read = next(
        index
        for index, call in enumerate(client.calls)
        if call[1][1:3] == ["domains", "read"]
    )
    assert first_domain_read < first_guard_call
    state = json.loads((tmp_path / "result.json").read_text())
    assert state["counts"]["changed"] == 1
    assert state["changes"][0]["after_explicit_content_guard"].endswith(
        "identity-guard/"
    )
    assert (
        state["domains"]["tenant-a"]["guards"]["hosted-pulp-default-vpn-check"][
            "action"
        ]
        == "created"
    )
    assert (
        state["domains"]["tenant-a"]["guards"]["hosted-pulp-default-identity-or-vpn"][
            "action"
        ]
        == "created"
    )
    assert state["domains"]["tenant-a"]["default_content_guard"]["action"] == "set"
    patch_calls = [
        call for call in client.calls if call[1][0:2] == ["api", "distributions"]
    ]
    assert all(
        "vpn-guard" not in call[1] and "composite-guard" not in call[1]
        for call in patch_calls
    )


def test_apply_persists_post_update_failure(monkeypatch, tmp_path):
    FakeHostedPulp.instances.clear()
    FakeHostedPulp.domain_name = "tenant-a"
    FakeHostedPulp.update_after_patch = False
    result = invoke_apply(
        monkeypatch, write_apply_report(tmp_path), tmp_path / "result.json"
    )

    assert result == 1
    state = json.loads((tmp_path / "result.json").read_text())
    assert state["counts"]["failed"] == 1
    assert state["errors"][0]["distribution_href"].endswith("dist-1/")
    assert state["rollback"][0]["state"] == "pending_review"


def test_apply_persists_domain_preflight_failure_without_creating_guard(
    monkeypatch, tmp_path
):
    FakeHostedPulp.instances.clear()
    FakeHostedPulp.domain_name = "wrong-domain"
    FakeHostedPulp.update_after_patch = True
    result = invoke_apply(
        monkeypatch, write_apply_report(tmp_path), tmp_path / "result.json"
    )

    assert result == 1
    client = FakeHostedPulp.instances[-1]
    assert not any(call[1][1] == "contentguards" for call in client.calls)
    state = json.loads((tmp_path / "result.json").read_text())
    assert state["counts"]["failed"] == 1
    assert state["changes"][0]["state"] == "failed_preflight"


def test_partial_apply_records_pending_audit_and_returns_distinct_status(
    monkeypatch, tmp_path
):
    report_path = tmp_path / "partial.json"
    report_path.write_text(
        json.dumps(
            {
                "complete": False,
                "errors": ["missing-domain: audit request failed"],
                "metadata": {
                    "environment": "stage",
                    "base_url": "https://packages.stage.redhat.com",
                    "public_domains_included": False,
                    "distribution_endpoints": ["distributions/rpm/rpm"],
                },
                "distributions": [
                    distribution(
                        "/api/pulp/tenant-a/api/v3/distributions/rpm/rpm/dist-1/",
                        domain_href=FakeHostedPulp.domain_href,
                        base_path="repo",
                        repository="/api/pulp/tenant-a/api/v3/repositories/rpm/rpm/repo/",
                    )
                ],
            }
        )
    )
    FakeHostedPulp.instances.clear()
    FakeHostedPulp.domain_name = "tenant-a"
    FakeHostedPulp.update_after_patch = True

    result = invoke_apply(
        monkeypatch, report_path, tmp_path / "result.json", partial=True
    )

    assert result == 3
    state = json.loads((tmp_path / "result.json").read_text())
    assert state["completion"] == "partial_pending_audit"
    assert state["unresolved_audit_domains"] == ["missing-domain"]
    assert state["counts"]["changed"] == 1


def test_partial_apply_defers_candidates_in_unresolved_audit_domains(
    monkeypatch, tmp_path
):
    report_path = tmp_path / "partial.json"
    report_path.write_text(
        json.dumps(
            {
                "complete": False,
                "errors": ["tenant-a: audit request failed"],
                "metadata": {
                    "environment": "stage",
                    "base_url": "https://packages.stage.redhat.com",
                    "public_domains_included": False,
                    "distribution_endpoints": ["distributions/rpm/rpm"],
                },
                "distributions": [
                    distribution(
                        "/api/pulp/tenant-a/api/v3/distributions/rpm/rpm/dist-1/",
                        domain_href=FakeHostedPulp.domain_href,
                        base_path="repo",
                        repository="/api/pulp/tenant-a/api/v3/repositories/rpm/rpm/repo/",
                    )
                ],
            }
        )
    )
    FakeHostedPulp.instances.clear()
    result = invoke_apply(
        monkeypatch, report_path, tmp_path / "result.json", partial=True
    )

    assert result == 3
    assert FakeHostedPulp.instances[-1].calls == []
    state = json.loads((tmp_path / "result.json").read_text())
    assert state["counts"]["changed"] == 0
    assert state["counts"]["audit_deferred"] == 1
    assert state["audit_deferred"][0]["reason"] == "unresolved_audit_domain"


def test_domain_defaults_only_uses_domain_inventory_without_distributions(
    monkeypatch, tmp_path
):
    report_path = tmp_path / "domains.json"
    report_path.write_text(
        json.dumps(
            {
                "complete": True,
                "errors": [],
                "metadata": {
                    "environment": "stage",
                    "base_url": "https://packages.stage.redhat.com",
                    "public_domains_included": False,
                    "distribution_endpoints": ["distributions/rpm/rpm"],
                },
                "domains": [
                    {"name": "tenant-a", "pulp_href": FakeHostedPulp.domain_href},
                    {"name": "public-copr", "pulp_href": "/public/"},
                    {"name": "default", "pulp_href": "/default/"},
                ],
                "distributions": [],
            }
        )
    )
    FakeHostedPulp.instances.clear()
    monkeypatch.setattr(tool, "HostedPulp", FakeHostedPulp)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply-identity-content-guards.py",
            "--report",
            str(report_path),
            "--domain-defaults-only",
            "--output",
            str(tmp_path / "result.json"),
        ],
    )

    assert tool.main() == 0
    client = FakeHostedPulp.instances[-1]
    assert not any(call[1][0:2] == ["api", "distributions"] for call in client.calls)
    state = json.loads((tmp_path / "result.json").read_text())
    assert state["counts"]["selected_domains"] == 1
    assert state["counts"]["excluded_domains"] == 2


def test_domain_defaults_only_partial_apply_defers_unresolved_domains(
    monkeypatch, tmp_path
):
    report_path = tmp_path / "domains-partial.json"
    report_path.write_text(
        json.dumps(
            {
                "complete": False,
                "errors": ["missing-domain: audit request failed"],
                "metadata": {
                    "environment": "stage",
                    "base_url": "https://packages.stage.redhat.com",
                    "public_domains_included": False,
                    "distribution_endpoints": ["distributions/rpm/rpm"],
                },
                "domains": [
                    {"name": "tenant-a", "pulp_href": FakeHostedPulp.domain_href},
                    {"name": "missing-domain", "pulp_href": "/missing/"},
                ],
                "distributions": [],
            }
        )
    )
    FakeHostedPulp.instances.clear()
    monkeypatch.setattr(tool, "HostedPulp", FakeHostedPulp)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply-identity-content-guards.py",
            "--report",
            str(report_path),
            "--domain-defaults-only",
            "--partial-apply",
            "--apply",
            "--yes",
            "--output",
            str(tmp_path / "result.json"),
        ],
    )

    assert tool.main() == 3
    state = json.loads((tmp_path / "result.json").read_text())
    assert state["completion"] == "partial_pending_audit"
    assert state["counts"]["audit_deferred"] == 1
    assert state["unresolved_audit_domains"] == ["missing-domain"]
