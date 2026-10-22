# 핸드오프 (Handoff)

## 철학
인수인계 문서는 쓰는 사람이 뭘 아는지 모른 채 쓰인다. 질문하는 쪽이 모르는 걸 끌어내야 지식이 남는다.

## 문제
- 퇴사자는 마지막 2주를 "문서 한 장" 쓰는 데 쓰고, 그 문서는 아무도 안 읽는다
- 정작 중요한 건 "왜 그렇게 했는지", "어디가 함정인지", "누구한테 물어야 하는지"인데 문서에 없다
- 후임은 입사 후 같은 실수를 반복한다

## 방향
- 퇴사 확정일부터 매일 15분, AI가 질문하고 퇴사자가 말로 답한다
- 질문은 담당 시스템·반복 업무·사고 이력·관계자 네 축으로 깊어진다
- 답변은 자동으로 구조화돼 검색 가능한 지식베이스가 된다
- 후임은 문서를 읽는 대신 그 지식베이스에 질문한다
- 빈 구멍(답 없는 영역)을 관리자에게 알려 퇴사 전에 채운다

## 하지 않는 것
- 감시·평가 용도
- 코드·시스템 자동 문서화

## 기술 방향
- 질문 생성: 업무 설명서·조직도를 입력받아 LLM이 질문 트리 생성
- 음성 답변 STT → 요약·태깅 → 벡터 DB
- 후임용 RAG 챗

## MVP
- 질문 트리 1개 직군(백엔드 개발자)
- 텍스트 입력으로 시작, 음성은 다음
- 10세션 후 지식베이스에 질문 20개 던져 답변 품질 확인

## 실행 방법

### 요구사항
- [uv](https://docs.astral.sh/uv/) 만 설치하면 된다. Python 3.12는 uv가 설치한다.

### 설치
```bash
uv sync
```

### 환경변수 (선택)
기본값으로 바로 실행된다. 바꾸려면 `cp .env.example .env` 후 값을 수정하고, 서버를 `uv run --env-file .env uvicorn ...` 처럼 `--env-file .env` 를 붙여 실행한다 (앱은 `.env` 를 직접 읽지 않는다).

| 변수 | 기본값 | 의미 |
|------|--------|------|
| `HANDOFF_PROVIDER` | `fake` | 공급자. 현재는 `fake`만 지원 |
| `HANDOFF_DATA_DIR` | `./data` | Qdrant 로컬 저장 디렉토리 (`<dir>/qdrant`) |
| `HANDOFF_MIN_SCORE` | `0.25` | 근거 채택 최소 코사인 점수 |
| `HANDOFF_COLLECTION` | `handoff_kb` | Qdrant 컬렉션 이름 |

### 서버 실행
```bash
uv run uvicorn handoff.main:create_app --factory --port 8765
```
준비 확인: `curl -sf http://localhost:8765/health` → `{"status":"ok"}`

### API 예시
세션 시작 (응답의 `session_id`를 다음 호출에 사용):
```bash
curl -s -X POST http://localhost:8765/sessions \
  -H 'content-type: application/json' \
  -d '{"role":"backend_developer"}'
```

답변 (`<session_id>`에 시작 응답의 값을 넣는다. 응답의 `next_question`이 꼬리 질문 또는 다음 질문):
```bash
curl -s -X POST http://localhost:8765/sessions/<session_id>/answers \
  -H 'content-type: application/json' \
  -d '{"text":"금요일 배포는 피합니다."}'
```

지식베이스 질의:
```bash
curl -s -X POST http://localhost:8765/kb/query \
  -H 'content-type: application/json' \
  -d '{"question":"금요일에 배포하면 왜 안 되나요?"}'
```

참고: 답변한 내용은 `POST /sessions/<session_id>/end`를 호출해야 지식베이스에 저장된다. 종료 전에는 질의해도 해당 내용이 검색되지 않는다.

### 테스트
단위 테스트:
```bash
uv run pytest tests/unit -q
```

E2E 테스트 (서버를 테스트가 직접 띄운다):
```bash
uv run pytest tests/e2e -q
```

### 제약
현재는 fake 공급자 모드(`HANDOFF_PROVIDER=fake`)만 지원하며 API 키가 필요 없다.
