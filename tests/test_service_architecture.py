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


if __name__ == '__main__':
    unittest.main()
