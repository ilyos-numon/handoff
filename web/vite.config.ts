import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 백엔드에 CORS 가 없으므로(07 #2) 개발 서버가 API 를 같은 출처처럼 넘긴다.
// 화면 경로(/, /intro, /chat, /report, /admin, /demo)는 이 앱이 그리고,
// /admin/leavers·/demo/<api> 처럼 같은 접두어의 API 만 백엔드로 보낸다.
// 다른 리스너가 IPv6 localhost 를 잡고 있으면(07 #26) HANDOFF_BACKEND 로 주소를 직접 준다.
const BACKEND = process.env.HANDOFF_BACKEND ?? "http://localhost:8780";
const API =
  "^/(auth|sessions|coverage|kb|leavers|depth|drills|question-trees|admin/leavers|demo/(start|status|role|reset)|health|static|docs|openapi\\.json)";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { [API]: { target: BACKEND, changeOrigin: false } },
  },
  preview: {
    port: 4173,
    proxy: { [API]: { target: BACKEND, changeOrigin: false } },
  },
});
