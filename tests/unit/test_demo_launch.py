"""One-line demo launcher (`handoff-demo` / `make demo`)."""

import asyncio
import os
import socket
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
import uvicorn

from handoff.config import load_settings
from handoff.demo import launch

ENV_KEYS = ("PORT", "HANDOFF_DATA_DIR", "HANDOFF_DEMO", "HANDOFF_AUTH", "HANDOFF_PROVIDER")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # launch.main writes os.environ directly; restore the whole environment afterwards
    # so its HANDOFF_* defaults do not leak into later tests (e.g. CLI subprocesses).
    saved = dict(os.environ)
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
    os.environ.clear()
    os.environ.update(saved)


class _FakeServer:
    """Stands in for uvicorn.Server: flips ``started`` and returns without binding."""

    instances: ClassVar[list["_FakeServer"]] = []

    def __init__(self, config: uvicorn.Config) -> None:
        self.config = config
        self.started = False
        _FakeServer.instances.append(self)

    async def serve(self) -> None:
        await asyncio.sleep(0.01)
        self.started = True
        await asyncio.sleep(0.2)


@pytest.fixture
def fake_server(monkeypatch: pytest.MonkeyPatch) -> type[_FakeServer]:
    _FakeServer.instances = []
    monkeypatch.setattr(launch.uvicorn, "Server", _FakeServer)
    return _FakeServer


def test_defaults_port_data_dir_and_demo_env(
    fake_server: type[_FakeServer], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert launch.main([]) == 0
    config = fake_server.instances[0].config
    assert config.port == 8780
    assert config.host == "localhost"
    assert config.factory is True
    assert config.app == "handoff.main:create_app"
    assert config.workers == 1
    assert os.environ["HANDOFF_DEMO"] == "on"
    assert os.environ["HANDOFF_AUTH"] == "on"
    assert os.environ["HANDOFF_PROVIDER"] == "replay"
    assert Path(os.environ["HANDOFF_DATA_DIR"]) == Path("data/demo")


def test_port_env_used_and_flag_wins(
    fake_server: type[_FakeServer], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PORT", "9123")
    monkeypatch.setenv("HANDOFF_DATA_DIR", str(tmp_path / "env-dir"))
    launch.main([])
    assert fake_server.instances[-1].config.port == 9123
    assert os.environ["HANDOFF_DATA_DIR"] == str(tmp_path / "env-dir")

    launch.main(["--port", "9200", "--data-dir", str(tmp_path / "flag-dir")])
    assert fake_server.instances[-1].config.port == 9200
    assert os.environ["HANDOFF_DATA_DIR"] == str(tmp_path / "flag-dir")


def test_public_binds_all_ipv4_with_proxy_headers(
    fake_server: type[_FakeServer], tmp_path: Path
) -> None:
    launch.main(["--public", "--data-dir", str(tmp_path)])
    config = fake_server.instances[0].config
    assert config.host == socket.inet_ntoa(struct.pack("!I", socket.INADDR_ANY))
    assert config.proxy_headers is True
    assert config.forwarded_allow_ips == "*"


def test_prints_exactly_one_address_line(
    fake_server: type[_FakeServer], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    launch.main(["--port", "8783", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert out == "데모 주소: http://localhost:8783/demo\n"


def test_no_line_when_server_never_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Failing(_FakeServer):
        async def serve(self) -> None:
            await asyncio.sleep(0.01)

    monkeypatch.setattr(launch.uvicorn, "Server", _Failing)
    assert launch.main(["--data-dir", str(tmp_path)]) == 1
    assert capsys.readouterr().out == ""


def test_existing_env_values_not_overwritten(
    fake_server: type[_FakeServer], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HANDOFF_PROVIDER", "fake")
    monkeypatch.setenv("HANDOFF_DEMO", "on")
    monkeypatch.setenv("HANDOFF_AUTH", "on")
    launch.main(["--data-dir", str(tmp_path)])
    assert os.environ["HANDOFF_PROVIDER"] == "fake"


def test_launch_env_loads_replay_settings_without_cassette(
    fake_server: type[_FakeServer], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before a cassette is recorded, the launcher still yields a valid replay demo config."""
    monkeypatch.setenv("HANDOFF_DEMO_CASSETTE", str(tmp_path / "missing.json"))
    launch.main(["--data-dir", str(tmp_path / "d")])
    settings = load_settings()
    assert settings.demo is True
    assert settings.auth is True
    assert settings.provider == "replay"
    assert settings.data_dir == tmp_path / "d"

    from fastapi.testclient import TestClient

    from handoff.main import create_app

    with TestClient(create_app(settings)) as client:
        assert client.get("/demo").status_code == 200


def test_main_module_exits_with_main_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def _fake_main(argv: list[str] | None = None) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr(launch, "main", _fake_main)
    import runpy

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("handoff.demo.__main__", run_name="__main__")
    assert exc.value.code == 0
    assert calls == [None]
