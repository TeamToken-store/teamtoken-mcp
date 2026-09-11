"""The credential a real client sends over real HTTP reaches the gateway.

Every other test in this suite reaches the tools in-memory and monkeypatches
`get_http_headers`, so the one thing none of them could see is what FastMCP
actually hands us at runtime — and that is exactly where it broke: FastMCP
strips credential headers (`authorization`, `cookie`) by default, "because they
cause issues if forwarded to downstream services". A key sent as
`Authorization: Bearer sk-…` — the form our own docs give for Claude Code and
Cursor — never arrived, and neither did the OAuth token from claude.ai. The user
saw "no credentials in this session" with a correctly configured client.

So this file deliberately takes the long way round: it drives the published ASGI
app over the streamable-http transport, speaking the protocol the way a client
does, and asserts on what the GATEWAY received. A stub in place of
`get_http_headers` would make it green again while the bug is back.
"""
from __future__ import annotations

import contextlib
import json

import httpx
import pytest
import respx

from tests.conftest import API_BASE, REAL_RESOLVE

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app(monkeypatch):
    """The conftest autouse fixture replaces `_resolve_key` with a fixed key; here
    we put the real one back, or the test would exercise the stub instead of the
    header parsing — exactly what the earlier tests could not see."""
    from teamtoken_mcp import server as srv

    monkeypatch.setattr(srv, "_resolve_key", REAL_RESOLVE)
    return srv.build_app()


def _sse_json(text: str) -> dict:
    """The transport answers in text/event-stream even for a single reply."""
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"no data line in the response: {text[:200]}")


@contextlib.asynccontextmanager
async def _serving(app):
    """The streamable-http transport lives in a task group the lifespan starts.
    ASGITransport never calls it, so we call it ourselves — otherwise the first
    tools/call dies with "Task group is not initialized", which reads as a
    refusal of ours."""
    inner = getattr(app, "app", app)
    async with inner.router.lifespan_context(inner):
        yield app


async def _call_get_balance(app, headers: dict[str, str]) -> dict:
    """initialize → initialized → tools/call, the way a real client does it."""
    base = {"content-type": "application/json",
            "accept": "application/json, text/event-stream", **headers}
    transport = httpx.ASGITransport(app=app)
    async with _serving(app), httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        init = await c.post("/mcp", headers=base, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}})
        assert init.status_code == 200, init.text
        sid = init.headers["mcp-session-id"]
        live = {**base, "mcp-session-id": sid}
        await c.post("/mcp", headers=live,
                     json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        resp = await c.post("/mcp", headers=live, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_balance", "arguments": {}}})
    assert resp.status_code == 200, resp.text
    return _sse_json(resp.text)


@respx.mock
@pytest.mark.parametrize("headers,sent", [
    ({"authorization": "Bearer sk-bearer"}, "sk-bearer"),
    ({"x-api-key": "sk-header"}, "sk-header"),
])
async def test_the_key_from_the_request_reaches_the_gateway(app, headers, sent):
    route = respx.get(f"{API_BASE}/v1/balance").mock(
        return_value=httpx.Response(200, json={"balance_usd": 7.5}))

    body = await _call_get_balance(app, headers)

    assert route.called, f"the gateway was never called at all: {body}"
    assert route.calls.last.request.headers["authorization"] == f"Bearer {sent}"
    assert "isError" not in body.get("result", {}) or not body["result"]["isError"], body


@respx.mock
async def test_without_a_credential_the_request_never_reaches_the_gateway(app):
    """The refusal has to happen at the door. A keyless request that reaches the
    gateway is a wasted network call and a 401 from there — the cause reported
    far from where it is."""
    route = respx.get(f"{API_BASE}/v1/balance")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        resp = await c.post("/mcp", headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream"}, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}}})

    assert resp.status_code == 401
    assert not route.called
