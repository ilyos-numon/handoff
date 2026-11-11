"""Container parity: run the Dockerfile CMD/ENV locally and check the public demo behaves.

The server is started exactly like the container does (``handoff-demo --public``) except
for a free port and a temp data dir. No LLM key is passed.
"""

import json
import os
import re
import select
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, expect, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = PROJECT_ROOT / "Dockerfile"
ANNOUNCE_TIMEOUT_S = 30
WARNING = "연습용 공간입니다. 실제 이름·연락처·회사 정보는 넣지 마세요. 서버가 쉬거나 하루가 지나면 지워집니다."
EXPECTED_CMD = ["uv", "run", "--frozen", "--no-dev", "handoff-demo", "--public", "--port", "7860"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return int(sock.getsockname()[1])


def _dockerfile_cmd() -> list[str]:
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("CMD "):
            parsed = json.loads(line[4:])
            assert isinstance(parsed, list)
            return [str(x) for x in parsed]
    raise AssertionError("Dockerfile has no CMD")


def _dockerfile_env() -> dict[str, str]:
    text = DOCKERFILE.read_text(encoding="utf-8")
    block = re.search(r"^ENV ((?:.*\\\n)*.*)$", text.split("USER user")[-1], re.MULTILINE)
    assert block is not None
    env: dict[str, str] = {}
    for token in block.group(1).replace("\\\n", " ").split():
        key, _, value = token.partition("=")
        if key.startswith("HANDOFF_") or key == "PORT":
            env[key] = value
    return env


@dataclass
class Running:
    url: str
    proc: subprocess.Popen[str]


@contextmanager
def _container_like(data_dir: Path, web_dist: Path) -> Iterator[Running]:
    port = _free_port()
    cmd = [a for a in _dockerfile_cmd() if a not in ("--frozen", "--no-dev")]
    cmd[cmd.index("--port") + 1] = str(port)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HANDOFF_")}
    env.update(_dockerfile_env())
    env["HANDOFF_DATA_DIR"] = str(data_dir)
    env["HANDOFF_WEB_DIST"] = str(web_dist)
    env["PORT"] = str(port)
    proc = subprocess.Popen(
        cmd, cwd=PROJECT_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        deadline = time.monotonic() + ANNOUNCE_TIMEOUT_S
        first = ""
        while time.monotonic() < deadline and not first:
            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                first = proc.stdout.readline().strip()
            if proc.poll() is not None and not first:
                break
        assert first.startswith("데모 주소:"), f"unexpected first line: {first!r}"
        yield Running(f"http://localhost:{port}", proc)
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


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory, web_dist: Path) -> Iterator[str]:
    with _container_like(tmp_path_factory.mktemp("cf-data"), web_dist) as running:
        yield running.url


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def test_static_parity_with_container_config() -> None:
    assert _dockerfile_cmd() == EXPECTED_CMD
    env = _dockerfile_env()
    assert env["PORT"] == "7860"
    assert env["HANDOFF_DATA_DIR"] == "/tmp/handoff-demo"
    index_ts = (PROJECT_ROOT / "deploy/cloudflare/src/index.ts").read_text(encoding="utf-8")
    assert "defaultPort = 7860" in index_ts
    wrangler = (PROJECT_ROOT / "deploy/cloudflare/wrangler.jsonc").read_text(encoding="utf-8")
    for needle in ('"name": "handoff-demo"', '"class_name": "HandoffContainer"',
                   '"image": "../../Dockerfile"', '"max_instances": 1'):
        assert needle in wrangler
    workflow = (PROJECT_ROOT / ".github/workflows/deploy-cloudflare.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in workflow


def test_health_is_json_200(server: str) -> None:
    res = httpx.get(f"{server}/health", timeout=10)
    assert res.status_code == 200
    assert isinstance(res.json(), dict)


def test_demo_page_has_three_panes_and_warning(server: str, browser: Browser, shot_dir: Path) -> None:
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    try:
        page = ctx.new_page()
        page.set_default_timeout(30_000)
        page.goto(f"{server}/demo")
        for title in ("팀장 화면", "퇴사자 화면", "후임 화면"):
            expect(page.get_by_title(title)).to_be_visible()
        assert page.locator("iframe").count() == 3
        expect(page.get_by_text(WARNING, exact=True)).to_be_visible()
        page.screenshot(path=str(shot_dir / "cf-01-demo.png"), full_page=True)
    finally:
        ctx.close()


def test_intro_pages(server: str, browser: Browser, shot_dir: Path) -> None:
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    try:
        page = ctx.new_page()
        page.set_default_timeout(30_000)
        resp = page.goto(f"{server}/intro")
        assert resp is not None and resp.status == 200
        expect(page.get_by_role("heading", level=1).first).to_be_visible()
        page.screenshot(path=str(shot_dir / "cf-02-intro.png"), full_page=True)
        resp = page.goto(f"{server}/intro/manager")
        assert resp is not None and resp.status == 200
        expect(page.get_by_role("heading", level=1, name="팀장 안내")).to_be_visible()
        page.screenshot(path=str(shot_dir / "cf-03-intro-manager.png"), full_page=True)
    finally:
        ctx.close()


def test_failure_paths(server: str) -> None:
    with httpx.Client(base_url=server, timeout=10) as c:
        res = c.get("/intro/ceo")
        assert res.status_code == 404
        assert res.json() == {"detail": "Not Found"}
        assert c.get("/admin/leavers").status_code in (401, 403)
        res = c.post("/leavers/x/sources", content=b"x", headers={"content-type": "text/plain"})
        assert res.status_code == 403


def test_status_shows_no_live_llm_calls(server: str) -> None:
    status = httpx.get(f"{server}/demo/status", timeout=10).json()
    assert status["demo"] is True
    llm = status["llm"]
    assert llm is not None
    assert llm["live_calls_today"] == 0


def test_stale_cookie_after_restart_gets_fresh_sandbox(
    tmp_path: Path, browser: Browser, web_dist: Path
) -> None:
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    try:
        page = ctx.new_page()
        page.set_default_timeout(30_000)
        with _container_like(tmp_path / "first", web_dist) as first:
            page.goto(f"{first.url}/demo")
            expect(page.get_by_title("후임 화면")).to_be_visible()
            before = ctx.request.get(f"{first.url}/demo/status").json()["leaver"]
            assert isinstance(before, str) and before.startswith("demo-")
        with _container_like(tmp_path / "second", web_dist) as second:
            page.goto(f"{second.url}/demo")
            expect(page.get_by_title("후임 화면")).to_be_visible()
            assert page.locator("iframe").count() == 3
            after = ctx.request.get(f"{second.url}/demo/status").json()["leaver"]
            assert isinstance(after, str) and after != before
    finally:
        ctx.close()
