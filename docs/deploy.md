> 대안(중단: 계정 무료 실행 한도 0). 현재 공개 데모는 Render 무료([deploy-render.md](deploy-render.md)).

# 공개 데모 배포 (Hugging Face Spaces)

배포 상태: 일시 중단 (2026-10-23) — 업로드 완료(huggingface.co/spaces/fdasfdsdasfasdf/handoff, 공개, AUTH secret 설정). 실행은 "Quota exceeded for flavor cpu-basic: limit=0" 로 거부됨. 계정이 당일 생성돼 이메일 인증 전 무료 실행 한도가 0 인 것으로 추정. 재개: 계정 이메일 인증 → `HfApi().restart_space("fdasfdsdasfasdf/handoff")` → /demo 200 확인 → README 데모 링크 교체. 대안: 같은 Dockerfile 로 Render·Koyeb.

로컬에서는 `make demo` 한 줄이면 된다. 이 문서는 누구나 브라우저로 열 수 있는 공개 데모를 Hugging Face Spaces(Docker)에 올리는 방법이다.

## 1. Space 만들기
1. Hugging Face 에서 New Space → SDK 는 **Docker**, 템플릿은 Blank 를 고른다.
2. 이름은 자유. 아래에서는 `<owner>/<space>` 로 적는다.
3. 하드웨어는 무료 CPU 로 충분하다. 데모는 녹화된 답변(replay)을 재생하므로 LLM 호출이 없다.

Space 설정(카드)은 `deploy/hf/README.md` 에 있다. `sdk: docker`, `app_port: 7860`, `base_path: /demo` 로 컨테이너의 7860 포트 `/demo` 화면이 첫 화면이 된다.

## 2. Secret 넣기 (Space → Settings → Variables and secrets)
| 이름 | 필수 | 설명 |
|------|------|------|
| `HANDOFF_AUTH_SECRET` | 필수 | 데모 쿠키 서명 키. 충분히 긴 무작위 문자열(예: `python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `HANDOFF_LLM_API_KEY` | 선택 | 녹화에 없는 질문을 실제 LLM 으로 답하게 할 때만(하루 상한 `HANDOFF_DEMO_LLM_DAILY_CAP`). 없으면 녹화에 없는 질문은 내장 예시 답으로 대신한다 |

비밀값은 저장소·스크립트·로그에 쓰지 않는다. Space 의 secret 칸에만 넣는다.

## 3. 올리기
먼저 무엇이 올라가는지 확인한다(토큰 불필요).

```bash
bash scripts/deploy-hf.sh --dry-run out/hf-dry
```

- `git ls-files` 기준으로 `Dockerfile`, `.dockerignore`, `pyproject.toml`, `uv.lock`, `README.md`, `src/` 만 모은다. 커밋 전 새 파일도 `.gitignore` 대상이 아니면 포함한다.
- `README.md` 는 저장소 README 대신 Space 카드(`deploy/hf/README.md`)로 바뀐다.
- `.env`, `data/`, 테스트·문서는 포함되지 않는다. 녹화본 `src/handoff/demo/cassette.json` 은 포함된다.

확인했으면 업로드한다. 업로드는 `uvx --from huggingface_hub hf upload` 를 쓰므로 프로젝트 의존성을 늘리지 않는다.

```bash
HF_TOKEN=<쓰기 권한 토큰> bash scripts/deploy-hf.sh <owner>/<space>
```

- `HF_TOKEN` 이나 `<owner>/<space>` 가 없으면 아무것도 올리지 않고 종료코드 2 로 끝난다.
- 업로드 뒤 Space 가 이미지를 빌드한다(수 분). 빌드 로그는 Space 의 Logs 탭에서 본다.

## 4. 직접 주소로 연다
공개 링크는 Space 페이지(`https://huggingface.co/spaces/<owner>/<space>`)가 아니라 **직접 주소**를 쓴다.

```
https://<owner>-<space>.hf.space/demo
```

이유: Space 페이지는 앱을 다른 출처의 iframe 안에 띄운다. 브라우저는 이 경우 앱 쿠키를 제3자 쿠키로 보고(`SameSite=Lax`) 보내지 않으므로, 데모 연습 공간 쿠키가 붙지 않아 역할 전환과 진행이 끊긴다. 직접 주소로 열면 앱이 최상위 창이라 쿠키가 정상으로 붙는다. 데모 화면도 다른 출처 iframe 안이면 "새 창에서 열기"를 보여 준다.

주소의 `<owner>`·`<space>` 는 소문자로, `_`·`.` 은 `-` 로 바뀐다. 스크립트가 업로드 뒤 이 주소를 출력한다.

## 5. 확인
```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<owner>-<space>.hf.space/demo   # 200
```

배포가 끝나면 이 문서 맨 위 "배포 상태" 줄과 README 의 데모 링크 줄을 그 주소로 바꾼다.
