"""MVP UI E2E: Playwright against a real server, with screenshots."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import Page, expect

ROLE = "backend_developer"
ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
ANSWER_FOLLOW = "복구는 큐를 비우고 정산 배치를 수동으로 재실행했습니다."
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"
QUERY_MISS = "PG사 장애 나면 누구한테 연락해요?"

StartServer = Callable[..., Any]


def _shot(page: Page, shot_dir: Path, name: str) -> None:
    page.screenshot(path=str(shot_dir / name), full_page=True)


def _seed_session(base_url: str, leaver: str, answers: list[str], end: bool = True) -> str:
    with httpx.Client(base_url=base_url, timeout=10) as client:
        resp = client.post("/sessions", json={"role": ROLE, "leaver": leaver})
        assert resp.status_code == 201
        sid = resp.json()["session_id"]
        for text in answers:
            assert client.post(f"/sessions/{sid}/answers", json={"text": text}).status_code == 200
        if end:
            assert client.post(f"/sessions/{sid}/end").status_code == 200
    return sid


def test_leaver_session_flow(
    start_server: StartServer, page: Page, shot_dir: Path, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    page.goto(f"{server.url}/?leaver=ui1")
    page.locator("#start-button").click()

    expect(page.locator("#session-number")).to_have_text("세션 1/10")
    expect(page.locator("#timer")).to_have_text(re.compile(r"^\d\d:\d\d 남음$"))
    expect(page.locator("#axis-label")).to_have_text("담당 시스템")
    _shot(page, shot_dir, "01-session-question.png")

    page.locator("#answer-input").fill(ANSWER_MAIN)
    page.locator("#send-button").click()
    expect(page.locator("#transcript .msg.bot")).not_to_have_count(1)
    assert page.locator("#transcript .msg.bot").count() >= 2
    expect(page.locator("#items-today")).to_contain_text("1항목")
    _shot(page, shot_dir, "02-session-followup.png")

    page.locator("#answer-input").fill(ANSWER_FOLLOW)
    page.locator("#send-button").click()
    expect(page.locator("#items-today")).to_contain_text("2항목")
    page.locator("#end-button").click()

    expect(page.locator("#summary")).to_be_visible()
    expect(page.locator("li.summary-line")).to_have_count(3)
    _shot(page, shot_dir, "03-session-summary.png")


def test_chat_answer_and_not_found(
    start_server: StartServer, page: Page, shot_dir: Path, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    _seed_session(server.url, "chat1", [ANSWER_MAIN])

    page.goto(f"{server.url}/chat?leaver=chat1")
    page.locator("#chat-input").fill(QUERY_HIT)
    page.locator("#chat-send").click()
    expect(page.locator(".citation-meta").first).to_contain_text("세션 1 (")
    _shot(page, shot_dir, "04-chat-answer.png")

    page.locator("#chat-input").fill(QUERY_MISS)
    page.locator("#chat-send").click()
    not_found = page.locator(".bot-answer.not-found")
    expect(not_found).to_have_count(1)
    expect(not_found.first).to_contain_text("이 주제는 인수인계에 없습니다")
    expect(not_found.first.locator(".citation")).to_have_count(0)
    expect(not_found.first.locator(".queued-notice")).to_contain_text("퇴사자 질문 목록에 올렸습니다")
    _shot(page, shot_dir, "05-chat-not-found.png")

    page.locator("#fence-input").fill("금요일 배포 금지")
    page.locator("#fence-send").click()
    fence = page.locator(".fence-answer")
    expect(fence).to_have_count(1)
    expect(fence.first).to_have_class(re.compile(r"\bnot-found\b"))
    expect(fence.first.locator(".answer-text")).to_have_text(
        "이 결정의 이유는 인수인계 기록에 없습니다."
    )
    expect(fence.first.locator(".certainty")).to_have_count(0)
    expect(fence.first.locator(".fence-reason")).to_have_count(0)
    expect(fence.first.locator(".queued-notice")).to_contain_text("퇴사자 질문 목록에 올렸습니다")
    if fence.first.locator(".fence-risk").count():
        expect(fence.first.locator(".fence-heading")).to_have_text("관련 위험:")


def test_report_shows_axes_and_empty_topics(
    start_server: StartServer, page: Page, shot_dir: Path, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    _seed_session(server.url, "rep1", [ANSWER_MAIN, ANSWER_FOLLOW])

    page.goto(f"{server.url}/report?leaver=rep1")
    expect(page.locator(".axis-row")).to_have_count(4)
    expect(page.locator(".axis-row.lowest")).to_have_count(1)
    expect(page.locator("li.empty-topic").first).to_be_visible()
    expect(page.locator("#report-title")).to_contain_text("세션 1/10 기준")
    _shot(page, shot_dir, "06-report.png")

    page.locator("#sources-mock").click()
    expect(page.locator("#report-status")).to_contain_text("불러왔습니다")
    page.locator("#questions-generate").click()
    expect(page.locator("#report-status")).to_contain_text("큐에 넣었습니다")
    page.locator("#gaps-refresh").click()
    expect(page.locator("#report-status")).to_contain_text("찾았습니다")
    gaps = page.locator("#gaps li.gap")
    expect(gaps.first).to_contain_text(re.compile(r"위험도 (높음|보통|낮음)$"))
    for text in gaps.all_inner_texts():
        assert not re.search(r"\b(high|medium|low)\b", text)


def test_timer_expiry_shows_summary(
    start_server: StartServer, page: Page, shot_dir: Path, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {"HANDOFF_SESSION_SECONDS": "3"})
    page.goto(f"{server.url}/?leaver=exp1")
    page.locator("#start-button").click()
    expect(page.locator("#session-number")).to_have_text("세션 1/10")

    expect(page.locator("#summary")).to_be_visible(timeout=10_000)
    expect(page.locator("li.summary-line")).to_have_count(3)
    _shot(page, shot_dir, "07-session-time-over.png")


def test_session_limit_error_message(
    start_server: StartServer, page: Page, shot_dir: Path, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {"HANDOFF_SESSIONS_PLANNED": "1"})
    _seed_session(server.url, "lim1", [ANSWER_MAIN])

    page.goto(f"{server.url}/?leaver=lim1")
    page.locator("#start-button").click()
    expect(page.locator("#error")).to_contain_text("모든 세션을 마쳤습니다")
    _shot(page, shot_dir, "08-session-limit-error.png")
