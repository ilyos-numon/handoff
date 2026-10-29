// 06-models-and-flows.md 의 데이터 모델 사전을 옮긴 타입.
export type Role = "leaver" | "successor" | "manager" | "admin";
export type DemoRole = "leaver" | "successor" | "manager";
export type Axis = "systems" | "routines" | "incidents" | "people";
export type QuestionKind = "main" | "follow_up" | "queued";
export type QueueSource = "manager" | "successor" | "gap" | "premortem" | "generated";
export type QueueStatus = "pending" | "asked" | "answered";
export type Certainty = "certain" | "uncertain";
export type CardType = "procedure" | "pitfall" | "decision" | "contact" | "entity";
export type GapKind = "mentioned_only" | "contradiction" | "bus_factor";
export type Risk = "high" | "medium" | "low";
export type ChangeEvent = "system_replaced" | "vendor_changed" | "team_reorganized" | "policy_changed" | "schedule_changed";
export type ArtifactKind = "runbook" | "onboarding_30d" | "pitfalls_top10" | "adr" | "premortem";
export type SourceKind = "wiki" | "runbook" | "cron" | "incident" | "commit" | "org";

export interface Me { auth: boolean; role: Role | null; leaver: string | null }

export interface Question {
  id: string; axis: Axis; text: string; kind: QuestionKind;
  source: QueueSource | null; reason: string | null;
}
export interface StartSessionResponse {
  session_id: string; question: Question; leaver: string; number: number; planned: number;
  remaining_seconds: number; items_today: number; items_total: number;
}
export interface AnswerResponse {
  next_question: Question | null; remaining_seconds: number; items_today: number; items_total: number;
}
export interface EndSessionResponse {
  session_id: string; ingested: number; number: number; summary: string[]; items_total: number;
}
export interface SessionState {
  session_id: string; leaver: string; role: string; number: number; planned: number;
  status: "open" | "ended"; question: Question | null; remaining_seconds: number;
  items_today: number; items_total: number; summary: string[];
}

export interface QueueItem {
  id: string; leaver: string; role: string; source: QueueSource; axis: Axis; text: string;
  reason: string; status: QueueStatus; ref_id: string | null;
  asked_session_id: string | null; answered_session_id: string | null; created_at: string;
}

export type ThreadState = "waiting" | "asking" | "answered_open" | "answered";
export interface ThreadTurn {
  question_id: string; follow_up: boolean; question: string; answer: string;
  session_number: number | null; created_at: string;
}
export interface QuestionThread { item: QueueItem; state: ThreadState; turns: ThreadTurn[] }

export interface Evidence { session_id: string; question_id: string; session_number: number | null; excerpt: string }
export interface InvalidationCondition { event: ChangeEvent; condition: string }
export interface KnowledgeCard {
  id: string; leaver: string; role: string; session_id: string; type: CardType;
  title: string; body: string; entities: string[]; confidence: number;
  evidence: Evidence[]; certainty: Certainty; uncertain_cues: string[];
  invalidated_when: InvalidationCondition[]; created_at: string;
}
/** 팀장에게 보낼 수 있는 카드 모양 — 답변 원문(evidence·body·uncertain_cues)을 뺐다(07 #1). */
export type ManagerSafeCard = Omit<KnowledgeCard, "evidence" | "body" | "uncertain_cues">;
export interface AtRiskCard<C = KnowledgeCard> { card: C; conditions: InvalidationCondition[] }
export interface AtRiskReport<C = KnowledgeCard> {
  leaver: string; event: ChangeEvent; label: string; target: string | null; cards: AtRiskCard<C>[];
}

export interface Citation {
  session_id: string; question_id: string; axis: Axis; excerpt: string; score: number;
  session_number: number | null; created_at: string | null;
}
export interface KBAnswer { found: boolean; answer: string; citations: Citation[]; claims_removed: number }
export interface AskRoute {
  ask_role: string; reason: string; basis: "contact_card" | "people_answer";
  card_ids: string[]; question_ids: string[];
}
export interface AskResponse { answer: KBAnswer; queued: QueueItem | null; route: AskRoute | null; certainty: Certainty | null }

export interface FencePoint { text: string; card_ids: string[] }
export interface FenceResponse {
  subject: string; found: boolean; reasons: FencePoint[]; risks: FencePoint[]; cards: KnowledgeCard[];
  invalidated_when: InvalidationCondition[]; certainty: Certainty | null; claims_removed: number;
  queued: QueueItem | null;
}

export interface Gap {
  id: string; leaver: string; kind: GapKind; topic: string; detail: string; risk: Risk;
  card_ids: string[]; entity_ids: string[]; queue_item_id: string | null; created_at: string;
}
export interface UnknownArea { topic: string; card_ids: string[]; cues: string[]; detail: string }
export interface GapReport { leaver: string; gaps: Gap[]; queued: QueueItem[]; unknown_areas: UnknownArea[] }

export interface AxisCoverage { axis: Axis; label: string; covered: number; total: number; queued_answered: number }
export interface EmptyTopic { question_id: string; axis: Axis; label: string; text: string }
export interface CoverageReport {
  leaver: string; role: string; sessions_done: number; sessions_planned: number; items_total: number;
  axes: AxisCoverage[]; empty_topics: EmptyTopic[];
}

export interface ArtifactSection { heading: string; body: string; card_ids: string[] }
export interface Artifact {
  id: string; leaver: string; kind: ArtifactKind; title: string; sections: ArtifactSection[];
  markdown: string; card_ids: string[]; generated_at: string;
}

export interface Source {
  id: string; leaver: string; kind: SourceKind; title: string; text: string; replaced: number; created_at: string;
}
export interface Entity { id: string; leaver: string; name: string; type: string; source_ids: string[]; mentions: number }
export interface GenerateResponse { entities: Entity[]; queued: QueueItem[] }

export interface DrillScenario {
  id: string; leaver: string; title: string; situation: string; ask: string;
  expected_points: string[]; card_ids: string[]; created_at: string;
}
export interface DrillFeedback {
  drill_id: string; covered_points: string[]; missed_points: string[]; feedback: string; card_refs: string[];
}

export interface LeaverRecord { leaver: string; alias: string | null; created_at: string }
export interface InviteResponse { role: DemoRole; leaver: string; url: string; expires_at: string }

export interface DemoStatus {
  demo: boolean; leaver: string | null; role: DemoRole | null; expires_at: string | null;
  llm: Record<string, number> | null;
}
export interface DemoStart { leaver: string; role: DemoRole; expires_at: string }
