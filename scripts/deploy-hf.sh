#!/usr/bin/env bash
# Hugging Face Spaces(Docker) 배포 스크립트.
#   scripts/deploy-hf.sh --dry-run <dir>   업로드할 파일만 <dir> 에 모은다 (토큰 불필요)
#   HF_TOKEN=... scripts/deploy-hf.sh <owner>/<space>   모은 파일을 Space 로 올린다
# 종료코드: 0 성공, 1 실행 오류, 2 사용법 오류·HF_TOKEN 없음
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CARD="$ROOT/deploy/hf/README.md"
MARKER=".deploy-hf-staging"
# Dockerfile 이 쓰는 것만 올린다. .env·data·로컬 작업 파일은 대상이 아니다.
INCLUDE=(Dockerfile .dockerignore pyproject.toml uv.lock README.md src)

usage() {
  echo "usage: $0 --dry-run <dir> | $0 <owner>/<space>  (업로드는 HF_TOKEN 필요)" >&2
  exit 2
}

stage() {
  local dest="$1"
  mkdir -p "$dest"
  if [ -n "$(ls -A "$dest")" ]; then
    if [ -f "$dest/$MARKER" ]; then
      find "$dest" -mindepth 1 -delete
    else
      echo "error: $dest 가 비어 있지 않다 (이 스크립트가 만든 폴더만 덮어쓴다)" >&2
      exit 1
    fi
  fi
  # git 이 추적하거나 아직 커밋 전이지만 무시 대상이 아닌 파일만 복사한다.
  (cd "$ROOT" && git ls-files -z --cached --others --exclude-standard -- "${INCLUDE[@]}") |
    while IFS= read -r -d '' f; do
      [ -f "$ROOT/$f" ] || continue
      mkdir -p "$dest/$(dirname "$f")"
      cp -p "$ROOT/$f" "$dest/$f"
    done
  for need in Dockerfile pyproject.toml uv.lock src/handoff/demo/cassette.json; do
    if [ ! -f "$dest/$need" ]; then
      echo "error: $need 가 없다" >&2
      exit 1
    fi
  done
  # 저장소 README 대신 Space 카드를 README.md 로 쓴다.
  cp "$CARD" "$dest/README.md"
}

[ $# -ge 1 ] || usage

if [ "$1" = "--dry-run" ]; then
  [ $# -eq 2 ] || usage
  stage "$2"
  touch "$2/$MARKER"
  (cd "$2" && find . -type f ! -name "$MARKER" | sed 's|^\./||' | sort)
  echo "dry-run: $2 (업로드하지 않음)" >&2
  exit 0
fi

[ $# -eq 1 ] || usage
SPACE_ID="$1"
case "$SPACE_ID" in
  -*|*/*/*|/*|*/) usage ;;
  */*) ;;
  *) usage ;;
esac
if [ -z "${HF_TOKEN:-}" ]; then
  echo "error: HF_TOKEN 이 없다. --dry-run 으로 파일 목록만 확인할 수 있다" >&2
  exit 2
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
stage "$TMP/space"
uvx --from huggingface_hub hf upload "$SPACE_ID" "$TMP/space" . --repo-type space
host="$(printf '%s-%s' "${SPACE_ID%%/*}" "${SPACE_ID#*/}" | tr '[:upper:]_.' '[:lower:]--')"
echo "업로드 완료. 빌드 후 주소: https://${host}.hf.space/demo"
