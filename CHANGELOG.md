# Changelog

Which Claude Code versions this spec was checked against, and what changed.

## 2.1.310, checked 2026-10-26 on Linux

`conformance/check.py`: 22 rules, all pass.

Seen live:

- A `-p` session and an interactive session both bind the socket and register
  under `<config dir>/sessions/`.
- A posted frame starts a turn in an idle session and is read mid-turn in a
  busy one.
- No receipt for an accepted message. Receipts seen: `dropped` (duplicate),
  `held`, `expired`, `expired` + `status_detail: refused`, and the idle notice.
- `rename` works on interactive sessions only.
- Own-child posts from a hook are delivered with and without the token.

Not checked: Windows, macOS, bare mode, the hold dialog's `denied` and
`delivered` receipts, a receiver in bypass mode, the rate-limit numbers.

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
