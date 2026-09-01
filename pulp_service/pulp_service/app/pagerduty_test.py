"""Synthetic gauge used to fire PulpPagerDutyTest.

POST /api/pulp/test/pagerduty-alert/ arms the gauge at 2 for one hour.
There is no reset endpoint; the value returns to 0 when the TTL expires.
"""

import time

from opentelemetry.metrics import Observation

from pulpcore.metrics import init_otel_meter

TTL_SECONDS = 3600
ARMED_VALUE = 2.0

_expires_at = 0.0
_instrument_registered = False


def arm():
    global _expires_at
    _expires_at = time.monotonic() + TTL_SECONDS
    _ensure_instrument()


def current_value():
    if time.monotonic() < _expires_at:
        return ARMED_VALUE
    return 0.0


def remaining_seconds():
    remaining = _expires_at - time.monotonic()
    return max(0, int(remaining))


def _observe(_options):
    yield Observation(current_value())


def _ensure_instrument():
    global _instrument_registered
    if _instrument_registered:
        return
    meter = init_otel_meter("pulp-api")
    meter.create_observable_gauge(
        "pagerduty.test",
        callbacks=[_observe],
        description="Armed for 1 hour after POST /api/pulp/test/pagerduty-alert/.",
    )
    _instrument_registered = True
