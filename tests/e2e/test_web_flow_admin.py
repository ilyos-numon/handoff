"""Flow (a): admin link -> register leaver -> role links -> role screens, gates, logout (new web, auth on)."""

import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET = "e2e-web-flow-a-secret"
ALIAS = "결제팀 백엔드"
ROLES = {"leaver": "퇴사자", "successor": "후임", "manager": "팀장"}
HOME = {"leaver": "/", "successor": "/chat", "manager": "/report"}
HEAD = {
    "leaver": re.compile("질문 몇 개에 답하면"),
    "successor": re.compile("막히면 먼저 기록에 물어보세요"),
    "manager": re.compile("무엇이 아직 안 넘어왔나"),
}


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _admin_link(url: str, data_dir: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "HANDOFF_LLM_API_KEY"}
    env.update(
        HANDOFF_DATA_DIR=str(data_dir),
        HANDOFF_AUTH="on",
        HANDOFF_AUTH_SECRET=SECRET,
        HANDOFF_PROVIDER="fake",
    )
    out = subprocess.run(
        [sys.executable, "-m", "handoff.rbac.cli", "admin", "--base-url", url],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _new_page(browser: Browser) -> Page:
    page = browser.new_context(viewport={"width": 1100, "height": 900}).new_page()
    page.set_default_timeout(20_000)
    return page


def test_admin_to_role_screens_gate_and_logout(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    data_dir = tmp_path / "data"
    server = web_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET})
    url = server.url

    def shot(page: Page, n: int, step: str) -> None:
        page.screenshot(path=str(shot_dir / f"web-a-{n:02d}-{step}.png"), full_page=True)

    # anonymous -> /intro
    anon = _new_page(browser)
    anon.goto(f"{url}/")
    anon.wait_for_url("**/intro")
    expect(anon.get_by_role("heading", name=re.compile("문서 대신"))).to_be_visible()
    shot(anon, 1, "anonymous-intro")

    # admin link -> /admin
    adm = _new_page(browser)
    adm.goto(_admin_link(url, data_dir))
    adm.wait_for_url("**/admin")
    expect(adm.get_by_role("heading", name="퇴사자 등록과 초대 링크")).to_be_visible()
    shot(adm, 2, "admin-open")

    # register leaver
    adm.get_by_label("별칭").fill(ALIAS)
    adm.get_by_role("button", name="등록", exact=True).click()
    expect(adm.get_by_text(ALIAS, exact=True)).to_be_visible()
    shot(adm, 3, "admin-registered")

    # three role links
    links: dict[str, str] = {}
    for role, label in ROLES.items():
        adm.get_by_role("button", name=f"{label} 링크", exact=True).click()
        box = adm.get_by_role("textbox", name=f"{label} 링크")
        expect(box).to_be_visible()
        links[role] = box.input_value()
        assert "/auth/accept?token=" in links[role]
    shot(adm, 4, "admin-role-links")

    # each link in a fresh context lands on its screen
    pages: dict[str, Page] = {}
    for n, role in enumerate(ROLES, start=5):
        page = _new_page(browser)
        pages[role] = page
        page.goto(links[role])
        page.goto(f"{url}{HOME[role]}")
        expect(page.get_by_role("heading", name=HEAD[role])).to_be_visible()
        shot(page, n, f"{role}-screen")

    # successor cannot open the manager screen
    succ = pages["successor"]
    succ.goto(f"{url}/report")
    expect(succ.get_by_role("heading", name="이 화면은 팀장 전용입니다", exact=True)).to_be_visible()
    expect(succ.get_by_text("지금은 후임(으)로 들어와 있습니다.")).to_be_visible()
    shot(succ, 8, "successor-blocked-report")

    # logout -> /intro, then protected screen bounces back
    succ.get_by_role("button", name="나가기").click()
    succ.wait_for_url("**/intro")
    succ.goto(f"{url}/chat")
    succ.wait_for_url("**/intro")
    shot(succ, 9, "logout-intro")
