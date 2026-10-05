"""Malformed review request envelopes must be controlled client errors."""
from app.models import LectureForm
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase


class LaunchReviewInputTest(_ReviewMutationCompatibilityBase):
    def test_immediate_auto_check_rejects_non_object_json(self):
        self._login(self.group_admin)
        for body in ([], True, 'invalid', None):
            with self.subTest(body=body):
                response = self.client.post('/admin/api/review/auto_check', json=body)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()['success'])
                self.assertEqual(LectureForm.query.count(), 0)

    def test_reference_query_rejects_non_object_json(self):
        self._login(self.group_admin)
        for body in ([], True, 'invalid', None):
            with self.subTest(body=body):
                response = self.client.post('/admin/api/review/reference_data', json=body)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()['success'])
                self.assertEqual(LectureForm.query.count(), 0)
