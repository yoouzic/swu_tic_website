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

    def test_member_actions_keep_view_visible_and_move_admin_actions_into_menu(self):
        start = self.template.index('<div class="group-user-row__actions">')
        end = self.template.index("                        `).join('')}", start)
        actions = self.template[start:end]

        self.assertIn('data-user-action="view" data-user-id="${user.id}"', actions)
        self.assertIn('class="dropdown group-user-row__menu"', actions)
        self.assertIn('id="userActions${user.id}"', actions)
        self.assertIn('aria-label="更多用户操作：${escapePeopleHtml(user.name)}"', actions)
        self.assertIn('class="dropdown-menu dropdown-menu-end"', actions)
        self.assertIn('data-user-action="edit" data-user-id="${user.id}"', actions)
        self.assertIn('data-user-action="depart" data-user-id="${user.id}"', actions)
        self.assertIn('data-user-action="delete" data-user-id="${user.id}"', actions)
        self.assertIn('class="dropdown-divider"', actions)

        self.assertNotIn(
            'class="btn btn-sm btn-outline-secondary" onclick="editUser(',
            actions,
        )
        self.assertNotIn(
            'class="btn btn-sm btn-outline-danger" onclick="departUser(',
            actions,
        )
        self.assertNotIn(
            'class="btn btn-sm btn-outline-danger" onclick="deleteUser(',
            actions,
        )

    def test_member_action_menu_temporarily_releases_list_overflow(self):
        for marker in (
            "show.bs.dropdown",
            "hidden.bs.dropdown",
            "user-list--menu-open",
            ".group-user-row__menu",
            ".user-list.user-list--menu-open",
            "overflow: visible",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.template)


if __name__ == '__main__':
    unittest.main()
