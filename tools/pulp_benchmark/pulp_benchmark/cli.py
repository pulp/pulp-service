# pulp_benchmark/cli.py
import asyncio
import logging

import click

from .client import get_system_status

# Configure logging, as it's a global setting
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@click.group()
@click.option('--api-root', envvar='PULP_API_ROOT', required=True, help='Root URL of the Pulp API.')
@click.option('--user', envvar='PULP_USER', default='admin', show_default=True, help='Username for API auth.')
@click.option('--password', envvar='PULP_PASSWORD', required=True, prompt=True, hide_input=True, help='Password for API auth.')
@click.pass_context
def cli(ctx, api_root, user, password):
    """A tool for load testing and analyzing a Pulp tasking system using async I/O."""
    ctx.obj = {'api_root': api_root, 'user': user, 'password': password}
    asyncio.run(get_system_status(api_root))
