import type {
  AnswerResponse, ArtifactKind, AskResponse, AtRiskReport, Artifact, CardType, Certainty, ChangeEvent, CoverageReport,
  DemoRole, DemoStart, DemoStatus, DrillFeedback, DrillScenario, EndSessionResponse, Entity, FenceResponse,
  GapReport, GenerateResponse, InviteResponse, KnowledgeCard, LeaverRecord, ManagerSafeCard, Me, QueueItem,
  QueueSource, QueueStatus, SessionState, Source, SourceKind, StartSessionResponse, Axis,
} from "./types";
import { DEMO_ROLES, JOB_ROLE } from "./labels";

/* ---------------- 데모 iframe: ?as=<role> → X-Handoff-As (05 B) ---------------- */
const asParam = new URLSearchParams(window.location.search).get("as");
export const AS_ROLE: DemoRole | null = (DEMO_ROLES as string[]).includes(asParam ?? "") ? (asParam as DemoRole) : null;
export const IN_FRAME = (() => { try { return window.top !== window; } catch { return true; } })();

/* ---------------- 오류 ---------------- */
export interface FieldError { field: string; type: string }
export class ApiError extends Error {
  constructor(public status: number, public code: string, public fields: FieldError[] = []) {
    super(code);
  }
  is(status: number, code?: string) { return this.status === status && (code === undefined || this.code === code); }
}

const GENERIC: Record<number, string> = {
  0: "서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  401: "초대 링크로 다시 들어와 주세요.",
  403: "이 기능을 쓸 권한이 없습니다.",
  404: "찾을 수 없습니다.",
  409: "지금은 할 수 없는 동작입니다. 화면을 새로 고칩니다.",
  413: "글이 너무 깁니다. 줄여서 다시 보내 주세요.",
  422: "입력값을 확인해 주세요.",
  429: "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
  503: "응답을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
};
// detail 은 영어 코드값이라 그대로 보여 주지 않는다(01 §5.3).
const SPECIFIC: Record<string, string> = {
  "401 demo session required": "데모 공간이 만료됐습니다. 데모를 새로 시작해 주세요.",
  "403 demo upload disabled": "데모에서는 모의 자료만 쓸 수 있습니다.",
  "409 session closed": "다른 창에서 세션이 끝났습니다.",
  "409 session time over": "세션 시간이 끝났습니다.",
  "409 session limit reached": "모든 세션을 마쳤습니다. 수고하셨습니다.",
  "409 question tree exhausted": "준비된 질문을 모두 마쳤습니다.",
  "409 no cards": "아직 퇴사자 세션 기록이 부족합니다. 세션이 끝난 뒤 다시 해 보세요.",
  "409 no sources": "업무 자료를 먼저 넣어 주세요.",
  "503 demo busy": "데모 방문자가 많습니다. 잠시 후 다시 시도해 주세요.",
  "503 demo seed failed": "데모를 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.",
};
export function userMessage(err: unknown): string {
  if (err instanceof ApiError) return SPECIFIC[`${err.status} ${err.code}`] ?? GENERIC[err.status] ?? "문제가 생겼습니다. 다시 시도해 주세요.";
  return GENERIC[0];
}

/* ---------------- fetch 래퍼 ---------------- */
// LLM 공급자가 qwen 이면 요청당 20초 + 재시도까지 걸릴 수 있다(03 §10).
const TIMEOUT_MS = 60_000;

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers = new Headers();
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (AS_ROLE) headers.set("X-Handoff-As", AS_ROLE);
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(path, {
      method, headers, credentials: "same-origin", signal: ctrl.signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network");
  } finally {
    window.clearTimeout(timer);
  }
  if (res.status === 204) return null as T;
  let data: unknown = null;
  try { data = await res.json(); } catch { /* 500 등 JSON 아님 */ }
  if (res.ok) return data as T;
  const d = data as { detail?: unknown; error?: unknown } | null;
  const raw = d?.detail ?? d?.error ?? res.statusText; // 429 만 error 키(07 #4)
  if (Array.isArray(raw)) {
    const fields = raw.map((e: { loc?: unknown[]; type?: string }) => ({
      field: (e.loc ?? []).slice(1).join("."), type: e.type ?? "",
    }));
    throw new ApiError(res.status, "validation", fields);
  }
  throw new ApiError(res.status, String(raw));
}

const enc = encodeURIComponent;
const qs = (o: Record<string, string | number | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};
const L = (leaver: string) => `/leavers/${enc(leaver)}`;

/** 팀장 화면에는 답변 원문 발췌가 가면 안 된다(07 #1). 한 곳에서 버린다.
 * 인증 켠 서버는 이미 빼고 보낸다(2026-10-27). 인증 끔 서버는 역할을 몰라 그대로 오므로 유지한다. */
function stripForManager(rep: AtRiskReport): AtRiskReport<ManagerSafeCard> {
  return {
    ...rep,
    cards: rep.cards.map(({ card, conditions }) => {
      const { evidence: _e, body: _b, uncertain_cues: _u, ...safe } = card;
      void _e; void _b; void _u;
      return { card: safe, conditions };
    }),
  };
}

/* ---------------- 엔드포인트 ---------------- */
// 인증 켬 모드에서 leaver 를 빠뜨리면 default 로 보고 403 이 난다(07 #3) → 모든 함수가 leaver 를 필수로 받는다.
export const Api = {
  me: () => request<Me>("GET", "/auth/me"),
  logout: () => request<null>("POST", "/auth/logout"),

  // 퇴사자
  startSession: (leaver: string) => request<StartSessionResponse>("POST", "/sessions", { role: JOB_ROLE, leaver }),
  session: (id: string) => request<SessionState>("GET", `/sessions/${enc(id)}`),
  answer: (id: string, text: string) => request<AnswerResponse>("POST", `/sessions/${enc(id)}/answers`, { text }),
  endSession: (id: string) => request<EndSessionResponse>("POST", `/sessions/${enc(id)}/end`),

  // 공통 읽기
  coverage: (leaver: string) => request<CoverageReport>("GET", `/coverage${qs({ leaver, role: JOB_ROLE })}`),
  artifact: (leaver: string, kind: ArtifactKind) => request<Artifact>("GET", `${L(leaver)}/artifacts/${kind}`),

  // 후임
  ask: (leaver: string, question: string) => request<AskResponse>("POST", "/kb/ask", { leaver, question }),
  fence: (leaver: string, subject: string) => request<FenceResponse>("POST", "/kb/fence", { leaver, subject }),
  cards: (leaver: string, f: { type?: CardType; certainty?: Certainty } = {}) =>
    request<KnowledgeCard[]>("GET", `${L(leaver)}/cards${qs(f)}`),
  atRisk: (leaver: string, event: ChangeEvent, target?: string) =>
    request<AtRiskReport>("GET", `${L(leaver)}/cards/at-risk${qs({ event, target })}`),
  atRiskForManager: async (leaver: string, event: ChangeEvent, target?: string) =>
    stripForManager(await request<AtRiskReport>("GET", `${L(leaver)}/cards/at-risk${qs({ event, target })}`)),
  successorQuestions: (leaver: string) => request<QueueItem[]>("GET", `${L(leaver)}/successor-questions`),
  newDrill: (leaver: string, seed_card_id?: string) =>
    request<DrillScenario>("POST", `${L(leaver)}/drills`, seed_card_id ? { seed_card_id } : {}),
  drill: (id: string) => request<DrillScenario>("GET", `/drills/${enc(id)}`),
  drillAnswer: (id: string, text: string) => request<DrillFeedback>("POST", `/drills/${enc(id)}/answers`, { text }),

  // 팀장
  queue: (leaver: string, f: { status?: QueueStatus; source?: QueueSource } = {}) =>
    request<QueueItem[]>("GET", `${L(leaver)}/queue${qs(f)}`),
  gaps: (leaver: string) => request<GapReport>("GET", `${L(leaver)}/gaps`),
  refreshGaps: (leaver: string) => request<GapReport>("POST", `${L(leaver)}/gaps/refresh${qs({ role: JOB_ROLE })}`),
  priority: (leaver: string, text: string, axis?: Axis) =>
    rawPost<QueueItem>(`${L(leaver)}/priority-questions`, { text, role: JOB_ROLE, ...(axis ? { axis } : {}) }),
  premortem: (leaver: string) => request<QueueItem[]>("POST", `${L(leaver)}/premortem/questions${qs({ role: JOB_ROLE })}`),
  sources: (leaver: string) => request<Source[]>("GET", `${L(leaver)}/sources`),
  addSource: (leaver: string, s: { kind: SourceKind; title: string; text: string; role_map?: Record<string, string> }) =>
    request<Source>("POST", `${L(leaver)}/sources`, { role_map: {}, ...s }),
  mockSources: (leaver: string) => request<Source[]>("POST", `${L(leaver)}/sources/mock`),
  entities: (leaver: string) => request<Entity[]>("GET", `${L(leaver)}/entities`),
  generate: (leaver: string, limit = 8) => request<GenerateResponse>("POST", `${L(leaver)}/questions/generate`, { role: JOB_ROLE, limit }),
  makeArtifact: (leaver: string, kind: ArtifactKind) => request<Artifact>("POST", `${L(leaver)}/artifacts/${kind}`),

  // 관리자
  leavers: () => request<LeaverRecord[]>("GET", "/admin/leavers"),
  createLeaver: (alias: string) => request<LeaverRecord>("POST", "/admin/leavers", alias ? { alias } : {}),
  invite: (leaver: string, role: DemoRole) => request<InviteResponse>("POST", `/admin/leavers/${enc(leaver)}/invites`, { role }),

  // 데모
  demoStatus: () => request<DemoStatus>("GET", "/demo/status"),
  demoStart: () => request<DemoStart>("POST", "/demo/start"),
  demoRole: (role: DemoRole) => request<{ role: DemoRole; home: string }>("POST", "/demo/role", { role }),
  demoReset: () => request<DemoStart>("POST", "/demo/reset"),
};

/** 200(기존 항목)과 201(새로 넣음)을 구분해야 하는 POST. */
async function rawPost<T>(path: string, body: unknown): Promise<{ created: boolean; data: T }> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (AS_ROLE) headers.set("X-Handoff-As", AS_ROLE);
  let res: Response;
  try {
    res = await fetch(path, { method: "POST", headers, credentials: "same-origin", body: JSON.stringify(body) });
  } catch { throw new ApiError(0, "network"); }
  let data: unknown = null;
  try { data = await res.json(); } catch { /* */ }
  if (!res.ok) {
    const d = data as { detail?: unknown; error?: unknown } | null;
    const raw = d?.detail ?? d?.error ?? res.statusText;
    throw new ApiError(res.status, Array.isArray(raw) ? "validation" : String(raw));
  }
  return { created: res.status === 201, data: data as T };
}
