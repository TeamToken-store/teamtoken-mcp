# TeamToken MCP server

Generate images and videos from your AI assistant. One API key, 50+ models —
Nano Banana, GPT Image, Grok, Veo, Kling, Seedance and more — through the
[Model Context Protocol](https://modelcontextprotocol.io).

The server is **remote**: nothing to install, no npm package. Point your client
at the URL below — whether you paste a key or sign in through the browser is
decided by the client, and both are covered under Setup.

```
https://teamtoken.store/mcp
```

## Setup

There are two ways in, and the client decides which: **sign in through the
browser**, or **send a key in a header**. Neither is more supported than the
other — pick by what your client can do.

### Sign in through the browser — no key anywhere

Leave the credential out of the config entirely. The server answers the first
call with a 401 that names where its authorization metadata lives (RFC 9728),
and a client that understands MCP OAuth takes it from there: it opens a browser,
you sign in with whatever your TeamToken account already uses — email, Telegram,
Google, GitHub — and approve on a consent screen.

```json
{
  "mcpServers": {
    "teamtoken": {
      "type": "http",
      "url": "https://teamtoken.store/mcp"
    }
  }
}
```

That is the whole config. It works in **Claude Code** — which registers itself
through a [Client ID Metadata Document](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#client-id-metadata-documents)
and comes back on a loopback port — and in any other client that implements the
same discovery.

**claude.ai and Claude Desktop** reach the same place through their own form:
Settings → Connectors → **Add custom connector**, paste the URL, press
**Connect**. There is nowhere to paste a key in that form, and no need to.

Every browser connection gets a key of its own, so disconnecting it in the
cabinet revokes that key alone and leaves the keys you created by hand working.
The cabinet lists each connection with the domain access goes to and the
browser, IP and time it was approved from — so a connection you did not make is
visible as one.

### Send a key in a header — Claude Code, Cursor, Cline, VS Code, Codex CLI

For clients without OAuth support, and for anyone who prefers a fixed
credential. Get an API key at [app.teamtoken.store](https://app.teamtoken.store),
then add the server to your client. Opening
[teamtoken.store/mcp](https://teamtoken.store/mcp) in a browser gives you these
configs with a copy button.

**Claude Desktop / Claude Code** — `claude_desktop_config.json` or `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "teamtoken": {
      "type": "http",
      "url": "https://teamtoken.store/mcp",
      "headers": { "x-api-key": "YOUR_KEY" }
    }
  }
}
```

**Cursor** — `.cursor/mcp.json`, same shape. **Cline**, **Windsurf**, **Goose**
and other clients that speak streamable-http take the same URL and header.

**VS Code** — `.vscode/mcp.json`, where the root key is `servers`, not
`mcpServers`, and `"type": "http"` is required: without it VS Code takes the URL
for a command and tries to run it.

```json
{
  "servers": {
    "teamtoken": {
      "type": "http",
      "url": "https://teamtoken.store/mcp",
      "headers": { "x-api-key": "YOUR_KEY" }
    }
  }
}
```

**Codex CLI** — `~/.codex/config.toml`, which reads the key from an environment
variable and sends it as `Authorization: Bearer`:

```toml
[mcp_servers.teamtoken]
url = "https://teamtoken.store/mcp"
bearer_token_env_var = "TEAMTOKEN_API_KEY"
```

## Tools

| Tool | What it does |
|---|---|
| `list_models` | Every image and video model with its price. No key needed. |
| `generate_image` | Text to image. Blocks and returns the picture. |
| `edit_image` | Edit or restyle pictures you pass in, or keep one character across images. |
| `generate_video` | Text/image/video to video. Returns a job id to poll. |
| `extend_video` | Continue an existing video. |
| `get_job` | Status and result of a job. Images come back inline; for video it returns the gateway link and the command to download it, because that link needs your API key and MCP hosts do not re-send credentials to links. |
| `get_balance` | Remaining balance in USD. |

### Images block, video does not

A picture takes seconds, so `generate_image` holds the call and hands back the
image. A video takes minutes, and MCP clients cut long calls on timeouts nobody
controls — a cut call looks like a broken service even though the job finished
and was billed. So `generate_video` returns a job id immediately, and the model
polls `get_job`.

If an image generation is unusually slow, the tool stops waiting and returns the
job id rather than a broken promise. The job is already paid for — poll it with
`get_job` instead of generating again.

## Pricing

Image models are priced per image, video models per second of output. Live
prices come from `list_models`, and from the
[public catalog](https://api.teamtoken.store/cabinet/api/public/media-models).
Failed generations are not billed.

## Running it yourself

```bash
pip install -e .
uvicorn teamtoken_mcp.app:app --port 8080
```

| Variable | Default | What it does |
|---|---|---|
| `TEAMTOKEN_API_BASE` | `https://api.teamtoken.store` | Gateway to proxy to. **Every authenticated call sends the caller's API key here** — a non-https or non-teamtoken.store address is refused at startup unless you set `TEAMTOKEN_ALLOW_ANY_BASE=1` |
| `TEAMTOKEN_CABINET_BASE` | `https://app.teamtoken.store` | Authorization server for the browser sign-in path. Used for one call: turning an OAuth token into the key behind it |
| `MCP_INTROSPECTION_SECRET` | — | Shared secret for that call. Empty means the browser sign-in path cannot work; the header path is unaffected |
| `MCP_RESOURCE_URL` | `https://teamtoken.store/mcp` | What the server calls itself in its RFC 9728 metadata. If it does not match the URL clients actually reach, a 401 sends them to authorise against the wrong thing |
| `MCP_IMAGE_WAIT_SECONDS` | `300` | How long `generate_image` waits before handing back a job id |
| `MCP_IMAGE_POLL_INTERVAL` | `2` | Gap between polls once the gateway has handed back a job id |
| `MCP_HTTP_TIMEOUT` | `200` | Timeout of a single gateway call |
| `MCP_PORT` | `8080` | Port the app is served on |
| `MCP_REGISTRY_AUTH` | — | Token served at `/.well-known/mcp-registry-auth` |

⚠️ The two timeouts are a ladder, and the order in it is load-bearing.
`MCP_HTTP_TIMEOUT` must outlive the gateway's own poll ceiling, or the single
call dies before the gateway can answer `202` with a job id — and the polling
that answer exists for never happens, so a slow generation looks like a dropped
connection while the job runs on and is billed. `MCP_IMAGE_WAIT_SECONDS` must in
turn leave room to poll after that answer arrives, or every slow job comes back
as a bare id instead of a picture. `tests/test_timeout_ladder.py` holds both
bounds.

Tests:

```bash
docker build -f Dockerfile.dev -t teamtoken-mcp-dev .
docker run --rm -v "$PWD":/work -w /work teamtoken-mcp-dev pytest -q
```

The server holds no credentials of its own: the caller's key is read from the
request and forwarded to the gateway, which authorises it. Nothing is stored.

## License

MIT — see [LICENSE](LICENSE).
