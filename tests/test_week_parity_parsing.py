"""Teaching-week expressions must honour a parity marker PER SEGMENT.

The workbook writes ``1-3周(单),4-6周(双),7-12周,14-16周(双)``: each range carries
its own odd/even rule.  Parsing only the first marker (or rejecting the expression
because a later marker survived) silently drops or mis-dates real teaching weeks,
which makes a course disappear from 查课/候选 during weeks it is actually taught.
"""
import unittest

from app.review_automation.schedules.normalization import parse_weeks
from app.services.listening_assistant import _parse_week_set


class WeekParityTests(unittest.TestCase):
    def test_multi_range_with_mixed_parity_is_not_dropped(self):
        value = '1-3周(单),4-6周(双),7-12周,14-16周(双)'
        expected = frozenset({1, 3, 4, 6, 7, 8, 9, 10, 11, 12, 14, 16})
        self.assertEqual(_parse_week_set(value), expected)
        self.assertEqual(parse_weeks(value), expected)

    def test_single_marker_still_applies_to_its_own_range(self):
        self.assertEqual(_parse_week_set('1-3周(单)'), frozenset({1, 3}))
        self.assertEqual(_parse_week_set('4-6周(双)'), frozenset({4, 6}))

    def test_unmarked_range_keeps_every_week(self):
        """A trailing marker must not leak onto a range that has none."""
        self.assertEqual(_parse_week_set('4-6周(双),7-12周'), frozenset({4, 6, 7, 8, 9, 10, 11, 12}))
        self.assertEqual(_parse_week_set('1周,7-9周(单),10-14周'),
                         frozenset({1, 7, 9, 10, 11, 12, 13, 14}))
        self.assertEqual(_parse_week_set('5-7周(单),8-10周,15周'),
                         frozenset({5, 7, 8, 9, 10, 15}))

    def test_even_then_odd_ranges(self):
        self.assertEqual(_parse_week_set('10-12周(双),13-15周(单),16周'),
                         frozenset({10, 12, 13, 15, 16}))

    def test_real_workbook_shapes(self):
        self.assertEqual(_parse_week_set('2周,5周,13-15周(单)'), frozenset({2, 5, 13, 15}))
        self.assertEqual(_parse_week_set('1-4周,9-12周'), frozenset(range(1, 5)) | frozenset(range(9, 13)))
        self.assertEqual(_parse_week_set('1-16周'), frozenset(range(1, 17)))

    def test_invalid_expressions_still_fail_closed(self):
        for value in ['1-1000000000', '第0周', '', None, True, 'abc', '1-3周(单)(双)',
                      '(单)', '0-4周', '5-2周', '1-53周']:
            self.assertIsNone(_parse_week_set(value), value)
        with self.assertRaises(Exception):
            parse_weeks('第0周')
        with self.assertRaises(Exception):
            parse_weeks('abc')

    def test_both_parsers_agree_on_multi_range_input(self):
        for value in ['1-3周(单),4-6周(双),7-12周,14-16周(双)',
                      '10-12周(双),13-15周(单),16周',
                      '4-6周(双),7-12周',
                      '2周,5周,13-15周(单)',
                      '1-16周']:
            self.assertEqual(_parse_week_set(value), parse_weeks(value), value)

    def test_integer_and_numeric_forms(self):
        self.assertEqual(_parse_week_set(7), frozenset({7}))
        self.assertEqual(_parse_week_set(7.0), frozenset({7}))
        self.assertEqual(parse_weeks(7), frozenset({7}))

    def test_range_spacing_is_distinct_from_a_week_list_separator(self):
        cases = {
            '1 - 3周': frozenset({1, 2, 3}),
            '1 ~ 3周(单),4 - 6周(双)': frozenset({1, 3, 4, 6}),
            '1 至 3周,6 到 8周': frozenset({1, 2, 3, 6, 7, 8}),
            '1 - 3周 7 - 9周': frozenset({1, 2, 3, 7, 8, 9}),
            '1-3周 (单)': frozenset({1, 3}),
            '1-3周 ( 单 ) 4-6周 ( 双 )': frozenset({1, 3, 4, 6}),
            '1 4': frozenset({1, 4}),
            '1-4 6-8': frozenset({1, 2, 3, 4, 6, 7, 8}),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_parse_week_set(raw), expected)
                self.assertEqual(parse_weeks(raw), expected)


if __name__ == '__main__':
    unittest.main()
