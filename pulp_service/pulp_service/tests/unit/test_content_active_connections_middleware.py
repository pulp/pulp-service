"""
track_active_connections: +1 on enter, -1 in finally.

Two cases only — they are the two ways the handler can finish. 404 is the
same finally path as 302 and is not tested separately.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from pulp_service.app.content import track_active_connections

WORKER_NAME = "1@content-host"


def _adds(counter):
    return [(c.args[0], c.kwargs["attributes"]) for c in counter.add.call_args_list]


@pytest.mark.asyncio
async def test_balanced_when_handler_returns():
    counter = MagicMock()
    middleware = track_active_connections(counter)
    handler = AsyncMock(return_value=web.Response())

    with patch("pulp_service.app.content.get_worker_name", return_value=WORKER_NAME):
        response = await middleware(MagicMock(), handler)

    assert response.status == 200
    assert _adds(counter) == [
        (1, {"worker.name": WORKER_NAME}),
        (-1, {"worker.name": WORKER_NAME}),
    ]


@pytest.mark.asyncio
async def test_balanced_when_handler_raises_http_found():
    """S3 redirect: pulpcore raises HTTPFound. Must still -1 and not swallow it."""
    counter = MagicMock()
    middleware = track_active_connections(counter)
    handler = AsyncMock(side_effect=web.HTTPFound(location="https://s3.example.com/artifact"))

    with (
        patch("pulp_service.app.content.get_worker_name", return_value=WORKER_NAME),
        pytest.raises(web.HTTPFound),
    ):
        await middleware(MagicMock(), handler)

    assert _adds(counter) == [
        (1, {"worker.name": WORKER_NAME}),
        (-1, {"worker.name": WORKER_NAME}),
    ]
