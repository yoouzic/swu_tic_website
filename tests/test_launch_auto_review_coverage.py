"""Missing current schedule is unverified coverage, never a fake course error."""
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

_BOOT_DIR = tempfile.TemporaryDirectory(prefix='launch-auto-review-bootstrap-')
_BOOT = Path(_BOOT_DIR.name)
os.environ.update({
    'DATABASE_URL': '', 'INSTANCE_DIR': str(_BOOT / 'instance'),
    'SQLITE_DB_PATH': str(_BOOT / 'bootstrap.sqlite'), 'UPLOAD_FOLDER': str(_BOOT / 'uploads'),
    'AUTOMATION_UPLOAD_DIR': str(_BOOT / 'automation'), 'AUTO_REVIEW_UPLOAD_DIR': str(_BOOT / 'auto-review'),
    'AUTO_REVIEW_REPORT_DIR': str(_BOOT / 'reports'), 'EXPORT_DIR': str(_BOOT / 'exports'),
    'CONTACT_TEMPLATE_PATH': str(_BOOT / 'contacts.xlsx'), 'SCHEDULE_TEMPLATE_PATH': str(_BOOT / 'schedule.xlsx'),
    'AUTO_REVIEW_DEFAULT_SCHEDULE_PATH': str(_BOOT / 'missing.xlsx'),
    'STORAGE_CLEANUP_ENABLED': 'false', 'DEEPSEEK_API_KEY': '',
    'DEEPSEEK_BASE_URL': 'http://127.0.0.1:1/disabled',
    'CELERY_BROKER_URL': 'memory://', 'CELERY_RESULT_BACKEND': 'cache+memory://',
    'SECRET_KEY': 'isolated-launch-auto-review',
})

import pandas as pd
from app.app import app
from app.models import SystemSetting, User, ScoreRecord, db
from app.services.schedule_snapshots import persist_import_snapshot
from app.services.review_schedule_source import resolve_review_schedule_source
from app.utils.auto_review import AutoReviewEngine
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.test_review_schedule_source import DEFAULT_ROW

CURRENT = '2026-2027-1'
OLD = '2025-2026-2'


def valid_form(**changes):
    fields = dict(id=1, listener_name='合成人员（外国语学院）', listener_number='SYN-1001',
                  teacher_name='合成教师', teacher_college='数学与统计学院', course_title='合成课程',
                  student_grade_class='2026级合成班', class_composition='2026级合成班',
                  lecture_date='2026/09/09星期三', class_period='第3-4节', lecture_location='32-302',
                  teaching_method='PPT演示法', classroom_discipline='好', classroom_atmosphere='好',
                  courseware_quality='好', overall_effect='好', quality_case='推荐',
                  course_feedback='该老师通过示例逐步解释课程概念，课堂讨论安排合理，学生能够结合材料理解知识并完成练习。' * 3,
                  suggestions='无', phone='13800000001')
    fields.update(changes)
    return SimpleNamespace(**fields)


class LaunchAutoReviewCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='launch-auto-review-')
        self.root = Path(self.tmp.name)
        configure_sqlite_database(app, db, self.root / 'test.sqlite')
        self.context = app.app_context()
        self.context.push()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        db.create_all()
        db.session.add(User(number='SYN-1001', student_id='SYN-student', name='合成人员', role='信息员',
                            department='办公部', group='合成组', gender='-', grade='2026', college='外国语学院',
                            major='-', dormitory='-', phone='13800000001', qq='-', password_hash='x', is_active=True))
        db.session.commit()
        for key, value in [('teaching_current_semester', CURRENT), ('teaching_first_week_monday', '2026-09-07'),
                           ('teaching_week_start_day', '0'), ('teaching_total_weeks', '16')]:
            SystemSetting.set(key, value)
        self.legacy_path = self.root / 'old-schedule.xlsx'
        pd.DataFrame([self.schedule_row(OLD)]).to_excel(self.legacy_path, index=False)
        SystemSetting.set('auto_review_schedule_path', str(self.legacy_path))

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()
        self.tmp.cleanup()

    def schedule_row(self, semester):
        row = dict(DEFAULT_ROW)
        row.update({'姓名': '合成教师', '教师所属学院': '数学与统计学院', '课程名称': '合成课程',
                    '教学班组成': '2026级合成班', '学期': semester})
        return row

    def activate(self, semester):
        persist_import_snapshot(pd.DataFrame([self.schedule_row(semester)]),
                                source_filename='synthetic.xlsx', source_sha256='a' * 64)
        db.session.commit()

    def assert_unverified(self, engine):
        form = valid_form()
        before = vars(form).copy()
        for method in [engine.review_any, engine.review_one]:
            result = method(form)
            self.assertEqual(result['issues'], [])
            self.assertEqual(result['fixes'], [])
            self.assertIsNone(result['course_info'])
            self.assertFalse(result['passed'], 'A missing dependency cannot imply full review clearance')
            self.assertTrue(result['checks_passed'])
            self.assertEqual(result['classification'], '系统无法判断')
            check = result['check_results']['school_schedule']
            self.assertFalse(check['available'])
            self.assertTrue(check['skipped'])
            self.assertEqual(check['coverage'], 'none')
            self.assertEqual(check['status'], 'unavailable')
            self.assertTrue(result['requires_manual_review'])
            self.assertIn('未核验', check['message'])
            self.assertEqual(result['warnings'], [check['message']])
            self.assertEqual(vars(form), before)
        self.assertEqual(ScoreRecord.query.count(), 0)

    def test_current_missing_with_old_snapshot_and_file_skips_only_school_matching(self):
        self.activate(OLD)
        with patch('app.services.review_schedule_source._read_excel') as read:
            engine = AutoReviewEngine()
        read.assert_not_called()
        self.assert_unverified(engine)

    def test_current_unset_never_reads_implicit_old_file_or_snapshot(self):
        self.activate(OLD)
        SystemSetting.set('teaching_current_semester', '')
        from app.services import review_schedule_source
        with patch('app.services.review_schedule_source._read_excel', wraps=review_schedule_source._read_excel) as read:
            engine = AutoReviewEngine()
        read.assert_not_called()
        self.assertIsNone(engine.schedule_df)
        self.assert_unverified(engine)
        self.assertIn('未设置', engine.review_any(valid_form())['warnings'][0])

    def test_non_schedule_identity_format_and_evaluation_errors_still_execute(self):
        result = AutoReviewEngine().review_any(valid_form(
            listener_name='合成人员（未知学院）', phone='invalid', class_period='第15-16节',
            teaching_method='非法教学法', course_feedback='过短'))
        issues = '；'.join(result['issues'])
        for fragment in ['有效学院', '联系电话格式错误', '节次范围不正确', '教学方法', '课程反馈字数']:
            self.assertIn(fragment, issues)
        self.assertNotIn('无法在课表中确认', issues)
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks_passed'])
        self.assertEqual(result['classification'], '建议复核')
        self.assertTrue(result['check_results']['school_schedule']['skipped'])

    def test_ready_restores_match_and_real_mismatch_checks(self):
        self.activate(CURRENT)
        with patch('app.services.review_schedule_source._read_excel') as read:
            engine = AutoReviewEngine()
        read.assert_not_called()
        matched = engine.review_any(valid_form())
        self.assertTrue(matched['passed'])
        self.assertEqual(matched['classification'], '无明显风险')
        self.assertEqual(matched['issues'], [])
        self.assertEqual(matched['warnings'], [])
        check = matched['check_results']['school_schedule']
        self.assertTrue(check['available'])
        self.assertFalse(check['skipped'])
        self.assertEqual(check['coverage'], 'complete')
        self.assertEqual(check['status'], 'matched')
        self.assertEqual(check['semester'], CURRENT)
        mismatch = engine.review_any(valid_form(course_title='填写错误的课程'))
        self.assertTrue(any('课程名称不匹配' in item for item in mismatch['issues']))
        self.assertFalse(mismatch['passed'])
        self.assertEqual(mismatch['classification'], '建议复核')

    def test_explicit_history_tool_still_matches_but_current_reference_does_not(self):
        SystemSetting.set('teaching_current_semester', '')
        implicit = AutoReviewEngine().search_reference_data({'teacher_name': '合成教师', 'course_title': '合成课程'})
        self.assertEqual(implicit['schedule_matches'], [])
        explicit = AutoReviewEngine(schedule_path=str(self.legacy_path))
        self.assertEqual(explicit.schedule_source_kind, 'explicit_legacy')
        self.assertTrue(explicit.review_any(valid_form())['passed'])
        self.assertTrue(explicit.search_reference_data({'teacher_name': '合成教师', 'course_title': '合成课程'})['schedule_matches'])

    def test_source_boundary_explicitly_disables_legacy_fallback(self):
        SystemSetting.set('teaching_current_semester', '')
        source = resolve_review_schedule_source(allow_unconfigured_legacy_fallback=False)
        self.assertEqual(source.kind, 'none')
        self.assertIsNone(source.dataframe)

    def test_current_review_api_exposes_gap_and_reference_api_excludes_implicit_history(self):
        user = User.query.one()
        user.role = '超级管理员'
        db.session.commit()
        SystemSetting.set('teaching_current_semester', '')
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=user.id, user_role=user.role, user_name=user.name)
        body = vars(valid_form()).copy()
        body['contact_phone1'] = body['phone']
        checked = client.post('/admin/api/review/auto_check', json=body)
        self.assertEqual(checked.status_code, 200)
        result = checked.get_json()['result']
        self.assertEqual(result['issues'], [])
        self.assertFalse(result['passed'])
        self.assertEqual(result['classification'], '系统无法判断')
        self.assertTrue(result['check_results']['school_schedule']['skipped'])
        references = client.post('/admin/api/review/reference_data', json=body)
        self.assertEqual(references.status_code, 200)
        self.assertEqual(references.get_json()['result']['schedule_matches'], [])

    def test_current_personal_dataset_and_independent_college_rules_survive_no_school(self):
        from datetime import date
        from app.review_automation.classification import aggregate_classification
        from app.review_automation.contracts import ReviewCategory, ScheduleCoverage
        from app.review_automation.models import PersonalScheduleSlot, ScheduleDataset
        from app.review_automation.rules.schedule import evaluate_schedule_rules
        from app.review_automation.schedules.repository import get_listener_schedule
        for semester, marker in [(CURRENT, 'current'), (OLD, 'old')]:
            dataset = ScheduleDataset(id='SYN-personal-' + marker, kind='personal', semester=semester,
                                      sha256=marker[0] * 64, original_filename='synthetic-personal.xlsx',
                                      status='active', row_count=1)
            db.session.add(dataset)
            db.session.flush()
            db.session.add(PersonalScheduleSlot(dataset_id=dataset.id, listener_number='SYN-1001',
                                               student_id='SYN-student', course_title='合成个人课-' + marker,
                                               weeks_json='[1]', weekday=3, start_period=3, end_period=4))
        db.session.commit()
        self.assertEqual(ScheduleDataset.query.filter_by(kind='school').count(), 0)
        schedule = get_listener_schedule('SYN-1001', 'SYN-student', CURRENT)
        self.assertEqual(schedule.coverage, ScheduleCoverage.COMPLETE)
        self.assertEqual([slot.course_title for slot in schedule.slots], ['合成个人课-current'])
        findings = evaluate_schedule_rules(valid_form(), schedule, semester_monday=date(2026, 9, 7))
        self.assertTrue(any(f.rule_key == 'personal_schedule_conflict' for f in findings))
        category = aggregate_classification(findings, coverage=schedule.coverage, llm_required=False)
        self.assertEqual(category.category, ReviewCategory.HIGH_RISK)
        college_findings = evaluate_schedule_rules(valid_form(teacher_college='外国语学院'), None,
                                                   listener_college='外国语学院')
        self.assertTrue(any(f.rule_key == 'same_college_teacher' for f in college_findings))
        missing = aggregate_classification((), coverage=ScheduleCoverage.NONE, llm_required=False)
        self.assertEqual(missing.category, ReviewCategory.UNKNOWN)
