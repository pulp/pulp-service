"""
Unit tests for CreateDomainView group_name logic.

These tests mock Django ORM and Pulp internals so they can run without
a live Pulp stack.  They focus on the group-resolution branch logic
introduced by the custom group_name feature.
"""

from unittest.mock import MagicMock, patch

from rest_framework.test import APIRequestFactory

from pulp_service.app.authorization import group_var
from pulp_service.app.viewsets import CreateDomainView

factory = APIRequestFactory()


def _make_request(data, user=None):
    """Build a POST request with a pre-attached authenticated user."""
    request = factory.post("/api/pulp/create-domain/", data, format="json")
    request.user = user or _make_user()
    request.resolver_match = MagicMock()
    request.resolver_match.view_name = "create-domain"
    request.META["HTTP_X_RH_IDENTITY"] = ""
    request.pulp_domain = MagicMock()
    return request


def _make_user(username="testuser", groups=None, is_superuser=False):
    user = MagicMock()
    user.username = username
    user.is_superuser = is_superuser
    user.is_authenticated = True

    group_qs = MagicMock()
    if groups:
        group_qs.exists.return_value = True
        group_qs.first.return_value = groups[0]
    else:
        group_qs.exists.return_value = False
        group_qs.first.return_value = None
    user.groups = group_qs
    return user


def _make_group(name, pk=1):
    group = MagicMock()
    group.name = name
    group.pk = pk
    return group


# -- Template domain & serializer patches used by every test that reaches
#    past the group-resolution block.


def _patch_domain_and_serializer():
    """Return a dict of patch targets suitable for ``with`` / ``patch.multiple``."""
    template = MagicMock()
    template.storage_settings = {"bucket": "test"}
    template.storage_class = "pulpcore.app.models.storage.FileSystem"
    template.pulp_labels = {}

    domain_instance = MagicMock()
    domain_instance.pk = 42

    serializer_instance = MagicMock()
    serializer_instance.is_valid.return_value = True
    serializer_instance.save.return_value = domain_instance

    return template, serializer_instance, domain_instance


# ---------------------------------------------------------------------------
# Tests for missing domain name
# ---------------------------------------------------------------------------


class TestCreateDomainViewValidation:
    def test_missing_domain_name_returns_400(self):
        request = _make_request({})
        view = CreateDomainView.as_view()

        with patch.object(CreateDomainView, "permission_classes", []):
            view = CreateDomainView.as_view()
            response = view(request)

        assert response.status_code == 400
        assert "Domain name is required" in response.data["error"]


# ---------------------------------------------------------------------------
# Tests for group_name resolution
# ---------------------------------------------------------------------------


class TestGroupNameResolution:
    """Verify the three group-resolution paths in CreateDomainView.post()."""

    def _call_view(self, request):
        """Call the view with permissions and downstream deps stubbed out."""
        template, serializer_inst, domain_inst = _patch_domain_and_serializer()

        with (
            patch.object(CreateDomainView, "permission_classes", []),
            patch("pulp_service.app.viewsets.Domain.objects") as domain_objects,
            patch("pulp_service.app.viewsets.DomainSerializer") as ser_cls,
            patch("pulp_service.app.viewsets.transaction"),
        ):
            domain_objects.get.return_value = template
            ser_cls.return_value = serializer_inst
            ser_cls.side_effect = None
            # Second call to DomainSerializer (for response) returns a mock
            # with a .data attribute.
            response_ser = MagicMock()
            response_ser.data = {"name": "test-domain", "pulp_href": "/href/"}
            ser_cls.side_effect = [serializer_inst, response_ser]

            view = CreateDomainView.as_view()
            return view(request)

    # -- Path 1: custom group_name, group does NOT exist ----------------

    @patch("pulp_service.app.viewsets.Group.objects")
    def test_new_custom_group_created_and_user_added(self, mock_group_objects):
        new_group = _make_group("my-team")
        mock_group_objects.get_or_create.return_value = (new_group, True)

        user = _make_user()
        request = _make_request({"name": "test-domain", "group_name": "my-team"}, user=user)

        response = self._call_view(request)

        assert response.status_code == 201
        mock_group_objects.get_or_create.assert_called_once_with(name="my-team")
        user.groups.add.assert_called_once_with(new_group)
        # group_var should have been set (signal will consume it)
        # We can't easily check it was set because the signal mock would
        # consume it, but we verify the view didn't error out.

    # -- Path 2: custom group_name, group already exists ----------------

    @patch("pulp_service.app.viewsets.Group.objects")
    def test_existing_custom_group_user_not_added(self, mock_group_objects):
        existing_group = _make_group("existing-team")
        mock_group_objects.get_or_create.return_value = (existing_group, False)

        user = _make_user()
        request = _make_request({"name": "test-domain", "group_name": "existing-team"}, user=user)

        response = self._call_view(request)

        assert response.status_code == 201
        mock_group_objects.get_or_create.assert_called_once_with(name="existing-team")
        # User must NOT be added to the existing group
        user.groups.add.assert_not_called()

    # -- Path 3: no group_name, user has no groups ----------------------

    @patch("pulp_service.app.viewsets.Group.objects")
    def test_auto_group_created_when_user_has_no_groups(self, mock_group_objects):
        auto_group = _make_group("domain-test-domain")
        mock_group_objects.get_or_create.return_value = (auto_group, True)

        user = _make_user()  # no groups
        request = _make_request({"name": "test-domain"}, user=user)

        response = self._call_view(request)

        assert response.status_code == 201
        mock_group_objects.get_or_create.assert_called_once_with(name="domain-test-domain")
        user.groups.add.assert_called_once_with(auto_group)

    # -- Path 4: no group_name, user already has a group ----------------

    @patch("pulp_service.app.viewsets.Group.objects")
    def test_existing_user_group_reused(self, mock_group_objects):
        existing = _make_group("preexisting-group")
        user = _make_user(groups=[existing])
        request = _make_request({"name": "test-domain"}, user=user)

        response = self._call_view(request)

        assert response.status_code == 201
        # get_or_create should NOT be called -- we reuse the user's group
        mock_group_objects.get_or_create.assert_not_called()

    # -- Path 5: group resolution raises -> 400 -------------------------

    @patch("pulp_service.app.viewsets.Group.objects")
    def test_group_resolution_error_returns_400(self, mock_group_objects):
        mock_group_objects.get_or_create.side_effect = Exception("DB down")

        user = _make_user()
        request = _make_request({"name": "test-domain", "group_name": "bad-group"}, user=user)

        with patch.object(CreateDomainView, "permission_classes", []):
            view = CreateDomainView.as_view()
            response = view(request)

        assert response.status_code == 400
        assert "Failed to resolve group" in response.data["error"]


# ---------------------------------------------------------------------------
# Tests for group_var context variable plumbing
# ---------------------------------------------------------------------------


class TestGroupVarContextVariable:
    """Verify that group_var is set correctly for the signal to consume."""

    @patch("pulp_service.app.viewsets.Group.objects")
    def test_group_var_set_with_custom_group(self, mock_group_objects):
        custom_group = _make_group("custom-team")
        mock_group_objects.get_or_create.return_value = (custom_group, True)

        template, serializer_inst, domain_inst = _patch_domain_and_serializer()

        user = _make_user()
        request = _make_request({"name": "test-domain", "group_name": "custom-team"}, user=user)

        captured_group = None

        # Intercept the serializer.save() call to capture group_var at that point
        original_save = serializer_inst.save

        def capture_group_var(*args, **kwargs):
            nonlocal captured_group
            captured_group = group_var.get(None)
            return original_save.return_value

        serializer_inst.save.side_effect = capture_group_var

        with (
            patch.object(CreateDomainView, "permission_classes", []),
            patch("pulp_service.app.viewsets.Domain.objects") as domain_objects,
            patch("pulp_service.app.viewsets.DomainSerializer") as ser_cls,
            patch("pulp_service.app.viewsets.transaction"),
        ):
            domain_objects.get.return_value = template
            response_ser = MagicMock()
            response_ser.data = {"name": "test-domain"}
            ser_cls.side_effect = [serializer_inst, response_ser]

            view = CreateDomainView.as_view()
            view(request)

        assert captured_group is custom_group

    @patch("pulp_service.app.viewsets.Group.objects")
    def test_group_var_set_with_auto_group(self, mock_group_objects):
        auto_group = _make_group("domain-test-domain")
        mock_group_objects.get_or_create.return_value = (auto_group, True)

        template, serializer_inst, domain_inst = _patch_domain_and_serializer()

        user = _make_user()  # no groups
        request = _make_request({"name": "test-domain"}, user=user)

        captured_group = None

        def capture_group_var(*args, **kwargs):
            nonlocal captured_group
            captured_group = group_var.get(None)
            return serializer_inst.save.return_value

        serializer_inst.save.side_effect = capture_group_var

        with (
            patch.object(CreateDomainView, "permission_classes", []),
            patch("pulp_service.app.viewsets.Domain.objects") as domain_objects,
            patch("pulp_service.app.viewsets.DomainSerializer") as ser_cls,
            patch("pulp_service.app.viewsets.transaction"),
        ):
            domain_objects.get.return_value = template
            response_ser = MagicMock()
            response_ser.data = {"name": "test-domain"}
            ser_cls.side_effect = [serializer_inst, response_ser]

            view = CreateDomainView.as_view()
            view(request)

        assert captured_group is auto_group
