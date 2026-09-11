"""ASGI entrypoint: `uvicorn teamtoken_mcp.app:app`.

Built at import time so the process fails at startup, not on the first request,
if a setting is wrong.
"""
from teamtoken_mcp.client import TeamTokenClient
from teamtoken_mcp.config import get_settings
from teamtoken_mcp.server import build_app

# Checked before the first request rather than on the first authenticated call:
# a gateway address that would receive users' keys must fail the deployment, not
# a user's generation.
TeamTokenClient.assert_safe_base(get_settings().api_base)

app = build_app()
