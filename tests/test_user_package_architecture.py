# -*- coding: utf-8 -*-
"""Narrow AST tests for the user Blueprint package split."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USER_PKG = ROOT / 'app' / 'blueprints' / 'user'

DOMAIN_MODULES = ('forms.py', 'profile.py', 'reservations.py')
SIBLING_MODULES = {'forms', 'profile', 'reservations'}


class UserPackageArchitectureTests(unittest.TestCase):
    def test_domain_modules_share_only_user_bp_and_have_no_sibling_imports(self):
        for filename in DOMAIN_MODULES:
            path = USER_PKG / filename
            tree = ast.parse(path.read_text(encoding='utf-8'))
            has_user_bp_import = False
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    # `from . import user_bp` is the only allowed package dependency.
                    if node.level == 1 and node.module is None:
                        if any(alias.name == 'user_bp' for alias in node.names):
                            has_user_bp_import = True
                    module = node.module or ''
                    if module in SIBLING_MODULES or module.startswith('app.blueprints.user.'):
                        self.fail(f'{filename} imports sibling module: {module}')
            self.assertTrue(has_user_bp_import, f'{filename} does not import user_bp')

    def test_only_init_constructs_blueprint(self):
        init_text = (USER_PKG / '__init__.py').read_text(encoding='utf-8')
        init_tree = ast.parse(init_text)
        blueprint_calls = [
            node for node in ast.walk(init_tree)
            if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'Blueprint'
        ]
        self.assertEqual(len(blueprint_calls), 1)

        for filename in DOMAIN_MODULES:
            tree = ast.parse((USER_PKG / filename).read_text(encoding='utf-8'))
            calls = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'Blueprint'
            ]
            self.assertEqual(calls, [], f'{filename} must not construct a Blueprint')

    def test_no_wildcard_imports(self):
        for path in USER_PKG.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(
                        [a.name for a in node.names],
                        ['*'],
                        f'{path.name} contains wildcard import',
                    )

    def test_function_coverage_manifest(self):
        functions = {}
        for path in USER_PKG.glob('*.py'):
            if path.name == '__init__.py':
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    functions[node.name] = path.name
        self.assertEqual(len(functions), 28)
        expected = {
            '_load_lecture_form_draft', '_delete_lecture_form_draft',
            '_parse_draft_payload', '_normalize_draft_payload',
            '_normalize_submission_value', '_build_submission_signature',
            '_find_recent_duplicate_submission', 'profile', 'lecture_form_draft',
            'edit_profile', '_build_activity_records', 'my_forms',
            'delete_form', 'submit_form', 'edit_form', 'success', 'view_form',
            'listening_registration', 'course_feedback_management',
            'api_available_courses', 'api_course_registration_history',
            'api_create_reservation', 'api_cancel_reservation',
            'api_my_reservations', 'api_update_my_reservation',
            'api_delete_my_reservation', 'api_unused_reservations',
            'api_time_suggestion',
        }
        self.assertEqual(set(functions), expected)
        self.assertEqual(len(functions), len(expected))


if __name__ == '__main__':
    unittest.main()
