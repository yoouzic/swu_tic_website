# -*- coding: utf-8 -*-
"""Department rechecks of group-approved forms use isolated SQLite fixtures."""
from datetime import timedelta

from app.models import LectureForm, ScoreRecord, db
from app.ui.workspace import load_workspace_snapshot
from tests.review_request_utils import opened_review_revision, post_opened_review
from tests.test_review_mutation_compatibility import (
    ROUTE_A,
    ROUTE_B,
    _ReviewMutationCompatibilityBase,
)


class DepartmentReviewRecheckTest(_ReviewMutationCompatibilityBase):
    def _approve(self, route, form, comment, *, changes=None, scores=None):
        payload = {
            'form_data': self._full_form_data(form, **(changes or {})),
            'review_comment': comment,
            'score_data': scores or [],
        }
        return post_opened_review(
            self.client, route.format(form_id=form.id), json=payload,
        )

    def _group_approved(self, route=ROUTE_A):
        original = self._make_form(self.officer)
        self._login(self.group_admin)
        response = self._approve(
            route, original, '小组核对通过',
            scores=[self._positive_score(reason='小组评分', dept=2, pers=1)],
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'], response.get_json())
        approved = original.get_latest_version()
        self.assertNotEqual(approved.id, original.id)
        self.assertEqual(approved.status, '部门已审核')
        return original, approved

    def _assert_group_history(self, form_id):
        db.session.expire_all()
        historical = db.session.get(LectureForm, form_id)
        self.assertEqual(historical.status, '部门已审核')
        self.assertEqual(historical.reviewer_id, self.group_admin.id)
        self.assertEqual(historical.review_comment, '小组核对通过')
        self.assertEqual(historical.course_feedback, 'feedback-base')
        score = ScoreRecord.query.filter_by(form_id=form_id).one()
        self.assertEqual(score.reviewer_id, self.group_admin.id)
        self.assertEqual(score.total_department_score, 2)
        self.assertEqual(score.total_personal_score, 1)
        self.assertEqual([item.reason for item in score.items], ['小组评分'])

    def test_both_approval_routes_allow_department_correction_and_keep_history(self):
        for route in (ROUTE_A, ROUTE_B):
            with self.subTest(route=route):
                original, group_approved = self._group_approved(route)
                group_id = group_approved.id
                self._login(self.dept_admin)
                response = self._approve(
                    route, group_approved, '部长复核并更正反馈',
                    changes={'course_feedback': '经部长重新核对后补充的课程反馈。' * 4},
                    scores=[self._positive_score(reason='部长复核评分', dept=3, pers=2)],
                )
                self.assertEqual(response.status_code, 200)
                body = response.get_json()
                self.assertTrue(body['success'], body)
                self.assertEqual(body['new_status'], '部门已审核')
                latest = original.get_latest_version()
                self.assertNotEqual(latest.id, group_id)
                self.assertEqual(len(original.get_all_versions()), 3)
                self.assertEqual(latest.reviewer_id, self.dept_admin.id)
                self.assertIn('部长复核并更正反馈', latest.review_comment)
                self.assertIn('课程反馈', latest.review_comment)
                self.assertEqual(latest.status, '部门已审核')
                score = ScoreRecord.query.filter_by(form_id=latest.id).one()
                self.assertEqual(score.reviewer_id, self.dept_admin.id)
                self.assertEqual(score.total_department_score, 3)
                self._assert_group_history(group_id)

    def test_both_approval_routes_append_even_when_department_only_confirms(self):
        for route in (ROUTE_A, ROUTE_B):
            with self.subTest(route=route):
                original, group_approved = self._group_approved(route)
                group_id = group_approved.id
                self._login(self.dept_admin)
                response = self._approve(route, group_approved, '部长复核无误')
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()['success'], response.get_json())
                latest = original.get_latest_version()
                self.assertNotEqual(latest.id, group_id)
                self.assertEqual(latest.status, '部门已审核')
                self.assertEqual(latest.review_comment, '部长复核无误')
                self.assertEqual(latest.reviewer_id, self.dept_admin.id)
                self.assertEqual(len(original.get_all_versions()), 3)
                self._assert_group_history(group_id)

    def test_department_can_reject_forms_approved_by_either_group_route(self):
        for route in (ROUTE_A, ROUTE_B):
            with self.subTest(group_route=route):
                original, group_approved = self._group_approved(route)
                group_id = group_approved.id
                self._login(self.dept_admin)
                response = post_opened_review(
                    self.client, f'/admin/api/review/reject/{group_id}',
                    json={'reason': '部长复核发现事实错误，请重新核对'},
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()['success'], response.get_json())
                latest = original.get_latest_version()
                self.assertNotEqual(latest.id, group_id)
                self.assertEqual(latest.status, '已驳回')
                self.assertEqual(latest.reviewer_id, self.dept_admin.id)
                self.assertIn('事实错误', latest.review_comment)
                self.assertEqual(len(original.get_all_versions()), 3)
                self._assert_group_history(group_id)

    def test_group_department_and_center_can_finish_the_full_review_chain(self):
        original, group_approved = self._group_approved(ROUTE_A)
        self._login(self.dept_admin)
        response = self._approve(
            ROUTE_B, group_approved, '部长复核通过',
            changes={'course_feedback': '部长复核并补充事实依据后的课程反馈。' * 4},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'], response.get_json())
        department_approved = original.get_latest_version()
        self.assertNotEqual(department_approved.id, group_approved.id)
        self.assertEqual(department_approved.status, '部门已审核')

        self._login(self.center_admin)
        detail = self.client.get(f'/admin/api/review/form/{department_approved.id}')
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.get_json()['form']['can_review'])
        response = self._approve(ROUTE_A, department_approved, '中心最终核对通过')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'], response.get_json())
        final = original.get_latest_version()
        self.assertEqual(final.status, '中心已审核')
        self.assertEqual(final.reviewer_id, self.center_admin.id)
        self.assertEqual(final.course_feedback, department_approved.course_feedback)

        self._login(self.dept_admin)
        detail = self.client.get(f'/admin/api/review/form/{final.id}')
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.get_json()['form']['can_review'])
        count_before = len(original.get_all_versions())
        rejected = post_opened_review(
            self.client, f'/admin/api/review/reject/{final.id}',
            json={'reason': '部长不能改写中心终审结果'},
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertFalse(rejected.get_json()['success'])
        self.assertEqual(original.get_latest_version().id, final.id)
        self.assertEqual(len(original.get_all_versions()), count_before)
        self.assertEqual(load_workspace_snapshot(self.dept_admin).pending_forms, 0)
        self._assert_group_history(group_approved.id)

    def test_list_detail_and_workspace_count_offer_department_recheck(self):
        original, group_approved = self._group_approved()
        self._make_form(self.officer, status='待审核')
        self._make_form(self.officer, status='中心已审核')
        self._make_form(self.officer, status='已驳回')
        for actor, reviewable, pending_count in (
            (self.dept_admin, True, 2),
            (self.group_admin, False, 1),
            # 中心级可终审部门已审核表单，也可强制直审待审核表单：两个逻辑表单均可操作。
            (self.center_admin, True, 2),
        ):
            with self.subTest(actor=actor.number):
                self._login(actor)
                response = self.client.get('/admin/api/review/forms')
                self.assertEqual(response.status_code, 200)
                versions = {
                    row['id']: row
                    for group in response.get_json()['forms']
                    for row in group['forms']
                }
                self.assertEqual(versions[group_approved.id]['can_review'], reviewable)
                self.assertFalse(versions[original.id]['can_review'])
                for version, expected in ((group_approved, reviewable), (original, False)):
                    detail = self.client.get(f'/admin/api/review/form/{version.id}')
                    self.assertEqual(detail.status_code, 200)
                    self.assertEqual(detail.get_json()['form']['can_review'], expected)
                self.assertEqual(load_workspace_snapshot(actor).pending_forms, pending_count)

    def test_group_cannot_recheck_and_department_cannot_reopen_center_decision(self):
        for actor, status in (
            (self.group_admin, '部门已审核'),
            (self.dept_admin, '中心已审核'),
        ):
            with self.subTest(actor=actor.number, status=status):
                form = self._make_form(self.officer, status=status)
                self._login(actor)
                for route in (ROUTE_A, ROUTE_B):
                    response = self._approve(route, form, '越阶段操作')
                    self.assertFalse(response.get_json()['success'])
                rejected = post_opened_review(
                    self.client, f'/admin/api/review/reject/{form.id}',
                    json={'reason': '越阶段驳回'},
                )
                self.assertEqual(rejected.status_code, 403)
                self.assertFalse(rejected.get_json()['success'])
                self.assertEqual(len(form.get_all_versions()), 1)
                self.assertEqual(db.session.get(LectureForm, form.id).status, status)

    def test_department_recheck_still_rejects_cross_department_and_own_forms(self):
        self._login(self.dept_admin)
        for owner in (self.officer_b, self.dept_admin):
            with self.subTest(owner=owner.number):
                form = self._make_form(owner, status='部门已审核')
                for route in (ROUTE_A, ROUTE_B):
                    response = self._approve(route, form, '范围外复核')
                    self.assertEqual(response.status_code, 403)
                    self.assertFalse(response.get_json()['success'])
                response = self.client.post(
                    f'/admin/api/review/reject/{form.id}', json={'reason': '范围外驳回'},
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(len(form.get_all_versions()), 1)
                self.assertEqual(ScoreRecord.query.count(), 0)

    def test_department_recheck_cannot_mutate_a_historical_group_version(self):
        original, group_approved = self._group_approved()
        # Model an already recorded subsequent department decision without
        # depending on the recheck write path under test to establish history.
        latest = self._make_form(self.officer, status='部门已审核')
        latest.unique_id = original.id
        latest.reviewer_id = self.dept_admin.id
        latest.review_comment = '已完成的一次复核'
        db.session.commit()
        latest_id = latest.id
        self._login(self.dept_admin)
        for route in (ROUTE_A, ROUTE_B):
            response = self._approve(route, group_approved, '历史版本复核')
            self.assertFalse(response.get_json()['success'])
        response = post_opened_review(
            self.client, f'/admin/api/review/reject/{group_approved.id}',
            json={'reason': '历史版本驳回'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(original.get_latest_version().id, latest_id)
        self.assertEqual(len(original.get_all_versions()), 3)
        self._assert_group_history(group_approved.id)

    def test_department_recheck_rejects_stale_opened_revision_on_all_write_routes(self):
        for route in (ROUTE_A, ROUTE_B, '/admin/api/review/reject/{form_id}'):
            with self.subTest(route=route):
                original, group_approved = self._group_approved()
                self._login(self.dept_admin)
                revision = opened_review_revision(self.client, group_approved.id)
                group_approved.updated_at += timedelta(seconds=1)
                db.session.commit()
                payload = {
                    'form_data': self._full_form_data(group_approved),
                    'review_comment': '过期页面复核',
                    'reason': '过期页面驳回',
                    **revision,
                }
                response = self.client.post(route.format(form_id=group_approved.id), json=payload)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()['code'], 'review_conflict')
                self.assertEqual(len(original.get_all_versions()), 2)
                self._assert_group_history(group_approved.id)
