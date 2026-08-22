import os
import re
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash


TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'ui_consistency_edge_cases.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'

from app.app import app
from app.blueprints.admin import _build_user_profile_stats
from app.models import User, db
from app.utils import review_permissions
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


REVIEW_TEMPLATE = Path('app/templates/admin/review_forms.html')
MEMBER_TEMPLATE = Path('app/templates/admin/_user_detail_panel.html')
ADMIN_BLUEPRINT = Path('app/blueprints/admin.py')
AUTH_BLUEPRINT = Path('app/blueprints/auth.py')
STYLE = Path('app/static/css/style.css')
PEOPLE_TEMPLATE = Path('app/templates/admin/manage_departments.html')
METRIC_TEMPLATE = Path('app/templates/partials/_metric.html')
WORKSPACE_TEMPLATE = Path('app/templates/main/workspace.html')
SYSTEM_IMPORTS_TEMPLATE = Path('app/templates/admin/_settings_imports.html')


class UIConsistencyStaticContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = REVIEW_TEMPLATE.read_text(encoding='utf-8')
        cls.member = MEMBER_TEMPLATE.read_text(encoding='utf-8')
        cls.admin = ADMIN_BLUEPRINT.read_text(encoding='utf-8')
        cls.auth = AUTH_BLUEPRINT.read_text(encoding='utf-8')
        cls.style = STYLE.read_text(encoding='utf-8')
        cls.people = PEOPLE_TEMPLATE.read_text(encoding='utf-8')
        cls.metric = METRIC_TEMPLATE.read_text(encoding='utf-8')
        cls.workspace = WORKSPACE_TEMPLATE.read_text(encoding='utf-8')
        cls.system_imports = SYSTEM_IMPORTS_TEMPLATE.read_text(encoding='utf-8')

    def test_system_destructive_confirmation_identifies_the_current_password(self):
        self.assertIn('account_username=user.student_id', self.admin)
        self.assertIn(
            'id="clearDataUsername" name="username" value="{{ account_username }}" autocomplete="username"',
            self.system_imports,
        )
        self.assertIn(
            'id="passwordInput" name="password" autocomplete="current-password"',
            self.system_imports,
        )

    def test_workspace_metrics_request_the_plain_metric_variant(self):
        self.assertIn("variant='card'", self.metric)
        self.assertIn('metric--{{ variant }}', self.metric)
        self.assertIn("variant='plain'", self.workspace)
        self.assertIn('.metric--plain', self.style)

    def test_review_scope_toolbar_responds_to_its_component_width(self):
        start = self.review.index('<div class="review-scope-toolbar')
        end = self.review.index('id="userStructure"', start)
        toolbar = self.review[start:end]
        self.assertIn('review-scope-toolbar', toolbar)
        self.assertIn('review-scope-toolbar__search', toolbar)
        self.assertIn('review-scope-toolbar__actions', toolbar)
        self.assertNotIn('col-md-6', toolbar)
        self.assertIn('.review-scope-toolbar', self.style)
        self.assertIn('repeat(auto-fit,minmax(min(100%,18rem),1fr))', self.style)
        self.assertNotIn('一键全选全部门', self.review)

    def test_group_cards_stretch_to_their_bootstrap_column_height(self):
        self.assertIn('.group-card { display: flex;', self.style)
        self.assertIn('.group-card > .card', self.style)
        self.assertIn('height: 100%', self.style)

    def test_activity_tabs_keep_business_labels_intact_in_narrow_containers(self):
        self.assertIn('.activity-tabs::-webkit-scrollbar { display: none; }', self.style)
        self.assertRegex(
            self.style,
            r'\.activity-tabs \{[^}]*overflow-x: auto;[^}]*scrollbar-width: none;',
        )
        self.assertRegex(
            self.style,
            r'\.activity-tabs button \{[^}]*flex: 0 0 auto;[^}]*padding: 11px 10px;[^}]*white-space: nowrap;',
        )

    def test_high_frequency_ordinary_actions_use_primary_or_neutral_styles(self):
        def button_tag(source, marker):
            marker_index = source.index(marker)
            start = source.rfind('<button', 0, marker_index)
            end = source.index('>', marker_index) + 1
            return source[start:end]

        review_actions = {
            'id="btnExportSelectedForms"': 'btn-outline-secondary',
            'id="btnBatchDefineTags"': 'btn-outline-secondary',
            'id="btnAutoReview"': 'btn-primary',
            'onclick="checkSelectedExportData()"': 'btn-outline-secondary',
            'onclick="viewForm(${version.id})"': 'btn-outline-secondary',
            'data-open-review="${version.id}"': 'btn-primary',
        }
        people_actions = {
            'onclick="showMoveMembersModal(\'department\')"': 'btn-outline-secondary',
            'onclick="showMoveMembersModal(\'group\')"': 'btn-outline-secondary',
            'onclick="showPermissionModal()"': 'btn-outline-secondary',
            'data-department-action="edit"': 'btn-outline-secondary',
            'data-department-action="disband"': 'text-danger',
        }
        for marker, expected_class in review_actions.items():
            with self.subTest(template='review', marker=marker):
                self.assertIn(expected_class, button_tag(self.review, marker))
        for marker, expected_class in people_actions.items():
            with self.subTest(template='people', marker=marker):
                self.assertIn(expected_class, button_tag(self.people, marker))

        for ordinary_status_class in ('btn-success', 'class="btn btn-info"', 'btn-outline-info'):
            with self.subTest(template='people', ordinary_status_class=ordinary_status_class):
                self.assertNotIn(ordinary_status_class, self.people)
        self.assertNotIn("classList.toggle('btn-warning', actionLabel !== '删除')", self.people)

    def test_first_party_close_buttons_have_accessible_names(self):
        unnamed = []
        for path in Path('app/templates').rglob('*.html'):
            source = path.read_text(encoding='utf-8')
            for match in re.finditer(r'<button\b[^>]*>', source, flags=re.IGNORECASE | re.DOTALL):
                tag = match.group(0)
                classes = re.search(r'class\s*=\s*(["\'])(.*?)\1', tag, flags=re.IGNORECASE | re.DOTALL)
                if not classes or 'btn-close' not in classes.group(2).split():
                    continue
                if not re.search(r'aria-(?:label|labelledby)\s*=', tag, flags=re.IGNORECASE):
                    line = source[:match.start()].count('\n') + 1
                    unnamed.append(f'{path}:{line}')
        self.assertEqual([], unnamed, f'Unnamed first-party btn-close controls: {unnamed}')

    def test_member_drawer_uses_component_scoped_layout(self):
        self.assertIn('class="member-detail-layout"', self.member)
        self.assertIn('class="member-detail-filters"', self.member)
        self.assertIn('container-type: inline-size', self.style)
        self.assertIn('@container member-detail', self.style)

        filter_start = self.member.index('<form')
        filter_end = self.member.index('</form>', filter_start)
        filter_markup = self.member[filter_start:filter_end]
        for viewport_grid_class in ('col-md-2', 'col-md-3', 'col-md-4'):
            self.assertNotIn(viewport_grid_class, filter_markup)

    def test_member_drawer_uses_neutral_surfaces_and_truthful_state_hooks(self):
        for legacy_surface in (
            'card-header bg-primary',
            'card-header bg-info',
            'card-header bg-secondary',
            'card-header bg-warning',
            'card shadow',
        ):
            with self.subTest(legacy_surface=legacy_surface):
                self.assertNotIn(legacy_surface, self.member)
        for state_key in (
            'current_week_label',
            'deduction_display',
            'has_evaluation_sample',
            'rating_label',
        ):
            self.assertIn(state_key, self.member)
        self.assertNotIn("-{{ '%.2f'|format(stats.total_deduction or 0) }}", self.member)

    def test_member_drawer_filter_refreshes_the_fragment_in_place(self):
        self.assertIn('data-member-detail-filter', self.member)
        self.assertIn("form.matches('[data-member-detail-filter]')", self.people)
        self.assertIn("params.set('format', 'fragment')", self.people)
        self.assertIn('body.dataset.userId', self.people)

    def test_review_main_queue_is_a_summary_list_not_a_fixed_table(self):
        self.assertIn('class="review-queue__list"', self.review)
        self.assertIn('class="review-queue__select-all"', self.review)
        self.assertNotIn('class="table review-queue__table"', self.review)
        self.assertNotIn('table-layout: fixed', self.style)
        self.assertIn('class="review-item"', self.review)

    def test_review_scope_uses_product_language_and_one_search_control(self):
        self.assertIn('permission_label', self.review)
        self.assertIn('scope_label', self.review)
        self.assertNotIn('`审核权限：${data.permission}`', self.review)
        self.assertNotIn('`当前权限：${currentPermission}`', self.review)
        self.assertNotIn('id="scopeSearchBtn"', self.review)

    def test_review_statistics_modal_uses_neutral_structural_surfaces(self):
        start = self.review.index('function renderStatistics(')
        end = self.review.index('function renderUserList(', start)
        renderer = self.review[start:end]
        self.assertIn('review-statistics-range', renderer)
        self.assertIn('review-statistics-card', renderer)
        for decorative_surface in (
            'alert-primary',
            'border-success',
            'bg-success text-white',
            'border-warning',
            'bg-warning text-dark',
        ):
            with self.subTest(decorative_surface=decorative_surface):
                self.assertNotIn(decorative_surface, renderer)

    def test_review_statistics_range_separates_label_and_date_tokens(self):
        start = self.review.index('function renderStatistics(')
        end = self.review.index('function renderUserList(', start)
        renderer = self.review[start:end]

        self.assertIn('review-statistics-range__label', renderer)
        self.assertIn('review-statistics-range__value', renderer)
        self.assertIn('review-statistics-range__date', renderer)
        self.assertNotIn('当前统计范围：${rangeText}', renderer)

    def test_people_disband_actions_live_in_accessible_more_menus(self):
        department_start = self.people.index('const standardButtonsHtml')
        department_end = self.people.index('const emptyButtonsHtml', department_start)
        department_actions = self.people[department_start:department_end]
        self.assertIn('dropdown department-card__menu', department_actions)
        self.assertIn('dropdown-item text-danger', department_actions)
        self.assertIn('data-department-action="disband"', department_actions)
        self.assertNotIn('btn-outline-danger', department_actions)

        group_start = self.people.index(
            'const buttonsHtml',
            self.people.index('function createGroupCard'),
        )
        group_end = self.people.index('return `', group_start)
        group_actions = self.people[group_start:group_end]
        self.assertIn('dropdown group-card__menu', group_actions)
        self.assertIn('dropdown-item text-danger', group_actions)
        self.assertIn('data-group-action="disband"', group_actions)
        self.assertNotIn('btn-outline-danger', group_actions)

        self.assertIn('function disbandDepartment(id, name)', self.people)
        self.assertIn('function disbandGroup(id, name)', self.people)
        self.assertIn('body: JSON.stringify({ password: password })', self.people)

    def test_permission_feedback_has_no_context_free_literals_in_core_routes(self):
        self.assertNotIn("flash('权限不足'", self.auth)
        self.assertNotIn("'message': '权限不足'", self.auth)
        self.assertNotIn("flash('权限不足'", self.admin)
        self.assertNotIn("'message': '权限不足'", self.admin)

    def test_people_password_controls_have_form_and_autocomplete_semantics(self):
        self.assertIn('id="confirmDeleteForm"', self.people)
        self.assertIn('id="deleteUsername" name="username" autocomplete="username"', self.people)
        self.assertIn('id="deletePassword" name="password" autocomplete="current-password"', self.people)
        self.assertIn('id="userStudentId" name="student_id" autocomplete="username"', self.people)
        self.assertIn('id="userPassword" name="password" autocomplete="new-password"', self.people)


class UIConsistencyRouteContractTest(unittest.TestCase):
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
        self.super_admin = self._create_user(
            student_id='super',
            number='SA001',
            name='中心管理员',
            role='超级管理员',
            department='中心',
        )
        self.listener = self._create_user(
            student_id='listener',
            number='U001',
            name='空样本成员',
            role='信息员',
            department='办公部',
        )
        db.session.commit()

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

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def test_empty_member_profile_does_not_invent_week_score_or_rating(self):
        stats = _build_user_profile_stats(self.listener)
        self.assertIsNone(stats['current_week'])
        self.assertEqual(stats['current_week_label'], '当前不在教学周内')
        self.assertEqual(stats['deduction_display'], '0.00')
        self.assertFalse(stats['has_evaluation_sample'])
        self.assertEqual(stats['rating_label'], '暂无评级')

    def test_review_permission_api_keeps_internal_value_and_adds_product_labels(self):
        self._login(self.super_admin)
        response = self.client.get('/admin/api/review/permission')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['permission'], '审表_中心')
        self.assertIn('permission_label', payload)
        self.assertIn('scope_label', payload)
        if 'permission_label' not in payload or 'scope_label' not in payload:
            return
        self.assertEqual(payload['permission_label'], '中心级审核')
        self.assertEqual(payload['scope_label'], '审核范围：全中心')

    def test_review_permission_presentation_covers_every_internal_level(self):
        get_review_permission_presentation = getattr(
            review_permissions,
            'get_review_permission_presentation',
            None,
        )
        self.assertIsNotNone(get_review_permission_presentation)
        if get_review_permission_presentation is None:
            return
        self.assertEqual(
            get_review_permission_presentation('审表_小组'),
            {'permission_label': '小组级审核', 'scope_label': '审核范围：本小组'},
        )
        self.assertEqual(
            get_review_permission_presentation('审表_部门'),
            {'permission_label': '部门级审核', 'scope_label': '审核范围：本部门'},
        )
        self.assertEqual(
            get_review_permission_presentation(None),
            {'permission_label': '无审核权限', 'scope_label': '审核范围：不可用'},
        )

    def test_role_guard_names_the_resource_in_html_feedback(self):
        self._login(self.listener)
        response = self.client.get('/admin/manage_departments', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('无法打开“人员与部门”'.encode('utf-8'), response.data)
        self.assertIn('当前账号没有该功能权限'.encode('utf-8'), response.data)


if __name__ == '__main__':
    unittest.main()
