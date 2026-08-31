# -*- coding: utf-8 -*-
"""Round 8B-P3A architecture boundary regressions for review_application.

These lock the extraction boundary, not line counts or naming:
    - the application core is HTTP/framework neutral;
    - transaction ownership is single (one commit path in the executor);
    - helper primitives stay commit/rollback-free;
    - Route A calls the core, Route B production code does not yet;
    - the inline ScoreRecord/ScoreItem transaction is gone from Route A.
"""
import ast
import dataclasses
import re
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CORE_PATH = BASE / 'app' / 'services' / 'review_application.py'
ROUTE_PATH = BASE / 'app' / 'blueprints' / 'admin' / 'review.py'
PRIMITIVES_PATH = BASE / 'app' / 'services' / 'review_mutation.py'


def _function_segment(route_src, function_name):
    tree = ast.parse(route_src)
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == function_name)
    return ast.get_source_segment(route_src, node)


class ReviewApplicationCoreBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.src = CORE_PATH.read_text(encoding='utf-8')
        self.tree = ast.parse(self.src)

    def test_core_imports_no_flask_or_http_names(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ('flask', 'request', 'session', 'jsonify',
                          'redirect', 'url_for', 'blueprint'):
            self.assertNotIn(forbidden, imported)

    def test_core_references_no_http_names(self):
        """Identifier-level check via AST: docstrings never trip it, and the
        SQLAlchemy ``db.session`` attribute (transaction ownership) is allowed
        while a bare Flask ``session`` name is forbidden."""
        bare_names = {node.id for node in ast.walk(self.tree) if isinstance(node, ast.Name)}
        for name in ('jsonify', 'request', 'session', 'url_for',
                     'redirect', 'Blueprint', 'abort'):
            self.assertNotIn(name, bare_names)

    def test_core_has_no_http_status_literals(self):
        self.assertIsNone(re.search(r',\s*\d{3}\s*\)', self.src))

    def test_executor_owns_exactly_one_commit_and_rollback(self):
        self.assertEqual(self.src.count('db.session.commit()'), 1)
        self.assertEqual(self.src.count('db.session.rollback()'), 1)

    def test_actions_are_explicit_enums(self):
        from app.services.review_application import ScoreAction, VersionAction
        self.assertEqual(
            {action.name for action in VersionAction},
            {'APPEND_VERSION', 'UPDATE_LATEST'},
        )
        self.assertEqual(
            {action.name for action in ScoreAction},
            {'REPLACE', 'CLEAR', 'NONE'},
        )

    def test_result_is_presentation_neutral(self):
        from app.services import review_application
        result_fields = {
            field.name for field in dataclasses.fields(review_application.ReviewMutationResult)
        }
        self.assertEqual(result_fields, {'target_form_id', 'new_status', 'modified_fields'})

    def test_plan_carries_explicit_version_and_score_semantics(self):
        from app.services import review_application
        plan_fields = {
            field.name for field in dataclasses.fields(review_application.ReviewMutationPlan)
        }
        for required in ('version_action', 'score_action',
                         'delete_existing_score_record', 'score_items_to_persist',
                         'resolved_fields', 'resolved_review_comment',
                         'modified_fields', 'registration_id', 'logical_id'):
            self.assertIn(required, plan_fields)


class ReviewMutationPrimitivesBoundaryTest(unittest.TestCase):
    def test_helper_primitives_have_no_transaction_ownership(self):
        src = PRIMITIVES_PATH.read_text(encoding='utf-8')
        self.assertNotIn('db.session.commit', src)
        self.assertNotIn('db.session.rollback', src)


class RouteAdoptionBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.route_src = ROUTE_PATH.read_text(encoding='utf-8')

    def test_route_a_calls_application_core(self):
        segment = _function_segment(self.route_src, 'submit_review')
        self.assertIn('ReviewMutationPlan(', segment)
        self.assertIn('execute_review_mutation(', segment)

    def test_route_a_no_longer_inlines_score_transaction(self):
        segment = _function_segment(self.route_src, 'submit_review')
        self.assertNotIn('ScoreRecord(', segment)
        self.assertNotIn('ScoreItem(', segment)

    def test_route_b_does_not_call_core_yet(self):
        segment = _function_segment(self.route_src, 'submit_form_review')
        self.assertNotIn('review_application', segment)
        self.assertNotIn('execute_review_mutation', segment)
        self.assertNotIn('ReviewMutationPlan', segment)
        # B 的内联 mutation 在 P3B 前保持原位
        self.assertIn('ScoreItem(', segment)
        self.assertIn('ScoreRecord(', segment)


if __name__ == '__main__':
    unittest.main()
