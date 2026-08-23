# -*- coding: utf-8 -*-
"""Architecture test: security decorators must not come from the auth Blueprint."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

AUTH_BLUEPRINT = ROOT / 'app' / 'blueprints' / 'auth.py'
SECURITY_MODULE = ROOT / 'app' / 'security.py'

BUSINESS_BLUEPRINTS = [
    ROOT / 'app' / 'blueprints' / 'user.py',
    *sorted((ROOT / 'app' / 'blueprints' / 'admin').glob('*.py')),
]


class SecurityExtractionTests(unittest.TestCase):
    def _imported_from_auth_blueprint(self, file_path):
        tree = ast.parse(file_path.read_text(encoding='utf-8'))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if module.endswith('.auth') or module == 'auth' or module == '..auth':
                    if any(alias.name in ('login_required', 'role_required') for alias in node.names):
                        imports.append((module, [a.name for a in node.names]))
        return imports

    def test_business_blueprints_do_not_import_guards_from_auth_blueprint(self):
        for path in BUSINESS_BLUEPRINTS:
            with self.subTest(path=path.name):
                self.assertEqual(self._imported_from_auth_blueprint(path), [])

    def test_auth_blueprint_imports_guards_from_security(self):
        tree = ast.parse(AUTH_BLUEPRINT.read_text(encoding='utf-8'))
        imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == 'app.security':
                names = [a.name for a in node.names]
                if 'login_required' in names and 'role_required' in names:
                    imported = True
        self.assertTrue(imported)

    def test_security_module_exposes_guards(self):
        tree = ast.parse(SECURITY_MODULE.read_text(encoding='utf-8'))
        functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn('login_required', functions)
        self.assertIn('role_required', functions)


if __name__ == '__main__':
    unittest.main()
