# 공개 데모 배포 (Render 무료)

현재 공개 데모: https://handoff-4krf.onrender.com/demo

로컬에서는 `make demo` 한 줄이면 된다. 이 문서는 저장소 루트 `Dockerfile` 을 수정 없이 Render 무료 웹 서비스로 올리는 방법이다. 대안 문서: [deploy-cloudflare.md](deploy-cloudflare.md)(유료: Workers Paid 필요), [deploy.md](deploy.md)(Hugging Face Spaces, 계정 무료 실행 한도 0 으로 중단).

## 절차

1. Render 대시보드 → New → Web Service → GitHub 저장소 연결.
2. Runtime: Docker. Branch: `main`. Dockerfile 경로는 저장소 루트 그대로.
3. Instance Type: Free.
4. 환경변수 2개:
   - `PORT=7860` — 컨테이너가 듣는 포트(`Dockerfile` 의 `EXPOSE 7860`)와 맞춘다.
   - `HANDOFF_AUTH_SECRET` — 초대 링크·로그인 쿠키 서명 키. 무작위 긴 문자열을 넣는다. 저장소에 커밋하지 않는다. LLM 키(`HANDOFF_LLM_API_KEY`)는 넣지 않는다.
5. Create Web Service. 이후 `main` 에 푸시하면 Render 가 다시 빌드·배포한다.

## 확인

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://handoff-4krf.onrender.com/health     # 200
curl -s -o /dev/null -w '%{http_code}\n' https://handoff-4krf.onrender.com/demo       # 200
curl -s -o /dev/null -w '%{http_code}\n' https://handoff-4krf.onrender.com/intro/ceo  # 404
```

- `/health` 200
- `/demo` 200, 브라우저로 열면 세 역할 화면 iframe 3개 (화면은 브라우저에서 그려지므로 curl 로는 셀 수 없다)
- `/intro` 200, 없는 역할 `/intro/ceo` 404

공개 주소를 실제로 여는 E2E 는 환경변수 `HANDOFF_LIVE_URL` 이 있을 때만 돈다: `HANDOFF_LIVE_URL=https://handoff-4krf.onrender.com uv run pytest tests/e2e/live_demo_check.py -q`.

## 주의

- 메모리 512 MB. 인스턴스는 하나다.
- 15분 동안 요청이 없으면 잠든다. 잠든 뒤 첫 접속은 30~60초 걸린다.
- 디스크는 남지 않는다. 재시작·재배포·잠들었다 깨어날 때 데이터 폴더가 초기화되고, 연습 공간은 새 시드로 다시 만들어진다.
