"""Minimal in-process HTTP driver for the FastAPI app (no httpx/TestClient
dependency, which this venv doesn't ship). Drives the real ASGI stack --
routing, query/body validation, app-wide dependencies, CORS middleware --
so tests see the same status codes and JSON a browser would."""

import asyncio
import json
from urllib.parse import urlsplit


def request(app, method, url, headers=None, json_body=None):
    """Returns (status_code, response headers dict, parsed JSON or raw text)."""
    parts = urlsplit(url)
    body = b"" if json_body is None else json.dumps(json_body).encode()
    hdrs = {"host": "testserver", **{k.lower(): v for k, v in (headers or {}).items()}}
    if json_body is not None:
        hdrs.setdefault("content-type", "application/json")
        hdrs["content-length"] = str(len(body))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method.upper(), "scheme": "http", "path": parts.path,
        "raw_path": parts.path.encode(), "query_string": parts.query.encode(),
        "root_path": "", "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
        "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
    }
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    out = {"status": None, "headers": {}, "body": b""}

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")

    asyncio.run(app(scope, receive, send))
    try:
        payload = json.loads(out["body"]) if out["body"] else None
    except ValueError:
        payload = out["body"].decode(errors="replace")
    return out["status"], out["headers"], payload
