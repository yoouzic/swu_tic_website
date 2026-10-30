"""Regression cases for review requests made without calendar parameters."""

import json
import unittest
from types import SimpleNamespace

from app.models import SystemSetting, db
from app.review_automation.models import ReviewAssessment, ReviewBatchItem
from app.review_automation.routes import _batch_payload, _coverage_counts
from app.review_automation.rules.schedule import evaluate_schedule_rules
from app.review_automation.tasks import review as tasks
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


class CurrentReviewContextTest(unittest.TestCase):
    def setUp(self):
        self.database = temporary_automation_database()
        self.database.__enter__()
        self.form = make_synthetic_form()
        db.session.add(self.form)
        db.session.commit()

    def tearDown(self):
        self.database.__exit__(None, None, None)

    def configure_calendar(self):
        SystemSetting.set('teaching_current_semester', 'SYN-CURRENT')
        SystemSetting.set('teaching_first_week_monday', '2026-08-31')

    def batch_config(self, **overrides):
        batch = tasks.create_review_batch([self.form.id], llm_enabled=False,
                                          enqueue=False, **overrides)
        return batch, json.loads(batch.config_snapshot_json)

    def test_request_without_semester_freezes_configured_current_calendar(self):
        self.configure_calendar()
        _, config = self.batch_config()
        self.assertEqual(config['semester'], 'SYN-CURRENT')
        self.assertEqual(config['semester_monday'], '2026-08-31')

    def test_frozen_calendar_does_not_change_when_settings_change(self):
        self.configure_calendar()
        batch, _ = self.batch_config()
        SystemSetting.set('teaching_current_semester', 'SYN-NEXT')
        SystemSetting.set('teaching_first_week_monday', '2027-02-22')
        service = tasks.build_default_service(json.loads(batch.config_snapshot_json))
        self.assertEqual(service.semester, 'SYN-CURRENT')
        self.assertEqual(service.semester_monday.isoformat(), '2026-08-31')

    def test_explicit_other_semester_does_not_borrow_current_calendar(self):
        self.configure_calendar()
        _, config = self.batch_config(semester='SYN-OTHER')
        self.assertEqual(config['semester'], 'SYN-OTHER')
        self.assertIsNone(config['semester_monday'])

    def test_unset_current_semester_does_not_invent_calendar_context(self):
        SystemSetting.set('teaching_first_week_monday', '2026-08-31')
        _, config = self.batch_config()
        self.assertIsNone(config['semester'])
        self.assertIsNone(config['semester_monday'])

    def test_calendar_change_invalidates_assessment_cache(self):
        self.configure_calendar()
        _, first = self.batch_config()
        service = tasks.build_default_service(first)
        a = service.assess_form(self.form.id, review_mode='rules_only')
        SystemSetting.set('teaching_first_week_monday', '2026-09-07')
        _, second = self.batch_config()
        b = tasks.build_default_service(second).assess_form(self.form.id, review_mode='rules_only')
        self.assertNotEqual(a.fingerprint, b.fingerprint)

    def test_preview_separates_unchecked_from_missing_personal_evidence(self):
        counts = _coverage_counts({2: {'coverage': 'none'}, 3: {'coverage': 'basic'}}, [1, 2, 3])
        self.assertEqual(counts['unchecked'], 1)
        self.assertEqual(counts['none'], 1)
        self.assertEqual(counts['basic'], 1)

    def test_actual_worker_uses_existing_admin_import_with_default_context(self):
        from tests.schedule_fixture import seed_current_schedule

        school = seed_current_schedule()
        SystemSetting.set('teaching_first_week_monday', '2026-08-31')
        self.form.teacher_name = '课表测试教师'
        self.form.teacher_college = '测试学院'
        self.form.course_title = '合成课表课程'
        self.form.lecture_location = '8-309'
        self.form.lecture_date = '2026/10/12星期一'
        db.session.commit()
        _, config = self.batch_config()
        self.assertEqual(config['schedule_datasets']['school']['source'], 'canonical_snapshot')
        self.assertEqual(config['schedule_datasets']['school']['id'], str(school.id))
        service = tasks.build_default_service(config)
        matches = service._load_school_matches(self.form)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].mismatched_fields, ())
        self.assertIn('weeks', matches[0].matched_fields)
        self.assertEqual(service._load_schedule(self.form).coverage.value, 'none')

    def test_configured_week_start_is_frozen_for_week_number_checks(self):
        self.configure_calendar()
        SystemSetting.set('teaching_week_start_day', '6')
        _, config = self.batch_config()
        self.assertEqual(config['semester_monday'], '2026-08-30')

    def test_polling_returns_coverage_of_the_completed_assessment(self):
        batch, _ = self.batch_config()
        assessment = ReviewAssessment(form_id=self.form.id, fingerprint='a' * 64,
                                      classification='建议复核', coverage='basic')
        db.session.add(assessment)
        db.session.flush()
        item = ReviewBatchItem.query.filter_by(batch_id=batch.id).one()
        item.assessment_id = assessment.id
        item.status = 'completed'
        item.category = '建议复核'
        db.session.commit()
        result = _batch_payload(batch)['results'][0]
        self.assertEqual(result['coverage'], 'basic')
        self.assertEqual(tasks._aggregate_batch(batch.id)['results'][0]['coverage'], 'basic')


class SchoolCandidateAlternativesTest(unittest.TestCase):
    def test_matching_class_is_not_flagged_for_other_valid_class_times(self):
        candidates = (
            SimpleNamespace(entry=SimpleNamespace(id=1), confidence=1.0,
                            matched_fields=('teacher_name', 'course_title', 'weekday', 'periods'),
                            mismatched_fields=()),
            SimpleNamespace(entry=SimpleNamespace(id=2), confidence=0.5,
                            matched_fields=('teacher_name', 'course_title'),
                            mismatched_fields=('weekday', 'periods')),
        )
        result = evaluate_schedule_rules(make_synthetic_form(), school_matches=candidates,
                                         only_rule='school_schedule_mismatch')
        self.assertEqual(result, ())

    def test_only_mismatching_candidates_still_require_review(self):
        candidate = SimpleNamespace(entry=SimpleNamespace(id=1), confidence=0.5,
                                    matched_fields=('teacher_name', 'course_title'),
                                    mismatched_fields=('weekday', 'periods'))
        result = evaluate_schedule_rules(make_synthetic_form(), school_matches=(candidate,),
                                         only_rule='school_schedule_mismatch')
        self.assertEqual(len(result), 1)


if __name__ == '__main__':
    unittest.main()
