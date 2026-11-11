"""Demo mode E2E: public visitor flow with replayed LLM answers (no key, no network).

The server runs with HANDOFF_DEMO=on, HANDOFF_PROVIDER=replay and the committed
cassette. Screens are driven in the order of PLAN section 4.11 (steps 11-18);
a different order changes the prompts and the cassette would miss.
"""

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright

SECRET = "e2e-secret"
ROLE_HEADER = "X-Handoff-As"
ASK_KNOWN = "결제 대행사 장애가 나면 무엇을 먼저 확인하나요?"
ASK_UNKNOWN = "캐시 서버를 재시작해야 할 때는 어떻게 하나요?"
FENCE_SUBJECT = "금요일 배포 금지"
ANSWER_CACHE = "캐시 서버는 트래픽이 적은 새벽에 한 대씩 재시작하고, 재시작 후 적중률을 확인합니다."
PRIORITY_QUESTION = "결제 대행사 장애가 나면 가장 먼저 무엇을 확인하나요?"

StartServer = Callable[..., Any]

DEMO_ENV = {
    "HANDOFF_AUTH": "on",
    "HANDOFF_DEMO": "on",
    "HANDOFF_PROVIDER": "replay",
    "HANDOFF_AUTH_SECRET": SECRET,
    "HANDOFF_DEMO_START_RATE": "100/minute",
}


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _visitor(browser: Browser) -> BrowserContext:
    return browser.new_context(viewport={"width": 1500, "height": 900})


def _open_split(page: Page, base: str) -> str:
    """Open /demo, wait until the three panes are loaded, return the sandbox leaver id."""
    page.goto(f"{base}/demo")
    expect(page.locator("#demo-bar")).to_be_visible()
    manager = page.frame_locator("#demo-pane-manager")
    expect(manager.locator("#queue li.queue-item").first).to_be_visible()
    status = page.context.request.get(f"{base}/demo/status").json()
    assert status["demo"] is True
    leaver = status["leaver"]
    assert isinstance(leaver, str) and leaver.startswith("demo-")
    return leaver


def _start_server(start_server: StartServer, tmp_path: Path, extra: dict[str, str]) -> Any:
    return start_server(tmp_path / "data", {**DEMO_ENV, **extra})


def test_split_view_shows_three_panes(
    start_server: StartServer, browser: Browser, shot_dir: Path, tmp_path: Path
) -> None:
    """S1: a new visitor sees manager, leaver and successor panes, already seeded."""
    server = _start_server(start_server, tmp_path, {})
    page = _visitor(browser).new_page()
    _open_split(page, server.url)

    for role in ("manager", "leaver", "successor"):
        expect(page.locator(f"#demo-pane-{role}")).to_be_visible()
    manager = page.frame_locator("#demo-pane-manager")
    expect(
        manager.locator("#queue li.queue-item").filter(
            has_text=f"{PRIORITY_QUESTION} · 팀장 우선 · 답변됨"
        )
    ).to_have_count(1)
    leaver = page.frame_locator("#demo-pane-leaver")
    expect(leaver.locator("#start-button")).to_be_enabled()
    expect(page.frame_locator("#demo-pane-successor").locator("#chat-input")).to_be_visible()
    expect(page.locator("#demo-error")).to_be_hidden()
    page.screenshot(path=str(shot_dir / "pd-01-demo-split.png"), full_page=True)


def test_role_bar_switches_to_successor(
    start_server: StartServer, browser: Browser, shot_dir: Path, tmp_path: Path
) -> None:
    """S2: the role bar shows on a top-level screen and switches the cookie role."""
    server = _start_server(start_server, tmp_path, {})
    page = _visitor(browser).new_page()
    _open_split(page, server.url)

    page.goto(f"{server.url}/report")
    expect(page.locator("#demo-bar")).to_be_visible()
    expect(page.locator("#report-error")).to_be_hidden()
    expect(page.locator("#demo-split")).to_have_attribute("href", "/demo")
    page.screenshot(path=str(shot_dir / "pd-02-role-bar-manager.png"), full_page=True)

    page.locator("#demo-role-successor").click()
    expect(page).to_have_url(f"{server.url}/chat")
    expect(page.locator("#demo-bar")).to_be_visible()
    expect(page.locator("#chat-error")).to_be_hidden()
    me = page.context.request.get(f"{server.url}/auth/me").json()
    assert me["role"] == "successor"
    page.screenshot(path=str(shot_dir / "pd-03-switch-successor.png"), full_page=True)


def test_scenario_steps_replay_without_miss(
    start_server: StartServer, browser: Browser, shot_dir: Path, tmp_path: Path
) -> None:
    """S3: PLAN 4.11 steps 11-18 through the screens; the cassette must never miss."""
    server = _start_server(start_server, tmp_path, {})
    base = server.url
    page = _visitor(browser).new_page()
    _open_split(page, base)

    # 11-13 successor: known question, unknown question, fence question
    page.goto(f"{base}/chat?as=successor")
    expect(page.locator("#chat-error")).to_be_hidden()
    # the role bar highlights the ?as= role, not the cookie role (manager)
    expect(page.locator("#demo-role-successor")).to_have_class(re.compile(r"\bprimary\b"))
    expect(page.locator("#demo-role-manager")).not_to_have_class(re.compile(r"\bprimary\b"))
    page.locator("#chat-input").fill(ASK_KNOWN)
    page.locator("#chat-send").click()
    expect(page.locator(".bot-answer")).to_have_count(1)
    page.locator("#chat-input").fill(ASK_UNKNOWN)
    page.locator("#chat-send").click()
    expect(page.locator(".bot-answer")).to_have_count(2)
    expect(page.locator(".bot-answer.not-found")).to_have_count(1)
    page.locator("#fence-input").fill(FENCE_SUBJECT)
    page.locator("#fence-send").click()
    expect(page.locator(".fence-answer")).to_have_count(1)

    # 14-16 manager: report, gaps, runbook
    page.goto(f"{base}/report?as=manager")
    status = page.locator("#report-status")
    expect(page.locator("#queue li.queue-item").first).to_be_visible()
    page.locator("#gaps-refresh").click()
    expect(status).to_have_text(
        re.compile(r"^빈 구멍 \d+개, 아무도 확실히 모르는 영역 \d+개를 찾았습니다\.$")
    )
    page.locator('#artifact-buttons button[data-kind="runbook"]').click()
    expect(status).to_have_text("산출물을 만들었습니다: 운영 런북")

    # 17 leaver: first question is the successor's question, answer it, end session
    page.goto(f"{base}/?as=leaver")
    expect(page.locator("#start-button")).to_be_enabled()
    page.locator("#start-button").click()
    expect(page.locator("#question-text")).to_have_text(ASK_UNKNOWN)
    page.locator("#answer-input").fill(ANSWER_CACHE)
    page.locator("#send-button").click()
    expect(page.locator("#items-today")).to_have_text("오늘 쌓인 것: 1항목")
    page.locator("#end-button").click()
    expect(page.locator("#summary")).to_be_visible()

    # 18 successor asks again: grounded answer with a citation box, no phone number
    page.goto(f"{base}/chat?as=successor")
    page.locator("#chat-input").fill(ASK_UNKNOWN)
    page.locator("#chat-send").click()
    grounded = page.locator(".bot-answer").last
    expect(grounded).not_to_have_class(re.compile(r"\bnot-found\b"))
    expect(grounded.locator(".citation").first).to_be_visible()
    expect(grounded.locator(".answer-text")).to_contain_text("캐시 서버")
    assert "010-" not in page.locator("#chat-log").inner_text()
    page.screenshot(path=str(shot_dir / "pd-04-successor-replay-answer.png"), full_page=True)

    llm = page.context.request.get(f"{base}/demo/status").json()["llm"]
    assert llm is not None
    assert llm["misses"] == 0, llm
    assert llm["hits"] >= 10, llm


def test_visitors_are_isolated(
    start_server: StartServer, browser: Browser, shot_dir: Path, tmp_path: Path
) -> None:
    """S4: two visitors get different sandboxes and cannot read each other's queue."""
    server = _start_server(start_server, tmp_path, {})
    base = server.url
    first = _visitor(browser).new_page()
    second = _visitor(browser).new_page()
    leaver_a = _open_split(first, base)
    leaver_b = _open_split(second, base)
    assert leaver_a != leaver_b

    headers = {ROLE_HEADER: "manager"}
    own_a = first.context.request.get(f"{base}/leavers/{leaver_a}/queue", headers=headers)
    own_b = second.context.request.get(f"{base}/leavers/{leaver_b}/queue", headers=headers)
    assert own_a.status == 200
    assert own_b.status == 200
    cross_a = first.context.request.get(f"{base}/leavers/{leaver_b}/queue", headers=headers)
    cross_b = second.context.request.get(f"{base}/leavers/{leaver_a}/queue", headers=headers)
    assert cross_a.status == 403
    assert cross_b.status == 403
    expect(second.frame_locator("#demo-pane-manager").locator("#report-error")).to_be_hidden()
    second.screenshot(path=str(shot_dir / "pd-05-second-visitor-split.png"), full_page=True)


def test_upload_blocked_but_mock_sources_allowed(
    start_server: StartServer, browser: Browser, tmp_path: Path
) -> None:
    """S5: public demo refuses file upload; the built-in mock sources still load."""
    server = _start_server(start_server, tmp_path, {})
    base = server.url
    page = _visitor(browser).new_page()
    leaver = _open_split(page, base)
    headers = {ROLE_HEADER: "manager"}
    upload = page.context.request.post(
        f"{base}/leavers/{leaver}/sources",
        headers=headers,
        data={"kind": "doc", "title": "x", "text": "hello"},
    )
    assert upload.status == 403
    assert upload.json() == {"detail": "demo upload disabled"}
    mock = page.context.request.post(f"{base}/leavers/{leaver}/sources/mock", headers=headers)
    assert mock.status == 201  # the route answers 201 Created


def test_oversized_body_is_rejected(
    start_server: StartServer, browser: Browser, tmp_path: Path
) -> None:
    """S6: a 9000-byte body is refused with 413."""
    server = _start_server(start_server, tmp_path, {})
    base = server.url
    page = _visitor(browser).new_page()
    leaver = _open_split(page, base)
    res = page.context.request.post(
        f"{base}/kb/ask",
        headers={ROLE_HEADER: "successor"},
        data={"question": "가" * 9000, "leaver": leaver},
    )
    assert len(res.body()) < 1000
    assert res.status == 413
    assert res.json() == {"detail": "request too large"}


def test_start_rate_limit(start_server: StartServer, tmp_path: Path) -> None:
    """S7: with a 2/minute start limit, a third start from fresh cookies gets 429."""
    server = _start_server(start_server, tmp_path, {"HANDOFF_DEMO_START_RATE": "2/minute"})
    codes = []
    for _ in range(3):
        with httpx.Client(base_url=server.url, timeout=30) as fresh:
            codes.append(fresh.post("/demo/start").status_code)
    assert codes == [200, 200, 429]


def test_demo_off_has_no_role_bar(
    start_server: StartServer, browser: Browser, shot_dir: Path, tmp_path: Path
) -> None:
    """S8: with demo off the report page has no demo bar and /demo is 404."""
    server = start_server(
        tmp_path / "data",
        {"HANDOFF_AUTH": "off", "HANDOFF_DEMO": "off", "HANDOFF_PROVIDER": "fake"},
    )
    page = _visitor(browser).new_page()
    page.goto(f"{server.url}/report")
    expect(page.locator("#report-title")).to_be_visible()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#demo-bar")).to_have_count(0)
    page.screenshot(path=str(shot_dir / "pd-06-demo-off-report.png"), full_page=True)
    res = page.context.request.get(f"{server.url}/demo")
    assert res.status == 404
    status = page.context.request.get(f"{server.url}/demo/status").json()
    assert status["demo"] is False
