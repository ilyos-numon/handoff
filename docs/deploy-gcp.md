# 공개 데모 배포 (Google Cloud Run)

현재 공개 데모: https://handoff-495289802616.asia-northeast3.run.app/demo

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

## 배포

저장소 루트에서 실행한다. Cloud Build 가 `Dockerfile` 로 이미지를 만들고 Cloud Run 서비스로 올린다(5~10분).

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
- `main` 에 푸시해도 자동 배포되지 않는다. 바뀐 코드를 올리려면 같은 명령을 다시 실행한다. 비밀값을 그대로 두려면 두 번째부터는 `--set-env-vars` 줄을 빼도 기존 값이 유지된다.

## 확인

```bash
U=https://handoff-495289802616.asia-northeast3.run.app
curl -s -o /dev/null -w '%{http_code}\n' $U/health     # 200
curl -s -o /dev/null -w '%{http_code}\n' $U/demo       # 200
curl -s -o /dev/null -w '%{http_code}\n' $U/intro/ceo  # 404
HANDOFF_LIVE_URL=$U uv run pytest tests/e2e/live_demo_check.py -q   # 3 passed
```

## 주의

- 비용: 요청 기반 과금의 무료 한도(월 요청 200만, vCPU 18만 초, 메모리 36만 GiB초) 안이면 0원. 빌드 이미지 저장(Artifact Registry `cloud-run-source-deploy`)은 소액이 나올 수 있다. 오래된 이미지는 지운다.
- 데이터는 남지 않는다. 인스턴스가 0개로 줄거나 재배포되면 데이터 폴더가 초기화되고, 연습 공간은 새 시드로 다시 만들어진다.
- 내리기: `gcloud run services delete handoff --region asia-northeast3`.
