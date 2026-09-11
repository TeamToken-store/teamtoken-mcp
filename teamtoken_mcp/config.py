"""Settings, all from the environment.

Every value has a default that works against the public gateway, so a bare
`docker run` is a working server. Deployment overrides only what differs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # The gateway this server proxies to. Never the cabinet host: /v1/images and
    # /v1/videos are served on api.* only.
    api_base: str = "https://api.teamtoken.store"

    # The cabinet, which is also the OAuth authorization server. Used for
    # one call only — turning an OAuth token into the key of the grant behind it.
    cabinet_base: str = "https://app.teamtoken.store"

    # Shared secret proving to the cabinet that an introspection call comes from
    # this service. It lives in the environment, never in this repository — which
    # is what lets the repository be public while the endpoint stays closed.
    introspection_secret: str = ""

    # What this server calls itself in RFC 9728 metadata. Must be the URL clients
    # actually reach, or the 401 sends them to authorise against the wrong thing.
    resource_url: str = "https://teamtoken.store/mcp"

    # Images are answered synchronously (see tools.generate_image). The gateway
    # itself may still hand back 202 for a slow provider, so we poll — but only
    # up to this budget, after which the job id is returned instead of a broken
    # promise. Kept well under the 10 min that MCP clients typically allow, and
    # far enough above the handback below that a slow provider still resolves to
    # a picture rather than to a bare job id.
    image_wait_seconds: float = 300.0
    image_poll_interval: float = 2.0

    # One HTTP call to the gateway. Image generation blocks on the provider, so
    # this must exceed a normal generation; the wait budget above bounds the
    # whole tool call.
    #
    # It must ALSO outlive the gateway's own poll ceiling, and that is what makes
    # the 202 branch above reachable at all: the gateway waits on the provider
    # for a bounded while and only then hands back 202 + job id. A client that
    # gives up first never sees that answer, so a slow generation surfaces as a
    # dropped connection while the job runs on and is billed — the polling path
    # becomes unreachable code for exactly the jobs it exists for. See
    # GATEWAY_HANDBACK_SECONDS in tests/test_timeout_ladder.py for the bound this
    # has to clear.
    http_timeout: float = 200.0

    port: int = 8080

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            api_base=(os.environ.get("TEAMTOKEN_API_BASE") or "https://api.teamtoken.store").rstrip("/"),
            cabinet_base=(os.environ.get("TEAMTOKEN_CABINET_BASE") or "https://app.teamtoken.store").rstrip("/"),
            introspection_secret=(os.environ.get("MCP_INTROSPECTION_SECRET") or "").strip(),
            resource_url=(os.environ.get("MCP_RESOURCE_URL") or "https://teamtoken.store/mcp").rstrip("/"),
            image_wait_seconds=_f("MCP_IMAGE_WAIT_SECONDS", 300.0),
            image_poll_interval=_f("MCP_IMAGE_POLL_INTERVAL", 2.0),
            http_timeout=_f("MCP_HTTP_TIMEOUT", 200.0),
            port=int(_f("MCP_PORT", 8080)),
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings
