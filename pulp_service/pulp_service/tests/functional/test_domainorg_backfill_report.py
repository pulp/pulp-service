"""Integration test for the domainorg_backfill_report command.

Seeds a missing-org_id DomainOrg in a resolvable shape (single-org team member) and an
unresolvable shape (empty team group) using the same django-admin shell escape hatch as
test_org_member_historical_backfill.py, then runs the command with --format json and asserts
the per-row classification. Read-only: it also asserts the command did not mutate org_id.
"""

import json
import subprocess
from base64 import b64encode
from uuid import uuid4


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


def _null_org_id(domain_name):
    """Force the domain's DomainOrg into the missing-org_id shape, keeping its team group + member."""
    _shell(
        "\n".join(
            [
                "from django.apps import apps as A",
                "DomainOrg = A.get_model('service', 'DomainOrg')",
                f"do = DomainOrg.objects.filter(domains__name='{domain_name}').first()",
                "do.org_id = None",
                "do.save(update_fields=['org_id'])",
                "print(do.pk)",
            ]
        )
    )


def _report_json():
    result = subprocess.run(
        ["django-admin", "domainorg_backfill_report", "--format", "json"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_report_classifies_resolvable_domain(create_service_domain):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(
        name=f"backreport-{uuid4().hex[:10]}", identity_header=owner, group_name=f"team-{uuid4().hex[:8]}"
    )
    # The owner (in rh-org-<org>) is already the team group member on create; null the org_id.
    _null_org_id(domain.name)

    report = _report_json()
    row = next((r for r in report if domain.name in r["domain_names"]), None)
    assert row is not None, f"{domain.name} not in report: {report}"
    assert row["resolvable"] is True
    assert row["derived_org_id"] == org
    assert row["reason"] == "derived-from-team"

    # Read-only: the command must not have written org_id back.
    assert (
        _shell(
            "\n".join(
                [
                    "from django.apps import apps as A",
                    "DomainOrg = A.get_model('service', 'DomainOrg')",
                    f"do = DomainOrg.objects.filter(domains__name='{domain.name}').first()",
                    "print(do.org_id)",
                ]
            )
        )
        == "None"
    ), "report command must not mutate org_id"
