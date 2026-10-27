"""Flow (c): manager-led handoff with auth on, one browser context per role (new web)."""

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
SECRET = "e2e-web-flow-c-secret"
LEAVER = "kim"
PRIORITY_Q = "배포는 왜 금요일에 하지 않나요?"
PRIORITY_A = "금요일 배포는 결제 정산 배치와 겹쳐 장애가 나도 대응할 사람이 없어서 하지 않습니다. 월요일에 배포합니다."
MARKER = "팀장이 우선 지정한 질문"


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


def _page(browser: Browser, url: str, link: str, home: str) -> Page:
    page = browser.new_context(viewport={"width": 1100, "height": 900}, reduced_motion="reduce").new_page()
    page.set_default_timeout(20_000)
    page.goto(link)
    page.goto(f"{url}{home}")
    return page


def test_manager_led_handoff_with_roles(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    data_dir = tmp_path / "data"
    url = web_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET}).url

    def shot(page: Page, n: int, step: str) -> None:
        page.screenshot(path=str(shot_dir / f"web-c-{n:02d}-{step}.png"), full_page=True)

    mgr = _page(browser, url, _invite(url, data_dir, "manager"), "/report")
    lea = _page(browser, url, _invite(url, data_dir, "leaver"), "/")
    suc = _page(browser, url, _invite(url, data_dir, "successor"), "/chat")

    # manager: mock sources and a priority question
    expect(mgr.get_by_role("heading", name="무엇이 아직 안 넘어왔나")).to_be_visible()
    mgr.get_by_role("button", name="모의 자료 불러오기").click()
    expect(mgr.get_by_text(re.compile(r"모의 자료 \d+건을 넣었습니다"))).to_be_visible()
    mgr.get_by_label("우선 질문").fill(PRIORITY_Q)
    mgr.get_by_role("button", name="큐에 넣기").click()
    expect(mgr.get_by_text("우선 질문을 큐 맨 앞에 넣었습니다.")).to_be_visible()
    shot(mgr, 1, "manager-priority-queued")

    # leaver: the first question is the manager's priority question, with its source
    expect(lea.get_by_role("heading", name=re.compile("질문 몇 개에 답하면"))).to_be_visible()
    lea.get_by_role("button", name="세션 시작").click()
    expect(lea.get_by_text(PRIORITY_Q)).to_be_visible()
    expect(lea.get_by_text(MARKER)).to_be_visible()
    shot(lea, 2, "leaver-first-question-from-manager")

    box = lea.get_by_label("답변")
    box.fill(PRIORITY_A)
    lea.get_by_role("button", name=re.compile("보내기")).click()
    expect(box).to_have_value("")
    done = lea.get_by_role("heading", name="오늘은 여기까지입니다.", exact=True)
    if not done.is_visible():
        lea.get_by_role("button", name="오늘은 여기까지").click()
    expect(done).to_be_visible()
    shot(lea, 3, "leaver-closed")

    # manager: coverage reflects session 1, gaps, runbook
    mgr.reload()
    expect(mgr.get_by_role("heading", name="무엇이 아직 안 넘어왔나")).to_be_visible()
    expect(mgr.get_by_text(re.compile(r"세션 1/\d+ 기준"))).to_be_visible()
    mgr.get_by_role("button", name="다시 찾기").click()
    expect(mgr.get_by_text(re.compile(r"빈 구멍 \d+개를 찾았고"))).to_be_visible()
    mgr.get_by_role("button", name="만들기", exact=True).first.click()
    expect(mgr.get_by_role("button", name=re.compile(r"운영 런북\s*절 \d+개"))).to_be_enabled()
    shot(mgr, 4, "manager-coverage-gaps-runbook")

    # change-impact panel: cards for the chosen event are listed, but never the leaver's answer text
    event = "system_replaced"
    panel = mgr.get_by_role("heading", name="조직이 바뀌면 틀려질 지식")
    expect(panel).to_be_visible()
    mgr.get_by_label("변화").select_option(label="시스템 교체·이전")
    path = f"/leavers/{LEAVER}/cards/at-risk?event={event}"
    manager_cards = mgr.context.request.get(f"{url}{path}").json()["cards"]
    assert len(manager_cards) >= 1
    for item in manager_cards:
        assert not {"evidence", "body", "uncertain_cues"} & set(item["card"]), item["card"].keys()
    expect(mgr.get_by_text(manager_cards[0]["card"]["title"]).first).to_be_visible()
    expect(mgr.get_by_text(PRIORITY_A)).to_have_count(0)
    shot(mgr, 5, "manager-risk-cards-no-answer-text")

    # the same request from the successor context does carry evidence
    successor_cards = suc.context.request.get(f"{url}{path}").json()["cards"]
    assert len(successor_cards) >= 1
    assert all(item["card"].get("evidence") for item in successor_cards)

    # successor reads the runbook in the handed-over documents
    suc.get_by_role("tab", name="인수인계 문서").click()
    expect(suc.get_by_role("heading", name="운영 런북").first).to_be_visible()
    expect(suc.get_by_role("list").get_by_role("heading", level=3).first).to_be_visible()
    shot(suc, 6, "successor-reads-runbook")
