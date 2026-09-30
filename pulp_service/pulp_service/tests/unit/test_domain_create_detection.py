"""
Unit tests for PulpServiceAccessPolicy._is_domain_create().

This predicate gates the bridge that populates the domain-create ContextVars for the
generic pulpcore DomainViewSet endpoint (so post_create_domain assigns the owner
DomainOrg row and RBAC roles). It must fire only for a POST to the generic
``domains-list`` route, never for reads or unrelated writes.
"""

from types import SimpleNamespace

from pulp_service.app.access_policy import PulpServiceAccessPolicy


def _request(method, view_name):
    resolver_match = None if view_name is None else SimpleNamespace(view_name=view_name)
    return SimpleNamespace(method=method, resolver_match=resolver_match)


def test_generic_domain_create_post_detected():
    assert PulpServiceAccessPolicy._is_domain_create(_request("POST", "domains-list")) is True


def test_domain_list_get_is_not_create():
    assert PulpServiceAccessPolicy._is_domain_create(_request("GET", "domains-list")) is False


def test_head_on_domains_list_is_not_create():
    assert PulpServiceAccessPolicy._is_domain_create(_request("HEAD", "domains-list")) is False


def test_post_to_other_endpoint_is_not_domain_create():
    assert PulpServiceAccessPolicy._is_domain_create(_request("POST", "repositories-rpm-rpm-list")) is False


def test_missing_resolver_match_is_not_domain_create():
    assert PulpServiceAccessPolicy._is_domain_create(_request("POST", None)) is False
