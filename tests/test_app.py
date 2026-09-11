"""The ASGI app actually starts and serves the three non-MCP routes.

These exist because of other people's infrastructure — a health probe, the
registry's domain proof, Smithery's metadata scan — and each of them is invisible
until it is missing, at which point a listing silently fails to appear.
"""
from __future__ import annotations

import re

import httpx
import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("MCP_REGISTRY_AUTH", "proof-token")
    from teamtoken_mcp.server import build_app

    return build_app()


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        return await c.get(path)


async def test_healthz(app):
    resp = await _get(app, "/healthz")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


async def test_server_card_advertises_the_remote_endpoint(app):
    resp = await _get(app, "/.well-known/mcp/server-card.json")
    assert resp.status_code == 200
    card = resp.json()
    remote = card["remotes"][0]
    assert remote["type"] == "streamable-http"
    assert remote["url"] == "https://teamtoken.store/mcp"
    # A card that omits how to authenticate makes the listing look keyless.
    assert card["authentication"]["name"] == "x-api-key"


async def test_registry_auth_serves_the_domain_proof(app):
    resp = await _get(app, "/.well-known/mcp-registry-auth")
    assert resp.status_code == 200
    assert resp.text.strip() == "proof-token"


async def test_the_icon_is_served_and_announced(app):
    """The connector tile comes from serverInfo instead of the client guessing.

    The check follows the LINK we announce rather than a path it knows in
    advance: announcing one address and serving another is exactly what shows up
    as a borrowed white placeholder instead of our mark, and exactly what a
    check with a hardcoded path cannot see.
    """
    from urllib.parse import urlparse

    from teamtoken_mcp.server import mcp

    assert mcp.icons, "serverInfo with no icons — the client will draw the tile itself"
    # The version in the URL is what defeats the WAF cache: without it the old
    # image keeps arriving for a day after a release, which reads as "the icon
    # was never fixed".
    from teamtoken_mcp import __version__
    assert all(f"v={__version__}" in i.src for i in mcp.icons)
    for icon in mcp.icons:
        resp = await _get(app, urlparse(icon.src).path)
        assert resp.status_code == 200, icon.src
        assert resp.headers["content-type"].startswith("image/png")
        # A PNG starts with an eight-byte signature: a 200 carrying the SPA's
        # HTML would look just as successful, and that is exactly how
        # /favicon.ico behaved.
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


async def test_server_info_carries_our_version_not_the_frameworks():
    """serverInfo carries FastMCP's version by default and the client shows it as
    ours — a number that says nothing about which build is answering."""
    from teamtoken_mcp import __version__
    from teamtoken_mcp.server import mcp

    assert mcp.version == __version__


async def test_a_browser_gets_a_page_and_a_client_still_gets_its_401(app):
    """A human who opens the address by hand gets instructions; a client gets the
    same JSON as before.

    ⚠️ The assertion about the status and the header matters more than the page
    itself: answering a browser with 200 would trade a working connection for
    the look of it, because Claude does not read WWW-Authenticate on a 200 and
    the 401 is what tells a client to begin authorization.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        html = await c.get("/mcp", headers={
            "accept": "text/html,application/xhtml+xml", "accept-language": "ru-RU,ru;q=0.9"})
        api = await c.post("/mcp", headers={"accept": "application/json, text/event-stream"},
                           json={})

    assert html.status_code == 401 and api.status_code == 401
    for r in (html, api):
        assert r.headers["www-authenticate"].startswith("Bearer resource_metadata=")
    assert html.headers["content-type"].startswith("text/html")
    assert "mcpServers" in html.text and "TeamToken MCP" in html.text
    # Russian follows Accept-Language, because the address lives in configs in
    # both languages and guessing by country is not something we can do.
    assert "машинный адрес" in html.text
    assert api.headers["content-type"] == "application/json"
    assert api.json()["error"] == "unauthorized"


async def test_a_client_sending_both_types_still_gets_json(app):
    """A client that sends both html and the event stream must still get JSON:
    its parser breaks on our markup, and that looks like our refusal."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        r = await c.post("/mcp", headers={"accept": "text/html, text/event-stream"}, json={})
    assert r.headers["content-type"] == "application/json"


async def test_the_page_carries_every_client_shape_it_promises(app):
    """The config shapes DIFFER, and not cosmetically: VS Code's root key is
    `servers`, not `mcpServers`, and without `"type": "http"` it takes the URL
    for a command and tries to run it. Codex reads TOML. One "universal" example
    would silently fail for two clients out of three."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as c:
        html = (await c.get("/mcp", headers={"accept": "text/html"})).text

    assert '"mcpServers"' in html and '"servers"' in html
    assert "[mcp_servers.teamtoken]" in html and "bearer_token_env_var" in html
    assert html.count('<pre data-i=') == 4
    # The first block carries no credential at all, and the ORDER is the
    # assertion: a client that speaks OAuth needs nothing created beforehand,
    # and putting the keyed config first silently teaches that a key is
    # mandatory.
    first = re.search(r'<pre data-i="0"[^>]*>(.*?)</pre>', html, re.S).group(1)
    assert '"url"' in first and "x-api-key" not in first
    # The copy button is why the page was revised a second time.
    assert 'id="copy"' in html
