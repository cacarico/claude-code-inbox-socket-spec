#!/usr/bin/env python3
"""Check the summary table on the front page against the tags in the pages.

SPEC.md is the front page. Its summary table is the first one whose header row
starts with `| page |`. It has one row per page under `spec/`, then a `total`
row.

A unit's count for a tag is the number of times the tag word appears in its
text outside fenced code blocks, as a whole word and not after "Not" or "not".
"UNVERIFIED (bundle)" counts as UNVERIFIED. In a page the tags are in the
Evidence table at the bottom, one per row.

The page cell links to the page; the OFFICIAL, OBSERVED, THIRD-PARTY and
UNVERIFIED columns hold the counts. A page's covers cell is its first
paragraph, so --fix writes it.

    python3 tests/spec_table.py         check; print the correct row for each wrong one
    python3 tests/spec_table.py --fix   rewrite the links, counts and page covers in place

--fix adds a row for a page that has none, after the pages the table already
has. The order of the rows is the order of the pages in the table.

Exit status 1 when anything fails.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = ROOT / "SPEC.md"
PAGES = ROOT / "spec"
TAGS = ("OFFICIAL", "OBSERVED", "THIRD-PARTY", "UNVERIFIED")
TAG = re.compile(r"\b(OFFICIAL|OBSERVED|THIRD-PARTY|UNVERIFIED)\b")
NEGATED = re.compile(r"\b[Nn]ot\s+$")
HEADER = "| page | covers | %s |" % " | ".join(TAGS)


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
    """{tag: count} for one unit's text."""
    counts = dict.fromkeys(TAGS, 0)
    for m in TAG.finditer(text):
        # "Not OBSERVED" says a claim was not seen; it is not a tag.
        if not NEGATED.search(text, 0, m.start()):
            counts[m.group(1)] += 1
    return counts


def covers(lines):
    """A page's first paragraph after its title, as a phrase for the table."""
    body, seen = [], False
    for line in lines:
        if line.startswith("# "):
            seen = True
        elif seen and line.strip():
            body.append(line.strip())
        elif seen and body:
            break
    text = " ".join(body).rstrip(".")
    return text[:1].lower() + text[1:]


def pages():
    """[(name, title, covers, counts)] for each spec/*.md, by name."""
    found = []
    for path in sorted(PAGES.glob("*.md")):
        lines = path.read_text().split("\n")
        title = next((l[2:].strip() for l in lines if l.startswith("# ")), path.stem)
        found.append((path.stem, title, covers(lines), count("\n".join(strip_code(lines)))))
    return found


def cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def render(row):
    return "| " + " | ".join(row) + " |"


def find_table(lines):
    """(header line index, end index) of the summary table, or None."""
    for i, line in enumerate(lines):
        if re.match(r"\|\s*page\s*\|", line) and (i == 0 or not lines[i - 1].startswith("|")):
            end = i
            while end < len(lines) and lines[end].startswith("|"):
                end += 1
            return i, end
    return None


def key(cell):
    """The key a row's first cell names: ("page", name), "total", or None."""
    if cell.lower() == "total":
        return "total"
    m = re.search(r"\]\(spec/([^/)#]+)\.md\)", cell)
    return ("page", m.group(1)) if m else None


def target(cell):
    m = re.search(r"\]\(([^)]*)\)", cell)
    return m.group(1) if m else None


def label(k):
    if k == "total":
        return "the total row"
    return "page %s" % k[1]


def expected(found_pages, header, old):
    """The rows the table should hold: [(key, cells)], `old` giving the hand-written cells by key."""
    tag_col = {h: i for i, h in enumerate(header) if h in TAGS}
    cover_col = next((i for i, h in enumerate(header) if i > 0 and h not in TAGS), None)
    rows, total = [], dict.fromkeys(TAGS, 0)
    by_name = {name: (title, cov, counts) for name, title, cov, counts in found_pages}
    # Pages keep the order the table has; a new one follows by name.
    order = [k[1] for k in old if isinstance(k, tuple) and k[1] in by_name]
    order += [n for n in by_name if n not in order]
    for name in order:
        title, cov, counts = by_name[name]
        row = list(old.get(("page", name)) or [""] * len(header))
        row[0] = "[%s](spec/%s.md)" % (title, name)
        if cover_col is not None:
            row[cover_col] = cov
        for tag, i in tag_col.items():
            row[i] = str(counts[tag])
            total[tag] += counts[tag]
        rows.append((("page", name), row))
    row = list(old.get("total") or [""] * len(header))
    row[0] = "total"
    for tag, i in tag_col.items():
        row[i] = str(total[tag])
    rows.append(("total", row))
    return rows


def check(text, fix):
    """(errors, the file's text after --fix or None)."""
    lines = text.split("\n")
    code_free = strip_code(lines)
    found_pages = pages()
    found = find_table(code_free)
    if not found:
        rows = expected(found_pages, cells(HEADER), {})
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
            errors.append("SPEC.md:%d: the first cell names no page: %s" % (i + 1, row[0]))
        elif k in old:
            errors.append("SPEC.md:%d: a second row for %s" % (i + 1, label(k)))
        else:
            old[k] = row
            order.append((k, i + 1))
    rows = expected(found_pages, header, old)
    keys = [k for k, _ in rows]
    for k, n in order:
        if k not in keys:
            errors.append("SPEC.md:%d: a row for %s, which does not exist" % (n, label(k)))
    if [k for k, _ in order if k in keys] != [k for k in keys if k in old]:
        errors.append("SPEC.md:%d: the rows are not in order: the pages, then the total" % (start + 1))
    cover_cols = [i for i, h in enumerate(header) if i > 0 and h not in TAGS]
    line_of = dict(order)
    for k, row in rows:
        have = old.get(k)
        where = "SPEC.md:%d" % line_of[k] if k in line_of else "SPEC.md:%d" % (start + 1)
        if have is None:
            errors.append("%s: no row for %s; it should read:\n  %s" % (where, label(k), render(row)))
            continue
        wrong = [i for i in range(len(header)) if header[i] in TAGS and have[i] != row[i]]
        if k != "total" and target(have[0]) != target(row[0]):
            wrong.append(0)
        if k != "total":
            wrong += [i for i in cover_cols if have[i] != row[i]]
        if wrong:
            errors.append("%s: %s is wrong; it should read:\n  %s" % (where, label(k), render(row)))
        if k != "total" and any(not have[i] for i in cover_cols):
            errors.append("%s: %s: the covers cell is empty" % (where, label(k)))
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
