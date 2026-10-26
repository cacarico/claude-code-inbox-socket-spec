# Changelog

What was verified, against which Claude Code version, and what changed in the
protocol between versions as far as it is known. Newest first.

## 2.1.310 (verified 2026-10-26)

Platform: Linux x86_64 (Debian 13), Python 3.13, OpenBSD netcat, socat.
Sessions: throwaway `claude -p --input-format stream-json` sessions and one
interactive session in a pty, all in a scratch directory, default permission
mode unless a rule says otherwise.

`conformance/check.py`, full run, 22 rules, 22 pass:

- PASS R01 registry record <config>/sessions/<pid>.json names a live socket; key file beside it
- PASS R01b socket mode 0600 in a 0700 directory
- PASS R02 minimal frame {type:user, message.content} starts a turn in an idle session
- PASS R02b the session's transcript records it as a user line with origin.kind=peer
- PASS R03 a frame with `from` that is accepted gets no receipt
- PASS R04 session_id that is not the receiver's: dropped silently
- PASS R05 empty, non-string or missing message.content: ignored
- PASS R06 unparseable or typeless lines skipped; a later valid line on the same connection delivered, even without a final newline
- PASS R07 identical repeat from the same sender within the window: `dropped` receipt, drop_reason=duplicate
- PASS R07b receipt orig_msg_id names the dropped frame
- PASS R07c the first copy was delivered once
- PASS R08 a wrong auth token does not close the connection on macOS/Linux; the message is still delivered
- PASS R09 more than 1 MiB without a newline: connection dropped, session unharmed
- PASS R10 notify_when_idle on an idle session: peer_idle_notice with state=idle
- PASS R11 unknown control action or frame type: ignored
- PASS R12 rename control on a -p session leaves the registry name alone (interactive sessions rename)
- PASS R13 a connection with no complete line is closed after 30 s
- PASS R14 crossSessionInbound=hold: `held` receipt, nothing delivered
- PASS R14b a held message is settled as `expired` when the session ends
- PASS R15 crossSessionInbound=refuse: `expired` receipt with status_detail=refused
- PASS R16 own child (SessionStart hook) posting with the token is delivered
- PASS R16b own child posting without the token is delivered (process evidence)

Observed live, beyond the conformance rules:

- Interactive session: `/status` shows `Peer address: uds:<path>`; the `rename`
  control renames it (registry `nameSource: peer`); a frame posted while idle
  starts a turn.
- `-p` session: `rename` has no effect; a frame posted during a tool call is
  read within the running turn; frames posted by a `SessionStart` hook are
  delivered before the first stdin prompt; three frames with priorities
  `later`, `next`, `now` posted within 100 ms were read as `later` + `now` in
  one turn, then `next`.
- Receipts: none for an accepted message; `dropped`/`duplicate` for an
  identical repeat; `held` under `crossSessionInbound: hold` and `expired` when
  that session exited; `expired` with `status_detail: refused` under `refuse`;
  `peer_idle_notice` with `state: idle` for a `notify_when_idle` sent to an
  idle session.
- The enveloped form `<cross-session-message from=… from-name=…>` is accepted
  and the transcript records the sender name and body.

Not verified in this round: native Windows, macOS, bare mode (needs an API-key
login), the hold dialog's `denied` and `delivered` receipts, the default hold
for a bypassing receiver, the rate-limit numbers, the `queue-full`, `hop-loop`
and `hop-runaway` drops, and `--messaging-socket-path`.

Protocol facts first read in the 2.1.310 bundle and not in the Go client's
write-up of 2.1.233: the `refused` receipt is encoded as `expired` with
`status_detail`; the `dropped` receipt with `drop_reason` and
`dropped_msg_ids`; the `notify_when_idle` and `peer_idle_notice` controls
(documented as a feature from 2.1.236); the artifact-yield controls; the
`from-session`, `hop-chain`, `from-mode` and `from-plugin` envelope attributes;
the two-token scheme (child token in the environment, peer token in the key
file); the registry fields `peerProtocol` and `peerFeatures`.

## 2.1.233 (THIRD-PARTY)

The Go client [claude-code-socket-transport](https://github.com/PeterSR/claude-code-socket-transport)
read the protocol out of 2.1.233 and verified: the `<base>/cc-socks/<pid>.sock`
path, one JSON object per line with half-close, the 1 MiB line cap, the
optional auth frame, the message frame with `msgV`, `msg_id`, `type`,
`message`, `priority`, `from` and `session_id`, the `rename` control, the
`peer_message_status` receipt with `held`, `denied`, `expired` and
`delivered`, the registry at `<config>/sessions/<pid>.json`, the envelope with
`from` and `from-name`, and the 50-message queue cap with duplicate and
per-sender rate limits. Nothing in the 2.1.310 run contradicts it.

## 2.1.224 (OFFICIAL)

Cross-session messaging ships on macOS and Linux; 2.1.234 on native Windows;
2.1.236 adds the idle notice in both sessions; 2.1.248 same-machine messaging
on Bedrock, Vertex, Foundry and with feature-flag fetching off.
