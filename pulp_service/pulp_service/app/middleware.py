import cProfile
import logging
import marshal
import tempfile

from contextlib import suppress
from contextvars import ContextVar
from os import getenv

from django.contrib.auth import login
from django.db import IntegrityError
from django.utils.deprecation import MiddlewareMixin


from pulpcore.metrics import init_otel_meter
from pulpcore.app.util import get_worker_name
from pulpcore.plugin.models import Artifact, Repository
from pulpcore.plugin.util import extract_pk, get_artifact_url, resolve_prn
from pulp_service.app.authentication import RHSamlAuthentication



_logger = logging.getLogger(__name__)
repository_name_var = ContextVar('repository_name')
x_quay_auth_var = ContextVar('x_quay_auth')
x_task_diagnostics_var = ContextVar('x_profile_task')
request_path_var = ContextVar('request_path')


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


class RhEdgeHostMiddleware(MiddlewareMixin):
    def process_view(self, request, *args, **kwargs):
        if "HTTP_X_RH_EDGE_HOST" in request.META and request.META["HTTP_X_RH_EDGE_HOST"] is not None:
            request.META["HTTP_X_FORWARDED_HOST"] = request.META["HTTP_X_RH_EDGE_HOST"]


class RHSamlAuthHeaderMiddleware(MiddlewareMixin):
    def process_view(self, request, *args, **kwargs):
        if '/pulp-mgmt/' in request.path:
            if "HTTP_X_RH_IDENTITY" in request.META:
                _logger.debug(f"{request.META['HTTP_X_RH_IDENTITY']}")

                # Authenticate user using RHSamlAuthentication backend
                if not request.user.is_authenticated:
                    backend = RHSamlAuthentication()
                    user, _ = backend.authenticate(request)

                    if user:
                        login(request, user, backend='pulp_service.app.authentication.RHSamlAuthentication')
                        request.session.modified = True
                        # Update request.user for the current request
                        request.user = user
                        _logger.info(f"User {user.username} authenticated for pulp-mgmt")
                    else:
                        _logger.warning("Failed to authenticate user from RH Identity header")


class RequestPathMiddleware(MiddlewareMixin):
    """
    Middleware to store the request path in a context variable.

    This allows signals to access the request path when processing model changes.
    """

    def process_view(self, request, *args, **kwargs):
        request_path_var.set(request.path)


class ActiveConnectionsMetricMiddleware(MiddlewareMixin):
    """
    Middleware to track the number of active/concurrent HTTP connections to the API.

    This middleware increments an UpDownCounter when a request enters and decrements it
    when the request completes, providing a real-time gauge of in-flight requests.
    """

    def __init__(self, get_response):
        self.meter = init_otel_meter("pulp-api")
        self.active_connections_counter = self.meter.create_up_down_counter(
            name="api.active_connections",
            description="Tracks the number of active/concurrent HTTP connections",
            unit="1",
        )
        self.get_response = get_response

    def __call__(self, request):
        # Increment active connections counter
        worker_attributes = {"worker.name": get_worker_name()}
        self.active_connections_counter.add(1, attributes=worker_attributes)

        try:
            return self.get_response(request)
        finally:
            # Decrement active connections counter
            self.active_connections_counter.add(-1, attributes=worker_attributes)
