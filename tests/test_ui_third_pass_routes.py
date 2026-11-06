import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from openpyxl import load_workbook
from werkzeug.security import generate_password_hash


TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'ui_third_pass.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app
from app.models import LectureForm, User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class ThirdPassRouteContractTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(TESTING=True)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()

        self.super_admin = self._create_user('super', 'SA001', '中心管理员', '超级管理员', '中心')
        self.department_admin = self._create_user('department-admin', 'DA001', '甲部门管理员', '管理员', '甲部门')
        self.listener_a = self._create_user('listener-a', 'U001', '甲用户', '信息员', '甲部门')
        self.listener_b = self._create_user('listener-b', 'U002', '乙用户', '信息员', '乙部门')
        db.session.flush()
        self.pending = self._create_form(
            self.listener_a,
            status='待审核',
            lecture_date='2026-08-01',
            teacher_name='教师甲',
            teacher_college='学院甲',
            created_at=datetime(2026, 8, 1, 9, 0),
        )
        self.department_reviewed = self._create_form(
            self.listener_a,
            status='部门已审核',
            lecture_date='2026-08-02',
            teacher_name='教师乙',
            teacher_college='学院甲',
            created_at=datetime(2026, 8, 2, 9, 0),
        )
        self.rejected = self._create_form(
            self.listener_b,
            status='已驳回',
            lecture_date='2026-08-03',
            teacher_name='教师丙',
            teacher_college='学院乙',
            created_at=datetime(2026, 8, 3, 9, 0),
        )
        db.session.commit()
        self._login(self.super_admin)

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()

    def _create_user(self, student_id, number, name, role, department):
        user = User(
            number=number,
            department=department,
            name=name,
            gender='-',
            grade='-',
            college='-',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash('password'),
            role=role,
            group='一组',
            is_active=True,
        )
        db.session.add(user)
        return user

    def _create_form(self, listener, status, lecture_date, teacher_name, teacher_college, created_at):
        form = LectureForm(
            listener_name=listener.name,
            listener_number=listener.number,
            lecture_date=lecture_date,
            class_period='1-2节',
            lecture_location='教室A',
            teacher_name=teacher_name,
            teacher_college=teacher_college,
            course_title=f'{teacher_name}课程',
            student_grade_class='2024级',
            teaching_method='讲授',
            classroom_discipline='良好',
            classroom_atmosphere='良好',
            courseware_quality='良好',
            overall_effect='良好',
            quality_case='无',
            course_feedback='清晰',
            student_signature1='学生甲',
            contact_phone1='10000000000',
            status=status,
            created_at=created_at,
            updated_at=created_at,
        )
        db.session.add(form)
        db.session.flush()
        form.unique_id = form.id
        return form

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def test_statistics_page_applies_filters_and_visualization_state(self):
        response = self.client.get(
            '/admin/statistics?department=甲部门&status=已审核&dimension=user'
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('甲用户'.encode('utf-8'), response.data)
        self.assertNotIn('乙用户'.encode('utf-8'), response.data)
        self.assertIn('暂不足以形成趋势'.encode('utf-8'), response.data)

        response = self.client.get('/admin/statistics?start_date=2026-09-01')
        self.assertEqual(response.status_code, 200)
        self.assertIn('暂无提交趋势数据'.encode('utf-8'), response.data)

    def test_statistics_detail_and_export_reuse_filter_scope(self):
        detail = self.client.get(
            '/admin/api/statistics_detail?department=甲部门&detail_dimension=user&detail_value=U001'
        )
        self.assertEqual(detail.status_code, 200)
        payload = detail.get_json()
        self.assertEqual(payload['pagination']['total'], 2)
        self.assertEqual({item['listener_department'] for item in payload['forms']}, {'甲部门'})

        exported = self.client.get(
            '/admin/export_statistics?department=甲部门&status=已审核&dimension=user'
        )
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(
            exported.mimetype,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        workbook = load_workbook(filename=__import__('io').BytesIO(exported.data), read_only=True)
        rows = list(workbook.active.iter_rows(values_only=True))
        self.assertEqual(rows[0][:2], ('用户', '部门'))
        self.assertEqual(rows[1][0:6], ('甲用户', '甲部门', 1, 0, 1, 0))

        self.listener_a.name = '=HYPERLINK("https://invalid.example")'
        db.session.commit()
        exported = self.client.get(
            '/admin/export_statistics?department=甲部门&status=已审核&dimension=user'
        )
        workbook = load_workbook(filename=__import__('io').BytesIO(exported.data), read_only=True)
        self.assertTrue(workbook.active['A2'].value.startswith("'="))

    def test_statistics_department_admin_cannot_expand_scope_with_query_param(self):
        self._login(self.department_admin)
        response = self.client.get('/admin/api/statistics_detail?department=乙部门')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['pagination']['total'], 2)
        self.assertEqual({item['listener_department'] for item in payload['forms']}, {'甲部门'})

    def _revise_pending_form(self, status='部门已审核', lecture_date='2026-08-01'):
        revision = self._create_form(
            self.listener_a, status=status, lecture_date=lecture_date,
            teacher_name='修订教师', teacher_college='学院甲',
            created_at=datetime(2026, 8, 5, 9, 0),
        )
        revision.unique_id = self.pending.id
        revision.updated_at = datetime(2026, 8, 5, 9, 0)
        # Imported legacy base rows can have no unique_id, and an audit can touch
        # an old version after a newer version has already been submitted.
        self.pending.unique_id = None
        self.pending.updated_at = datetime(2026, 8, 9, 9, 0)
        db.session.commit()
        return revision

    def test_statistics_details_keep_latest_revision_and_distinct_lectures(self):
        revision = self._revise_pending_form()
        result = self.client.get('/admin/api/statistics_detail?department=甲部门').get_json()
        self.assertEqual(result['pagination']['total'], 2)
        self.assertEqual({item['id'] for item in result['forms']},
                         {revision.id, self.department_reviewed.id})
        self.assertEqual({item['status'] for item in result['forms']}, {'部门已审核'})

    def test_statistics_status_filter_does_not_resurrect_old_pending_revision(self):
        self._revise_pending_form()
        result = self.client.get('/admin/api/statistics_detail?department=甲部门&status=待审核').get_json()
        self.assertEqual(result['pagination']['total'], 0)
        exported = self.client.get('/admin/export_statistics?department=甲部门&dimension=user')
        workbook = load_workbook(filename=__import__('io').BytesIO(exported.data), read_only=True)
        rows = list(workbook.active.iter_rows(values_only=True))
        self.assertEqual(rows[1][0:6], ('甲用户', '甲部门', 2, 0, 2, 0))

    def test_statistics_date_filter_runs_after_revision_selection(self):
        self._revise_pending_form(lecture_date='2026-09-01')
        result = self.client.get(
            '/admin/api/statistics_detail?department=甲部门&end_date=2026-08-31').get_json()
        self.assertEqual({item['id'] for item in result['forms']}, {self.department_reviewed.id})

    def test_statistics_date_range_accepts_stored_slash_weekday_and_legacy_formats(self):
        expected_ids = set()
        for date_text in ['2026/11/05星期四', '2026年11月5日', '2026.11.05', '2026-11-05']:
            form = self._create_form(
                self.listener_a, status='部门已审核', lecture_date=date_text,
                teacher_name='教师日期', teacher_college='学院甲',
                created_at=datetime(2026, 11, 5, 9, 0),
            )
            expected_ids.add(form.id)
        db.session.commit()
        query = '?department=甲部门&start_date=2026-11-05&end_date=2026-11-05'
        result = self.client.get('/admin/api/statistics_detail' + query).get_json()
        self.assertEqual({item['id'] for item in result['forms']}, expected_ids)
        counts = self.client.get('/admin/api/statistics_data' + query).get_json()
        self.assertEqual(counts['month_stats'], {'2026-11': {'pending': 4}})
        workbook = load_workbook(filename=__import__('io').BytesIO(
            self.client.get('/admin/export_statistics' + query).data), read_only=True)
        self.assertEqual(workbook.active['B2'].value, 4)

    def test_statistics_pending_api_ignores_touched_old_revision(self):
        self._revise_pending_form(status='中心已审核')
        self._login(self.department_admin)
        result = self.client.get('/admin/api/statistics_data?department=甲部门').get_json()
        self.assertEqual(result['overview']['pending_forms'], 0)

    def test_statistics_headline_counts_approved_latest_forms_only(self):
        self._revise_pending_form()
        pending = self._create_form(
            self.listener_a, status='待审核', lecture_date='2026-08-06',
            teacher_name='待审教师', teacher_college='学院甲',
            created_at=datetime(2026, 8, 6, 9, 0),
        )
        db.session.commit()
        with patch('app.blueprints.admin.statistics.render_template', return_value='ok') as render:
            self.assertEqual(self.client.get('/admin/statistics?department=甲部门').status_code, 200)
        context = render.call_args.kwargs
        self.assertEqual(context['total_forms'], 2)
        self.assertEqual(context['logical_form_count'], 3)
        self.assertEqual(context['pending_forms'], 1)

    def test_statistics_keeps_distinct_legacy_zero_identity_forms(self):
        self.pending.unique_id = 0
        self.department_reviewed.unique_id = 0
        db.session.commit()
        with patch('app.blueprints.admin.statistics.render_template', return_value='ok') as render:
            self.client.get('/admin/statistics?department=甲部门')
        self.assertEqual(render.call_args.kwargs['logical_form_count'], 2)

    def test_review_queue_serializes_server_authoritative_reviewability(self):
        newer = self._create_form(
            self.listener_a,
            status='部门已审核',
            lecture_date='2026-08-04',
            teacher_name='教师丁',
            teacher_college='学院甲',
            created_at=datetime(2026, 8, 4, 9, 0),
        )
        newer.unique_id = self.department_reviewed.unique_id
        db.session.commit()
        response = self.client.get('/admin/api/review/forms')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        by_id = {
            form['id']: form['can_review']
            for group in payload['forms']
            for form in group['forms']
        }
        # 中心级可对待审核表单强制直审；非最新版本与已驳回状态仍不可审。
        self.assertTrue(by_id[self.pending.id])
        self.assertFalse(by_id[self.department_reviewed.id])
        self.assertTrue(by_id[newer.id])
        self.assertFalse(by_id[self.rejected.id])

        detail = self.client.get(f'/admin/api/review/form/{self.department_reviewed.id}')
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.get_json()['form']['can_review'])


if __name__ == '__main__':
    unittest.main()
