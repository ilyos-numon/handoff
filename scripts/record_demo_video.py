"""Record the README demo video (mp4) and highlight GIF from the replay demo server.

Usage: uv run python scripts/record_demo_video.py

Starts ``handoff-demo`` on a free port (cassette replay, no LLM calls), drives the
/demo split view through steps 11-18 of the recording order, then encodes with ffmpeg.
Intermediate files go to out/video/ (gitignored); results to docs/media/.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "out" / "video"
DATA_DIR = VIDEO_DIR / "data"
MEDIA_DIR = ROOT / "docs" / "media"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
LOG_DIR = ROOT / ".ai" / "tasks" / "T-20261023-portfolio-demo" / "logs"

MP4_LIMIT = 4194304
GIF_LIMIT = 5242880
GIF_SECONDS = 24

Q_PAYMENT = "결제 대행사 장애가 나면 무엇을 먼저 확인하나요?"
Q_CACHE = "캐시 서버를 재시작해야 할 때는 어떻게 하나요?"
FENCE_SUBJECT = "금요일 배포 금지"
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


def drive(page: Page, base: str) -> None:
    page.goto(f"{base}/demo")
    manager = page.frame_locator("#demo-pane-manager")
    leaver = page.frame_locator("#demo-pane-leaver")
    successor = page.frame_locator("#demo-pane-successor")
    manager.locator("#axes .axis-row").first.wait_for(timeout=30000)
    leaver.locator("#start-button").wait_for(state="visible")
    hold(page, 5)

    # 11 successor asks about a payment provider outage
    successor.locator("#chat-send").wait_for()
    page.wait_for_function(
        "() => !document.getElementById('demo-pane-successor').contentDocument"
        ".getElementById('chat-send').disabled"
    )
    successor.locator("#chat-input").press_sequentially(Q_PAYMENT, delay=40)
    hold(page, 1)
    successor.locator("#chat-send").click()
    successor.locator(".bot-answer").nth(0).wait_for(timeout=30000)
    hold(page, 5)

    # 12 successor asks about restarting the cache server (not in the notes yet)
    successor.locator("#chat-input").press_sequentially(Q_CACHE, delay=40)
    hold(page, 1)
    successor.locator("#chat-send").click()
    successor.locator(".bot-answer").nth(1).wait_for(timeout=30000)
    hold(page, 5)

    # 13 successor asks why Friday deploys are forbidden
    successor.locator("#fence-input").press_sequentially(FENCE_SUBJECT, delay=40)
    hold(page, 1)
    successor.locator("#fence-send").click()
    successor.locator(".bot-answer").nth(2).wait_for(timeout=30000)
    hold(page, 5)

    # 14 manager screen: coverage and the question queue (reload to show the new question)
    manager.locator("#queue").scroll_into_view_if_needed()
    page.evaluate(
        "() => document.getElementById('demo-pane-manager').contentWindow.location.reload()"
    )
    manager.locator("#queue .queue-item").first.wait_for(timeout=30000)
    hold(page, 5)

    # 15 manager finds the gaps
    manager.locator("#gaps-refresh").click()
    manager.locator("#gaps .gap").first.wait_for(timeout=30000)
    hold(page, 5)

    # 16 manager builds the runbook
    manager.locator("#artifact-buttons button[data-kind=runbook]").click()
    manager.locator("#artifacts .artifact").first.wait_for(timeout=30000)
    hold(page, 5)

    # 17 the leaver answers the cache question in a session
    leaver.locator("#start-button").click()
    leaver.locator("#transcript .msg.bot").first.wait_for(timeout=30000)
    hold(page, 3)
    leaver.locator("#answer-input").press_sequentially(LEAVER_ANSWER, delay=40)
    hold(page, 1)
    leaver.locator("#send-button").click()
    leaver.locator("#transcript .msg.user").first.wait_for(timeout=30000)
    hold(page, 5)
    leaver.locator("#end-button").click()
    leaver.locator("#summary").wait_for(state="visible", timeout=30000)
    hold(page, 5)

    # 18 the same cache question again, now answered from the leaver's session
    successor.locator("#chat-input").press_sequentially(Q_CACHE, delay=40)
    hold(page, 1)
    successor.locator("#chat-send").click()
    successor.locator(".bot-answer").nth(3).wait_for(timeout=30000)
    hold(page, 9)

    # the one role switch: the bar opens the manager screen full size
    page.locator("#demo-role-manager").click()
    page.wait_for_url("**/report*", timeout=30000)
    page.locator("#queue .queue-item").first.wait_for(timeout=30000)
    hold(page, 8)
    page.locator("#artifacts").scroll_into_view_if_needed()
    hold(page, 5)


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
        )
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
    (LOG_DIR / "s16-llm-stats.log").write_text(line + "\n" + json.dumps(llm) + "\n")
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
