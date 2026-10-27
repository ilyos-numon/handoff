"""`make demo` smoke: the one-line launcher prints its address, serves /demo with three panes."""

import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANNOUNCE_TIMEOUT_S = 30
EXAMPLES = PROJECT_ROOT / "web" / "src" / "pages" / "successor" / "examples.ts"


def _first_recorded_question() -> str:
    line = next(ln for ln in EXAMPLES.read_text(encoding="utf-8").splitlines() if "DEMO_ASK" in ln and "[" in ln)
    return re.findall(r'"([^"]+)"', line.split("=", 1)[1])[0]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return int(sock.getsockname()[1])


def test_make_demo_serves_three_panes(tmp_path: Path, shot_dir: Path, web_dist: Path) -> None:
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
        res = httpx.get(url, timeout=10)
        assert res.status_code == 200
        assert res.text == (web_dist / "index.html").read_text(encoding="utf-8")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                page = ctx.new_page()
                page.set_default_timeout(30_000)
                page.goto(url)
                titles = ("팀장 화면", "퇴사자 화면", "후임 화면")
                for title in titles:
                    expect(page.get_by_title(title)).to_be_visible()
                assert page.locator("iframe").count() == 3
                succ = page.frame_locator("iframe[title='후임 화면']")
                expect(succ.get_by_role("heading", name="막히면 먼저 기록에 물어보세요")).to_be_visible()
                expect(
                    page.frame_locator("iframe[title='팀장 화면']").get_by_role("heading", name="무엇이 아직 안 넘어왔나")
                ).to_be_visible()
                page.screenshot(path=str(shot_dir / "web-demo-01-three-panes.png"), full_page=True)

                succ.get_by_role("button", name=_first_recorded_question(), exact=True).click()
                expect(succ.get_by_text(re.compile(r"근거 (확실|불확실)"))).to_be_visible()
                expect(succ.get_by_text(re.compile(r"세션 \d+ · "))).to_be_visible()
                page.screenshot(path=str(shot_dir / "web-demo-02-successor-evidence.png"), full_page=True)

                mobile = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce").new_page()
                mobile.set_default_timeout(30_000)
                mobile.goto(url)
                # two buttons are named "후임": the top role switch and the pane switch (the later group)
                pane_buttons = mobile.get_by_role("group").get_by_role("button", name="후임", exact=True)
                expect(pane_buttons).to_have_count(2)
                pane_buttons.last.click()
                expect(mobile.get_by_title("후임 화면")).to_be_visible()
                width = mobile.evaluate("document.documentElement.scrollWidth")
                mobile.screenshot(path=str(shot_dir / "web-demo-03-mobile-390.png"), full_page=True)
                assert width <= 390, f"scrollWidth {width} > 390"
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
