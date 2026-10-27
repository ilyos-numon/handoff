"""X-Forwarded-For is only trusted when the direct peer is a configured trusted proxy."""

import asyncio
import ipaddress
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.requests import Request

from handoff.config import Settings
from handoff.demo import launch
from handoff.demo.guard import make_client_key
from handoff.main import create_app

PEER = "::1"
PROXY_ENV = "HANDOFF_TRUSTED_PROXIES"


def _request(peer: str | None, forwarded: str | None = None) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode())]
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
    }
    if peer is not None:
        scope["client"] = (peer, 12345)
    return Request(scope)


# --- make_client_key ---------------------------------------------------------


def test_untrusted_peer_ignores_forwarded() -> None:
    key = make_client_key(())
    assert key(_request(PEER, "alice.example")) == PEER


def test_star_trusts_every_peer() -> None:
    key = make_client_key(("*",))
    assert key(_request(PEER, "spoof.example, alice.example")) == "alice.example"


def test_exact_peer_name_is_trusted() -> None:
    key = make_client_key(("testclient",))
    assert key(_request("testclient", "alice.example")) == "alice.example"
    assert key(_request("other", "alice.example")) == "other"


def test_cidr_match_is_trusted() -> None:
    key = make_client_key(("fd00::/8",))
    inside = str(ipaddress.ip_network("fd00::/8")[5])
    assert key(_request(inside, "alice.example")) == "alice.example"
    assert key(_request(PEER, "alice.example")) == PEER


def test_invalid_entries_are_ignored() -> None:
    key = make_client_key(("not-a-network/99", "::1/200"))
    assert key(_request(PEER, "alice.example")) == PEER
    assert key(_request("not-an-ip", "alice.example")) == "not-an-ip"


def test_trusted_peer_without_forwarded_uses_peer() -> None:
    key = make_client_key(("*",))
    assert key(_request(PEER)) == PEER
    assert key(_request(PEER, " , ")) == PEER


def test_rightmost_non_empty_forwarded_value() -> None:
    key = make_client_key(("*",))
    assert key(_request(PEER, "a.example, b.example, ")) == "b.example"


# --- full app: no trusted proxy ----------------------------------------------


def test_untrusted_forwarded_does_not_split_start_bucket(tmp_path: Path) -> None:
    settings = Settings(
        provider="fake",
        data_dir=tmp_path,
        min_score=0.25,
        collection="handoff_kb",
        auth=True,
        auth_secret="t",
        demo=True,
        demo_start_rate="2/minute",
    )
    with TestClient(create_app(settings)) as c:
        codes = [
            c.post("/demo/start", headers={"X-Forwarded-For": f"c{i}.example"}).status_code
            for i in range(3)
        ]
    assert codes == [200, 200, 429]


# --- launch.main --------------------------------------------------------------


class _FakeServer:
    instances: ClassVar[list["_FakeServer"]] = []

    def __init__(self, config: uvicorn.Config) -> None:
        self.config = config
        self.started = False
        _FakeServer.instances.append(self)

    async def serve(self) -> None:
        await asyncio.sleep(0.01)
        self.started = True
        await asyncio.sleep(0.05)


@pytest.fixture
def isolated_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    saved = dict(os.environ)
    for k in ("PORT", "HANDOFF_DATA_DIR", "HANDOFF_DEMO", "HANDOFF_AUTH", "HANDOFF_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv(PROXY_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    _FakeServer.instances = []
    monkeypatch.setattr(launch.uvicorn, "Server", _FakeServer)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.mark.usefixtures("isolated_launch")
def test_public_launch_trusts_every_proxy() -> None:
    assert launch.main(["--public"]) == 0
    assert os.environ[PROXY_ENV] == "*"


@pytest.mark.usefixtures("isolated_launch")
def test_public_launch_keeps_explicit_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROXY_ENV, "fd00::/8")
    assert launch.main(["--public"]) == 0
    assert os.environ[PROXY_ENV] == "fd00::/8"


@pytest.mark.usefixtures("isolated_launch")
def test_local_launch_does_not_set_proxies() -> None:
    assert launch.main([]) == 0
    assert PROXY_ENV not in os.environ


def test_local_config_ignores_proxy_headers() -> None:
    # Otherwise uvicorn rewrites request.client from X-Forwarded-For before the guard runs.
    assert launch._config(8782, public=False).proxy_headers is False


def test_public_config_keeps_proxy_headers() -> None:
    config = launch._config(8782, public=True)
    assert config.proxy_headers is True
    assert config.forwarded_allow_ips == "*"
