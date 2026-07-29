"""
Django management command to generate .triage/urls.json.

Run from within a Pulp Django environment:

    django-admin triage_urls --output .triage/urls.json
"""

import json
import sys

from django.core.management.base import BaseCommand
from django.urls import URLPattern, URLResolver, get_resolver


class Command(BaseCommand):
    help = "Export URL routing map for triage test impact analysis"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Output file path (default: stdout)",
        )

    def handle(self, *args, **options):
        resolver = get_resolver()
        routes = []

        self._collect_routes(resolver, "", routes)
        routes.sort(key=lambda route: route["pattern"])

        output = json.dumps(routes, indent=2)
        if options["output"]:
            with open(options["output"], "w") as output_file:
                output_file.write(output)
            self.stderr.write(f"Wrote {len(routes)} routes to {options['output']}")
        else:
            sys.stdout.write(output)

    def _collect_routes(self, resolver, prefix, routes):
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                new_prefix = prefix + str(pattern.pattern)
                self._collect_routes(pattern, new_prefix, routes)
            elif isinstance(pattern, URLPattern):
                route = self._extract_route(pattern, prefix)
                if route:
                    routes.append(route)

    def _extract_route(self, pattern, prefix):
        full_pattern = prefix + str(pattern.pattern)
        callback = pattern.callback

        viewset_class = _resolve_viewset_class(callback)
        if viewset_class is None:
            return None

        dotted_path = f"{viewset_class.__module__}.{viewset_class.__qualname__}"
        actions = _resolve_actions(callback)

        return {
            "pattern": "/" + full_pattern.lstrip("/"),
            "viewset": dotted_path,
            "actions": sorted(set(actions)),
        }


def _resolve_viewset_class(callback):
    if hasattr(callback, "cls"):
        return callback.cls
    if hasattr(callback, "view_class"):
        return callback.view_class
    if hasattr(callback, "initkwargs") and "cls" in callback.initkwargs:
        return callback.initkwargs["cls"]
    return None


def _resolve_actions(callback):
    if hasattr(callback, "actions"):
        return list(callback.actions.values())
    if hasattr(callback, "initkwargs"):
        actions_map = callback.initkwargs.get("actions", {})
        if isinstance(actions_map, dict):
            return list(actions_map.values())
    return []
