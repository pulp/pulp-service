# pulp_benchmark/cli.py
import asyncio
import logging

import click

# Import both sync and async clients
from .client_async import get_system_status as get_system_status_async
from .client_sync import get_system_status_sync

# Configure logging, as it's a global setting
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@click.group()
@click.option('--api-root', envvar='PULP_API_ROOT', required=True, help='Root URL of the Pulp API.')
@click.option('--user', envvar='PULP_USER', default='admin', show_default=True, help='Username for API auth.')
@click.option('--password', envvar='PULP_PASSWORD', help='Password for API auth.')
@click.option('--cert', type=click.Path(exists=True), help='Path to client certificate.')
@click.option('--key', type=click.Path(exists=True), help='Path to client certificate key.')
@click.option('--verify-ssl/--no-verify-ssl', default=True, show_default=True, help='Verify SSL certificates (use --no-verify-ssl for self-signed certs).')
@click.option('--client', type=click.Choice(['async', 'sync']), default='async', show_default=True, help='The HTTP client to use for requests.')
@click.pass_context
def cli(ctx, api_root, user, password, cert, key, verify_ssl, client):
    """A tool for load testing and analyzing a Pulp tasking system."""
    # Validate authentication options
    if not password and not (cert and key):
        raise click.UsageError('Either --password or both --cert and --key must be provided.')

    # The context object now holds the chosen client type for plugins to use
    ctx.obj = {
        'api_root': api_root,
        'user': user,
        'password': password,
        'cert': cert,
        'key': key,
        'verify_ssl': verify_ssl,
        'client_type': client
    }

    # Run the appropriate status check based on the chosen client
    if client == 'async':
        asyncio.run(get_system_status_async(api_root, user=user, password=password, cert=cert, key=key, verify_ssl=verify_ssl))
    else:
        get_system_status_sync(api_root, user=user, password=password, cert=cert, key=key, verify_ssl=verify_ssl)
