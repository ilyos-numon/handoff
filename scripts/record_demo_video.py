"""Record the README demo video (mp4) and highlight GIF from the replay demo server.

Usage: uv run python scripts/record_demo_video.py

Starts ``handoff-demo`` on a free port (cassette replay, no LLM calls), drives the
/demo split view (iframes by title, manager tabs) through steps 11-18 in cassette order, then encodes with ffmpeg.
Intermediate files go to out/video/ (gitignored); results to docs/media/.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import FrameLocator, Locator, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "out" / "video"
DATA_DIR = VIDEO_DIR / "data"
MEDIA_DIR = ROOT / "docs" / "media"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
LOG_DIR = ROOT / ".ai" / "tasks" / "T-20261029-public-release" / "logs"

MP4_LIMIT = 4194304
GIF_LIMIT = 5242880
GIF_SECONDS = 24

Q_PAYMENT = "결제 대행사 장애가 나면 무엇을 먼저 확인하나요?"
Q_CACHE = "캐시 서버를 재시작해야 할 때는 어떻게 하나요?"
FENCE_SUBJECT = "금요일 배포 금지"
MANAGER_H1 = "무엇이 아직 안 넘어왔나"
SUCCESSOR_H1 = "막히면 먼저 기록에 물어보세요"
LEAVER_ANSWER = "캐시 서버는 트래픽이 적은 새벽에 한 대씩 재시작하고, 재시작 후 적중률을 확인합니다."


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return int(s.getsockname()[1])


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as res:
        return json.loads(res.read())


def start_server(port: int) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if "KEY" not in k and "TOKEN" not in k}
    env["HANDOFF_PROVIDER"] = "replay"
    log = open(VIDEO_DIR / "server.log", "w")  # noqa: SIM115
    proc = subprocess.Popen(
        ["uv", "run", "handoff-demo", "--port", str(port), "--data-dir", str(DATA_DIR)],
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


def hold(page: Page, seconds: float) -> None:
    page.wait_for_timeout(int(seconds * 1000))


def to_top(target: Locator) -> None:
    """Scroll inside the pane so the newest result starts at the top of the frame."""
    target.evaluate("(el) => el.scrollIntoView({block: 'start', behavior: 'smooth'})")


def pane(page: Page, role: str) -> FrameLocator:
    return page.frame_locator(f"iframe[title='{role} 화면']")


def ask(page: Page, successor: FrameLocator, question: str, answers: int) -> None:
    box = successor.get_by_label("질문", exact=True)
    expect(box).to_be_enabled(timeout=30000)
    box.click()
    box.press_sequentially(question, delay=45)
    hold(page, 1.2)
    successor.get_by_role("button", name="묻기", exact=True).click()
    expect(successor.locator(".qa .answer")).to_have_count(answers, timeout=30000)
    expect(box).to_be_enabled(timeout=30000)
    to_top(successor.locator(".qa").first)


def drive(page: Page, base: str) -> None:
    page.goto(f"{base}/demo")
    manager = pane(page, "팀장")
    leaver = pane(page, "퇴사자")
    successor = pane(page, "후임")
    expect(manager.get_by_role("heading", name=MANAGER_H1)).to_be_visible(timeout=60000)
    expect(successor.get_by_role("heading", name=SUCCESSOR_H1)).to_be_visible(timeout=30000)
    expect(leaver.get_by_role("button", name="세션 시작")).to_be_visible(timeout=30000)
    expect(manager.locator(".skeleton, .spinner")).to_have_count(0, timeout=30000)
    hold(page, 3)

    # 11 successor asks about a payment provider outage: answered with the session 1 citation
    ask(page, successor, Q_PAYMENT, 1)
    hold(page, 5.5)

    # 12 successor asks about restarting the cache server: not in the notes, queued for the leaver
    ask(page, successor, Q_CACHE, 2)
    expect(successor.get_by_text("퇴사자 질문 목록에 올렸습니다").first).to_be_visible()
    hold(page, 5.5)

    # 13 successor asks why Friday deploys are forbidden
    successor.get_by_role("tab", name="이건 왜 이렇게?").click()
    hold(page, 1.5)
    subject = successor.get_by_label("이유가 궁금한 것")
    subject.click()
    subject.press_sequentially(FENCE_SUBJECT, delay=60)
    hold(page, 1)
    successor.get_by_role("button", name="이유 찾기").click()
    expect(subject).to_be_enabled(timeout=30000)
    to_top(subject)
    hold(page, 5)

    # 14-15 manager: reload so the new questions show, status tab, "?" help, find gaps
    page.evaluate(
        "() => document.querySelector(\"iframe[title='팀장 화면']\").contentWindow.location.reload()"
    )
    expect(manager.get_by_role("heading", name=MANAGER_H1)).to_be_visible(timeout=30000)
    expect(manager.locator(".skeleton, .spinner")).to_have_count(0, timeout=30000)
    hold(page, 1.5)
    manager.get_by_role("button", name="진행 상황 설명", exact=True).click()
    expect(manager.get_by_role("note", name="진행 상황 도움말")).to_be_visible()
    hold(page, 4)
    manager.locator("body").press("Escape")
    hold(page, 0.5)
    refresh = manager.get_by_role("button", name="다시 찾기")
    refresh.scroll_into_view_if_needed()
    hold(page, 1.5)
    refresh.click()
    expect(refresh).to_be_enabled(timeout=30000)
    expect(manager.locator(".skeleton, .spinner")).to_have_count(0, timeout=30000)
    hold(page, 3.5)

    # 14 manager: the successor's question waits in the queue for the next session
    manager.get_by_role("tab", name="질문 남기기", exact=True).click()
    expect(manager.get_by_label("우선 질문")).to_be_visible(timeout=30000)
    hold(page, 1.5)
    queued = manager.get_by_text(Q_CACHE).first
    queued.scroll_into_view_if_needed()
    hold(page, 4)

    # 16 manager builds the runbook
    manager.get_by_role("tab", name="자료와 문서", exact=True).click()
    hold(page, 1.5)
    tile = manager.locator(".art-tile").filter(has_text="운영 런북")
    tile.scroll_into_view_if_needed()
    hold(page, 0.8)
    tile.get_by_role("button", name=re.compile("만들기")).click()
    opened = manager.locator(".art-open")
    expect(opened).to_be_visible(timeout=30000)
    opened.scroll_into_view_if_needed()
    hold(page, 4.5)

    # 17 the leaver answers the cache question in session 2
    leaver.get_by_role("button", name="세션 시작").click()
    answer = leaver.get_by_label("답변")
    expect(answer).to_be_enabled(timeout=30000)
    hold(page, 2)
    answer.click()
    answer.press_sequentially(LEAVER_ANSWER, delay=35)
    hold(page, 1.2)
    leaver.get_by_role("button", name=re.compile("보내기")).click()
    expect(answer).to_have_value("", timeout=30000)
    expect(answer).to_be_enabled(timeout=30000)
    hold(page, 2)
    leaver.get_by_role("button", name="오늘은 여기까지").click()
    expect(leaver.get_by_role("heading", name="오늘은 여기까지입니다.", exact=True)).to_be_visible(
        timeout=30000
    )
    hold(page, 3.5)

    # 18 the same cache question again, now answered from session 2; then the handed-off thread
    successor.get_by_role("tab", name="질문하기").click()
    hold(page, 1)
    ask(page, successor, Q_CACHE, 3)
    hold(page, 4.5)
    successor.get_by_role("tab", name="넘긴 질문").click()
    hold(page, 1.5)
    successor.get_by_role("button", name=re.compile(re.escape(Q_CACHE))).first.click()
    thread = successor.get_by_role("region", name="질문 대화")
    expect(thread.get_by_text(Q_CACHE)).to_be_visible(timeout=30000)
    to_top(thread)
    hold(page, 4.5)

    # the one role switch: the bar opens the manager screen full size, my questions tab
    page.get_by_role("group", name="역할 바꾸기").get_by_role("button", name="팀장").click()
    page.wait_for_url("**/report*", timeout=30000)
    expect(page.get_by_role("heading", name=MANAGER_H1)).to_be_visible(timeout=30000)
    expect(page.locator(".skeleton, .spinner")).to_have_count(0, timeout=30000)
    hold(page, 2)
    page.get_by_role("tab", name="질문 남기기", exact=True).click()
    hold(page, 1.5)
    mine = page.get_by_role("heading", name="내가 남긴 질문")
    expect(mine).to_be_visible(timeout=30000)
    mine.evaluate("(el) => window.scrollBy({top: el.getBoundingClientRect().top - 90, behavior: 'smooth'})")
    hold(page, 4)
    page.get_by_role("button", name=re.compile("^화면 밝기")).first.click()
    hold(page, 3)


def record(port: int) -> Path:
    base = f"http://localhost:{port}"
    for old in VIDEO_DIR.glob("*.webm"):
        old.unlink()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1280, "height": 720},
            color_scheme="light",
        )
        # start light so the brightness button visibly switches to dark at the end
        context.add_init_script("try { localStorage.setItem('handoff.theme', 'light') } catch (e) {}")
        page = context.new_page()
        try:
            drive(page, base)
            status = context.request.get(f"{base}/demo/status").json()
        finally:
            context.close()
            browser.close()
    llm = status.get("llm", {})
    line = (
        f"LLM stats hits={llm.get('hits')} misses={llm.get('misses')} "
        f"fake_fallbacks={llm.get('fake_fallbacks')}"
    )
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "s4-llm-stats.log").write_text(line + "\n" + json.dumps(llm) + "\n")
    videos = sorted(VIDEO_DIR.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    return videos[-1]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def encode(webm: Path, crf: int, fps: int) -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    mp4 = MEDIA_DIR / "demo.mp4"
    gif = MEDIA_DIR / "demo.gif"
    run([
        FFMPEG, "-y", "-i", str(webm), "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(mp4),
    ])
    palette = VIDEO_DIR / "palette.png"
    filters = f"fps={fps},scale=800:-1:flags=lanczos"
    run([FFMPEG, "-y", "-t", str(GIF_SECONDS), "-i", str(webm), "-vf", f"{filters},palettegen",
         str(palette)])
    run([FFMPEG, "-y", "-t", str(GIF_SECONDS), "-i", str(webm), "-i", str(palette),
         "-lavfi", f"{filters}[x];[x][1:v]paletteuse", str(gif)])


def duration(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return float(out.strip())


def main() -> int:
    if VIDEO_DIR.exists():
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    port = free_port()
    server = start_server(port)
    try:
        webm = record(port)
    finally:
        server.terminate()
        server.wait(timeout=15)

    encode(webm, crf=32, fps=8)
    mp4 = MEDIA_DIR / "demo.mp4"
    gif = MEDIA_DIR / "demo.gif"
    if mp4.stat().st_size > MP4_LIMIT or gif.stat().st_size > GIF_LIMIT:
        print("size over limit, re-encoding once (crf 36, gif fps 6)", flush=True)
        encode(webm, crf=36, fps=6)
    print(f"mp4 bytes={mp4.stat().st_size} seconds={duration(mp4):.1f}")
    print(f"gif bytes={gif.stat().st_size}")
    if mp4.stat().st_size > MP4_LIMIT or gif.stat().st_size > GIF_LIMIT:
        print("BLOCKED: size still over limit", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
