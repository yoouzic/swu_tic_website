"""The submitted-feedback threshold counts letters and numbers, not decoration."""
from types import SimpleNamespace
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from app.services.lecture_form_validation import review_field_errors, submission_field_errors
from app.services.feedback_text import count_feedback_characters
from app.review_automation.rules.registry import RuleEngine
from app.utils.auto_review import AutoReviewEngine


CASES = [
    ('49_with_punctuation', '课' * 49 + '，。！？,.!?\n\t 🙂👍🏽', 49),
    ('50_mixed', '课' * 20 + 'Abcde' * 3 + '12345' * 3 + '， \n🙂', 50),
    ('51_mixed', '课' * 21 + 'Abcde' * 3 + '12345' * 3 + '。\n', 51),
    ('49_english', 'A' * 49 + ',.!?\n 🙂', 49),
    ('50_english', 'A' * 50 + ',.!?\n 🙂', 50),
    ('50_digits', '0123456789' * 5 + '。😀', 50),
    ('50_supplementary_chinese', '𠮷' * 50 + '。', 50),
    ('50_decomposed_accents', 'e\u0301' * 50, 50),
    ('50_fullwidth_letters_and_digits', 'Ａ１' * 25, 50),
    ('49_with_keycap_emoji', '课' * 49 + '1️⃣', 49),
    ('symbols_only', '，。!\n\t 🙂👍🏽★+' * 20, 0),
    ('ordinary_existing_text', '课堂重点讲解清楚' * 10, 80),
]


class FeedbackCharacterCountTests(unittest.TestCase):
    def test_python_and_browser_helper_agree_on_the_same_unicode_cases(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required to verify browser/Python Unicode parity')
        script = '''
const fs = require('node:fs');
const helper = require(process.argv[1]);
const inputs = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(inputs.map(helper.countFeedbackCharacters)));
'''
        helper = Path(__file__).resolve().parents[1] / 'app/static/js/feedback-text.js'
        result = subprocess.run([node, '-e', script, str(helper)],
                                input=json.dumps([text for _, text, _ in CASES]),
                                text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [count for _, _, count in CASES])

    def test_counts_meaningful_characters_without_changing_original_text(self):
        for label, text, count in CASES:
            with self.subTest(label=label):
                self.assertEqual(count_feedback_characters(text), count)

    def test_submission_and_changed_review_text_share_the_threshold(self):
        for label, text, count in CASES:
            with self.subTest(label=label):
                self.assertEqual('course_feedback' in submission_field_errors({'course_feedback': text}), count < 50)
                self.assertEqual('course_feedback' in review_field_errors(
                    SimpleNamespace(course_feedback='原始反馈'), {'course_feedback': text}), count < 50)

    def test_unchanged_historical_feedback_remains_reviewable(self):
        text = '旧反馈，。'
        self.assertEqual(review_field_errors(SimpleNamespace(course_feedback=text),
                                             {'course_feedback': text}), {})

    def test_legacy_auto_review_uses_the_same_threshold(self):
        engine = object.__new__(AutoReviewEngine)
        for label, text, count in CASES:
            with self.subTest(label=label):
                issues, fixes = [], []
                engine._validate_teaching_evaluation(
                    SimpleNamespace(course_feedback=text, suggestions='无'), issues, fixes)
                short = any('课程反馈' in issue and '字' in issue for issue in issues)
                self.assertEqual(short, count < 50)

    def test_rule_engine_counts_mixed_text_and_ignores_punctuation(self):
        engine = RuleEngine([{
            'rule_key': 'short', 'version': 1, 'handler': 'minimum_length',
            'enabled': True, 'severity': 'review',
            'parameters': {'minimum_characters': 50},
        }])
        for label, text, count in CASES:
            with self.subTest(label=label):
                findings = engine.evaluate({'course_feedback': text})
                self.assertEqual(bool(findings), count < 50)
                if findings:
                    self.assertEqual(findings[0].evidence['character_count'], count)


if __name__ == '__main__':
    unittest.main()
