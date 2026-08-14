"""Optional shared-secret gate for the HTTP dashboard / MCP endpoint.

Loopback demos need no token. Binding beyond localhost without one would
expose a write-capable DAQ server to the LAN, so the server refuses to start
in that configuration unless DAQ_MCP_TOKEN is set.
"""

from __future__ import annotations

import secrets
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in {"127.0.0.1", "localhost", "::1"}


def tokens_match(provided: str | None, expected: str) -> bool:
    if not provided:
        return False
    # Strip accidental "Bearer " if the client stuffed the whole header value
    # into the query param.
    raw = provided.strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return secrets.compare_digest(raw, expected)


def extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = request.headers.get("x-daq-token")
    if header:
        return header.strip()
    return request.query_params.get("token")


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests that do not present the shared secret.

    EventSource cannot set Authorization headers in browsers, so the query
    parameter `token=` is accepted as well as Bearer / X-DAQ-Token.
    """

    def __init__(self, app: Any, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next) -> Response:
        if tokens_match(extract_token(request), self._token):
            return await call_next(request)
        return JSONResponse(
            {"error": "Unauthorized. Pass Bearer token, X-DAQ-Token, or ?token="},
            status_code=401,
        )
