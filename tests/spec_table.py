#!/usr/bin/env python3
"""Check the summary table of SPEC.md against the tags in its sections.

A section is a `## N.` heading with N of 1 or more; section 0 describes the
document, makes no claims and has no row. A section's count for a tag is the
number of times the tag word appears in its text outside fenced code blocks,
as a whole word and not after "Not" or "not". "UNVERIFIED (bundle)" counts as
UNVERIFIED, and a statement with two tags counts once for each.

The table is the first one whose header row starts with `| section |`. It has
one row per section, in order, then a `total` row. The section cell links to
the section's heading; the OFFICIAL, OBSERVED, THIRD-PARTY and UNVERIFIED
columns hold the counts; any other column says what the section covers and is
written by hand.

    python3 tests/spec_table.py         check; print the correct row for each wrong one
    python3 tests/spec_table.py --fix   rewrite the links and counts in place

--fix keeps the hand-written cells and adds a row for a section that has none,
with an empty covers cell, which the check refuses until it is filled.

Exit status 1 when anything fails.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = ROOT / "SPEC.md"
TAGS = ("OFFICIAL", "OBSERVED", "THIRD-PARTY", "UNVERIFIED")
TAG = re.compile(r"\b(OFFICIAL|OBSERVED|THIRD-PARTY|UNVERIFIED)\b")
NEGATED = re.compile(r"\b[Nn]ot\s+$")
HEADER = "| section | covers | %s |" % " | ".join(TAGS)


def slug(heading):
    """GitHub's anchor for a heading, as tests/lint_docs.py computes it."""
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def strip_code(lines):
    """Blank the fenced code blocks, keeping line numbers."""
    out, fence = [], False
    for line in lines:
        if line.startswith("```"):
            fence = not fence
            out.append("")
            continue
        out.append("" if fence else line)
    return out


def count(text):
    """{tag: count} for one section's text."""
    counts = dict.fromkeys(TAGS, 0)
    for m in TAG.finditer(text):
        # "Not OBSERVED" says a claim was not seen; it is not a tag.
        if not NEGATED.search(text, 0, m.start()):
            counts[m.group(1)] += 1
    return counts


def sections(lines):
    """[(number, heading, counts)] for each numbered section from 1, in file order."""
    found, current, body = [], None, []

    def close():
        if current:
            found.append(current + (count("\n".join(body)),))

    for line in lines:
        if line.startswith("## "):
            close()
            current, body = None, []
            m = re.match(r"## (\d+)\. ", line)
            if m and int(m.group(1)) > 0:
                current = (int(m.group(1)), line[3:].strip())
            continue
        body.append(line)
    close()
    return found


def cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def render(row):
    return "| " + " | ".join(row) + " |"


def find_table(lines):
    """(header line index, end index) of the summary table, or None."""
    for i, line in enumerate(lines):
        if re.match(r"\|\s*section\s*\|", line) and (i == 0 or not lines[i - 1].startswith("|")):
            end = i
            while end < len(lines) and lines[end].startswith("|"):
                end += 1
            return i, end
    return None


def expected(spec, header, old):
    """The rows the table should hold: [(key, cells)], `old` giving the hand-written cells by key."""
    tag_col = {h: i for i, h in enumerate(header) if h in TAGS}
    rows, total = [], dict.fromkeys(TAGS, 0)
    for n, heading, counts in spec:
        row = list(old.get(n) or [""] * len(header))
        row[0] = "[%d %s](#%s)" % (n, heading.split(". ", 1)[1], slug(heading))
        for tag, i in tag_col.items():
            row[i] = str(counts[tag])
            total[tag] += counts[tag]
        rows.append((n, row))
    row = list(old.get("total") or [""] * len(header))
    row[0] = "total"
    for tag, i in tag_col.items():
        row[i] = str(total[tag])
    rows.append(("total", row))
    return rows


def key(cell):
    """The section number a row's first cell names, "total", or None."""
    if cell.lower() == "total":
        return "total"
    m = re.match(r"\[?(\d+)\b", cell)
    return int(m.group(1)) if m else None


def target(cell):
    m = re.search(r"\]\((#[^)]*)\)", cell)
    return m.group(1) if m else None


def check(text, fix):
    """(errors, the file's text after --fix or None)."""
    lines = text.split("\n")
    code_free = strip_code(lines)
    spec = sections(code_free)
    found = find_table(code_free)
    if not found:
        rows = expected(spec, cells(HEADER), {})
        return ["SPEC.md: no summary table; it needs these rows:", HEADER,
                render(["---"] * len(cells(HEADER)))] + [render(r) for _, r in rows], None
    start, end = found
    header = cells(lines[start])
    missing = [t for t in TAGS if t not in header]
    if missing:
        return ["SPEC.md:%d: the summary table has no column for %s" % (start + 1, ", ".join(missing))], None
    if start + 1 >= end or set("".join(cells(lines[start + 1]))) - set("-: "):
        return ["SPEC.md:%d: the summary table has no separator row under its header" % (start + 1)], None
    errors, old, order = [], {}, []
    for i in range(start + 2, end):
        row = cells(lines[i])
        k = key(row[0])
        if len(row) != len(header):
            errors.append("SPEC.md:%d: %d cells, the header has %d" % (i + 1, len(row), len(header)))
        elif k is None:
            errors.append("SPEC.md:%d: the first cell names no section: %s" % (i + 1, row[0]))
        elif k in old:
            errors.append("SPEC.md:%d: a second row for section %s" % (i + 1, k))
        else:
            old[k] = row
            order.append((k, i + 1))
    rows = expected(spec, header, old)
    keys = [k for k, _ in rows]
    for k, n in order:
        if k not in keys:
            errors.append("SPEC.md:%d: a row for section %s, which SPEC.md does not have" % (n, k))
    if [k for k, _ in order if k in keys] != [k for k in keys if k in old]:
        errors.append("SPEC.md:%d: the rows are not in section order" % (start + 1))
    covers = [i for i, h in enumerate(header) if i > 0 and h not in TAGS]
    line_of = dict(order)
    for k, row in rows:
        have = old.get(k)
        where = "SPEC.md:%d" % line_of[k] if k in line_of else "SPEC.md:%d" % (start + 1)
        name = "the total row" if k == "total" else "section %s" % k
        if have is None:
            errors.append("%s: no row for %s; it should read:\n  %s" % (where, name, render(row)))
            continue
        wrong = [i for i in range(len(header)) if header[i] in TAGS and have[i] != row[i]]
        if k != "total" and target(have[0]) != target(row[0]):
            wrong.append(0)
        if wrong:
            errors.append("%s: %s is wrong; it should read:\n  %s" % (where, name, render(row)))
        if k != "total" and any(not have[i] for i in covers):
            errors.append("%s: %s: the covers cell is empty" % (where, name))
    if not fix:
        return errors, None
    table = lines[start:start + 2] + [render(r) for _, r in rows]
    return errors, "\n".join(lines[:start] + table + lines[end:])


def main(argv):
    fix = "--fix" in argv[1:]
    text = SPEC.read_text()
    errors, fixed = check(text, fix)
    if fixed is not None:
        if fixed != text:
            SPEC.write_text(fixed)
            print("spec_table: rewrote the summary table")
        # What is left after the rewrite, such as a covers cell to fill.
        errors, _ = check(fixed, False)
    for e in errors:
        print(e)
    if errors and errors[0].startswith("SPEC.md: no summary table"):
        # --fix writes rows, not where the table goes: that place is chosen by hand.
        print("spec_table: no summary table" + ("; put its header and separator rows in SPEC.md, then run --fix" if fix else ""))
        return 1
    print("spec_table: %s" % ("%d problem(s)" % len(errors) if errors else "ok"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
