import time

from aiohttp import web
from frozenlist import FrozenList

from opentelemetry import metrics
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.sdk.resources import Resource

from pulpcore.app.util import get_worker_name
from pulpcore.plugin.content import app

from pulp_service.app.util import normalize_status


exporter = OTLPMetricExporter()
reader = PeriodicExportingMetricReader(exporter)
resource = Resource(attributes={"service.name": "pulp-content"})
provider = MeterProvider(metric_readers=[reader], resource=resource)

set_meter_provider(provider)
meter = metrics.get_meter("pulp.metrics")

request_duration_histogram = meter.create_histogram(
    name="content.request_duration",
    description="Tracks the duration of HTTP requests",
    unit="ms"
)


@web.middleware
async def metrics_middleware(request, handler):
    start_time = time.time()

    try:
        response = await handler(request)
        status_code = response.status
    except web.HTTPException as exc:
        status_code = exc.status
        response = exc

    duration_ms = (time.time() - start_time) * 1000

    request_duration_histogram.record(
        duration_ms,
        attributes={
            "http.method": request.method,
            "http.status_code": normalize_status(status_code),
            "http.route": _get_view_func(request),
            "worker.name": get_worker_name(),
        },
    )

    return response


def _get_view_func(request):
    try:
        return request.match_info.handler.__name__
    except AttributeError:
        return "unknown"


app._middlewares = FrozenList([metrics_middleware, *app.middlewares])
