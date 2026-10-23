"""Claim-level answer verification (fake and qwen)."""

from __future__ import annotations

import json
import re

from handoff.contracts import (
    FAKE_COMPOSE_PREFIX,
    ChatFn,
    Citation,
    ClaimVerdict,
    containment,
)
from handoff.question_tree import AXIS_LABELS

SUPPORT_MIN = 0.6
VERIFY_MAX_TOKENS = 600
QUESTION_STOPWORDS = frozenset(
    {
        "어떻게",
        "무엇",
        "무엇인가요",
        "누구",
        "누구한테",
        "언제",
        "어디",
        "어디서",
        "왜",
        "하나요",
        "해요",
        "있나요",
        "되나요",
        "인가요",
        "뭐예요",
        "뭐부터",
    }
)
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")
# "근거에 없다" 류 고지 문장: 정보를 주지 않으므로 qwen 판정과 무관하게 지원·답변 아님
_DISCLAIMER_RE = re.compile(
    r"(명시|언급|기재|제시)\s*(되어\s*있지|돼\s*있지|되어\s*있진|되지)\s*않"
    r"|(적혀|나와)\s*있지\s*않"
    r"|(근거|자료|기록|인수인계)\s*(에는|에서는|에서|에|상)\s*(관련\s*)?"
    r"(내용|정보|절차|방법|언급)?\s*(이|은|는|가)?\s*없"
)

VERIFY_SYSTEM = (
    "당신은 인수인계 기록 기반 답변을 주장(문장) 단위로 검증하는 검사기입니다. "
    "질문, 번호가 붙은 근거 발췌, 번호가 붙은 주장을 읽고 주장마다 두 가지를 판정합니다.\n"
    "1. supported: 그 주장의 내용이 어떤 근거 발췌에 직접 적혀 있으면 true. "
    "근거에 없는 날짜·숫자·사람·조직·절차·연락처가 하나라도 들어 있으면 false. "
    "근거에 없는 사실·이름을 지어내지 않습니다.\n"
    "2. answers_question: 그 주장이 질문이 묻는 바로 그 대상과 그 종류의 정보"
    "(절차·연락처·담당 역할·검증 방법·숫자·이유 등)를 직접 말하면 true. "
    "관련 주제(같은 시스템·같은 장애)를 다룰 뿐 질문이 묻는 정보를 직접 말하지 않으면 false. "
    "예: 질문이 '재고 실사 수량이 어긋나면 어떻게 맞추나요?' 인데 주장이 어긋났던 사례나 "
    "원인만 말하고 맞추는 절차를 적지 않으면 false. 질문이 '프린터가 고장 나면 누구에게 "
    "연락해요?' 인데 주장이 고장 증상만 말하고 연락할 역할·경로를 적지 않으면 false.\n"
    "3. 질문이 묻는 정보가 근거에 없다고만 말하는 주장('~는 명시되어 있지 않습니다', "
    "'~는 근거에 나와 있지 않습니다')은 정보를 주지 않으므로 supported=false, "
    "answers_question=false, citation=null 입니다.\n"
    "4. 근거가 일반적인 상황만 말하는데 주장이 질문의 특정 상황·조건을 붙여 옮겨 적으면 "
    "supported=false, answers_question=false 입니다. 예: 근거는 '모르는 게 있으면 창고 "
    "관리자에게 묻습니다' 인데 질문이 '냉동 창고 온도 경보가 울리면 누구에게 알려요?' 이고 "
    "주장이 '온도 경보 시 창고 관리자에게 알립니다' 이면 근거가 온도 경보를 말하지 않으므로 "
    "둘 다 false. 같은 근거라도 질문이 '모르는 게 있으면 누구에게 묻나요?' 이면 근거가 "
    "그 상황을 직접 말하므로 true.\n"
    "5. 먼저 질문이 묻는 구체 상황(대상과 조건)을 situation 에 한 줄로 적습니다. "
    "예: '냉동 창고 온도 경보가 울렸을 때 알릴 대상'. 질문에 특별한 조건이 없으면 대상만 "
    "적습니다. 그다음 주장마다 situation_covered 를 판정합니다: 인용한 근거 발췌가 그 상황"
    "(같은 대상·같은 조건)을 직접 다루면 true, 다른 상황이나 더 일반적인 상황"
    "(예: '모르는 게 있으면')만 다루면 false. 표현이 달라도 같은 대상·같은 조건을 다루면 "
    "true 입니다. situation_covered 는 answers_question 과 따로 판정합니다.\n"
    "citation 은 그 주장을 뒷받침하는 근거 번호(1부터), 없으면 null 입니다. "
    "citation 이 null 이면 answers_question 은 false 입니다. "
    "사람 이름 대신 역할명을 씁니다. "
    '반드시 {"situation": "<질문의 구체 상황 한 줄>", "verdicts": [{"index": <주장 번호 1부터>, '
    '"supported": true|false, "answers_question": true|false, '
    '"situation_covered": true|false, "citation": <근거 번호 또는 null>}]} '
    "형식의 JSON 만 출력하고, 주장마다 정확히 한 항목을 주장 번호 순서로 씁니다."
)


def _strip_prefix(claim: str) -> str:
    return claim.removeprefix(FAKE_COMPOSE_PREFIX).strip()


def _question_keys(question: str) -> list[str]:
    return [t[:2] for t in _TOKEN_RE.findall(question) if t not in QUESTION_STOPWORDS]


def fake_verify_claims(
    question: str, claims: list[str], citations: list[Citation]
) -> list[ClaimVerdict]:
    keys = _question_keys(question)
    verdicts: list[ClaimVerdict] = []
    for claim in claims:
        body = _strip_prefix(claim)
        citation_index: int | None = None
        for i, c in enumerate(citations, start=1):
            if containment(body, c.excerpt) >= SUPPORT_MIN:
                citation_index = i
                break
        verdicts.append(
            ClaimVerdict(
                claim=claim,
                supported=citation_index is not None,
                answers_question=any(k in body for k in keys),
                citation_index=citation_index,
            )
        )
    return verdicts


def _all_unsupported(claims: list[str]) -> list[ClaimVerdict]:
    return [ClaimVerdict(claim=c, supported=False, answers_question=False) for c in claims]


def _parse_verdicts(
    content: str, claims: list[str], citations: list[Citation]
) -> list[ClaimVerdict] | None:
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    situation = parsed.get("situation")
    if situation is not None and not isinstance(situation, str):
        return None
    items = parsed.get("verdicts")
    if not isinstance(items, list) or len(items) != len(claims):
        return None
    verdicts: list[ClaimVerdict] = []
    for claim, item in zip(claims, items):
        if not isinstance(item, dict):
            return None
        supported = item.get("supported")
        answers = item.get("answers_question")
        if not isinstance(supported, bool) or not isinstance(answers, bool):
            return None
        # 없으면 true 로 본다(구 형식 호환). 있으면 bool 이어야 한다.
        covered = item.get("situation_covered", True)
        if not isinstance(covered, bool):
            return None
        answers = answers and covered
        cite = item.get("citation")
        citation_index = (
            cite
            if isinstance(cite, int) and not isinstance(cite, bool) and 1 <= cite <= len(citations)
            else None
        )
        if _DISCLAIMER_RE.search(claim):
            supported, answers, citation_index = False, False, None
        verdicts.append(
            ClaimVerdict(
                claim=claim,
                supported=supported,
                answers_question=answers and citation_index is not None,
                citation_index=citation_index,
            )
        )
    return verdicts


def qwen_verify_claims(
    chat: ChatFn, question: str, claims: list[str], citations: list[Citation]
) -> list[ClaimVerdict]:
    if not claims:
        return []
    evidence = "\n".join(
        f"[근거 {i}] ({AXIS_LABELS[c.axis]}) {c.excerpt}" for i, c in enumerate(citations, start=1)
    )
    numbered = "\n".join(f"[주장 {i}] {c}" for i, c in enumerate(claims, start=1))
    user = f"질문: {question}\n근거:\n{evidence}\n주장:\n{numbered}"
    content = chat(VERIFY_SYSTEM, user, VERIFY_MAX_TOKENS, json_mode=True)
    parsed = _parse_verdicts(content, claims, citations)
    return parsed if parsed is not None else _all_unsupported(claims)
