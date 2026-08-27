"""Authenticated native HTTP transports for the read-only MCP server."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

ASGIApp = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]

__all__ = ["BearerTokenASGI", "authenticated_mcp_app", "serve_authenticated_mcp"]


class BearerTokenASGI:
    """Require one configured bearer token before a request reaches MCP."""

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        if not token:
            raise ValueError("MCP HTTP bearer token must not be empty")
        self._app = app
        self._token = token

    async def __call__(self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]], send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._app(scope, receive, send)
            return
        if scope_type == "http":
            if self._authorized(scope):
                await self._app(scope, receive, send)
                return
            await JSONResponse({"detail": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "unauthorized"})
            return
        raise RuntimeError(f"unsupported ASGI scope type {scope_type!r}")

    def _authorized(self, scope: dict[str, Any]) -> bool:
        headers = dict(scope.get("headers", ()))
        value = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, credential = value.partition(" ")
        return separator == " " and scheme.lower() == "bearer" and hmac.compare_digest(credential, self._token)


def authenticated_mcp_app(mcp: Any, *, transport: str, host: str, bearer_token: str) -> BearerTokenASGI:
    """Build an authenticated native MCP SSE or streamable HTTP application."""

    if transport == "streamable-http":
        app = mcp.streamable_http_app(host=host)
    elif transport == "sse":
        app = mcp.sse_app(host=host)
    else:
        raise ValueError(f"MCP HTTP app needs 'sse' or 'streamable-http', not {transport!r}")
    return BearerTokenASGI(app, token=bearer_token)


def serve_authenticated_mcp(mcp: Any, *, transport: str, host: str, port: int, bearer_token: str) -> None:
    """Run an authenticated native MCP HTTP transport with Uvicorn."""

    import uvicorn

    uvicorn.run(
        authenticated_mcp_app(mcp, transport=transport, host=host, bearer_token=bearer_token),
        host=host,
        port=port,
        access_log=True,
    )
