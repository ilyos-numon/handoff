"""Claim-level answer verification (implemented in stage S8)."""

from __future__ import annotations

from handoff.contracts import ChatFn, Citation, ClaimVerdict


def fake_verify_claims(
    question: str, claims: list[str], citations: list[Citation]
) -> list[ClaimVerdict]:
    raise NotImplementedError("verify: implemented in stage S8")


def qwen_verify_claims(
    chat: ChatFn, question: str, claims: list[str], citations: list[Citation]
) -> list[ClaimVerdict]:
    raise NotImplementedError("verify: implemented in stage S8")
