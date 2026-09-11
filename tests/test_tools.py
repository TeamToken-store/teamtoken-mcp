"""What the tools promise the model, verified against a mocked gateway."""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastmcp.exceptions import ToolError

from tests.conftest import API_BASE

PNG = "iVBORw0KGgoAAAANSUhEUg=="


def texts(result) -> str:
    return " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")


@respx.mock
async def test_list_models_groups_by_modality_and_needs_no_key(client):
    respx.get(f"{API_BASE}/cabinet/api/public/media-models").mock(
        return_value=httpx.Response(200, json=[
            {"model": "nano-banana-pro", "modality": "image", "billing_unit": "per_image",
             "unit_price_usd": 0.027},
            {"model": "seedance-2-fast-720p", "modality": "video", "billing_unit": "per_second",
             "unit_price_usd": 0.18},
        ]))
    async with client:
        res = await client.call_tool("list_models", {})
    body = texts(res)
    assert "Image models (1)" in body and "Video models (1)" in body
    assert "per second" in body and "per image" in body
    # The catalog call must not carry the key: it is public, and the tool has to
    # answer even when the session is misconfigured.
    assert "authorization" not in {k.lower() for k in respx.calls[0].request.headers}


@respx.mock
async def test_generate_image_returns_the_picture_and_the_cost(client):
    respx.post(f"{API_BASE}/v1/images/generations").mock(
        return_value=httpx.Response(200, json={"cost_usd": "0.027", "data": [{"b64_json": PNG}]}))
    async with client:
        res = await client.call_tool("generate_image", {"prompt": "a red cube", "model": "nano-banana-pro"})
    assert any(getattr(c, "type", "") == "image" for c in res.content)
    assert "$0.027" in texts(res)


@respx.mock
async def test_slow_image_is_polled_not_abandoned(client):
    respx.post(f"{API_BASE}/v1/images/generations").mock(
        return_value=httpx.Response(202, json={"id": "img_1", "status": "processing"}))
    route = respx.get(f"{API_BASE}/v1/images/jobs/img_1").mock(side_effect=[
        httpx.Response(200, json={"id": "img_1", "status": "processing"}),
        httpx.Response(200, json={"id": "img_1", "status": "completed", "cost_usd": "0.04",
                                  "data": [{"b64_json": PNG}]}),
    ])
    async with client:
        res = await client.call_tool("generate_image", {"prompt": "a red cube", "model": "nano-banana-pro"})
    assert route.call_count == 2
    assert any(getattr(c, "type", "") == "image" for c in res.content)


@respx.mock
async def test_image_still_running_hands_back_the_job_id(client):
    respx.post(f"{API_BASE}/v1/images/generations").mock(
        return_value=httpx.Response(202, json={"id": "img_slow", "status": "processing"}))
    respx.get(f"{API_BASE}/v1/images/jobs/img_slow").mock(
        return_value=httpx.Response(200, json={"id": "img_slow", "status": "processing"}))
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("generate_image", {"prompt": "a red cube", "model": "nano-banana-pro"})
    # Losing the id would make the model generate — and pay — a second time.
    assert "img_slow" in str(exc.value)
    assert "do not generate again" in str(exc.value)


@respx.mock
async def test_video_returns_job_id_with_an_explicit_next_step(client):
    respx.post(f"{API_BASE}/v1/videos").mock(
        return_value=httpx.Response(202, json={"id": "vid_9", "status": "queued"}))
    async with client:
        res = await client.call_tool("generate_video", {"prompt": "a corgi surfing",
                                                        "model": "seedance-2-fast-720p"})
    body = texts(res)
    assert "vid_9" in body and "get_job" in body and "30 seconds" in body
    # wait=false is the whole point: the call must not block on the provider.
    sent = json.loads(respx.calls[0].request.read())
    assert sent["wait"] is False


@respx.mock
async def test_get_job_routes_video_ids_to_the_video_endpoint(client):
    respx.get(f"{API_BASE}/v1/videos/vid_9").mock(
        return_value=httpx.Response(200, json={
            "id": "vid_9", "status": "completed", "cost_usd": "0.9",
            "data": [{"url": f"{API_BASE}/v1/videos/vid_9/content"}]}))
    async with client:
        res = await client.call_tool("get_job", {"job_id": "vid_9"})
    body = texts(res)
    assert "/v1/videos/vid_9/content" in body and "$0.9" in body


@respx.mock
async def test_archived_result_is_not_reported_as_an_empty_generation(client):
    respx.get(f"{API_BASE}/v1/images/jobs/img_old").mock(
        return_value=httpx.Response(200, json={"id": "img_old", "status": "completed",
                                               "archived": True, "data": []}))
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("get_job", {"job_id": "img_old"})
    assert "retention" in str(exc.value)


@respx.mock
async def test_provider_error_carries_a_remedy_and_the_parked_job_id(client):
    respx.post(f"{API_BASE}/v1/videos").mock(return_value=httpx.Response(
        502, json={"error": {"message": "generation failed", "code": "INVALID_VIDEO_FILE",
                             "id": "vid_parked"}}))
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("generate_video", {"prompt": "edit this",
                                                      "model": "kling-3.0-edit-720p"})
    msg = str(exc.value)
    assert "INVALID_VIDEO_FILE" in msg
    assert "`video` input is required" in msg or "video` input is required" in msg
    assert "vid_parked" in msg


@respx.mock
async def test_insufficient_balance_says_what_to_do(client):
    respx.post(f"{API_BASE}/v1/images/generations").mock(
        return_value=httpx.Response(402, json={"error": {"message": "insufficient balance"}}))
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("generate_image", {"prompt": "a red cube", "model": "nano-banana-pro"})
    assert "get_balance" in str(exc.value)


@respx.mock
async def test_get_balance_reports_the_number(client):
    respx.get(f"{API_BASE}/v1/balance").mock(return_value=httpx.Response(
        200, json={"object": "balance", "granted": 10.0, "spend": 2.5, "balance": 7.5,
                   "currency": "USD"}))
    async with client:
        res = await client.call_tool("get_balance", {})
    assert "$7.5" in texts(res)


async def test_missing_credential_tells_the_user_how_to_fix_it(monkeypatch, client):
    """The stubbed `_resolve_key` is put back for this one test, so the message a
    user actually sees comes from the shipped function and not from a copy of it
    written in the test."""
    from teamtoken_mcp import server as srv

    from tests.conftest import REAL_RESOLVE

    # No key and no token — the state of a freshly added client.
    # `**_`: the stub has to accept what the real function accepts. A zero-arg
    # replacement would hide that the call site changed (include=…).
    monkeypatch.setattr(srv, "get_http_headers", lambda **_: {})
    monkeypatch.setattr(srv, "_resolve_key", REAL_RESOLVE)
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("get_balance", {})
    msg = str(exc.value)
    assert "x-api-key" in msg and "connector" in msg


@pytest.mark.parametrize("headers,expected", [
    ({"x-api-key": "sk-1"}, "sk-1"),
    ({"authorization": "Bearer sk-2"}, "sk-2"),
    ({"Authorization": "bearer sk-3"}, "sk-3"),   # case varies between hosts
])
def test_credential_is_read_from_both_header_shapes(headers, expected):
    """Hosts send the credential differently, and both forms must arrive at the
    same string: reading the wrong header gives a bare 401 far from the cause."""
    from teamtoken_mcp.auth import credential_from

    assert credential_from({k.lower(): v for k, v in headers.items()}) == expected


async def test_an_unexpected_failure_does_not_travel_to_the_user(monkeypatch, client):
    """The reader of an error is a model that will paraphrase it to a human, so
    whatever lands in that text is published. This branch catches ANY exception,
    including ones from other libraries, so only our own sentence goes out and
    the detail goes to the log."""
    from teamtoken_mcp import server as srv

    async def boom(*a, **k):
        raise RuntimeError("connect to https://api.internal:5432/?password=hunter2 failed")

    monkeypatch.setattr(srv.TeamTokenClient, "get", boom)
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("get_balance", {})
    msg = str(exc.value)
    assert "hunter2" not in msg and "api.internal" not in msg
    assert "TeamToken MCP server" in msg
