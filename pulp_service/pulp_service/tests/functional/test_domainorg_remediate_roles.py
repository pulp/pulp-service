"""Integration test for the domainorg_remediate_roles task module.

Creates a domain under an org identity, strips the rh-org-<org_id> group's roles to the
"locked out" shape, then exercises remediate()/remediate_one() via django-admin shell:
dry_run makes no writes, a real run restores FULL access, and bad input is skipped with a
reason. Mirrors test_domainorg_backfill_report.py's shell-driven style.
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
    _shell(
        "\n".join(
            [
                "from pulpcore.app.models.role import GroupRole",
                f"GroupRole.objects.filter(group__name='rh-org-{org_id}').delete()",
            ]
        )
    )


def _domainorg_pk_for_org(org_id):
    return _shell(
        "\n".join(
            [
                "from pulp_service.app.models import DomainOrg",
                f"print(DomainOrg.objects.filter(org_id='{org_id}').values_list('pk', flat=True).first())",
            ]
        )
    )


def _run_remediate(pk, org_id, dry_run):
    """Call remediate() in-process via shell and return the parsed JSON report."""
    code = "\n".join(
        [
            "import json",
            "from pulp_service.app.tasks.domainorg_remediate_roles import remediate",
            f"report = remediate([{{'domain_org_pk': {pk}, 'org_id': '{org_id}'}}], dry_run={dry_run})",
            "print(json.dumps(report))",
        ]
    )
    return json.loads(_shell(code))


def _org_group_scoped_roles(org_id):
    """Count service.domain_admin (domain-scoped) roles held by the org group."""
    out = _shell(
        "\n".join(
            [
                "from pulpcore.app.models.role import GroupRole",
                f"qs = GroupRole.objects.filter(group__name='rh-org-{org_id}', role__name='service.domain_admin')",
                "print(qs.count())",
            ]
        )
    )
    return int(out)


def test_dry_run_makes_no_writes(create_service_domain):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    create_service_domain(name=f"remediate-dry-{uuid4().hex[:10]}", identity_header=owner)
    _strip_org_group_roles(org)
    assert _org_group_scoped_roles(org) == 0

    pk = _domainorg_pk_for_org(org)
    report = _run_remediate(pk, org, dry_run=True)

    assert report["dry_run"] is True
    assert report["results"][0]["action"] == "would-assign"
    assert report["summary"]["would_assign"] == 1
    # No writes happened.
    assert _org_group_scoped_roles(org) == 0


def test_real_run_restores_full_access(create_service_domain):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    create_service_domain(name=f"remediate-apply-{uuid4().hex[:10]}", identity_header=owner)
    _strip_org_group_roles(org)
    assert _org_group_scoped_roles(org) == 0

    pk = _domainorg_pk_for_org(org)
    report = _run_remediate(pk, org, dry_run=False)

    assert report["results"][0]["action"] == "assigned"
    assert report["results"][0]["reason"] == "ok"
    assert report["summary"]["assigned"] == 1
    # The scoped role is back -> the org group can GET+PUSH again.
    assert _org_group_scoped_roles(org) >= 1


def test_nonexistent_pk_is_skipped_not_found():
    # Valid org_id but unknown pk -> not-found, not a crash.
    report = _run_remediate(999999999, _rand_org(), dry_run=False)
    assert report["results"][0]["action"] == "skipped"
    assert report["results"][0]["reason"] == "not-found"
    assert report["summary"]["skipped"] == 1


def test_blank_org_id_is_skipped_invalid():
    # Blank org_id -> invalid-org-id (checked before the pk lookup, so pk is irrelevant).
    report = _run_remediate(999999999, "", dry_run=False)
    assert report["results"][0]["action"] == "skipped"
    assert report["results"][0]["reason"] == "invalid-org-id"


def test_rerun_is_idempotent(create_service_domain):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    create_service_domain(name=f"remediate-rerun-{uuid4().hex[:10]}", identity_header=owner)
    _strip_org_group_roles(org)
    pk = _domainorg_pk_for_org(org)

    first = _run_remediate(pk, org, dry_run=False)
    second = _run_remediate(pk, org, dry_run=False)

    # Second run does not raise, still reports assigned, and leaves exactly one scoped role.
    assert first["results"][0]["action"] == "assigned"
    assert second["results"][0]["action"] == "assigned"
    assert _org_group_scoped_roles(org) == 1


def _endpoint(bindings_cfg):
    return urljoin(bindings_cfg.host, "/api/pulp/debug/domainorg-remediate-roles/")


def test_endpoint_requires_admin(bindings_cfg):
    resp = requests.post(_endpoint(bindings_cfg), json={"assignments": []}, timeout=60)
    assert resp.status_code in (401, 403), resp.text


def test_endpoint_rejects_malformed_body(bindings_cfg):
    admin_auth = (bindings_cfg.username, bindings_cfg.password)
    # assignments missing entirely
    resp = requests.post(_endpoint(bindings_cfg), auth=admin_auth, json={}, timeout=60)
    assert resp.status_code == 400, resp.text
    # assignments not a list
    resp = requests.post(_endpoint(bindings_cfg), auth=admin_auth, json={"assignments": "nope"}, timeout=60)
    assert resp.status_code == 400, resp.text
    # item missing org_id
    resp = requests.post(
        _endpoint(bindings_cfg), auth=admin_auth, json={"assignments": [{"domain_org_pk": 1}]}, timeout=60
    )
    assert resp.status_code == 400, resp.text
    # body is a JSON array, not an object -> 400, not a 500
    resp = requests.post(_endpoint(bindings_cfg), auth=admin_auth, json=[], timeout=60)
    assert resp.status_code == 400, resp.text
    # dry_run is not a boolean
    resp = requests.post(
        _endpoint(bindings_cfg),
        auth=admin_auth,
        json={"assignments": [{"domain_org_pk": 1, "org_id": "1"}], "dry_run": "false"},
        timeout=60,
    )
    assert resp.status_code == 400, resp.text
    # duplicate domain_org_pk entries
    resp = requests.post(
        _endpoint(bindings_cfg),
        auth=admin_auth,
        json={"assignments": [{"domain_org_pk": 1, "org_id": "1"}, {"domain_org_pk": 1, "org_id": "2"}]},
        timeout=60,
    )
    assert resp.status_code == 400, resp.text


def test_endpoint_remediates_and_produces_downloadable_json(create_service_domain, bindings_cfg, monitor_task):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    create_service_domain(name=f"remediate-api-{uuid4().hex[:10]}", identity_header=owner)
    _strip_org_group_roles(org)
    assert _org_group_scoped_roles(org) == 0
    pk = int(_domainorg_pk_for_org(org))

    admin_auth = (bindings_cfg.username, bindings_cfg.password)
    resp = requests.post(
        _endpoint(bindings_cfg),
        auth=admin_auth,
        json={"assignments": [{"domain_org_pk": pk, "org_id": org}], "dry_run": False},
        timeout=60,
    )
    assert resp.status_code == 202, resp.text
    task = monitor_task(resp.json()["task"])
    assert task.state == "completed"

    pa_resp = requests.get(
        urljoin(bindings_cfg.host, f"{task.pulp_href}profile_artifacts/"), auth=admin_auth, timeout=60
    )
    assert pa_resp.status_code == 200, pa_resp.text
    urls = pa_resp.json()["urls"]
    assert "domainorg_remediate_roles" in urls, urls

    report = json.loads(requests.get(urls["domainorg_remediate_roles"], auth=admin_auth, timeout=60).text)
    assert report["results"][0]["action"] == "assigned"
    assert _org_group_scoped_roles(org) >= 1
