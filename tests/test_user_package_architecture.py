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
        self.assertEqual(len(functions), 48)
        expected = {
            '_is_draft_scalar', '_normalize_assistant_draft_payload',
            '_extract_assistant_submission_payload',
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
            'api_time_suggestion', '_envelope', '_error_response',
            '_assistant_login_required', '_clean_text', '_parse_date',
            '_normalize_rejected_ids', '_query_rejected_ids', '_json_object',
            '_required_batch_id', '_build_query', '_normalize_confirm_query',
            '_normalize_selection_payload', '_public_confirmation',
            '_expected_selection_error', 'listening_assistant_candidates',
            'listening_assistant_fallback', 'listening_assistant_confirm',
        }
        self.assertEqual(set(functions), expected)
        self.assertEqual(len(functions), len(expected))

    def test_profile_controller_delegates_statistics_to_service(self):
        """profile() must be a thin controller; statistics live in the service."""
        tree = ast.parse((USER_PKG / 'profile.py').read_text(encoding='utf-8'))
        profile_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == 'profile'
        ]
        self.assertEqual(len(profile_defs), 1)
        profile_fn = profile_defs[0]

        loaded_names = {
            node.id for node in ast.walk(profile_fn)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        self.assertIn('build_user_profile_stats', loaded_names)

        # Controller must not orchestrate statistics itself.
        forbidden_statistics_names = {
            'LectureForm', 'ScoreRecord', 'ScoreItem', 'SystemSetting', 'func',
            'effective_form_week', 'teaching_week_number',
        }
        self.assertEqual(loaded_names & forbidden_statistics_names, set())

    def test_draft_delete_helper_remains_commit_free(self):
        """_delete_lecture_form_draft 是纯 mutation primitive：不得自带 commit。

        正常/重复提交都必须把 draft DELETE 与表单、registration、leave makeup
        放进同一个业务 commit（Round 7C-P2）；helper 自己 commit 会制造
        partial transaction。防止将来有人把 commit 塞回 helper。
        """
        tree = ast.parse((USER_PKG / 'forms.py').read_text(encoding='utf-8'))
        helper_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == '_delete_lecture_form_draft'
        ]
        self.assertEqual(len(helper_defs), 1)
        helper_fn = helper_defs[0]

        commit_calls = [
            node for node in ast.walk(helper_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'commit'
        ]
        self.assertEqual(commit_calls, [], 'draft delete helper must not commit')

    def test_submit_form_commits_only_at_accepted_terminals(self):
        """submit_form 内的 commit 只允许出现在两个成功终态分支。

        结构性约束（非脆弱的全文 commit 计数）：duplicate accepted 分支必须
        含显式 commit；normal accepted 尾部保留唯一一个非 duplicate 分支的
        commit；failure (except) 路径只允许 rollback、不得 commit。
        行为契约由 test_submit_form_transaction.py 的 fault-injection 覆盖。
        """
        tree = ast.parse((USER_PKG / 'forms.py').read_text(encoding='utf-8'))
        submit_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == 'submit_form'
        ]
        self.assertEqual(len(submit_defs), 1)
        submit_fn = submit_defs[0]

        def _is_db_session_commit(node):
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'commit'
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == 'session'
            )

        # duplicate accepted 分支（`if duplicate_form:`）必须含显式 commit。
        duplicate_branches = [
            node for node in ast.walk(submit_fn)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == 'duplicate_form'
        ]
        self.assertEqual(len(duplicate_branches), 1)
        duplicate_commits = [
            node for node in ast.walk(duplicate_branches[0])
            if _is_db_session_commit(node)
        ]
        self.assertEqual(
            len(duplicate_commits), 1,
            'duplicate accepted branch must commit exactly once',
        )

        # normal accepted 尾部：唯一一个 duplicate 分支之外的 commit。
        outside_commits = [
            node for node in ast.walk(submit_fn)
            if _is_db_session_commit(node) and node not in ast.walk(duplicate_branches[0])
        ]
        self.assertEqual(
            len(outside_commits), 1,
            'normal accepted path must keep exactly one commit',
        )

        # failure 路径：except 内只允许 rollback，不得 commit。
        for handler in [n for n in ast.walk(submit_fn) if isinstance(n, ast.ExceptHandler)]:
            for node in ast.walk(handler):
                self.assertFalse(
                    _is_db_session_commit(node),
                    'failure path must not commit',
                )
        rollbacks = [
            node for node in ast.walk(submit_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'rollback'
        ]
        self.assertGreaterEqual(len(rollbacks), 1, 'failure path must rollback')


if __name__ == '__main__':
    unittest.main()
