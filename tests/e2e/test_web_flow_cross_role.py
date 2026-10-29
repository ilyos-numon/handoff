"""Cross-role loop (auth on): successor asks -> leaver answers on the web -> successor and manager see the result."""

import contextlib
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright
from playwright.sync_api import Error as PlaywrightError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET = "e2e-web-cross-role-secret"
LEAVER = "kim"
QUESTION = "PG 점검 공지는 어디서 봐요?"
ANSWER = "결제대행사 관리자 페이지 게시판을 매주 월요일 아침에 먼저 확인합니다."
ANSWER_MARK = "결제대행사"
FILLER_ANSWER = "배포 전에는 일정표를 확인하고, 겹치면 다음 월요일로 미룹니다."
MAX_ANSWERS = 4


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
    out = subprocess.run(
        [sys.executable, "-m", "handoff.rbac.cli", "invite", "--role", role, "--leaver", LEAVER, "--base-url", url],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _page(browser: Browser, url: str, data_dir: Path, role: str, home: str) -> Page:
    page = browser.new_context(viewport={"width": 1100, "height": 900}, reduced_motion="reduce").new_page()
    page.set_default_timeout(20_000)
    page.goto(_invite(url, data_dir, role))
    page.goto(f"{url}{home}")
    return page


def _send(page: Page, text: str) -> None:
    box = page.get_by_label("답변")
    box.fill(text)
    page.get_by_role("button", name=re.compile("보내기")).click()
    expect(box).to_have_value("")


def _ask(page: Page, text: str) -> None:
    box = page.get_by_label("질문", exact=True)
    box.fill(text)
    page.get_by_role("button", name="묻기", exact=True).click()
    expect(box).to_be_enabled()


def _successor_asks(suc: Page) -> None:
    expect(suc.get_by_role("heading", name="막히면 먼저 기록에 물어보세요")).to_be_visible()
    _ask(suc, QUESTION)
    expect(suc.get_by_text("이 질문은 퇴사자 질문 목록에 올렸습니다.")).to_be_visible()


def _leaver_answers_successor_question(lea: Page) -> None:
    """Start a session, answer filler questions until the successor's question shows up, then answer it."""
    lea.get_by_role("button", name="세션 시작").click()
    marker = lea.get_by_text("후임이 물어본 질문")
    expect(lea.get_by_label("답변")).to_be_visible()
    answered = 0
    while answered < MAX_ANSWERS:
        try:
            marker.wait_for(state="visible", timeout=3_000)
            break
        except Exception:  # noqa: BLE001 - not yet; the question on screen is another one
            _send(lea, FILLER_ANSWER)
            answered += 1
    expect(marker).to_be_visible()
    expect(lea.get_by_text(QUESTION)).to_be_visible()
    _send(lea, ANSWER)


def test_successor_reask_before_close_gets_nothing_new(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    data_dir = tmp_path / "data"
    url = web_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET}).url

    suc = _page(browser, url, data_dir, "successor", "/chat")
    lea = _page(browser, url, data_dir, "leaver", "/")

    _successor_asks(suc)
    _leaver_answers_successor_question(lea)  # answered, but the session is not closed

    suc.reload()
    expect(suc.get_by_role("heading", name="막히면 먼저 기록에 물어보세요")).to_be_visible()
    _ask(suc, QUESTION)
    expect(suc.get_by_text("이 질문은 퇴사자 질문 목록에 올렸습니다.")).to_be_visible()
    expect(suc.get_by_text(re.compile(r"근거 (확실|불확실)"))).to_have_count(0)
    suc.screenshot(path=str(shot_dir / "web-x-01-reask-before-close.png"), full_page=True)

    items = suc.request.get(f"{url}/leavers/{LEAVER}/successor-questions").json()
    assert len(items) == 1, items  # no duplicate re-queue

    suc.get_by_role("tab", name="넘긴 질문").click()
    expect(suc.get_by_text(QUESTION).locator("visible=true")).to_have_count(1)
    suc.screenshot(path=str(shot_dir / "web-x-02-handed-list-one-item.png"), full_page=True)


def test_cross_role_loop_reaches_successor_and_manager(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    data_dir = tmp_path / "data"
    url = web_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET}).url

    def shot(page: Page, name: str) -> None:
        page.screenshot(path=str(shot_dir / f"{name}.png"), full_page=True)

    suc = _page(browser, url, data_dir, "successor", "/chat")
    lea = _page(browser, url, data_dir, "leaver", "/")
    mgr_ctx = browser.new_context(viewport={"width": 1100, "height": 900}, reduced_motion="reduce")
    mgr = mgr_ctx.new_page()
    mgr.set_default_timeout(20_000)
    bodies: dict[str, list[str]] = {"coverage": [], "queue": [], "gaps": []}

    def collect(resp: Any) -> None:
        for key, items in bodies.items():
            if f"/{key}" in resp.url:
                with contextlib.suppress(PlaywrightError):  # body unavailable (redirect/aborted)
                    items.append(resp.text())

    mgr.on("response", collect)
    mgr.goto(_invite(url, data_dir, "manager"))

    _successor_asks(suc)
    shot(suc, "web-x-03-successor-handed-off")

    _leaver_answers_successor_question(lea)
    done = lea.get_by_role("heading", name="오늘은 여기까지입니다.", exact=True)
    if not done.is_visible():
        lea.get_by_role("button", name="오늘은 여기까지").click()
    expect(done).to_be_visible()
    shot(lea, "web-x-04-leaver-closed")

    # successor: handed list shows it answered; opening it shows the leaver answer in the thread
    suc.reload()
    suc.get_by_role("tab", name="넘긴 질문").click()
    item = suc.get_by_role("listitem").filter(has_text=QUESTION)
    expect(item).to_have_count(1)
    expect(item.get_by_text("답변됨")).to_be_visible()
    item.get_by_role("button", name=re.compile(re.escape(QUESTION))).click()
    thread = suc.get_by_role("region", name="질문 대화")
    expect(thread.get_by_text(ANSWER_MARK)).to_be_visible()
    expect(thread.get_by_text(re.compile(r"세션 1"))).to_be_visible()
    shot(suc, "web-x-05-successor-linked-answer")
    items = suc.request.get(f"{url}/leavers/{LEAVER}/successor-questions").json()
    assert len(items) == 1, items

    # manager: queue and coverage show it answered, without the answer text
    mgr.goto(f"{url}/report")
    expect(mgr.get_by_role("heading", name="무엇이 아직 안 넘어왔나")).to_be_visible()
    mgr.get_by_role("tab", name="질문 남기기", exact=True).click()
    mgr.get_by_role("button", name="전체", exact=True).click()
    row = mgr.get_by_role("listitem").filter(has_text=QUESTION)
    expect(row).to_have_count(1)
    expect(row.get_by_text("답변됨")).to_be_visible()
    shot(mgr, "web-x-06-manager-queue")
    mgr.get_by_role("tab", name="빈 곳 보기", exact=True).click()
    expect(mgr.get_by_text("추가 질문 1개 답함")).to_be_visible()
    shot(mgr, "web-x-07-manager-coverage")

    assert ANSWER_MARK not in mgr.text_content("body")
    assert bodies["coverage"], "no /coverage response captured"
    assert bodies["queue"], "no /queue response captured"
    for key, texts in bodies.items():
        for text in texts:
            assert ANSWER_MARK not in text, f"{key} response leaks the leaver's answer"
