#!/usr/bin/env bash
# Cloud Run 자동 배포(GitHub Actions + Workload Identity Federation) 일회성 설정 스크립트.
#   scripts/setup-cloudrun-cicd.sh            dry-run: 실행할 명령을 stdout 에 한 줄씩 출력한다 (아무것도 바꾸지 않음)
#   scripts/setup-cloudrun-cicd.sh --apply    실행한다. gcloud 로그인·gh 로그인(GH_USER) 필요
# 이미 있는 풀·프로바이더·서비스 계정은 describe 로 확인하고 건너뛴다. IAM 바인딩은 멱등이라 그대로 실행한다.
# 배포(gcloud run deploy)·서비스 환경변수 변경은 하지 않는다. 비밀값은 어디에도 없다.
# 종료코드: 0 성공, 1 실행 오류, 2 사용법 오류
set -euo pipefail

PROJECT_ID=${PROJECT_ID:-handoff-demo-513200}
PROJECT_NUMBER=${PROJECT_NUMBER:-495289802616}
REGION=${REGION:-asia-northeast3}
GH_REPO=${GH_REPO:-ilyos-numon/handoff}
GH_USER=${GH_USER:-ilyos-numon}
DEPLOY_SA_NAME=${DEPLOY_SA_NAME:-handoff-deployer}
RUNTIME_SA=${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}
AR_REPO=${AR_REPO:-cloud-run-source-deploy}
POOL=github
PROVIDER=github-oidc

DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GH_REPO}"

usage() {
  echo "usage: $0 [--apply]  (인자 없음 = dry-run)" >&2
  exit 2
}

APPLY=0
if [ $# -eq 1 ] && [ "$1" = "--apply" ]; then
  APPLY=1
elif [ $# -ne 0 ]; then
  usage
fi

# 명령 한 줄을 사람이 다시 붙여 넣을 수 있는 모양으로 출력한다.
show() {
  local out="" a
  for a in "$@"; do
    if [[ "$a" =~ ^[A-Za-z0-9_./:@=,+-]+$ ]]; then
      out+="$a "
    elif [[ "$a" != *[\$\`\"\\]* ]]; then
      out+="\"$a\" "
    else
      out+="'${a//\'/\'\\\'\'}' "
    fi
  done
  echo "${out% }"
}

# dry-run 이면 출력만, --apply 면 출력 후 실행한다.
run() {
  show "$@"
  if [ "$APPLY" -eq 1 ]; then
    "$@"
  fi
}

# --apply 에서만 존재를 확인한다. 있으면 0 (생성 건너뜀).
exists() {
  [ "$APPLY" -eq 1 ] || return 1
  "$@" >/dev/null 2>&1
}

skip() {
  echo "# skip: $1 이미 있음" >&2
}

G=(--project "$PROJECT_ID")

# 1 API
run gcloud services enable sts.googleapis.com iamcredentials.googleapis.com "${G[@]}"

# 2 배포 서비스 계정
if exists gcloud iam service-accounts describe "$DEPLOY_SA" "${G[@]}"; then
  skip "$DEPLOY_SA"
else
  run gcloud iam service-accounts create "$DEPLOY_SA_NAME" "${G[@]}"
fi

# 3~5 배포 SA 권한: Cloud Run 관리, 이미지 push, 런타임 SA 로 실행
run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$DEPLOY_SA" --role=roles/run.admin "${G[@]}"
run gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" --location="$REGION" \
  --member="serviceAccount:$DEPLOY_SA" --role=roles/artifactregistry.writer "${G[@]}"
run gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:$DEPLOY_SA" --role=roles/iam.serviceAccountUser "${G[@]}"

# 6 Workload Identity 풀
if exists gcloud iam workload-identity-pools describe "$POOL" --location=global "${G[@]}"; then
  skip "pool $POOL"
else
  run gcloud iam workload-identity-pools create "$POOL" --location=global "${G[@]}"
fi

# 7 GitHub OIDC 프로바이더 (이 저장소의 main 브랜치만)
if exists gcloud iam workload-identity-pools providers describe "$PROVIDER" --location=global \
  --workload-identity-pool="$POOL" "${G[@]}"; then
  skip "provider $PROVIDER"
else
  run gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global \
    --workload-identity-pool="$POOL" \
    --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref \
    --attribute-condition="assertion.repository=='$GH_REPO' && assertion.ref=='refs/heads/main'" \
    "${G[@]}"
fi

# 8 저장소 주체가 배포 SA 를 가장할 수 있게 한다
run gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --role=roles/iam.workloadIdentityUser \
  --member="$PRINCIPAL" "${G[@]}"

# 9~10 GitHub Variables (평문. 토큰 값은 출력하지 않는다)
set_var() {
  local name="$1" value="$2"
  echo "GH_TOKEN=\$(gh auth token -u $GH_USER) $(show gh variable set "$name" --body "$value" --repo "$GH_REPO")"
  if [ "$APPLY" -eq 1 ]; then
    GH_TOKEN="$(gh auth token -u "$GH_USER")" gh variable set "$name" --body "$value" --repo "$GH_REPO"
  fi
}
set_var GCP_PROJECT_ID "$PROJECT_ID"
set_var GCP_REGION "$REGION"
set_var GCP_WIF_PROVIDER "$WIF_PROVIDER"
set_var GCP_DEPLOY_SA "$DEPLOY_SA"

if [ "$APPLY" -eq 0 ]; then
  echo "dry-run: 실행하지 않음 (--apply 로 실행)" >&2
fi
