from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from pulpcore.plugin.models import Domain, Repository


CONTENT_SOURCES_LABEL_NAME = "contentsources"
RHEL_AI_DOMAIN_NAME = "rhel-ai"


def _create_meter(service_name):
    exporter = OTLPMetricExporter()
    resource = Resource(attributes={"service.name": service_name})
    metric_reader = PeriodicExportingMetricReader(exporter)
    provider = MeterProvider(metric_readers=[metric_reader], resource=resource)
    return provider.get_meter(__name__), metric_reader


def content_sources_domains_count():
    meter, metric_reader = _create_meter("content-sources_domains")
    meter.create_observable_up_down_counter(
        "content_sources.domains.count",
        callbacks=[_get_content_sources_domains_count],
        description="Count of Content Sources owned domains.",
    )
    metric_reader.collect()


def _get_content_sources_domains_count(options):
    content_sources_domains = Domain.objects.filter(
        pulp_labels__contains={CONTENT_SOURCES_LABEL_NAME: "true"},
    )
    content_sources_domains_count = content_sources_domains.count()
    yield Observation(content_sources_domains_count)


def rhel_ai_repos_count():
    meter, metric_reader = _create_meter("rhel-ai_repos")
    meter.create_observable_up_down_counter(
        "rhel_ai.repositories.count",
        callbacks=[_get_rhel_ai_repos_count],
        description="Count of RHEL AI owned repositories.",
    )
    metric_reader.collect()


def _get_rhel_ai_repos_count(options):
    rhel_ai_repos = Repository.objects.select_related("pulp_domain").filter(
        pulp_domain__name=RHEL_AI_DOMAIN_NAME
    )
    rhel_ai_repos_count = rhel_ai_repos.count()
    yield Observation(rhel_ai_repos_count)
