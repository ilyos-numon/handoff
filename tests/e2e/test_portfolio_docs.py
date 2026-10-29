"""Portfolio document bundle: README order, links, SVG, case study, video script, usability kit."""

import re
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINKED_DOCS = [
    "README.md",
    "docs/case-study.md",
    "docs/demo-video-script.md",
    "docs/usability-test-kit.md",
    "docs/deploy-cloudflare.md",
    "docs/deploy-render.md",
]
README_FIRST_60 = [
    "demo.gif", "demo.mp4", "architecture.svg", "https://handoff-4krf.onrender.com/demo", "## 핵심 기능", "## 설계 원칙", "make demo",
]
PRESERVED_HEADINGS = [
    "## 바로 해 보기", "## 실제 사용 흐름", "## 구조", "## 철학", "## 문제", "## 방향", "## 하지 않는 것",
    "## MVP", "## 실행 방법", "### 요구사항과 설치", "### 서버 실행", "### 역할과 초대 링크", "### 화면",
    "### 테스트", "### 제약",
]
NOT_DOING = [
    "- 감시·평가 용도 (팀장은 본인이 남긴 우선 질문의 답 외에는 답변 원문·세션 요약·개인별 깊이를 볼 수 없다)",
    "- 코드·시스템 자동 문서화",
]
SVG_TEXTS = [
    "방문자 브라우저",
    "Cloudflare Worker (요청 전달)",
    "Container: handoff-demo (1개, 10분 쉬면 정지)",
    "FastAPI 앱 (화면 web/dist + API)",
    "초대 링크 쿠키 · 역할 정책",
    "SQLite + Qdrant (컨테이너 디스크, 정지 시 초기화)",
    "답 공급자: 녹화 재생 / 내장 예시 (공개 데모는 외부 LLM 없음)",
]
CASE_H2 = ["## 문제", "## 원칙: 감시 도구가 되지 않게", "## 버그: 후임 재질문 루프", "## 품질 평가", "## 배운 것"]
KIT_H2 = [
    "## 목적과 원칙", "## 준비물", "## 참여자와 역할 카드", "## 진행 순서", "## 관찰 체크리스트",
    "## 사후 질문지", "## 결과 기록 양식",
]
LINK_RE = re.compile(r'\]\(([^)\s]+)(?:\s+"[^"]*")?\)|src="([^"]+)"')


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _lines(rel: str) -> list[str]:
    return _read(rel).splitlines()


def missing_links(doc: Path, root: Path) -> list[str]:
    """Relative link/image targets in ``doc`` that do not exist (paths shown relative to root)."""
    missing: list[str] = []
    for m in LINK_RE.finditer(doc.read_text(encoding="utf-8")):
        target = m.group(1) or m.group(2)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0]
        if path and not (doc.parent / path).exists():
            missing.append((doc.parent / path).relative_to(root).as_posix())
    return missing


def test_readme_first_screen() -> None:
    lines = _lines("README.md")
    head = "\n".join(lines[:60])
    for needle in README_FIRST_60:
        assert needle in head, needle
    text = _read("README.md")
    assert text.count("YOUR-SUBDOMAIN") == 0
    assert text.count("<!-- DEMO_URL") == 1


def test_render_deploy_doc() -> None:
    text = _read("docs/deploy-render.md")
    for needle in ("PORT=7860", "HANDOFF_AUTH_SECRET", "512", "15분"):
        assert needle in text, needle


def test_readme_preserved_headings_and_not_doing_block() -> None:
    lines = _lines("README.md")
    for heading in PRESERVED_HEADINGS:
        assert lines.count(heading) == 1, heading
    i = lines.index("## 하지 않는 것")
    assert lines[i + 1 : i + 3] == NOT_DOING


def test_relative_links_and_images_exist() -> None:
    for rel in LINKED_DOCS:
        assert missing_links(ROOT / rel, ROOT) == [], rel


def test_missing_link_checker_reports_broken_link(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ok.md").write_text("ok", encoding="utf-8")
    doc = tmp_path / "doc.md"
    doc.write_text("[x](docs/nope.md) [y](docs/ok.md#a) [z](https://example.com)", encoding="utf-8")
    assert missing_links(doc, tmp_path) == ["docs/nope.md"]


def test_architecture_svg() -> None:
    path = ROOT / "docs/media/architecture.svg"
    assert path.stat().st_size <= 40 * 1024
    svg = path.read_text(encoding="utf-8")
    assert re.search(r"<svg\b[^>]*viewBox=", svg)
    for banned in ("<script", "foreignObject", "<image", 'href="http'):
        assert banned not in svg, banned
    for text in SVG_TEXTS:
        assert text in svg, text


def test_case_study_headings_and_numbers_come_from_usage() -> None:
    text = _read("docs/case-study.md")
    h2 = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert h2 == CASE_H2
    usage = _read("docs/usage.md")
    numbers = set(re.findall(r"\b\d+/\d+\b", text))
    assert numbers
    for n in numbers:
        assert n in usage, n


def _sec(value: str) -> int:
    minutes, seconds = value.split(":")
    return int(minutes) * 60 + int(seconds)


def test_video_script_scene_table() -> None:
    rows = [
        [c.strip() for c in ln.strip().strip("|").split("|")]
        for ln in _lines("docs/demo-video-script.md")
        if re.match(r"\|\s*\d+\s*\|", ln)
    ]
    assert 6 <= len(rows) <= 10
    spans = []
    for row in rows:
        m = re.fullmatch(r"(\d+:\d{2})–(\d+:\d{2})", row[1])
        assert m, row[1]
        spans.append((_sec(m.group(1)), _sec(m.group(2))))
        assert len(row[3]) <= 30, row[3]
    assert spans[0][0] == 0
    assert spans[-1][1] == 90
    for (_, end), (start, _) in pairwise(spans):
        assert end == start


def test_usability_kit_structure() -> None:
    text = _read("docs/usability-test-kit.md")
    h2 = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert h2 == KIT_H2
    assert len(re.findall(r"^\s*- \[ \]", text, re.MULTILINE)) >= 10
    for ln in text.splitlines():
        if "클릭 추적" in ln:
            assert "하지 않는다" in ln, ln
    assert "클릭 추적" in text

