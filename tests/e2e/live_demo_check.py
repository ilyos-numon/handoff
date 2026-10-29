"""Public demo smoke (opt-in, read-only).

Run: HANDOFF_LIVE_URL=https://... uv run pytest tests/e2e/live_demo_check.py -q
"""

import os

import httpx
import pytest
from playwright.sync_api import sync_playwright

TIMEOUT_S = 90


@pytest.fixture
def base() -> str:
    url = os.environ.get("HANDOFF_LIVE_URL", "")
    assert url, (
        "HANDOFF_LIVE_URL 이 필요합니다: "
        "HANDOFF_LIVE_URL=https://... uv run pytest tests/e2e/live_demo_check.py"
    )
    return url.rstrip("/")


def test_health_ok(base: str) -> None:
    assert httpx.get(f"{base}/health", timeout=TIMEOUT_S, follow_redirects=True).status_code == 200


def test_demo_page_has_three_iframes(base: str) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(TIMEOUT_S * 1000)
            resp = page.goto(f"{base}/demo")
            assert resp is not None and resp.status == 200
            page.locator("iframe").nth(2).wait_for(state="attached")
            assert page.locator("iframe").count() == 3
        finally:
            browser.close()


def test_unknown_role_intro_is_404(base: str) -> None:
    assert httpx.get(f"{base}/intro/ceo", timeout=TIMEOUT_S, follow_redirects=True).status_code == 404
