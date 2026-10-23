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
- 실제 공급자(qwen)로 돌릴 때만 OpenAI 호환 Qwen API 키가 필요하다. fake 모드는 키 없이 돈다.

### 설치
```bash
uv sync
```

### 환경변수
기본값(fake 공급자)으로 바로 실행된다. 바꾸려면 `cp .env.example .env` 후 값을 수정하고, 명령에 `--env-file .env` 를 붙여 실행한다 (앱은 `.env` 를 직접 읽지 않는다). 키는 `.env` 에만 두고 커밋하지 않는다.

| 변수 | 기본값 | 의미 |
|------|--------|------|
| `HANDOFF_PROVIDER` | `fake` | `fake` 또는 `qwen`. 그 밖의 값이면 시작 시 오류 |
| `HANDOFF_DATA_DIR` | `./data` | 저장 디렉토리. Qdrant `<dir>/qdrant`, 세션 SQLite `<dir>/handoff.db` |
| `HANDOFF_MIN_SCORE` | fake `0.25` / qwen `0.40` | 근거 채택 최소 코사인 점수. 설정하면 공급자와 무관하게 그 값 |
| `HANDOFF_COLLECTION` | `handoff_kb` | Qdrant 컬렉션 이름 접두어. qwen 은 `<이름>_<임베딩 모델>_<차원>` (예: `handoff_kb_text_embedding_v4_1024`) |
| `HANDOFF_LLM_BASE_URL` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | OpenAI 호환 base URL (LLM·임베딩 공용) |
| `HANDOFF_LLM_MODEL` | `qwen3.7-flash` | 채팅 모델 |
| `HANDOFF_LLM_API_KEY` | (없음) | qwen 일 때 필수 |
| `HANDOFF_LLM_TIMEOUT` | `20` | 요청당 제한 시간(초) |
| `HANDOFF_EMBED_MODEL` | `text-embedding-v4` | 임베딩 모델 |
| `HANDOFF_EMBED_DIM` | `1024` | 임베딩 차원 |
| `HANDOFF_SESSION_SECONDS` | `900` | 세션 길이(초) |
| `HANDOFF_SESSION_GRACE_SECONDS` | `30` | 마감 후 답변을 받아 주는 유예(초) |
| `HANDOFF_SESSIONS_PLANNED` | `10` | 퇴사자당 계획 회차 |
| `HANDOFF_DEPTH_MAX_FOLLOWUPS` | `1` | 주제당 빈 슬롯 꼬리 질문 상한 (정수 ≥ 0) |
| `HANDOFF_QUEUE_PER_SESSION` | `2` | 세션마다 질문 큐에서 먼저 출제할 항목 수 (정수 ≥ 0) |
| `HANDOFF_VERIFY_CLAIMS` | `on` | 지식베이스 답의 주장 단위 검증. `on` 또는 `off` 만 허용, 그 밖의 값이면 시작 시 오류 |

qwen 채팅 요청은 `enable_thinking=false` 로 보낸다 (추론 모드를 끄고 응답 시간을 줄인다).

### 서버 실행
fake 공급자 (키 불필요):
```bash
uv run uvicorn handoff.main:create_app --factory --port 8780
```

qwen 공급자 (`.env` 에 `HANDOFF_PROVIDER=qwen`, `HANDOFF_LLM_API_KEY` 설정):
```bash
uv run --env-file .env uvicorn handoff.main:create_app --factory --port 8780
```
준비 확인: `curl -sf http://localhost:8780/health` → `{"status":"ok"}`

### 화면
| 주소 | 사용자 | 내용 |
|------|--------|------|
| `http://localhost:8780/?leaver=<id>` | 퇴사자 | 세션 시작, 질문에 답하기, 남은 시간, 종료 시 오늘 요약 3줄 (`leaver` 생략 시 `default`) |
| `http://localhost:8780/chat` | 후임 | 지식베이스에 질문하고 근거(세션 회차·축·발췌)와 함께 답 받기 |
| `http://localhost:8780/report?leaver=<id>` | 팀장 | 네 축별 커버리지 막대, 가장 비어 있는 축 표시, 빈 주제 목록 |

세션 규칙: 한 세션 15분(`HANDOFF_SESSION_SECONDS`), 마감 판정은 서버가 하고 유예 30초 뒤 답변을 거부한다. 퇴사자당 10회를 넘는 시작은 409로 거부하고, 새 세션을 시작하면 열려 있던 이전 세션은 자동 종료된다. 세션 상태는 SQLite 에 저장돼 서버를 재시작해도 회차·진행 위치·요약이 남는다.

### API 예시
세션 시작 (응답의 `session_id`를 다음 호출에 사용):
```bash
curl -s -X POST http://localhost:8780/sessions \
  -H 'content-type: application/json' \
  -d '{"role":"backend_developer","leaver":"default"}'
```

답변 (`<session_id>`에 시작 응답의 값을 넣는다. 응답의 `next_question`이 꼬리 질문 또는 다음 질문):
```bash
curl -s -X POST http://localhost:8780/sessions/<session_id>/answers \
  -H 'content-type: application/json' \
  -d '{"text":"금요일 배포는 피합니다."}'
```

세션 종료 (요약 3줄 반환, 이때 답변이 지식베이스에 저장된다):
```bash
curl -s -X POST http://localhost:8780/sessions/<session_id>/end
```

지식베이스 질의:
```bash
curl -s -X POST http://localhost:8780/kb/query \
  -H 'content-type: application/json' \
  -d '{"question":"금요일에 배포하면 왜 안 되나요?"}'
```

커버리지: `curl -s 'http://localhost:8780/coverage?leaver=default'`

참고: 답변한 내용은 `POST /sessions/<session_id>/end`를 호출해야 지식베이스에 저장된다. 종료 전에는 질의해도 해당 내용이 검색되지 않는다.

### 지식 루프 API
세션 답변을 업무 자료·지식 카드·빈 구멍·후임 질문과 이어 붙이는 백엔드 API다 (모두 JSON). `{leaver}` 는 `^[a-z0-9_-]{1,40}$` (위반 시 422), 공급자 오류는 503 `provider unavailable`. 응답에 사람 단위 점수·순위·등급 필드는 없다.

| 기능 | 메서드 경로 | 내용 |
|------|-------------|------|
| 업무 자료 | `POST /leavers/{leaver}/sources` · `POST .../sources/mock` · `GET .../sources` · `GET .../entities` | 자료 업로드(역할 맵 치환·이메일·전화번호 자리표시 후 저장), 모의 자료 8건 적재, 엔티티 추출 결과 |
| 맞춤 질문 | `POST /leavers/{leaver}/questions/generate` | 자료·엔티티로 질문을 만들어 큐에 넣음. 자료가 없으면 409 `no sources` |
| 답변 깊이 | `POST /depth/judge` · `POST /depth/follow-up` · `GET /sessions/{session_id}/depth` | 왜·언제·누가·어떻게·대안 5슬롯 판정, 첫 빈 슬롯을 겨냥한 꼬리 질문, 세션별 슬롯 채움 |
| 지식 카드 | `GET /leavers/{leaver}/cards?type=&min_confidence=` · `POST /sessions/{session_id}/cards/extract` | 타입별 카드(절차·함정·결정·연락처·엔티티)와 근거 답변 id. 열린 세션 추출은 409 `session open` |
| 빈 구멍·큐 | `POST /leavers/{leaver}/gaps/refresh?role=` · `GET .../gaps` · `GET .../queue?status=&source=` | 자료에는 있는데 답변에 없는 주제, 출제 대기 질문 큐 (후임 질문 > 빈 구멍 > 생성 질문 순) |
| 후임 질문 루프 | `POST /kb/ask` · `GET /leavers/{leaver}/successor-questions` | 주장 단위 검증을 거친 답. 답이 없으면 그 질문을 퇴사자 큐에 넣고, 다음 세션에서 먼저 묻는다 |
| 산출물 | `POST /leavers/{leaver}/artifacts/{kind}` · `GET ...` | `runbook`·`onboarding_30d`·`pitfalls_top10`·`adr`. 절마다 근거 카드 id. 카드가 없으면 409 `no cards` |
| 장애 훈련 | `POST /leavers/{leaver}/drills` · `GET /drills/{drill_id}` · `POST /drills/{drill_id}/answers` | 카드로 만든 장애 시나리오, 답에서 다룬 요점·빠진 요점 피드백 (점수 없음, 답 원문 저장 안 함) |

모의 업무 자료 적재 (가상 결제 서비스 문서 8건):
```bash
curl -s -X POST http://localhost:8780/leavers/default/sources/mock
```

자료 기반 맞춤 질문 생성 (생성된 질문은 큐에 들어가 다음 세션에 먼저 출제된다):
```bash
curl -s -X POST http://localhost:8780/leavers/default/questions/generate \
  -H 'content-type: application/json' \
  -d '{"role":"backend_developer","limit":8}'
```

후임 질문 (`answer.found` 가 false 면 `queued` 에 퇴사자에게 넘어간 큐 항목이 온다):
```bash
curl -s -X POST http://localhost:8780/kb/ask \
  -H 'content-type: application/json' \
  -d '{"question":"정산 배치가 실패하면 손으로 어떻게 복구하나요?","leaver":"default"}'
```

운영 런북 생성 (세션을 마치고 카드가 쌓인 뒤):
```bash
curl -s -X POST http://localhost:8780/leavers/default/artifacts/runbook
```

### 실제 공급자 점검 (live_check)
```bash
uv run --env-file .env python -m handoff.live_check
```
임베딩 차원, 꼬리 질문, 근거 기반 답, 근거가 답하지 못할 때 `NONE` 처리, 요약 3줄, 추론 모드 꺼짐을 실제 API로 한 번씩 확인한다. qwen 설정이 없으면 `LIVE SKIP` 을 출력하고 0으로 끝난다. 실측 출력(요약): `LIVE OK embed_dim=1024 ... none_guard=ok summary_lines=3 thinking=off`

지식 루프용 LLM 메서드 10개는 `handoff.live_check_loop` 로 한 번씩 확인한다 (qwen 이 아니면 `LIVE SKIP`):
```bash
HANDOFF_PROVIDER=qwen uv run --env-file .env python -m handoff.live_check_loop
```
실측 출력: `LIVE LOOP OK entities=5 questions=3 depth=ok slot_q=ok cards=4 contradictions=0 verify_guard=ok artifact=ok drill=ok review=ok latency_ms=7301`

### 품질 평가
가상 퇴사자 고정 데이터(`eval/fixtures/`)로 10세션을 진행한 뒤 후임 질문 20개를 던지고 규칙(기대 주제 + 키워드)으로 판정한다. 평가용 서버는 비어 있는 데이터 디렉토리로 따로 띄우는 것이 안전하다 (같은 퇴사자로 10회를 넘기면 409).
```bash
HANDOFF_DATA_DIR=./data/eval uv run --env-file .env uvicorn handoff.main:create_app --factory --port 8780
uv run python -m handoff.evaluation --base-url http://localhost:8780 --out-dir docs/eval --name mvp-qwen
```
`--require-zero-fabricated` 를 붙이면 지어낸 답이 1건이라도 있을 때 종료코드 1.

결과 (qwen3.7-flash + text-embedding-v4, min_score 0.40): 답변 있음 16/20 · 근거 세션 맞음 14/14 · 좋음 16 · 보통 2 · 틀림 2 · **지어낸 답 2** · 최장 세션 6초. 상세: [docs/eval/mvp-qwen.md](docs/eval/mvp-qwen.md)

- 목표였던 "지어낸 답 0건"은 아직 달성하지 못했다. q16(정산 배치 수동 복구)·q17(결제 대행사 장애 연락처)은 인수인계에 없는 주제인데, 관련은 있지만 질문에 답하지 못하는 근거로 답을 만들었다. 다음 개선 대상이다.
- 보정 전 1회차(이전 값 min_score 0.25 가 남아 있던 상태)는 지어낸 답 5건이었다. 임계값 0.40 적용과 `NONE` 판정 보정 후 2건으로 줄었다.
- 위 수치는 주장 단위 검증이 없던 MVP 기준이다. 지식 루프(검증 켬) 이후의 같은 평가 결과는 아래 "지식 루프 평가"에 있다.

### 지식 루프 평가
`handoff.evaluation_loop` 가 HTTP 로 답변 깊이·지식 카드·빈 구멍·후임 질문 루프·산출물·훈련·개인정보 지표를 잰다 (고정 데이터 `eval/fixtures/loop_eval.yaml`, 규칙 판정). 같은 서버에서 기존 평가(`--require-zero-fabricated`)를 먼저 돌리고 이어서 루프 평가를 돌린다. 주의: `.env` 의 기본 공급자가 `fake` 이면 qwen 으로 재려면 `HANDOFF_PROVIDER=qwen` 을 앞에 붙인다.
```bash
HANDOFF_PROVIDER=qwen HANDOFF_DATA_DIR=./data/eval-loop uv run --env-file .env uvicorn handoff.main:create_app --factory --port 8780
uv run python -m handoff.evaluation --base-url http://localhost:8780 --out-dir docs/eval --name llm-loop-qwen-base --require-zero-fabricated
uv run python -m handoff.evaluation_loop --base-url http://localhost:8780 --out-dir docs/eval --name llm-loop-qwen --leaver eval --require-targets
```
`--require-targets` 를 붙이면 목표 미달 지표가 하나라도 있을 때 종료코드 1. 마지막 줄은 `LOOP ...` 요약이다.

결과 (qwen3.7-flash + text-embedding-v4, 매 회차 빈 데이터 디렉토리). 보정은 qwen 프롬프트·파서만 고쳤고 평가 데이터·목표값은 바꾸지 않았다. 상세: [docs/eval/llm-loop-qwen.md](docs/eval/llm-loop-qwen.md), [docs/eval/llm-loop-qwen-base.md](docs/eval/llm-loop-qwen-base.md) (2회차)

| 지표 | 목표 | 보정 전 | 1회차 보정 | 2회차 보정 (최종) |
|------|------|--------|-----------|------------------|
| 지어낸 답 (fabricated) | 0 | 2 (q16·q17) | **0** | **0** |
| 근거 세션 맞음 (cited_ok) | ≥ 12/14 | 13/14 | 13/14 | 12/14 |
| 답변 있음 (참고) | - | 15/20 | 13/20 | 12/20 |
| depth_agree | ≥ 0.8 | 0.640 × | 1.000 | 1.000 |
| slot_target | ≥ 0.8 | 0.200 × | 1.000 | 1.000 |
| slot_fill | ≥ 0.7 | 1.000 | 1.000 | 1.000 |
| card_recall | ≥ 0.7 | 0.727 | 0.818 | 0.727 |
| card_evidence | ≥ 0.9 | 0.750 × | 0.889 × | 1.000 |
| gap_recall | ≥ 0.6 | 1.000 | 1.000 | 1.000 |
| loop_closed | 전부 | 1/2 × | 2/2 | 1/2 × |
| artifact_cited | = 1.0 | 1.000 | 1.000 | 1.000 |
| drill_ok | 전부 | 2/2 | 2/2 | 2/2 |
| pii_leaks | 0 | 0 | 0 | 0 |

- 지어낸 답 0 은 1회차·2회차 모두 달성했다 (주장 단위 검증 + 질문 상황 일치 판정).
- 두 회차에서 각각 다른 지표 1개가 미달했다: 1회차는 card_evidence 0.889, 2회차는 loop_closed 1/2 (대행사 장애 연락 질문이 닫히지 않음). 2회차 보정은 카드 쪽만 고쳤는데 1회차에 통과했던 loop_closed 가 떨어졌다. qwen 응답이 실행마다 달라지는 탓으로 보며, 전 지표 동시 충족은 아직 못 했다. 보정 상한(2회)에 도달해 여기서 멈췄다.
- 대가: 검증이 보수적이라 답변 있음이 MVP 16/20 에서 12/20 으로 줄었고(q09·q13 놓침), 최장 세션이 6초에서 28초로 늘었다 (답마다 슬롯 판정·꼬리 질문 LLM 호출이 더해짐).

### 테스트
단위 테스트:
```bash
uv run pytest tests/unit -q
```

E2E 테스트 (서버를 테스트가 직접 띄운다, fake 공급자):
```bash
uv run pytest tests/e2e -q
```

### 제약
- 기본 테스트·E2E 는 fake 공급자로 돌아 API 키가 필요 없다. 실제 답변 품질은 qwen 공급자에서만 의미가 있다.
- qwen 공급자는 답변 원문을 외부 API(LLM·임베딩)로 보낸다. 기기 안 처리가 아니다.
- 질문 트리는 백엔드 개발자 직군 1개, 입력은 텍스트만 (음성은 다음 단계).
- 커버리지 리포트는 `/report` 화면과 `GET /coverage` JSON 1장이다. 메일 발송과 "이 주제 우선 질문하기"는 MVP 에서 뺐다.
- 품질 평가는 규칙 기반이며 LLM 심판을 쓰지 않는다. 평가 데이터는 가상 고정 데이터다.
- 인수인계에 없는 주제의 지어낸 답은 주장 단위 검증으로 qwen 실측 두 회차 모두 0/6 이었다 (검증 전 MVP 는 2/6). 다만 규칙 평가 20문항 기준이고, 검증이 보수적이라 있는 주제도 답하지 않는 경우가 늘었다 (답변 있음 12/20). `HANDOFF_VERIFY_CLAIMS=off` 면 지식베이스 답을 검증 없이 MVP 때처럼 만든다.
