from pathlib import Path
import unittest


class PeopleResponsiveContractTest(unittest.TestCase):
    def setUp(self):
        self.template = Path('app/templates/admin/manage_departments.html').read_text(encoding='utf-8')
        self.css = Path('app/static/css/style.css').read_text(encoding='utf-8')

    def test_people_cards_use_wrap_safe_summary_and_action_classes(self):
        for class_name in (
            'department-card__header',
            'department-card__actions',
            'department-evaluation__summary',
            'group-card__header',
            'group-card__actions',
            'group-user-row__identity',
            'group-user-row__actions',
        ):
            self.assertIn(class_name, self.template)
        self.assertIn('class="col-12 col-xl-6 mb-3 group-card"', self.template)
        self.assertNotIn('class="col-md-6 col-lg-4 mb-3 group-card"', self.template)

    def test_people_card_layout_has_mobile_and_overflow_guards(self):
        for selector in (
            '.department-card__header',
            '.department-card__actions',
            '.department-evaluation__summary',
            '.group-card__header',
            '.group-card__actions',
            '.group-user-row__identity',
            '.group-user-row__actions',
        ):
            self.assertIn(selector, self.css)
        self.assertIn('overflow-wrap: anywhere', self.css)
        mobile = self.css[self.css.index('@media (max-width: 768px)'):]
        self.assertIn('.group-user-row { grid-template-columns: 1fr; }', mobile)
        self.assertIn('.group-user-row__actions { justify-content: flex-start; }', mobile)


if __name__ == '__main__':
    unittest.main()
