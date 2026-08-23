# -*- coding: utf-8 -*-
"""Architecture contracts for the new service layer."""
import ast
import unittest
from pathlib import Path


SERVICE_FILE = Path('app/services/review_domain.py')

FORBIDDEN_IMPORT_PREFIXES = (
    'flask',
    'app.blueprints',
)

FORBIDDEN_NAMES = {
    'session', 'request', 'jsonify', 'abort', 'flash',
    'admin_bp', 'current_app', 'render_template', 'redirect', 'url_for',
}


class ServiceArchitectureTests(unittest.TestCase):
    def test_review_domain_service_has_no_flask_or_blueprint_dependency(self):
        tree = ast.parse(SERVICE_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                        self.fail(f'review_domain.py imports forbidden module: {name}')
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    self.fail(f'review_domain.py imports forbidden module: {module}')

    def test_review_domain_service_does_not_reference_http_names(self):
        tree = ast.parse(SERVICE_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in FORBIDDEN_NAMES:
                    self.fail(f'review_domain.py references HTTP name: {node.id}')

    def test_review_domain_service_has_no_wildcard_import(self):
        tree = ast.parse(SERVICE_FILE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.assertNotEqual(alias.name, '*')

    def test_review_domain_service_exposes_narrow_public_api(self):
        tree = ast.parse(SERVICE_FILE.read_text(encoding='utf-8'))
        functions = {
            n.name for n in tree.body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertIn('partition_forms_by_review_scope', functions)
        self.assertIn('is_form_in_review_scope', functions)


if __name__ == '__main__':
    unittest.main()
