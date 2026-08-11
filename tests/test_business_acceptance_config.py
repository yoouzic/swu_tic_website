from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.business_acceptance.config import AcceptanceConfig


class BusinessAcceptanceConfigTest(unittest.TestCase):
    def test_defaults_are_frozen_and_validate_arithmetic_contract(self):
        with TemporaryDirectory() as tmp:
            config = AcceptanceConfig(runtime_root=Path(tmp))
            config.validate()

        self.assertEqual(config.seed, 260811)
        self.assertEqual(config.officer_count, 1002)
        self.assertEqual(config.logical_form_count, 1500)
        self.assertEqual(config.per_mode, 500)
        self.assertEqual(config.normal_per_mode, 75)
        self.assertEqual(config.http_attempt_ceiling, 1200)
        self.assertEqual(config.staged_concurrency, (4, 8, 16))
        self.assertEqual(config.staged_size_each, 20)
        self.assertEqual(config.semester, '2025-2026-2')
        self.assertEqual(config.semester_monday, '2026-03-02')

        with self.assertRaises(FrozenInstanceError):
            config.seed = 1

    def test_validate_rejects_count_and_path_contract_violations(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                AcceptanceConfig(runtime_root=root, per_mode=499).validate()
            with self.assertRaises(ValueError):
                AcceptanceConfig(runtime_root=root, normal_per_mode=501).validate()
            with self.assertRaises(ValueError):
                AcceptanceConfig(runtime_root=root, http_attempt_ceiling=0).validate()


if __name__ == '__main__':
    unittest.main()
