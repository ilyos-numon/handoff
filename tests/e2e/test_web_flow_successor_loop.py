"""Flow (b): successor asks -> handed off -> leaver session surfaces it -> answer, close -> successor re-asks with evidence (new web, auth off)."""

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

LEAVER = "kim"
QUESTION = "정산 배치가 실패하면 누구에게 알리나요?"
FOLLOWUP_ANSWER = "배포 전에는 정산 배치 일정표를 확인하고, 겹치면 다음 월요일로 미룹니다."
ANSWER = "정산 배치가 실패하면 결제팀 온콜 담당자에게 먼저 알리고, 30분 안에 복구되지 않으면 팀장에게 알립니다."
MAX_ANSWERS = 4


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _new_page(browser: Browser) -> Page:
    ctx = browser.new_context(viewport={"width": 1100, "height": 900}, reduced_motion="reduce")
    page = ctx.new_page()
    page.set_default_timeout(20_000)
    return page


def _send(page: Page, text: str) -> None:
    box = page.get_by_label("답변")
    box.fill(text)
    page.get_by_role("button", name=re.compile("보내기")).click()
    expect(box).to_have_value("")  # cleared only after the server accepted the answer


def _ask(page: Page, text: str) -> None:
    box = page.get_by_label("질문", exact=True)
    box.fill(text)
    page.get_by_role("button", name="묻기", exact=True).click()
    expect(box).to_be_enabled()


def test_successor_question_loops_through_leaver_and_back(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url = web_server(tmp_path / "data").url
    q = f"?leaver={LEAVER}"

    def shot(page: Page, n: int, step: str) -> None:
        page.screenshot(path=str(shot_dir / f"web-b-{n:02d}-{step}.png"), full_page=True)

    suc = _new_page(browser)
    lea = _new_page(browser)

    # successor asks something nobody answered yet
    suc.goto(f"{url}/chat{q}")
    expect(suc.get_by_role("heading", name="막히면 먼저 기록에 물어보세요")).to_be_visible()
    _ask(suc, QUESTION)
    expect(suc.get_by_text("이 질문은 퇴사자 질문 목록에 올렸습니다.")).to_be_visible()
    shot(suc, 1, "successor-handed-off")

    # the handed-off list shows it as waiting
    suc.get_by_role("tab", name="넘긴 질문").click()
    expect(suc.get_by_text(QUESTION).locator("visible=true")).to_be_visible()
    expect(suc.get_by_text("다음 세션에 물어볼 예정").locator("visible=true")).to_be_visible()
    shot(suc, 2, "successor-handed-list")

    # leaver session: keep answering (at most MAX_ANSWERS) until the successor's question shows up with its source
    lea.goto(f"{url}/{q}")
    lea.get_by_role("button", name="세션 시작").click()
    marker = lea.get_by_text("후임이 물어본 질문")
    expect(lea.get_by_label("답변")).to_be_visible()
    shot(lea, 3, "leaver-session-start")
    answered = 0
    while answered < MAX_ANSWERS:
        try:
            marker.wait_for(state="visible", timeout=3_000)
            break
        except Exception:  # noqa: BLE001 - not yet; the question on screen is another one
            _send(lea, FOLLOWUP_ANSWER)
            answered += 1
    expect(marker).to_be_visible()
    expect(lea.get_by_text(QUESTION)).to_be_visible()
    shot(lea, 4, "leaver-successor-question")
    _send(lea, ANSWER)

    done = lea.get_by_role("heading", name="오늘은 여기까지입니다.", exact=True)
    if not done.is_visible():
        lea.get_by_role("button", name="오늘은 여기까지").click()
    expect(done).to_be_visible()
    shot(lea, 5, "leaver-closed")

    # successor asks the same question again: now there is evidence
    suc.reload()
    expect(suc.get_by_role("heading", name="막히면 먼저 기록에 물어보세요")).to_be_visible()
    _ask(suc, QUESTION)
    expect(suc.get_by_text(re.compile(r"근거 (확실|불확실)"))).to_be_visible()
    expect(suc.get_by_text(re.compile(r"세션 1 · "))).to_be_visible()
    shot(suc, 6, "successor-answer-with-evidence")
