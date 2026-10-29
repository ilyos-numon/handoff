"""Shared contracts for the LLM knowledge loop (models, constants, pure helpers)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import BaseModel, Field, StringConstraints

from handoff.kb import Citation, KBAnswer, KBEntry  # noqa: F401  (re-export)
from handoff.question_tree import Axis, FollowUp

if TYPE_CHECKING:
    from handoff.session import Question

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
LeaverId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,40}$")]

Slot = Literal["why", "when", "who", "how", "fallback"]
SLOTS: tuple[Slot, ...] = ("why", "when", "who", "how", "fallback")
SlotState = Literal["filled", "missing", "not_applicable"]
SourceKind = Literal["wiki", "runbook", "cron", "incident", "commit", "org"]
EntityType = Literal["system", "job", "incident", "role", "document", "external"]
CardType = Literal["procedure", "pitfall", "decision", "contact", "entity"]
QueueSource = Literal["successor", "gap", "premortem", "generated", "manager"]
QueueStatus = Literal["pending", "asked", "answered"]
GapKind = Literal["mentioned_only", "contradiction", "bus_factor"]
Risk = Literal["high", "medium", "low"]
ArtifactKind = Literal["runbook", "onboarding_30d", "pitfalls_top10", "adr", "premortem"]
Certainty = Literal["certain", "uncertain"]
ChangeEvent = Literal[
    "system_replaced", "vendor_changed", "team_reorganized", "policy_changed", "schedule_changed"
]
RouteBasis = Literal["contact_card", "people_answer"]

QUEUE_PRIORITY: dict[QueueSource, int] = {
    "manager": 0,
    "successor": 1,
    "gap": 2,
    "premortem": 3,
    "generated": 4,
}
ENTITY_TYPE_AXIS: dict[EntityType, Axis] = {
    "system": "systems",
    "document": "systems",
    "external": "people",
    "job": "routines",
    "incident": "incidents",
    "role": "people",
}
CARD_TYPE_AXIS: dict[CardType, Axis] = {
    "procedure": "routines",
    "pitfall": "incidents",
    "decision": "systems",
    "contact": "people",
    "entity": "systems",
}
ARTIFACT_CARD_TYPES: dict[ArtifactKind, tuple[CardType, ...]] = {
    "runbook": ("procedure", "pitfall"),
    "onboarding_30d": ("entity", "contact", "procedure", "pitfall", "decision"),
    "pitfalls_top10": ("pitfall",),
    "adr": ("decision",),
    "premortem": ("pitfall", "procedure"),
}
ARTIFACT_TITLES: dict[ArtifactKind, str] = {
    "runbook": "운영 런북",
    "onboarding_30d": "30일 온보딩 플랜",
    "pitfalls_top10": "함정 Top10",
    "adr": "결정 기록(ADR)",
    "premortem": "사전 부검: 먼저 터질 일 Top5",
}
FAKE_COMPOSE_PREFIX = "인수인계 기록에 따르면:"  # FakeLLM.compose_answer 출력 접두어와 같다
DEFAULT_ROLE = "backend_developer"
MANAGER_REASON = "팀장 우선 질문"

# 조직 변화 이벤트 (호출자가 지정, 자동 감지 없음)
CHANGE_EVENTS: tuple[ChangeEvent, ...] = (
    "system_replaced",
    "vendor_changed",
    "team_reorganized",
    "policy_changed",
    "schedule_changed",
)
CHANGE_EVENT_LABELS: dict[ChangeEvent, str] = {
    "system_replaced": "시스템 교체·이전",
    "vendor_changed": "외부 업체 변경",
    "team_reorganized": "조직·담당 변경",
    "policy_changed": "규정·권한 정책 변경",
    "schedule_changed": "일정·주기 변경",
}
# 사전 부검
PREMORTEM_QUESTIONS: tuple[str, str, str] = (
    "내가 떠나고 3개월 뒤 가장 먼저 터질 일은 무엇인가요? 왜 그 일이 먼저 터지나요?",
    "그 일이 터지기 전에 어떤 징후가 먼저 보이나요? 어디를 보면 알 수 있나요?",
    "그 일이 터지면 후임이 가장 먼저 해야 할 대응은 무엇인가요?",
)
PREMORTEM_REASON = "사전 부검 질문"
PREMORTEM_TOP = 5
# 체스터턴의 울타리
FENCE_REASON = "울타리 근거 없음"
FENCE_QUEUE_TEMPLATE = "'{subject}' 은 왜 지금 방식으로 되어 있나요? 바꾸거나 없애면 무엇이 깨지나요?"
# verify_claims 에 넘기는 질문
FENCE_QUESTION_TEMPLATE = "{subject} 은 왜 지금 방식으로 되어 있고, 바꾸거나 없애면 무엇이 위험한가요?"
NOT_RECORDED_SIGN = "징후가 기록되지 않았습니다. 퇴사자에게 확인하세요."
NOT_RECORDED_RESPONSE = "대응이 기록되지 않았습니다. 퇴사자에게 확인하세요."

# 슬롯 판정 fake 단서어 (filled = 답변에 하나라도 포함; fake 는 not_applicable 을 내지 않는다)
SLOT_CUES: dict[Slot, tuple[str, ...]] = {
    "why": ("때문", "이유", "왜냐", "위해서"),
    "when": (
        "언제",
        "매주",
        "매일",
        "매달",
        "매년",
        "월말",
        "새벽",
        "오전",
        "오후",
        "직후",
        "시점",
        "할 때",
        "경우",
    ),
    "who": ("리드", "담당", "팀", "역할", "에게"),
    "how": ("확인", "실행", "절차", "방법", "순서", "먼저", "그다음", "조회", "롤백", "재실행"),
    "fallback": ("안 되면", "실패하면", "대안", "급하면", "없을 때", "대신", "휴가"),
}
# 빈 슬롯 꼬리 질문 fake 템플릿 (qwen 도 해석 실패 시 이 문장을 쓴다)
SLOT_FOLLOW_UP_TEMPLATES: dict[Slot, str] = {
    "why": "그렇게 하는 이유는 무엇인가요? 왜 그 방식이 필요한가요?",
    "when": "그 일은 언제, 어떤 조건에서 하나요?",
    "who": "그 일에 관련된 역할은 누구인가요? 막히면 누구에게 묻나요?",
    "how": "구체적으로 어떤 방법과 순서로 하나요?",
    "fallback": "그 방법이 안 되면 대안은 무엇인가요?",
}
# 평가 slot_target 단서어 (꼬리 질문 문장에 하나라도 있으면 겨냥 성공)
SLOT_TARGET_CUES: dict[Slot, tuple[str, ...]] = {
    "why": ("이유", "왜"),
    "when": ("언제", "조건", "시점"),
    "who": ("누구", "역할", "담당"),
    "how": ("어떻게", "방법", "순서", "절차"),
    "fallback": ("안 되면", "대안", "실패"),
}

# 오류 detail 문자열
SESSION_NOT_FOUND = "session not found"
SESSION_OPEN = "session open"
NO_SOURCES = "no sources"
NO_CARDS = "no cards"
CARD_NOT_FOUND = "card not found"
DRILL_NOT_FOUND = "drill not found"
ARTIFACT_NOT_FOUND = "artifact not found"
QUESTION_NOT_FOUND = "question not found"
PROVIDER_UNAVAILABLE = "provider unavailable"


class KnowledgeNotFound(LookupError):  # 라우터: 404, detail=str(exc)
    ...


class KnowledgeConflict(RuntimeError):  # 라우터: 409, detail=str(exc)
    ...


class ChatFn(Protocol):  # QwenLLM._chat 와 같은 시그니처
    def __call__(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str: ...


# ---- 업무 자료·엔티티·큐
class SourceUploadRequest(BaseModel):
    kind: SourceKind
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)
    ]
    role_map: dict[str, str] = {}


class SourceDoc(BaseModel):
    id: str  # "src<int>"
    leaver: str
    kind: SourceKind
    title: str
    text: str
    replaced: int
    created_at: datetime


class EntityDraft(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    type: EntityType
    mention: str  # 근거 줄 발췌 ≤120자
    source_id: str  # 그 엔티티가 나온 SourceDoc.id


class Entity(BaseModel):
    id: str  # "e<int>"
    leaver: str
    name: str
    type: EntityType
    source_ids: list[str]
    mentions: int


class GeneratedQuestion(BaseModel):
    text: str
    axis: Axis
    entity_names: list[str]


class GenerateQuestionsRequest(BaseModel):
    role: str = DEFAULT_ROLE
    limit: int = Field(8, ge=1, le=20)


class QueueItem(BaseModel):
    id: str  # "q<int>"  (정규식 ^q\d+$)
    leaver: str
    role: str
    source: QueueSource
    axis: Axis
    text: str
    reason: str
    status: QueueStatus
    ref_id: str | None = None
    asked_session_id: str | None = None
    answered_session_id: str | None = None
    created_at: datetime


ThreadState = Literal["waiting", "asking", "answered_open", "answered"]


class ThreadTurn(BaseModel):
    question_id: str  # "q<n>" 또는 꼬리 "q<n>-s<k>"
    follow_up: bool  # question_id != item.id
    question: str  # answers.question, scrub_contacts 적용
    answer: str  # answers.answer, scrub_contacts 적용 (자르지 않음)
    session_number: int | None
    created_at: datetime


class QuestionThread(BaseModel):
    item: QueueItem
    state: ThreadState
    turns: list[ThreadTurn]  # 저장 순서(answers.id ASC)


class PriorityQuestionRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    axis: Axis | None = None
    role: str = DEFAULT_ROLE


class GenerateQuestionsResponse(BaseModel):
    entities: list[Entity]
    queued: list[QueueItem]  # queued = 이번 호출에서 새로 들어간 항목만


# ---- 깊이
class SlotJudgement(BaseModel):
    slots: dict[Slot, SlotState]  # 키 5개 모두
    missing: list[Slot]  # SLOTS 순서


class DepthJudgeRequest(BaseModel):
    question: NonBlank
    answer: NonBlank


class DepthJudgement(BaseModel):
    session_id: str
    question_id: str
    slots: dict[Slot, SlotState]
    missing: list[Slot]
    follow_up_id: str | None
    created_at: datetime


class SlotFollowUpResponse(BaseModel):
    slot: Slot | None  # 빈 슬롯 없으면 둘 다 None
    text: str | None


class SessionDepthReport(BaseModel):
    session_id: str
    judgements: list[DepthJudgement]
    filled: int
    applicable: int
    slot_fill_rate: float


# ---- 카드
class Evidence(BaseModel):
    session_id: str
    question_id: str
    session_number: int | None
    excerpt: str  # ≤200자


class CardDraft(BaseModel):
    type: CardType
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    entities: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_question_ids: list[str] = Field(min_length=1)


class InvalidationCondition(BaseModel):
    event: ChangeEvent
    condition: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class NewCard(BaseModel):
    leaver: str
    role: str
    session_id: str
    type: CardType
    title: str
    body: str
    entities: list[str]
    confidence: float
    evidence: list[Evidence] = Field(min_length=1)
    certainty: Certainty = "certain"
    uncertain_cues: list[str] = []
    invalidated_when: list[InvalidationCondition] = Field(default_factory=list, max_length=3)


class KnowledgeCard(NewCard):
    id: str  # "c<int>"
    created_at: datetime


class AtRiskCard(BaseModel):
    card: KnowledgeCard
    conditions: list[InvalidationCondition]  # 그 이벤트 조건만


class AtRiskReport(BaseModel):
    leaver: str
    event: ChangeEvent
    label: str
    target: str | None
    cards: list[AtRiskCard]


class ManagerSafeCard(BaseModel):
    """팀장용 카드: KnowledgeCard 에서 evidence·body·uncertain_cues 를 뺀 것."""

    id: str
    created_at: datetime
    leaver: str
    role: str
    session_id: str
    type: CardType
    title: str
    entities: list[str]
    confidence: float
    certainty: Certainty
    invalidated_when: list[InvalidationCondition]


class ManagerAtRiskCard(BaseModel):
    card: ManagerSafeCard
    conditions: list[InvalidationCondition]


class ManagerAtRiskReport(BaseModel):
    leaver: str
    event: ChangeEvent
    label: str
    target: str | None
    cards: list[ManagerAtRiskCard]


def to_manager_report(report: AtRiskReport) -> ManagerAtRiskReport:
    """Drop evidence, body and uncertain cues from every card of an at-risk report."""
    return ManagerAtRiskReport(
        leaver=report.leaver,
        event=report.event,
        label=report.label,
        target=report.target,
        cards=[
            ManagerAtRiskCard(
                card=ManagerSafeCard.model_validate(
                    r.card.model_dump(exclude={"evidence", "body", "uncertain_cues"})
                ),
                conditions=r.conditions,
            )
            for r in report.cards
        ],
    )


# ---- 빈 구멍
class ContradictionDraft(BaseModel):
    card_ids: list[str] = Field(min_length=2, max_length=2)
    subject: str
    explanation: str


class NewGap(BaseModel):
    leaver: str
    kind: GapKind
    topic: str
    detail: str
    risk: Risk
    card_ids: list[str] = []
    entity_ids: list[str] = []
    queue_item_id: str | None = None


class Gap(NewGap):
    id: str  # "g<int>"
    created_at: datetime


class UnknownArea(BaseModel):
    topic: str
    card_ids: list[str] = Field(min_length=1)
    cues: list[str]
    detail: str


class GapReport(BaseModel):
    leaver: str
    gaps: list[Gap]
    queued: list[QueueItem]
    unknown_areas: list[UnknownArea] = []


# ---- 검증·후임 루프
class ClaimVerdict(BaseModel):
    claim: str
    supported: bool
    answers_question: bool
    citation_index: int | None = None  # 1부터


class VerifyOutcome(BaseModel):
    answer: str | None
    claims: list[ClaimVerdict]
    kept: int
    removed: int


class AskRequest(BaseModel):
    question: NonBlank
    leaver: LeaverId


class RouteCandidate(BaseModel):
    ref: str  # 카드면 "c<int>", 답변이면 "a:<session_id>:<question_id>"
    basis: RouteBasis
    text: str
    roles: list[str] = Field(min_length=1)


class RouteDraft(BaseModel):
    ref: str
    role: str


class AskRoute(BaseModel):
    ask_role: str
    reason: str
    basis: RouteBasis
    card_ids: list[str]
    question_ids: list[str]


class AskResponse(BaseModel):
    answer: KBAnswer
    queued: QueueItem | None
    route: AskRoute | None = None
    certainty: Certainty | None = None


# ---- 암묵지 꼬리 질문
class TacitFollowUpResponse(BaseModel):
    cues: list[str]
    text: str | None  # cues 없으면 None


# ---- 체스터턴의 울타리
class FenceRequest(BaseModel):
    leaver: LeaverId
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    role: str = DEFAULT_ROLE


class FencePoint(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    card_ids: list[str] = Field(min_length=1)


class FenceDraft(BaseModel):
    reasons: list[FencePoint] = []
    risks: list[FencePoint] = []


class FenceResponse(BaseModel):
    subject: str
    found: bool
    reasons: list[FencePoint]
    risks: list[FencePoint]
    cards: list[KnowledgeCard]
    invalidated_when: list[InvalidationCondition]
    certainty: Certainty | None
    claims_removed: int
    queued: QueueItem | None


# ---- 산출물
class ArtifactSection(BaseModel):
    heading: str
    body: str
    card_ids: list[str] = Field(min_length=1)


class NewArtifact(BaseModel):
    leaver: str
    kind: ArtifactKind
    title: str
    sections: list[ArtifactSection]
    markdown: str
    card_ids: list[str]


class Artifact(NewArtifact):
    id: str  # "a<int>"
    generated_at: datetime


# ---- 훈련 (점수·등급 필드 금지)
class DrillCreateRequest(BaseModel):
    seed_card_id: str | None = None


class DrillDraft(BaseModel):
    title: str
    situation: str
    ask: str
    expected_points: list[str] = Field(min_length=1, max_length=5)
    card_ids: list[str] = Field(min_length=1)


class NewDrill(DrillDraft):
    leaver: str


class DrillScenario(NewDrill):
    id: str  # "d<int>"
    created_at: datetime


class DrillAnswerRequest(BaseModel):
    text: NonBlank


class DrillFeedbackDraft(BaseModel):
    covered_points: list[str]
    missed_points: list[str]
    feedback: str


class DrillFeedback(DrillFeedbackDraft):
    drill_id: str
    card_refs: list[str]


# ---- 세션 훅 (S3 가 호출, S11 이 구현)
class SessionHooks(Protocol):
    def next_queued(self, session_id: str, leaver: str, role: str) -> QueueItem | None: ...

    def after_answer(
        self, session_id: str, answered: Question, text: str, candidates: list[FollowUp]
    ) -> Question | None: ...

    def on_end(self, session_id: str, leaver: str, role: str, answers: list[KBEntry]) -> None: ...


# ---- 공용 순수 함수
TOPIC_RE = r"^(q\d+|[a-z]{3}-\d{2})"
_QUEUE_ID_RE = re.compile(r"q\d+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")
_BIGRAM_KEEP_RE = re.compile(r"[^가-힣A-Za-z0-9]")


def topic_id(question_id: str) -> str:
    """Return the topic part (``q12`` or ``sys-01``) of a question id, or the id itself."""
    m = re.match(TOPIC_RE, question_id)
    return m.group(1) if m else question_id


def is_queue_id(question_id: str) -> bool:
    return _QUEUE_ID_RE.fullmatch(topic_id(question_id)) is not None


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s and s.strip()]


def char_bigrams(text: str) -> set[str]:
    kept = _BIGRAM_KEEP_RE.sub("", text).lower()
    return {kept[i : i + 2] for i in range(len(kept) - 1)}


def containment(part: str, whole: str) -> float:
    part_grams = char_bigrams(part)
    if not part_grams:
        return 0.0
    return len(part_grams & char_bigrams(whole)) / len(part_grams)


def judge_slots_by_cues(answer: str) -> SlotJudgement:
    slots: dict[Slot, SlotState] = {
        slot: "filled" if any(cue in answer for cue in SLOT_CUES[slot]) else "missing"
        for slot in SLOTS
    }
    return SlotJudgement(slots=slots, missing=[s for s in SLOTS if slots[s] == "missing"])
