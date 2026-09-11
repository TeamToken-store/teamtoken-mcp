"""The OAuth path: what claude.ai needs and what a revoked grant does.

The header key keeps working — that is checked here too, because the whole point
of adding OAuth was to reach one more host, not to take anything away from the
ones that already work.
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx
import pytest
import respx
from fastmcp.exceptions import ToolError

from teamtoken_mcp.auth import BadCredential, NoCredential, credential_from, resolve_api_key
from tests.conftest import API_BASE, CABINET_BASE, REAL_RESOLVE

TOKEN = "tt_oat_abc123"


def test_credential_is_read_from_either_header():
    assert credential_from({"x-api-key": "sk-1"}) == "sk-1"
    assert credential_from({"authorization": "Bearer sk-2"}) == "sk-2"
    with pytest.raises(NoCredential):
        credential_from({})


async def test_a_plain_key_is_not_sent_to_introspection():
    # A key is already a key. A detour to the cabinet would add a network
    # dependency for clients that take no part in OAuth at all.
    assert await resolve_api_key("sk-plain") == "sk-plain"


@respx.mock
async def test_oauth_token_is_exchanged_for_the_grants_key():
    route = respx.post(f"{CABINET_BASE}/cabinet/api/oauth/introspect").mock(
        return_value=httpx.Response(200, json={"active": True, "sub": "7",
                                               "api_key": "sk-of-that-grant"}))
    assert await resolve_api_key(TOKEN) == "sk-of-that-grant"
    assert route.called


@respx.mock
async def test_revoked_token_says_reconnect_not_just_denied():
    respx.post(f"{CABINET_BASE}/cabinet/api/oauth/introspect").mock(
        return_value=httpx.Response(200, json={"active": False}))
    with pytest.raises(BadCredential) as exc:
        await resolve_api_key(TOKEN)
    assert "reconnect" in str(exc.value)


@respx.mock
async def test_cabinet_outage_is_not_reported_as_revocation():
    respx.post(f"{CABINET_BASE}/cabinet/api/oauth/introspect").mock(
        side_effect=httpx.ConnectError("boom"))
    with pytest.raises(BadCredential) as exc:
        await resolve_api_key(TOKEN)
    # Saying "access was revoked" when the outage is ours is a lie that sends
    # someone off to reconnect a connector that works.
    assert "unreachable" in str(exc.value)


@respx.mock
@pytest.mark.parametrize("status", [401, 403, 500])
async def test_a_refused_introspection_is_our_fault_not_a_revocation(status):
    """A non-200 from introspection means the shared secret is missing or wrong,
    or the cabinet is broken — our failure, not the user's.

    Measured before this branch existed: a deployment with no
    MCP_INTROSPECTION_SECRET got 401 from the cabinet and told every user their
    connection had been revoked. All of them would go and reconnect, and every
    reconnection would fail the same way, because nothing was wrong on their
    side at all.
    """
    respx.post(f"{CABINET_BASE}/cabinet/api/oauth/introspect").mock(
        return_value=httpx.Response(status, json={"detail": "nope"}))
    with pytest.raises(BadCredential) as exc:
        await resolve_api_key(TOKEN)
    msg = str(exc.value)
    assert "unavailable" in msg
    assert "revoked" not in msg and "reconnect" not in msg


@respx.mock
async def test_tool_call_over_oauth_spends_the_grants_key(monkeypatch, client):
    from teamtoken_mcp import server as srv

    monkeypatch.setattr(srv, "get_http_headers", lambda **_: {"authorization": f"Bearer {TOKEN}"})
    monkeypatch.setattr(srv, "_resolve_key", REAL_RESOLVE)
    respx.post(f"{CABINET_BASE}/cabinet/api/oauth/introspect").mock(
        return_value=httpx.Response(200, json={"active": True, "api_key": "sk-of-that-grant"}))
    balance = respx.get(f"{API_BASE}/v1/balance").mock(return_value=httpx.Response(
        200, json={"granted": 5.0, "spend": 1.0, "balance": 4.0}))
    async with client:
        await client.call_tool("get_balance", {})
    assert balance.calls.last.request.headers["authorization"] == "Bearer sk-of-that-grant"


@respx.mock
async def test_revoked_grant_surfaces_as_a_tool_error(monkeypatch, client):
    from teamtoken_mcp import server as srv

    monkeypatch.setattr(srv, "get_http_headers", lambda **_: {"authorization": f"Bearer {TOKEN}"})
    monkeypatch.setattr(srv, "_resolve_key", REAL_RESOLVE)
    respx.post(f"{CABINET_BASE}/cabinet/api/oauth/introspect").mock(
        return_value=httpx.Response(200, json={"active": False}))
    async with client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("get_balance", {})
    assert "reconnect" in str(exc.value)


# -- HTTP layer -------------------------------------------------------------

@pytest.fixture
def app():
    from teamtoken_mcp.server import build_app

    return build_app()


async def _request(app, path: str, headers: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        return await c.post(path, headers=headers or {}, json={})


async def test_no_credential_gets_401_pointing_at_the_metadata(app):
    resp = await _request(app, "/mcp")
    # A client reads 403 as "closed for good" and never asks again; a 401 with
    # this header tells it to begin authorization (RFC 9728). One status code
    # apart is the difference between a connector that connects and one that
    # does not.
    assert resp.status_code == 401
    auth = resp.headers["www-authenticate"]
    assert auth.startswith("Bearer resource_metadata=")
    assert "/.well-known/oauth-protected-resource" in auth


async def test_the_advertised_metadata_url_answers(app):
    """The 401 header has to lead where the metadata ACTUALLY is.

    The first version built the address as `{resource_url}/.well-known/…`, i.e.
    under `/mcp` — under the very 401 that issued it. The client was told to go
    and authorize and was then refused at the document it authorizes from: the
    browser flow never started, and it looked like a broken connector. The check
    follows the LINK out of the header rather than a path known in advance, or
    it drifts away from it silently.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        resp = await c.post("/mcp", json={})
        url = resp.headers["www-authenticate"].split('resource_metadata="')[1].rstrip('"')
        got = await c.get(urlparse(url).path)
    assert got.status_code == 200, f"{url} -> {got.status_code}"
    assert got.json()["authorization_servers"] == ["https://app.teamtoken.store"]


async def test_protected_resource_metadata_points_at_the_cabinet(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        resp = await c.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorization_servers"] == ["https://app.teamtoken.store"]
    assert body["scopes_supported"] == ["media"]


async def test_discovery_files_are_not_behind_the_401(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        for path in ("/healthz", "/.well-known/mcp/server-card.json",
                     "/.well-known/mcp-registry-auth"):
            # Registries and scanners come here without a key; a 401 on the card
            # would mean a listing that silently never appears.
            assert (await c.get(path)).status_code == 200, path


@respx.mock
async def test_introspection_carries_the_service_secret(monkeypatch):
    """The cabinet answers introspection only to this service. Without the header
    the exchange returns 401 and every OAuth session silently stops working."""
    from teamtoken_mcp import config

    monkeypatch.setattr(config, "_settings", config.Settings(
        api_base=API_BASE, cabinet_base=CABINET_BASE, introspection_secret="s3cr3t"))
    route = respx.post(f"{CABINET_BASE}/cabinet/api/oauth/introspect").mock(
        return_value=httpx.Response(200, json={"active": True, "api_key": "sk-grant"}))
    assert await resolve_api_key(TOKEN) == "sk-grant"
    assert route.calls.last.request.headers["authorization"] == "Bearer s3cr3t"
