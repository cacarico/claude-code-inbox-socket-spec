#!/usr/bin/env python3
"""Check the compatibility table in CHANGELOG.md against the logs in conformance/runs/.

The table is the first one whose header row starts with `| Claude Code |`. It has
one row per log: the version from `claude --version`, the operating system from
the platform line, the `msgV` the frames in the log carry, how many rules passed
out of how many ran, and the date. Newest version first, then by operating system.

    python3 tests/compat_table.py         check; print the correct table when it is wrong
    python3 tests/compat_table.py --fix   rewrite the table in place

Exit status 1 when anything fails.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
RUNS = ROOT / "conformance" / "runs"
HEADER = ["Claude Code", "OS", "msgV", "rules passed", "date"]
SYSTEMS = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}


def parse(text, name="log"):
    """The row cells for one log: [version, os, msgV, "P of N", date]."""
    def field(label):
        m = re.search(r"^%s:\s+(.+)$" % label, text, re.M)
        if not m:
            raise ValueError("%s: no `%s:` line" % (name, label))
        return m.group(1).strip()

    version = field("claude").split()[0]
    system = field("platform").split("-")[0]
    totals = re.search(r"^(\d+) rules: (\d+) pass, (\d+) fail, (\d+) skipped$", text, re.M)
    if not totals:
        raise ValueError("%s: no totals line" % name)
    msgv = sorted(set(re.findall(r'"msgV": (\d+)', text)))
    passed = "%s of %s" % (totals.group(2), totals.group(1))
    return [version, SYSTEMS.get(system, system), ", ".join(msgv) or "unknown", passed, field("date")]


def order(row):
    return ([-int(n) for n in re.findall(r"\d+", row[0])], row[1])


def rows():
    found = [parse(p.read_text(), p.name) for p in sorted(RUNS.glob("*.txt"))]
    return sorted(found, key=order)


def render(cells):
    return "| " + " | ".join(cells) + " |"


def expected():
    return [render(HEADER), "|" + "---|" * len(HEADER)] + [render(r) for r in rows()]


def find_table(lines):
    for i, line in enumerate(lines):
        if line.startswith("| Claude Code |"):
            end = i
            while end < len(lines) and lines[end].startswith("|"):
                end += 1
            return i, end
    return None


def main(argv):
    text = CHANGELOG.read_text()
    lines = text.split("\n")
    found = find_table(lines)
    want = expected()
    if not found:
        print("CHANGELOG.md: no compatibility table; it needs these rows:")
        print("\n".join(want))
        print("compat_table: 1 problem(s)")
        return 1
    start, end = found
    if lines[start:end] == want:
        print("compat_table: ok")
        return 0
    if "--fix" in argv[1:]:
        CHANGELOG.write_text("\n".join(lines[:start] + want + lines[end:]))
        print("compat_table: rewrote the table")
        return 0
    print("CHANGELOG.md:%d: the compatibility table does not match conformance/runs/; it should read:" % (start + 1))
    print("\n".join(want))
    print("compat_table: 1 problem(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
