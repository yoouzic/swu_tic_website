"""SQLAlchemy models for additive automated-review data only."""

import uuid
from datetime import datetime

from app.models import db

from .contracts import (
    BatchStatus,
    DatasetStatus,
    ReviewCategory,
    ScheduleCoverage,
)


def _uuid_string():
    return str(uuid.uuid4())


def _now():
    return datetime.now()


class ScheduleDataset(db.Model):
    __tablename__ = 'automation_schedule_datasets'
    __table_args__ = (
        db.UniqueConstraint(
            'kind', 'semester', 'sha256',
            name='uq_automation_schedule_dataset_identity',
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid_string)
    kind = db.Column(db.String(32), nullable=False)
    semester = db.Column(db.String(64), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(16), nullable=False, default=DatasetStatus.STAGED.value)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    error_count = db.Column(db.Integer, nullable=False, default=0)
    summary_json = db.Column(db.Text, nullable=False, default='{}')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)


class SchoolScheduleEntry(db.Model):
    __tablename__ = 'automation_school_schedule_entries'
    __table_args__ = (
        db.Index(
            'ix_automation_school_schedule_time',
            'dataset_id', 'weekday', 'start_period', 'end_period',
        ),
        db.Index(
            'ix_automation_school_schedule_teacher',
            'dataset_id', 'teacher_name',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(
        db.String(36), db.ForeignKey('automation_schedule_datasets.id'), nullable=False
    )
    teacher_name = db.Column(db.String(100), nullable=False)
    teacher_college = db.Column(db.String(100), nullable=True)
    course_title = db.Column(db.String(200), nullable=False)
    teaching_class = db.Column(db.Text, nullable=True)
    major = db.Column(db.Text, nullable=True)
    weeks_json = db.Column(db.Text, nullable=False, default='[]')
    weekday = db.Column(db.Integer, nullable=False)
    start_period = db.Column(db.Integer, nullable=False)
    end_period = db.Column(db.Integer, nullable=False)
    location = db.Column(db.String(200), nullable=True)
    source_row = db.Column(db.Integer, nullable=True)

    dataset = db.relationship('ScheduleDataset', backref='school_entries')


class ListenerClassMapping(db.Model):
    __tablename__ = 'automation_listener_class_mappings'

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(
        db.String(36), db.ForeignKey('automation_schedule_datasets.id'), nullable=False
    )
    listener_number = db.Column(db.String(64), nullable=True)
    student_id = db.Column(db.String(64), nullable=True)
    admin_class = db.Column(db.String(160), nullable=False)
    match_status = db.Column(db.String(32), nullable=False, default='matched')
    source_row = db.Column(db.Integer, nullable=True)

    dataset = db.relationship('ScheduleDataset', backref='listener_mappings')

    def validate_identity(self):
        if not (self.listener_number or self.student_id):
            raise ValueError('listener_number or student_id is required')
        return self


class PersonalScheduleSlot(db.Model):
    __tablename__ = 'automation_personal_schedule_slots'

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(
        db.String(36), db.ForeignKey('automation_schedule_datasets.id'), nullable=False
    )
    listener_number = db.Column(db.String(64), nullable=True)
    student_id = db.Column(db.String(64), nullable=True)
    course_title = db.Column(db.String(200), nullable=False)
    weeks_json = db.Column(db.Text, nullable=False, default='[]')
    weekday = db.Column(db.Integer, nullable=False)
    start_period = db.Column(db.Integer, nullable=False)
    end_period = db.Column(db.Integer, nullable=False)
    source_row = db.Column(db.Integer, nullable=True)

    dataset = db.relationship('ScheduleDataset', backref='personal_slots')


class ScheduleImportIssue(db.Model):
    __tablename__ = 'automation_schedule_import_issues'

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(
        db.String(36), db.ForeignKey('automation_schedule_datasets.id'), nullable=False
    )
    row_number = db.Column(db.Integer, nullable=False)
    code = db.Column(db.String(64), nullable=False)
    message = db.Column(db.Text, nullable=False)
    value_excerpt = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    dataset = db.relationship('ScheduleDataset', backref='import_issues')


class ReviewRuleRevision(db.Model):
    __tablename__ = 'automation_rule_revisions'
    __table_args__ = (
        db.UniqueConstraint(
            'rule_key', 'version',
            name='uq_automation_rule_revision_key_version',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    rule_key = db.Column(db.String(100), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    handler = db.Column(db.String(100), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    severity = db.Column(db.String(16), nullable=False, default='review')
    parameters_json = db.Column(db.Text, nullable=False, default='{}')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    change_reason = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)


AutomationRuleRevision = ReviewRuleRevision


class ReviewBatch(db.Model):
    __tablename__ = 'automation_review_batches'

    id = db.Column(db.String(36), primary_key=True, default=_uuid_string)
    celery_root_id = db.Column(db.String(255), nullable=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    status = db.Column(db.String(32), nullable=False, default=BatchStatus.QUEUED.value)
    target_form_count = db.Column(db.Integer, nullable=False, default=0)
    clear_count = db.Column(db.Integer, nullable=False, default=0)
    review_count = db.Column(db.Integer, nullable=False, default=0)
    high_risk_count = db.Column(db.Integer, nullable=False, default=0)
    unknown_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    cache_count = db.Column(db.Integer, nullable=False, default=0)
    snapshot_json = db.Column(db.Text, nullable=False, default='{}')
    config_snapshot_json = db.Column(db.Text, nullable=False, default='{}')
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    assessments = db.relationship('ReviewAssessment', back_populates='batch')


class ReviewAssessment(db.Model):
    __tablename__ = 'automation_review_assessments'

    id = db.Column(db.String(36), primary_key=True, default=_uuid_string)
    form_id = db.Column(db.Integer, db.ForeignKey('lecture_forms.id'), nullable=False)
    form_version = db.Column(db.String(64), nullable=True)
    batch_id = db.Column(
        db.String(36), db.ForeignKey('automation_review_batches.id'), nullable=True
    )
    classification = db.Column(
        db.String(32), nullable=False, default=ReviewCategory.UNKNOWN.value
    )
    coverage = db.Column(
        db.String(16), nullable=False, default=ScheduleCoverage.NONE.value
    )
    fingerprint = db.Column(db.String(64), nullable=False, unique=True)
    dependency_ids_json = db.Column(db.Text, nullable=False, default='{}')
    dependency_versions_json = db.Column(db.Text, nullable=False, default='{}')
    model_id = db.Column(db.String(100), nullable=True)
    prompt_version = db.Column(db.String(100), nullable=True)
    schedule_version = db.Column(db.String(100), nullable=True)
    rule_version = db.Column(db.String(100), nullable=True)
    validated_model_json = db.Column(db.Text, nullable=True)
    suggested_comment = db.Column(db.Text, nullable=True)
    error_code = db.Column(db.String(64), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    form = db.relationship('LectureForm', backref='automation_assessments')
    batch = db.relationship('ReviewBatch', back_populates='assessments')
    findings = db.relationship(
        'ReviewFinding', back_populates='assessment', cascade='all, delete-orphan'
    )


class ReviewFinding(db.Model):
    __tablename__ = 'automation_review_findings'

    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(
        db.String(36), db.ForeignKey('automation_review_assessments.id'), nullable=False
    )
    source = db.Column(db.String(16), nullable=False)
    rule_key = db.Column(db.String(100), nullable=True)
    severity = db.Column(db.String(16), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    objective = db.Column(db.Boolean, nullable=False, default=False)
    evidence_strength = db.Column(db.String(16), nullable=False, default='weak')
    raw_value_excerpt = db.Column(db.String(255), nullable=True)
    comparison_value_excerpt = db.Column(db.String(255), nullable=True)
    evidence_json = db.Column(db.Text, nullable=False, default='{}')
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    assessment = db.relationship('ReviewAssessment', back_populates='findings')


class AutomationAuditLog(db.Model):
    __tablename__ = 'automation_audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    target_type = db.Column(db.String(100), nullable=True)
    target_id = db.Column(db.String(100), nullable=True)
    details_json = db.Column(db.Text, nullable=False, default='{}')
    created_at = db.Column(db.DateTime, nullable=False, default=_now)


__all__ = [
    'AutomationAuditLog',
    'AutomationRuleRevision',
    'ListenerClassMapping',
    'PersonalScheduleSlot',
    'ReviewAssessment',
    'ReviewBatch',
    'ReviewFinding',
    'ReviewRuleRevision',
    'ScheduleDataset',
    'ScheduleImportIssue',
    'SchoolScheduleEntry',
]
