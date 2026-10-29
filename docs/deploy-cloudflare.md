# 공개 데모 배포 (Cloudflare Containers)

> 대안(유료: Workers Paid 필요). 현재 공개 데모는 Render 무료([deploy-render.md](deploy-render.md)).

로컬에서는 `make demo` 한 줄이면 된다. 이 문서는 누구나 브라우저로 열 수 있는 공개 데모를 Cloudflare Containers 에 올리는 방법이다. 배포 구성은 `deploy/cloudflare/`(Worker·설정)와 `.github/workflows/deploy-cloudflare.yml`(수동 실행 워크플로)에 있다. Hugging Face Spaces 로 올리는 방법은 대안으로 [deploy.md](deploy.md) 에 남겨 둔다.

## 왜 Containers 인가

- 앱은 Python(FastAPI) + 로컬 Qdrant 라서 Workers 만으로는 돌릴 수 없다. Containers 는 저장소 루트의 `Dockerfile` 을 수정 없이 그대로 빌드해 실행한다.
- 요청은 Worker(`deploy/cloudflare/src/index.ts`)가 받아 컨테이너 하나(`getContainer(env.HANDOFF, "demo")`)로 그대로 넘긴다. 경로를 바꾸거나 리다이렉트하지 않는다.
- 인스턴스는 1개(`max_instances: 1`)다. 방문자별 연습 공간은 한 프로세스의 데이터 폴더 안에서 나뉘므로, 인스턴스를 나누면 같은 방문자의 요청이 다른 디스크로 가서 연습 공간이 끊긴다.
- 컨테이너는 10분 동안 요청이 없으면 정지한다(`sleepAfter = "10m"`). 정지하면 컨테이너 디스크가 초기화되어 연습 공간도 사라진다. 다음 방문자는 깨끗한 시드로 다시 시작한다.
- 컨테이너 포트는 `Dockerfile` CMD 의 `--port 7860` 과 같은 `defaultPort = 7860` 이다.
- Worker 는 `X-Forwarded-For` 를 `CF-Connecting-IP` 하나로 덮어쓰고(없으면 지움) `X-Forwarded-Proto: https` 를 넣는다. 앱은 `X-Forwarded-For` 맨 오른쪽 값을 요청 상한 키로 쓰므로, 방문자가 위조한 값이 남지 않는다.

## 비용

아래 값은 2026-10-29 에 공식 가격 페이지 https://developers.cloudflare.com/containers/pricing/ (페이지 표기 "Last updated Oct 5, 2026")에서 확인한 것이다. 배포 전에 다시 확인한다.

| 항목 | Free | Workers Paid (월 5달러) |
|------|------|------------------------|
| 메모리 | N/A | 월 25 GiB-hours 포함, 초과 GiB-second 당 $0.0000025 |
| CPU | N/A | 월 375 vCPU-minutes 포함, 초과 vCPU-second 당 $0.000020 |
| 디스크 | N/A | 월 200 GB-hours 포함, 초과 GB-second 당 $0.00000007 |

- Containers 는 Free 플랜에서 쓸 수 없다(N/A). Workers Paid 가 필요하다.
- 실행 중인 동안 10ms 단위로 과금된다. 메모리·디스크는 고른 인스턴스 크기 기준, CPU 는 실제 사용량 기준이다. 컨테이너가 잠들면 과금이 멈춘다.
- 인스턴스 크기(같은 페이지): `lite` 1/16 vCPU·256 MiB·2 GB, `basic` 1/4 vCPU·1 GiB·4 GB, `standard-1` 1/2 vCPU·4 GiB·8 GB. 이 데모는 `basic` 이다.
- 계산 예(`basic`, 위 값으로만): 메모리 1 GiB × 25시간 = 25 GiB-hours 라서 포함량 안에서 컨테이너를 월 25시간 켜 둘 수 있다. 디스크는 4 GB × 50시간 = 200 GB-hours 라서 50시간까지 포함된다. 따라서 메모리가 먼저 찬다. 그 뒤로는 메모리만 1시간에 1 GiB × 3600초 × $0.0000025 = $0.009 가 더 든다(디스크·CPU 초과분 별도).
- 같은 페이지에 따르면 Worker 와 Durable Object 사용량, 네트워크 송신(한국 지역 GB 당 $0.05, 월 500 GB 포함), 로그도 따로 과금된다.

## 준비(사용자 몫)

1. Cloudflare 계정을 Workers Paid 요금제로 바꾼다(Containers 는 Free 에서 N/A).
2. API 토큰을 만든다: 대시보드 Account API tokens → Create Token → "Edit Cloudflare Workers" 템플릿 → 계정 범위를 이 계정 하나로 좁힌다. (출처: https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/ , 2026-10-29 확인. Containers 전용 권한을 따로 더해야 하는지는 이 문서에서 확인하지 못했다 — 미확인. 첫 배포가 권한 오류로 실패하면 오류 문구를 보고 권한을 더한다.)
3. GitHub 저장소 → Settings → Secrets and variables → Actions 에 secret 2개를 넣는다.
   - `CLOUDFLARE_API_TOKEN`: 2번에서 만든 토큰
   - `CLOUDFLARE_ACCOUNT_ID`: 계정 ID
4. (선택) 초대 링크 서명 비밀값을 고정하고 싶으면 `cd deploy/cloudflare && npx wrangler secret put HANDOFF_AUTH_SECRET`. 넣지 않으면 앱이 부팅할 때마다 데이터 폴더에 새로 만든다(공개 데모는 정지 시 어차피 초기화되므로 없어도 된다). LLM 키(`HANDOFF_LLM_API_KEY`)는 넣지 않는다. Worker 가 컨테이너로 넘기지도 않는다.

## 배포

1. GitHub 저장소 → Actions → `deploy-cloudflare` → Run workflow.
2. 워크플로는 `deploy/cloudflare` 에서 `npm ci` → `npm run types` → `npm run typecheck` → `npx wrangler deploy` 를 실행한다. 러너(ubuntu-latest)의 Docker 가 `Dockerfile` 이미지를 빌드해 올린다. Cloudflare 는 이미지가 linux/amd64 에서 돌아야 한다고 적는다(https://developers.cloudflare.com/containers/get-started/ ).
3. 첫 배포 뒤에는 몇 분 기다린다. 공식 문서에 따르면 Cloudflare 가 컨테이너를 준비하는 동안 Worker 주소는 응답해도 컨테이너로 가는 요청은 오류가 날 수 있다(같은 get-started 페이지).

`worker-configuration.d.ts`(Worker 타입)는 `wrangler types` 가 만드는 생성 파일이라 저장소에 넣지 않는다(`.gitignore`). 로컬에서 타입체크하려면 `cd deploy/cloudflare && npm ci && npm run types && npm run typecheck`. CI 는 워크플로의 `npm run types` 단계가 타입체크 전에 만든다.

## 확인

`YOUR-SUBDOMAIN` 은 계정의 workers.dev 하위 도메인이다(대시보드 Workers & Pages 에서 보인다).

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://handoff-demo.YOUR-SUBDOMAIN.workers.dev/health   # → 200
curl -s -o /dev/null -w '%{http_code}\n' https://handoff-demo.YOUR-SUBDOMAIN.workers.dev/demo     # → 200
```

브라우저로 `/demo` 를 열어 세 창(팀장·퇴사자·후임)과 맨 위 경고 문구 "연습용 공간입니다. …" 가 보이는지 본다. 컨테이너가 잠들어 있던 뒤 첫 요청은 기동 시간만큼 느리다.

## README 링크 바꾸기

README 의 `<!-- DEMO_URL: ... -->` 주석 바로 아래 "바로 써 보기" 줄에 있는 `https://handoff-demo.YOUR-SUBDOMAIN.workers.dev/demo` 의 `YOUR-SUBDOMAIN` 한 곳만 실제 하위 도메인으로 바꾸고 "(배포 전 자리표시)" 문구를 지운다. README 에서 `YOUR-SUBDOMAIN` 은 그 한 곳뿐이다.

## 공개 데모 안전 점검

| 항목 | 현재 동작 | 근거(파일·테스트) |
|------|-----------|-------------------|
| 답 공급자 | `handoff-demo` 가 `HANDOFF_PROVIDER=replay` 를 기본으로 둔다. 녹화(cassette)에 없는 요청은 LLM 키와 하루 상한이 있을 때만 실제 호출하고, 키가 없으면 내장 예시 답(FakeLLM)으로 돌린다. 녹화 재생은 네트워크를 쓰지 않는다(`_NoNetwork`). Worker 는 LLM 키를 넘기지 않으므로 공개 데모는 외부 호출이 없다 | `src/handoff/demo/launch.py`, `src/handoff/providers_replay.py`(`ReplayLLM._run`), `src/handoff/providers.py`, `deploy/cloudflare/src/index.ts`; `tests/e2e/test_demo_mode.py::test_scenario_steps_replay_without_miss` |
| 업로드·본문 크기·요청 상한 | 실제 자료 업로드(`POST /leavers/{id}/sources`)는 403 `demo upload disabled`. 본문이 8192 바이트를 넘으면 413. 경로별 분당 120회, `/demo/start` 는 시간당 10회 | `src/handoff/demo/guard.py`(`DemoBodyGuard`), `src/handoff/config.py`; `tests/e2e/test_demo_mode.py::test_upload_blocked_but_mock_sources_allowed`·`test_oversized_body_is_rejected`·`test_start_rate_limit` |
| 관리 화면·초대 발급 | `/admin/leavers` 와 초대 발급 API 는 admin 역할만 허용한다. admin 링크는 CLI(`python -m handoff.rbac.cli admin`)로만 만든다. 데모 역할 전환은 leaver·successor·manager 만 받고 `admin` 은 422 라서 공개 데모 방문자는 관리자가 될 수 없다 | `src/handoff/rbac/policy.csv`, `src/handoff/rbac/cli.py`, `src/handoff/demo/routes.py`(`RoleRequest`); `tests/unit/test_admin.py::test_other_roles_forbidden_on_admin_api`·`test_anonymous_admin_api_401`, `tests/unit/test_demo_routes.py::test_role_invalid_is_422` |
| 방문자별 연습 공간 격리 | `/demo/start` 마다 임의 이름(`demo-` + 무작위 10자리)의 퇴사자 공간을 새로 만들고 세션 쿠키에 묶는다. 다른 방문자의 쿠키로는 남의 공간을 읽지 못한다 | `src/handoff/demo/sandbox.py`, `src/handoff/demo/routes.py`; `tests/e2e/test_demo_mode.py::test_visitors_are_isolated`, `tests/unit/test_demo_routes.py::test_other_visitor_cookie_cannot_read_first_sandbox` |
| 데이터 수명 | 연습 공간은 24시간(`HANDOFF_DEMO_TTL_HOURS` 기본 24) 뒤 만료되고, 새 공간을 만들 때 만료된 공간의 데이터를 지운다. 동시에 살아 있는 공간은 200개까지(넘으면 503). Cloudflare 에서는 10분 쉬어 컨테이너가 정지하면 디스크가 초기화되어 그 전에 모두 사라진다 | `src/handoff/demo/sandbox.py`(`purge_expired`), `src/handoff/config.py`, `deploy/cloudflare/src/index.ts`(`sleepAfter`); `tests/unit/test_demo_sandbox.py::test_purge_expired_removes_rows_and_data`, `tests/unit/test_demo_routes.py::test_max_sandboxes_returns_503` |
| 입력 경고 문구 | `/demo` 머리에 "연습용 공간입니다. 실제 이름·연락처·회사 정보는 넣지 마세요. 서버가 쉬거나 하루가 지나면 지워집니다." 를 늘 보여 준다 | `web/src/pages/Demo.tsx`; E2E 는 S7 에서 추가 |
| 클라이언트 주소 | 주소는 요청 상한 키로만 쓴다. 신뢰 프록시 뒤에서는 `X-Forwarded-For` 맨 오른쪽 값만 쓰고, slowapi 의 상한 초과 로그에서는 `<client>` 로 가린다. 연습 공간 기록에는 주소를 저장하지 않는다 | `src/handoff/demo/guard.py`(`make_client_key`, `_RedactClientKey`); `tests/unit/test_demo_trusted_proxies.py`, `tests/unit/test_demo_sandbox.py::test_registry_persists_without_visitor_address`. 로그 가림 자체를 확인하는 테스트는 없다(코드로만 확인) |

## 로컬 검증 한계

- 이 기기에서 `npm run dry-run`(`wrangler deploy --dry-run`)이 `Dockerfile` 이미지를 실제로 빌드하고 Worker 를 번들했다(exit 0, 2026-10-29). 결과 원문 한 줄: `Total Upload: 53.90 KiB / gzip: 13.29 KiB`, 이어서 `--dry-run: exiting now.` 이 기기에서 만든 이미지의 아키텍처가 linux/amd64 인지는 확인하지 않았다. 실제 배포는 ubuntu 러너에서 빌드한다.
- Cloudflare 위에서 컨테이너가 실제로 기동하는지, workers.dev 주소가 `/health`·`/demo` 에 200 을 주는지는 배포 뒤에만 확인할 수 있다(위 "확인" 절).
- 앱 동작은 컨테이너와 같은 실행 명령(`handoff-demo`, Dockerfile CMD 는 `--public --port 7860` 을 더함)을 로컬에서 띄우는 E2E 로 대신 확인한다: `tests/e2e/test_make_demo.py`(`make demo` 가 주소를 출력하고 `/demo` 세 창을 보여 줌), `tests/e2e/test_demo_mode.py`(녹화 재생·격리·업로드 차단·본문 상한·요청 상한).
- Worker 자체(헤더 덮어쓰기·컨테이너 전달)는 `npm run typecheck` 까지만 확인했다. `wrangler dev` 는 로컬 컨테이너 실행이 필요해 돌리지 않았다.
- `worker-configuration.d.ts` 는 생성 파일이라 gitignore 했고, CI 워크플로의 `npm run types` 단계가 만든다.

## 인스턴스 크기 바꾸기

메모리가 부족하면 `deploy/cloudflare/wrangler.jsonc` 의 `"instance_type": "basic"` 을 `"standard-1"`(1/2 vCPU·4 GiB·8 GB)로 바꾸고 워크플로를 다시 실행한다. 메모리 과금이 4배가 되므로 위 "비용" 절의 포함 시간도 그만큼 줄어든다.
