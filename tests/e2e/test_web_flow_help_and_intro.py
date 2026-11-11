"""Help '?' buttons, manager tabs, role intro pages (auth on, one browser context per role)."""

import contextlib
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright
from playwright.sync_api import Error as PlaywrightError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET = "e2e-web-help-and-intro-secret"
LEAVER = "kim"
MARK = "HI-MARK-7741"
MANAGER_H1 = "무엇이 아직 안 넘어왔나"
STATUS_H = "진행 상황"
ASK_H = "꼭 남겨 달라고 할 질문"
SUCCESSOR_TABS = ["질문하기", "넘긴 질문", "인수인계 문서", "지식 카드", "이건 왜 이렇게?", "장애 훈련"]


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _invite(url: str, data_dir: Path, role: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "HANDOFF_LLM_API_KEY"}
    env.update(
        HANDOFF_DATA_DIR=str(data_dir),
        HANDOFF_AUTH="on",
        HANDOFF_AUTH_SECRET=SECRET,
        HANDOFF_PROVIDER="fake",
    )
    args = ["admin"] if role == "admin" else ["invite", "--role", role, "--leaver", LEAVER]
    out = subprocess.run(
        [sys.executable, "-m", "handoff.rbac.cli", *args, "--base-url", url],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _context(browser: Browser, width: int = 1100, height: int = 900) -> BrowserContext:
    return browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")


def _page(ctx: BrowserContext, url: str, data_dir: Path, role: str, home: str) -> Page:
    page = ctx.new_page()
    page.set_default_timeout(20_000)
    page.goto(_invite(url, data_dir, role))
    page.goto(f"{url}{home}")
    return page


def _server(web_server: Callable[..., Any], tmp_path: Path) -> tuple[str, Path]:
    data_dir = tmp_path / "data"
    url = web_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET}).url
    return url, data_dir


def _shot(page: Page, shot_dir: Path, name: str, full_page: bool = True) -> None:
    page.evaluate(  # infinite animations never finish; wait only for finite ones
        "Promise.all(document.getAnimations()"
        ".filter((a) => a.effect.getComputedTiming().endTime !== Infinity).map((a) => a.finished))"
    )
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(shot_dir / f"{name}.png"), full_page=full_page)


def _at_least(loc: Any, n: int) -> None:
    """Wait until at least n matches exist (the panel may still be loading), then assert."""
    loc.nth(n - 1).wait_for(state="attached")
    assert loc.count() >= n


def _help(page: Page, name: str) -> Any:
    return page.get_by_role("button", name=f"{name} 설명", exact=True)


def _note(page: Page, name: str) -> Any:
    return page.get_by_role("note", name=f"{name} 도움말")


def _manager(browser: Browser, url: str, data_dir: Path, width: int = 1100, height: int = 900) -> Page:
    page = _page(_context(browser, width, height), url, data_dir, "manager", "/report")
    expect(page.get_by_role("heading", name=MANAGER_H1)).to_be_visible()
    return page


def test_help_opens_by_keyboard_and_esc_returns_focus(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)
    mgr = _manager(browser, url, data_dir)

    btn = _help(mgr, "진행 상황")
    expect(btn).to_be_visible()
    btn.focus()
    expect(btn).to_be_focused()
    _shot(mgr, shot_dir, "hi-01-help-focus")

    mgr.keyboard.press("Enter")
    expect(_note(mgr, "진행 상황")).to_be_visible()
    expect(btn).to_have_attribute("aria-expanded", "true")
    _shot(mgr, shot_dir, "hi-02-help-open")

    mgr.keyboard.press("Escape")
    expect(mgr.get_by_role("note")).to_have_count(0)
    expect(btn).to_have_attribute("aria-expanded", "false")
    assert mgr.evaluate("document.activeElement.getAttribute('aria-label')") == "진행 상황 설명"


def test_only_one_help_open_and_close_button(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)
    mgr = _manager(browser, url, data_dir)

    _help(mgr, "진행 상황").click()
    expect(_note(mgr, "진행 상황")).to_be_visible()
    _help(mgr, "빈 구멍").click()
    expect(mgr.get_by_role("note")).to_have_count(1)
    expect(_note(mgr, "빈 구멍")).to_be_visible()

    _note(mgr, "빈 구멍").get_by_role("button", name="닫기", exact=True).click()
    expect(mgr.get_by_role("note")).to_have_count(0)


def test_manager_tabs_keep_url_and_fall_back(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)
    mgr = _manager(browser, url, data_dir)

    tabs = mgr.get_by_role("tablist").get_by_role("tab")
    expect(tabs).to_have_text(["빈 곳 보기", "질문 남기기", "자료와 문서"])
    expect(mgr.get_by_role("heading", name=STATUS_H, exact=True)).to_be_visible()
    _at_least(mgr.get_by_role("button", name=re.compile(r" 설명$")), 4)  # status tab (+ page title)

    mgr.get_by_role("tab", name="질문 남기기", exact=True).click()
    expect(mgr).to_have_url(re.compile(r"[?&]tab=ask"))
    expect(mgr.get_by_role("heading", name=ASK_H)).to_be_visible()
    expect(mgr.get_by_role("heading", name=STATUS_H, exact=True)).to_be_hidden()
    ask_panel = mgr.get_by_role("tabpanel", name="질문 남기기")
    _at_least(ask_panel.get_by_role("button", name=re.compile(r" 설명$")), 3)

    mgr.reload()
    expect(mgr).to_have_url(re.compile(r"[?&]tab=ask"))
    expect(mgr.get_by_role("heading", name=ASK_H)).to_be_visible()
    expect(mgr.get_by_role("heading", name=STATUS_H, exact=True)).to_be_hidden()
    _shot(mgr, shot_dir, "hi-04-manager-ask")

    mgr.get_by_role("tab", name="자료와 문서", exact=True).click()
    expect(mgr).to_have_url(re.compile(r"[?&]tab=docs"))
    docs_panel = mgr.get_by_role("tabpanel", name="자료와 문서")
    expect(docs_panel).to_be_visible()
    _at_least(docs_panel.get_by_role("button", name=re.compile(r" 설명$")), 3)
    _shot(mgr, shot_dir, "hi-05-manager-docs")

    mgr.goto(f"{url}/report?tab=zzz")
    expect(mgr.get_by_role("tab", name="빈 곳 보기", exact=True)).to_have_attribute("aria-selected", "true")
    expect(mgr.get_by_role("heading", name=STATUS_H, exact=True)).to_be_visible()
    _shot(mgr, shot_dir, "hi-03-manager-status")


def test_other_roles_help_and_successor_tab_order(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)

    lea = _page(_context(browser), url, data_dir, "leaver", "/")
    expect(lea.get_by_role("button", name="세션 시작")).to_be_visible()
    expect(_help(lea, "세션")).to_have_count(1)
    lea.get_by_role("button", name="세션 시작").click()
    expect(lea.get_by_label("답변")).to_be_visible()
    for title in ("남은 시간", "네 가지 축", "세션 마치기"):
        expect(_help(lea, title)).to_be_visible()

    suc = _page(_context(browser), url, data_dir, "successor", "/chat")
    expect(suc.get_by_role("tablist").get_by_role("tab")).to_have_text(SUCCESSOR_TABS)

    adm = _page(_context(browser), url, data_dir, "admin", "/admin")
    expect(_help(adm, "퇴사자 등록")).to_be_visible()


def test_intro_new_sections_and_role_intro(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)

    anon = _context(browser).new_page()
    anon.set_default_timeout(20_000)
    anon.goto(f"{url}/intro")
    for name in ("회사에 들이는 법", "하지 않는 것과 개인정보 원칙", "역할별 안내"):
        expect(anon.get_by_role("heading", name=name)).to_be_visible()
    expect(anon.locator("a[href^='/intro/']")).to_have_count(4)
    anon.get_by_role("heading", name="회사에 들이는 법").scroll_into_view_if_needed()
    _shot(anon, shot_dir, "hi-06-intro-adopt", full_page=False)

    anon.get_by_role("link", name=re.compile("팀장 안내")).click()
    expect(anon).to_have_url(re.compile(r"/intro/manager$"))
    expect(anon.get_by_role("heading", level=1, name="팀장 안내")).to_be_visible()
    expect(anon.get_by_role("heading", level=2)).to_have_count(4)
    expect(anon.get_by_text("다른 사람").first).to_be_visible()
    _shot(anon, shot_dir, "hi-07-role-intro-manager")

    suc = _page(_context(browser), url, data_dir, "successor", "/chat")
    suc.get_by_role("link", name="이 화면 안내", exact=True).click()
    expect(suc).to_have_url(re.compile(r"/intro/successor$"))
    expect(suc.get_by_role("heading", level=1, name="후임 안내")).to_be_visible()
    suc.get_by_role("link", name="내 화면으로", exact=True).click()
    expect(suc).to_have_url(re.compile(r"/chat$"))


def test_unknown_role_intro_is_404(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path
) -> None:
    url, _data_dir = _server(web_server, tmp_path)
    ctx = _context(browser)
    resp = ctx.request.get(f"{url}/intro/ceo")
    assert resp.status == 404
    assert resp.json()["detail"] == "Not Found"


def test_manager_never_sees_leaver_answer_text(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)
    lea = _page(_context(browser), url, data_dir, "leaver", "/")
    mgr_ctx = _context(browser)
    mgr = mgr_ctx.new_page()
    mgr.set_default_timeout(20_000)
    bodies: list[str] = []

    def collect(resp: Any) -> None:
        with contextlib.suppress(PlaywrightError):  # body unavailable (redirect/aborted)
            bodies.append(resp.body().decode(errors="replace"))

    mgr.on("response", collect)

    lea.get_by_role("button", name="세션 시작").click()
    box = lea.get_by_label("답변")
    expect(box).to_be_visible()
    box.fill(f"배포 전에 {MARK} 를 먼저 확인합니다.")
    lea.get_by_role("button", name=re.compile("보내기")).click()
    expect(box).to_have_value("")
    done = lea.get_by_role("heading", name="오늘은 여기까지입니다.", exact=True)
    if not done.is_visible():
        lea.get_by_role("button", name="오늘은 여기까지").click()
    expect(done).to_be_visible()

    mgr.goto(_invite(url, data_dir, "manager"))
    mgr.goto(f"{url}/report")
    expect(mgr.get_by_role("heading", name=MANAGER_H1)).to_be_visible()
    for tab in ("빈 곳 보기", "질문 남기기", "자료와 문서"):
        mgr.get_by_role("tab", name=tab, exact=True).click()
        expect(mgr.get_by_role("tab", name=tab, exact=True)).to_have_attribute("aria-selected", "true")
        mgr.reload()
        expect(mgr.get_by_role("heading", name=MANAGER_H1)).to_be_visible()
        assert MARK not in mgr.text_content("body")

    assert bodies, "no manager responses captured"
    for text in bodies:
        assert MARK not in text, "a manager-context response leaks the leaver's answer"


def test_help_and_role_intro_fit_mobile_width(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)
    mgr = _manager(browser, url, data_dir, 390, 844)

    _help(mgr, "진행 상황").click()
    note = _note(mgr, "진행 상황")
    expect(note).to_be_visible()
    box = note.bounding_box()
    assert box is not None
    assert box["x"] >= 0, box
    assert box["x"] + box["width"] <= 390, box
    width = mgr.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, f"horizontal overflow: scrollWidth={width}"
    _shot(mgr, shot_dir, "hi-08-mobile-help-390")

    mgr.goto(f"{url}/intro/leaver")
    expect(mgr.get_by_role("heading", level=1, name="퇴사자 안내")).to_be_visible()
    width = mgr.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, f"horizontal overflow: scrollWidth={width}"
    _shot(mgr, shot_dir, "hi-09-mobile-role-intro-390")
