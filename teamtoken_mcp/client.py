"""Thin async HTTP client for the TeamToken gateway.

One place owns the wire contract — auth header, the `{"error": {...}}` envelope,
the 200-vs-202 split and job polling — so the tool functions stay declarative.
Nothing here knows about MCP.

Contract reference: the TeamToken API guide at https://teamtoken.store/docs.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from teamtoken_mcp.config import Settings, get_settings
from teamtoken_mcp.errors import TeamTokenError, envelope, humanize

# Terminal job states, per the gateway's media lifecycle.
DONE = {"completed", "succeeded", "success"}
FAILED = {"failed", "error", "canceled", "cancelled"}


# One client for the whole process, not one per tool call: an AsyncClient owns a
# connection pool, and creating a fresh one per call leaked pools and sockets
# until the garbage collector got round to them. Closed only at process exit,
# which is exactly the lifetime of this server.
_SHARED: httpx.AsyncClient | None = None


def shared_http(timeout: float) -> httpx.AsyncClient:
    global _SHARED
    if _SHARED is None or _SHARED.is_closed:
        _SHARED = httpx.AsyncClient(timeout=timeout)
    return _SHARED


async def close_shared_http() -> None:
    global _SHARED
    if _SHARED is not None and not _SHARED.is_closed:
        await _SHARED.aclose()
    _SHARED = None


class TeamTokenClient:
    def __init__(self, api_key: str, settings: Settings | None = None,
                 http: httpx.AsyncClient | None = None,
                 deadline: float | None = None):
        self.settings = settings or get_settings()
        self.api_key = api_key
        # An injected client is what the tests drive through respx; otherwise the
        # process-wide pool above.
        self._http = http
        # Monotonic point in time after which this tool call must be over. Without
        # it the advertised wait budget bounds only the polling loop, and a slow
        # POST plus a slow final GET can push a "180 second" call past seven
        # minutes — long enough for the client to cut the call and lose the id of
        # a job that was already paid for.
        self.deadline = deadline

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = shared_http(self.settings.http_timeout)
        return self._http

    def _timeout(self) -> float:
        if self.deadline is None:
            return self.settings.http_timeout
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TeamTokenError("the tool call ran out of its time budget",
                                 status=None)
        return min(self.settings.http_timeout, left)

    def _url(self, path: str) -> str:
        return f"{self.settings.api_base}/{path.lstrip('/')}"

    @staticmethod
    def assert_safe_base(base: str) -> None:
        """Refuse to attach the caller's key to a plaintext or foreign endpoint.

        `TEAMTOKEN_API_BASE` is not a routine setting: every authenticated call
        sends the END USER's key to whatever it names. A typo, a copied compose
        file or a compromised environment would exfiltrate keys, so a
        non-https destination is refused outright and anything but a
        teamtoken.store host has to be opted into with
        TEAMTOKEN_ALLOW_ANY_BASE=1 — an environment setting, which is the one
        place a shared config file cannot reach.
        """
        import os
        from urllib.parse import urlparse

        parsed = urlparse(base)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" and host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError(f"refusing to send API keys over {parsed.scheme or '(no scheme)'} "
                             f"to {host or base!r}: use https")
        trusted = host.endswith("teamtoken.store") or host in {"localhost", "127.0.0.1", "::1"}
        if not trusted and os.environ.get("TEAMTOKEN_ALLOW_ANY_BASE", "") != "1":
            raise ValueError(f"refusing to send API keys to untrusted host {host!r}; "
                             f"set TEAMTOKEN_ALLOW_ANY_BASE=1 if this is your own gateway")

    def _headers(self) -> dict[str, str]:
        # Bearer is the documented form; the gateway also accepts x-api-key.
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def post(self, path: str, payload: dict) -> tuple[int, dict]:
        resp = await self._client().post(self._url(path), json=payload,
                                         headers=self._headers(), timeout=self._timeout())
        return self._handle(resp)

    async def get(self, path: str, *, auth: bool = True) -> tuple[int, dict | list]:
        headers = self._headers() if auth else {}
        resp = await self._client().get(self._url(path), headers=headers,
                                        timeout=self._timeout())
        return self._handle(resp, allow_list=not auth)

    def _handle(self, resp: httpx.Response, *, allow_list: bool = False) -> tuple[int, Any]:
        try:
            body = resp.json()
        except ValueError:
            if resp.is_success:
                raise TeamTokenError("the gateway returned a non-JSON success body",
                                     status=resp.status_code)
            raise TeamTokenError(humanize(resp.text[:300], resp.status_code, None),
                                 status=resp.status_code)
        # 200 = done, 202 = accepted and still running. Both are success; the
        # caller decides whether to poll.
        if resp.status_code in (200, 202):
            if isinstance(body, list) and allow_list:
                return resp.status_code, body
            if not isinstance(body, dict):
                raise TeamTokenError("the gateway returned a non-object JSON body",
                                     status=resp.status_code)
            return resp.status_code, body
        message, code, job_id = envelope(body)
        raise TeamTokenError(humanize(message, resp.status_code, code, job_id),
                             status=resp.status_code, code=code, job_id=job_id)

    # -- job polling -------------------------------------------------------

    async def poll_job(self, job_id: str, *, budget: float,
                       interval: float | None = None) -> dict:
        """Poll until terminal or the budget runs out; returns the last body.

        Returning the last body rather than raising on timeout is deliberate: a
        job that is still running is not an error, and the caller must be able to
        hand the id back to the model instead of losing it.
        """
        interval = interval or self.settings.image_poll_interval
        deadline = time.monotonic() + budget
        while True:
            # Budget gone BEFORE the request: do not ask. `_timeout()` would
            # raise "out of time budget", and the id of an already paid job would
            # leave with the exception. An empty body can be given the id back
            # further up; an exception cannot.
            if time.monotonic() >= deadline:
                return {}
            try:
                _, raw = await self.get(job_path(job_id))
            except (httpx.TimeoutException, httpx.TransportError):
                # A poll that fails on the wire says nothing about the job: it was
                # accepted and is being paid for. Letting the exception out would
                # replace a known id with "something went wrong", and the caller
                # would have nothing to hand back to the user.
                return {}
            body = raw if isinstance(raw, dict) else {}
            status = str(body.get("status") or "").lower()
            if status in DONE or status in FAILED:
                return body
            if time.monotonic() >= deadline:
                return body
            await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    async def aclose(self) -> None:
        """Only closes an INJECTED client. The shared pool outlives the call and
        is closed by close_shared_http() at shutdown."""
        if self._http is not None and self._http is not _SHARED:
            await self._http.aclose()
        self._http = None


def job_path(job_id: str) -> str:
    """Route a job id to the endpoint that knows it.

    The gateway mints ids with a modality prefix (`img_`/`vid_`), and the two
    modalities have separate status routes. An unprefixed id is treated as an
    image job, which is the older and more common shape.
    """
    return f"/v1/videos/{job_id}" if job_id.startswith("vid_") else f"/v1/images/jobs/{job_id}"
