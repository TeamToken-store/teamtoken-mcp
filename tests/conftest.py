"""Shared fixtures.

Tools are exercised through an in-memory MCP client, i.e. the same path a real
client takes — schema validation included. The one thing that has no equivalent
in-memory is the HTTP request, so `_resolve_key` is stubbed; everything else is real.
"""
from __future__ import annotations

import pytest

from teamtoken_mcp import server

API_BASE = "https://api.teamtoken.store"
CABINET_BASE = "https://app.teamtoken.store"

# Captured before the autouse fixture replaces the module attribute: a test that
# needs the REAL function would otherwise get the stub and recurse into it.
REAL_RESOLVE = server._resolve_key


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    async def _fixed() -> str:
        return "sk-test"

    monkeypatch.setattr(server, "_resolve_key", _fixed)


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch):
    """Keep the poll loop from turning a unit test into a wall-clock test."""
    from teamtoken_mcp import config

    monkeypatch.setattr(config, "_settings", config.Settings(
        api_base=API_BASE, cabinet_base=CABINET_BASE, image_wait_seconds=1.0,
        image_poll_interval=0.01, http_timeout=5.0))


@pytest.fixture
def client():
    from fastmcp import Client

    return Client(server.mcp)
