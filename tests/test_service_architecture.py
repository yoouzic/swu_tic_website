# -*- coding: utf-8 -*-
"""Architecture contracts for the new service layer."""
import ast
import unittest
from pathlib import Path


SERVICE_FILES = (
    Path('app/services/review_domain.py'),
    Path('app/services/review_mutation.py'),
    Path('app/services/form_bindings.py'),
    Path('app/services/review_scores.py'),
    Path('app/services/organization_membership.py'),
    Path('app/services/organization_policy.py'),
    Path('app/services/profile_stats.py'),
)

TEACHING_CALENDAR_FILE = Path('app/services/teaching_calendar.py')
TEACHING_CALENDAR_SETTINGS_FILE = Path('app/services/teaching_calendar_settings.py')
FORM_WEEK_SEMANTICS_FILE = Path('app/services/form_week_semantics.py')
SCHEDULE_SNAPSHOTS_FILE = Path('app/services/schedule_snapshots.py')
ACADEMIC_TERM_FILE = Path('app/services/academic_term.py')
REVIEW_SCHEDULE_SOURCE_FILE = Path('app/services/review_schedule_source.py')
REVIEW_SCHEDULE_MATCHER_FILE = Path('app/services/review_schedule_matcher.py')
REVIEW_REFERENCE_DATA_FILE = Path('app/services/review_reference_data.py')
ORGANIZATION_MEMBERSHIP_FILE = Path('app/services/organization_membership.py')
ORGANIZATION_POLICY_FILE = Path('app/services/organization_policy.py')
PROFILE_STATS_FILE = Path('app/services/profile_stats.py')

ADMIN_USERS_FILE = Path('app/blueprints/admin/users.py')

FORBIDDEN_IMPORT_PREFIXES = (
    'flask',
    'app.blueprints',
)

FORBIDDEN_NAMES = {
    'session', 'request', 'jsonify', 'abort', 'flash',
    'admin_bp', 'current_app', 'render_template', 'redirect', 'url_for',
}


class ServiceArchitectureTests(unittest.TestCase):
    def test_services_have_no_flask_or_blueprint_dependency(self):
        for service_file in SERVICE_FILES:
            tree = ast.parse(service_file.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.name
                        if name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                            self.fail(
                                f'{service_file.name} imports forbidden module: {name}'
                            )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                        self.fail(
                            f'{service_file.name} imports forbidden module: {module}'
                        )

    def test_services_do_not_reference_http_or_model_names(self):
        for service_file in SERVICE_FILES:
            tree = ast.parse(service_file.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id in FORBIDDEN_NAMES:
                        self.fail(
                            f'{service_file.name} references forbidden name: {node.id}'
                        )

    def test_services_have_no_wildcard_import(self):
        for service_file in SERVICE_FILES:
            tree = ast.parse(service_file.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        self.assertNotEqual(
                            alias.name, '*',
                            f'{service_file.name} contains wildcard import',
                        )

    def test_form_bindings_reconcile_primitive_is_commit_free(self):
        """Round 8A：reconcile_registration_usage_flags 是 transaction-local
        mirror primitive——不得 commit/rollback，事务所有权留给调用方。"""
        service_file = SERVICE_FILES[2]
        tree = ast.parse(service_file.read_text(encoding='utf-8'))
        reconcile_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == 'reconcile_registration_usage_flags'
        ]
        self.assertEqual(len(reconcile_defs), 1)
        for node in ast.walk(reconcile_defs[0]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(
                    node.func.attr, {'commit', 'rollback'},
                    'reconcile primitive must not own transaction boundaries',
                )

    def test_review_mutation_service_has_no_app_import(self):
        service_file = SERVICE_FILES[1]
        tree = ast.parse(service_file.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('app.'):
                        self.fail(
                            f'{service_file.name} imports app module: {alias.name}'
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if module.startswith('app.'):
                    self.fail(
                        f'{service_file.name} imports app module: {module}'
                    )

    def test_review_scores_service_has_no_model_or_sqlalchemy_import(self):
        service_file = SERVICE_FILES[3]
        tree = ast.parse(service_file.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('app.') or alias.name.startswith('sqlalchemy'):
                        self.fail(
                            f'{service_file.name} imports forbidden module: {alias.name}'
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if module.startswith('app.') or module.startswith('sqlalchemy'):
                    self.fail(
                        f'{service_file.name} imports forbidden module: {module}'
                    )

    def test_review_domain_service_exposes_narrow_public_api(self):
        tree = ast.parse(SERVICE_FILES[0].read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertIn('partition_forms_by_review_scope', functions)
        self.assertIn('is_form_in_review_scope', functions)

    def test_review_mutation_service_exposes_pure_primitives(self):
        tree = ast.parse(SERVICE_FILES[1].read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertIn('collect_modified_fields', functions)
        self.assertIn('append_review_modification_note', functions)
        self.assertIn('REVIEW_EDITABLE_FIELD_LABELS', {n.targets[0].id for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)})

    def test_teaching_calendar_core_has_no_app_model_or_flask_dependency(self):
        tree = ast.parse(TEACHING_CALENDAR_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask') or name.startswith('app.') or name.startswith('sqlalchemy'),
                        f'{TEACHING_CALENDAR_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask') or module.startswith('app.') or module.startswith('sqlalchemy'),
                    f'{TEACHING_CALENDAR_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(node.id, FORBIDDEN_NAMES | {'SystemSetting', 'db', 'LectureForm'})

    def test_teaching_calendar_settings_boundary_has_no_flask_or_blueprint_dependency(self):
        tree = ast.parse(TEACHING_CALENDAR_SETTINGS_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask') or name.startswith('app.blueprints'),
                        f'{TEACHING_CALENDAR_SETTINGS_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask') or module.startswith('app.blueprints'),
                    f'{TEACHING_CALENDAR_SETTINGS_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(node.id, {'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for', 'flash'})

    def test_teaching_calendar_core_exposes_canonical_api(self):
        tree = ast.parse(TEACHING_CALENDAR_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        classes = {
            n.name for n in tree.body
            if isinstance(n, ast.ClassDef)
        }
        self.assertIn('TeachingCalendarConfig', classes)
        self.assertIn('teaching_term_start', functions)
        self.assertIn('teaching_term_end', functions)
        self.assertIn('teaching_week_number', functions)
        self.assertIn('date_for_teaching_weekday', functions)
        self.assertIn('parse_lecture_date', functions)

    def test_form_week_semantics_has_no_model_blueprint_or_flask_dependency(self):
        tree = ast.parse(FORM_WEEK_SEMANTICS_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask') or name.startswith('app.models') or name.startswith('app.blueprints') or name.startswith('sqlalchemy'),
                        f'{FORM_WEEK_SEMANTICS_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask') or module.startswith('app.models') or module.startswith('app.blueprints') or module.startswith('sqlalchemy'),
                    f'{FORM_WEEK_SEMANTICS_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(node.id, {'request', 'session', 'jsonify', 'db', 'LectureForm', 'flash'})

    def test_form_week_semantics_exposes_effective_api(self):
        tree = ast.parse(FORM_WEEK_SEMANTICS_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertIn('effective_form_week', functions)

    def test_schedule_snapshots_boundary_has_no_flask_blueprint_or_review_automation(self):
        tree = ast.parse(SCHEDULE_SNAPSHOTS_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask')
                        or name.startswith('app.blueprints')
                        or name.startswith('app.review_automation'),
                        f'{SCHEDULE_SNAPSHOTS_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask')
                    or module.startswith('app.blueprints')
                    or module.startswith('app.review_automation'),
                    f'{SCHEDULE_SNAPSHOTS_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(
                    node.id,
                    {'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for', 'flash', 'current_app'},
                )

    def test_academic_term_boundary_has_no_flask_blueprint_or_review_automation(self):
        tree = ast.parse(ACADEMIC_TERM_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask')
                        or name.startswith('app.blueprints')
                        or name.startswith('app.review_automation'),
                        f'{ACADEMIC_TERM_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask')
                    or module.startswith('app.blueprints')
                    or module.startswith('app.review_automation'),
                    f'{ACADEMIC_TERM_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(
                    node.id,
                    {'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for', 'flash', 'current_app'},
                )

    def test_review_schedule_source_boundary_has_no_flask_blueprint_or_review_automation(self):
        tree = ast.parse(REVIEW_SCHEDULE_SOURCE_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask')
                        or name.startswith('app.blueprints')
                        or name.startswith('app.review_automation'),
                        f'{REVIEW_SCHEDULE_SOURCE_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask')
                    or module.startswith('app.blueprints')
                    or module.startswith('app.review_automation'),
                    f'{REVIEW_SCHEDULE_SOURCE_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(
                    node.id,
                    {'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for', 'flash', 'current_app'},
                )

    def test_review_schedule_source_exposes_adapter_api(self):
        tree = ast.parse(REVIEW_SCHEDULE_SOURCE_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        classes = {
            n.name for n in tree.body
            if isinstance(n, ast.ClassDef)
        }
        self.assertIn('snapshot_rows_to_legacy_df', functions)
        self.assertIn('resolve_review_schedule_source', functions)
        self.assertIn('snapshot_scalar_metadata_complete', functions)
        self.assertIn('ReviewScheduleSourceResolution', classes)

    def test_schedule_snapshots_exposes_canonical_api(self):
        tree = ast.parse(SCHEDULE_SNAPSHOTS_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        classes = {
            n.name for n in tree.body
            if isinstance(n, ast.ClassDef)
        }
        self.assertIn('get_active_schedule_batch', functions)
        self.assertIn('get_active_schedule_rows', functions)
        self.assertIn('persist_import_snapshot', functions)
        self.assertIn('resolve_current_schedule_snapshot', functions)
        self.assertIn('CurrentScheduleSelection', classes)

    def test_academic_term_exposes_canonical_api(self):
        tree = ast.parse(ACADEMIC_TERM_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        constants = {
            n.targets[0].id for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        }
        self.assertIn('get_current_teaching_semester', functions)
        self.assertIn('normalize_semester_identifier', functions)
        self.assertIn('SETTING_KEY_CURRENT_TEACHING_SEMESTER', constants)

    def test_review_schedule_matcher_boundary_has_no_flask_blueprint_or_review_automation(self):
        tree = ast.parse(REVIEW_SCHEDULE_MATCHER_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask')
                        or name.startswith('app.blueprints')
                        or name.startswith('app.review_automation'),
                        f'{REVIEW_SCHEDULE_MATCHER_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask')
                    or module.startswith('app.blueprints')
                    or module.startswith('app.review_automation'),
                    f'{REVIEW_SCHEDULE_MATCHER_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(
                    node.id,
                    {'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for', 'flash', 'current_app'},
                )

    def test_review_reference_data_boundary_has_no_flask_blueprint_or_review_automation(self):
        tree = ast.parse(REVIEW_REFERENCE_DATA_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask')
                        or name.startswith('app.blueprints')
                        or name.startswith('app.review_automation'),
                        f'{REVIEW_REFERENCE_DATA_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask')
                    or module.startswith('app.blueprints')
                    or module.startswith('app.review_automation'),
                    f'{REVIEW_REFERENCE_DATA_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(
                    node.id,
                    {'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for', 'flash', 'current_app'},
                )

    def test_review_schedule_matcher_exposes_frozen_api(self):
        tree = ast.parse(REVIEW_SCHEDULE_MATCHER_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertIn('find_course_in_schedule', functions)
        self.assertIn('normalize_weekday', functions)
        self.assertIn('normalize_class_period', functions)
        self.assertIn('normalize_location', functions)

    def test_review_reference_data_exposes_orchestration_api(self):
        tree = ast.parse(REVIEW_REFERENCE_DATA_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertIn('search_review_reference_data', functions)

    def test_layered_services_do_not_depend_upward_on_reference_orchestration(self):
        for path in (REVIEW_SCHEDULE_MATCHER_FILE, REVIEW_SCHEDULE_SOURCE_FILE):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('review_reference_data', text, f'{path.name} must not import orchestration service')

    def test_organization_membership_boundary_has_no_flask_or_blueprint_dependency(self):
        tree = ast.parse(ORGANIZATION_MEMBERSHIP_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask') or name.startswith('app.blueprints'),
                        f'{ORGANIZATION_MEMBERSHIP_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask') or module.startswith('app.blueprints'),
                    f'{ORGANIZATION_MEMBERSHIP_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(
                    node.id,
                    {'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for', 'flash', 'current_app'},
                )

    def test_organization_membership_service_does_not_register_routes_or_read_http_state(self):
        text = ORGANIZATION_MEMBERSHIP_FILE.read_text(encoding='utf-8')
        self.assertNotIn('Blueprint', text)
        self.assertNotIn('session[', text)
        self.assertNotIn('request.', text)
        self.assertNotIn('jsonify(', text)

    def test_organization_membership_exposes_canonical_api(self):
        tree = ast.parse(ORGANIZATION_MEMBERSHIP_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertIn('is_user_in_canonical_group_scope', functions)
        self.assertIn('is_group_in_canonical_scope', functions)
        self.assertIn('canonical_group_user_criteria', functions)
        self.assertIn('canonical_scope_group_criteria', functions)
        self.assertIn('group_member_criteria', functions)
        self.assertIn('group_member_criteria_for_identity', functions)
        self.assertIn('assign_user_to_group', functions)
        self.assertIn('clear_user_group', functions)

    def test_organization_policy_boundary_has_no_flask_or_blueprint_dependency(self):
        tree = ast.parse(ORGANIZATION_POLICY_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask') or name.startswith('app.blueprints'),
                        f'{ORGANIZATION_POLICY_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask') or module.startswith('app.blueprints'),
                    f'{ORGANIZATION_POLICY_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(
                    node.id,
                    {'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for', 'flash', 'current_app'},
                )

    def test_organization_policy_exposes_capability_api(self):
        tree = ast.parse(ORGANIZATION_POLICY_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertIn('can_create_group', functions)
        self.assertIn('can_batch_move_group_members', functions)
        self.assertIn('can_assign_group_leader', functions)
        self.assertIn('leader_assignment_may_migrate_department', functions)

    def test_profile_stats_exposes_canonical_builder_and_rating_bands(self):
        tree = ast.parse(PROFILE_STATS_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        constants = {
            n.targets[0].id for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        }
        self.assertIn('build_user_profile_stats', functions)
        self.assertIn('RATING_BAND_NONE', constants)
        self.assertIn('RATING_BAND_ATTENTION', constants)
        self.assertIn('RATING_BAND_GOOD', constants)
        self.assertIn('RATING_BAND_EXCELLENT', constants)

    def test_profile_stats_service_boundary_has_no_flask_or_blueprint_dependency(self):
        tree = ast.parse(PROFILE_STATS_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    self.assertFalse(
                        name.startswith('flask') or name.startswith('app.blueprints'),
                        f'{PROFILE_STATS_FILE.name} imports forbidden module: {name}',
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                self.assertFalse(
                    module.startswith('flask') or module.startswith('app.blueprints'),
                    f'{PROFILE_STATS_FILE.name} imports forbidden module: {module}',
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(
                    node.id,
                    {'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for', 'flash', 'current_app'},
                )

    def test_profile_stats_ranking_population_and_tie_contract(self):
        """Ranking pool must filter by active+eligible-role users; ties use right edge."""
        tree = ast.parse(PROFILE_STATS_FILE.read_text(encoding='utf-8'))

        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported_names.update(
                    (alias.asname or alias.name).split('.')[0] for alias in node.names
                )
        self.assertIn('active_user_filter', imported_names)
        self.assertIn('User', imported_names)

        constants = {
            n.targets[0].id for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        }
        self.assertIn('PROFILE_STATS_ELIGIBLE_ROLES', constants)

        functions = {
            n.name: n for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertIn('_aggregate_listener_scores', functions)
        aggregator_text = ast.get_source_segment(
            PROFILE_STATS_FILE.read_text(encoding='utf-8'), functions['_aggregate_listener_scores'],
        )
        self.assertIn('PROFILE_STATS_ELIGIBLE_ROLES', aggregator_text)
        self.assertIn('active_user_filter()', aggregator_text)

        # percentile 不得再用 first-index tie bias（list.index）。
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotEqual(
                    node.func.attr, 'index',
                    f'{PROFILE_STATS_FILE.name} must not rank via list.index (first-index tie bias)',
                )

    def test_admin_profile_wrapper_is_compatibility_only(self):
        """_build_user_profile_stats must be a thin delegation, not a stats engine."""
        tree = ast.parse(ADMIN_USERS_FILE.read_text(encoding='utf-8'))
        wrapper_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == '_build_user_profile_stats'
        ]
        self.assertEqual(len(wrapper_defs), 1)
        wrapper = wrapper_defs[0]

        loaded_names = {
            node.id for node in ast.walk(wrapper)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        self.assertIn('build_user_profile_stats', loaded_names)
        self.assertIn('datetime', loaded_names)
        forbidden_statistics_names = {'LectureForm', 'ScoreRecord', 'ScoreItem', 'SystemSetting', 'func', 'effective_form_week', 'teaching_week_number'}
        self.assertEqual(loaded_names & forbidden_statistics_names, set())


if __name__ == '__main__':
    unittest.main()
