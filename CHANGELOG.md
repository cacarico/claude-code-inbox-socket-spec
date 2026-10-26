# Changelog

Which Claude Code versions this spec was checked against, and what changed.

## 2.1.310, checked 2026-10-26 on Linux

`conformance/check.py`: 26 rules, all pass.

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
