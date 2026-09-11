"""Regressions for the findings of an adversarial review of this server.

Each test names the failure it prevents rather than the code it calls: these are
the cases the implementation looked correct for and was not.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from fastmcp.exceptions import ToolError

from tests.conftest import API_BASE

PNG = "iVBORw0KGgoAAAANSUhEUg=="
JPEG = "/9j/4AAQSkZJRgABAQAA"


@pytest.fixture
def app():
    from teamtoken_mcp.server import build_app

    return build_app()


async def test_transport_answers_on_the_published_path(app):
    """The reverse proxy forwards /mcp unchanged; no prefix is stripped.

    A transport mounted at "/" would answer 404 on the published address, and the
    in-memory client the other tests use never sees that path at all.

    The app is started with its lifespan: streamable-http without it fails with
    "Task group is not initialized", so a test that skipped the lifespan would be
    checking something other than what runs in production.
    """
    inner = app.app                      # unwrap RequireCredential
    async with inner.router.lifespan_context(inner):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
            resp = await c.post("/mcp", headers={
                "x-api-key": "sk-1",
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            }, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18",
                                "capabilities": {},
                                "clientInfo": {"name": "t", "version": "1"}}})
    assert resp.status_code != 404, "the transport does not listen on the published path"
    assert resp.status_code < 500, resp.text[:200]


async def test_no_credential_is_401_at_the_http_boundary(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        resp = await c.post("/mcp", json={})
    # A client reads 403 as "closed for good" and never asks again.
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Bearer")


@respx.mock
async def test_dropped_connection_on_submit_forbids_a_retry(client):
    """A dropped submit is ambiguous: the job may have been created and billed.

    "The gateway could not be reached" invites the model to try again, and the
    retry pays for a second generation.
    """
    respx.post(f"{API_BASE}/v1/videos").mock(side_effect=httpx.ConnectError("boom"))
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("generate_video", {"prompt": "a corgi",
                                                      "model": "seedance-2-fast-720p"})
    msg = str(exc.value)
    assert "do NOT submit again" in msg
    assert "billed" in msg


@respx.mock
async def test_success_without_a_job_id_does_not_ask_for_a_retry(client):
    respx.post(f"{API_BASE}/v1/videos").mock(
        return_value=httpx.Response(202, json={"status": "queued"}))
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("generate_video", {"prompt": "a corgi",
                                                      "model": "seedance-2-fast-720p"})
    assert "do NOT submit again" in str(exc.value)


@respx.mock
async def test_jpeg_result_is_not_labelled_png(client):
    """A client that trusts the MIME type would show a broken image the user paid for."""
    respx.post(f"{API_BASE}/v1/images/generations").mock(
        return_value=httpx.Response(200, json={"cost_usd": "0.02",
                                               "data": [{"b64_json": JPEG}]}))
    async with client:
        res = await client.call_tool("generate_image", {"prompt": "a red cube",
                                                        "model": "nano-banana-pro"})
    images = [c for c in res.content if getattr(c, "type", "") == "image"]
    mime = getattr(images[0], "mime_type", None) or images[0].mimeType
    assert images and mime == "image/jpeg"


@respx.mock
async def test_completed_job_without_a_result_stops_the_polling_loop(client):
    """A terminal status with no data: telling the model to wait would loop it
    forever over a job whose answer will not change."""
    respx.get(f"{API_BASE}/v1/videos/vid_empty").mock(
        return_value=httpx.Response(200, json={"id": "vid_empty", "status": "completed",
                                               "data": []}))
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("get_job", {"job_id": "vid_empty"})
    assert "no result" in str(exc.value)


@respx.mock
async def test_transport_failure_while_polling_keeps_the_job_id(client):
    """The submit succeeded and is being paid for; a failing poll says nothing
    about it. Letting the error out would replace a known id with "something
    went wrong" and leave the user unable to collect what they bought."""
    respx.post(f"{API_BASE}/v1/images/generations").mock(
        return_value=httpx.Response(202, json={"id": "img_paid", "status": "processing"}))
    respx.get(f"{API_BASE}/v1/images/jobs/img_paid").mock(
        side_effect=httpx.ReadTimeout("slow"))
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("generate_image", {"prompt": "a red cube",
                                                      "model": "nano-banana-pro"})
    assert "img_paid" in str(exc.value)


def test_gateway_address_that_would_leak_keys_is_refused():
    """TEAMTOKEN_API_BASE is not a routine setting: every authenticated call
    sends the end user's key to whatever it names."""
    from teamtoken_mcp.client import TeamTokenClient

    TeamTokenClient.assert_safe_base("https://api.teamtoken.store")   # ok
    TeamTokenClient.assert_safe_base("http://localhost:8000")         # ok, no network
    for bad in ("http://evil.example", "https://evil.example"):
        with pytest.raises(ValueError):
            TeamTokenClient.assert_safe_base(bad)


async def test_non_bearer_authorization_does_not_pass_the_door(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        resp = await c.post("/mcp", headers={"authorization": "Basic x"}, json={})
    # This header used to pass the handshake and fail later inside every tool,
    # so the client believed the session worked and never asked for credentials.
    assert resp.status_code == 401
