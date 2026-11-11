"""Public demo abuse guards: slowapi rate limits, upload block and body size cap."""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any

from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from handoff.rbac.deps import DEMO_ROLE_HEADER

__all__ = [
    "DEMO_ROLE_HEADER",
    "UPLOAD_BLOCKED",
    "DemoBodyGuard",
    "DemoGuardConfig",
    "install_demo_guard",
    "make_client_key",
]

UPLOAD_BLOCKED = "demo upload disabled"
TOO_LARGE = "request too large"
START_PATH = "/demo/start"
START_SCOPE = "demo-start"
_UPLOAD_PATH = re.compile(r"^/leavers/[^/]+/sources$")

Gate = Callable[[Request], Any]
FORWARDED_FOR = "x-forwarded-for"


def _forwarded_client(request: Request) -> str | None:
    forwarded = request.headers.get(FORWARDED_FOR, "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    return hops[-1] if hops else None


def _is_trusted(peer: str, trusted: tuple[str, ...]) -> bool:
    if "*" in trusted or peer in trusted:
        return True
    try:
        address = ip_address(peer)
    except ValueError:
        return False
    for entry in trusted:
        try:
            if address in ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def make_client_key(trusted: tuple[str, ...]) -> Callable[[Request], str]:
    """Rate-limit key: the direct peer, or the right-most X-Forwarded-For value if the peer is trusted.

    Behind a reverse proxy (e.g. Hugging Face Spaces) every request shares the proxy's
    remote address. The proxy appends the real client to the right of X-Forwarded-For;
    values on the left come from the client and can be forged, so they are ignored.
    A peer that is not a trusted proxy could forge the whole header, so it is ignored.
    """

    def client_key(request: Request) -> str:
        peer = request.client.host if request.client else get_remote_address(request)
        if _is_trusted(peer, trusted):
            forwarded = _forwarded_client(request)
            if forwarded:
                return forwarded
        return peer

    return client_key


class _RedactClientKey(logging.Filter):
    """Drop the client key (the caller's address) from slowapi's "limit exceeded" log line."""

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            isinstance(record.msg, str)
            and record.msg.startswith("ratelimit ")
            and isinstance(record.args, tuple)
            and len(record.args) == 3
        ):
            record.args = (record.args[0], "<client>", record.args[2])
        return True


def _redact_slowapi_log() -> None:
    logger = logging.getLogger("slowapi")
    if not any(isinstance(f, _RedactClientKey) for f in logger.filters):
        logger.addFilter(_RedactClientKey())


@dataclass(frozen=True)
class DemoGuardConfig:
    max_body_bytes: int
    rate: str
    start_rate: str
    trusted_proxies: tuple[str, ...] = ()


async def _send_json(send: Send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class DemoBodyGuard:
    """Rate-limit every request, block real uploads and cap request bodies.

    slowapi's own middleware looks route handlers up in ``app.routes``, which no longer
    lists the endpoints of included routers, so the limits are checked here through
    slowapi-decorated gate functions instead.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int,
        default_gate: Gate,
        start_gate: Gate,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.default_gate = default_gate
        self.start_gate = start_gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"].upper()
        request = Request(scope)
        gate = self.start_gate if method == "POST" and scope["path"] == START_PATH else None
        try:
            (gate or self.default_gate)(request)
        except RateLimitExceeded as exc:
            await _rate_limit_exceeded_handler(request, exc)(scope, receive, send)
            return
        if method == "POST" and _UPLOAD_PATH.match(scope["path"]):
            await _send_json(send, 403, UPLOAD_BLOCKED)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            await _send_json(send, 413, TOO_LARGE)
            return
        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            if total > self.max_body_bytes:
                await _send_json(send, 413, TOO_LARGE)
                return
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self.app(scope, replay, send)


def install_demo_guard(app: FastAPI, settings_like: DemoGuardConfig) -> None:
    """Add the demo guards. Call before SessionMiddleware is added so the session stays outermost."""
    limiter = Limiter(
        key_func=make_client_key(settings_like.trusted_proxies),
        default_limits=[settings_like.rate],
    )
    _redact_slowapi_log()

    # Per-path counters, like slowapi's default limits (key style "url").
    @limiter.limit(settings_like.rate)
    def demo_default_gate(request: Request) -> None:
        return None

    # One shared counter per client for /demo/start, with its own (stricter) limit.
    @limiter.shared_limit(settings_like.start_rate, scope=START_SCOPE)
    def demo_start_gate(request: Request) -> None:
        return None

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(
        DemoBodyGuard,
        max_body_bytes=settings_like.max_body_bytes,
        default_gate=demo_default_gate,
        start_gate=demo_start_gate,
    )
