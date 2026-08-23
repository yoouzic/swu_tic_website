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
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.name
                        if name.startswith('app.blueprints.admin.'):
                            child = name.split('.')[-1]
                            if child in DOMAIN_MODULES and child != domain:
                                self.fail(
                                    f'{domain}.py has forbidden sibling import: {name}'
                                )
                    continue
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
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('app.blueprints.admin.'):
                        self.fail(f'shared.py imports domain module: {alias.name}')
                continue
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
        # Count every Blueprint construction; only __init__.py may create it.
        for module in ALL_MODULES:
            tree = read_ast(module)
            blueprint_calls = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_blueprint_call = (
                    (isinstance(func, ast.Name) and func.id == 'Blueprint')
                    or (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id == 'flask'
                        and func.attr == 'Blueprint'
                    )
                )
                if is_blueprint_call:
                    blueprint_calls.append(node.lineno)
            if module == '__init__':
                self.assertEqual(
                    len(blueprint_calls), 1,
                    f'__init__.py must create exactly one Blueprint; found {blueprint_calls}',
                )
            else:
                self.assertEqual(
                    len(blueprint_calls), 0,
                    f'{module}.py must not create a Blueprint; found {blueprint_calls}',
                )

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
