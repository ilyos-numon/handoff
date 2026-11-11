"""Public release readiness: tracked-file scope, link hygiene, gallery, license, media sizes."""

import fnmatch
import re
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCAL_ONLY_MARKER = "# 공개 저장소 밖"
KEEP = {"tests/e2e/screenshots/.gitkeep"}


def _local_only_entries() -> list[str]:
    """Path lines of the local-only block in .gitignore (marker comment until blank line)."""
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(LOCAL_ONLY_MARKER))
    entries: list[str] = []
    for ln in lines[start + 1:]:
        if not ln.strip():
            break
        if not ln.startswith("#"):
            entries.append(ln.strip().lstrip("/"))
    return entries


LOCAL_ONLY = _local_only_entries()
# Tracked-file patterns: directories become "dir/*", files and globs stay as written.
REMOVED = [e + "*" if e.endswith("/") else e for e in LOCAL_ONLY]
# Text needles: non-glob entries, plus bare file names (e.g. a moved copy of a local-only file).
FORBIDDEN = sorted(
    {e for e in LOCAL_ONLY if "*" not in e}
    | {Path(e).name for e in LOCAL_ONLY if "*" not in e and not e.endswith("/")}
)
SCAN_SUFFIXES = (".md", ".py", ".ts", ".tsx", ".html", ".yml", ".toml")
SCAN_EXCLUDE = {".gitignore", ".dockerignore", "tests/e2e/test_public_release.py"}
GALLERY = [
    "01-demo-split.png", "02-leaver-session.png", "03-leaver-summary.png", "04-successor-answer.png",
    "05-successor-thread.png", "06-manager-status-help.png", "07-manager-ask.png",
    "08-manager-docs-runbook.png", "09-intro-roles-dark.png", "10-mobile-successor.png",
]
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ln for ln in out.splitlines() if ln]


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_no_removed_paths_tracked() -> None:
    bad = [
        f for f in _tracked()
        if f not in KEEP and any(fnmatch.fnmatchcase(f, pat) for pat in REMOVED)
    ]
    assert bad == []


def test_tracked_text_has_no_internal_references() -> None:
    hits: list[str] = []
    for rel in _tracked():
        if rel in SCAN_EXCLUDE or not (rel.endswith(SCAN_SUFFIXES) or Path(rel).name == "Makefile"):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += [f"{rel}: {needle}" for needle in FORBIDDEN if needle in text]
    assert hits == []


def test_env_untracked_and_example_tracked() -> None:
    tracked = set(_tracked())
    assert ".env" not in tracked
    assert ".env.example" in tracked


def test_screenshot_gallery() -> None:
    gallery = ROOT / "docs/media/screenshots"
    readme = _readme()
    assert "## 화면 둘러보기" in readme.splitlines()
    for name in GALLERY:
        path = gallery / name
        assert path.is_file(), name
        data = path.read_bytes()
        assert data[:8] == PNG_SIG, name
        width = struct.unpack(">I", data[16:20])[0]
        assert width <= 1280, (name, width)
        assert len(data) <= 409600, (name, len(data))
        assert f"docs/media/screenshots/{name}" in readme, name


def test_license_and_badges() -> None:
    first = (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()[0]
    assert first == "MIT License"
    readme = _readme()
    assert "Copyright (c) 2026 ilyos-numon" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    head = "\n".join(readme.splitlines()[:10])
    assert head.count("badgen.net") >= 3
    assert "](LICENSE)" in readme
    assert re.search(r"\d+\s*(passed|tests|개 테스트)", readme) is None


@pytest.mark.parametrize(
    ("rel", "limit"),
    [("docs/media/demo.gif", 5242880), ("docs/media/demo.mp4", 4194304)],
)
def test_demo_media_size(rel: str, limit: int) -> None:
    path = ROOT / rel
    assert path.is_file(), rel
    assert path.stat().st_size <= limit
