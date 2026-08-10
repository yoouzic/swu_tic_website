"""Strict Pydantic contracts for the final DeepSeek JSON object."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra='forbid')

    code: str
    severity: Literal['review', 'high']
    message: str
    evidence: str


class DeepSeekReviewResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    compliance: Literal['compliant', 'needs_review', 'high_risk', 'unknown']
    summary: str
    findings: list[ReviewFinding]
    suggested_comment: str


__all__ = ['DeepSeekReviewResponse', 'ReviewFinding']
