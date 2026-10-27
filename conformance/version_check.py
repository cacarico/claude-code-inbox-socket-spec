#!/usr/bin/env python3
"""Say whether a Claude Code release is newer than every version in CHANGELOG.md.

The weekly workflow (.github/workflows/version-check.yml) runs this. It reads the
latest version of @anthropic-ai/claude-code from the npm registry and the
`## X.Y.Z` headings of CHANGELOG.md. When the release is newer than all of them
and no issue has its title yet, it writes the issue to open into --out: the
title on the first line, the body after it. Otherwise it writes nothing. It
needs no model and no token; the workflow opens the issue.

    python3 conformance/version_check.py --out issue.md
    python3 conformance/version_check.py --npm-json latest.json --issues-json issues.json --out issue.md

--npm-json reads the registry's answer from a file instead of fetching it, and
--issues-json is the output of `gh issue list --json title`. Exit status 1 when
the registry gives no version.
"""
import argparse
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = "https://registry.npmjs.org/@anthropic-ai/claude-code/latest"
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def listed_versions(changelog):
    """The versions CHANGELOG.md has a section for, as (major, minor, patch)."""
    return [tuple(int(n) for n in m.groups()) for m in re.finditer(r"^## (\d+)\.(\d+)\.(\d+)\b", changelog, re.M)]


def latest_version(npm_json):
    data = json.loads(npm_json.read_text()) if npm_json else json.load(urllib.request.urlopen(REGISTRY, timeout=30))
    m = VERSION.match(str(data.get("version", "")))
    return tuple(int(n) for n in m.groups()) if m else None


def name(version):
    return ".".join(str(n) for n in version)


def issue_for(version, newest_listed):
    title = "Check Claude Code %s" % name(version)
    body = """Claude Code %s is on npm, and CHANGELOG.md goes up to %s.

To check it, install that version, then from the repository root run

    python3 conformance/check.py --log

which writes conformance/runs/%s-<os>.txt. Add a section for the version to
CHANGELOG.md with what passed and what changed, and fix any statement in the
spec the run contradicts.
""" % (name(version), name(newest_listed) if newest_listed else "no version", name(version))
    return title, body


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--changelog", type=pathlib.Path, default=ROOT / "CHANGELOG.md")
    ap.add_argument("--npm-json", type=pathlib.Path, help="the registry's answer, read from a file")
    ap.add_argument("--issues-json", type=pathlib.Path, help="existing issues, as `gh issue list --json title` prints them")
    ap.add_argument("--out", type=pathlib.Path, required=True, help="where to write the issue, if one is needed")
    args = ap.parse_args()

    latest = latest_version(args.npm_json)
    if latest is None:
        print("version_check: the registry gave no version", file=sys.stderr)
        return 1
    listed = listed_versions(args.changelog.read_text())
    newest = max(listed, default=None)
    if latest in listed or (newest and latest < newest):
        print("Claude Code %s is in CHANGELOG.md, or older than what it lists" % name(latest))
        return 0
    title, body = issue_for(latest, newest)
    titles = {i.get("title") for i in json.loads(args.issues_json.read_text())} if args.issues_json else set()
    if title in titles:
        print("Claude Code %s already has an issue" % name(latest))
        return 0
    args.out.write_text(title + "\n" + body)
    print("Claude Code %s is newer than CHANGELOG.md: wrote %s" % (name(latest), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
