# How this document is written

SPEC.md describes what Claude Code does; it does not say what anyone should
build. It reads like the normative pages of RFC 6455 (sections 4 to 8), the
Language Server Protocol's lifecycle section and the Model Context Protocol's
lifecycle page, without their keywords: the document is descriptive, and its
section 0 says so. This page is the style that SPEC.md, README.md,
CHANGELOG.md and the comments in the code follow.

## Before a change

`make check` runs without Claude Code and costs nothing. It lints SPEC.md's
tags and every relative link, checks the counts in the summary table, compiles
the Python files, runs shellcheck when it is installed, and tests the example
clients against a fake receiver. Run it before every commit. `make conformance`
starts throwaway `claude -p` sessions and spends a few cents of model use;
run it after a Claude Code upgrade, not for a wording change.

A change keeps every evidence tag on the statement it covered. A statement may
move to another section with its tag, and a new statement carries a tag of its
own. The counts in the summary table prove it: `python3 tests/spec_table.py`
prints the correct row for each count that is wrong, and `--fix` rewrites the
counts and links (the "covers" column stays as written).

## Style

1. Write about the protocol, in the indicative. "The receiver closes the
   connection after 30 seconds." Not "It is important to note that the
   receiver will close...", and not "In this section we describe...".
2. State a fact once, in the section it belongs to, and refer to it by section
   number elsewhere. Do not summarise at the end of a section, and do not write
   "as mentioned above". Four sections are lookup views and say so in their
   first line: 3.1 and 8.1 (the lifecycles), 14 (security) and 15 (the error
   table). They gather statements from the rest of the document, each with its
   section number and its tag, so a statement there is a restatement on
   purpose.
3. Use specific words: a number, a byte, a field name, a quoted log line. Leave
   out "robust", "seamless", "comprehensive", "carefully", "simply",
   "leverage", "ensure", "crucial", "key" and "powerful". An adjective that can
   be deleted without losing a fact is deleted.
4. Use paragraphs for reasoning and lists for enumerations. A list has items of
   one kind: fields, codes, states or steps. Three sentences in a row that each
   state a fact are a paragraph, not three bullets.
5. Use bold for nothing. Italics mark a defined term on first use, if at all.
   Headings are nouns. Code spans hold identifiers, paths, values and log text.
6. Vary the length of sentences. Put a qualification in the sentence it
   qualifies, not in a trailing fragment. Do not write triplets for rhythm,
   stacked hedges ("may possibly"), chains of em dashes, exclamation marks or
   rhetorical questions.
7. Say doubt flatly, in place, in the same voice, with the tag: "The exact
   limit is 103 bytes. UNVERIFIED (bundle)." The tag does the hedging; the
   sentence states the claim.
8. Put the tag at the end of the sentence it covers, as its own sentence:
   `OBSERVED.`, `OFFICIAL.`, `THIRD-PARTY.` or `UNVERIFIED (bundle).`, with a
   rule id or a source in parentheses when there is one, as in `OBSERVED
   (R13).` Join two tags for one claim with a semicolon: `OFFICIAL; OBSERVED
   (R02).` In a table, the tag column holds the same forms.
9. Start a changelog line with a verb and name the thing: "Add section 15, one
   table of what the receiver does with bad input."
10. Write commit messages and pull request text in the same register. Say what
    changed and why, and name nothing outside this repository.
