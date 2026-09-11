"""The MCP server: seven tools over the TeamToken media API.

Two decisions shape everything here, and both come from the reader being a model
rather than a person.

*Descriptions are interface, not documentation.* The model picks the tool and
fills the arguments from these strings alone, so they name concrete values
("duration: 5 or 10 seconds; Kling only accepts 5") instead of gesturing at
"generation parameters".

*Images block, videos do not.* A picture takes seconds, so generate_image holds
the call and returns the picture. A video takes minutes, and MCP clients cut
long calls on timeouts we do not control — a cut call reads as a broken service
even though the job finished and the money was spent. So generate_video returns
the job id plus an explicit instruction to poll get_job.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools import ToolResult
from mcp.types import Icon, ImageContent, TextContent
from pydantic import Field

from teamtoken_mcp import __version__
from teamtoken_mcp.auth import BadCredential, NoCredential, credential_from, resolve_api_key
from teamtoken_mcp.client import DONE, FAILED, TeamTokenClient, job_path
from teamtoken_mcp.config import get_settings
from teamtoken_mcp.errors import TeamTokenError
from teamtoken_mcp.landing import page, wants_html

log = logging.getLogger("teamtoken-mcp")

ASSETS = Path(__file__).parent / "assets"
# One size, not a list. A client is free to pick from a list however it likes,
# and Claude Desktop took the small one and drew it large: an upscaled 192 was
# the blurred mark that sent us back to redraw the icon.
ICONS = [("icon-512.png", "512x512")]


def asset_url(name: str) -> str:
    """Absolute address of a file served next to us. The icon has to live on OUR
    domain: a client shows it in its connector list, and an image pulled from
    someone else's host both leaks that a user connected us and turns into a
    broken tile the day that host moves.

    ⚠️ The `?v=` is not decoration. The file goes out with `max-age=86400`, and
    caches in front of us honour it: a release can be live and still be invisible
    for a day, which looks exactly like an icon that was never fixed. Versioning
    the URL moves the cache key precisely when the contents move, which is what
    lets the lifetime stay long instead of being cut for everyone to serve one
    release."""
    parts = urlparse(get_settings().resource_url)
    return f"{parts.scheme}://{parts.netloc}/.well-known/mcp/{name}?v={__version__}"


# The icon is declared in serverInfo (MCP 2025-11-25+) rather than left to the
# client to guess. Without it Claude Desktop draws the tile itself — from the
# domain's favicon if it finds one, and a host that publishes nothing but /mcp
# and /.well-known/mcp* serves none. Hence the borrowed white placeholder where
# our mark should have been.
mcp = FastMCP(
    name="teamtoken-media",
    # Without this, serverInfo carries FastMCP's own version rather than ours,
    # and that number says nothing about which build of this server is answering
    # — which is the only reason anyone reads it.
    version=__version__,
    website_url="https://app.teamtoken.store",
    icons=[Icon(src=asset_url(n), mimeType="image/png", sizes=[size])
           for n, size in ICONS],
    instructions=(
        "Generate images and videos through TeamToken. Call list_models first when "
        "the user has not named a model — prices and availability change. Images "
        "come back from generate_image directly; video generation returns a job id "
        "that you must poll with get_job."),
)


# -- plumbing ---------------------------------------------------------------

WELL_KNOWN_PR = "/.well-known/oauth-protected-resource"


def _metadata_path(resource_url: str) -> str:
    """Where RFC 9728 says the metadata for this resource lives: the well-known
    segment first, the resource's own path after it."""
    return WELL_KNOWN_PR + (urlparse(resource_url).path or "").rstrip("/")


def metadata_url(resource_url: str) -> str:
    """Absolute form of the above — what the 401 header must carry."""
    parts = urlparse(resource_url)
    return f"{parts.scheme}://{parts.netloc}{_metadata_path(resource_url)}"


async def _resolve_key() -> str:
    """The key this call may spend, from whichever credential the host sent.

    Hosts differ: Smithery injects its session config as `x-api-key`, Claude Code
    and Cursor send `Authorization: Bearer <key>`, and claude.ai sends an OAuth
    token in the same header — which is exchanged for the key of its grant.
    """
    try:
        # `include={"authorization"}` is neither decoration nor caution. FastMCP
        # STRIPS credential headers (`authorization`, `cookie`) by default —
        # they "cause issues if forwarded to downstream services". Sensible for
        # a proxy, fatal for us: a key sent as `Authorization: Bearer sk-…`,
        # which is exactly how Claude Code and Cursor send it, never arrived
        # here at all, and neither did an OAuth token from claude.ai. From the
        # outside it read as "no credentials in this session" on a correctly
        # configured client.
        credential = credential_from(get_http_headers(include={"authorization"}))
    except NoCredential:
        # Never a bare "unauthorized": the model relays this to a human who has
        # to know what to do about it.
        raise ToolError("No TeamToken credential in this session. Either add your API key "
                        "(header x-api-key, or Authorization: Bearer <key>) or connect the "
                        "TeamToken connector, which asks for access in the browser.")
    try:
        return await resolve_api_key(credential)
    except BadCredential as exc:
        raise ToolError(str(exc)) from exc


async def _client(deadline: float | None = None) -> TeamTokenClient:
    return TeamTokenClient(await _resolve_key(), deadline=deadline)


async def _call(fn, *, submits: bool = False) -> Any:
    """Run a gateway call, turning its failures into model-readable tool errors.

    `submits` marks the calls that can spend money. A dropped connection or a
    read timeout on those is genuinely ambiguous: the gateway may have created
    and billed the job and only the answer was lost. Saying merely "could not be
    reached" invites the model to try again, and the retry pays for a second
    generation — so an ambiguous submit says so, in words, and forbids the retry.
    """
    try:
        return await fn()
    except TeamTokenError as exc:
        raise ToolError(str(exc)) from exc
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        log.warning("transport failure on a gateway call", exc_info=True)
        if submits:
            raise ToolError(
                "The connection to TeamToken dropped while submitting the generation, so it is "
                "unknown whether the job was created. It may already be running and billed — do "
                "NOT submit again. Call get_balance, and check the job history before retrying."
            ) from exc
        raise ToolError("The TeamToken gateway could not be reached. Retry in a few seconds; if it "
                        "keeps failing, the service may be down.") from exc
    except Exception as exc:  # noqa: BLE001 — the model gets a sentence, the log gets the trace
        # The exception text does NOT travel outward, and that is not pedantry.
        # The reader of this string is a model that will paraphrase it to a
        # human, so whatever lands here is published. This branch catches ANY
        # exception, including ones from libraries that may one day put a URL
        # with query parameters, or a fragment of an upstream response, into
        # their message. Only what we wrote ourselves goes out; the detail
        # already went to the log on the line above.
        log.exception("unexpected failure in a tool call")
        raise ToolError("Something went wrong inside the TeamToken MCP server, not in the request "
                        "itself. Retry once; if it repeats, the server log has the detail.") from exc


def _cost(body: dict) -> str:
    c = body.get("cost_usd")
    return f"${c}" if c not in (None, "") else "unknown"


# base64 prefixes of the container formats providers actually return. Declaring
# everything as PNG is not harmless: a client that trusts the MIME type shows a
# broken image for a picture the user already paid for.
_MAGIC = (("/9j/", "image/jpeg"), ("iVBORw0KGgo", "image/png"),
          ("UklGR", "image/webp"), ("R0lGOD", "image/gif"))


def _mime_of(b64: str) -> str:
    for prefix, mime in _MAGIC:
        if b64.startswith(prefix):
            return mime
    return "image/png"


def _images_from(body: dict) -> list[ImageContent]:
    out: list[ImageContent] = []
    for item in body.get("data") or []:
        if isinstance(item, dict) and item.get("b64_json"):
            out.append(ImageContent(type="image", data=item["b64_json"],
                                    mimeType=_mime_of(item["b64_json"])))
    return out


# -- tools ------------------------------------------------------------------

@mcp.tool(
    name="list_models",
    description=(
        "List every image and video model with its price. Call this before generating "
        "when the user has not named a model, and to check what a model costs: prices "
        "are per image for image models and per second of output for video models. "
        "Takes no arguments and spends nothing."),
)
async def list_models() -> ToolResult:
    settings = get_settings()
    client = TeamTokenClient("", settings)

    async def run():
        # Public catalog: no key, so the tool answers even for a misconfigured
        # session — which is exactly when the model needs to see what exists.
        _, body = await client.get("/cabinet/api/public/media-models", auth=False)
        return body

    body = await _call(run)
    rows = body if isinstance(body, list) else []
    images = [r for r in rows if r.get("modality") == "image"]
    videos = [r for r in rows if r.get("modality") == "video"]

    def fmt(rows_: list[dict]) -> str:
        return "\n".join(
            f"  {r.get('model')} — ${r.get('unit_price_usd')} "
            f"{'per second' if r.get('billing_unit') == 'per_second' else 'per image'}"
            for r in sorted(rows_, key=lambda r: str(r.get("model"))))

    text = (f"{len(rows)} models available.\n\nImage models ({len(images)}):\n{fmt(images)}\n\n"
            f"Video models ({len(videos)}):\n{fmt(videos)}")
    return ToolResult(content=[TextContent(type="text", text=text)],
                      structured_content={"models": rows})


@mcp.tool(
    name="generate_image",
    description=(
        "Generate an image and return it. Blocks until the picture is ready (seconds, "
        "occasionally up to a few minutes) — do not poll after calling this. To edit or "
        "restyle existing pictures, or to keep the same character across images, pass "
        "them in `images` and use edit_image instead."),
)
async def generate_image(
    prompt: Annotated[str, Field(description="What to draw. Be specific; at least 10 characters.")],
    model: Annotated[str, Field(description="Image model id from list_models, e.g. 'nano-banana-pro'.")],
    aspect_ratio: Annotated[str, Field(description="One of 1:1, 16:9, 9:16, 4:3, 3:4.")] = "1:1",
    resolution: Annotated[str, Field(description="One of 1K, 2K, 4K. Larger costs more on some models.")] = "1K",
    n: Annotated[int, Field(description="How many images to generate, usually 1.", ge=1, le=4)] = 1,
) -> ToolResult:
    settings = get_settings()
    deadline = time.monotonic() + settings.image_wait_seconds
    client = await _client(deadline)
    payload = {"model": model, "prompt": prompt, "aspect_ratio": aspect_ratio,
               "resolution": resolution, "n": n}

    async def run():
        status, body = await client.post("/v1/images/generations", payload)
        # 202 means the provider is slow, not that anything went wrong: hold the
        # call and poll, because the tool promised a picture. The budget is what
        # is LEFT of the deadline, not the full wait again — otherwise a slow
        # submit and a slow poll would add up instead of sharing one limit.
        if status == 202 and body.get("id"):
            body = _keep_id(body["id"], await client.poll_job(
                str(body["id"]), budget=max(0.0, deadline - time.monotonic())))
        return body

    body = await _call(run, submits=True)
    return _image_result(body)


def _keep_id(job_id: str, polled: dict) -> dict:
    """Polling may return nothing (budget spent). The id must still survive into
    the answer: without it the model cannot collect a result already paid for."""
    if not polled.get("id"):
        polled = {**polled, "id": job_id}
    return polled


@mcp.tool(
    name="edit_image",
    description=(
        "Edit, restyle or extend existing pictures, or reuse the same character in a new "
        "scene. Give the source pictures in `images` (https URLs, data-URLs or bare "
        "base64 — one kind per request, do not mix) and describe the change in `prompt`. "
        "Blocks and returns the result, like generate_image."),
)
async def edit_image(
    prompt: Annotated[str, Field(description="The change to make, e.g. 'same person, in a forest, golden hour'.")],
    model: Annotated[str, Field(description="Image model that accepts references: nano-banana-pro, nano-banana-2, grok-image, gpt-image-2.")],
    images: Annotated[list[str], Field(description="1-4 source pictures: https URLs, or data-URLs, or bare base64. Do not mix URL and base64 in one call.")],
    aspect_ratio: Annotated[str, Field(description="One of 1:1, 16:9, 9:16, 4:3, 3:4.")] = "1:1",
    resolution: Annotated[str, Field(description="One of 1K, 2K, 4K.")] = "1K",
) -> ToolResult:
    settings = get_settings()
    deadline = time.monotonic() + settings.image_wait_seconds
    client = await _client(deadline)
    payload = {"model": model, "prompt": prompt, "images": images,
               "aspect_ratio": aspect_ratio, "resolution": resolution}

    async def run():
        status, body = await client.post("/v1/images/edits", payload)
        if status == 202 and body.get("id"):
            body = _keep_id(body["id"], await client.poll_job(
                str(body["id"]), budget=max(0.0, deadline - time.monotonic())))
        return body

    body = await _call(run, submits=True)
    return _image_result(body)


def _image_result(body: dict) -> ToolResult:
    images = _images_from(body)
    status = str(body.get("status") or ("completed" if images else "unknown")).lower()
    job_id = body.get("id")
    if images:
        text = f"Generated {len(images)} image(s). Cost: {_cost(body)}."
        return ToolResult(content=[TextContent(type="text", text=text), *images],
                          structured_content={"id": job_id, "cost_usd": body.get("cost_usd"),
                                              "status": status})
    if status in FAILED:
        raise ToolError(f"Generation failed and was not billed (job {job_id}). "
                        f"Try a different prompt or model.")
    if body.get("archived"):
        # archived + empty data means the 7-day result window closed, NOT that
        # the generation produced nothing — the distinction the field exists for.
        raise ToolError(f"Job {job_id} completed earlier, but its result has passed the "
                        f"7-day retention window and the bytes are gone.")
    # Still running past our wait budget: hand back the id rather than a lie.
    raise ToolError(
        f"The image is still generating after the wait budget. Job id {job_id} — call "
        f"get_job with it in about 30 seconds; it is already paid for, do not generate again.")


@mcp.tool(
    name="generate_video",
    description=(
        "Start a video generation. Returns a job id immediately — video takes 30 seconds "
        "to a few minutes, so you MUST call get_job with that id afterwards (wait ~30 "
        "seconds before the first check) and keep checking until it reports completed. "
        "Do not call this tool again while a job is running: each call costs money."),
)
async def generate_video(
    prompt: Annotated[str, Field(description="The scene to generate.")],
    model: Annotated[str, Field(description="Video model id from list_models, e.g. 'seedance-2-fast-720p'.")],
    duration: Annotated[int, Field(description="Seconds of output. Allowed values differ per family: veo 4/6/8, omni-flash 4/6/8/10, seedance 4-15, kling 3-15, grok 6. Ignored by edit and motion models, which take it from the input video.", ge=3, le=15)] = 5,
    aspect_ratio: Annotated[str, Field(description="One of 16:9, 9:16, 1:1.")] = "16:9",
    image: Annotated[str | None, Field(description="Optional input picture for image-to-video, or the character reference for motion control. URL, data-URL or base64.")] = None,
    video: Annotated[str | None, Field(description="Optional input video for video-to-video editing or motion control. Required by kling-*-edit-* and kling-*-motion-* models. URL, data-URL or base64.")] = None,
) -> ToolResult:
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "duration": duration,
                               "aspect_ratio": aspect_ratio, "wait": False}
    if image:
        payload["image"] = image
    if video:
        payload["video"] = video
    return await _start_video("/v1/videos", payload)


@mcp.tool(
    name="extend_video",
    description=(
        "Continue an existing video, adding more seconds to its end. Same asynchronous "
        "contract as generate_video: returns a job id to poll with get_job."),
)
async def extend_video(
    prompt: Annotated[str, Field(description="What should happen in the continuation.")],
    model: Annotated[str, Field(description="Video model id from list_models.")],
    video: Annotated[str, Field(description="The video to continue: URL, data-URL or base64.")],
    duration: Annotated[int, Field(description="Seconds to add.", ge=3, le=15)] = 5,
) -> ToolResult:
    payload = {"model": model, "prompt": prompt, "video": video,
               "duration": duration, "wait": False}
    return await _start_video("/v1/videos/extend", payload)


async def _start_video(path: str, payload: dict) -> ToolResult:
    client = await _client()

    async def run():
        _, body = await client.post(path, payload)
        return body

    body = await _call(run, submits=True)
    job_id = body.get("id")
    if not job_id:
        # No "retry": the gateway answered success, so a job may well have been
        # created and billed and only its id was lost. A retry would pay twice.
        raise ToolError(
            "The gateway accepted the request but returned no job id. A generation may "
            "already have been created and billed — do NOT submit again. Check the balance "
            "with get_balance and tell the user to look at their job history.")
    status = str(body.get("status") or "processing")
    text = (f"Video job {job_id} accepted (status: {status}). Wait about 30 seconds, then call "
            f"get_job with id {job_id}, and keep checking every 30 seconds until it reports "
            f"completed. Do not start another generation for this request — it would be billed "
            f"separately.")
    return ToolResult(content=[TextContent(type="text", text=text)],
                      structured_content={"id": job_id, "status": status})


@mcp.tool(
    name="get_job",
    description=(
        "Check a generation job by its id and get the result when ready. Works for both "
        "image jobs (id starts with img_) and video jobs (vid_). While status is queued or "
        "processing, wait ~30 seconds and call again."),
)
async def get_job(
    job_id: Annotated[str, Field(description="The job id returned by generate_video, extend_video or a timed-out image generation.")],
) -> ToolResult:
    client = await _client()

    async def run():
        _, body = await client.get(job_path(job_id))
        return body

    body = await _call(run)
    status = str(body.get("status") or "unknown").lower()
    images = _images_from(body)
    urls = [i.get("url") for i in (body.get("data") or [])
            if isinstance(i, dict) and i.get("url")]

    if status in FAILED:
        raise ToolError(f"Job {job_id} failed and was not billed. Adjust the prompt or "
                        f"input and start a new generation.")
    if status in DONE:
        if images:
            return ToolResult(content=[TextContent(type="text", text=f"Job {job_id} is done. Cost: {_cost(body)}."), *images],
                              structured_content={"id": job_id, "status": status,
                                                  "cost_usd": body.get("cost_usd")})
        if urls:
            # The link is served by the gateway and requires the same key, and MCP
            # hosts do not re-send credentials to URLs they find in tool text —
            # so the answer carries a command the user can actually run, not just
            # an address they will get a 401 from.
            text = (f"Job {job_id} is done. Cost: {_cost(body)}.\n"
                    f"The result is at {urls[0]} and needs the TeamToken API key. "
                    f"Give the user this command:\n"
                    f"  curl -L -H \"Authorization: Bearer $TEAMTOKEN_KEY\" "
                    f"-o result.mp4 \"{urls[0]}\"")
            return ToolResult(content=[TextContent(type="text", text=text)],
                              structured_content={"id": job_id, "status": status,
                                                  "url": urls[0], "cost_usd": body.get("cost_usd")})
        if body.get("archived"):
            raise ToolError(f"Job {job_id} completed earlier, but its result passed the 7-day "
                            f"retention window and the bytes are gone.")
        # Terminal status with neither result nor archived: polling further is
        # pointless, the answer will not change, and "wait a bit more" would loop
        # the model forever over a job it already paid for.
        raise ToolError(
            f"Job {job_id} reports completed but carries no result. Do not generate again; "
            f"report the job id to the user so support can look it up.")
    text = (f"Job {job_id} is {status}. Wait about 30 seconds and call get_job again — it is "
            f"already paid for, do not start a new generation.")
    return ToolResult(content=[TextContent(type="text", text=text)],
                      structured_content={"id": job_id, "status": status})


@mcp.tool(
    name="get_balance",
    description=(
        "Check the account balance in USD. Call it before an expensive generation, and "
        "after a payment error, so you can tell the user how much is missing."),
)
async def get_balance() -> ToolResult:
    client = await _client()

    async def run():
        _, body = await client.get("/v1/balance")
        return body

    body = await _call(run)
    balance = body.get("balance")
    text = (f"Balance: ${balance} (granted ${body.get('granted')}, spent ${body.get('spend')}). "
            f"Top up in the TeamToken cabinet at https://app.teamtoken.store/topup.")
    return ToolResult(content=[TextContent(type="text", text=text)], structured_content=body)


# -- discovery files --------------------------------------------------------

def build_app():
    """The ASGI app: MCP at /mcp plus the discovery files and a health probe.

    The files exist because of somebody else's infrastructure, not ours. The MCP
    registry proves domain ownership by fetching mcp-registry-auth; Smithery
    scans metadata with a bot out of Cloudflare Workers, which our WAF may block,
    and their documented way around that is a static card it can read instead;
    RFC 9728 metadata is how a client that got a 401 finds out where to authorise.
    """
    import os

    from starlette.responses import FileResponse, JSONResponse, PlainTextResponse

    settings = get_settings()

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request):
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/.well-known/mcp-registry-auth", methods=["GET"])
    async def registry_auth(_request):
        # Filled at deploy time with the token the registry issues for the
        # domain; empty means "namespace not claimed yet", which is honest.
        return PlainTextResponse(os.environ.get("MCP_REGISTRY_AUTH", "").strip() + "\n")

    for _name, _ in ICONS:
        # Bind by value: without `name=_name` every route would serve the last
        # file — a silent substitution rather than an error.
        @mcp.custom_route(f"/.well-known/mcp/{_name}", methods=["GET"], name=f"icon_{_name}")
        async def icon(_request, name: str = _name):
            return FileResponse(ASSETS / name, media_type="image/png",
                                headers={"cache-control": "public, max-age=86400"})

    @mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
    async def serve_card(_request):
        return JSONResponse(server_card())

    def _resource_metadata(_request):
        # RFC 9728. Points at the cabinet, which is the authorization server:
        # this process issues nothing and stores nothing.
        return JSONResponse({
            "resource": settings.resource_url,
            "authorization_servers": [settings.cabinet_base],
            "scopes_supported": ["media"],
            "bearer_methods_supported": ["header"],
        })

    # Two paths, and the second is the one clients actually fetch. RFC 9728 §3.1
    # builds the metadata URL by putting `/.well-known/oauth-protected-resource`
    # BETWEEN the host and the resource path, so a resource at `/mcp` is
    # described at `/.well-known/oauth-protected-resource/mcp` — not under `/mcp`
    # itself, which is exactly where the 401 gate lives. Advertising the latter
    # made the header point at its own 401: the client got told to go discover
    # authorisation and was refused at the discovery document, so the browser
    # flow never started and the failure looked like "the connector is broken".
    # The bare path stays served because it is what a human opens by hand.
    @mcp.custom_route(WELL_KNOWN_PR, methods=["GET"])
    async def protected_resource(request):
        return _resource_metadata(request)

    # The `if` is not decoration: for a resource with no path both forms are the
    # same string, and registering that route twice is a duplicate, not a
    # safety net.
    if _metadata_path(settings.resource_url) != WELL_KNOWN_PR:
        @mcp.custom_route(_metadata_path(settings.resource_url), methods=["GET"])
        async def protected_resource_for_path(request):
            return _resource_metadata(request)

    # The path is /mcp, not "/": the reverse proxy in front forwards the request
    # unchanged, with no prefix stripped, so a transport mounted at the root would
    # answer 404 on the published address — while the in-memory test client never
    # touches it.
    return RequireCredential(mcp.http_app(path="/mcp", transport="http"), settings)


class RequireCredential:
    """401 — never 403 — on an MCP request that carries no credential.

    The difference is not cosmetic. Per RFC 9728 a client reads 401 as "start
    authorisation discovery" and follows the WWW-Authenticate header to our
    metadata; 403 it reads as "closed for good" and never asks again. That single
    header is what makes the browser consent flow start at all in hosts that have
    nowhere to put an API key. Enforcing it here rather than inside each tool
    also means an unauthenticated client learns the truth at the handshake, not
    after a tool call that looks like it worked.
    """

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").startswith("/mcp"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
        # Only the shapes the tools can actually use. Accepting any Authorization
        # value let `Basic x` or a stray space through the handshake, after which
        # every tool failed inside the MCP layer instead of at the door — the
        # client sees a working session and never asks for credentials.
        auth = (headers.get("authorization") or "").strip()
        if (headers.get("x-api-key") or "").strip() or auth.lower().startswith("bearer "):
            return await self.app(scope, receive, send)
        meta = metadata_url(self.settings.resource_url)
        # A page for a human, JSON for a client. ONLY the body changes: the 401
        # and the WWW-Authenticate header have to stay, because a connection
        # starts from that pair (RFC 9728) and Claude says plainly that it does
        # not read the header on a 200.
        if wants_html(headers.get("accept", "")):
            body = page(url=self.settings.resource_url,
                        icon=asset_url(ICONS[0][0]),
                        cabinet=self.settings.cabinet_base,
                        docs=f"{urlparse(self.settings.resource_url).scheme}://"
                             f"{urlparse(self.settings.resource_url).netloc}/docs/tools",
                        lang=headers.get("accept-language", ""))
            content_type = b"text/html; charset=utf-8"
        else:
            body = (b'{"error":"unauthorized","error_description":'
                    b'"TeamToken API key or OAuth token required"}')
            content_type = b"application/json"
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", content_type),
            (b"www-authenticate", f'Bearer resource_metadata="{meta}"'.encode()),
            (b"content-length", str(len(body)).encode()),
        ]})
        await send({"type": "http.response.body", "body": body})


def server_card() -> dict:
    """Built per request rather than at import: the address and the icons come
    from settings, and settings come from the environment — a card frozen at
    import time is easily built before the settings are real."""
    return {
        "name": "store.teamtoken/media",
        "description": "Image and video generation across 50+ models through one API key.",
        "version": __version__,
        "websiteUrl": "https://app.teamtoken.store",
        # From the setting, not a literal: the card, the RFC 9728 metadata and
        # the icon URLs must all name ONE address. A literal here would one day
        # disagree with `resource_url`, and registries would publish an address
        # other than the one a 401 sends clients to.
        "remotes": [{"type": "streamable-http", "url": get_settings().resource_url}],
        "capabilities": {"tools": True},
        "icons": [{"src": asset_url(n), "mimeType": "image/png", "sizes": [size]}
                  for n, size in ICONS],
        "authentication": {"type": "apiKey", "in": "header", "name": "x-api-key"},
    }
