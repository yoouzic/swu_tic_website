import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import Department, Group, LectureForm, Permission, RolePermission, User, db
from app.review_automation.models import ReviewAssessment
from app.utils.review_permissions import can_review_status
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

from tools.business_acceptance.human_flow import (
    BusinessActor,
    HumanFlowError,
    build_first_stage_actions,
    first_stage_action,
    form_data_for_resubmission,
    run_route_backed_human_flow,
    version_ids,
)
from tools.business_acceptance.cli import _build_parser


ROLE_INFO = '信息员'
ROLE_ADMIN = '管理员'
ROLE_SUPER = '超级管理员'
PERMISSION_GROUP = '审表_小组'
PERMISSION_DEPARTMENT = '审表_部门'
PERMISSION_CENTER = '审表_中心'
SPECIAL_PREFIX = '特殊角色_'
STATUS_PENDING = '待审核'
STATUS_DEPARTMENT = '部门已审核'
STATUS_REJECTED = '已驳回'
STATUS_CENTER = '中心已审核'


class BusinessAcceptanceHumanFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='human-flow-')
        self.db_path = Path(self.temp_dir.name) / 'human-flow.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(
            TESTING=True,
            AUTOMATION_UPLOAD_DIR=str(Path(self.temp_dir.name) / 'uploads'),
        )
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.password = 'acceptance-test-password'
        self.departments = []
        self.groups = {}
        self.officers = []
        self.group_admins = []
        self.department_admins = []
        self._create_structure()
        self.forms = self._create_forms()
        self.actors = {
            'groups': [
                BusinessActor(app, user.student_id, self.password)
                for user in self.group_admins
            ],
            'departments': [
                BusinessActor(app, user.student_id, self.password)
                for user in self.department_admins
            ],
            'center': BusinessActor(app, self.center_admin.student_id, self.password),
            'super': BusinessActor(app, self.super_admin.student_id, self.password),
        }

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _create_structure(self):
        permissions = {
            name: Permission(name=name, description='acceptance test permission')
            for name in (PERMISSION_GROUP, PERMISSION_DEPARTMENT, PERMISSION_CENTER)
        }
        db.session.add_all(permissions.values())
        for department_index in range(4):
            department = Department(name=f'acceptance-dept-{department_index}', description='test')
            db.session.add(department)
            self.departments.append(department)
        db.session.flush()

        for department_index, department in enumerate(self.departments):
            for group_index in range(2):
                group = Group(
                    name=f'acceptance-group-{department_index}-{group_index}',
                    department=department.name,
                    description='test',
                    max_members=100,
                )
                db.session.add(group)
                self.groups[(department_index, group_index)] = group
        db.session.flush()

        for department_index, department in enumerate(self.departments):
            group = self.groups[(department_index, 0)]
            self.group_admins.append(self._user(
                f'G{department_index}', f'group-admin-{department_index}',
                ROLE_ADMIN, department.name, group.name, group.id,
            ))
            self.department_admins.append(self._user(
                f'D{department_index}', f'department-admin-{department_index}',
                ROLE_ADMIN, department.name, group.name, group.id,
            ))

        self.center_admin = self._user(
            'C0', 'center-admin', ROLE_ADMIN,
            self.departments[0].name, self.groups[(0, 0)].name, self.groups[(0, 0)].id,
        )
        self.super_admin = self._user(
            'S0', 'super-admin', ROLE_SUPER,
            self.departments[0].name, self.groups[(0, 0)].name, self.groups[(0, 0)].id,
        )

        for department_index, department in enumerate(self.departments):
            for group_index in range(2):
                group = self.groups[(department_index, group_index)]
                for copy_index in range(2):
                    officer = self._user(
                        f'O{department_index}{group_index}{copy_index}',
                        f'officer-{department_index}-{group_index}-{copy_index}',
                        ROLE_INFO,
                        department.name,
                        group.name,
                        group.id,
                    )
                    self.officers.append(officer)

        db.session.flush()
        for admin, permission_name in (
            *[(admin, PERMISSION_GROUP) for admin in self.group_admins],
            *[(admin, PERMISSION_DEPARTMENT) for admin in self.department_admins],
            (self.center_admin, PERMISSION_CENTER),
            (self.super_admin, PERMISSION_CENTER),
        ):
            db.session.add(RolePermission(
                role=f'{SPECIAL_PREFIX}{admin.id}',
                permission_id=permissions[permission_name].id,
            ))
        db.session.commit()

    def _user(self, number, student_id, role, department, group, group_id):
        user = User(
            number=number,
            department=department,
            name=f'acceptance-{number}',
            gender='-',
            grade='acceptance',
            college=department,
            major='acceptance',
            dormitory='acceptance',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash(self.password),
            role=role,
            group=group,
            group_id=group_id,
            is_active=True,
        )
        db.session.add(user)
        return user

    def _create_forms(self):
        forms = []
        for index, officer in enumerate(self.officers):
            for copy_index in range(2):
                form = LectureForm(
                    listener_name=f'{officer.name}（acceptance）',
                    listener_number=officer.number,
                    course_changes='无',
                    lecture_date=f'2026-03-{index + copy_index + 2:02d}',
                    class_period='1-2',
                    lecture_location=f'acceptance-room-{index}-{copy_index}',
                    teacher_name=f'acceptance-teacher-{index}',
                    teacher_college=officer.department,
                    course_title=f'acceptance-course-{index}-{copy_index}',
                    student_grade_class='acceptance-class',
                    abnormal_situation='无',
                    teaching_method='讲解',
                    classroom_discipline='良好',
                    classroom_atmosphere='积极',
                    courseware_quality='清晰',
                    overall_effect='良好',
                    quality_case='推荐',
                    course_feedback='该老师讲解清楚，课堂组织完整，学生参与积极。',
                    suggestions='无',
                    student_signature1='验收同学一',
                    contact_phone1='-',
                    student_signature2='验收同学二',
                    contact_phone2='-',
                    status=STATUS_PENDING,
                    audit_tag='需要人工审核',
                )
                db.session.add(form)
                forms.append(form)
        db.session.flush()
        for form in forms:
            form.unique_id = form.id
        db.session.add(ReviewAssessment(
            form_id=forms[0].id,
            form_version='acceptance-v1',
            classification='建议复核',
            coverage='none',
            fingerprint='a' * 64,
            suggested_comment='自动审核建议：请人工复核。',
        ))
        db.session.commit()
        return forms

    def _forms_for_department(self, department_index):
        department_name = self.departments[department_index].name
        officer_departments = {user.number: user.department for user in self.officers}
        return [
            form for form in self.forms
            if officer_departments[form.listener_number] == department_name
        ]

    def _forms_for_group(self, department_index, group_index):
        group_id = self.groups[(department_index, group_index)].id
        officer_numbers = {
            user.number for user in self.officers if user.group_id == group_id
        }
        return [form for form in self.forms if form.listener_number in officer_numbers]

    def test_first_stage_action_covers_all_four_classifications(self):
        self.assertEqual(first_stage_action('无明显风险', 1), 'approve')
        self.assertEqual(first_stage_action('高风险疑似假表', 1), 'reject')
        self.assertEqual(first_stage_action('建议复核', 1), 'approve')
        self.assertEqual(first_stage_action('建议复核', 3), 'reject')
        self.assertEqual(first_stage_action('系统无法判断', 1), 'approve')
        self.assertEqual(first_stage_action('系统无法判断', 3), 'reject')

    def test_first_stage_plan_assigns_group_quota_and_all_categories_from_actual_results(self):
        rows = []
        classifications = {}
        categories = ('无明显风险', '建议复核', '高风险疑似假表', '系统无法判断')
        form_id = 1000
        for department_index in range(2):
            department = f'dept-{department_index}'
            for group_index in range(2):
                group = f'group-{group_index}'
                for ordinal in range(4):
                    form_id += 1
                    rows.append({
                        'form_id': form_id,
                        'unique_id': f'U-{form_id}',
                        'department': department,
                        'group': group,
                        'ordinal': form_id,
                    })
                    classifications[form_id] = categories[ordinal]

        actions = build_first_stage_actions(
            rows,
            classifications,
            group_admin_groups={
                'dept-0': 'group-0',
                'dept-1': 'group-0',
            },
            group_forms_per_department=4,
        )
        self.assertEqual(len(actions), len(rows))
        for department in ('dept-0', 'dept-1'):
            department_actions = [item for item in actions if item.department == department]
            group_actions = [item for item in department_actions if item.role == 'group']
            department_role_actions = [item for item in department_actions if item.role == 'department']
            self.assertEqual(len(group_actions), 4)
            self.assertEqual(len(department_role_actions), 4)
            self.assertEqual({item.category for item in group_actions}, set(categories))
            self.assertEqual({item.category for item in department_role_actions}, set(categories))
            self.assertEqual(
                {item.action for item in group_actions if item.category == '无明显风险'},
                {'approve'},
            )

    def test_route_runner_reassesses_repairs_and_leaves_half_final_rejections(self):
        categories = ('无明显风险', '建议复核', '高风险疑似假表', '系统无法判断')
        for index, form in enumerate(self.forms):
            existing = ReviewAssessment.query.filter_by(form_id=form.id).first()
            if existing is not None:
                existing.classification = categories[index % len(categories)]
                continue
            db.session.add(ReviewAssessment(
                form_id=form.id,
                form_version='acceptance-v1',
                classification=categories[index % len(categories)],
                coverage='none',
                fingerprint=f'{index + 1:064d}',
                suggested_comment=f'自动建议-{index}',
            ))
        db.session.commit()

        officers_by_number = {user.number: user for user in self.officers}
        specs = []
        for ordinal, form in enumerate(self.forms, start=1):
            officer = officers_by_number[form.listener_number]
            specs.append(SimpleNamespace(
                officer_id=officer.number,
                department=officer.department,
                group=officer.group,
                ordinal=ordinal,
                course_title=form.course_title,
                lecture_date=form.lecture_date,
                review_mode='rules_only',
            ))
        manifest = SimpleNamespace(forms=tuple(specs))
        reassessed = []

        result = run_route_backed_human_flow(
            app,
            manifest,
            self.password,
            admin_users={
                'group': self.group_admins,
                'department': self.department_admins,
                'center': [self.center_admin],
                'super': [self.super_admin],
            },
            reassess=lambda form_id, mode: reassessed.append((form_id, mode)),
            group_forms_per_department=4,
            final_rejection_every=4,
            final_repair_every=2,
        )

        self.assertEqual(result['first_stage']['planned'], len(self.forms))
        self.assertGreater(result['first_stage']['rejected'], 0)
        self.assertEqual(
            result['first_stage']['resubmitted'],
            result['first_stage']['rejected'],
        )
        self.assertGreater(result['final']['center_actions'], 0)
        self.assertGreater(result['final']['super_actions'], 0)
        self.assertEqual(result['final']['repaired'], 4)
        self.assertEqual(result['final']['left_rejected'], 4)
        self.assertEqual(
            len(reassessed),
            result['first_stage']['rejected'] + result['final']['repaired'],
        )

    def test_cli_exposes_route_backed_human_flow_command(self):
        args = _build_parser().parse_args([
            'run-human-flow',
            '--manifest',
            'acceptance/manifest.json',
        ])
        self.assertEqual(args.command, 'run-human-flow')
        self.assertEqual(str(args.manifest), 'acceptance\\manifest.json')

    def test_production_status_gate_matches_the_four_stage_business_flow(self):
        for admin in (*self.group_admins, *self.department_admins):
            with self.subTest(role='first-stage', admin=admin.number):
                self.assertTrue(can_review_status(admin.id, STATUS_PENDING))
                self.assertFalse(can_review_status(admin.id, STATUS_DEPARTMENT))
                self.assertFalse(can_review_status(admin.id, STATUS_CENTER))
                self.assertFalse(can_review_status(admin.id, STATUS_REJECTED))

        for admin in (self.center_admin, self.super_admin):
            with self.subTest(role='final-stage', admin=admin.number):
                self.assertTrue(can_review_status(admin.id, STATUS_DEPARTMENT))
                self.assertFalse(can_review_status(admin.id, STATUS_PENDING))
                self.assertFalse(can_review_status(admin.id, STATUS_CENTER))
                self.assertFalse(can_review_status(admin.id, STATUS_REJECTED))

    def test_all_first_stage_admins_login_and_have_exact_group_or_department_scope(self):
        for department_index in range(4):
            group_actor = self.actors['groups'][department_index]
            department_actor = self.actors['departments'][department_index]
            with self.subTest(role='group', department=department_index):
                self.assertIn(group_actor.login().status_code, {200, 302})
                expected = {form.id for form in self._forms_for_group(department_index, 0)}
                self.assertEqual(set(group_actor.list_review_form_ids()), expected)
            with self.subTest(role='department', department=department_index):
                self.assertIn(department_actor.login().status_code, {200, 302})
                expected = {form.id for form in self._forms_for_department(department_index)}
                self.assertEqual(set(department_actor.list_review_form_ids()), expected)

        center = self.actors['center']
        super_admin = self.actors['super']
        self.assertIn(center.login().status_code, {200, 302})
        self.assertIn(super_admin.login().status_code, {200, 302})
        expected_global = {form.id for form in self.forms}
        self.assertEqual(set(center.list_review_form_ids()), expected_global)
        self.assertEqual(set(super_admin.list_review_form_ids()), expected_global)

    def test_group_and_department_actions_keep_manual_fields_and_final_review_requires_department_stage(self):
        group_actor = self.actors['groups'][0]
        department_actor = self.actors['departments'][0]
        center_actor = self.actors['center']
        super_actor = self.actors['super']
        for actor in (group_actor, department_actor, center_actor, super_actor):
            self.assertIn(actor.login().status_code, {200, 302})

        group_forms = self._forms_for_group(0, 0)
        detail = group_actor.get_form(group_forms[0].id)
        assessment = center_actor.client.get(
            f"/admin/api/automation/assessments/{detail['automation']['assessment_id']}"
        ).get_json()['assessment']
        self.assertEqual(
            assessment['suggested_comment'],
            '自动审核建议：请人工复核。',
        )
        with self.assertRaisesRegex(HumanFlowError, 'department-approved'):
            center_actor.approve(group_forms[2].id, '中心管理员不得直审待审核表单')
        self.assertEqual(self._snapshot_model(group_forms[2].id).status, STATUS_PENDING)
        group_approved = group_actor.approve(group_forms[0].id, '小组管理员验收通过')
        self.assertEqual(group_approved.status, STATUS_DEPARTMENT)
        self.assertEqual(group_approved.review_comment, '小组管理员验收通过')
        self.assertEqual(group_approved.reviewer_id, group_actor.user_id)
        self.assertIsNotNone(group_approved.review_time)
        self.assertNotEqual(group_approved.review_comment, '自动审核建议：请人工复核。')

        original_rejected = self._snapshot_model(group_forms[1].id)
        rejected = group_actor.reject(group_forms[1].id, '小组管理员驳回，请修改后重交')
        self.assertEqual(rejected.status, STATUS_REJECTED)
        self.assertNotEqual(rejected.id, original_rejected.id)
        self.assertEqual(rejected.review_comment, '小组管理员驳回，请修改后重交')
        self.assertEqual(rejected.reviewer_id, group_actor.user_id)
        self.assertEqual(self._snapshot_model(original_rejected.id).status, STATUS_PENDING)

        officer = next(user for user in self.officers if user.number == rejected.listener_number)
        officer_actor = BusinessActor(app, officer.student_id, self.password)
        self.assertIn(officer_actor.login().status_code, {200, 302})
        repaired = form_data_for_resubmission(rejected)
        repaired['course_feedback'] = '该老师讲解清楚，课堂互动自然，学生理解良好。'
        resubmitted = officer_actor.resubmit(rejected.id, repaired)
        self.assertEqual(resubmitted.status, STATUS_PENDING)
        self.assertNotEqual(resubmitted.id, rejected.id)
        self.assertEqual(resubmitted.unique_id, rejected.unique_id)
        self.assertEqual(version_ids(rejected.unique_id), sorted(version_ids(rejected.unique_id)))
        department_repaired = department_actor.approve(resubmitted.id, '部门管理员复核返修通过')
        self.assertEqual(department_repaired.status, STATUS_DEPARTMENT)

        department_forms = self._forms_for_group(0, 1)
        department_approved = department_actor.approve(department_forms[0].id, '部门管理员验收通过')
        self.assertEqual(department_approved.status, STATUS_DEPARTMENT)
        department_rejected = department_actor.reject(
            department_forms[1].id,
            '部门管理员驳回，请补充具体听课评价后重交',
        )
        self.assertEqual(department_rejected.status, STATUS_REJECTED)
        self.assertNotEqual(department_rejected.id, department_forms[1].id)

        center_result = center_actor.approve(
            department_approved.id,
            '中心管理员最终验收通过',
        )
        self.assertEqual(center_result.status, STATUS_CENTER)
        self.assertEqual(center_result.reviewer_id, center_actor.user_id)

        super_source = department_actor.approve(
            self._forms_for_group(0, 1)[2].id,
            '部门管理员为超级管理员准备终审样本',
        )
        self.assertEqual(super_source.status, STATUS_DEPARTMENT)
        super_result = super_actor.approve(
            super_source.id,
            '超级管理员最终验收通过',
        )
        self.assertEqual(super_result.status, STATUS_CENTER)
        self.assertEqual(super_result.reviewer_id, super_actor.user_id)

    def _snapshot_model(self, form_id):
        form = db.session.get(LectureForm, int(form_id))
        self.assertIsNotNone(form)
        return form


if __name__ == '__main__':
    unittest.main()
