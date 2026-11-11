"""Try the real (auth on) flow: admin, leaver, successor and manager windows at once.

Usage: make try [TRY_ARGS="--port 8781 --data-dir ./data/try"]
   or: uv run python scripts/try_handoff.py [--port INT] [--data-dir PATH] [--alias TEXT]
                                            [--headless] [--exit-after-open]
                                            [--screenshot-dir PATH]

Starts the server with auth on and the fake provider, registers a leaver through the
admin API, prints the four role links and opens one browser context per role (cookies
kept apart). Ctrl+C, SIGTERM or closing every window stops the server too.
Exit codes: 0 normal, 1 server/API failure, 2 port in use, 3 Playwright chromium missing.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import FrameType
from typing import IO
from urllib.parse import urlparse

import httpx
from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from handoff.rbac.tokens import ADMIN_ROLE, ADMIN_SCOPE, Principal, issue_invite, load_secret

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8780
DEFAULT_ALIAS = "결제팀 백엔드 A"
READY_TIMEOUT_S = 20.0
POLL_INTERVAL_S = 0.2
STDERR_TAIL = 2000

ROLE_ORDER = ("admin", "leaver", "successor", "manager")
ROLE_LABEL = {"admin": "관리자", "leaver": "퇴사자", "successor": "후임", "manager": "팀장"}
HOME = {"admin": "/admin", "leaver": "/", "successor": "/chat", "manager": "/report"}

NOTICE = (
    "안내: 이 창들은 역할마다 쿠키가 분리돼 있습니다. "
    "링크를 다른 곳에서 열려면 역할마다 다른 브라우저(또는 프로필)를 쓰세요. "
    "한 브라우저에서 링크를 여러 개 열면 마지막 링크의 역할로 바뀝니다."
)
STOP_HINT = "종료: Ctrl+C 를 누르거나 브라우저 창을 모두 닫으면 서버도 함께 꺼집니다."
NO_CHROMIUM = (
    "Playwright 크로미움이 설치돼 있지 않습니다. 먼저 실행하세요: uv run playwright install chromium"
)


class _Terminated(BaseException):
    """Raised from the SIGTERM handler so it takes the same cleanup path as Ctrl+C."""


class _Failure(Exception):
    """Server did not start or an API call failed (exit code 1)."""


def _on_sigterm(signum: int, frame: FrameType | None) -> None:
    raise _Terminated


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/try_handoff.py")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--alias", default=DEFAULT_ALIAS)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--exit-after-open", action="store_true")
    parser.add_argument("--screenshot-dir", type=Path, default=None)
    return parser


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("localhost", port)) == 0


class _Server:
    def __init__(self, port: int, data_dir: Path) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("HANDOFF_")}
        env |= {
            "HANDOFF_AUTH": "on",
            "HANDOFF_PROVIDER": "fake",
            "HANDOFF_DEMO": "off",
            "HANDOFF_DATA_DIR": str(data_dir),
        }
        argv = [
            sys.executable, "-m", "uvicorn", "handoff.main:create_app", "--factory",
            "--host", "localhost", "--port", str(port), "--no-proxy-headers",
            "--log-level", "warning",
        ]  # fmt: skip
        self.url = f"http://localhost:{port}"
        self.proc = subprocess.Popen(
            argv,
            cwd=ROOT,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._tail: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL)
        assert self.proc.stderr is not None
        self._reader = threading.Thread(target=self._drain, args=(self.proc.stderr,), daemon=True)
        self._reader.start()

    def _drain(self, stream: IO[bytes]) -> None:
        # Keep the pipe empty so a chatty server never blocks; remember the last chars only.
        for chunk in iter(lambda: stream.read1(4096), b""):  # type: ignore[attr-defined]
            self._tail.extend(chunk.decode(errors="replace"))

    def stderr_tail(self) -> str:
        return "".join(self._tail)

    def wait_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(POLL_INTERVAL_S)
        raise _Failure(f"서버가 {READY_TIMEOUT_S:.0f}초 안에 준비되지 않았습니다: {self.url}/health")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._reader.join(timeout=2)


def _expect(res: httpx.Response, code: int, what: str) -> httpx.Response:
    if res.status_code != code:
        raise _Failure(f"{what}: HTTP {res.status_code} (기대 {code}) {res.text[:300]}")
    return res


def _issue_links(base: str, admin_url: str, alias: str) -> dict[str, str]:
    links = {"admin": admin_url}
    try:
        with httpx.Client(base_url=base, follow_redirects=False, timeout=10) as client:
            _expect(client.get(admin_url), 303, "관리자 링크 수락")
            leavers = _expect(client.get("/admin/leavers"), 200, "퇴사자 목록").json()
            leaver = next((row["leaver"] for row in leavers if row.get("alias") == alias), None)
            if leaver is None:
                created = client.post("/admin/leavers", json={"alias": alias})
                leaver = _expect(created, 201, "퇴사자 등록").json()["leaver"]
            for role in ROLE_ORDER[1:]:
                res = client.post(f"/admin/leavers/{leaver}/invites", json={"role": role})
                links[role] = _expect(res, 201, f"{role} 초대 발급").json()["url"]
    except httpx.HTTPError as exc:
        raise _Failure(f"API 호출 실패: {exc}") from exc
    return links


def _title_script(label: str) -> str:
    prefix = json.dumps(f"[{label}] ")
    return (
        'document.addEventListener("DOMContentLoaded", () => '
        f"{{ document.title = {prefix} + document.title; }});"
    )


def _open_windows(
    browser: Browser, base: str, links: dict[str, str], shot_dir: Path | None
) -> list[Page]:
    pages: list[Page] = []
    for role in ROLE_ORDER:
        label = ROLE_LABEL[role]
        context = browser.new_context(base_url=base)
        context.add_init_script(_title_script(label))
        page = context.new_page()
        pages.append(page)
        page.goto(links[role])
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightError:
            pass  # a long-polling screen never goes idle; the load event is enough
        path = urlparse(page.url).path
        me_role = context.request.get("/auth/me").json()["role"]
        print(f"창 확인: [{label}] {path} 역할={me_role}", flush=True)
        if shot_dir is not None:
            shot_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot_dir / f"web-try-{role}.png"), full_page=True)
    return pages


def _wait_closed(pages: list[Page]) -> None:
    try:
        for p in pages:
            if not p.is_closed():
                p.wait_for_event("close", timeout=0)
    except PlaywrightError:
        pass  # browser disconnected: same exit path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    port: int = args.port
    signal.signal(signal.SIGTERM, _on_sigterm)

    pw: Playwright | None = None
    browser: Browser | None = None
    server: _Server | None = None
    temp_dir: Path | None = None
    code = 0
    try:
        pw = sync_playwright().start()
        if not Path(pw.chromium.executable_path).exists():
            print(NO_CHROMIUM, file=sys.stderr, flush=True)
            return 3
        if _port_in_use(port):
            print(
                f"포트 {port} 가 이미 쓰이고 있습니다. "
                f'make try TRY_ARGS="--port <다른 포트>" 로 다시 실행하세요.',
                file=sys.stderr,
                flush=True,
            )
            return 2

        if args.data_dir is None:
            temp_dir = Path(tempfile.mkdtemp(prefix="handoff-try-")).resolve()
            data_dir = temp_dir
            note = ", 종료 시 삭제"
        else:
            data_dir = args.data_dir.resolve()
            data_dir.mkdir(parents=True, exist_ok=True)
            note = ""
        base = f"http://localhost:{port}"
        secret = load_secret(data_dir)
        admin_token = issue_invite(secret, Principal(role=ADMIN_ROLE, leaver=ADMIN_SCOPE))
        admin_url = f"{base}/auth/accept?token={admin_token}"

        server = _Server(port, data_dir)
        print(
            f"서버: {base} (인증 켬, fake 공급자, 데이터 폴더 {data_dir}{note})",
            flush=True,
        )
        server.wait_ready()
        links = _issue_links(base, admin_url, args.alias)
        for role in ROLE_ORDER:
            print(f"[{ROLE_LABEL[role]}] {links[role]}", flush=True)
        print(NOTICE, flush=True)

        browser = pw.chromium.launch(headless=args.headless)
        pages = _open_windows(browser, base, links, args.screenshot_dir)
        if not args.exit_after_open:
            print(STOP_HINT, flush=True)
            _wait_closed(pages)
    except (KeyboardInterrupt, _Terminated):
        code = 0
    except _Failure as exc:
        print(str(exc), file=sys.stderr, flush=True)
        code = 1
    except PlaywrightError as exc:
        print(f"브라우저 오류: {exc}", file=sys.stderr, flush=True)
        code = 1
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if browser is not None:
            # The driver may already be gone after Ctrl+C; the server must still stop.
            with contextlib.suppress(BaseException):
                browser.close()
        if pw is not None:
            with contextlib.suppress(BaseException):
                pw.stop()
        if server is not None:
            server.stop()
            if code == 1:
                print(server.stderr_tail(), file=sys.stderr, flush=True)
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if server is not None:
            print("종료했습니다.", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
