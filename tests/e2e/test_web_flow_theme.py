"""Screen brightness toggle: device default, persistence, keyboard, intro pages, blocked storage, demo head."""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET = "e2e-web-theme-secret"
LEAVER = "kim"
LIGHT_BG = "rgb(246, 245, 241)"
DARK_BG = "rgb(18, 19, 20)"
MANAGER_H1 = "무엇이 아직 안 넘어왔나"
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


def _invite(url: str, data_dir: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "HANDOFF_LLM_API_KEY"}
    env.update(
        HANDOFF_DATA_DIR=str(data_dir), HANDOFF_AUTH="on", HANDOFF_AUTH_SECRET=SECRET, HANDOFF_PROVIDER="fake"
    )
    out = subprocess.run(
        [sys.executable, "-m", "handoff.rbac.cli", "invite", "--role", "manager", "--leaver", LEAVER,
         "--base-url", url],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _ctx(browser: Browser, scheme: str, width: int = 1100, height: int = 900) -> BrowserContext:
    return browser.new_context(
        viewport={"width": width, "height": height}, color_scheme=scheme, reduced_motion="reduce"  # type: ignore[arg-type]
    )


def _server(web_server: Callable[..., Any], tmp_path: Path) -> tuple[str, Path]:
    data_dir = tmp_path / "data"
    url = web_server(data_dir, {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": SECRET}).url
    return url, data_dir


def _report(ctx: BrowserContext, url: str, data_dir: Path) -> Page:
    page = ctx.new_page()
    page.set_default_timeout(20_000)
    page.goto(_invite(url, data_dir))
    page.goto(f"{url}/report")
    expect(page.get_by_role("heading", name=MANAGER_H1)).to_be_visible()
    return page


def _toggle(page: Page, label: str) -> Any:
    btn = page.get_by_role("button", name=f"화면 밝기: {label}", exact=True)
    expect(btn).to_be_visible()
    return btn


def _shot(page: Page, shot_dir: Path, name: str) -> None:
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(shot_dir / f"{name}.png"), full_page=True)


def _bg(page: Page, selector: str = "body") -> str:
    return str(page.eval_on_selector(selector, "e => getComputedStyle(e).backgroundColor"))


def test_dark_device_default_then_light_persists_then_system(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)
    page = _report(_ctx(browser, "dark"), url, data_dir)
    expect(page.locator("body")).to_have_css("background-color", DARK_BG)
    dark_top = _bg(page, ".shell-top")
    _shot(page, shot_dir, "theme-dark-report")

    _toggle(page, "기기 설정").click()
    _toggle(page, "밝게")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    expect(page.locator("body")).to_have_css("background-color", LIGHT_BG)
    assert _bg(page, ".shell-top") != dark_top
    _shot(page, shot_dir, "theme-light-report")

    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    expect(page.locator("body")).to_have_css("background-color", LIGHT_BG)
    _toggle(page, "밝게").click()
    _toggle(page, "어둡게").click()
    _toggle(page, "기기 설정")
    expect(page.locator("html")).not_to_have_attribute("data-theme", "light")
    assert page.evaluate("document.documentElement.hasAttribute('data-theme')") is False
    expect(page.locator("body")).to_have_css("background-color", DARK_BG)


def test_light_device_default(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)
    page = _report(_ctx(browser, "light"), url, data_dir)
    expect(page.locator("body")).to_have_css("background-color", LIGHT_BG)
    _toggle(page, "기기 설정")


def test_keyboard_toggle(browser: Browser, web_server: Callable[..., Any], tmp_path: Path) -> None:
    url, data_dir = _server(web_server, tmp_path)
    page = _report(_ctx(browser, "dark"), url, data_dir)
    _toggle(page, "기기 설정").focus()
    page.keyboard.press("Enter")
    _toggle(page, "밝게")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    page.keyboard.press("Space")
    _toggle(page, "어둡게")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


def test_intro_and_role_intro_toggle(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url, _ = _server(web_server, tmp_path)
    page = _ctx(browser, "dark").new_page()
    page.set_default_timeout(20_000)
    page.goto(f"{url}/intro")
    expect(page.locator("body")).to_have_css("background-color", DARK_BG)
    _toggle(page, "기기 설정")
    _shot(page, shot_dir, "theme-dark-intro")
    page.get_by_role("button", name="화면 밝기: 기기 설정", exact=True).click()
    expect(page.locator("body")).to_have_css("background-color", LIGHT_BG)
    _shot(page, shot_dir, "theme-light-intro")

    page.goto(f"{url}/intro/leaver")
    expect(page.locator("body")).to_have_css("background-color", LIGHT_BG)
    _toggle(page, "밝게")


def test_storage_blocked_still_renders(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path
) -> None:
    url, _ = _server(web_server, tmp_path)
    ctx = _ctx(browser, "dark")
    ctx.add_init_script(
        "Object.defineProperty(window,'localStorage',"
        "{get(){throw new DOMException('blocked','SecurityError')}})"
    )
    page = ctx.new_page()
    page.set_default_timeout(20_000)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{url}/intro")
    expect(page.locator(".hero-title")).to_be_visible()
    expect(page.locator("body")).to_have_css("background-color", DARK_BG)
    _toggle(page, "기기 설정").click()
    expect(page.locator("body")).to_have_css("background-color", LIGHT_BG)
    page.reload()
    expect(page.locator(".hero-title")).to_be_visible()
    expect(page.locator("body")).to_have_css("background-color", DARK_BG)
    assert errors == []


def test_other_tab_follows(browser: Browser, web_server: Callable[..., Any], tmp_path: Path) -> None:
    url, _ = _server(web_server, tmp_path)
    ctx = _ctx(browser, "dark")
    a, b = ctx.new_page(), ctx.new_page()
    a.goto(f"{url}/intro")
    b.goto(f"{url}/intro")
    _toggle(b, "기기 설정")
    _toggle(a, "기기 설정").click()
    expect(b.locator("html")).to_have_attribute("data-theme", "light", timeout=5000)


def test_shell_390_one_line(
    browser: Browser, web_server: Callable[..., Any], tmp_path: Path, shot_dir: Path
) -> None:
    url, data_dir = _server(web_server, tmp_path)
    page = _report(_ctx(browser, "dark", 390, 844), url, data_dir)
    for scheme_label, name in (("기기 설정", "dark"), ("기기 설정", "light")):
        if name == "light":
            _toggle(page, scheme_label).click()
            expect(page.locator("body")).to_have_css("background-color", LIGHT_BG)
        inner = page.locator(".shell-top-inner")
        fits = inner.evaluate("e => e.scrollWidth <= e.clientWidth")
        assert fits is True
        assert page.locator(".shell-top").bounding_box()["height"] <= 64  # type: ignore[index]
        expect(page.locator(".theme-toggle")).to_be_visible()
        _shot(page, shot_dir, f"shell-390-{name}")


def test_demo_head_brand_and_iframes(
    web_server: Callable[..., Any], browser: Browser, tmp_path: Path, shot_dir: Path
) -> None:
    base = web_server(tmp_path / "data", DEMO_ENV).url
    for width, height in ((1440, 900), (390, 844)):
        ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme="dark")
        page = ctx.new_page()
        page.set_default_timeout(30_000)
        page.goto(f"{base}/demo")
        expect(page.locator("iframe")).to_have_count(3)
        brand = page.locator("header.demo-head a.brand")
        expect(brand).to_be_visible()
        assert brand.bounding_box()["height"] <= 36  # type: ignore[index]
        assert page.locator("header.demo-head").evaluate("e => e.scrollWidth <= e.clientWidth") is True
        if width == 1440:
            page.get_by_role("button", name="화면 밝기: 기기 설정", exact=True).click()
            for i in range(3):
                frame = page.frame_locator("iframe").nth(i)
                expect(frame.locator("html")).to_have_attribute("data-theme", "light")
                expect(frame.locator(".theme-toggle")).to_have_count(0)
            for i in range(3):  # let finite transitions finish before asserting colors / shooting
                page.frame_locator("iframe").nth(i).locator("html").evaluate(
                    "() => Promise.all(document.getAnimations()"
                    ".filter((a) => a.effect.getComputedTiming().endTime !== Infinity).map((a) => a.finished))"
                )
            chip = page.frame_locator("iframe").nth(2).locator("button.chip").first
            expect(chip).to_have_css("background-color", "rgb(255, 255, 255)")
            primary = page.frame_locator("iframe").nth(1).locator("button.btn.primary").first
            expect(primary).to_have_css("background-color", "rgb(31, 92, 76)")
        _shot(page, shot_dir, f"demo-head-{width}")
        ctx.close()
