from contextlib import suppress
import cProfile
import logging
import marshal
import tempfile
import time

from opentelemetry import metrics
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.sdk.resources import Resource

from django.db import IntegrityError
from django.utils.deprecation import MiddlewareMixin

from pulpcore.app.models import Artifact
from pulpcore.app.util import get_artifact_url, get_worker_name


_logger = logging.getLogger(__name__)

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


class DjangoMetricsMiddleware:
    def __init__(self, get_response):
        exporter = OTLPMetricExporter()
        reader = PeriodicExportingMetricReader(exporter)
        resource = Resource(attributes={"service.name": "pulp-api"})
        provider = MeterProvider(metric_readers=[reader], resource=resource)

        set_meter_provider(provider)
        meter = metrics.get_meter("pulp.custom_metrics")

        self.request_duration_histogram = meter.create_histogram(
            name="http.server.custom.request_duration",
            description="Tracks the duration of HTTP requests",
            unit="ms"
        )

        self.get_response = get_response

    def __call__(self, request):
        start_time = time.time()
        response = self.get_response(request)
        end_time = time.time()

        duration_ms = (end_time - start_time) * 1000
        attributes = self._process_attributes(request, response)

        self.request_duration_histogram.record(duration_ms, attributes=attributes)

        return response

    def _process_attributes(self, request, response):
        return {
            "http.method": request.method,
            "http.status_code": self.normalize_status(response.status_code),
            "http.target": self._process_path(request, response),
            "worker.name": get_worker_name(),
        }

    @staticmethod
    def normalize_status(status):
        if 100 <= status < 200:
            return "1xx"
        elif 200 <= status < 300:
            return "2xx"
        elif 300 <= status < 400:
            return "3xx"
        elif 400 <= status < 500:
            return "4xx"
        elif 500 <= status < 600:
            return "5xx"
        else:
            return ""

    def _process_path(self, request, response):
        # to prevent cardinality explosion, do not record invalid paths
        if response.status_code > 400:
            return ""

        match = getattr(request, "resolver_match", "")
        route = getattr(match, "route", "")
        return route
