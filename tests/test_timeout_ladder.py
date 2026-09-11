"""The timeout ladder that makes the 202 branch of generate_image reachable.

This server cannot import the gateway's code, so the gateway's handback bound is
restated here as a constant — a deliberate twin, and twins drift unless something
asserts them. This one drifted once: `http_timeout` sat below the handback, so the
submit call died before the 202 + job id could exist, the polling branch in
`server.generate_image` became unreachable code for exactly the jobs it was
written for, and a slow generation reached the user as a dropped connection while
the job ran on and was billed.
"""
from __future__ import annotations

from teamtoken_mcp.config import Settings

# Twin of the gateway's `_POLL_MAX_SECONDS` in backend `app/media/router.py`:
# how long it polls the provider before handing back 202 + job id. Raising it
# there without raising `http_timeout` here reopens this bug.
GATEWAY_HANDBACK_SECONDS = 150.0

# How much of the wait budget must survive a worst-case handback, so that the
# poll that follows it has room to return an actual picture rather than an id.
MIN_POLL_ROOM_SECONDS = 60.0


def test_one_http_call_outlives_the_gateway_handback():
    """Below this the 202 never arrives and a slow job is billed and lost."""
    assert Settings().http_timeout > GATEWAY_HANDBACK_SECONDS


def test_wait_budget_leaves_room_to_poll_after_a_late_handback():
    """A budget that the handback alone exhausts turns every slow job into a
    bare job id — correct, but never the picture the tool promised."""
    settings = Settings()
    assert settings.image_wait_seconds - GATEWAY_HANDBACK_SECONDS >= MIN_POLL_ROOM_SECONDS


def test_env_defaults_match_the_field_defaults(monkeypatch):
    """`from_env()` spells every default a second time; a drift between the two
    means a deployment without the variable set behaves unlike the code says.

    The variables are cleared explicitly: a developer who has them exported would
    otherwise measure their shell instead of the code."""
    for var in ("MCP_HTTP_TIMEOUT", "MCP_IMAGE_WAIT_SECONDS", "MCP_IMAGE_POLL_INTERVAL"):
        monkeypatch.delenv(var, raising=False)
    declared = Settings()
    from_env = Settings.from_env()
    assert from_env.http_timeout == declared.http_timeout
    assert from_env.image_wait_seconds == declared.image_wait_seconds
    assert from_env.image_poll_interval == declared.image_poll_interval
