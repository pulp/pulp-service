from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch
from uuid import uuid4

from django.test import TestCase
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from pulp_npm.app.models import NpmRepository

from pulp_service.app.viewsets import NpmManifestBackfillView


class TestNpmManifestBackfillView(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.repository = SimpleNamespace(pk=uuid4())
        self.admin = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=True)

    def _request(self, data, user=None):
        request = self.factory.post("/api/pulp/npm/backfill-manifests/", data, format="json")
        request.pulp_domain = SimpleNamespace(pulp_id="default")
        force_authenticate(request, user=user or self.admin)
        return request

    def test_requires_admin(self):
        user = SimpleNamespace(is_authenticated=True, is_staff=False, is_superuser=False)
        response = NpmManifestBackfillView.as_view()(self._request({"repository": str(uuid4())}, user=user))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_rejects_invalid_repository_id(self):
        response = NpmManifestBackfillView.as_view()(self._request({"repository": "not-a-uuid"}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_returns_404_for_missing_repository(self):
        with patch(
            "pulp_service.app.viewsets.NpmRepository.objects.get",
            side_effect=NpmRepository.DoesNotExist,
        ):
            response = NpmManifestBackfillView.as_view()(self._request({"repository": str(uuid4())}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_returns_501_without_backfill_task(self):
        with (
            patch(
                "pulp_service.app.viewsets.NpmRepository.objects.get",
                return_value=self.repository,
            ),
            patch("pulp_service.app.viewsets._get_npm_backfill_task", return_value=None),
        ):
            response = NpmManifestBackfillView.as_view()(self._request({"repository": str(uuid4())}))
        self.assertEqual(response.status_code, status.HTTP_501_NOT_IMPLEMENTED)

    def test_dispatches_backfill_task(self):
        task_function = Mock()
        task = Mock()
        request = self._request({"repository": str(uuid4()), "overwrite": True})
        with (
            patch(
                "pulp_service.app.viewsets.NpmRepository.objects.get",
                return_value=self.repository,
            ),
            patch(
                "pulp_service.app.viewsets._get_npm_backfill_task",
                return_value=task_function,
            ),
            patch("pulp_service.app.viewsets.dispatch", return_value=task) as dispatch,
            patch(
                "pulp_service.app.viewsets.OperationPostponedResponse",
                return_value=Response(status=status.HTTP_202_ACCEPTED),
            ) as response_class,
        ):
            # The domain-scoped URL passes pulp_domain as a view kwarg.
            response = NpmManifestBackfillView.as_view()(request, pulp_domain="default")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        dispatch.assert_called_once_with(
            task_function,
            exclusive_resources=[self.repository],
            kwargs={
                "repository_pk": str(self.repository.pk),
                "overwrite": True,
            },
        )
        response_class.assert_called_once_with(task, ANY)
