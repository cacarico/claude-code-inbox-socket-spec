#!/usr/bin/env python3
"""Check how tests/compat_table.py reads a run log. No Claude Code, no model.

    python3 -m unittest discover -s tests -v
"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compat_table", ROOT / "tests" / "compat_table.py")
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)

LOG = """claude:   2.1.311 (Claude Code)
platform: Darwin-24.1.0-arm64
date:     2026-11-02
model:    haiku
mode:     quick

PASS R02 minimal frame starts a turn  -- {"msgV": 1}
SKIP R13 a silent connection is closed after 30 s  -- --quick

2 rules: 1 pass, 0 fail, 1 skipped
"""


class CompatTableTests(unittest.TestCase):
    def test_a_log_becomes_a_row(self):
        self.assertEqual(compat.parse(LOG), ["2.1.311", "macOS", "1", "1 of 2", "2026-11-02"])

    def test_a_log_without_totals_is_refused(self):
        with self.assertRaises(ValueError):
            compat.parse(LOG.replace("2 rules: 1 pass, 0 fail, 1 skipped", ""))

    def test_newest_version_first(self):
        rows = [["2.1.9", "Linux"], ["2.1.310", "macOS"], ["2.1.310", "Linux"]]
        self.assertEqual([r[:2] for r in sorted(rows, key=compat.order)],
                         [["2.1.310", "Linux"], ["2.1.310", "macOS"], ["2.1.9", "Linux"]])


if __name__ == "__main__":
    unittest.main()
