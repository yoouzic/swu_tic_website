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
)

TEACHING_CALENDAR_FILE = Path('app/services/teaching_calendar.py')
TEACHING_CALENDAR_SETTINGS_FILE = Path('app/services/teaching_calendar_settings.py')
FORM_WEEK_SEMANTICS_FILE = Path('app/services/form_week_semantics.py')
SCHEDULE_SNAPSHOTS_FILE = Path('app/services/schedule_snapshots.py')
ACADEMIC_TERM_FILE = Path('app/services/academic_term.py')
REVIEW_SCHEDULE_SOURCE_FILE = Path('app/services/review_schedule_source.py')

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
        self.assertIn('snapshot_rows_to_legacy_df', functions)
        self.assertIn('current_canonical_legacy_df', functions)

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


if __name__ == '__main__':
    unittest.main()
