# Handoff 웹 프론트엔드

React + Vite + TypeScript. 백엔드 연동 규칙: 모든 API 호출은 `lib/api.ts` 한 곳을 거치고, 요청마다 `/auth/me` 의 퇴사자 id 를 명시하며, 팀장 화면에는 본인이 남긴 우선 질문의 답 말고는 퇴사자 답 원문을 그리지 않는다. 데모에서는 같은 쿠키로 `X-Handoff-As` 헤더가 역할을 고른다.

## 실행

```bash
# 1) 백엔드 (다른 터미널)
cd ..
HANDOFF_AUTH=off uv run uvicorn handoff.main:create_app --factory --port 8780   # 쿠키 없이 개발
# 또는 make demo                                                                 # 데모 모드(인증 켬)

# 2) 프론트
cd web
npm install
npm run dev          # http://localhost:5173
```

개발 서버가 API 경로를 백엔드로 넘긴다(`vite.config.ts`). 백엔드 주소는 `HANDOFF_BACKEND`(기본 `http://localhost:8780`).

- 인증 끔: `http://localhost:5173/?leaver=kim`, `/chat?leaver=kim`, `/report?leaver=kim`
- 인증 켬: 초대 링크를 개발 서버 주소로 만든다. 그래야 쿠키가 5173 출처에 붙는다.
  ```bash
  HANDOFF_AUTH=on uv run python -m handoff.rbac.cli admin --base-url http://localhost:5173
  ```
- 데모: `make demo` 뒤 `http://localhost:5173/demo`

## 화면

| 경로 | 화면 | 역할 |
|---|---|---|
| `/intro` | 소개 페이지 (GSAP) | 누구나. 인증 켠 서버에서 로그인 전에 `/` 로 오면 여기로 보낸다 |
| `/` | 오늘의 15분 | 퇴사자 |
| `/chat` | 기록에 묻기 (질문하기·이건 왜 이렇게?·넘긴 질문·문서·카드·훈련) | 후임 |
| `/report` | 인수인계 현황 (진행·빈 구멍·큐·우선 질문·자료·산출물·변화 영향) | 팀장 |
| `/admin` | 퇴사자 등록·초대 링크 | 관리자 |
| `/demo` | 세 역할 나란히 보기 | 데모 서버 방문자 |

## 배포 (같은 출처)

```bash
npm run build            # dist/
```
`deploy/nginx.conf` 처럼 리버스 프록시 하나 뒤에 `dist/` 와 FastAPI 를 같이 둔다. 초대 링크 앞부분이 공개 주소가 되도록 백엔드에 `HANDOFF_PUBLIC_URL=https://<공개 주소>` 를 준다.

## 구조

```
src/
  lib/api.ts        fetch 래퍼(오류 형식 4종, 데모 X-Handoff-As, 팀장용 at-risk 원문 제거) + 엔드포인트
  lib/types.ts      API 데이터 모델 타입
  lib/labels.ts     열거형 → 화면 문구
  lib/app.tsx       /auth/me + /demo/status 한 번 읽어 공유
  components/RoleGate.tsx   시작 순서(역할 확인·leaver 결정)
  pages/            화면별
  styles/           tokens.css(색), global.css, intro.css
```

## 백엔드 이상에 대한 프론트 우회

프론트를 만들며 실제 호출로 찾은 백엔드 동작 중 화면이 따로 대비해야 하는 것과 그 우회 위치다. 번호는 작업 당시 이상 목록의 번호이고, "서버 해결됨" 표시가 있는 것은 서버를 고친 뒤에도 프론트 방어를 남겨 둔 항목이다.

| # | 우회 위치 |
|---|---|
| 1 팀장 at-risk 원문 | `Api.atRiskForManager` 가 `evidence·body·uncertain_cues` 를 버린다 (서버 해결됨, 유지) |
| 2 CORS 없음 | `vite.config.ts` 프록시, `deploy/nginx.conf` |
| 3 leaver 생략 시 403 | 모든 API 함수가 leaver 를 필수 인자로 받는다 |
| 4 429 `error` 키 | `request()` 가 `detail ?? error` 로 읽는다 |
| 5 시작만 해도 회차 소진 | 시작 버튼 잠금, 열린 세션은 이어하기 |
| 6 마감 뒤에도 open | 타이머 0·복구 시 바로 `end` |
| 7 요약의 이메일 원문 | `maskContacts()` 로 표시할 때 가림 (서버 해결됨, 유지) |
| 8 카드 id 변경 | 근거 칩이 없는 카드를 "갱신됨" 으로 표시 |
| 9 언급 수 누적 | 질문 만들기 버튼 잠금, mentions 숫자 숨김 |
| 10 대화 기록 API 없음 | `localStorage` 에 세션별 저장, 끝나면 삭제 |
| 14·15 gaps queued/id | 새로 넣은 수는 refresh 응답에서만, 키는 kind+topic |
| 16 모의 자료 중복 | 자료가 없을 때만 버튼 노출 |
| 17 사전 부검 200 | "N개가 큐에 있습니다" 상태 문구 |
| 26 IPv6 localhost | 다른 리스너와 겹치면 `HANDOFF_BACKEND` 로 백엔드 주소를 준다 |
