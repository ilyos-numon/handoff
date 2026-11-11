import type {
  ArtifactKind, Axis, CardType, Certainty, ChangeEvent, DemoRole, GapKind, QueueSource, QueueStatus, Risk, Role, SourceKind, ThreadState,
} from "./types";

// 06 §3 열거형 → 화면 문구.
export const AXES: Axis[] = ["systems", "routines", "incidents", "people"];
export const AXIS_LABEL: Record<Axis, string> = {
  systems: "담당 시스템", routines: "반복 업무", incidents: "사고 이력", people: "관계자",
};
export const ROLE_LABEL: Record<Role, string> = {
  leaver: "퇴사자", successor: "후임", manager: "팀장", admin: "관리자",
};
export const ROLE_HOME: Record<Role, string> = { leaver: "/", successor: "/chat", manager: "/report", admin: "/admin" };
export const DEMO_ROLES: DemoRole[] = ["leaver", "successor", "manager"];

export const SOURCE_LEAVER_LABEL: Record<QueueSource, string> = {
  manager: "팀장이 우선 지정한 질문",
  successor: "후임이 물어본 질문",
  gap: "빈 구멍 확인 질문",
  premortem: "사전 부검 질문",
  generated: "업무 자료에서 만든 질문",
};
export const SOURCE_LABEL: Record<QueueSource, string> = {
  manager: "팀장 우선", successor: "후임 질문", gap: "빈 구멍", premortem: "사전 부검", generated: "업무 자료",
};
export const SOURCE_PRIORITY: Record<QueueSource, number> = { manager: 0, successor: 1, gap: 2, premortem: 3, generated: 4 };
export const STATUS_LABEL: Record<QueueStatus, string> = { pending: "대기", asked: "질문함", answered: "답변됨" };
export const STATUS_SUCCESSOR_LABEL: Record<QueueStatus, string> = {
  pending: "다음 세션에 물어볼 예정",
  asked: "퇴사자 세션에서 질문 중",
  answered: "답변됨",
};
export const THREAD_STATE_NOTE: Record<Exclude<ThreadState, "answered">, string> = {
  waiting: "다음 세션에서 물어볼 예정이에요",
  asking: "퇴사자 세션에서 묻고 있어요",
  answered_open: "퇴사자가 답했어요. 세션이 끝나면 여기에 보여요",
};
export const ANSWERED_BADGE = "답이 왔어요";
export const CERTAINTY_LABEL: Record<Certainty, string> = {
  certain: "근거 확실", uncertain: "근거 불확실 · 퇴사자 확인 권장",
};
export const CARD_TYPE_LABEL: Record<CardType, string> = {
  procedure: "절차", pitfall: "함정", decision: "결정", contact: "연락처", entity: "대상",
};
export const GAP_KIND_LABEL: Record<GapKind, string> = {
  mentioned_only: "언급만 있고 기록 없음", contradiction: "기록끼리 어긋남", bus_factor: "한 곳에만 있는 지식",
};
export const RISK_LABEL: Record<Risk, string> = { high: "높음", medium: "보통", low: "낮음" };
export const EVENTS: ChangeEvent[] = ["system_replaced", "vendor_changed", "team_reorganized", "policy_changed", "schedule_changed"];
export const EVENT_LABEL: Record<ChangeEvent, string> = {
  system_replaced: "시스템 교체·이전",
  vendor_changed: "외부 업체 변경",
  team_reorganized: "조직·담당 변경",
  policy_changed: "규정·권한 정책 변경",
  schedule_changed: "일정·주기 변경",
};
export const ARTIFACT_KINDS: ArtifactKind[] = ["runbook", "onboarding_30d", "pitfalls_top10", "adr", "premortem"];
export const ARTIFACT_TITLE: Record<ArtifactKind, string> = {
  runbook: "운영 런북",
  onboarding_30d: "30일 온보딩 플랜",
  pitfalls_top10: "함정 Top10",
  adr: "결정 기록(ADR)",
  premortem: "사전 부검: 먼저 터질 일 Top5",
};
export const SOURCE_KINDS: SourceKind[] = ["wiki", "runbook", "cron", "incident", "commit", "org"];
export const SOURCE_KIND_LABEL: Record<SourceKind, string> = {
  wiki: "위키", runbook: "런북", cron: "정기 작업", incident: "사고 기록", commit: "커밋", org: "조직도",
};
export const JOB_ROLE = "backend_developer";
