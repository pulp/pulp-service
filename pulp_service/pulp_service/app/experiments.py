"""Temporary, opt-in performance experiments for hosted Pulp."""

import json
import logging
import random
from time import perf_counter

from django.conf import settings
from django.db import connections
from django.db.models import Q
from django_guid import get_guid

logger = logging.getLogger("pulp.experiment")
DIRECTORY_EXPERIMENT = "PULP-2505"


def run_experiment(exp_id, control, candidate, *, p_candidate, context):
    """Evaluate one callable and record its duration, including failures."""
    variant = "B" if random.random() < p_candidate else "A"  # noqa: S311
    record = {
        **context,
        "event": "ab_experiment",
        "exp_id": exp_id,
        "variant": variant,
        "p_candidate": p_candidate,
        "correlation_id": get_guid(),
        "outcome": "error",
    }
    start = perf_counter()
    try:
        result = (candidate if variant == "B" else control)()
        record.update(outcome="success", result_count=len(result))
        return result
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        raise
    finally:
        record["duration_ms"] = round((perf_counter() - start) * 1000, 3)
        logger.info(json.dumps(record))


def control_membership_dates(memberships, names):
    """Keep the existing membership iteration and Python filtering."""
    return {names[row.content_id]: row.pulp_created for row in memberships if row.content_id in names}


def candidate_membership_dates(memberships, names, sources):
    """Filter memberships with SQL subqueries, without binding every content ID."""
    listed_content = Q(content_id__in=[])
    for queryset, field in sources:
        listed_content |= Q(content_id__in=queryset.values(field))
    rows = memberships.filter(listed_content).values_list("content_id", "pulp_created")
    return {names[content_id]: created for content_id, created in rows if content_id in names}


def _marked_lookup(memberships, names, sources, variant):
    alias = memberships.db

    def mark(execute, sql, params, many, context):
        return execute(f"/* pexp={DIRECTORY_EXPERIMENT} v={variant} */ {sql}", params, many, context)

    with connections[alias].execute_wrapper(mark):
        memberships = memberships.using(alias)
        if variant == "A":
            return control_membership_dates(memberships, names)
        return candidate_membership_dates(memberships, names, sources)


def _ineligibility_reason(names, probability, revision):
    if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
        return "invalid_probability"
    if not isinstance(revision, str) or not revision.strip():
        return "missing_revision"
    if not names:
        return "empty"
    if len(names) != len(set(names.values())):
        return "shared_name"
    return None


def directory_membership_dates(version, names, sources, *, serving_mode, directory_count, is_root):
    """Run PULP-2505 only when configuration and displayed names are unambiguous."""
    memberships = version._content_relationships()
    if not getattr(settings, "CONTENT_DIRECTORY_AB_ENABLED", False):
        return control_membership_dates(memberships, names)

    probability = getattr(settings, "CONTENT_DIRECTORY_AB_PROBABILITY", 0.5)
    revision = getattr(settings, "CONTENT_DIRECTORY_AB_REVISION", "")
    context = {
        "serving_mode": serving_mode,
        "is_root": is_root,
        "candidate_count": len(names),
        "directory_count": directory_count,
        "deployment_revision": revision if isinstance(revision, str) else None,
    }
    if reason := _ineligibility_reason(names, probability, revision):
        logger.info(
            json.dumps(
                {
                    **context,
                    "event": "ab_experiment_skipped",
                    "exp_id": DIRECTORY_EXPERIMENT,
                    "reason": reason,
                    "correlation_id": get_guid(),
                }
            )
        )
        return control_membership_dates(memberships, names)

    return run_experiment(
        DIRECTORY_EXPERIMENT,
        lambda: _marked_lookup(memberships, names, sources, "A"),
        lambda: _marked_lookup(memberships, names, sources, "B"),
        p_candidate=probability,
        context=context,
    )
