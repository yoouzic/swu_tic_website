import json
import unittest
from datetime import datetime

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.app import app
from app.models import db
from app.review_automation.contracts import (
    EvidenceStrength,
    FindingSeverity,
    FindingSource,
    ReviewCategory,
)
from app.review_automation.models import ReviewAssessment
from app.review_automation.models import ReviewRuleRevision
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


SEED_RULE_KEYS = {
    'feedback_required_prefix',
    'feedback_min_length',
    'common_word_confusion',
    'personal_schedule_conflict',
    'class_schedule_conflict',
    'same_college_teacher',
    'witness_reused_across_weeks',
    'witness_phone_name_conflict',
    'consecutive_teacher_weeks',
    'same_listener_same_slot',
    'school_schedule_mismatch',
    'feedback_similarity',
}


class AutomationModelTest(unittest.TestCase):
    def test_init_schema_command_is_idempotent_and_seeds_revision_one(self):
        with temporary_automation_database():
            runner = app.test_cli_runner()
            first = runner.invoke(args=['auto-review', 'init-schema'])
            second = runner.invoke(args=['auto-review', 'init-schema'])

            self.assertEqual(first.exit_code, 0, first.output)
            self.assertEqual(second.exit_code, 0, second.output)

            rows = ReviewRuleRevision.query.order_by(ReviewRuleRevision.rule_key).all()
            self.assertEqual({row.rule_key for row in rows}, SEED_RULE_KEYS)
            self.assertEqual(len(rows), len(SEED_RULE_KEYS))
            self.assertTrue(all(row.version == 1 for row in rows))
            self.assertTrue(all(isinstance(json.loads(row.parameters_json), dict) for row in rows))

            values = {
                row.rule_key: json.loads(row.parameters_json)
                for row in rows
            }
            self.assertEqual(
                values['feedback_required_prefix'],
                {'required_prefix': '该老师'},
            )
            self.assertEqual(
                values['feedback_min_length'],
                {'minimum_characters': 50},
            )
            self.assertEqual(
                values['witness_reused_across_weeks'],
                {
                    'minimum_distinct_weeks': 2,
                    'high_risk_candidate_weeks': 3,
                    'identity': 'phone_primary',
                },
            )
            self.assertEqual(
                values['consecutive_teacher_weeks'],
                {'maximum_week_gap': 1},
            )

    def test_contract_enum_values_are_stable(self):
        self.assertEqual(ReviewCategory.CLEAR.value, '无明显风险')
        self.assertEqual(ReviewCategory.REVIEW.value, '建议复核')
        self.assertEqual(ReviewCategory.HIGH_RISK.value, '高风险疑似假表')
        self.assertEqual(ReviewCategory.UNKNOWN.value, '系统无法判断')
        self.assertEqual(EvidenceStrength.APPROXIMATE.value, 'approximate')
        self.assertEqual(EvidenceStrength.EXACT.value, 'exact')
        self.assertEqual(FindingSeverity.REVIEW.value, 'review')
        self.assertEqual(FindingSource.SYSTEM.value, 'system')

    def test_finding_draft_is_frozen_and_json_safe(self):
        from dataclasses import FrozenInstanceError

        from app.review_automation.contracts import FindingDraft

        finding = FindingDraft(
            rule_key='synthetic_rule',
            source=FindingSource.RULE,
            severity=FindingSeverity.REVIEW,
            title='合成发现',
            message='合成证据',
            objective=True,
            evidence_strength=EvidenceStrength.EXACT,
            evidence={'form_id': 'synthetic-1', 'weeks': [1, 2]},
        )

        self.assertTrue(finding.objective)
        with self.assertRaises(FrozenInstanceError):
            finding.message = '不应被修改'

    def test_automation_tables_are_additive(self):
        expected_tables = {
            'automation_schedule_datasets',
            'automation_school_schedule_entries',
            'automation_listener_class_mappings',
            'automation_personal_schedule_slots',
            'automation_schedule_import_issues',
            'automation_rule_revisions',
            'automation_review_batches',
            'review_batch_items',
            'automation_review_assessments',
            'automation_review_findings',
            'automation_audit_logs',
        }

        with temporary_automation_database():
            tables = set(inspect(db.engine).get_table_names())

        self.assertTrue(expected_tables.issubset(tables))

    def test_assessment_fingerprint_is_unique(self):
        with temporary_automation_database():
            form = make_synthetic_form()
            db.session.add(form)
            db.session.flush()

            db.session.add(
                ReviewAssessment(
                    form_id=form.id,
                    fingerprint='synthetic-fingerprint',
                )
            )
            db.session.commit()

            db.session.add(
                ReviewAssessment(
                    form_id=form.id,
                    fingerprint='synthetic-fingerprint',
                )
            )
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_saving_assessment_does_not_change_human_review_columns(self):
        with temporary_automation_database():
            form = make_synthetic_form()
            db.session.add(form)
            db.session.commit()
            before = (form.status, form.reviewer_id, form.review_time, form.review_comment)

            assessment = ReviewAssessment(
                form_id=form.id,
                fingerprint='synthetic-protected-columns',
                classification='无明显风险',
                suggested_comment='合成建议，不是最终审核意见',
                created_at=datetime.now(),
            )
            db.session.add(assessment)
            db.session.commit()
            db.session.refresh(form)

            after = (form.status, form.reviewer_id, form.review_time, form.review_comment)
            self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
