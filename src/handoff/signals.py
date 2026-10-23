"""Pure cue-based signals for knowledge honesty (uncertainty, tacit cues, roles, expiry)."""

from __future__ import annotations

from handoff.contracts import ChangeEvent, InvalidationCondition, containment, split_sentences

UNCERTAIN_CUES: tuple[str, ...] = (
    "아마",
    "모르겠",
    "모릅니다",
    "전임자가",
    "확실하지 않",
    "확실치 않",
    "기억이 안",
)
TACIT_CUES: tuple[str, ...] = ("그냥", "감으로", "보통", "경험상", "느낌")
TACIT_TARGET_CUES: tuple[str, ...] = ("기준", "판단", "신호", "근거")
TACIT_FOLLOW_UP_TEMPLATE = (
    "'{cue}' 라고 하셨는데, 그렇게 판단하는 기준은 무엇인가요? 어떤 신호를 보고 정하나요?"
)
ROLE_SUFFIXES: tuple[str, ...] = ("팀 리드", "팀장", "리드", "담당자", "담당", "당번", "센터", "팀")
UNCERTAIN_OVERLAP = 0.3
UNCERTAIN_CONFIDENCE_MAX = 0.4
ROUTE_MIN_MATCH = 0.15
FENCE_MIN_MATCH = 0.3
FENCE_TOP_CARDS = 5
FENCE_POINTS_MAX = 3
EVENT_CUES: dict[ChangeEvent, tuple[str, ...]] = {
    "system_replaced": ("서버", "시스템", "배치", "스크립트", "화면", "데이터베이스", "테이블", "큐"),
    "vendor_changed": ("대행사", "외부", "업체", "벤더"),
    "team_reorganized": ("팀", "리드", "담당", "당번", "채널"),
    "policy_changed": ("규정", "정책", "승인", "권한", "보관", "금지"),
    "schedule_changed": ("매일", "매주", "매달", "월말", "새벽", "금요일", "월요일", "주기"),
}
EVENT_CONDITION_TEMPLATES: dict[ChangeEvent, str] = {
    "system_replaced": "관련 시스템·배치·화면이 교체되거나 이전되면 이 내용이 틀려질 수 있습니다.",
    "vendor_changed": "외부 업체나 대행사가 바뀌면 이 내용이 틀려질 수 있습니다.",
    "team_reorganized": "담당 팀·역할이 바뀌면 이 연락·책임 내용이 틀려질 수 있습니다.",
    "policy_changed": "승인·권한·보관 규정이 바뀌면 이 내용이 틀려질 수 있습니다.",
    "schedule_changed": "작업 일정이나 주기가 바뀌면 이 내용이 틀려질 수 있습니다.",
}

_ROLE_MAX_LEN = 20
_MAX_INVALIDATIONS = 3
# 역할 단어 뒤에 붙을 수 있는 조사 (빈 문자열 = 조사 없음)
_PARTICLES: tuple[str, ...] = (
    "",
    "이",
    "가",
    "은",
    "는",
    "을",
    "를",
    "의",
    "에",
    "에서",
    "에게",
    "에게서",
    "한테",
    "께",
    "와",
    "과",
    "로",
    "으로",
    "도",
    "만",
    "님",
    "님께",
    "님에게",
)
# 이 글자로 끝나는 앞 단어는 조사·어미가 붙은 것으로 보고 역할에 붙이지 않는다
_NON_NOUN_ENDINGS = frozenset("은는이가을를에의도만고며면서다요게니지와과로")
_TRAILING_PUNCT = ".,!?;:()[]\"'·…"


def find_cues(text: str, cues: tuple[str, ...]) -> list[str]:
    """Return cues (in ``cues`` order, no duplicates) that appear in ``text``."""
    found: list[str] = []
    for cue in cues:
        if cue in text and cue not in found:
            found.append(cue)
    return found


def uncertain_cues(text: str) -> list[str]:
    return find_cues(text, UNCERTAIN_CUES)


def tacit_cues(text: str) -> list[str]:
    return find_cues(text, TACIT_CUES)


def card_uncertain_cues(card_text: str, evidence_answers: list[str]) -> list[str]:
    """Uncertainty cues in the card text or in answer sentences that overlap the card."""
    hits = set(uncertain_cues(card_text))
    for answer in evidence_answers:
        for sentence in split_sentences(answer):
            cues = uncertain_cues(sentence)
            if cues and containment(sentence, card_text) >= UNCERTAIN_OVERLAP:
                hits.update(cues)
    return [cue for cue in UNCERTAIN_CUES if cue in hits]


def _role_core(word: str) -> str | None:
    """Return the longest prefix of ``word`` ending in a role suffix followed only by a particle."""
    best: str | None = None
    for suffix in ROLE_SUFFIXES:
        if " " in suffix:
            continue
        start = word.find(suffix)
        while start != -1:
            end = start + len(suffix)
            if word[end:] in _PARTICLES and (best is None or end > len(best)):
                best = word[:end]
            start = word.find(suffix, start + 1)
    return best


def _is_noun_word(word: str) -> bool:
    return bool(word) and word[-1] not in _NON_NOUN_ENDINGS


def extract_roles(text: str) -> list[str]:
    """Extract role phrases ending in ``ROLE_SUFFIXES`` (appearance order, no duplicates)."""
    words = [w.strip(_TRAILING_PUNCT) for w in text.split()]
    found: list[tuple[int, str]] = []  # (단어 위치, 역할)
    for i, word in enumerate(words):
        core = _role_core(word)
        if core is None:
            continue
        role = core
        if core in ROLE_SUFFIXES and i > 0 and _is_noun_word(words[i - 1]):
            role = f"{words[i - 1]} {core}"
            if found and found[-1][0] == i - 1:
                found.pop()
        if len(role) > _ROLE_MAX_LEN:
            if len(core) > _ROLE_MAX_LEN:
                continue
            role = core
        found.append((i, role))
    roles: list[str] = []
    for _, role in found:
        if role not in roles:
            roles.append(role)
    return roles


def fake_invalidation(card_text: str) -> list[InvalidationCondition]:
    """Cue-based invalidation conditions in ``CHANGE_EVENTS`` order, at most three."""
    conditions: list[InvalidationCondition] = []
    for event, cues in EVENT_CUES.items():
        if any(cue in card_text for cue in cues):
            conditions.append(
                InvalidationCondition(event=event, condition=EVENT_CONDITION_TEMPLATES[event])
            )
        if len(conditions) == _MAX_INVALIDATIONS:
            break
    return conditions
