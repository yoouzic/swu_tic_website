# -*- coding: utf-8 -*-
"""AST integrity tests for AutoReviewEngine method completeness."""
import ast
import unittest
from pathlib import Path

from app.utils.auto_review import AutoReviewEngine

AUTO_REVIEW_FILE = Path('app/utils/auto_review.py')


class AutoReviewEngineIntegrityTests(unittest.TestCase):
    def _engine_ast(self):
        tree = ast.parse(AUTO_REVIEW_FILE.read_text(encoding='utf-8'))
        return next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'AutoReviewEngine'
        )

    def test_all_self_method_calls_resolve_to_definitions(self):
        klass = self._engine_ast()
        definitions = {
            node.name for node in klass.body
            if isinstance(node, ast.FunctionDef)
        }
        calls = set()
        for node in ast.walk(klass):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'self'
            ):
                calls.add(node.func.attr)
        unresolved = calls - definitions
        self.assertEqual(unresolved, set())

    def test_dead_feedback_methods_removed_and_check_text_errors_present(self):
        klass = self._engine_ast()
        definitions = {
            node.name for node in klass.body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn('check_text_errors', definitions)
        self.assertNotIn('files_status', definitions)
        self.assertNotIn('review_feedback', definitions)
        self.assertNotIn('export_report', definitions)

    def test_check_text_errors_freezes_legacy_text_rules_without_typos_runtime(self):
        dummy = object()
        repeated = AutoReviewEngine.check_text_errors(
            dummy, '好的的', enable_typos_check=False,
        )
        self.assertTrue(any('重复字' in issue for issue in repeated))

        punctuation = AutoReviewEngine.check_text_errors(
            dummy, '你好,world', enable_typos_check=False,
        )
        self.assertTrue(any('英文逗号' in issue for issue in punctuation))


if __name__ == '__main__':
    unittest.main()
