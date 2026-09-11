"""Two ways in, one result: the API key this call may spend.

A raw TeamToken key works everywhere a client can set a header — Claude Code,
Cursor, Cline. It cannot reach claude.ai, whose connector form offers OAuth and
nowhere to put a header, so an OAuth token is accepted too and exchanged for the
key of the grant behind it.

Telling the two apart is a prefix, not a guess: OAuth tokens are minted as
`tt_oat_…`. Guessing would fail in the worst way — an OAuth token sent to the
gateway as a key comes back as a bare 401, with nothing pointing at the cause.
"""
from __future__ import annotations

import logging

import httpx

from teamtoken_mcp.config import get_settings

log = logging.getLogger("teamtoken-mcp")

OAUTH_PREFIX = "tt_oat_"


class NoCredential(Exception):
    """No key and no token at all — the client has not been configured yet."""


class BadCredential(Exception):
    """A credential arrived and is not usable (revoked, expired, unknown)."""


def credential_from(headers: dict[str, str]) -> str:
    key = (headers.get("x-api-key") or "").strip()
    if key:
        return key
    auth = (headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    raise NoCredential()


async def resolve_api_key(credential: str, http: httpx.AsyncClient | None = None) -> str:
    """The key to spend. An API key is itself; an OAuth token is introspected."""
    if not credential.startswith(OAUTH_PREFIX):
        return credential
    settings = get_settings()
    client = http or httpx.AsyncClient(timeout=15.0)
    try:
        headers = ({"Authorization": f"Bearer {settings.introspection_secret}"}
                   if settings.introspection_secret else {})
        resp = await client.post(f"{settings.cabinet_base}/cabinet/api/oauth/introspect",
                                 json={"token": credential}, headers=headers)
        # A non-200 is OUR failure, not the user's, and the difference decides
        # what they are told. 401 or 403 means the shared secret is missing or
        # wrong on one of the two sides; 5xx means the cabinet is broken. Folding
        # those into "not active" — which is what reading `resp.status_code ==
        # 200 else {}` did — announced a revoked connection to every user of a
        # misconfigured deployment, and sent all of them off to reconnect, which
        # would fail in exactly the same way. Measured: with no secret set the
        # cabinet answers 401 and the user was told "revoked or expired".
        if resp.status_code != 200:
            log.error("oauth introspection refused: HTTP %s from the cabinet — check "
                      "MCP_INTROSPECTION_SECRET on both sides", resp.status_code)
            raise BadCredential("the authorization service is unavailable, try again shortly")
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        log.warning("oauth introspection failed", exc_info=True)
        # Deliberately not "invalid token": the cabinet being unreachable is our
        # outage, and telling the user their access was revoked would be a lie.
        raise BadCredential("the authorization service is unreachable, try again shortly")
    finally:
        if http is None:
            await client.aclose()
    if not body.get("active") or not body.get("api_key"):
        raise BadCredential("this connection was revoked or has expired — reconnect the "
                            "TeamToken connector to grant access again")
    return str(body["api_key"])
