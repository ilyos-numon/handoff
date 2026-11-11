"""Entity extraction and tailored question generation (stage S4)."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from handoff.contracts import (
    ENTITY_TYPE_AXIS,
    ChatFn,
    Entity,
    EntityDraft,
    EntityType,
    GeneratedQuestion,
    SourceDoc,
    SourceKind,
)
from handoff.question_tree import AXIS_LABELS

MENTION_MAX = 120
NAME_MAX = 60
QUESTION_MAX = 200
DOC_TEXT_MAX = 4000

_BRACKET = re.compile(r"\[\[([^\[\]]+)\]\]")
_BRACKET_TYPE: dict[SourceKind, EntityType] = {
    "wiki": "system", "runbook": "system", "commit": "system",
    "incident": "incident", "cron": "job", "org": "role",
}
_QUESTION_TEMPLATES: dict[EntityType, str] = {
    "system": "{n} 에서 가장 조심해야 하는 변경은 무엇이고, 왜 그런가요?",
    "job": "{n} 은 언제 돌고, 실패하면 어떻게 확인하고 복구하나요?",
    "incident": "'{n}' 때 무엇이 원인이었고, 다시 나면 무엇부터 하나요?",
    "role": "{n} 에게는 어떤 일로, 어떤 경로로 연락하나요?",
    "document": "{n} 은 언제 보고, 어디가 실제와 다른가요?",
    "external": "{n} 쪽 문제가 생기면 어떤 역할에게 어떤 순서로 연락하나요?",
}

_COMMON_RULES = (
    "근거에 없는 사실·이름을 지어내지 않는다. 사람 이름 대신 역할명을 쓴다. "
    "설명 없이 JSON 객체만 출력한다."
)
EXTRACT_SYSTEM = (
    "너는 퇴사자의 업무 자료에서 인수인계에 필요한 엔티티를 뽑는 도우미다. "
    "엔티티 타입은 system(시스템·서비스·스크립트), job(정기 작업·배치), incident(장애), "
    "role(역할), document(문서), external(외부 업체·기관) 중 하나다. "
    "자료에 실제로 적힌 이름만 그대로 쓴다. " + _COMMON_RULES + " "
    '형식: {"entities": [{"name": "이름", "type": "system", '
    '"mention": "근거 줄 발췌(120자 이내)", "source_id": "src1"}]}'
)
QUESTIONS_SYSTEM = (
    "너는 퇴사자 인터뷰에서 물을 맞춤 질문을 만드는 도우미다. 엔티티 목록만 보고, "
    "각 질문은 엔티티 하나 이상을 겨냥해 퇴사자만 아는 운영 지식(이유·시점·담당 역할·절차·대안)을 묻는다. "
    "axis 는 systems(담당 시스템), routines(반복 업무), incidents(사고 이력), people(사람·연락) 중 하나다. "
    + _COMMON_RULES + " "
    '형식: {"questions": [{"text": "질문 문장", "axis": "systems", "entity_names": ["이름"]}]}'
)


def _clip_line(line: str) -> str:
    return line.strip()[:MENTION_MAX]


def _doc_entities(doc: SourceDoc) -> list[EntityDraft]:
    seen: set[str] = set()
    out: list[EntityDraft] = []

    def add(name: str, type_: EntityType, line: str) -> None:
        name = name.strip()[:NAME_MAX].strip()
        if not name or name in seen:
            return
        seen.add(name)
        out.append(EntityDraft(name=name, type=type_, mention=_clip_line(line), source_id=doc.id))

    for raw in doc.text.splitlines():
        line = raw.strip()
        if not line:
            continue
        for m in _BRACKET.finditer(line):
            add(m.group(1), _BRACKET_TYPE[doc.kind], line)
        if doc.kind == "cron" and not line.startswith("#"):
            parts = line.split(maxsplit=5)
            if len(parts) == 6:
                add(parts[5], "job", line)
        if doc.kind == "org" and ":" in line:
            add(line.split(":", 1)[0], "role", line)
    return out


def fake_extract_entities(docs: list[SourceDoc]) -> list[EntityDraft]:
    out: list[EntityDraft] = []
    for doc in docs:
        out.extend(_doc_entities(doc))
    return out


def _load_list(content: str, key: str) -> list[Any] | None:
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    items = parsed.get(key)
    return items if isinstance(items, list) else None


def qwen_extract_entities(chat: ChatFn, docs: list[SourceDoc]) -> list[EntityDraft]:
    user = "\n\n".join(
        f"[{d.id}] 종류={d.kind} 제목={d.title}\n{d.text[:DOC_TEXT_MAX]}" for d in docs
    )
    content = chat(EXTRACT_SYSTEM, user, 2000, json_mode=True)
    items = _load_list(content, "entities")
    if items is None:
        return fake_extract_entities(docs)
    doc_ids = {d.id for d in docs}
    seen: set[tuple[str, str]] = set()
    out: list[EntityDraft] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            draft = EntityDraft(
                name=str(item.get("name", "")),
                type=item.get("type"),  # type: ignore[arg-type]
                mention=_clip_line(str(item.get("mention", ""))),
                source_id=str(item.get("source_id", "")),
            )
        except ValidationError:
            continue
        key = (draft.name, draft.source_id)
        if draft.source_id not in doc_ids or key in seen:
            continue
        seen.add(key)
        out.append(draft)
    return out or fake_extract_entities(docs)


def fake_generate_questions(entities: list[Entity], limit: int) -> list[GeneratedQuestion]:
    ranked = sorted(entities, key=lambda e: (-e.mentions, e.name))[:limit]
    return [
        GeneratedQuestion(
            text=_QUESTION_TEMPLATES[e.type].format(n=e.name),
            axis=ENTITY_TYPE_AXIS[e.type],
            entity_names=[e.name],
        )
        for e in ranked
    ]


def qwen_generate_questions(
    chat: ChatFn, entities: list[Entity], limit: int
) -> list[GeneratedQuestion]:
    lines = "\n".join(f"- {e.name} ({e.type}, 언급 {e.mentions}회)" for e in entities)
    user = f"엔티티 목록:\n{lines}\n질문은 최대 {limit}개."
    content = chat(QUESTIONS_SYSTEM, user, 1500, json_mode=True)
    items = _load_list(content, "questions")
    if items is None:
        return fake_generate_questions(entities, limit)
    names = {e.name for e in entities}
    out: list[GeneratedQuestion] = []
    seen: set[str] = set()
    for item in items:
        if len(out) >= limit:
            break
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        axis = item.get("axis")
        refs = item.get("entity_names")
        if not isinstance(text, str) or not isinstance(refs, list) or axis not in AXIS_LABELS:
            continue
        text = text.strip()
        picked = [r for r in refs if isinstance(r, str) and r in names]
        if not text or len(text) > QUESTION_MAX or text in seen or not picked:
            continue
        seen.add(text)
        out.append(GeneratedQuestion(text=text, axis=axis, entity_names=picked))
    return out or fake_generate_questions(entities, limit)
