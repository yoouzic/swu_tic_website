import re
import unittest
from pathlib import Path


TEMPLATE = Path("app/templates/admin/statistics.html")


class StatisticsBootstrap5ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE.read_text(encoding="utf-8")

    def test_statistics_uses_bootstrap5_modal_and_spacing_contract(self):
        self.assertNotIn('data-dismiss="modal"', self.template)
        self.assertNotRegex(
            self.template,
            r'class\s*=\s*["\'][^"\']*(?<!-)\bclose\b(?!-)[^"\']*["\']',
        )
        self.assertNotRegex(
            self.template,
            r"\$\([^)]*\)\.modal\(\s*['\"](?:show|hide)['\"]",
        )
        self.assertNotIn("input-group-prepend", self.template)
        self.assertNotIn("input-group-append", self.template)
        self.assertNotRegex(self.template, r"\b(?:ml|mr)-[0-9]+\b")

        self.assertIn(
            "bootstrap.Modal.getOrCreateInstance(document.getElementById('leaveMakeupModal')).show()",
            self.template,
        )
        self.assertIn(
            "bootstrap.Modal.getOrCreateInstance(document.getElementById('leaveMakeupModal')).hide()",
            self.template,
        )
        self.assertIn(
            "bootstrap.Modal.getOrCreateInstance(document.getElementById('detailModal')).show()",
            self.template,
        )
        self.assertIn('data-bs-dismiss="modal"', self.template)
        self.assertIn('class="btn-close"', self.template)

    def test_statistics_preserves_shared_components_modal_markup_and_handlers(self):
        for fragment in ("metric-grid", "page_header"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.template)

        for modal_id in (
            "detailModal",
            "detailModalTitle",
            "detailModalContent",
            "leaveMakeupModal",
            "leaveMakeupModalTitle",
            "leaveMakeupModalHint",
            "leaveMakeupFormList",
            "btnSaveLeaveMakeup",
        ):
            with self.subTest(modal_id=modal_id):
                self.assertIn(modal_id, self.template)

        for handler in (
            "showLeaveMakeupModal",
            "hideLeaveMakeupModal",
            "openLeaveMakeupModal",
            "saveLeaveMakeupSelection",
            "loadLeaveStatus",
            "createCurrentWeekLeave",
            "resetFilters",
            "viewDetails",
            "exportStatistics",
        ):
            with self.subTest(handler=handler):
                self.assertRegex(
                    self.template,
                    rf"function\s+{re.escape(handler)}\s*\(",
                )

        self.assertIn('name="start_date"', self.template)
        self.assertIn('name="end_date"', self.template)
        self.assertIn('<span class="input-group-text">至</span>', self.template)


if __name__ == "__main__":
    unittest.main()
