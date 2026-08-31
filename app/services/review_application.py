# -*- coding: utf-8 -*-
"""Behavior-preserving application core for review mutations (Round 8B-P3A).

This module owns the review mutation TRANSACTION only. It is deliberately
HTTP-neutral: no flask/request/session/jsonify/redirect/url_for/Blueprint
imports, no HTTP status literals, and results are presentation-neutral
dataclasses. Route adapters resolve all input policy (field resolution,
comment defaults, authorization, envelope parsing) and hand over an explicit
ReviewMutationPlan; the executor never guesses semantics.

Round 8B-P3A scope (REVIEW_MUTATION_CANONICAL_POLICY_V1 is the long-term
target, NOT implemented here):

    - only Route A (submit_review) adopts this core;
    - Route B (submit_form_review) production code is frozen byte-for-byte
      and keeps its own inline mutation until Round 8B-P3B;
    - no policy contract change is implemented in this round.

VersionAction / ScoreAction are explicit so the core contains no hidden
policy judgment:

    VersionAction.APPEND_VERSION  create a new physical LectureForm version
    VersionAction.UPDATE_LATEST   mutate the latest physical row in place
                                  (legacy A compatibility branch; currently
                                  unreachable through the normal
                                  permission/status flow but must stay
                                  expressible until its own retirement round)

    ScoreAction.REPLACE           insert a replacement ScoreRecord plus the
                                  plan's validated items on the target row
    ScoreAction.CLEAR             no new record; any existing record is only
                                  removed when the plan says so via
                                  delete_existing_score_record
    ScoreAction.NONE              no score work at all

Route A legacy mapping preserved verbatim (see submit_review):

    UPDATE_LATEST  -> delete_existing_score_record=True  (the old record is
                      marked deleted BEFORE the shared flush, so DELETE is
                      emitted before any replacement INSERT — the same
                      ordering proven correct for UNIQUE(form_id) in P1)
    APPEND_VERSION -> delete_existing_score_record=False (previous physical
                      versions keep their historical ScoreRecords)
"""
import enum
from dataclasses import dataclass
from typing import Optional, Tuple

from app.models import LectureForm, ScoreRecord, ScoreItem, db
from app.services.review_mutation import append_review_modification_note
from app.utils.review_drafts import delete_review_form_draft


class VersionAction(enum.Enum):
    APPEND_VERSION = 'append_version'
    UPDATE_LATEST = 'update_latest'


class ScoreAction(enum.Enum):
    REPLACE = 'replace'
    CLEAR = 'clear'
    NONE = 'none'


@dataclass(frozen=True)
class ReviewMutationPlan:
    """Fully resolved mutation intent; adapters own every policy decision."""

    actor_id: int
    original_form_id: int
    original_form: LectureForm
    latest_form: LectureForm
    logical_id: int
    target_status: str
    registration_id: Optional[int]
    resolved_fields: dict
    resolved_review_comment: str
    audit_note_base: str
    modified_fields: Tuple[str, ...]
    version_action: VersionAction
    score_action: ScoreAction
    delete_existing_score_record: bool
    score_items_to_persist: Tuple[dict, ...]


@dataclass(frozen=True)
class ReviewMutationResult:
    """Presentation-neutral outcome; adapters build their own HTTP payload."""

    target_form_id: int
    new_status: str
    modified_fields: Tuple[str, ...]


def execute_review_mutation(plan):
    """Run one review mutation as exactly one final commit.

    Any exception rolls the transaction back and re-raises so the calling
    adapter keeps its existing error contract.
    """
    try:
        target_form = _apply_version_mutation(plan)
        db.session.flush()  # 统一 flush：新表单ID可见，且先于任何 replacement INSERT 发出 DELETE
        _apply_score_mutation(plan, target_form)
        _apply_audit_note(plan, target_form)
        delete_review_form_draft(plan.actor_id, plan.original_form_id)
        db.session.commit()
        return ReviewMutationResult(
            target_form_id=target_form.id,
            new_status=plan.target_status,
            modified_fields=plan.modified_fields,
        )
    except Exception:
        db.session.rollback()
        raise


def _apply_version_mutation(plan):
    if plan.version_action is VersionAction.UPDATE_LATEST:
        target_form = plan.latest_form
        for field_name, value in plan.resolved_fields.items():
            if field_name != 'unique_id' and hasattr(target_form, field_name):
                setattr(target_form, field_name, value)
        if plan.delete_existing_score_record:
            old_score_record = ScoreRecord.query.filter_by(form_id=target_form.id).first()
            if old_score_record:
                db.session.delete(old_score_record)
        return target_form
    target_form = LectureForm(**plan.resolved_fields)
    target_form.created_at = plan.original_form.created_at  # 继承创建时间
    db.session.add(target_form)
    return target_form


def _apply_score_mutation(plan, target_form):
    if plan.score_action is ScoreAction.NONE or plan.score_action is ScoreAction.CLEAR:
        return
    total_dept_score = sum(item['department_score'] for item in plan.score_items_to_persist)
    total_personal_score = sum(item['personal_score'] for item in plan.score_items_to_persist)
    score_record = ScoreRecord(
        form_id=target_form.id,
        reviewer_id=plan.actor_id,
        total_department_score=total_dept_score,
        total_personal_score=total_personal_score,
    )
    db.session.add(score_record)
    db.session.flush()  # 获取评分记录ID
    for item in plan.score_items_to_persist:
        db.session.add(ScoreItem(
            score_record_id=score_record.id,
            reason=item['reason'],
            department_score=item['department_score'],
            personal_score=item['personal_score'],
            is_auto_generated=item['is_auto_generated'],
        ))


def _apply_audit_note(plan, target_form):
    """Apply the shared modification-note primitive to already-resolved inputs.

    The note base is computed by the adapter (its comment-default policy);
    the core never re-compares HTTP payloads.
    """
    if not plan.modified_fields:
        return
    target_form.review_comment = append_review_modification_note(
        plan.audit_note_base,
        plan.modified_fields,
    )
