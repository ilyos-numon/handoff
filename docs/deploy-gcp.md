# 공개 데모 배포 (Google Cloud Run)

현재 공개 데모: https://handoff-1072416881206.asia-northeast3.run.app/demo

로컬에서는 `make demo` 한 줄이면 된다. 이 문서는 저장소 루트 `Dockerfile` 을 수정 없이 Cloud Run 에 올리는 방법이다. 예비: [deploy-render.md](deploy-render.md)(Render 무료). 대안: [deploy-cloudflare.md](deploy-cloudflare.md)(유료: Workers Paid 필요), [deploy.md](deploy.md)(Hugging Face Spaces, 중단).

## 준비 (한 번)

1. Google Cloud 콘솔에서 프로젝트를 만들고 결제 계정을 연결한다. 무료 한도 안이어도 결제 계정이 있어야 한다. 예산 알림(예: 1달러)을 걸어 둔다.
2. gcloud 설치·로그인:
   ```bash
   brew install --cask google-cloud-sdk
   gcloud auth login
   gcloud config set project <PROJECT_ID>
   ```
3. 새 프로젝트는 소스 빌드에 쓰는 기본 서비스 계정에 권한이 없어 첫 배포가 `PERMISSION_DENIED` 로 실패한다. 빌드 권한을 한 번 준다(`<PROJECT_NUMBER>` 는 오류 메시지의 `<번호>-compute@developer.gserviceaccount.com` 앞부분):
   ```bash
   gcloud projects add-iam-policy-binding <PROJECT_ID> \
     --member=serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com \
     --role=roles/run.builder
   ```
4. 자동 배포 설정: GitHub Actions 가 키 파일 없이 Workload Identity Federation 으로 GCP 에 로그인하도록 배포 서비스 계정·WIF 풀·GitHub Variables 를 만든다. 먼저 인자 없이 실행해 명령을 확인하고, 맞으면 `--apply` 로 실행한다(`gcloud`·`gh` 로그인 필요, 다시 실행해도 안전하다):
   ```bash
   scripts/setup-cloudrun-cicd.sh           # dry-run: 실행할 명령만 출력
   scripts/setup-cloudrun-cicd.sh --apply   # 실제 생성
   ```
   만드는 것: 배포 SA `handoff-deployer`(`roles/run.admin`, Artifact Registry `cloud-run-source-deploy` 쓰기, 런타임 SA 에 `roles/iam.serviceAccountUser`), WIF pool `github` / provider `github-oidc`(이 저장소의 `refs/heads/main` 만 허용), 아래 GitHub Variables 4개. 비밀값(Secrets)은 쓰지 않는다.

   | GitHub Variable | 값 |
   |-----------------|----|
   | `GCP_PROJECT_ID` | GCP 프로젝트 ID |
   | `GCP_REGION` | `asia-northeast3` |
   | `GCP_WIF_PROVIDER` | `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-oidc` |
   | `GCP_DEPLOY_SA` | `handoff-deployer@<PROJECT_ID>.iam.gserviceaccount.com` |

## 배포 (자동 — 기본)

`main` 에 push 하면 GitHub Actions `deploy-cloudrun`(`.github/workflows/deploy-cloudrun.yml`)이 돈다. 지금 코드로 다시 올리려면 GitHub 저장소 → Actions → `deploy-cloudrun` → Run workflow(`main`)를 누른다.

하는 일: Workload Identity Federation 으로 GCP 에 로그인(키 없음) → 러너에서 `Dockerfile` 로 이미지 빌드 → Artifact Registry `cloud-run-source-deploy/handoff:<커밋 SHA>` 에 push → `gcloud run deploy handoff --image ...`(`--port 7860 --memory 1Gi --max-instances 1 --min-instances 0`, 수동 절과 같은 값) → 새 리비전과 환경변수 이름 `HANDOFF_AUTH_SECRET` 존재 확인 → 서비스 URL 의 `/health` 가 200 이 될 때까지 최대 10분 기다린 뒤 URL 과 리비전을 실행 요약에 적는다.

- GitHub Variable `GCP_PROJECT_ID` 나 `GCP_WIF_PROVIDER` 가 없으면 배포 단계는 건너뛴다(실패 아님). 둘은 있는데 `GCP_REGION`·`GCP_DEPLOY_SA` 가 없으면 실패한다.
- `HANDOFF_AUTH_SECRET` 은 그대로 유지된다. 워크플로우는 환경변수·서비스 계정 플래그를 넘기지 않으므로 기존 리비전의 값을 이어받고, 확인 단계도 이름만 읽어 값은 로그에 출력되지 않는다.

## 배포 (수동 — 예비)

자동 배포가 막히면(Actions 장애, WIF 설정 문제 등) 저장소 루트에서 이 명령으로 직접 올린다. Cloud Build 가 `Dockerfile` 로 이미지를 만들고 Cloud Run 서비스로 올린다(5~10분).

```bash
SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
gcloud run deploy handoff --source . --region asia-northeast3 \
  --port 7860 --memory 1Gi --max-instances 1 --min-instances 0 \
  --allow-unauthenticated \
  --set-env-vars HANDOFF_AUTH_SECRET=$SECRET
```

- `--port 7860`: 컨테이너가 듣는 포트(`Dockerfile` 의 `EXPOSE 7860`).
- `--max-instances 1`: 연습 공간이 컨테이너 안(`/tmp`)에 있으므로 인스턴스가 둘이면 방문자 요청이 갈라져 진행이 끊긴다. 반드시 1.
- `--memory 1Gi`: Cloud Run 의 `/tmp` 는 메모리에 잡힌다. 데이터까지 담을 여유를 둔다.
- `--min-instances 0`: 요청이 없으면 0개로 줄어 비용이 들지 않는다. 다음 첫 요청은 컨테이너를 다시 띄운다.
- `HANDOFF_AUTH_SECRET`: 쿠키 서명 키. 화면·저장소·채팅에 남기지 않는다. LLM 키(`HANDOFF_LLM_API_KEY`)는 넣지 않는다.
- 자동 배포가 막히면 이 명령으로 바뀐 코드를 올린다. 비밀값을 그대로 두려면 두 번째부터는 `--set-env-vars` 줄을 빼도 기존 값이 유지된다.

## 확인

```bash
U=https://handoff-1072416881206.asia-northeast3.run.app
curl -s -o /dev/null -w '%{http_code}\n' $U/health     # 200
curl -s -o /dev/null -w '%{http_code}\n' $U/demo       # 200
curl -s -o /dev/null -w '%{http_code}\n' $U/intro/ceo  # 404
HANDOFF_LIVE_URL=$U uv run pytest tests/e2e/live_demo_check.py -q   # 3 passed
```

## 주의

- 비용: 요청 기반 과금의 무료 한도(월 요청 200만, vCPU 18만 초, 메모리 36만 GiB초) 안이면 0원. 빌드 이미지 저장(Artifact Registry `cloud-run-source-deploy`)은 소액이 나올 수 있다. 오래된 이미지는 지운다.
- 데이터는 남지 않는다. 인스턴스가 0개로 줄거나 재배포되면 데이터 폴더가 초기화되고, 연습 공간은 새 시드로 다시 만들어진다.
- 내리기: `gcloud run services delete handoff --region asia-northeast3`.
- 되돌리기(직전 리비전으로): 리비전 목록을 보고 이전 리비전에 트래픽을 모두 돌린다. 다음 배포가 성공하면 트래픽은 다시 최신 리비전으로 간다.
  ```bash
  gcloud run revisions list --service handoff --region asia-northeast3
  gcloud run services update-traffic handoff --region asia-northeast3 --to-revisions <이전리비전>=100
  ```
- 자동 배포 걷어내기: `.github/workflows/deploy-cloudrun.yml` 을 지우거나 GitHub Variable `GCP_WIF_PROVIDER` 를 지우면(배포 단계 건너뜀) 멈춘다. GCP 쪽 리소스까지 지우려면:
  ```bash
  gcloud iam service-accounts delete handoff-deployer@<PROJECT_ID>.iam.gserviceaccount.com --project <PROJECT_ID>
  gcloud iam workload-identity-pools delete github --location=global --project <PROJECT_ID>
  ```
