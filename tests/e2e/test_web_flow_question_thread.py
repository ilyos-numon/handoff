"""Question thread (auth on): successor and manager open their own question as a conversation with the leaver's answer."""

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
SECRET = "e2e-web-question-thread-secret"
LEAVER = "kim"
S_QUESTION = "PG 점검 공지는 어디서 봐요?"
S_ANSWER = "결제대행사 관리자 페이지 게시판을 매주 월요일 아침에 먼저 확인합니다."
S_MARK = "결제대행사"
M_QUESTION = "정산 배치가 새벽에 실패하면 누가 깨어 있어야 하나요?"
M_ANSWER = "야간 당직표의 첫 번째 사람이 새벽점검표대로 재실행을 맡습니다."
M_MARK = "새벽점검표"
FILLER_ANSWER = "배포 전에는 일정표를 확인하고, 겹치면 다음 월요일로 미룹니다."
MAX_FILLERS = 6
SUCCESSOR_MARKER = "후임이 물어본 질문"
MANAGER_MARKER = "팀장이 우선 지정한 질문"
REGION = "질문 대화"


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


def _context(browser: Browser, width: int = 1100, height: int = 900) -> BrowserContext:
    return browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")


def _page(ctx: BrowserContext, url: str, data_dir: Path, role: str, home: str) -> Page:
    page = ctx.new_page()
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
    _ask(suc, S_QUESTION)
    expect(suc.get_by_text("이 질문은 퇴사자 질문 목록에 올렸습니다.")).to_be_visible()


def _manager_asks(mgr: Page) -> None:
    expect(mgr.get_by_role("heading", name="무엇이 아직 안 넘어왔나")).to_be_visible()
    mgr.get_by_label("우선 질문").fill(M_QUESTION)
    mgr.get_by_role("button", name="큐에 넣기").click()
    expect(mgr.get_by_text("우선 질문을 큐 맨 앞에 넣었습니다.")).to_be_visible()


def _leaver_answers(lea: Page, targets: dict[str, tuple[str, str, str]]) -> None:
    """Start a session and answer the target questions as they come up.

    targets: marker label -> (question text, answer, name). Other questions get a filler answer (bounded).
    """
    pending = dict(targets)
    lea.get_by_role("button", name="세션 시작").click()
    expect(lea.get_by_label("답변")).to_be_visible()
    fillers = 0
    while pending:
        hit = None
        for marker, (question, _answer, _name) in pending.items():
            try:
                lea.get_by_text(marker, exact=True).wait_for(state="visible", timeout=1_500)
            except PlaywrightError:
                continue
            if lea.get_by_text(question).is_visible():
                hit = marker
                break
        if hit is None:
            assert fillers < MAX_FILLERS, f"questions never came up: {list(pending)}"
            _send(lea, FILLER_ANSWER)
            fillers += 1
            continue
        _send(lea, pending.pop(hit)[1])
        _finish_follow_ups(lea)


def _finish_follow_ups(lea: Page) -> None:
    """A follow-up keeps the queue item 'asked'; answer follow-ups (bounded) until the next top-level question."""
    follow = lea.get_by_text("이어서", exact=True)
    for _ in range(MAX_FILLERS):
        try:
            follow.wait_for(state="visible", timeout=1_500)
        except PlaywrightError:
            return
        _send(lea, FILLER_ANSWER)
    expect(follow).to_have_count(0)


def _leaver_closes(lea: Page) -> None:
    done = lea.get_by_role("heading", name="오늘은 여기까지입니다.", exact=True)
    if not done.is_visible():
        lea.get_by_role("button", name="오늘은 여기까지").click()
    expect(done).to_be_visible()


def _setup(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path
) -> tuple[str, Path, Page, Page, Page, list[str]]:
    """Successor, leaver, manager pages; manager responses are collected."""
    data_dir = tmp_path / "data"
    url = web_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET}).url
    suc = _page(_context(browser), url, data_dir, "successor", "/chat")
    lea = _page(_context(browser), url, data_dir, "leaver", "/")
    mgr = _context(browser).new_page()
    mgr.set_default_timeout(20_000)
    bodies: list[str] = []

    def collect(resp: Any) -> None:
        with contextlib.suppress(PlaywrightError):  # body unavailable (redirect/aborted)
            bodies.append(resp.body().decode(errors="replace"))

    mgr.on("response", collect)
    mgr.goto(_invite(url, data_dir, "manager"))
    mgr.goto(f"{url}/report")
    return url, data_dir, suc, lea, mgr, bodies


def _settle(page: Page) -> None:
    """Wait for CSS fade-in animations to finish so screenshots show the final state."""
    page.evaluate("Promise.all(document.getAnimations().map((a) => a.finished))")


def _shot(page: Page, shot_dir: Path, name: str) -> None:
    _settle(page)
    page.screenshot(path=str(shot_dir / f"{name}.png"), full_page=True)


def _open_handed(suc: Page) -> None:
    suc.get_by_role("tab", name="넘긴 질문").click()


def test_successor_sees_leaver_answer_as_thread(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    _url, _dd, suc, lea, _mgr, _bodies = _setup(browser, web_server, tmp_path)

    _successor_asks(suc)
    _open_handed(suc)
    item = suc.get_by_role("button", name=re.compile(re.escape(S_QUESTION)))
    expect(item).to_have_count(1)
    _shot(suc, shot_dir, "qt-01-successor-list")

    _leaver_answers(lea, {SUCCESSOR_MARKER: (S_QUESTION, S_ANSWER, "successor")})  # answered, not closed

    suc.reload()
    _open_handed(suc)
    suc.get_by_role("button", name=re.compile(re.escape(S_QUESTION))).click()
    thread = suc.get_by_role("region", name=REGION)
    expect(thread).to_be_visible()
    expect(thread.get_by_text(S_QUESTION)).to_be_visible()
    expect(thread.get_by_text("세션이 끝나면 여기에 보여요")).to_be_visible()
    expect(thread.get_by_text(S_MARK)).to_have_count(0)
    _shot(suc, shot_dir, "qt-02-successor-before-close")

    _leaver_closes(lea)

    thread.get_by_role("button", name="목록으로").click()
    suc.reload()
    _open_handed(suc)
    badge_item = suc.get_by_role("button", name=re.compile(re.escape(S_QUESTION))).filter(has_text="답이 왔어요")
    expect(badge_item).to_have_count(1)
    _shot(suc, shot_dir, "qt-04-successor-list-badge")

    badge_item.click()
    thread = suc.get_by_role("region", name=REGION)
    expect(thread.get_by_text(S_QUESTION)).to_be_visible()
    expect(thread.get_by_text(S_MARK)).to_be_visible()
    expect(thread.get_by_text(re.compile(r"퇴사자 답 · 세션 1")).first).to_be_visible()
    expect(thread.get_by_role("button", name="목록으로")).to_be_visible()
    expect(suc.get_by_role("button", name="다시 묻기")).to_have_count(0)
    _shot(suc, shot_dir, "qt-03-successor-thread")


def test_manager_sees_leaver_answer_to_own_priority_question(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    _url, _dd, _suc, lea, mgr, _bodies = _setup(browser, web_server, tmp_path)

    _manager_asks(mgr)
    _leaver_answers(lea, {MANAGER_MARKER: (M_QUESTION, M_ANSWER, "manager")})
    _leaver_closes(lea)

    mgr.reload()
    expect(mgr.get_by_role("heading", name="무엇이 아직 안 넘어왔나")).to_be_visible()
    item = mgr.get_by_role("button", name=re.compile(re.escape(M_QUESTION))).filter(has_text="답이 왔어요")
    expect(item).to_have_count(1)
    _shot(mgr, shot_dir, "qt-05-manager-list")

    item.click()
    thread = mgr.get_by_role("region", name=REGION)
    expect(thread.get_by_text(M_QUESTION)).to_be_visible()
    expect(thread.get_by_text(M_MARK)).to_be_visible()
    expect(thread.get_by_text("내가 남긴 질문")).to_be_visible()
    expect(thread.get_by_text(re.compile(r"퇴사자 답 · 세션 1")).first).to_be_visible()
    _shot(mgr, shot_dir, "qt-06-manager-thread")


def test_manager_never_sees_successor_answer_and_threads_are_scoped(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url, _dd, suc, lea, mgr, bodies = _setup(browser, web_server, tmp_path)

    _successor_asks(suc)
    _manager_asks(mgr)
    _leaver_answers(
        lea,
        {
            SUCCESSOR_MARKER: (S_QUESTION, S_ANSWER, "successor"),
            MANAGER_MARKER: (M_QUESTION, M_ANSWER, "manager"),
        },
    )
    _leaver_closes(lea)

    mgr.reload()
    item = mgr.get_by_role("button", name=re.compile(re.escape(M_QUESTION))).filter(has_text="답이 왔어요")
    expect(item).to_have_count(1)
    item.click()
    thread = mgr.get_by_role("region", name=REGION)
    expect(thread.get_by_text(M_MARK)).to_be_visible()  # the manager's own thread shows the manager's answer

    assert S_MARK not in mgr.inner_text("body")
    assert bodies, "no manager responses captured"
    for text in bodies:
        assert S_MARK not in text, "a manager-context response leaks the successor's answer"

    queue = mgr.request.get(f"{url}/leavers/{LEAVER}/queue").json()
    s_id = next(q["id"] for q in queue if q["source"] == "successor")
    m_id = next(q["id"] for q in queue if q["source"] == "manager")
    assert mgr.request.get(f"{url}/leavers/{LEAVER}/questions/{s_id}/thread").status == 403
    assert suc.request.get(f"{url}/leavers/{LEAVER}/questions/{m_id}/thread").status == 403
    # own thread is still reachable for each
    assert mgr.request.get(f"{url}/leavers/{LEAVER}/questions/{m_id}/thread").status == 200
    assert suc.request.get(f"{url}/leavers/{LEAVER}/questions/{s_id}/thread").status == 200


def test_thread_fits_mobile_width(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url, data_dir, _suc, lea, _mgr, _bodies = _setup(browser, web_server, tmp_path)
    mob = _page(_context(browser, 390, 844), url, data_dir, "successor", "/chat")

    _successor_asks(mob)
    _leaver_answers(lea, {SUCCESSOR_MARKER: (S_QUESTION, S_ANSWER, "successor")})
    _leaver_closes(lea)

    mob.reload()
    _open_handed(mob)
    mob.get_by_role("button", name=re.compile(re.escape(S_QUESTION))).filter(has_text="답이 왔어요").click()
    thread = mob.get_by_role("region", name=REGION)
    expect(thread.get_by_text(S_MARK)).to_be_visible()
    _shot(mob, shot_dir, "qt-07-mobile-390")
    width = mob.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, f"horizontal overflow: scrollWidth={width}"
