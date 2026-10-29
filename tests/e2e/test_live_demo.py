"""Public demo smoke (opt-in): set HANDOFF_LIVE_URL to run. Read-only requests."""

import os

import httpx
import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.skipif(
    not os.environ.get("HANDOFF_LIVE_URL"),
    reason="HANDOFF_LIVE_URL 없음: 공개 데모 네트워크 테스트는 선택 실행",
)
TIMEOUT_S = 90


def _base() -> str:
    return os.environ["HANDOFF_LIVE_URL"].rstrip("/")


def test_health_ok() -> None:
    assert httpx.get(f"{_base()}/health", timeout=TIMEOUT_S, follow_redirects=True).status_code == 200


def test_demo_page_has_three_iframes() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(TIMEOUT_S * 1000)
            resp = page.goto(f"{_base()}/demo")
            assert resp is not None and resp.status == 200
            page.locator("iframe").nth(2).wait_for(state="attached")
            assert page.locator("iframe").count() == 3
        finally:
            browser.close()


def test_unknown_role_intro_is_404() -> None:
    assert httpx.get(f"{_base()}/intro/ceo", timeout=TIMEOUT_S, follow_redirects=True).status_code == 404
