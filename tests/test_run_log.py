#!/usr/bin/env python3
"""Check the run log that conformance/check.py writes with --log. No Claude Code, no model.

The log is meant to be committed under conformance/runs/, so these tests check
its name, its header and rule lines, and that it carries no home directory.

    python3 -m unittest discover -s tests -v
"""
import importlib.util
import pathlib
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check", ROOT / "conformance" / "check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class RunLogTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".tmp"
        base.mkdir(exist_ok=True)
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="t-log-", dir=base))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_default_name(self):
        self.assertEqual(check.log_name("2.1.310 (Claude Code)", "Linux"), "2.1.310-linux.txt")
        self.assertEqual(check.log_name("2.1.310 (Claude Code)", "Darwin"), "2.1.310-macos.txt")
        self.assertEqual(check.log_name("", "Linux"), "unknown-linux.txt")

    def test_contents(self):
        home = str(pathlib.Path.home())
        results = [
            ("R01 registry record names a live socket", "PASS", "socket=%s/x/1.sock" % home),
            ("R02 minimal frame starts a turn", "FAIL", ""),
            ("R13 a silent connection is closed after 30 s", "SKIP", "--quick"),
        ]
        path = self.dir / "runs" / "log.txt"
        check.write_log(path, results, {"claude": "2.1.310 (Claude Code)", "platform": "Linux-6.12-x86_64",
                                        "date": "2026-10-26", "model": "haiku", "mode": "quick"})
        lines = path.read_text().splitlines()
        self.assertEqual(lines[:5], ["claude:   2.1.310 (Claude Code)", "platform: Linux-6.12-x86_64",
                                     "date:     2026-10-26", "model:    haiku", "mode:     quick"])
        self.assertIn("PASS R01 registry record names a live socket  -- socket=~/x/1.sock", lines)
        self.assertIn("FAIL R02 minimal frame starts a turn", lines)
        self.assertIn("SKIP R13 a silent connection is closed after 30 s  -- --quick", lines)
        self.assertEqual(lines[-1], "3 rules: 1 pass, 1 fail, 1 skipped")
        self.assertNotIn(home + "/", path.read_text())


if __name__ == "__main__":
    unittest.main()
