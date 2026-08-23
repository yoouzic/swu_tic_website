import json
import os
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.app import app
from app.blueprints.admin import (
    _course_model_key,
    _course_row_key,
    _resolve_group_id_for_import,
)
from app.models import (
    Course,
    Department,
    Group,
    LectureForm,
    Permission,
    RolePermission,
    SystemSetting,
    User,
    db,
)
from app.utils.review_permissions import get_reviewable_users
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

ROLE_INFO = '信息员'
ROLE_ADMIN = '管理员'
ROLE_SUPER = '超级管理员'


class FunctionalSecurityFixesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='functional-security-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'functional-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self._create_org()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _create_org(self):
        self.permissions = {}
        for name in ('审表_小组', '审表_部门', '审表_中心', '管理部门', '管理部门小组', '填表'):
            perm = Permission(name=name, description='test')
            db.session.add(perm)
            self.permissions[name] = perm
        db.session.flush()
        self.dept_a = Department(name='办公部')
        self.dept_b = Department(name='策划部')
        db.session.add_all([self.dept_a, self.dept_b])
        db.session.flush()
        self.group_a = Group(name='一组', department=self.dept_a.name)
        self.group_b = Group(name='一组', department=self.dept_b.name)
        db.session.add_all([self.group_a, self.group_b])
        db.session.flush()
        self.officer_a = self._user('A1', 's-a1', ROLE_INFO, self.dept_a.name, self.group_a.name, self.group_a.id)
        self.officer_b = self._user('B1', 's-b1', ROLE_INFO, self.dept_b.name, self.group_b.name, self.group_b.id)
        self.dept_manager = self._user(
            'D1', 's-d1', ROLE_ADMIN, self.dept_a.name, self.group_a.name, self.group_a.id,
            perms={'管理部门', '审表_部门'},
        )
        self.group_manager = self._user(
            'G1', 's-g1', ROLE_ADMIN, self.dept_a.name, self.group_a.name, self.group_a.id,
            perms={'管理部门小组', '审表_小组'},
        )
        self.center_manager = self._user(
            'C1', 's-c1', ROLE_ADMIN, self.dept_a.name, self.group_a.name, self.group_a.id,
            perms={'审表_中心'},
        )
        db.session.commit()

    def _user(self, number, student_id, role, department, group, group_id, perms=None):
        user = User(
            number=number,
            department=department,
            name=f'User {number}',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash('password'),
            role=role,
            group=group,
            group_id=group_id,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        for name in (perms or set()):
            db.session.add(RolePermission(
                role=f'特殊角色_{user.id}',
                permission_id=self.permissions[name].id,
            ))
        return user

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _form(self, owner, status='待审核', unique_id=None):
        form = LectureForm(
            listener_name=f'{owner.name}（Test College）',
            listener_number=owner.number,
            course_changes='无',
            lecture_date='2026/06/04星期四',
            class_period='第3-4节',
            lecture_location='32-302',
            teacher_name='Teacher A',
            teacher_college='Test College',
            course_title='Database Systems',
            student_grade_class='2024 Test Class',
            abnormal_situation='无',
            teaching_method='PPT演示法',
            classroom_discipline='好',
            classroom_atmosphere='好',
            courseware_quality='好',
            overall_effect='好',
            quality_case='推荐',
            course_feedback='This feedback is long enough for the automated review.',
            suggestions='无',
            student_signature1='Student One',
            contact_phone1='13800000001',
            student_signature2='Student Two',
            contact_phone2='13800000002',
            status=status,
            audit_tag='需要人工审核',
        )
        db.session.add(form)
        db.session.flush()
        if unique_id is None:
            form.unique_id = None
        else:
            form.unique_id = unique_id
        db.session.commit()
        return form

    def _review_payload(self, form):
        return {
            'form_data': {
                'listener_name': form.listener_name,
                'listener_number': form.listener_number,
                'lecture_date': '2026/06/04',
                'class_period': '第3-4节',
                'lecture_location': '32-302',
                'teacher_name': 'Teacher A',
                'teacher_college': 'Test College',
                'course_title': 'Database Systems',
                'student_grade_class': '2024 Test Class',
                'course_changes': '无',
                'abnormal_situation': '无',
                'teaching_method': 'PPT演示法',
                'classroom_discipline': '好',
                'classroom_atmosphere': '好',
                'courseware_quality': '好',
                'overall_effect': '好',
                'quality_case': '推荐',
                'course_feedback': 'This feedback is long enough for the automated review.',
                'suggestions': '无',
                'student_signature1': 'Student One',
                'contact_phone1': '13800000001',
                'student_signature2': 'Student Two',
                'contact_phone2': '13800000002',
            },
            'review_comment': '通过',
            'score_data': [],
        }

    # P1-7: semester config
    def test_registration_api_blocks_when_semester_not_configured(self):
        self._login(self.officer_a)
        response = self.client.post('/user/api/create_reservation', json={
            'course_code': 'C1',
            'selection_code': 'S1',
            'listening_info': '第1周星期一第1节',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('系统设置', response.get_json()['message'])

    def test_registration_page_disables_button_when_semester_not_configured(self):
        self._login(self.officer_a)
        response = self.client.get('/user/listening_registration')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('配置学期起始周', html)
        self.assertIn('id="submitReservationButton"', html)
        self.assertIn('disabled', html)

    # P1-10: unique_id NULL fallback
    def test_submit_review_handles_unique_id_null(self):
        self._login(self.dept_manager)
        form = self._form(self.officer_a, unique_id=None)
        response = self.client.post(f'/admin/api/review/submit/{form.id}', json=self._review_payload(form))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['new_status'], '部门已审核')

    def test_reject_handles_unique_id_null(self):
        self._login(self.dept_manager)
        form = self._form(self.officer_a, unique_id=None)
        response = self.client.post(f'/admin/api/review/reject/{form.id}', json={'reason': 'no'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['new_status'], '已驳回')

    # P1-11: contact import group_id sync
    def test_contact_import_helper_syncs_group_id_and_scopes_review(self):
        user = self._user('NEW1', 's-new1', ROLE_INFO, self.dept_a.name, '新小组', None)
        db.session.commit()
        group_id = _resolve_group_id_for_import(user)
        user.group_id = group_id
        db.session.commit()
        self.assertIsNotNone(group_id)
        self.assertIsNotNone(user.group_id)
        self.assertIn(self.officer_a.id, get_reviewable_users(self.group_manager.id))
        # A department-scoped manager would still see the new user after group sync.
        self.assertIn(user.id, get_reviewable_users(self.dept_manager.id))

    # P1-12: schedule natural key
    def test_schedule_import_idempotent_and_seeks_stream(self):
        import openpyxl
        from app.models import Course

        headers = [
            '教工号', '姓名', '性别', '职称名称', '教师所属学院', '教师联系电话',
            '场地编号', '场地名称', '场地类别名称', '校区', '楼层号', '教学楼', '座位数',
            '课程号', '选课课号', '起始周', '星期几', '上课节次', '课程名称',
            '场地上课起始周', '场地上课节次', '教学班人数', '教学班组成', '学分', '总学时',
            '开课学院', '专业组成', '选课人数', '周学时', '上课时间', '上课地点',
            '课程性质', '学期', '学年',
        ]
        row = [
            'T001', 'Teacher A', '男', '教授', '理学院', '13900000000',
            'V001', 'A101', '教学楼', '北碚', '1', 'A', '100',
            'C001', 'S001', '1-16', '3', '第3-4节', 'Math',
            '1-16', '第3-4节', '30', '行政班1', '2', '32',
            '理学院', '数学', '25', '2', '周三第3-4节', 'A101',
            '必修', '2025-2026-1', '2025',
        ]
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        ws.append(row)
        template_path = Path(self.temp_dir.name) / 'schedule_template.xlsx'
        wb.save(template_path)
        os.environ['SCHEDULE_TEMPLATE_PATH'] = str(template_path)

        super_admin = self._user('SA9', 's-sa9', ROLE_SUPER, self.dept_a.name, self.group_a.name, self.group_a.id)
        db.session.commit()
        self._login(super_admin)

        with open(template_path, 'rb') as handle:
            first = self.client.post(
                '/admin/api/schedule/import',
                data={'file': (handle, 'schedule.xlsx')},
                content_type='multipart/form-data',
            )
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertTrue(first.get_json().get('success'))
        self.assertEqual(first.get_json()['stats']['courses_added'], 1)

        with open(template_path, 'rb') as handle:
            second = self.client.post(
                '/admin/api/schedule/import',
                data={'file': (handle, 'schedule.xlsx')},
                content_type='multipart/form-data',
            )
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        stats = second.get_json()['stats']
        self.assertEqual(stats['courses_added'], 0)
        self.assertEqual(stats['courses_skipped_duplicate'], 1)
        self.assertEqual(Course.query.count(), 1)

    def test_schedule_natural_key_keeps_different_slots_and_dedupes_identical_rows(self):
        row_a = {
            '课程号': 'C001', '选课课号': 'S001', '起始周': '1-16', '星期几': 3,
            '上课节次': '第3-4节', '课程名称': 'Math', '场地上课起始周': '1-16',
            '场地上课节次': '第3-4节', '教学班人数': 30, '教学班组成': '行政班1',
            '学分': 2, '总学时': 32, '开课学院': '理学院', '专业组成': '数学',
            '选课人数': 25, '周学时': 2, '上课时间': '周三第3-4节', '上课地点': 'A101',
            '课程性质': '必修', '教工号': 'T001', '场地编号': 'V001', '学期': '2025-1',
            '学年': '2025',
        }
        same = dict(row_a)
        different = dict(row_a)
        different['上课节次'] = '第5-6节'
        different['上课时间'] = '周三第5-6节'
        key_a = _course_row_key(__import__('pandas').Series(row_a))
        key_b = _course_row_key(__import__('pandas').Series(same))
        key_c = _course_row_key(__import__('pandas').Series(different))
        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)

    # P1-13: permission elevation / department permissions
    def test_department_admin_cannot_grant_center_review(self):
        self._login(self.dept_manager)
        target = self._user('T1', 's-t1', ROLE_INFO, self.dept_a.name, self.group_a.name, self.group_a.id)
        db.session.commit()
        response = self.client.put(
            f'/admin/api/users/{target.id}/permissions',
            json={'permission_ids': [self.permissions['审表_中心'].id]},
        )
        self.assertEqual(response.status_code, 403)

    def test_department_admin_can_grant_same_level_review(self):
        self._login(self.dept_manager)
        target = self._user('T2', 's-t2', ROLE_INFO, self.dept_a.name, self.group_a.name, self.group_a.id)
        db.session.commit()
        response = self.client.put(
            f'/admin/api/users/{target.id}/permissions',
            json={'permission_ids': [self.permissions['审表_部门'].id]},
        )
        self.assertEqual(response.status_code, 200)

    def test_group_manager_cannot_update_department(self):
        self._login(self.group_manager)
        response = self.client.put(
            f'/admin/api/departments/{self.dept_a.id}',
            json={'name': self.dept_a.name, 'description': 'x'},
        )
        self.assertEqual(response.status_code, 403)

    def test_department_update_rejects_cross_department_manager(self):
        self._login(self.dept_manager)
        response = self.client.put(
            f'/admin/api/departments/{self.dept_a.id}',
            json={'name': self.dept_a.name, 'manager_id': self.officer_b.id},
        )
        self.assertEqual(response.status_code, 400)


class XssStaticRegressionTest(unittest.TestCase):
    def test_review_form_no_unprotected_previous_sibling_label_read(self):
        path = Path('app/templates/admin/review_form.html').read_text(encoding='utf-8')
        self.assertNotIn('previousElementSibling.textContent', path)

    def test_lecture_form_uses_tojson_instead_of_safe_dumps(self):
        path = Path('app/templates/user/lecture_form.html').read_text(encoding='utf-8')
        self.assertIn('tojson', path)
        self.assertNotIn('form_data_json|safe', path)

    def test_manage_departments_uses_data_actions_for_dynamic_names(self):
        path = Path('app/templates/admin/manage_departments.html').read_text(encoding='utf-8')
        self.assertIn('data-user-action="depart"', path)
        self.assertIn('data-group-action="disband"', path)
        self.assertNotIn("onclick=\"departUser(${user.id}, '${user.name}')\"", path)
        self.assertNotIn("onclick=\"deleteUser(${user.id}, '${user.name}')\"", path)

    def test_review_forms_escapes_version_comment_and_structure_names(self):
        path = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertIn('escapeHtml(version.review_comment', path)
        self.assertIn('escapeHtml(deptName)', path)
        self.assertIn('escapeHtml(groupName)', path)
        self.assertIn('escapeHtml(user.name)', path)

    def test_local_date_helpers_no_utc_iso(self):
        for file in (
            Path('app/templates/admin/review_forms.html'),
            Path('app/static/js/activity-center.js'),
            Path('app/templates/user/my_forms.html'),
        ):
            text = file.read_text(encoding='utf-8')
            self.assertNotIn("toISOString().split('T')[0]", text)

    def test_polling_lifecycle_guards_are_present(self):
        automation = Path('app/static/js/automation-center.js').read_text(encoding='utf-8')
        self.assertIn('clearBatchPollTimer', automation)
        self.assertIn('activeBatchId', automation)
        manage = Path('app/templates/admin/manage_departments.html').read_text(encoding='utf-8')
        self.assertIn('const stopTimer', manage)
        self.assertIn('pollCount > 120', manage)
        review_forms = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertIn('ack.onchange', review_forms)

    def test_version_badge_and_default_user_selection_are_present(self):
        review_forms = Path('app/templates/admin/review_forms.html').read_text(encoding='utf-8')
        self.assertIn("toggleVersions('${groupCollapseId}', '${chevronId}')", review_forms)
        self.assertIn('默认选择当前允许范围内的全部可审核用户', review_forms)


if __name__ == '__main__':
    unittest.main()
