# Changelog

Which Claude Code versions this spec was checked against, and what changed.

## Compatibility

One row for each run of `conformance/check.py` that is logged in
[conformance/runs/](conformance/runs). `msgV` is the protocol version the
frames in the log carry. A version that has no row was not run here; the
sections below say what is known of it.

| Claude Code | OS | msgV | rules passed | date |
|---|---|---|---|---|
| 2.1.310 | Linux | 1 | 34 of 37 (3 skipped) | 2026-10-27 |
| 2.1.310 | Linux | 1 | 28 of 28 | 2026-10-26 |

## Changes to this document

- Mark seven claims as observed, from a logged run of the new rules
  (`conformance/runs/2.1.310-linux-2026-10-27.txt`): the byte-exact envelope,
  a `msg_id` that is not a UUID, priority order, `queue-full`, receipts only to
  the poster, and the first line that clears the 30-second timer.
- Move the receipts, delivery rules, inbound controls and limits into
  `spec/delivery.md`, the error table into `spec/errors.md`, the security
  section into `spec/security.md`, and `claude -p`, bare mode, the platform
  notes and the minimal client into `spec/platforms.md`. SPEC.md is now the
  front page alone. Every claim keeps its tag in the evidence table of its new
  page, and the totals do not change.
- Link the two references that named a section by number, in the inbound
  controls and the error table.
- Add the pages `spec/transport.md`, `spec/frames.md` and `spec/about.md`, and
  make SPEC.md the front page: what the socket is, the version, the summary
  table with one row per page, and what the four tags mean. Each page opens
  with real frames and ends with an evidence table.
- Check the tags in each page's evidence table, not in its sentences, and write
  the summary table from the pages.
- Mark the hold rule for a bypassing receiver as observed in sections 9, 14
  and 15, from a logged run against a `-p` session in `bypassPermissions` mode.
- Add section 15, every way a connection or a frame fails and what the sender
  sees, with an evidence table.
- Add CONTRIBUTING.md, which says how the document is written.
- Add section 0, the status, versions, conventions and terminology of the
  document, with the tag definitions moved into it as 0.5.
- Add the summary table at the top of SPEC.md, one row per section with the
  count of each tag, and run its check in `make check`.
- Complete the example frames in sections 6 and 7.
- Add a per-section tag count table and the script that checks it.

## 2.1.310, checked 2026-10-26 on Linux

The runs of `conformance/check.py` are in the table above.

Seen live:

- A `-p` session and an interactive session both bind the socket and register
  under `<config dir>/sessions/`.
- A posted frame starts a turn in an idle session and is read mid-turn in a
  busy one.
- No receipt for an accepted message. Receipts seen: `dropped` (duplicate and
  rate-limited), `held`, `denied`, `delivered`, `expired`, `expired` +
  `status_detail: refused`, and the idle notice.
- The rate bucket and the duplicate window: a burst of distinct bodies from one
  sender was admitted up to 30 and the 31st dropped with `rate-limited`; from an
  emptied bucket, steady probing was admitted at about 0.5 per second; an
  identical body was dropped at 5 s and admitted again after 31 s.
- The interactive hold dialog: a message asserting `from-mode="bypass"`, posted
  to a prompting session, opened the approval dialog. Deny dropped it and sent a
  `denied` receipt; Approve released it to the model (a turn started) and sent a
  `delivered` receipt.
- The envelope's `from-mode` at a `-p` session (a prompting receiver): a
  `prompting` assertion was delivered with no receipt, a `bypass` assertion was
  held with the permission-mode-parity reason.
- The fallback socket directory: with `XDG_RUNTIME_DIR` unset the socket is
  `/tmp/cc-socks/<pid>.sock`; a 174-byte path falls back to
  `/tmp/cc-socks-<uid>/`. The exact limit, `$CLAUDE_CODE_TMPDIR` and the
  directory refusal conditions were read in the bundle only.
- A permission prompt open in an interactive session: a `now` frame closed
  it at once and the tool call was recorded as rejected by the user, then the
  message was delivered (seen twice); a `next` frame waited in the queue with
  the prompt still open. A `now` frame posted during a running Bash command
  did not interrupt it.
- `rename` works on interactive sessions only.
- Own-child posts from a hook are delivered with and without the token.

Corrected against the shipped bundle:

- The fallback directory when `XDG_RUNTIME_DIR` is unset is `/tmp/cc-socks/`
  (the system temp base, or `$CLAUDE_CODE_TMPDIR`), not the per-user
  `/tmp/cc-socks-<uid>/`; the per-user directory is for an over-long path or a
  refused directory.
- Idle-notice subscriptions: at most 32 are kept in all and 4 per verified
  sender (8 for an unverified sender), not "3 per target, 32 in all"; the 3 is
  how many a sending Claude Code keeps outstanding.
- Bare mode skips the default inbox, but an explicit `--messaging-socket-path`
  still binds one; `CLAUDE_CODE_SIMPLE=1` is treated like `--bare`.

Not checked: Windows, macOS, bare mode, a receiver in bypass mode, the
`queue-full` drop reason, and the `hop-loop` and `hop-runaway` drop reasons
(these two run only when the sender's pid is not kernel-verified, so a local
socket client cannot reach them).

New since the Go client's notes on 2.1.233: `refused` is sent as `expired`
with `status_detail`; `dropped` carries `drop_reason`; the `notify_when_idle`
and `peer_idle_notice` controls; the `from-mode` and `hop-chain` envelope
attributes; a child token in the environment beside the peer token in the key
file.

## 2.1.233, third party

The Go client [claude-code-socket-transport](https://github.com/PeterSR/claude-code-socket-transport)
read the frame out of this version: the socket path, one JSON object per line,
the 1 MiB line cap, the optional auth line, the message fields, `rename`, the
`peer_message_status` receipt, the registry file and the 50-message queue.
Nothing seen on 2.1.310 contradicts it.

## 2.1.224, official

Cross-session messaging ships on macOS and Linux. 2.1.234 adds native Windows,
2.1.236 the idle notice, 2.1.248 same-machine messaging on third-party
providers.
