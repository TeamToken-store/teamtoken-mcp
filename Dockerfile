# Build context is this directory, and nothing outside it is needed: the server
# talks to the public gateway over HTTPS and imports no TeamToken code at all —
# which is the whole reason it can live in a public repository of its own.
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml ./
# Editable install against a stub package: the dependency layer is invalidated
# by pyproject.toml alone, never by code edits.
RUN mkdir -p teamtoken_mcp && touch teamtoken_mcp/__init__.py \
    && pip install --no-cache-dir -e .

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local /usr/local
COPY teamtoken_mcp ./teamtoken_mcp
# The server faces the internet and parses untrusted input. Code execution
# inside it is unlikely — but who it runs as is what decides whether it stays
# inside the container. Port 8080 needs no privileges, so the runtime has no use
# for root at all.
RUN useradd --system --no-create-home --uid 10001 mcp
USER mcp
EXPOSE 8080
# --no-access-log: every MCP client polls, and the access log would bury the
# lines that matter. Errors still go to stderr.
CMD ["uvicorn", "teamtoken_mcp.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
