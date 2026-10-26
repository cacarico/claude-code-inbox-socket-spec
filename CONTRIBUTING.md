# How this document is written

SPEC.md and the pages under `spec/` describe what Claude Code does; they do not
say what anyone should build. Each page is short, covers one topic, opens with
real frames and then says what they mean in plain sentences and field tables.
This page is the style that the pages, README.md, CHANGELOG.md and the comments
in the code follow.

## Before a change

`make check` runs without Claude Code and costs nothing. It lints the tags and
every relative link, checks the counts in the summary table on SPEC.md,
compiles the Python files, runs shellcheck when it is installed, and tests the
example clients against a fake receiver. Run it before every commit.
`make conformance` starts throwaway `claude -p` sessions and spends a few cents
of model use; run it after a Claude Code upgrade, not for a wording change.

A change keeps every evidence tag on the claim it covered. A claim may move to
another page with its row, and a new claim gets a row of its own. The counts in
the summary table prove it: `python3 tests/spec_table.py` prints the correct row
for each count that is wrong, and `--fix` rewrites the links, the counts and a
page's covers cell, which is the first paragraph under the page's title.

## Where the tags go

A page states what the protocol does and carries no tag word in its sentences,
tables of fields or examples. At the bottom it has an Evidence table, headed
`| Claim | Source |`, with one row per claim in the order of the page. The
Source cell holds one of the four tags, with a short qualifier when the claim
needs one, as in `OBSERVED (174-byte path, 2.1.310 on Linux)` or
`UNVERIFIED (bundle)`. A claim with two sources gets one row per source. The
linter fails a tag outside the table and a row without exactly one tag.

Something not known is a word in a table cell, such as "unknown" or "not seen",
or one short sentence.

## Style

1. Write about the protocol, in the indicative. "The receiver closes the
   connection after 30 seconds." Not "It is important to note that the
   receiver will close...", and not "In this section we describe...".
2. State a fact once, on the page it belongs to, and link to it elsewhere. Do
   not summarise at the end of a page, and do not write "as mentioned above".
   The error section gathers statements from the rest of the document on
   purpose, and each of its rows says where the statement is.
3. Use specific words: a number, a byte, a field name, a quoted log line. Leave
   out "robust", "seamless", "comprehensive", "carefully", "simply",
   "leverage", "ensure", "crucial", "key" and "powerful". An adjective that can
   be deleted without losing a fact is deleted.
4. Open a page with frames and replies that really occur, then explain them.
   Use paragraphs for reasoning, tables for fields and lists for enumerations.
   A list has items of one kind: fields, codes, states or steps. Three
   sentences in a row that each state a fact are a paragraph, not three
   bullets.
5. Use bold for nothing but a lead-in word that names an item of a short list.
   Headings are nouns. Code spans hold identifiers, paths, values and log text.
6. Vary the length of sentences. Put a qualification in the sentence it
   qualifies, not in a trailing fragment. Do not write triplets for rhythm,
   stacked hedges ("may possibly"), chains of em dashes, exclamation marks or
   rhetorical questions.
7. Say doubt flatly and in place: "The limit is 103 bytes." The Evidence table
   says how that is known.
8. Start a changelog line with a verb and name the thing: "Add section 15, one
   table of what the receiver does with bad input."
9. Write commit messages and pull request text in the same register. Say what
   changed and why, and name nothing outside this repository.
