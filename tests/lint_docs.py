#!/usr/bin/env python3
"""Check the Markdown files without the network.

- SPEC.md: every bullet and every table row in a numbered section says how it is
  known, with at least one of the tags OFFICIAL, OBSERVED, THIRD-PARTY or
  UNVERIFIED. Sections 0 and 16 (about the document and its files) and 13 (a
  recipe) make no claims and are left out.
- Every Markdown file: relative links point at files that exist, and #anchors
  at headings that exist. External links are not fetched.

Exit status 1 when anything fails.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TAG = re.compile(r"\b(OFFICIAL|OBSERVED|THIRD-PARTY|UNVERIFIED)\b")
UNTAGGED_SECTIONS = {"0", "13", "16"}
errors = []


def slug(heading):
    """GitHub's anchor for a heading."""
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def strip_code(text):
    """Drop fenced code blocks, keeping line numbers."""
    out, fence = [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            fence = not fence
            out.append("")
            continue
        out.append("" if fence else line)
    return out


def spec_tags():
    lines = strip_code((ROOT / "SPEC.md").read_text())
    section = None
    item, start = [], 0
    header_row = None

    def flush():
        if item and section and section not in UNTAGGED_SECTIONS:
            text = " ".join(item)
            if not TAG.search(text):
                errors.append("SPEC.md:%d: section %s: untagged: %s" % (start, section, text[:90]))

    for n, line in enumerate(lines, 1):
        m = re.match(r"## (\d+)\.", line)
        if m or line.startswith("## "):
            flush(); item = []
            section = m.group(1) if m else None
            continue
        if line.startswith("- ") or line.startswith("|"):
            flush(); item = []
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if set("".join(cells)) <= set("-: "):
                    continue  # the separator row
                if header_row is None or not lines[n - 2].startswith("|"):
                    header_row = cells
                    continue  # a table's first row is its header
                # Only tables with a tag column are claims; the others are lookup tables
                # (registry fields) covered by the statement that introduces them.
                if "tag" not in [h.lower() for h in header_row]:
                    continue
            item, start = [line], n
        elif line.startswith("  ") and item:
            item.append(line.strip())
        else:
            flush(); item = []
    flush()


def links():
    headings = {}
    files = sorted(p for p in ROOT.rglob("*.md") if ".tmp" not in p.parts and ".git" not in p.parts)
    for f in files:
        headings[f] = {slug(m.group(1)) for m in re.finditer(r"^#+ (.+)$", "\n".join(strip_code(f.read_text())), re.M)}
    for f in files:
        for n, line in enumerate(strip_code(f.read_text()), 1):
            for target in re.findall(r"\]\(([^)\s]+)\)", line):
                if re.match(r"[a-z]+:", target):
                    continue
                path, _, anchor = target.partition("#")
                dest = (f.parent / path).resolve() if path else f
                rel = f.relative_to(ROOT)
                if not dest.exists():
                    errors.append("%s:%d: link to a missing file: %s" % (rel, n, target))
                elif anchor and dest.suffix == ".md" and anchor not in headings.get(dest, set()):
                    errors.append("%s:%d: link to a missing heading: %s" % (rel, n, target))


spec_tags()
links()
for e in errors:
    print(e)
print("lint_docs: %s" % ("%d problem(s)" % len(errors) if errors else "ok"))
sys.exit(1 if errors else 0)
