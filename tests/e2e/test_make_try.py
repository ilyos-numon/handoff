"""`make try` / scripts/try_handoff.py: four roles arrive, clean shutdown, clear failures."""

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROLES = (("관리자", "/admin", "admin"), ("퇴사자", "/", "leaver"), ("후임", "/chat", "successor"), ("팀장", "/report", "manager"))
SHOTS = ("admin", "leaver", "successor", "manager")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return int(sock.getsockname()[1])


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("HANDOFF_")}


def _assert_gone(port: int) -> None:
    with pytest.raises(httpx.TransportError):
        httpx.get(f"http://localhost:{port}/health", timeout=2)
    res = subprocess.run(["pgrep", "-f", "--", f"--port {port}"], capture_output=True, text=True, check=False)
    assert res.returncode == 1, f"leftover processes: {res.stdout}"


def test_make_try_opens_four_roles(shot_dir: Path, web_dist: Path) -> None:
    port = _free_port()
    for name in SHOTS:
        (shot_dir / f"web-try-{name}.png").unlink(missing_ok=True)
    res = subprocess.run(
        ["make", "try", f"TRY_ARGS=--port {port} --headless --exit-after-open --screenshot-dir {shot_dir}"],
        cwd=PROJECT_ROOT, env=_clean_env(), capture_output=True, text=True, timeout=120, check=False,
    )
    assert res.returncode == 0, res.stderr[-2000:]
    lines = res.stdout.splitlines()
    for label, _, _ in ROLES:
        pat = re.compile(rf"^\[{label}\] http://localhost:{port}/auth/accept\?token=\S+$")
        assert sum(1 for ln in lines if pat.match(ln)) == 1, f"link line for {label} missing:\n{res.stdout}"
    assert sum(1 for ln in lines if ln.startswith("안내:")) == 1
    for label, path, role in ROLES:
        assert f"창 확인: [{label}] {path} 역할={role}" in lines, res.stdout
    assert lines[-1] == "종료했습니다."
    _assert_gone(port)
    for name in SHOTS:
        assert (shot_dir / f"web-try-{name}.png").is_file(), name


def test_try_sigint_stops_server(web_dist: Path) -> None:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "scripts/try_handoff.py", "--port", str(port), "--headless"],
        cwd=PROJECT_ROOT, env=_clean_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    data_dir = ""
    try:
        assert proc.stdout is not None
        seen: list[str] = []
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and sum(1 for ln in seen if ln.startswith("창 확인:")) < 4:
            line = proc.stdout.readline()
            if not line:
                break
            seen.append(line.rstrip("\n"))
        assert sum(1 for ln in seen if ln.startswith("창 확인:")) == 4, "\n".join(seen)
        m = re.search(r"데이터 폴더 (\S+?)(?:,| ?\))", seen[0])
        assert m, seen[0]
        data_dir = m.group(1)
        assert Path(data_dir).exists()
        os.killpg(proc.pid, signal.SIGINT)
        rest = proc.stdout.read()
        assert proc.wait(timeout=15) == 0
        out = [ln for ln in (rest.splitlines() or seen) if ln]
        assert out[-1] == "종료했습니다."
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        for s in (proc.stdout, proc.stderr):
            if s is not None:
                s.close()
    _assert_gone(port)
    assert not Path(data_dir).exists()


def test_try_without_chromium(tmp_path: Path) -> None:
    port = _free_port()
    env = _clean_env()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(tmp_path / "empty-browsers")
    (tmp_path / "empty-browsers").mkdir()
    res = subprocess.run(
        [sys.executable, "scripts/try_handoff.py", "--port", str(port), "--headless", "--exit-after-open"],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert res.returncode == 3, res.stdout + res.stderr
    assert "uv run playwright install chromium" in res.stderr
    _assert_gone(port)


def test_try_port_in_use() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        sock.listen(1)
        port = int(sock.getsockname()[1])
        res = subprocess.run(
            [sys.executable, "scripts/try_handoff.py", "--port", str(port), "--headless", "--exit-after-open"],
            cwd=PROJECT_ROOT, env=_clean_env(), capture_output=True, text=True, timeout=60, check=False,
        )
    assert res.returncode == 2, res.stdout + res.stderr
    assert "--port" in res.stderr
    assert shutil.which("pgrep") is not None
