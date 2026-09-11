"""The defaults README advertises are the defaults the code has.

A self-hosted deployment is configured from that table and nothing else, so a
stale row is not a typo — it is instructions that produce a server behaving
unlike the one described. The table went stale exactly once, the moment the two
timeouts moved, and nothing noticed: prose does not fail a test run unless
something asserts it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from teamtoken_mcp.config import Settings

README = Path(__file__).resolve().parents[1] / "README.md"

# Environment variable -> the Settings field it fills. Only knobs with a real
# default belong here; secrets documented as "—" have nothing to compare.
DOCUMENTED: dict[str, str] = {
    "TEAMTOKEN_API_BASE": "api_base",
    "TEAMTOKEN_CABINET_BASE": "cabinet_base",
    "MCP_RESOURCE_URL": "resource_url",
    "MCP_IMAGE_WAIT_SECONDS": "image_wait_seconds",
    "MCP_IMAGE_POLL_INTERVAL": "image_poll_interval",
    "MCP_HTTP_TIMEOUT": "http_timeout",
    "MCP_PORT": "port",
}

_ROW = re.compile(r"^\|\s*`([A-Z_]+)`\s*\|\s*(.+?)\s*\|", re.MULTILINE)


def _documented_defaults() -> dict[str, str]:
    """VAR -> the default cell as written, backticks stripped."""
    return {var: cell.strip("` ") for var, cell in _ROW.findall(README.read_text(encoding="utf-8"))}


def _same(cell: str, value) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(cell) == float(value)
        except ValueError:
            return False
    return cell == str(value)


@pytest.mark.parametrize("var,field", sorted(DOCUMENTED.items()))
def test_readme_documents_the_real_default(var: str, field: str):
    cell = _documented_defaults().get(var)
    assert cell is not None, f"{var} is no longer in the README table"
    assert _same(cell, getattr(Settings(), field)), (
        f"README says {var} defaults to {cell!r}, the code says "
        f"{getattr(Settings(), field)!r}")


def test_every_documented_knob_is_checked():
    """A row added to the table without an entry above would go unguarded, which
    is the state this whole module exists to end."""
    unchecked = set(_documented_defaults()) - set(DOCUMENTED) - {"MCP_INTROSPECTION_SECRET",
                                                                 "MCP_REGISTRY_AUTH"}
    assert not unchecked, f"documented but not compared against the code: {sorted(unchecked)}"
