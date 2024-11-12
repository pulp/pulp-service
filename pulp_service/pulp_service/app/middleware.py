from contextlib import suppress
from contextvars import ContextVar
import cProfile
import logging
import marshal
import tempfile

from django.db import IntegrityError
from django.utils.deprecation import MiddlewareMixin

from pulpcore.app.models import Artifact
from pulpcore.app.util import get_artifact_url, get_worker_name, resolve_prn


_logger = logging.getLogger(__name__)
repository_name_var = ContextVar('repository_name')
x_quay_auth_var = ContextVar('x_quay_auth')


class ProfilerMiddleware(MiddlewareMixin):
    """
    Simple profile middleware to profile django views. To run it, add
    x-profile-request header to the request.

    This is adapted from an example found here:
    https://github.com/omarish/django-cprofile-middleware/blob/master/django_cprofile_middleware/middleware.py
    """
    PROFILER_REQUEST_ATTR_NAME = '_django_cprofile_middleware_profiler'

    def can(self, request):
        if 'HTTP_X_PROFILE_REQUEST' in request.META:
            return True
        return False
    def process_view(self, request, callback, callback_args, callback_kwargs):
        if self.can(request):
            profiler = cProfile.Profile()
            setattr(request, self.PROFILER_REQUEST_ATTR_NAME, profiler)
            args = (request,) + callback_args
            try:
                return profiler.runcall(
                    callback, *args, **callback_kwargs)
            except Exception:
                # we want the process_exception middleware to fire
                # https://code.djangoproject.com/ticket/12250
                return

    def process_response(self, request, response):
        if hasattr(request, self.PROFILER_REQUEST_ATTR_NAME):
            profiler = getattr(request, self.PROFILER_REQUEST_ATTR_NAME)
            profiler.create_stats()

            output = marshal.dumps(profiler.stats)

            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(output)
                temp_file.flush()
                artifact = Artifact.init_and_validate(temp_file.name)
                with suppress(IntegrityError):
                    artifact.save()
                _logger.info(f"Profile data URL: {get_artifact_url(artifact)}")

        return response


class OCIStorageMiddleware(MiddlewareMixin):
    def process_view(self, request, callback, callback_args, callback_kwargs):
        # Set the repository name in a contextvar
        if 'repository' in request.POST and request.POST['repository'].startswith('prn:'):
            m, pk = resolve_prn(request.POST['repository'])
            repository_name = m.objects.get(pk=pk).name
            repository_name_var.set(repository_name)
        if 'HTTP_X_QUAY_AUTH' in request.META:
            x_quay_auth_var.set(request.META['HTTP_X_QUAY_AUTH'])
