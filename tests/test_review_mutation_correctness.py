# -*- coding: utf-8 -*-
"""Round 8B-P1 focused correctness regressions for the review mutation endpoints.

Scope: LD-1 (Route A score-write atomicity), LD-2 (Route A unexpected-failure
HTTP contract), LD-3 (Route B ScoreRecord replacement ordering).  The P0
characterization suite in test_review_mutation_compatibility.py remains the
PRE_FIX evidence; the four defect-contract tests there were converted into
POST_FIX regressions (EXPECTED_DEFECT_CONTRACT_CHANGE).  This file holds the
new focused regressions that P0 could not state:

    A2  second-ScoreItem fault -> full rollback (ROUTE_A_SCORE_MUTATION_ATOMIC)
    B1  stage2 first score when no old record exists (precision guard)
    B3  stage2 zero-score replacement under the unchanged B policy
    B5  replacement ScoreItem fault -> old record restored
    B6  replacement commit fault -> old record restored

LD-3 precise trigger condition (measured in P0):

    Route B AND stage-2 in-place update AND target physical form already has
    a ScoreRecord AND new score_data is non-empty  ->  old record DELETE marked
    + replacement INSERT in one flush -> UNIQUE(score_records.form_id) failure.

No shared mutation core is introduced in this round.
"""
import unittest
from unittest import mock

from app.models import ScoreItem, ScoreRecord, db
from app.services import review_application as review_application_module
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase


class ReviewMutationScoreAtomicityCorrectnessTest(_ReviewMutationCompatibilityBase):
    """Partial-staging faults must roll back the whole review transaction."""

    def test_route_a_second_score_item_fault_rolls_back_http_500(self):
        """A2: item1 constructs and stages normally, item2 constructor raises a
        pure-Python RuntimeError. Score persistence is part of the review
        transaction, so even with ScoreRecord + item1 already staged nothing
        may commit. (ROUTE_A_SCORE_MUTATION_ATOMIC)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        self._put_draft(form.id)

        real_score_item = review_application_module.ScoreItem
        calls = {'count': 0}

        def factory(*args, **kwargs):
            calls['count'] += 1
            if calls['count'] == 1:
                return real_score_item(*args, **kwargs)
            raise RuntimeError('second score item fault')

        payload = {
            'form_data': self._full_form_data(form),
            'review_comment': 'x',
            'score_data': [
                self._positive_score(reason='item1', dept=1.0, pers=1.0),
                self._positive_score(reason='item2', dept=2.0, pers=2.0),
            ],
        }
        with mock.patch(
            'app.services.review_application.ScoreItem',
            side_effect=factory,
        ):
            response = self._submit_a(form.id, payload)

        self.assertEqual(calls['count'], 2)
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertEqual(body['message'], '审核提交失败，请稍后重试')
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, '待审核')
        self.assertEqual(ScoreRecord.query.count(), 0)
        self.assertEqual(ScoreItem.query.count(), 0)
        self.assertTrue(self._draft_exists(form.id))

    def test_route_b_replacement_item_fault_restores_old_score(self):
        """B5: stage-2 replacement where the new ScoreItem constructor raises
        AFTER old-record delete + flush + new-record flush. The rollback must
        restore the old ScoreRecord, its items, totals, the stage-2 form
        mutation, and the draft — delete+flush is not a partial commit.
        (ROUTE_B_SCORE_REPLACEMENT_ROLLBACK_RESTORES_OLD_RECORD)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        stage1 = self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'dept pass',
            'score_data': [self._positive_score(reason='stage1', dept=2.0, pers=1.0)],
        })
        self.assertEqual(stage1.status_code, 200)
        v2_id = form.get_latest_version().id

        self._login(self.center_admin)
        self._put_draft(v2_id)
        with mock.patch(
            'app.blueprints.admin.review.ScoreItem',
            side_effect=RuntimeError('replacement item fault'),
        ):
            stage2 = self._submit_b(v2_id, {
                'form_data': {'course_feedback': 'feedback-stage2 ' * 4},
                'review_comment': 'center pass',
                'score_data': [self._positive_score(reason='stage2', dept=3.0, pers=2.0)],
            })

        self.assertEqual(stage2.status_code, 500)
        body = stage2.get_json()
        self.assertFalse(body['success'])
        self.assertEqual(body['message'], '审核提交失败，请稍后重试')

        db.session.expire_all()
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 2)
        v2, base = rows
        self.assertEqual(base.status, '待审核')
        self.assertEqual(v2.id, v2_id)
        self.assertEqual(v2.status, '部门已审核')
        self.assertEqual(v2.review_comment, 'dept pass')
        self.assertEqual(v2.course_feedback, 'feedback-base')
        records = ScoreRecord.query.filter_by(form_id=v2_id).all()
        self.assertEqual(len(records), 1)
        old_record = records[0]
        self.assertEqual(old_record.total_department_score, 2.0)
        self.assertEqual(old_record.total_personal_score, 1.0)
        items = list(old_record.items)
        self.assertEqual([item.reason for item in items], ['stage1'])
        self.assertEqual(ScoreItem.query.count(), 1)
        self.assertTrue(self._draft_exists(v2_id))

    def test_route_b_replacement_commit_fault_restores_old_score(self):
        """B6: stage-2 replacement fully staged, final commit raises. Rollback
        must restore the old score record/items and the pre-stage-2 form."""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        stage1 = self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'dept pass',
            'score_data': [self._positive_score(reason='stage1', dept=2.0, pers=1.0)],
        })
        self.assertEqual(stage1.status_code, 200)
        v2_id = form.get_latest_version().id

        self._login(self.center_admin)
        self._put_draft(v2_id)
        with mock.patch(
            'sqlalchemy.orm.Session.commit',
            side_effect=RuntimeError('commit fault'),
        ):
            stage2 = self._submit_b(v2_id, {
                'form_data': {'course_feedback': 'feedback-stage2 ' * 4},
                'review_comment': 'center pass',
                'score_data': [self._positive_score(reason='stage2', dept=3.0, pers=2.0)],
            })

        self.assertEqual(stage2.status_code, 500)
        body = stage2.get_json()
        self.assertFalse(body['success'])
        self.assertEqual(body['message'], '审核提交失败，请稍后重试')

        db.session.expire_all()
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 2)
        v2, base = rows
        self.assertEqual(v2.id, v2_id)
        self.assertEqual(v2.status, '部门已审核')
        self.assertEqual(v2.review_comment, 'dept pass')
        records = ScoreRecord.query.filter_by(form_id=v2_id).all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].total_department_score, 2.0)
        self.assertEqual([item.reason for item in records[0].items], ['stage1'])
        self.assertTrue(self._draft_exists(v2_id))


class ReviewMutationScoreReplacementPolicyTest(_ReviewMutationCompatibilityBase):
    """Stage-2 replacement semantics that must survive the LD-3 ordering fix."""

    def test_route_b_stage2_first_score_when_no_old_record_succeeds(self):
        """B1 precision guard: stage1 carried no scores, so stage2 is the FIRST
        ScoreRecord for v2 — no replacement, no UNIQUE conflict. The fix must
        not change this path. (ROUTE_B_STAGE2_FIRST_SCORE_ALREADY_WORKS)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        stage1 = self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'dept pass',
            'score_data': [],
        })
        self.assertEqual(stage1.status_code, 200)
        self.assertEqual(ScoreRecord.query.count(), 0)
        v2_id = form.get_latest_version().id

        self._login(self.center_admin)
        stage2 = self._submit_b(v2_id, {
            'form_data': {},
            'review_comment': 'center pass',
            'score_data': [self._positive_score(reason='stage2', dept=3.0, pers=2.0)],
        })
        self.assertEqual(stage2.status_code, 200)
        body = stage2.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['new_status'], '中心已审核')

        db.session.expire_all()
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 2)
        v2 = rows[0]
        self.assertEqual(v2.id, v2_id)
        self.assertEqual(v2.status, '中心已审核')
        records = ScoreRecord.query.filter_by(form_id=v2_id).all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].total_department_score, 3.0)
        self.assertEqual(records[0].total_personal_score, 2.0)
        self.assertEqual([item.reason for item in records[0].items], ['stage2'])

    def test_route_b_stage2_zero_replacement_keeps_zero_score_policy(self):
        """B3: stage1 positive record replaced by stage2 score_data=[zero item].
        The existing B zero-score policy (record kept, items dropped, totals 0)
        is UNKNOWN_POLICY and must be untouched by the ordering fix.
        (ZERO_SCORE_POLICY_UNCHANGED)"""
        form = self._make_form(self.officer)
        self._login(self.dept_admin)
        stage1 = self._submit_b(form.id, {
            'form_data': {},
            'review_comment': 'dept pass',
            'score_data': [self._positive_score(reason='stage1', dept=2.0, pers=1.0)],
        })
        self.assertEqual(stage1.status_code, 200)
        v2_id = form.get_latest_version().id

        self._login(self.center_admin)
        stage2 = self._submit_b(v2_id, {
            'form_data': {},
            'review_comment': 'center pass',
            'score_data': [self._zero_score()],
        })
        self.assertEqual(stage2.status_code, 200)
        self.assertTrue(stage2.get_json()['success'])

        db.session.expire_all()
        rows = self._logical_rows(form)
        self.assertEqual(len(rows), 2)
        v2 = rows[0]
        self.assertEqual(v2.id, v2_id)
        self.assertEqual(v2.status, '中心已审核')
        records = ScoreRecord.query.filter_by(form_id=v2_id).all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].total_department_score, 0.0)
        self.assertEqual(records[0].total_personal_score, 0.0)
        self.assertEqual(len(records[0].items), 0)


if __name__ == '__main__':
    unittest.main()
