import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import LectureForm, Permission, RolePermission, User, db
from app.review_automation.models import ReviewAssessment, ReviewBatch, ReviewFinding
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class AutomationRoutesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='automation-routes-')
        configure_sqlite_database(app, db, os.path.join(self.temp_dir.name, 'routes.sqlite'))
        app.config.update(TESTING=True, CELERY_TASK_ALWAYS_EAGER=True)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.celery = app.extensions['review_automation_celery']
        self.celery.conf.update(task_always_eager=True, task_eager_propagates=True)

        self.officer = self.create_user('officer', 'INFO-001', '合成信息员', '信息员')
        self.other_officer = self.create_user('other', 'INFO-002', '另一信息员', '信息员')
        self.center_reviewer = self.create_user('center', 'ADMIN-001', '中心审核员', '管理员')
        self.super_admin = self.create_user('super', 'SUPER-001', '合成超管', '超级管理员')
        center_permission = Permission(name='审表_中心', description='synthetic center review permission')
        db.session.add(center_permission)
        db.session.flush()
        db.session.add(RolePermission(role='管理员', permission_id=center_permission.id))

        stamp = datetime(2026, 8, 10, 9, 0, 0)
        self.old_form = self.create_form(
            self.officer.number,
            unique_id=7001,
            updated_at=stamp,
            review_comment='synthetic old review comment',
        )
        self.latest_form = self.create_form(
            self.officer.number,
            unique_id=7001,
            updated_at=stamp + timedelta(minutes=1),
            review_comment='synthetic latest review comment',
        )
        self.other_form = self.create_form(
            self.center_reviewer.number,
            unique_id=7002,
            updated_at=stamp + timedelta(minutes=2),
        )
        db.session.commit()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()
        self.temp_dir.cleanup()

    @staticmethod
    def create_user(student_id, number, name, role):
        user = User(
            number=number,
            department='合成部门',
            name=name,
            gender='-',
            grade='-',
            college='合成学院',
            major='合成专业',
            dormitory='-',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash('password'),
            role=role,
            group='合成小组',
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        return user

    @staticmethod
    def create_form(listener_number, *, unique_id, updated_at, review_comment='合成原始审核意见'):
        form = LectureForm(
            listener_name='合成听课人',
            listener_number=listener_number,
            lecture_date='2026-04-13',
            class_period='1-2',
            lecture_location='合成教室',
            teacher_name='合成教师',
            teacher_college='合成学院',
            course_title='合成课程',
            student_grade_class='合成班级',
            teaching_method='合成方法',
            classroom_discipline='合成纪律',
            classroom_atmosphere='合成氛围',
            courseware_quality='合成课件',
            overall_effect='合成效果',
            quality_case='合成案例',
            course_feedback='该老师的合成课程反馈内容',
            student_signature1='合成见证人',
            contact_phone1='13900000001',
            status='待审核',
            reviewer_id=None,
            review_time=None,
            review_comment=review_comment,
            unique_id=unique_id,
            created_at=updated_at,
            updated_at=updated_at,
        )
        db.session.add(form)
        db.session.flush()
        return form

    def login(self, user):
        with self.client.session_transaction() as session:
            session['user_id'] = user.id
            session['user_role'] = user.role
            session['user_name'] = user.name

    def protected_snapshot(self):
        return {
            form.id: (form.status, form.reviewer_id, form.review_time, form.review_comment)
            for form in (self.old_form, self.latest_form, self.other_form)
        }

    def test_automation_api_requires_json_401_and_role_scoped_403(self):
        response = self.client.get('/admin/api/automation/health')
        self.assertEqual(response.status_code, 401)
        self.login(self.officer)
        self.assertEqual(self.client.get('/admin/api/automation/rules').status_code, 403)
        self.login(self.center_reviewer)
        self.assertEqual(self.client.get('/admin/api/automation/rules').status_code, 403)
        self.assertEqual(self.client.get('/admin/api/automation/health').status_code, 200)
        app.config['DEEPSEEK_API_KEY'] = 'SYNTHETIC_SECRET_MUST_NOT_LEAK'
        health = self.client.get('/admin/api/automation/health')
        self.assertNotIn(b'SYNTHETIC_SECRET_MUST_NOT_LEAK', health.data)
        app.config['DEEPSEEK_API_KEY'] = ''

    def test_all_twelve_automation_api_routes_are_registered(self):
        expected = {
            ('GET', '/admin/api/automation/health'),
            ('GET', '/admin/api/automation/rules'),
            ('POST', '/admin/api/automation/rules/<rule_key>/revisions'),
            ('POST', '/admin/api/automation/datasets/<kind>/preview'),
            ('POST', '/admin/api/automation/datasets/<dataset_id>/activate'),
            ('GET', '/admin/api/automation/datasets'),
            ('GET', '/admin/api/automation/forms/<int:form_id>/assessment'),
            ('GET', '/admin/api/automation/assessments/<assessment_id>'),
            ('POST', '/admin/api/automation/batches/preview'),
            ('POST', '/admin/api/automation/batches'),
            ('GET', '/admin/api/automation/batches/<batch_id>'),
            ('POST', '/admin/api/automation/batches/<batch_id>/cancel'),
        }
        registered = {
            (method, rule.rule)
            for rule in app.url_map.iter_rules()
            for method in rule.methods
            if method in {'GET', 'POST'} and rule.rule.startswith('/admin/api/automation/')
        }
        self.assertTrue(expected.issubset(registered))

    def test_super_admin_can_read_rules_and_center_reviewer_cannot_change_configuration(self):
        self.login(self.super_admin)
        rules = self.client.get('/admin/api/automation/rules')
        self.assertEqual(rules.status_code, 200)
        self.assertIn('rules', rules.get_json())
        response = self.client.post(
            '/admin/api/automation/rules/feedback_required_prefix/revisions',
            json={
                'handler': 'required_prefix',
                'severity': 'review',
                'enabled': True,
                'parameters': {'required_prefix': '该老师'},
                'change_reason': 'synthetic route test',
            },
        )
        self.assertEqual(response.status_code, 201)
        self.login(self.center_reviewer)
        forbidden = self.client.post(
            '/admin/api/automation/rules/feedback_required_prefix/revisions',
            json={'handler': 'required_prefix', 'parameters': {'required_prefix': '该老师'}},
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_batch_scope_intersects_reviewable_users_and_keeps_latest_logical_form(self):
        self.login(self.center_reviewer)
        before = self.protected_snapshot()
        preview = self.client.post(
            '/admin/api/automation/batches/preview',
            json={
                'form_ids': [self.old_form.id, self.other_form.id, 999999],
                'llm_enabled': False,
            },
        )
        self.assertEqual(preview.status_code, 200)
        stats = preview.get_json()['stats']
        self.assertEqual(stats['processable'], 1)
        self.assertEqual(stats['latest_form_ids'], [self.latest_form.id])

        created = self.client.post(
            '/admin/api/automation/batches',
            json={
                'form_ids': [self.old_form.id, self.other_form.id],
                'llm_enabled': False,
            },
        )
        self.assertEqual(created.status_code, 201)
        payload = created.get_json()
        self.assertEqual(payload['target_form_count'], 1)
        batch = db.session.get(ReviewBatch, payload['batch_id'])
        config = json.loads(batch.config_snapshot_json)
        self.assertEqual(config['form_ids'], [self.latest_form.id])
        self.assertTrue(payload.get('legacy_force_ignored') is False)
        self.assertEqual(before, self.protected_snapshot())

    def test_llm_batch_requires_external_transfer_acknowledgement(self):
        self.login(self.center_reviewer)
        response = self.client.post(
            '/admin/api/automation/batches/preview',
            json={'form_ids': [self.latest_form.id], 'llm_enabled': True},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('external', response.get_json()['code'])

    def test_assessment_summary_and_detail_are_bulk_safe_and_read_only(self):
        assessment = ReviewAssessment(
            form_id=self.latest_form.id,
            form_version='synthetic-v1',
            classification='建议复核',
            coverage='basic',
            fingerprint='a' * 64,
            suggested_comment='synthetic editable suggestion',
        )
        db.session.add(assessment)
        db.session.flush()
        db.session.add(ReviewFinding(
            assessment_id=assessment.id,
            source='schedule',
            rule_key='synthetic_rule',
            severity='review',
            title='合成证据',
            message='合成证据消息',
            objective=True,
            evidence_strength='approximate',
            evidence_json='{"marker":"SYNTHETIC_EVIDENCE"}',
        ))
        db.session.commit()
        self.login(self.center_reviewer)
        before = self.protected_snapshot()
        listing = self.client.get('/admin/api/review/forms')
        self.assertEqual(listing.status_code, 200)
        latest_rows = [
            item
            for group in listing.get_json()['forms']
            for item in group['forms']
            if item['id'] == self.latest_form.id
        ]
        self.assertEqual(latest_rows[0]['automation']['category'], '建议复核')
        detail = self.client.get(f'/admin/api/review/form/{self.latest_form.id}')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()['automation']['assessment_id'], assessment.id)
        self.assertEqual(self.client.get(f'/admin/api/automation/assessments/{assessment.id}').status_code, 200)
        self.assertEqual(before, self.protected_snapshot())

    def test_legacy_batch_route_ignores_force_all_and_does_not_change_human_state(self):
        self.login(self.center_reviewer)
        before = self.protected_snapshot()
        response = self.client.post(
            '/admin/api/review/batch_auto_check',
            json={'form_ids': [self.old_form.id], 'force_all': True, 'llm_enabled': False},
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()['legacy_force_ignored'])
        self.assertEqual(before, self.protected_snapshot())


if __name__ == '__main__':
    unittest.main()
