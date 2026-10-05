"""Consumers must not identify an old edited row as the current version."""
from datetime import datetime

from app.models import db
from app.services.review_form_queries import latest_form_groups_for_users
from app.utils.leave_management import _latest_form_groups_for_user_number
from tests.test_review_mutation_compatibility import _ReviewMutationCompatibilityBase


class BusinessLatestSemanticsTest(_ReviewMutationCompatibilityBase):
    def test_review_and_leave_use_physical_version_order(self):
        old = self._make_form(self.officer)
        new = self._make_form(self.officer, status='中心已审核')
        new.unique_id = old.id
        old.created_at = datetime(2026, 10, 1)
        new.created_at = datetime(2026, 9, 1)
        old.updated_at = datetime(2026, 10, 2)
        db.session.commit()
        for get_groups in (lambda: latest_form_groups_for_users([self.officer.number]),
                           lambda: _latest_form_groups_for_user_number(self.officer.number)):
            with self.subTest(consumer=get_groups):
                groups = get_groups()
                self.assertEqual(groups[0]['latest_form'].id, new.id)
                self.assertEqual([form.id for form in groups[0]['forms']], [new.id, old.id])
