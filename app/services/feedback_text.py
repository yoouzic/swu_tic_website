"""Shared feedback length semantics; never modifies the submitted text."""
import re
import unicodedata


MINIMUM_FEEDBACK_CHARACTERS = 50
_KEYCAP_EMOJI = re.compile(r'[0-9#*]\ufe0f?\u20e3')


def count_feedback_characters(value):
    """Count Unicode letters/numbers, excluding punctuation, space and emoji.

    Each letter/number is one character, including supplementary Han letters.
    Combining marks do not add another character. Keycap emoji are removed as
    sequences so their ASCII digit does not count as written feedback.
    """
    text = '' if value is None else str(value)
    text = _KEYCAP_EMOJI.sub('', text)
    return sum(unicodedata.category(character)[0] in {'L', 'N'} for character in text)
