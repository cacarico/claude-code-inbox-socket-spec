#!/usr/bin/env python3
"""Check conformance/version_check.py offline: the npm answer and the issue list
are fixtures, so nothing is fetched.

    python3 -m unittest discover -s tests -v
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "conformance" / "version_check.py"

CHANGELOG = """# Changelog

## Changes to this document

- Add section 15.

## 2.1.310, checked 2026-10-26 on Linux

- Everything in SPEC.md holds.

## 2.1.233, third party

## 2.1.224, official
"""


class VersionCheckTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".tmp"
        base.mkdir(exist_ok=True)
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="t-version-", dir=base))
        self.addCleanup(shutil.rmtree, self.dir, True)
        (self.dir / "CHANGELOG.md").write_text(CHANGELOG)

    def run_check(self, latest, issues=()):
        (self.dir / "npm.json").write_text(json.dumps({"name": "@anthropic-ai/claude-code", "version": latest}))
        (self.dir / "issues.json").write_text(json.dumps([{"title": t} for t in issues]))
        out = self.dir / "issue.md"
        out.unlink(missing_ok=True)
        r = subprocess.run([sys.executable, str(SCRIPT), "--changelog", str(self.dir / "CHANGELOG.md"),
                            "--npm-json", str(self.dir / "npm.json"), "--issues-json", str(self.dir / "issues.json"),
                            "--out", str(out)], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, (out.read_text() if out.exists() else None)

    def test_listed_version_needs_nothing(self):
        said, issue = self.run_check("2.1.310")
        self.assertIsNone(issue)
        self.assertIn("2.1.310 is in CHANGELOG.md", said)

    def test_newer_version_opens_an_issue(self):
        said, issue = self.run_check("2.1.320")
        title, _, body = issue.partition("\n")
        self.assertEqual(title, "Check Claude Code 2.1.320")
        self.assertIn("2.1.310", body)
        self.assertIn("python3 conformance/check.py --log", body)
        self.assertIn("2.1.320", said)

    def test_no_second_issue_for_the_same_version(self):
        said, issue = self.run_check("2.1.320", issues=["Check Claude Code 2.1.320", "Something else"])
        self.assertIsNone(issue)
        self.assertIn("already has an issue", said)

    def test_versions_compare_as_numbers(self):
        # 2.1.99 is older than 2.1.310, though it sorts after it as text.
        _, issue = self.run_check("2.1.99")
        self.assertIsNone(issue)
        _, issue = self.run_check("2.10.0")
        self.assertTrue(issue.startswith("Check Claude Code 2.10.0\n"))

    def test_a_bad_npm_answer_fails(self):
        (self.dir / "npm.json").write_text(json.dumps({"error": "not found"}))
        r = subprocess.run([sys.executable, str(SCRIPT), "--changelog", str(self.dir / "CHANGELOG.md"),
                            "--npm-json", str(self.dir / "npm.json"), "--out", str(self.dir / "issue.md")],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no version", r.stderr)
        self.assertFalse((self.dir / "issue.md").exists())


if __name__ == "__main__":
    unittest.main()
