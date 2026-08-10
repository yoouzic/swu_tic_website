import json
import unittest
from datetime import date
from types import SimpleNamespace

from app.models import User, db
from app.review_automation.contracts import (
    DatasetStatus,
    EvidenceStrength,
    ReviewCategory,
    ScheduleCoverage,
)
from app.review_automation.models import (
    ListenerClassMapping,
    PersonalScheduleSlot,
    ReviewAssessment,
    ReviewRuleRevision,
    ScheduleDataset,
    SchoolScheduleEntry,
)
from app.review_automation.schedules.repository import get_listener_schedule_for_datasets
from app.review_automation.tasks import review as review_tasks
from tests.automation_test_utils import make_synthetic_form, temporary_automation_database


SEMESTER = 'SYN-2026-SPRING'
SEMESTER_MONDAY = '2026-04-13'
LISTENER_NUMBER = 'SYN-LISTENER-D'
STUDENT_ID = 'SYN-STUDENT-D'


class FrozenDependencyTest(unittest.TestCase):
    def setUp(self):
        self.database = temporary_automation_database()
        self.database.__enter__()
        self.previous_factory = review_tasks.SERVICE_FACTORY
        self.user = User(
            number=LISTENER_NUMBER,
            department='SYN-DEPARTMENT',
            name='SYNTHETIC_NAME_MARKER',
            gender='SYN',
            grade='SYN-GRADE',
            college='SYN-COLLEGE',
            major='SYN-MAJOR',
            dormitory='SYN-DORM',
            phone='SYNTHETIC_PHONE_MARKER',
            qq='SYNTHETIC_QQ',
            student_id=STUDENT_ID,
            password_hash='SYNTHETIC_HASH',
            role='信息员',
            group='SYN-GROUP',
        )
        self.form = make_synthetic_form(
            listener_number=LISTENER_NUMBER,
            teacher_name='SYN-TEACHER',
            course_title='SYN-COURSE',
            teacher_college='SYN-OTHER-COLLEGE',
            lecture_date='2026-04-13',
            class_period='1-2',
            course_feedback='SYNTHETIC_FEEDBACK_MARKER',
        )
        db.session.add_all([self.user, self.form])
        db.session.commit()

    def tearDown(self):
        review_tasks.SERVICE_FACTORY = self.previous_factory
        self.database.__exit__(None, None, None)

    def dataset(self, kind, dataset_id, sha, *, status=DatasetStatus.ACTIVE.value):
        return ScheduleDataset(
            id=dataset_id,
            kind=kind,
            semester=SEMESTER,
            sha256=sha,
            original_filename='synthetic.xlsx',
            status=status,
        )

    def rule(self, rule_key, handler, version=1):
        return ReviewRuleRevision(
            rule_key=rule_key,
            version=version,
            handler=handler,
            enabled=True,
            severity='review',
            parameters_json='{}',
            change_reason='synthetic test revision',
        )

    def add_school_row(self, dataset_id, *, teacher_college='SYN-OTHER-COLLEGE'):
        db.session.add(SchoolScheduleEntry(
            dataset_id=dataset_id,
            teacher_name='SYN-TEACHER',
            teacher_college=teacher_college,
            course_title='SYN-COURSE',
            teaching_class='SYN-CLASS-001',
            major='SYN-MAJOR',
            weeks_json='[1]',
            weekday=1,
            start_period=1,
            end_period=2,
            location='SYN-ROOM',
        ))

    def add_personal_row(self, dataset_id):
        db.session.add(PersonalScheduleSlot(
            dataset_id=dataset_id,
            listener_number=LISTENER_NUMBER,
            student_id=STUDENT_ID,
            course_title='SYN-PERSONAL-COURSE',
            weeks_json='[1]',
            weekday=1,
            start_period=1,
            end_period=2,
        ))

    def add_mapping(self, dataset_id):
        db.session.add(ListenerClassMapping(
            dataset_id=dataset_id,
            listener_number=LISTENER_NUMBER,
            student_id=STUDENT_ID,
            admin_class='SYN-CLASS-001',
        ))

    def create_batch(self, *, rule_key, handler, include_personal=False, include_mapping=False):
        school = self.dataset('school', 'SYN-SCHOOL-OLD', 'a' * 64)
        mapping = self.dataset('class_mapping', 'SYN-MAPPING-OLD', 'b' * 64)
        personal = self.dataset('personal', 'SYN-PERSONAL-OLD', 'c' * 64)
        db.session.add_all([school, mapping, personal, self.rule(rule_key, handler)])
        db.session.flush()
        self.add_school_row(school.id)
        if include_mapping:
            self.add_mapping(mapping.id)
        if include_personal:
            self.add_personal_row(personal.id)
        db.session.commit()
        batch = review_tasks.create_review_batch(
            [self.form.id],
            semester=SEMESTER,
            semester_monday=SEMESTER_MONDAY,
            llm_enabled=False,
            enqueue=False,
        )
        return batch, school, mapping, personal

    def build_service(self, batch):
        config = json.loads(batch.config_snapshot_json)
        return review_tasks.build_default_service(config)

    def test_frozen_dataset_lookup_uses_explicit_ids_and_exact_personal_coverage(self):
        _, school, mapping, personal = self.create_batch(
            rule_key='personal_schedule_conflict',
            handler='personal_schedule_conflict',
            include_personal=True,
            include_mapping=True,
        )

        schedule = get_listener_schedule_for_datasets(
            LISTENER_NUMBER,
            STUDENT_ID,
            SEMESTER,
            {
                'school': {'id': school.id},
                'class_mapping': {'id': mapping.id},
                'personal': {'id': personal.id},
            },
        )

        self.assertEqual(schedule.coverage, ScheduleCoverage.COMPLETE)
        self.assertTrue(any(
            slot.evidence_strength == EvidenceStrength.EXACT
            for slot in schedule.slots
        ))

    def test_default_service_personal_schedule_conflict_is_high_risk(self):
        batch, _, _, _ = self.create_batch(
            rule_key='personal_schedule_conflict',
            handler='personal_schedule_conflict',
            include_personal=True,
        )

        summary = self.build_service(batch).assess_form(self.form.id, llm_enabled=False)
        assessment = db.session.get(ReviewAssessment, summary.assessment_id)

        self.assertEqual(summary.coverage, ScheduleCoverage.COMPLETE.value)
        self.assertEqual(summary.category, ReviewCategory.HIGH_RISK.value)
        self.assertIn('personal_schedule_conflict', {item.rule_key for item in assessment.findings})

    def test_default_service_class_mapping_is_basic_approximate_review(self):
        batch, _, _, _ = self.create_batch(
            rule_key='class_schedule_conflict',
            handler='class_schedule_conflict',
            include_mapping=True,
        )

        summary = self.build_service(batch).assess_form(self.form.id, llm_enabled=False)
        assessment = db.session.get(ReviewAssessment, summary.assessment_id)
        finding = next(item for item in assessment.findings if item.rule_key == 'class_schedule_conflict')

        self.assertEqual(summary.coverage, ScheduleCoverage.BASIC.value)
        self.assertEqual(summary.category, ReviewCategory.REVIEW.value)
        self.assertEqual(finding.evidence_strength, EvidenceStrength.APPROXIMATE.value)

    def test_default_service_same_listener_college_is_high_risk(self):
        self.form.teacher_college = 'SYN-COLLEGE'
        db.session.commit()
        batch, _, _, _ = self.create_batch(
            rule_key='same_college_teacher',
            handler='same_college_teacher',
        )

        summary = self.build_service(batch).assess_form(self.form.id, llm_enabled=False)

        self.assertEqual(summary.category, ReviewCategory.HIGH_RISK.value)

    def test_rule_context_contains_frozen_schedule_metadata(self):
        from app.review_automation.service import AssessmentService

        captured = {}
        service = AssessmentService(
            llm_enabled=False,
            schedule_loader=lambda form: SimpleNamespace(coverage=ScheduleCoverage.NONE),
            school_matches_loader=lambda form: ('SYN-SCHOOL-MATCH',),
            listener_profile_loader=lambda form: SimpleNamespace(college='SYN-COLLEGE'),
            semester=SEMESTER,
            semester_monday=date(2026, 4, 13),
            deterministic_runner=lambda form, context: captured.update({
                'school_matches': context.school_matches,
                'listener_college': context.listener_college,
                'semester': context.semester,
                'semester_monday': context.semester_monday,
            }) or (),
        )

        service.assess_form(self.form.id)

        self.assertEqual(captured['school_matches'], ('SYN-SCHOOL-MATCH',))
        self.assertEqual(captured['listener_college'], 'SYN-COLLEGE')
        self.assertEqual(captured['semester'], SEMESTER)
        self.assertEqual(captured['semester_monday'], date(2026, 4, 13))

    def test_batch_snapshot_freezes_datasets_and_rule_revision_before_active_switch(self):
        batch, school, mapping, personal = self.create_batch(
            rule_key='personal_schedule_conflict',
            handler='personal_schedule_conflict',
            include_personal=True,
        )
        config = json.loads(batch.config_snapshot_json)
        self.assertEqual(config['schedule_datasets']['personal']['id'], personal.id)
        self.assertEqual(config['schedule_datasets']['personal']['sha256'], 'c' * 64)
        self.assertEqual(config['rule_revisions']['personal_schedule_conflict']['version'], 1)
        self.assertIn('model_id', config)
        self.assertIn('prompt_version', config)
        rendered = batch.config_snapshot_json
        self.assertNotIn('SYNTHETIC_NAME_MARKER', rendered)
        self.assertNotIn('SYNTHETIC_PHONE_MARKER', rendered)
        self.assertNotIn('SYNTHETIC_FEEDBACK_MARKER', rendered)

        replacement = self.dataset('personal', 'SYN-PERSONAL-NEW', 'd' * 64)
        db.session.add(replacement)
        db.session.add(self.rule('personal_schedule_conflict', 'personal_schedule_conflict', version=2))
        db.session.commit()
        personal.status = DatasetStatus.RETIRED.value
        replacement.status = DatasetStatus.ACTIVE.value
        db.session.commit()

        review_tasks.SERVICE_FACTORY = lambda frozen_config: review_tasks.build_default_service(frozen_config)
        review_tasks.run_review_batch_task.run(batch.id)
        assessment = ReviewAssessment.query.filter_by(batch_id=batch.id).one()

        dependency_ids = json.loads(assessment.dependency_ids_json)
        dependency_versions = json.loads(assessment.dependency_versions_json)
        self.assertEqual(assessment.coverage, ScheduleCoverage.COMPLETE.value)
        self.assertEqual(assessment.classification, ReviewCategory.HIGH_RISK.value)
        self.assertEqual(dependency_ids['personal'], personal.id)
        self.assertEqual(dependency_versions['personal'], config['schedule_datasets']['personal']['version'])
        self.assertEqual(dependency_versions['rules']['personal_schedule_conflict']['version'], 1)

    def test_missing_semester_or_datasets_is_unknown_not_clear(self):
        batch = review_tasks.create_review_batch(
            [self.form.id],
            llm_enabled=False,
            enqueue=False,
        )

        review_tasks.SERVICE_FACTORY = lambda frozen_config: review_tasks.build_default_service(frozen_config)
        review_tasks.run_review_batch_task.run(batch.id)
        assessment = ReviewAssessment.query.filter_by(batch_id=batch.id).one()

        self.assertEqual(assessment.coverage, ScheduleCoverage.NONE.value)
        self.assertEqual(assessment.classification, ReviewCategory.UNKNOWN.value)


if __name__ == '__main__':
    unittest.main()
