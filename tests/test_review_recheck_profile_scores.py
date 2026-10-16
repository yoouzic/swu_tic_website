# -*- coding: utf-8 -*-
"""Latest review scores, including real department rechecks, drive profiles."""
import os
import tempfile
from datetime import datetime
from pathlib import Path


# Establish safe paths before the compatibility fixture imports the global app.
# DATABASE_URL must also be set so a local .env cannot override SQLite isolation.
_IMPORT_DIR = tempfile.TemporaryDirectory(prefix='review-profile-import-')
_IMPORT_ROOT = Path(_IMPORT_DIR.name)
os.environ.update({
    'DATABASE_URL': 'sqlite:///' + str(_IMPORT_ROOT / 'import.sqlite'),
    'SQLITE_DB_PATH': str(_IMPORT_ROOT / 'import.sqlite'),
    'INSTANCE_DIR': str(_IMPORT_ROOT / 'instance'),
    'UPLOAD_FOLDER': str(_IMPORT_ROOT / 'uploads'),
    'AUTOMATION_UPLOAD_DIR': str(_IMPORT_ROOT / 'automation'),
    'SECRET_KEY': 'test-review-profile-secret',
    'STORAGE_CLEANUP_ENABLED': '0',
    'FLASK_ENV': 'testing',
})

from app.models import ScoreRecord, db
from app.services.profile_stats import build_user_profile_stats
from tests.review_request_utils import post_opened_review
from tests.test_review_mutation_compatibility import (
    ROUTE_A,
    ROUTE_B,
    _ReviewMutationCompatibilityBase,
)


class ReviewRecheckProfileScoresTest(_ReviewMutationCompatibilityBase):
    def _approve(self, route, form, *, scores):
        response = post_opened_review(
            self.client, route.format(form_id=form.id),
            json={
                'form_data': self._full_form_data(form),
                'review_comment': '核对通过',
                'score_data': scores,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'], response.get_json())
        return form.get_latest_version()

    def _group_approved(self, route=ROUTE_A, *, legacy_base=False):
        original = self._make_form(self.officer, backfill_unique_id=not legacy_base)
        self._login(self.group_admin)
        approved = self._approve(route, original, scores=[
            self._positive_score(reason='原审核扣分', dept=2, pers=3),
        ])
        self.assertNotEqual(original.id, approved.id)
        self.assertEqual(approved.status, '部门已审核')
        return original, approved

    def _profile(self, user=None):
        return build_user_profile_stats(user or self.officer, now=datetime(2026, 10, 16))

    def _assert_latest_without_scores(self, latest):
        self.assertIsNone(ScoreRecord.query.filter_by(form_id=latest.id).first())
        stats = self._profile()
        self.assertEqual(stats['total_deduction'], 0)
        self.assertEqual(stats['assessment_items'], [])
        self.assertFalse(stats['has_evaluation_sample'])
        self.assertIsNone(stats['percentile'])
        self.assertEqual(stats['rating_label'], '暂无评级')

    def _assert_carried_scores_count_once(self, route):
        original, group_approved = self._group_approved(route)
        # Match the production page: carry the GET response's scoring rows into
        # the department recheck rather than inventing a second penalty.
        self._login(self.dept_admin)
        detail = self.client.get(f'/admin/api/review/form/{group_approved.id}')
        carried_scores = detail.get_json()['score_data']
        latest = self._approve(route, group_approved, scores=carried_scores)
        self.assertNotEqual(latest.id, group_approved.id)
        self.assertEqual(len(original.get_all_versions()), 3)
        self.assertEqual(ScoreRecord.query.filter_by(form_id=group_approved.id).count(), 1)
        self.assertEqual(ScoreRecord.query.filter_by(form_id=latest.id).count(), 1)

        peer_form = self._make_form(self.officer_b, status='中心已审核')
        db.session.add(ScoreRecord(form_id=peer_form.id, total_personal_score=4))
        db.session.commit()

        stats = self._profile()
        self.assertEqual(stats['total_deduction'], 3)
        self.assertTrue(stats['has_evaluation_sample'])
        self.assertEqual(stats['percentile'], 1)
        self.assertEqual(self._profile(self.officer_b)['percentile'], 0.5)
        self.assertEqual(len(stats['assessment_items']), 1)
        self.assertEqual(stats['assessment_items'][0]['form_id'], latest.id)
        self.assertEqual(stats['assessment_items'][0]['personal_score'], 3)

    def test_route_a_department_recheck_does_not_duplicate_carried_scores(self):
        self._assert_carried_scores_count_once(ROUTE_A)

    def test_route_b_department_recheck_does_not_duplicate_carried_scores(self):
        self._assert_carried_scores_count_once(ROUTE_B)

    def test_department_recheck_without_scores_does_not_revive_history(self):
        original, group_approved = self._group_approved()
        self._login(self.dept_admin)
        latest = self._approve(ROUTE_A, group_approved, scores=[])
        self.assertNotEqual(latest.id, group_approved.id)
        self.assertEqual(len(original.get_all_versions()), 3)
        self.assertEqual(ScoreRecord.query.filter_by(form_id=group_approved.id).count(), 1)
        self._assert_latest_without_scores(latest)

    def test_department_rejection_does_not_revive_group_scores(self):
        original, group_approved = self._group_approved()
        self._login(self.dept_admin)
        response = post_opened_review(
            self.client, f'/admin/api/review/reject/{group_approved.id}',
            json={'reason': '部长复核发现错误'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'], response.get_json())
        latest = original.get_latest_version()
        self.assertEqual(latest.status, '已驳回')
        self.assertEqual(ScoreRecord.query.filter_by(form_id=group_approved.id).count(), 1)
        self._assert_latest_without_scores(latest)

    def test_legacy_base_and_newest_id_use_replacement_scores_only(self):
        original, group_approved = self._group_approved(legacy_base=True)
        self.assertIsNone(original.unique_id)
        self._login(self.dept_admin)
        latest = self._approve(ROUTE_A, group_approved, scores=[
            self._positive_score(reason='部长更正扣分', dept=1, pers=1),
        ])
        # Claims update historic activity timestamps; physical ID still decides
        # which score is current, including legacy bases with NULL unique_id.
        group_approved.updated_at = datetime(2030, 1, 1)
        latest.updated_at = datetime(2020, 1, 1)
        db.session.commit()
        stats = self._profile()
        self.assertEqual(stats['total_deduction'], 1)
        self.assertEqual(len(stats['assessment_items']), 1)
        self.assertEqual(stats['assessment_items'][0]['form_id'], latest.id)
        self.assertEqual(stats['assessment_items'][0]['reason'], '部长更正扣分')

    def test_zero_score_latest_remains_an_evaluated_sample(self):
        _original, group_approved = self._group_approved()
        self._login(self.dept_admin)
        latest = self._approve(ROUTE_A, group_approved, scores=[self._zero_score()])
        self.assertEqual(ScoreRecord.query.filter_by(form_id=latest.id).count(), 1)
        stats = self._profile()
        self.assertEqual(stats['total_deduction'], 0)
        self.assertTrue(stats['has_evaluation_sample'])
        self.assertEqual(stats['percentile'], 1)
        self.assertEqual(stats['assessment_items'], [])
