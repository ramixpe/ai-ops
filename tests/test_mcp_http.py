from __future__ import annotations

import asyncio

from mcp_server.http_api import BearerTokenASGI


async def _request(app, authorization: str | None) -> tuple[int, bytes]:
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    headers = [] if authorization is None else [(b"authorization", authorization.encode())]
    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": headers,
            "query_string": b"",
        },
        receive,
        send,
    )
    response = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return response["status"], body


def test_bearer_asgi_refuses_missing_and_invalid_tokens():
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BearerTokenASGI(downstream, token="test-token")

    missing_status, missing_body = asyncio.run(_request(app, None))
    invalid_status, invalid_body = asyncio.run(_request(app, "Bearer wrong"))

    assert missing_status == invalid_status == 401
    assert b"test-token" not in missing_body + invalid_body


def test_bearer_asgi_accepts_only_the_exact_bearer_token():
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BearerTokenASGI(downstream, token="test-token")

    status, body = asyncio.run(_request(app, "Bearer test-token"))

    assert status == 204
    assert body == b""


def test_bearer_asgi_refuses_websocket_scopes_and_allows_lifespan():
    reached = []
    sent = []

    async def downstream(scope, receive, send):
        reached.append(scope["type"])

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    app = BearerTokenASGI(downstream, token="test-token")
    asyncio.run(app({"type": "websocket", "headers": []}, receive, send))
    asyncio.run(app({"type": "lifespan"}, receive, send))

    assert reached == ["lifespan"]
    assert sent == [{"type": "websocket.close", "code": 1008, "reason": "unauthorized"}]
