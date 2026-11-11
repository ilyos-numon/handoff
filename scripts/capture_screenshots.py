"""Capture the README screen gallery (10 PNGs) from the replay demo server.

Usage: uv run python scripts/capture_screenshots.py [--out docs/media/screenshots]

Starts ``handoff-demo`` on a free port (cassette replay, no LLM calls), walks the demo
scenario in the cassette's order (successor asks -> manager tabs -> leaver session 2 ->
successor thread) and saves one viewport screenshot per scene. Requires ``make web``.
Last stdout line: ``shots=10 max_bytes=<n> max_width=<n>``; exit 1 if any shot is over spec.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Locator, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "media" / "screenshots"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
MAX_BYTES = 409600
MAX_WIDTH = 1280
DESKTOP = {"width": 1280, "height": 800}
MOBILE = {"width": 390, "height": 844}
THEME_KEY = "handoff.theme"
# infinite ones (skeleton shimmer, spinner, intro scroll cue) never finish; skeleton/spinner are checked separately
FINITE_ANIMATIONS = (
    "Promise.all(document.getAnimations()"
    ".filter((a) => a.effect?.getTiming().iterations !== Infinity).map((a) => a.finished))"
)

ASK_KNOWN = "결제 대행사 장애가 나면 무엇을 먼저 확인하나요?"
ASK_UNKNOWN = "캐시 서버를 재시작해야 할 때는 어떻게 하나요?"
FENCE_SUBJECT = "금요일 배포 금지"
CACHE_ANSWER = "캐시 서버는 트래픽이 적은 새벽에 한 대씩 재시작하고, 재시작 후 적중률을 확인합니다."
PRIORITY_DRAFT = "정산 배치가 새벽에 실패하면 누가 깨어 있어야 하나요?"
MANAGER_H1 = "무엇이 아직 안 넘어왔나"
SUCCESSOR_H1 = "막히면 먼저 기록에 물어보세요"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return int(s.getsockname()[1])


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as res:
        return json.loads(res.read())


def start_server(port: int, data_dir: Path, log_path: Path) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if "KEY" not in k and "TOKEN" not in k}
    env["HANDOFF_PROVIDER"] = "replay"
    log = open(log_path, "w")  # noqa: SIM115
    proc = subprocess.Popen(
        ["uv", "run", "handoff-demo", "--port", str(port), "--data-dir", str(data_dir)],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        try:
            get_json(f"http://localhost:{port}/health")
            return proc
        except OSError:
            time.sleep(1)
    proc.terminate()
    raise RuntimeError("demo server did not become ready")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def png_width(path: Path) -> int:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return int(struct.unpack(">I", head[16:20])[0])


def shrink(path: Path) -> None:
    """Re-save an oversized PNG once with a 256-colour palette (ffmpeg), else downscale (sips)."""
    tmp = path.with_suffix(".tmp.png")
    if Path(FFMPEG).exists():
        subprocess.run(
            [FFMPEG, "-v", "error", "-y", "-i", str(path), "-vf",
             "split[a][b];[a]palettegen=max_colors=256[p];[b][p]paletteuse", str(tmp)],
            check=True,
        )
        tmp.replace(path)
    elif shutil.which("sips"):
        subprocess.run(["sips", "-Z", "1100", str(path)], check=True, capture_output=True)


def settle(page: Page) -> None:
    """Network idle, fonts loaded, CSS animations finished, no skeleton or spinner left."""
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    page.evaluate(FINITE_ANIMATIONS)
    expect(page.locator(".skeleton, .spinner")).to_have_count(0)


def settle_frames(page: Page) -> None:
    settle(page)
    for frame in page.frames[1:]:
        frame.wait_for_load_state("networkidle")
        frame.evaluate(FINITE_ANIMATIONS)
        expect(frame.locator(".skeleton, .spinner")).to_have_count(0)


def shot(page: Page, out: Path, name: str, target: Locator | None = None) -> Path:
    if target is not None:
        target.scroll_into_view_if_needed()
    page.evaluate("document.activeElement instanceof HTMLElement && document.activeElement.blur()")
    settle(page)
    path = out / name
    page.screenshot(path=str(path))
    if path.stat().st_size > MAX_BYTES:
        shrink(path)
    print(f"{name} bytes={path.stat().st_size} width={png_width(path)}", flush=True)
    return path


def new_context(browser: Browser, viewport: dict[str, int], scheme: str = "light",
                storage: dict | None = None) -> BrowserContext:
    ctx = browser.new_context(
        viewport=viewport,  # type: ignore[arg-type]
        device_scale_factor=1,
        color_scheme=scheme,  # type: ignore[arg-type]
        reduced_motion="reduce",
        storage_state=storage,  # type: ignore[arg-type]
    )
    ctx.add_init_script(f"try {{ localStorage.setItem('{THEME_KEY}', '{scheme}') }} catch (e) {{}}")
    return ctx


def open_page(ctx: BrowserContext, url: str) -> Page:
    page = ctx.new_page()
    page.set_default_timeout(30_000)
    page.goto(url)
    return page


def demo_split(page: Page, base: str, out: Path) -> None:
    page.goto(f"{base}/demo")
    for title in ("팀장 화면", "퇴사자 화면", "후임 화면"):
        expect(page.get_by_title(title)).to_be_visible()
    expect(page.frame_locator("iframe[title='팀장 화면']").get_by_role("heading", name=MANAGER_H1)).to_be_visible()
    expect(page.frame_locator("iframe[title='후임 화면']").get_by_role("heading", name=SUCCESSOR_H1)).to_be_visible()
    expect(page.frame_locator("iframe[title='퇴사자 화면']").get_by_role("button", name="세션 시작")).to_be_visible()
    settle_frames(page)
    shot(page, out, "01-demo-split.png")


def successor_asks(page: Page, base: str, out: Path) -> None:
    page.goto(f"{base}/chat?as=successor")
    expect(page.get_by_role("heading", name=SUCCESSOR_H1)).to_be_visible()
    page.get_by_role("button", name=ASK_KNOWN, exact=True).click()
    certainty = page.get_by_text(re.compile(r"근거 (확실|불확실)"))
    expect(certainty).to_be_visible()
    expect(page.get_by_label("질문", exact=True)).to_be_enabled()
    shot(page, out, "04-successor-answer.png", certainty)
    page.get_by_role("button", name=ASK_UNKNOWN, exact=True).click()
    expect(page.get_by_text("퇴사자 질문 목록에 올렸습니다").first).to_be_visible()
    expect(page.get_by_label("질문", exact=True)).to_be_enabled()
    # fence question keeps the cassette order (recorded right after the two asks)
    page.get_by_role("tab", name="이건 왜 이렇게?").click()
    page.get_by_label("이유가 궁금한 것").fill(FENCE_SUBJECT)
    page.get_by_role("button", name="이유 찾기").click()
    expect(page.get_by_label("이유가 궁금한 것")).to_be_enabled()
    settle(page)


def manager_tabs(page: Page, base: str, out: Path) -> None:
    page.goto(f"{base}/report?as=manager&tab=status")
    expect(page.get_by_role("heading", name=MANAGER_H1)).to_be_visible()
    settle(page)
    page.get_by_role("button", name="다시 찾기").click()
    expect(page.get_by_role("button", name="다시 찾기")).to_be_enabled()
    settle(page)
    page.get_by_role("button", name="진행 상황 설명", exact=True).click()
    note = page.get_by_role("note", name="진행 상황 도움말")
    expect(note).to_be_visible()
    shot(page, out, "06-manager-status-help.png")
    page.keyboard.press("Escape")

    page.get_by_role("tab", name="질문 남기기", exact=True).click()
    expect(page.get_by_label("우선 질문")).to_be_visible()
    page.get_by_label("우선 질문").fill(PRIORITY_DRAFT)  # draft only, not queued
    shot(page, out, "07-manager-ask.png")

    page.get_by_role("tab", name="자료와 문서", exact=True).click()
    tile = page.locator(".art-tile").filter(has_text="운영 런북")
    tile.get_by_role("button", name=re.compile("만들기")).click()
    opened = page.locator(".art-open")
    expect(opened).to_be_visible()
    panel_top = page.get_by_role("heading", name="인수인계 산출물").evaluate(
        "(el) => el.closest('section, .panel')?.getBoundingClientRect().top ?? el.getBoundingClientRect().top"
    )
    page.evaluate(f"window.scrollBy(0, {panel_top} - 72)")
    shot(page, out, "08-manager-docs-runbook.png")


def leaver_session(page: Page, base: str, out: Path) -> None:
    page.goto(f"{base}/?as=leaver")
    page.get_by_role("button", name="세션 시작").click()
    box = page.get_by_label("답변")
    expect(box).to_be_enabled()
    box.fill(CACHE_ANSWER)
    shot(page, out, "02-leaver-session.png")
    page.get_by_role("button", name=re.compile("보내기")).click()
    expect(box).to_have_value("")
    expect(box).to_be_enabled()
    page.get_by_role("button", name="오늘은 여기까지").click()
    done = page.get_by_role("heading", name="오늘은 여기까지입니다.", exact=True)
    expect(done).to_be_visible()
    shot(page, out, "03-leaver-summary.png")


def successor_thread(page: Page, base: str, out: Path) -> None:
    page.goto(f"{base}/chat?as=successor")
    expect(page.get_by_role("heading", name=SUCCESSOR_H1)).to_be_visible()
    page.get_by_role("tab", name="넘긴 질문").click()
    page.get_by_role("button", name=re.compile(re.escape(ASK_UNKNOWN))).first.click()
    thread = page.get_by_role("region", name="질문 대화")
    expect(thread.get_by_text(ASK_UNKNOWN)).to_be_visible()
    shot(page, out, "05-successor-thread.png")


def intro_dark(browser: Browser, base: str, out: Path) -> None:
    ctx = new_context(browser, DESKTOP, "dark")
    page = open_page(ctx, f"{base}/intro")
    roles = page.locator("a[href^='/intro/']")
    expect(roles).to_have_count(4)
    heading = page.get_by_role("heading", name="역할별 안내", exact=True)
    heading.scroll_into_view_if_needed()
    top = heading.evaluate("(el) => el.getBoundingClientRect().top")
    page.evaluate(f"window.scrollBy(0, {top} - 160)")
    expect(roles.first).to_be_in_viewport()
    shot(page, out, "09-intro-roles-dark.png")
    ctx.close()


def mobile_successor(browser: Browser, base: str, out: Path, storage: dict) -> None:
    ctx = new_context(browser, MOBILE, "light", storage)
    page = open_page(ctx, f"{base}/chat?as=successor")
    expect(page.get_by_role("heading", name=SUCCESSOR_H1)).to_be_visible()
    page.get_by_role("button", name=ASK_KNOWN, exact=True).click()
    expect(page.get_by_text(re.compile(r"근거 (확실|불확실)"))).to_be_visible()
    page.evaluate("window.scrollTo(0, 0)")
    shot(page, out, "10-mobile-successor.png")
    ctx.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    out: Path = args.out if args.out.is_absolute() else ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    port = free_port()
    base = f"http://localhost:{port}"
    with tempfile.TemporaryDirectory(prefix="handoff-shots-") as tmp:
        proc = start_server(port, Path(tmp) / "data", Path(tmp) / "server.log")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    ctx = new_context(browser, DESKTOP)
                    page = open_page(ctx, f"{base}/demo")
                    demo_split(page, base, out)
                    successor_asks(page, base, out)
                    manager_tabs(page, base, out)
                    leaver_session(page, base, out)
                    successor_thread(page, base, out)
                    storage = ctx.storage_state()
                    intro_dark(browser, base, out)
                    mobile_successor(browser, base, out, storage)  # type: ignore[arg-type]
                    ctx.close()
                finally:
                    browser.close()
            llm = get_json(f"{base}/demo/status").get("llm")
            print(f"replay={llm}", flush=True)
        finally:
            stop_server(proc)

    names = sorted(p for p in out.glob("[0-9][0-9]-*.png"))
    max_bytes = max(p.stat().st_size for p in names)
    max_width = max(png_width(p) for p in names)
    print(f"shots={len(names)} max_bytes={max_bytes} max_width={max_width}")
    ok = len(names) == 10 and max_bytes <= MAX_BYTES and max_width <= MAX_WIDTH
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
