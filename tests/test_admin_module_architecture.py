# -*- coding: utf-8 -*-
"""Phase 1.5 architecture contracts for the admin package.

These tests intentionally use AST, not textual search, and assert structural
boundaries that should survive future feature growth.
"""
import ast
import unittest
from pathlib import Path


ADMIN_DIR = Path('app/blueprints/admin')
DOMAIN_MODULES = [
    'system', 'contacts', 'review', 'org', 'users', 'courses', 'forms_io',
    'schedule', 'leave', 'statistics', 'assessment_stats',
]
ALL_MODULES = ['__init__', 'shared'] + DOMAIN_MODULES


def read_ast(module_name):
    path = ADMIN_DIR / f'{module_name}.py'
    return ast.parse(path.read_text(encoding='utf-8'))


def imported_module_name(node):
    """Return the imported module string for an ImportFrom node.

    Relative imports are expanded to their ``app.blueprints.admin.<module>``
    form for uniform checking.
    """
    if node.level == 0:
        return node.module or ''
    # admin package depth: .shared -> app.blueprints.admin.shared
    prefix = 'app.blueprints.admin'
    parts = []
    if node.module:
        parts = node.module.split('.')
    if node.level == 1:
        return prefix + ('.' + '.'.join(parts) if parts else '')
    # For this package only level 1 is used; fall back to a conservative value.
    return prefix + '.' + '.'.join(parts)


class AdminModuleArchitectureTests(unittest.TestCase):
    def test_domain_modules_do_not_import_sibling_domain(self):
        for domain in DOMAIN_MODULES:
            tree = read_ast(domain)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                imported = imported_module_name(node)
                # Allowed: from . import admin_bp; from .shared import ...
                if imported == 'app.blueprints.admin.shared':
                    continue
                if imported == 'app.blueprints.admin':
                    # Only allowed when importing admin_bp from the package root.
                    for alias in node.names:
                        if alias.name != 'admin_bp':
                            self.fail(
                                f'{domain}.py imports {alias.name} from package root'
                            )
                    continue
                if imported in {
                    f'app.blueprints.admin.{sibling}'
                    for sibling in DOMAIN_MODULES
                    if sibling != domain
                }:
                    self.fail(
                        f'{domain}.py has forbidden sibling import: {imported}'
                    )

    def test_shared_does_not_import_domain_module(self):
        tree = read_ast('shared')
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            imported = imported_module_name(node)
            if imported.startswith('app.blueprints.admin.') and imported != 'app.blueprints.admin.shared':
                self.fail(f'shared.py imports domain module: {imported}')

    def test_shared_registers_no_routes(self):
        tree = read_ast('shared')
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if (
                        isinstance(decorator, ast.Call)
                        and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr == 'route'
                    ):
                        self.fail('shared.py contains @admin_bp.route')

    def test_single_admin_blueprint_created_only_in_init(self):
        # It must be created only in __init__.py.
        for module in ALL_MODULES:
            tree = read_ast(module)
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                if not any(
                    isinstance(t, ast.Name) and t.id == 'admin_bp'
                    for t in node.targets
                ):
                    continue
                call = node.value
                is_blueprint_call = (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == 'Blueprint'
                )
                if module != '__init__' and is_blueprint_call:
                    self.fail(f'{module}.py creates a new admin_bp')
                if module == '__init__' and not is_blueprint_call:
                    self.fail('__init__.py admin_bp is not created via Blueprint()')

    def test_no_wildcard_imports_in_admin_package(self):
        for module in ALL_MODULES:
            tree = read_ast(module)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        self.assertNotEqual(
                            alias.name, '*',
                            f'{module}.py contains wildcard import',
                        )


if __name__ == '__main__':
    unittest.main()
