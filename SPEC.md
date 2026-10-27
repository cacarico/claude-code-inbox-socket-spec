# The Claude Code session inbox socket

A running Claude Code session accepts messages from other processes on the same machine on a socket, and this document describes it: the frames a process writes, what the session answers, and what the model then sees. It is not official, and it describes frame version 1 (`msgV: 1`) as shipped in Claude Code 2.1.310, as seen on Linux; the frame is an internal interface and can change in any release.

Run `conformance/check.py` after an upgrade before trusting a page, and read [the changes to this document](CHANGELOG.md). The running Claude Code is the source of truth: where it and this document differ, the document has a bug.

How much of each page rests on which kind of evidence, counted as the next section says:

| page | covers | OFFICIAL | OBSERVED | THIRD-PARTY | UNVERIFIED |
|---|---|---|---|---|---|
| [Transport](spec/transport.md) | the socket, its path and permissions, how to find a session, and how lines are framed on it | 9 | 24 | 4 | 10 |
| [Frames](spec/frames.md) | the auth line, the message frame and its envelope, what the model sees, and the control frames | 8 | 28 | 5 | 12 |
| [Delivery](spec/delivery.md) | what the receiver does with an accepted frame: the receipts it sends, when the model reads a message, how the inbound controls hold or refuse one, and every limit | 20 | 31 | 2 | 11 |
| [Errors](spec/errors.md) | every way a connection or a frame fails, and what the sender sees when it does | 13 | 28 | 1 | 9 |
| [Security](spec/security.md) | who can post to the socket, what a sender can claim, what a message can and cannot do, and how to turn the inbox off | 8 | 10 | 0 | 0 |
| [Platforms](spec/platforms.md) | how `claude -p` and bare mode differ from an interactive session, what changes on each platform, and the steps of a minimal client | 9 | 4 | 0 | 2 |
| [About this document](spec/about.md) | what the document covers, the versions it names, the conventions it uses, and a list of terms | 0 | 0 | 0 | 0 |
| total |  | 67 | 125 | 12 | 44 |

## How each statement is known

Every statement is known in one of four ways. Each page ends with an Evidence table that gives the way for each of its claims, one row per source.

- OFFICIAL: Anthropic's docs say it, on the page [Message your other Claude Code sessions](https://code.claude.com/docs/en/cross-session-messaging) unless another is named.
- OBSERVED: run against Claude Code 2.1.310 on Linux, 2026-10-26 and 2026-10-27, in throwaway sessions; `CHANGELOG.md` and `conformance/runs/` have the runs.
- THIRD-PARTY: another client says it: the Go client [claude-code-socket-transport](https://github.com/PeterSR/claude-code-socket-transport), which read 2.1.233.
- UNVERIFIED: not run. "UNVERIFIED (bundle)" means read in the JavaScript bundled in the 2.1.310 binary and never seen happen.

The table counts the tag words in a page's Evidence table, outside fenced code blocks, as whole words and not directly after "Not" or "not". A claim with two sources has two rows and counts twice. `python3 tests/spec_table.py` recomputes the counts and writes the table with `--fix`, and `make check` runs it.

## Pages

- [Transport](spec/transport.md): the socket, how to find a session, how lines are framed.
- [Frames](spec/frames.md): the auth line, the message frame, the envelope, what the model sees, the control frames.
- [Delivery](spec/delivery.md): the receipts, when the model reads a message, the inbound controls, the limits.
- [Errors](spec/errors.md): every way a connection or a frame fails and what the sender sees.
- [Security](spec/security.md): who can post, what a sender can claim, what a message can do.
- [Platforms](spec/platforms.md): `claude -p`, bare mode, each platform, a minimal client.
- [About this document](spec/about.md): what the document covers, its versions and conventions, and a list of terms.
