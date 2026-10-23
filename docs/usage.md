# 사용 문서

README 첫 화면에서 옮긴 긴 내용이다: 환경변수 전체, 데모 모드 설정, API 예시, 품질 평가 결과. 실행 방법 요약과 역할·화면 설명은 [README](../README.md) 에 있다.

## 환경변수
기본값(fake 공급자)으로 바로 실행된다. 바꾸려면 `cp .env.example .env` 후 값을 수정하고, 명령에 `--env-file .env` 를 붙여 실행한다 (앱은 `.env` 를 직접 읽지 않는다). 키는 `.env` 에만 두고 커밋하지 않는다.

| 변수 | 기본값 | 의미 |
|------|--------|------|
| `HANDOFF_PROVIDER` | `fake` (`HANDOFF_DEMO=on` 이면 `replay`) | `fake`·`qwen`·`replay`(녹화 재생)·`record`(녹화). 그 밖의 값이면 시작 시 오류. `qwen`·`record` 는 `HANDOFF_LLM_API_KEY` 필수 |
| `HANDOFF_DATA_DIR` | `./data` | 저장 디렉토리. Qdrant `<dir>/qdrant`, 세션 SQLite `<dir>/handoff.db` |
| `HANDOFF_MIN_SCORE` | fake·replay·record `0.25` / qwen `0.40` | 근거 채택 최소 코사인 점수. 설정하면 공급자와 무관하게 그 값 |
| `HANDOFF_COLLECTION` | `handoff_kb` | Qdrant 컬렉션 이름 접두어. qwen 은 `<이름>_<임베딩 모델>_<차원>` (예: `handoff_kb_text_embedding_v4_1024`) |
| `HANDOFF_LLM_BASE_URL` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | OpenAI 호환 base URL (LLM·임베딩 공용) |
| `HANDOFF_LLM_MODEL` | `qwen3.7-flash` | 채팅 모델 |
| `HANDOFF_LLM_API_KEY` | (없음) | `qwen`·`record` 일 때 필수. `replay` 에서는 있으면 녹화에 없는 요청을 하루 상한까지 실제 LLM 으로 답한다 |
| `HANDOFF_LLM_TIMEOUT` | `20` | 요청당 제한 시간(초) |
| `HANDOFF_EMBED_MODEL` | `text-embedding-v4` | 임베딩 모델 |
| `HANDOFF_EMBED_DIM` | `1024` | 임베딩 차원 |
| `HANDOFF_SESSION_SECONDS` | `900` | 세션 길이(초) |
| `HANDOFF_SESSION_GRACE_SECONDS` | `30` | 마감 후 답변을 받아 주는 유예(초) |
| `HANDOFF_SESSIONS_PLANNED` | `10` | 퇴사자당 계획 회차 |
| `HANDOFF_DEPTH_MAX_FOLLOWUPS` | `1` | 주제당 빈 슬롯 꼬리 질문 상한 (정수 ≥ 0) |
| `HANDOFF_QUEUE_PER_SESSION` | `2` | 세션마다 질문 큐에서 먼저 출제할 항목 수 (정수 ≥ 0) |
| `HANDOFF_VERIFY_CLAIMS` | `on` | 지식베이스 답의 주장 단위 검증. `on` 또는 `off` 만 허용, 그 밖의 값이면 시작 시 오류 |
| `HANDOFF_TACIT_FOLLOWUPS` | `on` | 암묵지 신호어("그냥"·"감으로"·"보통"·"경험상"·"느낌")가 든 답에 판단 기준을 묻는 꼬리 질문을 먼저 냄. 상한은 `HANDOFF_DEPTH_MAX_FOLLOWUPS` 와 공유. `on` 또는 `off` 만 허용 |
| `HANDOFF_AUTH` | `on` | 초대 링크 역할 인증. `on` 이면 로그인(초대 링크) 없이 API 를 부르면 401, 역할 밖이면 403. `off` 면 인증 없이 모든 API 가 열린다 (평가 도구·옛 API 예시용). `on` 또는 `off` 만 허용 |
| `HANDOFF_AUTH_SECRET` | (없음) | 초대 링크·로그인 쿠키 서명 키. 비우면 `<HANDOFF_DATA_DIR>/auth_secret` 파일을 읽고, 없으면 무작위로 만들어 저장한다 (권한 600) |
| `HANDOFF_INVITE_TTL_HOURS` | `72` | 초대 링크 유효 시간(시간, 정수 ≥ 1) |
| `HANDOFF_PUBLIC_URL` | (없음) | 관리 화면이 만드는 초대 링크의 앞부분. 비우면 요청이 들어온 주소를 쓴다. 공개 배포 주소가 프록시 뒤에서 달라질 때 준다 |
| `HANDOFF_DEMO` | `off` | 데모 모드. `on` 이면 `/demo` 가 열리고 방문자마다 연습 공간을 만든다. `HANDOFF_AUTH=on` 이 필요하다(아니면 시작 시 오류). `off` 면 `/demo` API 는 404 |
| `HANDOFF_DEMO_TTL_HOURS` | `24` | 연습 공간 유지 시간(시간, 정수 ≥ 1). 지난 공간은 다음 `/demo/start` 때 지운다 |
| `HANDOFF_DEMO_MAX_SANDBOXES` | `200` | 동시에 살아 있는 연습 공간 상한(정수 ≥ 1). 넘으면 `/demo/start` 가 503 |
| `HANDOFF_DEMO_LLM_DAILY_CAP` | `300` | 녹화에 없는 요청을 실제 LLM 으로 보내는 하루 상한(정수 ≥ 0). 넘거나 키가 없으면 내장 예시 답(fake)으로 대신한다. 계수는 `<HANDOFF_DATA_DIR>/demo_llm_budget.json` |
| `HANDOFF_DEMO_MAX_BODY_BYTES` | `8192` | 데모 모드 요청 본문 상한(바이트, 정수 ≥ 1024). 넘으면 413 |
| `HANDOFF_DEMO_RATE` | `120/minute` | 데모 모드 접속 주소별 요청 상한 |
| `HANDOFF_DEMO_START_RATE` | `10/hour` | 접속 주소별 `/demo/start` 상한 |
| `HANDOFF_DEMO_CASSETTE` | `src/handoff/demo/cassette.json` | 녹화 파일 경로 (`replay` 가 읽고 `record` 가 쓴다) |

qwen 채팅 요청은 `enable_thinking=false` 로 보낸다 (추론 모드를 끄고 응답 시간을 줄인다).

## 데모 모드
`make demo` 는 `uv run handoff-demo` 를 부른다 (실행기 `handoff.demo.launch`). 실행기는 환경에 값이 없을 때만 `HANDOFF_DEMO=on`, `HANDOFF_AUTH=on`, `HANDOFF_PROVIDER=replay` 를 채우고, 데이터 폴더는 `./data/demo`(`--data-dir` 또는 `HANDOFF_DATA_DIR`), 포트는 `8780`(`--port` 또는 `PORT`)로 띄운다. 서버가 준비되면 `데모 주소: http://localhost:8780/demo` 한 줄을 출력한다. 옵션은 `make demo DEMO_ARGS="--port 8790"` 처럼 넘긴다.

- `/demo` 에서 `데모 시작` 을 누르면 방문자 전용 연습 공간(퇴사자 id `demo-` + 16진 10자리)이 생기고, [시연 시나리오](demo-scenario.md) 1~10단계(자료 불러오기 → 질문 만들기 → 사전 부검 → 우선 질문 → 세션 1회)가 미리 채워진다. 팀장·퇴사자·후임 화면이 나란히 뜨고, 11단계부터 바로 해 볼 수 있다.
- 역할 전환은 연습 공간 쿠키에서만 된다 (`POST /demo/role`, 나란히 보기 창은 `X-Handoff-As` 헤더). 일반 초대 링크·관리자 쿠키에서는 헤더를 무시한다.
- `POST /demo/reset` 은 새 연습 공간으로 다시 시작, `GET /demo/status` 는 현재 역할·만료·LLM 사용량을 보여 준다.
- 데모 모드에서는 업무 자료 업로드(`POST /leavers/{leaver}/sources`)가 403 이고, 본문 크기·요청 수 상한이 걸린다 (위 표의 `HANDOFF_DEMO_*`).
- 시나리오 경로의 답은 녹화본(`src/handoff/demo/cassette.json`)을 재생하므로 LLM 비용이 들지 않는다. 임베딩은 녹화·재생 모두 키 없이 도는 내장 임베딩이다.
- 공개 주소로 띄우려면 `make demo DEMO_ARGS=--public` (모든 IPv4 인터페이스에 바인딩, 프록시 헤더 신뢰). 공개 배포 절차는 [deploy.md](deploy.md).

녹화본 다시 만들기 (`.env` 에 `HANDOFF_LLM_API_KEY` 필요) / 재생 확인 (키 불필요, 녹화에 없는 요청 수를 센다):
```bash
uv run --env-file .env python -m handoff.demo.record
uv run python -m handoff.demo.record --verify
```

## 관리 화면과 초대 링크 (명령)
첫 관리자 링크는 명령으로 만든다. 이후 퇴사자 등록과 역할별 링크 발급은 `/admin` 화면에서 한다 (`GET·POST /admin/leavers`, `POST /admin/leavers/{leaver_id}/invites`). 관리자는 링크를 만들 뿐 퇴사자 데이터는 볼 수 없다.
```bash
uv run python -m handoff.rbac.cli admin
```

## API 예시
아래 예시는 인증을 끈 서버(`HANDOFF_AUTH=off`) 기준이다. 인증을 켠 서버에서는 초대 링크로 받은 로그인 쿠키가 있어야 하고 역할 범위 밖이면 403 이다.

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

## 지식 루프 API
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
| 팀장 우선 질문 | `POST /leavers/{leaver}/priority-questions` | 팀장이 지정한 질문을 큐 맨 앞(출처 `manager`)에 넣는다. 다음 세션에서 가장 먼저 출제된다 |

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

## 지식 정직성 API
지식베이스가 "아는 것"만이 아니라 "모르는 것·불확실한 것·언젠가 틀려질 것"도 드러내게 하는 기능 6개다. 불확실·암묵지 감지는 단서어 규칙(결정적)이고, LLM 은 문장 만들기와 후보 고르기만 한다. 이유·위험·역할·징후·대응은 모두 기존 카드나 답변 문장에서만 나오며, 근거가 없으면 지어내지 않고 퇴사자 큐로 넘긴다.

| 기능 | 메서드 경로 | 무엇을, 왜 |
|------|-------------|-----------|
| 체스터턴의 울타리 | `POST /kb/fence` | "이건 왜 이렇게 돼 있나, 없애면 뭐가 깨지나"에 결정·함정 카드로 이유와 위험을 답한다. 이유를 모르는 규칙을 후임이 함부로 걷어내지 않게 하려는 것. 문장마다 주장 검증을 거치고, 남는 이유가 없으면 `found=false` 와 함께 퇴사자에게 질문을 넘긴다 |
| 사전 부검 | `POST /leavers/{leaver}/premortem/questions?role=` · `POST /leavers/{leaver}/artifacts/premortem` | "떠나고 3개월 뒤 먼저 터질 일·징후·대응" 질문 3개를 큐에 넣고, 답이 쌓이면 함정 카드 Top5 를 징후·대응과 함께 산출물로 만든다. 사고가 나기 전에 실패 시나리오를 끌어내기 위해서다 |
| 무지의 지도 | `GET /leavers/{leaver}/cards?certainty=` · `GET·POST /leavers/{leaver}/gaps[/refresh]` 의 `unknown_areas` | "아마·모르겠·전임자가" 같은 단서가 든 카드를 `uncertain` 으로 표시하고, 아무도 확실히 모르는 영역을 빈 구멍 리포트에 따로 모은다. 확실하지 않은 지식이 확실한 것처럼 전달되지 않게 한다 |
| 지식 유통기한 | `GET /leavers/{leaver}/cards/at-risk?event=&target=` | 카드마다 "어떤 변화가 생기면 틀려지는지"(시스템 교체·업체 변경·조직 변경·정책 변경·일정 변경 5종)를 붙이고, 이벤트로 위험해지는 카드를 조회한다. 지식은 시간이 지나면 낡는다 |
| 질문 라우팅 | `POST /kb/ask` 의 `route`·`certainty` | 답이 없을 때 "누구(역할)에게 물어야 하는지"를 연락처 카드·관계자 답변 안의 역할 구간에서만 고른다. 맞는 근거가 없으면 `route=null` (추측하지 않음). 사람 이름은 다루지 않는다 |
| 암묵지 신호 | `POST /depth/tacit` (세션 중에는 자동) | "그냥·감으로·보통·경험상·느낌" 같은 말이 나오면 그 판단의 기준·신호를 묻는 꼬리 질문을 만든다. 말로 설명하지 않던 판단 기준을 끌어내기 위해서다 |

울타리 질문 (`found=false` 면 `queued` 에 퇴사자에게 넘어간 큐 항목이 온다):
```bash
curl -s -X POST http://localhost:8780/kb/fence \
  -H 'content-type: application/json' \
  -d '{"leaver":"default","subject":"금요일 배포 금지"}'
```

사전 부검 질문 3개를 큐에 넣기 / 사전 부검 산출물 (카드가 없으면 409 `no cards`):
```bash
curl -s -X POST 'http://localhost:8780/leavers/default/premortem/questions?role=backend_developer'
curl -s -X POST http://localhost:8780/leavers/default/artifacts/premortem
```

불확실한 카드만 / 무지의 지도(`unknown_areas`):
```bash
curl -s 'http://localhost:8780/leavers/default/cards?certainty=uncertain'
curl -s -X POST 'http://localhost:8780/leavers/default/gaps/refresh?role=backend_developer'
```

외부 업체가 바뀌면 틀려질 수 있는 카드 (`event` 는 `system_replaced`·`vendor_changed`·`team_reorganized`·`policy_changed`·`schedule_changed`, 없거나 그 밖이면 422):
```bash
curl -s 'http://localhost:8780/leavers/default/cards/at-risk?event=vendor_changed'
```

답이 없을 때 물어볼 역할 (`answer.found` 가 false 면 `route.ask_role` 과 근거 카드·답변 id):
```bash
curl -s -X POST http://localhost:8780/kb/ask \
  -H 'content-type: application/json' \
  -d '{"question":"결제 대행사 정산 파일이 늦게 오면 누구에게 물어보나요?","leaver":"default"}'
```

암묵지 신호 꼬리 질문 (신호어가 없으면 `cues` 빈 목록, `text` null):
```bash
curl -s -X POST http://localhost:8780/depth/tacit \
  -H 'content-type: application/json' \
  -d '{"question":"배포 시점은 어떻게 정하나요?","answer":"보통 트래픽 보고 감으로 정합니다."}'
```

## 실제 공급자 점검 (live_check)
```bash
uv run --env-file .env python -m handoff.live_check
```
임베딩 차원, 꼬리 질문, 근거 기반 답, 근거가 답하지 못할 때 `NONE` 처리, 요약 3줄, 추론 모드 꺼짐을 실제 API로 한 번씩 확인한다. qwen 설정이 없으면 `LIVE SKIP` 을 출력하고 0으로 끝난다. 실측 출력(요약): `LIVE OK embed_dim=1024 ... none_guard=ok summary_lines=3 thinking=off`

지식 루프용 LLM 메서드 10개는 `handoff.live_check_loop` 로 한 번씩 확인한다 (qwen 이 아니면 `LIVE SKIP`):
```bash
HANDOFF_PROVIDER=qwen uv run --env-file .env python -m handoff.live_check_loop
```
실측 출력: `LIVE LOOP OK entities=5 questions=3 depth=ok slot_q=ok cards=4 contradictions=0 verify_guard=ok artifact=ok drill=ok review=ok latency_ms=7301`

지식 정직성용 LLM 메서드는 `handoff.live_check_honesty` 로 확인한다 (qwen 이 아니면 `LIVE SKIP`):
```bash
HANDOFF_PROVIDER=qwen uv run --env-file .env python -m handoff.live_check_honesty
```
실측 출력: `LIVE HONESTY OK fence=1 expiry=5 route=물류운영팀 리드 tacit=ok premortem=1 latency_ms=3186`

## 품질 평가
평가 도구는 인증을 지원하지 않고 옛 API `/kb/query` 를 쓴다. 평가용 서버는 반드시 `HANDOFF_AUTH=off` 로 띄운다 (`evaluation_repeat` 는 자기가 띄우는 서버에 직접 넣는다).

가상 퇴사자 고정 데이터(`eval/fixtures/`)로 10세션을 진행한 뒤 후임 질문 20개를 던지고 규칙(기대 주제 + 키워드)으로 판정한다. 평가용 서버는 비어 있는 데이터 디렉토리로 따로 띄우는 것이 안전하다 (같은 퇴사자로 10회를 넘기면 409).
```bash
HANDOFF_AUTH=off HANDOFF_DATA_DIR=./data/eval uv run --env-file .env uvicorn handoff.main:create_app --factory --port 8780
uv run python -m handoff.evaluation --base-url http://localhost:8780 --out-dir docs/eval --name mvp-qwen
```
`--require-zero-fabricated` 를 붙이면 지어낸 답이 1건이라도 있을 때 종료코드 1.

결과 (qwen3.7-flash + text-embedding-v4, min_score 0.40): 답변 있음 16/20 · 근거 세션 맞음 14/14 · 좋음 16 · 보통 2 · 틀림 2 · **지어낸 답 2** · 최장 세션 6초. 상세: [docs/eval/mvp-qwen.md](eval/mvp-qwen.md)

- 목표였던 "지어낸 답 0건"은 아직 달성하지 못했다. q16(정산 배치 수동 복구)·q17(결제 대행사 장애 연락처)은 인수인계에 없는 주제인데, 관련은 있지만 질문에 답하지 못하는 근거로 답을 만들었다. 다음 개선 대상이다.
- 보정 전 1회차(이전 값 min_score 0.25 가 남아 있던 상태)는 지어낸 답 5건이었다. 임계값 0.40 적용과 `NONE` 판정 보정 후 2건으로 줄었다.
- 위 수치는 주장 단위 검증이 없던 MVP 기준이다. 지식 루프(검증 켬) 이후의 같은 평가 결과는 아래 "지식 루프 평가"에 있다.

## 지식 루프 평가
`handoff.evaluation_loop` 가 HTTP 로 답변 깊이·지식 카드·빈 구멍·후임 질문 루프·산출물·훈련·개인정보 지표를 잰다 (고정 데이터 `eval/fixtures/loop_eval.yaml`, 규칙 판정). 같은 서버에서 기존 평가(`--require-zero-fabricated`)를 먼저 돌리고 이어서 루프 평가를 돌린다. 주의: `.env` 의 기본 공급자가 `fake` 이면 qwen 으로 재려면 `HANDOFF_PROVIDER=qwen` 을 앞에 붙인다.
```bash
HANDOFF_AUTH=off HANDOFF_PROVIDER=qwen HANDOFF_DATA_DIR=./data/eval-loop uv run --env-file .env uvicorn handoff.main:create_app --factory --port 8780
uv run python -m handoff.evaluation --base-url http://localhost:8780 --out-dir docs/eval --name llm-loop-qwen-base --require-zero-fabricated
uv run python -m handoff.evaluation_loop --base-url http://localhost:8780 --out-dir docs/eval --name llm-loop-qwen --leaver eval --require-targets
```
`--require-targets` 를 붙이면 목표 미달 지표가 하나라도 있을 때 종료코드 1. 마지막 줄은 `LOOP ...` 요약이다.

결과 (qwen3.7-flash + text-embedding-v4, 매 회차 빈 데이터 디렉토리). 보정은 qwen 프롬프트·파서만 고쳤고 평가 데이터·목표값은 바꾸지 않았다. 상세: [docs/eval/llm-loop-qwen.md](eval/llm-loop-qwen.md), [docs/eval/llm-loop-qwen-base.md](eval/llm-loop-qwen-base.md) (2회차)

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

## 평가 N회 집계
위 결과처럼 qwen 은 같은 입력에도 실행마다 답이 달라진다 (LLM 비결정성). 한 번 잰 수치는 운이 좋았거나 나빴던 한 표본일 뿐이라, `handoff.evaluation_repeat` 로 같은 평가를 N회(1~10) 돌려 평균·최소·최대·목표 충족 회차를 낸다. 회차마다 빈 임시 데이터 디렉토리로 서버를 새로 띄운다 (회차끼리 데이터가 섞이지 않게 한다). 서버는 도구가 직접 띄우므로 따로 실행하지 않는다.

정직성 평가(`handoff.evaluation_honesty`, 고정 데이터 `eval/fixtures/honesty_eval.yaml`, 규칙 판정)는 단독으로도 돌릴 수 있다 (`--base-url`, `--require-targets`, 마지막 줄 `HONESTY ...`). 아래는 기존 평가·루프 평가·정직성 평가 3종을 3회 묶어 재는 명령이다.
```bash
HANDOFF_PROVIDER=qwen uv run --env-file .env python -m handoff.evaluation_repeat --runs 3 --out-dir docs/eval --name honesty-qwen --require-mean-targets honesty,base.fabricated
```
- 출력: `docs/eval/<name>.md`(지표 표 `| 지표 | 목표 | 평균 | 최소 | 최대 | 충족 회차 |` + 회차별 표 + 오류) · `<name>.json`. 마지막 줄 `REPEAT runs=<N> ok=<오류 없는 회차> all_met=<모든 목표 충족 회차>/<ok> unmet_mean=<평균 미달 지표 또는 ->`.
- 해석: 판정은 평균 기준이다. 다만 지어낸 답은 최댓값이 0 이어야 한다 (한 회차라도 지어내면 실패). 최소·최대 폭이 크면 그 지표는 비결정성에 흔들리는 것이고, "충족 회차 2/3" 은 평균이 미달이어도 어떤 회차에서는 목표를 넘었다는 뜻이다.
- 종료코드: 모든 회차 오류 2, `--require-mean-targets` 접두어(쉼표 목록)에 걸리는 지표 중 평균 미달이 있으면 1, 그 외 0. 회차 오류는 기록하고 다음 회차를 계속한다. `--suites base,loop,honesty` 로 묶음을 고를 수 있다 (`loop` 는 `base` 와 함께).

결과 (qwen3.7-flash + text-embedding-v4, 3회, 회차당 약 10분). 종료코드 1 — 정직성 지표 3개가 평균 미달이다. 상세: [docs/eval/honesty-qwen.md](eval/honesty-qwen.md)

| 지표 | 목표 | 평균 | 최소 | 최대 | 충족 회차 |
|------|------|------|------|------|----------|
| base.fabricated (지어낸 답) | ≤ 0 | 0 | 0 | 0 | 3/3 |
| base.cited_ok | ≥ 0.857 | 0.953 | 0.929 | 1 | 3/3 |
| base.answered (참고) | - | 0.667 | 0.65 | 0.7 | - |
| base.max_session_seconds (참고) | - | 37.333 | 35 | 39 | - |
| honesty.fence_accuracy | ≥ 0.8 | **0.733 ×** | 0.6 | 0.8 | 2/3 |
| honesty.fence_fabricated | ≤ 0 | 0 | 0 | 0 | 3/3 |
| honesty.uncertain_detect | ≥ 0.8 | **0.778 ×** | 0.667 | 0.833 | 2/3 |
| honesty.tacit_followup | ≥ 0.8 | 1 | 1 | 1 | 3/3 |
| honesty.routing | ≥ 0.75 | **0.667 ×** | 0.5 | 0.75 | 2/3 |
| honesty.at_risk | ≥ 0.7 | 0.889 | 0.833 | 1 | 3/3 |
| honesty.premortem_cited | ≥ 1 | 1 | 1 | 1 | 3/3 |
| honesty.pii_leaks | ≤ 0 | 0 | 0 | 0 | 3/3 |
| honesty.surveillance_keys | ≤ 0 | 0 | 0 | 0 | 3/3 |
| loop.card_evidence (보고만) | ≥ 0.9 | 0.963 | 0.889 | 1 | 2/3 |
| loop.loop_closed (보고만) | ≥ 1 | 0.5 × | 0.5 | 0.5 | 0/3 |

그 밖의 루프 지표(depth_agree·slot_target·slot_fill·card_recall·gap_recall·artifact_cited·drill_ok·pii_leaks)는 3회 모두 목표를 충족했다. 모든 목표를 동시에 충족한 회차는 0/3 이다.

- 지어낸 답은 3회 모두 0 이다 (기존 평가 `base.fabricated`, 울타리 `fence_fabricated` 둘 다). 근거가 없는 울타리 질문에 이유를 만들어 낸 적은 없다.
- 평균 미달 3개(fence_accuracy 0.733, routing 0.667, uncertain_detect 0.778)는 진단만 하고 고치지 못했다. 원인은 이번 보정 범위 밖이다.
  - 울타리: 검증 단계(`answers_question` 판정)가 이유만 담은 문장을 "질문에 대한 답이 아님"으로 거르는 쪽으로 보수적이다. 그래서 있는 이유도 버려 `found=false` 가 된다. 검증 프롬프트는 기존 지식베이스 답과 공유해 이번에 바꾸지 않았다.
  - 라우팅: 고정 데이터가 기대하는 연락처가 질문과 주제가 다른 경우가 있고, qwen 은 그때 추측하지 않고 `route=null` 을 낸다. 실패라기보다 고정 데이터 기대값을 다시 검토해야 할 수 있다.
  - 불확실 감지: 카드 추출이 "아마·모르겠" 같은 불확실 문장을 카드에 담지 않고 빠뜨린다. 감지 규칙은 결정적이라, 카드에 문장이 없으면 잡을 수 없다. 카드 추출 프롬프트는 2차 지표를 지키려고 건드리지 않았다.
- 테스트·목표값·고정 데이터는 완화하지 않았다. 범위 안 보정 1회는 효과가 없어 되돌렸다.
- 대가: 최장 세션이 2차 28초에서 35~39초로 늘었다 (세션 종료 때 유통기한 LLM 호출 1회가 더해진 영향으로 본다). 답변 있음은 0.65~0.7 (13~14/20).
