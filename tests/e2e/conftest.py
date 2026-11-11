"""E2E fixtures: a real uvicorn subprocess per test, isolated data dir."""

import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, sync_playwright

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
        "HANDOFF_AUTH": "off",
        "HANDOFF_WEB_DIST": "off",
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


class ServerStartError(RuntimeError):
    """The server process exited or never became ready; carries its stderr."""

    def __init__(self, message: str, stderr: str) -> None:
        super().__init__(message)
        self.stderr = stderr


@dataclass
class ServerHandle:
    url: str
    proc: subprocess.Popen[bytes]

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc.stderr is not None and not self.proc.stderr.closed:
            self.proc.stderr.close()


def _launch(data_dir: Path, extra_env: dict[str, str]) -> ServerHandle:
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "HANDOFF_LLM_API_KEY"}
    env["HANDOFF_PROVIDER"] = "fake"
    env["HANDOFF_DATA_DIR"] = str(data_dir)
    env["HANDOFF_AUTH"] = "off"
    env["HANDOFF_WEB_DIST"] = "off"
    env.update(extra_env)
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
    handle = ServerHandle(f"http://localhost:{port}", proc)
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            if httpx.get(f"{handle.url}/health", timeout=1).status_code == 200:
                return handle
        except httpx.TransportError:
            pass
        time.sleep(POLL_INTERVAL_S)
    if proc.poll() is None:
        proc.terminate()
    try:
        _, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, err = proc.communicate()
    raise ServerStartError(
        f"server did not become ready on {handle.url}", (err or b"").decode(errors="replace")
    )


@pytest.fixture
def start_server() -> Iterator[Callable[[Path, dict[str, str]], ServerHandle]]:
    handles: list[ServerHandle] = []

    def factory(data_dir: Path, extra_env: dict[str, str] | None = None) -> ServerHandle:
        handle = _launch(data_dir, extra_env or {})
        handles.append(handle)
        return handle

    try:
        yield factory
    finally:
        for h in handles:
            h.stop()


@pytest.fixture
def shot_dir() -> Path:
    path = Path(os.environ.get("ILLY_SHOT_DIR", "tests/e2e/screenshots"))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1000, "height": 900})
        pg = context.new_page()
        try:
            yield pg
        finally:
            context.close()
            browser.close()


@pytest.fixture(scope="session")
def web_dist() -> Path:
    if shutil.which("npm") is None:
        pytest.fail("npm not found: source ~/.nvm/nvm.sh")
    result = subprocess.run(
        ["make", "web"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300, check=False
    )
    index = PROJECT_ROOT / "web" / "dist" / "index.html"
    if result.returncode != 0 or not index.is_file():
        pytest.fail(f"make web failed ({result.returncode})\n{result.stderr[-2000:]}")
    return index.parent


@pytest.fixture
def web_server(
    start_server: Callable[[Path, dict[str, str]], ServerHandle], web_dist: Path
) -> Callable[..., ServerHandle]:
    def factory(data_dir: Path, extra: dict[str, str] | None = None) -> ServerHandle:
        return start_server(data_dir, {"HANDOFF_WEB_DIST": str(web_dist), **(extra or {})})

    return factory
