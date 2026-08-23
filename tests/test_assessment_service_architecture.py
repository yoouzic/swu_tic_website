# -*- coding: utf-8 -*-
"""Architecture contracts for assessment/statistics service layer (Step 2)."""
import ast
import unittest
from pathlib import Path

ASSESSMENT_SERVICE_FILES = (
    Path('app/services/assessment_scope.py'),
    Path('app/services/review_form_queries.py'),
    Path('app/services/stat_snapshots.py'),
    Path('app/services/assessment_calc.py'),
    Path('app/services/legacy_review_compat.py'),
    Path('app/services/review_contacts.py'),
)

BLUEPRINT_PREFIX = 'app.blueprints'
HTTP_NAMES = {
    'request', 'session', 'jsonify', 'render_template', 'redirect', 'url_for',
    'flash', 'abort', 'current_app', 'admin_bp',
}


def _check_no_flask_or_blueprint(tree, filename, allow_flask=False):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name.startswith(BLUEPRINT_PREFIX):
                    raise AssertionError(f'{filename} imports blueprint: {name}')
                if not allow_flask and name.startswith('flask'):
                    raise AssertionError(f'{filename} imports flask: {name}')
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            if module.startswith(BLUEPRINT_PREFIX):
                raise AssertionError(f'{filename} imports blueprint: {module}')
            if not allow_flask and module.startswith('flask'):
                raise AssertionError(f'{filename} imports flask: {module}')


class AssessmentServiceArchitectureTests(unittest.TestCase):
    def test_assessment_services_have_no_blueprint_import(self):
        for service_file in ASSESSMENT_SERVICE_FILES:
            tree = ast.parse(service_file.read_text(encoding='utf-8'))
            _check_no_flask_or_blueprint(tree, service_file.name)

    def test_assessment_calc_has_no_flask_or_http_name_reference(self):
        service_file = Path('app/services/assessment_calc.py')
        tree = ast.parse(service_file.read_text(encoding='utf-8'))
        _check_no_flask_or_blueprint(tree, service_file.name, allow_flask=False)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(node.id, HTTP_NAMES)

    def test_stat_snapshots_has_no_flask_or_http_name_reference(self):
        service_file = Path('app/services/stat_snapshots.py')
        tree = ast.parse(service_file.read_text(encoding='utf-8'))
        _check_no_flask_or_blueprint(tree, service_file.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.assertNotIn(node.id, HTTP_NAMES)

    def test_scope_and_query_services_are_http_free(self):
        for service_file in (
            Path('app/services/assessment_scope.py'),
            Path('app/services/review_form_queries.py'),
            Path('app/services/legacy_review_compat.py'),
            Path('app/services/review_contacts.py'),
        ):
            tree = ast.parse(service_file.read_text(encoding='utf-8'))
            _check_no_flask_or_blueprint(tree, service_file.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    self.assertNotIn(node.id, HTTP_NAMES - {'admin_bp'})

    def test_all_services_forbid_blueprint_imports(self):
        services_dir = Path('app/services')
        for service_file in services_dir.glob('*.py'):
            tree = ast.parse(service_file.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(
                            alias.name.startswith(BLUEPRINT_PREFIX),
                            f'{service_file.name} imports blueprint: {alias.name}',
                        )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    self.assertFalse(
                        module.startswith(BLUEPRINT_PREFIX),
                        f'{service_file.name} imports blueprint: {module}',
                    )


if __name__ == '__main__':
    unittest.main()
