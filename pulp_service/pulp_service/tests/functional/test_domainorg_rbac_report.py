"""Integration test for the domainorg_rbac_report command.

Creates a domain under an org identity -- which grants the creator and the rh-org-<org_id> group
full domain roles -- then runs the command with --org-id --format json and asserts the derived
org group reads as FULL (can GET and PUSH). A second case strips the org group's roles to the
"locked out" shape and asserts the report flags it NONE. Mirrors test_domainorg_backfill_report.py.
"""

import json
import subprocess
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import requests


def _identity_header(org_id, username):
    identity = {
        "identity": {
            "org_id": org_id,
            "internal": {"org_id": org_id},
            "user": {"username": username},
        }
    }
    return b64encode(json.dumps(identity).encode("ascii")).decode("ascii")


def _rand_org():
    return str(uuid4().int % 90000000 + 10000000)


def _shell(code):
    result = subprocess.run(  # noqa: S603
        ["django-admin", "shell", "-c", code],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _strip_org_group_roles(org_id):
    """Delete every role held by the rh-org-<org_id> group, simulating the locked-out shape."""
    _shell(
        "\n".join(
            [
                "from pulpcore.app.models.role import GroupRole",
                f"n = GroupRole.objects.filter(group__name='rh-org-{org_id}').delete()",
                "print(n)",
            ]
        )
    )


def _report_json(*args):
    result = subprocess.run(  # noqa: S603
        ["django-admin", "domainorg_rbac_report", "--format", "json", *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _org_group_row(report, domain_name):
    return next(
        (r for r in report if r["domain_name"] == domain_name and r["principal_source"] == "derived-org-group"),
        None,
    )


def test_report_classifies_full_access_domain(create_service_domain):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"rbacreport-{uuid4().hex[:10]}", identity_header=owner)

    row = _org_group_row(_report_json("--org-id", org), domain.name)
    assert row is not None, f"{domain.name} org group not in report"
    assert row["principal_name"] == f"rh-org-{org}"
    assert row["tier"] == "FULL"
    assert row["can_get"] is True
    assert row["can_push"] is True
    assert row["flagged"] is False


def test_report_flags_locked_out_org_group(create_service_domain):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"rbaclocked-{uuid4().hex[:10]}", identity_header=owner)

    _strip_org_group_roles(org)

    row = _org_group_row(_report_json("--org-id", org), domain.name)
    assert row is not None, f"{domain.name} org group not in report"
    assert row["tier"] == "NONE"
    assert row["can_push"] is False
    assert row["flagged"] is True
    assert row["reason"] == "no-roles-locked-out"


def test_rbac_report_endpoint_produces_downloadable_json(create_service_domain, bindings_cfg, monitor_task):
    """The admin endpoint dispatches a task that yields a downloadable JSON report."""
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"rbacreport-api-{uuid4().hex[:10]}", identity_header=owner)

    admin_auth = (bindings_cfg.username, bindings_cfg.password)
    endpoint = urljoin(bindings_cfg.host, "/api/pulp/debug/domainorg-rbac-report/")

    resp = requests.post(endpoint, auth=admin_auth, timeout=60)
    assert resp.status_code == 202, resp.text
    task = monitor_task(resp.json()["task"])
    assert task.state == "completed"

    pa_resp = requests.get(
        urljoin(bindings_cfg.host, f"{task.pulp_href}profile_artifacts/"), auth=admin_auth, timeout=60
    )
    assert pa_resp.status_code == 200, pa_resp.text
    urls = pa_resp.json()["urls"]
    assert "domainorg_rbac_report" in urls, urls

    report_resp = requests.get(urls["domainorg_rbac_report"], auth=admin_auth, timeout=60)
    assert report_resp.status_code == 200, report_resp.text
    report = json.loads(report_resp.text)
    assert isinstance(report, list)

    row = _org_group_row(report, domain.name)
    assert row is not None, f"{domain.name} org group not in downloaded report"
    assert row["tier"] == "FULL"


def test_rbac_report_endpoint_requires_admin(bindings_cfg):
    """Unauthenticated callers cannot dispatch the report task."""
    endpoint = urljoin(bindings_cfg.host, "/api/pulp/debug/domainorg-rbac-report/")
    resp = requests.post(endpoint, timeout=60)
    assert resp.status_code in (401, 403), resp.text
