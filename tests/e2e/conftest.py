"""E2E fixtures: a real uvicorn subprocess per test, isolated data dir."""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
READY_TIMEOUT_S = 15
POLL_INTERVAL_S = 0.2


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def server_url(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    env = {
        **os.environ,
        "HANDOFF_DATA_DIR": str(tmp_path),
        "HANDOFF_PROVIDER": "fake",
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "handoff.main:create_app",
            "--factory",
            "--host",
            "localhost",
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://localhost:{port}"
    try:
        deadline = time.monotonic() + READY_TIMEOUT_S
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    ready = True
                    break
            except httpx.TransportError:
                pass
            time.sleep(POLL_INTERVAL_S)
        if not ready:
            proc.terminate()
            try:
                _, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, err = proc.communicate()
            stderr = (err or b"").decode(errors="replace")
            pytest.fail(f"server did not become ready on {url}\n{stderr}")
        yield url
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc.stderr is not None:
            proc.stderr.close()


@pytest.fixture
def client(server_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=server_url, timeout=10) as c:
        yield c
