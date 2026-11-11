"""Admin screen after the browser's role changed: 403/401 guidance instead of silence (auth on)."""

import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET = "e2e-session-switch-secret"
ALIAS = "결제팀 백엔드 A"
NOTE = "이 브라우저에서 열면 관리자 로그인이 바뀝니다. 링크는 받을 사람에게 전달해 주세요."
MSG_403 = "이 브라우저는 지금 다른 역할로 들어와 있습니다. 관리자 링크로 다시 들어와 주세요."
MSG_401 = "관리자 링크로 다시 들어와 주세요."


def _admin_link(url: str, data_dir: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "HANDOFF_LLM_API_KEY"}
    env.update(HANDOFF_DATA_DIR=str(data_dir), HANDOFF_AUTH="on", HANDOFF_AUTH_SECRET=SECRET, HANDOFF_PROVIDER="fake")
    out = subprocess.run(
        [sys.executable, "-m", "handoff.rbac.cli", "admin", "--base-url", url],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _open_admin(browser: Browser, web_server: Callable[..., Any], tmp_path: Path) -> tuple[BrowserContext, Page, str]:
    server = web_server(tmp_path / "data", {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET})
    ctx = browser.new_context(viewport={"width": 1100, "height": 900}, reduced_motion="reduce")
    ctx.set_default_timeout(20_000)
    tab_a = ctx.new_page()
    tab_a.goto(_admin_link(server.url, tmp_path / "data"))
    tab_a.wait_for_url("**/admin")
    tab_a.get_by_label("별칭").fill(ALIAS)
    tab_a.get_by_role("button", name="등록", exact=True).click()
    expect(tab_a.get_by_text(ALIAS, exact=True)).to_be_visible()
    return ctx, tab_a, server.url


def test_admin_403_after_role_switch(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    ctx, tab_a, url = _open_admin(browser, web_server, tmp_path)
    try:
        tab_a.get_by_role("button", name="퇴사자 링크", exact=True).click()
        leaver_box = tab_a.get_by_role("textbox", name="퇴사자 링크")
        expect(leaver_box).to_be_visible()
        expect(tab_a.get_by_text(NOTE)).to_be_visible()
        tab_a.screenshot(path=str(shot_dir / "web-try-admin-link-note.png"), full_page=True)

        tab_b = ctx.new_page()
        tab_b.goto(leaver_box.input_value())
        tab_b.goto(f"{url}/")
        expect(tab_b.get_by_role("heading", name=re.compile("질문 몇 개에 답하면"))).to_be_visible()

        tab_a.get_by_role("button", name="후임 링크", exact=True).click()
        expect(tab_a.get_by_role("alert").filter(has_text=MSG_403)).to_be_visible()
        expect(tab_a.get_by_role("textbox", name="후임 링크")).to_have_count(0)
        tab_a.screenshot(path=str(shot_dir / "web-try-admin-403.png"), full_page=True)
    finally:
        ctx.close()


def test_admin_401_after_logout(browser: Browser, web_server: Callable[..., Any], tmp_path: Path) -> None:
    ctx, tab_a, url = _open_admin(browser, web_server, tmp_path)
    try:
        assert ctx.request.post(f"{url}/auth/logout").ok
        tab_a.get_by_role("button", name="팀장 링크", exact=True).click()
        expect(tab_a.get_by_role("alert").filter(has_text=MSG_401)).to_be_visible()
        expect(tab_a.get_by_role("textbox", name="팀장 링크")).to_have_count(0)
    finally:
        ctx.close()
