"""Demo scenario E2E: follows docs/demo-scenario.md step by step.

Auth-on server, invite links from the CLI, one browser with three contexts
(manager, leaver, successor) so each role keeps its own cookie. Every step
saves ``demo-NN-<slug>.png`` under ``ILLY_SHOT_DIR``. Sentences and expected
texts here are the same strings the document tells a person to type and see.
"""

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
SECRET = "e2e-secret"
LEAVER = "kim"

# Step 5 — manager priority question
PRIORITY_QUESTION = "결제 대행사 장애가 나면 가장 먼저 무엇을 확인하나요?"
# Step 8 — leaver answer with a phone number
ANSWER_PHONE = (
    "결제 대행사 장애가 나면 먼저 대행사 상태 페이지를 확인하고, "
    "담당 엔지니어에게 010-1234-5678 로 전화합니다."
)
# Step 9 — two more answers
ANSWER_BATCH = "정산 배치는 매일 새벽 2시에 돌고, 실패하면 아침 9시 전에 수동으로 다시 돌립니다."
ANSWER_FRIDAY = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
# Step 11 — successor question the leaver answered
ASK_KNOWN = "결제 대행사 장애가 나면 무엇을 먼저 확인하나요?"
# Step 12 / 18 — successor question nobody answered yet
ASK_UNKNOWN = "캐시 서버를 재시작해야 할 때는 어떻게 하나요?"
# Step 13 — fence question
FENCE_SUBJECT = "금요일 배포 금지"
# Step 17 — leaver answer to the successor question
ANSWER_CACHE = "캐시 서버는 트래픽이 적은 새벽에 한 대씩 재시작하고, 재시작 후 적중률을 확인합니다."

FOLLOW_UP = "그렇게 하는 이유는 무엇인가요? 왜 그 방식이 필요한가요?"
MSG_FORBIDDEN = "이 화면을 볼 권한이 없습니다."
QUEUED_NOTICE = "퇴사자 질문 목록에 올렸습니다. 다음 세션에서 물어봅니다."

StartServer = Callable[..., Any]


def _invite(base_url: str, data_dir: Path, role: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "HANDOFF_LLM_API_KEY"}
    env.update(
        {
            "HANDOFF_DATA_DIR": str(data_dir),
            "HANDOFF_AUTH": "on",
            "HANDOFF_AUTH_SECRET": SECRET,
            "HANDOFF_PROVIDER": "fake",
        }
    )
    out = subprocess.run(
        [sys.executable, "-m", "handoff.rbac.cli", "invite", "--role", role,
         "--leaver", LEAVER, "--base-url", base_url],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
    )
    url = out.stdout.strip()
    assert url.startswith(f"{base_url}/auth/accept?token="), url
    return url


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _window(browser: Browser) -> Page:
    context = browser.new_context(viewport={"width": 1000, "height": 900})
    return context.new_page()


def test_demo_scenario(
    start_server: StartServer, browser: Browser, shot_dir: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    server = start_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET})
    base = server.url
    invites = {role: _invite(base, data_dir, role) for role in ("manager", "leaver", "successor")}

    manager = _window(browser)
    leaver = _window(browser)
    successor = _window(browser)

    def shot(page: Page, n: int, slug: str) -> None:
        page.screenshot(path=str(shot_dir / f"demo-{n:02d}-{slug}.png"), full_page=True)

    queue = manager.locator("#queue li.queue-item")
    status = manager.locator("#report-status")

    # 1 — manager accepts invite, report starts empty
    manager.goto(invites["manager"])
    expect(manager).to_have_url(f"{base}/report")
    expect(manager.locator("#report-title")).to_have_text("인수인계 진행 상황 · 세션 0/10 기준")
    expect(manager.locator(".axis-row")).to_have_count(4)
    for count in manager.locator(".axis-count").all_inner_texts():
        assert count == "0/10"
    expect(manager.locator("#report-error")).to_be_hidden()
    shot(manager, 1, "manager-report-empty")

    # 2 — mock work sources
    manager.locator("#sources-mock").click()
    expect(status).to_have_text(re.compile(r"^모의 업무 자료 \d+건을 불러왔습니다\.$"))
    shot(manager, 2, "manager-sources")

    # 3 — questions from sources
    manager.locator("#questions-generate").click()
    expect(status).to_have_text(re.compile(r"^업무 자료로 질문 \d+개를 큐에 넣었습니다\.$"))
    expect(queue.filter(has_text="· 업무 자료 · 대기").first).to_be_visible()
    shot(manager, 3, "manager-generated-questions")

    # 4 — premortem questions
    manager.locator("#premortem-add").click()
    expect(status).to_have_text("사전 부검 질문 3개를 큐에 넣었습니다.")
    expect(queue.filter(has_text="· 사전 부검 · 대기")).to_have_count(3)
    shot(manager, 4, "manager-premortem")

    # 5 — priority question
    manager.locator("#priority-input").fill(PRIORITY_QUESTION)
    manager.locator("#priority-send").click()
    expect(status).to_have_text("우선 질문을 큐에 넣었습니다.")
    expect(queue.filter(has_text=f"{PRIORITY_QUESTION} · 팀장 우선 · 대기")).to_have_count(1)
    shot(manager, 5, "manager-priority")

    # 6 — leaver accepts invite
    leaver.goto(invites["leaver"])
    expect(leaver).to_have_url(f"{base}/")
    expect(leaver.locator("#start-button")).to_be_enabled()
    expect(leaver.locator("#error")).to_be_hidden()
    shot(leaver, 6, "leaver-entry")

    # 7 — session 1, first question is the manager's priority question
    leaver.locator("#start-button").click()
    expect(leaver.locator("#session-number")).to_have_text("세션 1/10")
    expect(leaver.locator("#question-text")).to_have_text(PRIORITY_QUESTION)
    expect(leaver.locator("#question-source")).to_have_text("팀장이 우선 지정한 질문")
    expect(leaver.locator("#question-source")).to_be_visible()
    expect(leaver.locator("#axis-label")).to_have_text("사고 이력")
    shot(leaver, 7, "leaver-priority-question")

    # 8 — answer with a phone number
    leaver.locator("#answer-input").fill(ANSWER_PHONE)
    leaver.locator("#send-button").click()
    expect(leaver.locator("#items-today")).to_have_text("오늘 쌓인 것: 1항목")
    expect(leaver.locator("#question-text")).to_have_text(FOLLOW_UP)
    expect(leaver.locator("#question-source")).to_be_hidden()
    shot(leaver, 8, "leaver-answer-phone")

    # 9 — two more answers; a premortem question comes up in between
    leaver.locator("#answer-input").fill(ANSWER_BATCH)
    leaver.locator("#send-button").click()
    expect(leaver.locator("#items-today")).to_have_text("오늘 쌓인 것: 2항목")
    expect(leaver.locator("#question-source")).to_have_text("사전 부검 질문")
    leaver.locator("#answer-input").fill(ANSWER_FRIDAY)
    leaver.locator("#send-button").click()
    expect(leaver.locator("#items-today")).to_have_text("오늘 쌓인 것: 3항목")
    shot(leaver, 9, "leaver-more-answers")

    # 10 — end session 1
    leaver.locator("#end-button").click()
    expect(leaver.locator("#summary")).to_be_visible()
    expect(leaver.locator("li.summary-line")).to_have_count(3)
    expect(leaver.locator("li.summary-line").first).to_contain_text("사고 이력: 결제 대행사 장애가")
    expect(leaver.locator("#items-total")).to_have_text("전체 3항목")
    shot(leaver, 10, "leaver-summary")

    # 11 — successor asks a known question: citation, certainty, masked phone
    successor.goto(invites["successor"])
    expect(successor).to_have_url(f"{base}/chat")
    expect(successor.locator("#chat-error")).to_be_hidden()
    successor.locator("#chat-input").fill(ASK_KNOWN)
    successor.locator("#chat-send").click()
    known = successor.locator(".bot-answer").last
    expect(known).not_to_have_class(re.compile(r"\bnot-found\b"))
    expect(known.locator(".answer-text")).to_contain_text("[전화번호]")
    expect(known.locator(".citation-meta").first).to_contain_text("세션 1 (사고 이력)")
    expect(known.locator(".citation-excerpt").first).to_contain_text("[전화번호]")
    expect(known.locator(".certainty")).to_have_text("근거 확실")
    assert "010-" not in successor.locator("#chat-log").inner_text()
    shot(successor, 11, "successor-known-answer")

    # 12 — successor asks something nobody answered: goes to the leaver queue
    successor.locator("#chat-input").fill(ASK_UNKNOWN)
    successor.locator("#chat-send").click()
    missing = successor.locator(".bot-answer.not-found").last
    expect(missing.locator(".answer-text")).to_have_text("이 주제는 인수인계에 없습니다. 팀장에게 확인하세요.")
    expect(missing.locator(".queued-notice")).to_have_text(QUEUED_NOTICE)
    expect(missing.locator(".citation")).to_have_count(0)
    shot(successor, 12, "successor-unknown-queued")

    # 13 — fence question
    successor.locator("#fence-input").fill(FENCE_SUBJECT)
    successor.locator("#fence-send").click()
    fence = successor.locator(".fence-answer").last
    expect(fence.locator(".answer-text")).to_have_text("이 결정의 이유는 인수인계 기록에 없습니다.")
    expect(fence.locator(".fence-risk").first).to_have_text(
        "작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
    )
    expect(fence.locator(".queued-notice")).to_have_text(QUEUED_NOTICE)
    shot(successor, 13, "successor-fence")

    # 14 — manager reloads: queue answers counted per axis, queue statuses
    manager.reload()
    expect(manager.locator("#report-title")).to_have_text("인수인계 진행 상황 · 세션 1/10 기준")
    incidents = manager.locator(".axis-row", has_text="사고 이력")
    expect(incidents.locator(".axis-extra")).to_have_text("추가 질문 2개 답함")
    expect(manager.locator(".axis-extra")).to_have_count(1)
    expect(queue.filter(has_text=f"{PRIORITY_QUESTION} · 팀장 우선 · 답변됨")).to_have_count(1)
    expect(queue.filter(has_text=f"{ASK_UNKNOWN} · 후임 질문 · 대기")).to_have_count(1)
    shot(manager, 14, "manager-report-progress")

    # 15 — gaps and areas nobody knows for sure
    manager.locator("#gaps-refresh").click()
    expect(status).to_have_text(
        re.compile(r"^빈 구멍 \d+개, 아무도 확실히 모르는 영역 \d+개를 찾았습니다\.$")
    )
    expect(manager.locator("#gaps li.gap").first).to_contain_text(
        re.compile(r" · 위험도 (높음|보통|낮음)$")
    )
    expect(manager.locator("#gaps li.gap", has_text="메시지 큐 · 위험도 높음")).to_have_count(1)
    # Every answer so far was stated with certainty, so no area is marked unknown yet.
    expect(manager.locator("#unknown-areas li")).to_have_text(["찾은 영역이 없습니다."])
    shot(manager, 15, "manager-gaps")

    # 16 — runbook artifact
    manager.locator('#artifact-buttons button[data-kind="runbook"]').click()
    expect(status).to_have_text("산출물을 만들었습니다: 운영 런북")
    artifact = manager.locator("#artifacts .artifact").first
    expect(artifact.locator(".artifact-title")).to_have_text("운영 런북")
    artifact.locator("summary").click()
    expect(artifact.locator(".artifact-body").first).to_contain_text("[전화번호]")
    assert "010-" not in manager.locator("#artifacts").inner_text()
    shot(manager, 16, "manager-runbook")

    # 17 — leaver session 2: first question is the successor's question
    leaver.reload()
    expect(leaver.locator("#summary")).to_be_visible()
    leaver.locator("#start-button").click()
    expect(leaver.locator("#session-number")).to_have_text("세션 2/10")
    expect(leaver.locator("#question-text")).to_have_text(ASK_UNKNOWN)
    expect(leaver.locator("#question-source")).to_have_text("후임이 물어본 질문")
    shot(leaver, 17, "leaver-successor-question")
    leaver.locator("#answer-input").fill(ANSWER_CACHE)
    leaver.locator("#send-button").click()
    expect(leaver.locator("#items-today")).to_have_text("오늘 쌓인 것: 1항목")
    leaver.locator("#end-button").click()
    expect(leaver.locator("#summary")).to_be_visible()
    expect(leaver.locator("li.summary-line").first).to_have_text(f"담당 시스템: {ANSWER_CACHE}")
    session_id = leaver.evaluate(f"localStorage.getItem('handoff.session.{LEAVER}')")
    assert isinstance(session_id, str) and session_id

    # 18 — successor asks the step-12 question again: now grounded
    successor.locator("#chat-input").fill(ASK_UNKNOWN)
    successor.locator("#chat-send").click()
    expect(successor.locator(".bot-answer")).to_have_count(4)
    grounded = successor.locator(".bot-answer").last
    expect(grounded).not_to_have_class(re.compile(r"\bnot-found\b"))
    expect(grounded.locator(".answer-text")).to_have_text(f"인수인계 기록에 따르면: {ANSWER_CACHE}")
    expect(grounded.locator(".citation-meta").first).to_contain_text("세션 2 (담당 시스템)")
    expect(grounded.locator(".certainty")).to_have_text("근거 확실")
    expect(grounded.locator(".queued-notice")).to_have_count(0)
    shot(successor, 18, "successor-grounded-answer")

    # 19 — successor opens the manager report: blocked
    successor.goto(f"{base}/report")
    expect(successor.locator("#report-error")).to_have_text(MSG_FORBIDDEN)
    expect(successor.locator("#report-error")).to_be_visible()
    expect(successor.locator(".axis-row")).to_have_count(0)
    expect(successor.locator("#priority-send")).to_be_disabled()
    shot(successor, 19, "successor-report-forbidden")

    # 20 — manager opens a leaver session's depth API directly: 403
    resp = manager.goto(f"{base}/sessions/{session_id}/depth")
    assert resp is not None
    assert resp.status == 403
    expect(manager.locator("body")).to_contain_text('"detail":"forbidden"')
    shot(manager, 20, "manager-depth-forbidden")


def test_demo_invite_link_is_required(
    start_server: StartServer, browser: Browser, shot_dir: Path, tmp_path: Path
) -> None:
    """Opening a screen without an invite link shows the login message, not data."""
    server = start_server(tmp_path / "data", {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET})
    page = _window(browser)
    page.goto(f"{server.url}/report")
    expect(page.locator("#report-error")).to_have_text("초대 링크로 들어와 주세요.")
    page.goto(f"{server.url}/chat")
    expect(page.locator("#chat-error")).to_have_text("초대 링크로 들어와 주세요.")
    page.goto(f"{server.url}/")
    expect(page.locator("#error")).to_have_text("초대 링크로 들어와 주세요.")
    expect(page.locator("#start-button")).to_be_disabled()
    resp = page.goto(f"{server.url}/auth/accept?token=forged")
    assert resp is not None
    assert resp.status == 401
