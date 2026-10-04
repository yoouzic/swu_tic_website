"""Rendered hierarchy, rather than isolated child.hidden, determines visibility."""
from html.parser import HTMLParser
import os
from pathlib import Path
import tempfile
import unittest

TEMP = tempfile.TemporaryDirectory(prefix='photo-guide-visibility-')
for key, value in {
    'SQLITE_DB_PATH': str(Path(TEMP.name) / 'visibility.sqlite'),
    'INSTANCE_DIR': str(Path(TEMP.name) / 'instance'),
    'UPLOAD_FOLDER': str(Path(TEMP.name) / 'uploads'),
    'AUTOMATION_UPLOAD_DIR': str(Path(TEMP.name) / 'automation'),
    'DATABASE_URL': '', 'DEEPSEEK_API_KEY': '', 'STORAGE_CLEANUP_ENABLED': '0',
    'LECTURE_CAPTURE_ENABLED': '1',
}.items():
    os.environ[key] = value

from app.app import app
from app.models import db, User
from tests.app_test_utils import configure_sqlite_database, cleanup_sqlite_database


class Hierarchy(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'ancestors': list(self.stack)}
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def one(self, attribute):
        values = [node for node in self.nodes if attribute in node['attrs']]
        if len(values) != 1:
            raise AssertionError(f'Expected one {attribute}, got {len(values)}')
        return values[0]


class PhotoGuideVisibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.context = app.app_context()
        cls.context.push()
        db.create_all()
        user = User(number='VISIBILITY', student_id='visibility', name='Guide test',
                    role='信息员', department='test', group='test', is_active=True,
                    gender='-', grade='-', college='test', major='-', dormitory='-',
                    phone='-', qq='-', password_hash='unused-synthetic')
        db.session.add(user)
        db.session.commit()
        cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cleanup_sqlite_database(db, drop_all=True)
        cls.context.pop()
        TEMP.cleanup()

    def page(self, capture):
        app.config['LECTURE_CAPTURE_ENABLED'] = capture
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(user_id=self.user_id, user_role='信息员')
            response = client.get('/user/submit_form')
        self.assertEqual(response.status_code, 200)
        result = Hierarchy()
        result.feed(response.get_data(as_text=True))
        return result

    def test_evaluation_questions_are_not_children_of_hidden_course_finder(self):
        question = self.page(True).one('data-form-completion')
        self.assertFalse(any('data-listening-assistant' in parent['attrs'] for parent in question['ancestors']))

    def test_photo_evaluation_has_its_own_initially_hidden_visibility_gate(self):
        hierarchy = self.page(True)
        gate = hierarchy.one('data-site-review-guide')
        question = hierarchy.one('data-form-completion')
        self.assertIn('hidden', gate['attrs'])
        self.assertIn(gate, question['ancestors'])

    def test_standard_form_does_not_require_photo_review_gate_to_activate_questions(self):
        gate = self.page(False).one('data-site-review-guide')
        self.assertNotIn('hidden', gate['attrs'])

    def test_evaluation_component_remains_in_the_real_submission_form(self):
        question = self.page(True).one('data-form-completion')
        self.assertTrue(any(parent['tag'] == 'form' and parent['attrs'].get('id') == 'lectureForm'
                            for parent in question['ancestors']))


if __name__ == '__main__':
    unittest.main()
