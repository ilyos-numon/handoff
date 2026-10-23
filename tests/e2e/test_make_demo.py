"""`make demo` smoke: the one-line launcher prints its address, serves /demo with three panes."""

import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANNOUNCE_TIMEOUT_S = 30


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return int(sock.getsockname()[1])


def test_make_demo_serves_three_panes(tmp_path: Path, shot_dir: Path) -> None:
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "HANDOFF_LLM_API_KEY"}
    for key in ("HANDOFF_DEMO", "HANDOFF_AUTH", "HANDOFF_PROVIDER", "HANDOFF_DATA_DIR"):
        env.pop(key, None)
    proc = subprocess.Popen(
        ["make", "demo", f"DEMO_ARGS=--port {port} --data-dir {tmp_path / 'demo'}"],
        cwd=PROJECT_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        deadline = time.monotonic() + ANNOUNCE_TIMEOUT_S
        first = ""
        while time.monotonic() < deadline:
            first = proc.stdout.readline().strip()
            if first or proc.poll() is not None:
                break
        url = f"http://localhost:{port}/demo"
        assert first == f"데모 주소: {url}"
        assert httpx.get(url, timeout=10).status_code == 200
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                page.goto(url)
                page.wait_for_load_state("load")
                assert page.locator("iframe").count() == 3
                for pane in ("manager", "leaver", "successor"):
                    page.frame_locator(f"#demo-pane-{pane}").locator("h1").first.wait_for(
                        timeout=15000
                    )
                page.screenshot(path=str(shot_dir / "pd-17-make-demo.png"), full_page=True)
            finally:
                browser.close()
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
