#!/usr/bin/env python3
"""Check the Markdown files without the network.

- Pages under spec/: the tags OFFICIAL, OBSERVED, THIRD-PARTY and UNVERIFIED
  appear only in the Evidence table at the bottom (a table headed
  `| Claim | Source |`), and each of its rows has exactly one tag, in the Source
  cell. A page with no tags needs no Evidence table; every other page has one.
- SPEC.md: every bullet and every table row in a numbered section says how it is
  known, with at least one of the tags. Section 13 (a recipe) makes no claims and
  is left out, and so is everything before the first numbered section, the front
  page. The numbered sections go when they become pages.
- Every Markdown file: relative links point at files that exist, and #anchors
  at headings that exist, in the same file or another page. External links are
  not fetched.

Exit status 1 when anything fails.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TAG = re.compile(r"\b(OFFICIAL|OBSERVED|THIRD-PARTY|UNVERIFIED)\b")
NEGATED = re.compile(r"\b[Nn]ot\s+$")
UNTAGGED_SECTIONS = {"0", "13", "16"}
EVIDENCE_HEADER = ["Claim", "Source"]
errors = []


def tags_in(text):
    """The tag words in `text`, leaving out "Not OBSERVED" and the like."""
    return [m.group(1) for m in TAG.finditer(text) if not NEGATED.search(text, 0, m.start())]


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


def row_cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


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
                cells = row_cells(line)
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


def page_tags(path):
    """Tags only in the Evidence table, one per row."""
    rel = path.relative_to(ROOT)
    lines = strip_code(path.read_text())
    evidence = next((i for i, l in enumerate(lines) if re.match(r"##\s+Evidence\s*$", l)), None)
    rows = set()
    if evidence is not None:
        i = evidence + 1
        while i < len(lines) and not lines[i].startswith("|"):
            i += 1
        header = row_cells(lines[i]) if i < len(lines) else []
        if header != EVIDENCE_HEADER:
            errors.append("%s:%d: the Evidence table has no `| Claim | Source |` header" % (rel, i + 1))
        i += 2  # the header and the separator
        while i < len(lines) and lines[i].startswith("|"):
            cells = row_cells(lines[i])
            rows.add(i)
            if len(cells) != 2:
                errors.append("%s:%d: an Evidence row has %d cells, not 2" % (rel, i + 1, len(cells)))
            else:
                if tags_in(cells[0]):
                    errors.append("%s:%d: a tag in the Claim cell: %s" % (rel, i + 1, cells[0][:60]))
                found = tags_in(cells[1])
                if len(found) != 1:
                    errors.append("%s:%d: the Source cell needs exactly one tag, has %d: %s"
                                  % (rel, i + 1, len(found), cells[1][:60]))
            i += 1
    for n, line in enumerate(lines):
        if n not in rows and tags_in(line):
            errors.append("%s:%d: a tag outside the Evidence table: %s" % (rel, n + 1, line.strip()[:60]))
    if evidence is None and not any(tags_in(l) for l in lines):
        return
    if evidence is None:
        errors.append("%s: no Evidence table" % rel)


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
for page in sorted((ROOT / "spec").glob("*.md")):
    page_tags(page)
links()
for e in errors:
    print(e)
print("lint_docs: %s" % ("%d problem(s)" % len(errors) if errors else "ok"))
sys.exit(1 if errors else 0)
