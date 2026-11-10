"""
Functional tests for AndGateContentGuard.

These mirror test_domain_dual_write.py: they build real content guards through the
Django ORM in a throwaway domain and call permit() directly against a fake request,
exercising the real `guards` M2M, the .cast() dispatch, and real sub-guard permit()
calls. AndGate permits only when EVERY sub-guard permits.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from pulpcore.app.models import HeaderContentGuard
from pulpcore.plugin.models import Domain

from pulp_service.app.models import AndGateContentGuard, FeatureContentGuard
from pulp_service.exceptions import AuthenticationError

# Base64 of "true"; the header value both sub-guards below expect.
_TRUE = "dHJ1ZQ=="
HEADER_A = "x-test-a"
HEADER_B = "x-test-b"


def _make_domain():
    return Domain.objects.create(
        name=f"andgate-{uuid4()}",
        storage_class="pulpcore.app.models.storage.FileSystem",
        storage_settings={"MEDIA_ROOT": "/var/lib/pulp/media/"},
    )


def _header_guard(domain, name, header_name):
    return HeaderContentGuard.objects.create(
        name=name,
        header_name=header_name,
        header_value="true",
        pulp_domain=domain,
    )


@pytest.mark.django_db
def test_and_gate_permits_only_when_all_sub_guards_permit():
    domain = _make_domain()
    guard_a = _header_guard(domain, f"a-{uuid4()}", HEADER_A)
    guard_b = _header_guard(domain, f"b-{uuid4()}", HEADER_B)
    and_gate = AndGateContentGuard.objects.create(name=f"andgate-{uuid4()}", pulp_domain=domain)
    and_gate.guards.add(guard_a, guard_b)

    try:
        # Both headers satisfied -> allow.
        and_gate.permit(SimpleNamespace(headers={HEADER_A: _TRUE, HEADER_B: _TRUE}))

        # Only one satisfied -> deny.
        with pytest.raises(PermissionError):
            and_gate.permit(SimpleNamespace(headers={HEADER_A: _TRUE}))
        with pytest.raises(PermissionError):
            and_gate.permit(SimpleNamespace(headers={HEADER_B: _TRUE}))

        # Neither satisfied -> deny.
        with pytest.raises(PermissionError):
            and_gate.permit(SimpleNamespace(headers={}))
    finally:
        and_gate.delete()
        guard_a.delete()
        guard_b.delete()
        domain.delete()


def _feature_guard(domain):
    return FeatureContentGuard.objects.create(
        name=f"feat-{uuid4()}",
        header_name="x-rh-identity",
        jq_filter=".identity.org_id",
        features=["lightwell-network"],
        pulp_domain=domain,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("feature_first", [False, True])
def test_and_gate_raises_authentication_error_when_identity_sub_guard_needs_auth(feature_first):
    # Combines a FeatureContentGuard (needs x-rh-identity) with a plain header guard. With no
    # headers at all, the feature guard raises AuthenticationError and the header guard raises
    # PermissionError; the AndGate must surface AuthenticationError (401 challenge) regardless
    # of guard order, so an unauthenticated client can retry with credentials rather than
    # getting a dead-end 403. The feature guard raises before any Features Service call.
    # Parametrized over both guard orderings (guards are created in the parametrized order so
    # the M2M .all() iteration order varies) to keep the 401-over-403 order-independence covered.
    domain = _make_domain()
    if feature_first:
        feature_guard = _feature_guard(domain)
        header_guard = _header_guard(domain, f"h-{uuid4()}", HEADER_A)
    else:
        header_guard = _header_guard(domain, f"h-{uuid4()}", HEADER_A)
        feature_guard = _feature_guard(domain)
    and_gate = AndGateContentGuard.objects.create(name=f"andgate-{uuid4()}", pulp_domain=domain)
    and_gate.guards.add(header_guard, feature_guard)

    try:
        with pytest.raises(AuthenticationError):
            and_gate.permit(SimpleNamespace(headers={}))
    finally:
        and_gate.delete()
        header_guard.delete()
        feature_guard.delete()
        domain.delete()


@pytest.mark.django_db
def test_and_gate_with_no_guards_denies_fail_closed():
    domain = _make_domain()
    and_gate = AndGateContentGuard.objects.create(name=f"andgate-{uuid4()}", pulp_domain=domain)

    try:
        with pytest.raises(PermissionError):
            and_gate.permit(SimpleNamespace(headers={HEADER_A: _TRUE}))
    finally:
        and_gate.delete()
        domain.delete()
