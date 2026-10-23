"""Anonymization helpers for leaver work sources."""

import re

from pydantic import BaseModel

EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
PHONE_RE = r"(?<!\d)0\d{1,2}-?\d{3,4}-?\d{4}(?!\d)"
HANDLE_RE = r"(?<![A-Za-z0-9.])@[a-z][a-z0-9-]{1,30}"
EMAIL_MASK = "[이메일]"
PHONE_MASK = "[전화번호]"

_EMAIL = re.compile(EMAIL_RE)
_PHONE = re.compile(PHONE_RE)
_HANDLE = re.compile(HANDLE_RE)


class AnonymizeResult(BaseModel):
    text: str
    replaced: int


def _mask_contacts(text: str) -> tuple[str, int]:
    text, emails = _EMAIL.subn(EMAIL_MASK, text)
    text, phones = _PHONE.subn(PHONE_MASK, text)
    return text, emails + phones


def anonymize(text: str, role_map: dict[str, str]) -> AnonymizeResult:
    out, replaced = _mask_contacts(text)
    for key in sorted(role_map, key=len, reverse=True):
        if not key:
            continue
        count = out.count(key)
        if count:
            out = out.replace(key, role_map[key])
            replaced += count
    return AnonymizeResult(text=out, replaced=replaced)


def scrub_contacts(text: str) -> str:
    return _mask_contacts(text)[0]


def find_pii(text: str) -> list[str]:
    found: list[str] = []
    for pattern in (_EMAIL, _PHONE, _HANDLE):
        found.extend(m.group(0) for m in pattern.finditer(text))
    return found
