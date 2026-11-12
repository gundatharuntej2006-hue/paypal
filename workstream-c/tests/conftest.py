"""Local HTTP fixtures for agent integration tests; no external services."""

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest

from agentguard_workstream_c.merchants.server import MerchantHandler


@contextmanager
def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def agent_server():
    with serve(MerchantHandler) as url:
        yield url


@pytest.fixture
def server_factory():
    return serve
