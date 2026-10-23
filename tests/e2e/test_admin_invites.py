"""Admin screen E2E: bootstrap admin link from the CLI, alias registration, invite links per role."""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Browser, Page, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET = "e2e-admin-secret"
ALIAS = "결제팀 백엔드"
StartServer = Callable[..., Any]


class Admin:
    def __init__(self, url: str, data_dir: Path) -> None:
        self.url = url
        self.data_dir = data_dir

    def link(self) -> str:
        env = {k: v for k, v in os.environ.items() if k != "HANDOFF_LLM_API_KEY"}
        env.update(
            {
                "HANDOFF_DATA_DIR": str(self.data_dir),
                "HANDOFF_AUTH": "on",
                "HANDOFF_AUTH_SECRET": SECRET,
                "HANDOFF_PROVIDER": "fake",
            }
        )
        out = subprocess.run(
            [sys.executable, "-m", "handoff.rbac.cli", "admin", "--base-url", self.url],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()

    @contextmanager
    def api_client(self) -> Iterator[httpx.Client]:
        with httpx.Client(base_url=self.url, timeout=20) as c:
            assert c.get(self.link()).status_code == 303
            yield c


@pytest.fixture
def admin(start_server: StartServer, tmp_path: Path) -> Admin:
    data_dir = tmp_path / "data"
    server = start_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET})
    return Admin(server.url, data_dir)


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _admin_page(browser: Browser, admin: Admin) -> Page:
    page = browser.new_context(viewport={"width": 1000, "height": 900}).new_page()
    page.goto(admin.link())
    page.wait_for_url("**/admin")
    return page


def test_admin_link_opens_admin_screen(browser: Browser, admin: Admin, shot_dir: Path) -> None:
    page = _admin_page(browser, admin)
    assert page.locator("h1").inner_text() == "인수인계 관리"
    page.get_by_text("등록된 퇴사자가 없습니다.").wait_for()
    assert page.locator("#admin-error").is_hidden()
    page.screenshot(path=str(shot_dir / "pd-11-admin-open.png"), full_page=True)


def test_register_alias_lists_one_leaver(browser: Browser, admin: Admin, shot_dir: Path) -> None:
    page = _admin_page(browser, admin)
    page.fill("#alias-input", ALIAS)
    page.click("#leaver-create")
    page.locator("li.leaver").first.wait_for()
    assert page.locator("li.leaver").count() == 1
    assert page.locator(".leaver-alias").inner_text() == ALIAS
    page.screenshot(path=str(shot_dir / "pd-12-alias-registered.png"), full_page=True)


def test_manager_invite_link_shown(browser: Browser, admin: Admin, shot_dir: Path) -> None:
    page = _admin_page(browser, admin)
    page.fill("#alias-input", ALIAS)
    page.click("#leaver-create")
    page.locator("li.leaver").first.wait_for()
    page.get_by_role("button", name="팀장 링크").click()
    url_box = page.locator(".invite-url")
    url_box.wait_for()
    assert "/auth/accept?token=" in url_box.input_value()
    page.screenshot(path=str(shot_dir / "pd-13-manager-invite.png"), full_page=True)


def test_three_role_links_land_on_their_screens(
    browser: Browser, admin: Admin, shot_dir: Path
) -> None:
    with admin.api_client() as c:
        leaver = c.post("/admin/leavers", json={"alias": ALIAS}).json()["leaver"]
        urls = {
            role: c.post(f"/admin/leavers/{leaver}/invites", json={"role": role}).json()["url"]
            for role in ("manager", "leaver", "successor")
        }
    expected = {
        "manager": ("/report", "pd-14-manager-report.png"),
        "leaver": ("/", "pd-15-leaver-home.png"),
        "successor": ("/chat", "pd-16-successor-chat.png"),
    }
    for role, url in urls.items():
        ctx = browser.new_context(viewport={"width": 1000, "height": 900})
        try:
            page = ctx.new_page()
            page.goto(url)
            page.wait_for_load_state("load")
            path, shot = expected[role]
            assert page.url == f"{admin.url}{path}"
            me = page.request.get(f"{admin.url}/auth/me").json()
            assert me == {"auth": True, "role": role, "leaver": leaver}
            page.screenshot(path=str(shot_dir / shot), full_page=True)
        finally:
            ctx.close()


def test_admin_cannot_read_leaver_data(admin: Admin) -> None:
    with admin.api_client() as c:
        leaver = c.post("/admin/leavers", json={"alias": ALIAS}).json()["leaver"]
        assert c.get(f"/leavers/{leaver}/queue").status_code == 403
        assert c.get("/coverage", params={"leaver": leaver}).status_code == 403


def test_alias_with_phone_number_is_rejected(browser: Browser, admin: Admin) -> None:
    page = _admin_page(browser, admin)
    page.fill("#alias-input", "김 010-1234-5678")
    page.click("#leaver-create")
    page.locator("#admin-error:not(.hidden)").wait_for()
    assert "이메일·전화번호" in page.locator("#admin-error").inner_text()
    assert page.locator("li.leaver").count() == 0
