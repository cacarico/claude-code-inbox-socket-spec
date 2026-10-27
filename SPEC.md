# The Claude Code session inbox socket

A running Claude Code session accepts messages from other processes on the same machine on a socket, and this document describes it: the frames a process writes, what the session answers, and what the model then sees. It is not official, and it describes frame version 1 (`msgV: 1`) as shipped in Claude Code 2.1.310, as seen on Linux; the frame is an internal interface and can change in any release.

Run `conformance/check.py` after an upgrade before trusting a page, and read [the changes to this document](CHANGELOG.md). The running Claude Code is the source of truth: where it and this document differ, the document has a bug.

How much of each page rests on which kind of evidence, counted as the next section says:

| page | covers | OFFICIAL | OBSERVED | THIRD-PARTY | UNVERIFIED |
|---|---|---|---|---|---|
| [Transport](spec/transport.md) | the socket, its path and permissions, how to find a session, and how lines are framed on it | 9 | 22 | 4 | 11 |
| [Frames](spec/frames.md) | the auth line, the message frame and its envelope, what the model sees, and the control frames | 8 | 25 | 5 | 15 |
| [About this document](spec/about.md) | what the document covers, the versions it names, the conventions it uses, and a list of terms | 0 | 0 | 0 | 0 |
| [7 Receipt frames](#7-receipt-frames) | the receipts the receiver sends and to whom | 0 | 9 | 1 | 4 |
| [8 Delivery rules](#8-delivery-rules) | when a message is read, priorities and the queue | 3 | 6 | 1 | 2 |
| [9 Inbound controls](#9-inbound-controls) | `crossSessionInbound`, permission classes and the own-child exception | 12 | 8 | 0 | 2 |
| [10 Limits](#10-limits) | every numeric limit in one table | 5 | 4 | 0 | 4 |
| [11 `claude -p` and bare mode](#11-claude--p-and-bare-mode) | `-p` sessions and bare mode | 4 | 3 | 0 | 1 |
| [12 Platform notes](#12-platform-notes) | macOS, Windows, containers and sandboxes | 5 | 1 | 0 | 1 |
| [13 A minimal client](#13-a-minimal-client) | the steps of a minimal client | 0 | 0 | 0 | 0 |
| [14 Security](#14-security) | who can post, what a sender can claim and what it cannot do | 8 | 9 | 0 | 1 |
| [15 Error handling](#15-error-handling) | every way a connection or a frame fails, and what the sender sees | 13 | 24 | 1 | 12 |
| total |  | 67 | 111 | 12 | 53 |

## How each statement is known

Every statement is known in one of four ways. Each page ends with an Evidence table that gives the way for each of its claims, one row per source.

- OFFICIAL: Anthropic's docs say it, on the page [Message your other Claude Code sessions](https://code.claude.com/docs/en/cross-session-messaging) unless another is named.
- OBSERVED: run against Claude Code 2.1.310 on Linux, 2026-10-26, in throwaway sessions; `CHANGELOG.md` has the run.
- THIRD-PARTY: another client says it: the Go client [claude-code-socket-transport](https://github.com/PeterSR/claude-code-socket-transport), which read 2.1.233.
- UNVERIFIED: not run. "UNVERIFIED (bundle)" means read in the JavaScript bundled in the 2.1.310 binary and never seen happen.

The table counts the tag words in a page's Evidence table, outside fenced code blocks, as whole words and not directly after "Not" or "not". A claim with two sources has two rows and counts twice. `python3 tests/spec_table.py` recomputes the counts and writes the table with `--fix`, and `make check` runs it.

## Pages

- [Transport](spec/transport.md): the socket, how to find a session, how lines are framed.
- [Frames](spec/frames.md): the auth line, the message frame, the envelope, what the model sees, the control frames.
- [About this document](spec/about.md): what the document covers, its versions and conventions, and a list of terms.

Receipts, delivery, inbound controls, limits, `claude -p`, platforms, a minimal client, security and errors are still sections of this file, numbered from 7.

## 7. Receipt frames

A receipt is a control frame the receiver sends, on a new connection, to the
`from` address of the frame it is about:

```json
{"type":"control","action":"peer_message_status","status":"held","reason":"Your message is held for the recipient user's approval before it reaches their Claude session (permission-mode parity).","from":"uds:/run/user/1000/cc-socks/12345.sock","orig_msg_id":"4b7e0f3a-6f4b-4c1e-9f6d-2a1c7e5b9d10","msgV":1,"msg_id":"c81f5a07-2d3e-4b96-a0f4-5e7d9b3c1a28"}
```

| `status` | when | extra fields | tag |
|---|---|---|---|
| `held` | the inbound controls hold the message for the user | | OBSERVED (`crossSessionInbound: hold`) |
| `denied` | the user declined a held message | | OBSERVED (declined in the interactive hold dialog; `reason`: "The recipient user declined your message; it was not delivered to their Claude session.") |
| `expired` | a held message was dropped: dialog deadline passed, hold buffer full, or the session shut down with it held | | OBSERVED (held message settled as `expired` at session exit) |
| `expired` + `status_detail: "refused"` | the session refuses peer messages (`crossSessionInbound: refuse`, or a policy) | `status_detail` | OBSERVED |
| `delivered` | a previously held message was approved and released | | OBSERVED (approved in the interactive hold dialog; the message then started a turn; `reason`: "Your previously-held message was approved and released to the recipient's Claude session.") |
| `dropped` | the inbox guard dropped it | `drop_reason`: `duplicate`, `rate-limited`, `hop-loop`, `hop-runaway` or `queue-full`; `dropped_msg_ids`: other ids coalesced into this receipt | OBSERVED for `duplicate` and `rate-limited`. `queue-full`, `hop-loop` and `hop-runaway`: the two hop reasons are not reachable from a local client, since they run only when the sender's pid is not kernel-verified, and a local socket connection always is. UNVERIFIED (bundle) |

Rules:

- **An accepted message produces no receipt.** Silence after a clean write
  means the frame was taken; it does not mean the model has read it yet.
  OBSERVED (no frame within 6 s of an immediate delivery). THIRD-PARTY says the
  same: a successful send is "took the frame off the socket".
- `reason` is a human-readable sentence; `orig_msg_id` is the `msg_id` of the
  frame concerned; `from` is the receiver's own address. OBSERVED.
- The receiver only sends to a `from` it can vet: a `uds:` address whose path
  ends in `.sock` in the same directory as its own socket, or a
  `<digits>.sock`, `<digits>-<8 hex>.sock` or `<hex>.sock` in one of the
  known `cc-socks` directories of the same uid when the sender's pid was
  verified. UNVERIFIED (bundle). OBSERVED that `<own pid>.sock`
  beside the target's socket works.
- When sending a receipt the receiver checks, through the kernel, that the
  process listening at `from` is the process that posted the frame (same pid
  and start token). UNVERIFIED (bundle). So the listener must be
  the sender process itself, which is what `examples/send.py --receipts` does.
- Drop receipts to one sender are coalesced over a 5-second trail; repeated
  drops add ids to `dropped_msg_ids`. UNVERIFIED (bundle).

## 8. Delivery rules

- The receiving model reads the message between tool calls during a running
  turn; a running tool is never interrupted. OFFICIAL; OBSERVED (a frame posted
  while a `-p` session was in a tool call reached the model later in the same
  turn).
- When the session is idle, Claude Code starts a new turn with the message.
  OFFICIAL; OBSERVED for `-p` and interactive sessions.
- Priorities, OBSERVED with three frames posted within 100 ms to an idle
  session: the first to arrive (`later`) started a turn, `now` joined that
  turn, and `next` came in the following turn. The meaning bundle:
  `now` is handled outside the arrival queue and can overtake frames that
  arrived earlier; `next` is the default; `later` is placed after `next`.
  UNVERIFIED beyond that one observation. `now` does not interrupt a running
  tool: posted during a 20-second Bash command, it was read after the command
  finished. OBSERVED. It does close an open permission prompt as a refusal
  ([what the model sees](spec/frames.md#what-the-model-sees)). OBSERVED. THIRD-PARTY: "`now` can overtake
  earlier frames from the same sender".
- Frames posted by a `SessionStart` hook were delivered before the session's
  first prompt from stdin. OBSERVED in a `-p` session.
- Within a session at most 50 accepted peer messages wait to be read; past
  that, new ones are dropped with `queue-full`. OFFICIAL (50); receipt reason
  UNVERIFIED (bundle).

## 9. Inbound controls

The setting `crossSessionInbound` takes `accept`, `hold` or `refuse` and applies
to messages from the socket like any other peer message. OFFICIAL.

- `accept`: every frame is delivered. OFFICIAL.
- `hold`: a `held` receipt is sent, nothing is delivered until a later
  `accept` applies; such a message does not expire on its own. OFFICIAL;
  OBSERVED (`held`, then `expired` only when the session exited).
- `refuse`: dropped, with an `expired`/`refused` receipt. OFFICIAL (drops);
  OBSERVED (receipt).
- A session that refuses still binds its socket and looks the same from
  outside. OFFICIAL.

When no value applies, the decision is per message, by permission class.
OFFICIAL:

- Sessions are in one of two classes: **bypassing** (`bypassPermissions`, and
  plan mode in an interactive terminal where bypass is available) and
  **prompting** (everything else, `auto`, `acceptEdits` and `dontAsk`
  included). OFFICIAL.
- A **prompting** receiver delivers every message, except one whose sender
  identifies itself as bypassing: that one is held. OFFICIAL; OBSERVED that a
  default `-p` session delivers frames that assert no class, and that a bypass
  assertion to a prompting session is held (`holdCause: mode-mismatch`).
- A **bypassing** receiver holds every message, except one whose sender
  identifies itself as bypassing too. OFFICIAL; OBSERVED (R20, R20b).
- The sender's class is the `from-mode` attribute of the envelope (section
  5.2). OBSERVED for `bypass` (held) and `prompting` (delivered) at a prompting
  receiver. A socket post the receiver cannot verify as its own child is
  treated "like any other that asserts no permission class". OFFICIAL. That
  the attribute is the only way to assert one is UNVERIFIED (bundle); frames
  without it are delivered to a prompting receiver, OBSERVED.
- **Own-child exception**: a message the receiver can verify as coming from its
  own child process (a hook or Bash command posting back to its session) is
  delivered regardless of class. On Linux the verification is by process
  ancestry, even after the child exited; on macOS only while it runs; where
  that evidence is missing the child token in the auth line verifies it.
  OFFICIAL; OBSERVED on Linux with and without the token.
- A held message in an interactive terminal opens a dialog; unanswered past
  `dialogExpiry` (5 minutes by default) it is dropped. A `-p` session has no
  dialog and drops a default-held message after the same deadline, reporting
  `expired`. At most 100 messages are held; past that the oldest is dropped.
  OFFICIAL. OBSERVED (the dialog opened on a held bypass assertion; its two
  choices are to deny, which drops the message and sends a `denied` receipt, or
  to deliver it, which releases it to the model and sends a `delivered` receipt).
- If the receiver cannot tell its own permission mode it holds (fail closed).
  UNVERIFIED (bundle).

## 10. Limits

| limit | value | tag |
|---|---|---|
| line length | 1,048,576 characters; the connection is dropped at that point | OBSERVED |
| message size at the sender | "about a million characters" of serialised frame; Claude Code refuses to send past it, naming the sizes | OFFICIAL; the same 1,048,576 constant minus the auth line, UNVERIFIED (bundle) |
| first line deadline | 30 seconds from connect | OFFICIAL; OBSERVED |
| queued accepted messages per session | 50 | OFFICIAL |
| held messages per session | 100 | OFFICIAL |
| identical repeats | a body identical to the previous one from the same sender within 30 seconds is dropped (`duplicate`) | OBSERVED: a repeat at 5 s was dropped, and the same body after 31 s was admitted |
| rate per sender | a token bucket of 30 messages refilled at 0.5 per second; past it `rate-limited` | OBSERVED: a burst of distinct bodies was admitted up to the 30th and the 31st was `rate-limited`; from an emptied bucket, steady probing was admitted at 0.5 per second |
| senders tracked for the above | 256, least recently seen evicted | UNVERIFIED (bundle) |
| relay loops | a `hop-chain` that already contains the receiver 10 times, or longer than 28 hops, is dropped (`hop-loop`, `hop-runaway`) | UNVERIFIED (bundle) |
| bursts at a Claude sender | Claude Code paces its own sends to one target and refuses more "just now" | OFFICIAL; does not apply to a third-party sender, which is subject to the receiver's bucket above |

The sender key for the duplicate and rate rules is the frame's `from` address
when present, else the connecting pid. UNVERIFIED (bundle).

## 11. `claude -p` and bare mode

- A `claude -p` session binds an inbox like an interactive one and appears in
  the registry (`entrypoint: sdk-cli`). OFFICIAL; OBSERVED. A plain `-p` run
  exits after its turn and takes the socket with it; with
  `--input-format stream-json` it stays open and keeps receiving, which is how
  `conformance/check.py` tests it. OBSERVED.
- A `-p` session cannot show the hold dialog; see section 9. OFFICIAL. To take
  messages unattended start it with `--settings '{"crossSessionInbound":"accept"}'`.
  OFFICIAL.
- `rename` has no effect on a `-p` session. OBSERVED.
- Bare mode (`--bare`, and `CLAUDE_CODE_SIMPLE=1`, which is treated the same)
  skips the default inbox, so the session cannot receive and is not listed.
  OFFICIAL. An explicit `--messaging-socket-path` still binds an inbox even under
  bare mode. UNVERIFIED (bundle). Not OBSERVED here: bare mode needs an API-key login.

## 12. Platform notes

- Linux and WSL 2: everything above. OBSERVED on Linux.
- macOS: same socket and frames; auth optional; own-child verification by
  process evidence only while the child runs. OFFICIAL. Claude Code's own client
  delays its half-close by 150 ms there. UNVERIFIED (bundle).
- Native Windows: a named pipe, auth line required, the peer token read from the
  key file, no process evidence. OFFICIAL. Nothing here was run on Windows.
- Containers: a session inside and one on the host cannot reach each other;
  two inside the same container can. OFFICIAL. Where Claude Code is pid 1 it
  has no process evidence and relies on the token. OFFICIAL.
- Sandboxed Bash: whether a command can reach the socket from inside the
  sandbox is set by `sandbox.network.allowUnixSockets` and
  `allowAllUnixSockets`. OFFICIAL.

## 13. A minimal client

1. Find the socket: `$CLAUDE_CODE_MESSAGING_SOCKET` inside the session, or the
   registry record of the session you want, after checking it connects.
2. Build the payload first: an optional auth line, then one `user` frame with
   `type` and `message.content`; add `msg_id`, `from` and `session_id` if you
   want receipts.
3. Connect, write, half-close, read to EOF, close.
4. If you set `from`, listen there. No frame means accepted. `held`,
   `expired`, `denied` or `dropped` means the model did not get it.

`examples/send.py` is this in code. `conformance/check.py` holds the checks
behind the observed statements that a headless `-p` session can show; the rest (the
interactive hold dialog, the fallback socket directories, the refill rate) were
run by hand and are listed in `CHANGELOG.md`.

## 14. Security

What the socket lets a process do, and what it does not:

- **Who can post.** Any process running as your OS user, and nothing else on
  the machine: the socket is `0600` in a `0700` directory. OFFICIAL; OBSERVED
  (modes). On macOS and Linux nothing more is asked: the auth line is optional
  and a wrong token is accepted ([the auth frame](spec/frames.md#the-auth-frame)). OFFICIAL; OBSERVED. Such a process
  could already edit your settings, hooks and `CLAUDE.md`; what the socket adds
  is a short path from any script into the model's context.
- **Sender identity is self-declared.** `from`, `session_id`, `msg_id` and the
  envelope's `from-name` and `from-mode` are whatever the sender writes.
  OBSERVED: a plain script's `from-name` and `from-mode` were taken as written
  ([the envelope](spec/frames.md#the-envelope), `conformance/check.py` R19). The
  [claudemessaging](https://github.com/S3cur3Th1sSh1t/claudemessaging) tool
  sets them on purpose. What the receiver checks itself: the connecting pid
  and its start time, through the kernel, recorded as `verifiedPeerPid` in the
  transcript (OBSERVED); whether that pid is its own child (OFFICIAL); and that
  the process listening at `from` is the one that posted, before it sends a
  receipt there (UNVERIFIED (bundle)).
- **The hold gate rests on that claim.** A bypassing receiver holds a message
  unless the sender says it is bypassing too (OFFICIAL), and saying so is the
  `from-mode` attribute ([the envelope](spec/frames.md#the-envelope)). So any process of your user that writes
  `from-mode="bypass"` reaches a bypassing session without the dialog.
  OBSERVED (R20): a `-p` session in `bypassPermissions` mode delivered such a
  frame with no hold and no receipt.
- **What a message cannot do.** It cannot approve a permission prompt, change
  configuration or run a slash command, and the session's own permission
  prompts and rules apply to whatever it asks for. OFFICIAL. The model is told
  the text came from another session and not from you ([what the model sees](spec/frames.md#what-the-model-sees)). OBSERVED.
  What it can do is everything the session's permissions already allow without
  a prompt, so a message to a session in `auto`, `acceptEdits`, `dontAsk` or
  bypass mode, or with broad allow rules, acts with those rights.
- **It can refuse a prompt.** A `now` frame closes an open permission prompt
  as if the user had declined ([what the model sees](spec/frames.md#what-the-model-sees)). OBSERVED. Any process of your user
  can make a pending action fail; send `next`, the default, for wake-ups.
- **Prompt injection.** The text reaches the model as a request to act on:
  the wrapper of [what the model sees](spec/frames.md#what-the-model-sees) tells it to treat the text as a teammate's
  request. OBSERVED. Do not paste text you do not control, such as CI logs, issue titles, commit
  messages or PR bodies, into a message. Post a fixed line ("build 482 failed;
  log at `build/482.log`") and let the session read the details with its own
  tools.
- **Silence is not delivery.** An accepted message produces no receipt
  (section 7), and a held one produces a `held` receipt only when the frame
  named a `from` the receiver can reach. OBSERVED. A sender with no `from`
  cannot tell a held message from an accepted one. This bites unattended
  sessions in bypass mode, which hold every message that does not assert
  bypass: an interactive one opens a dialog that nobody answers, and either
  kind drops the message after `dialogExpiry`, five minutes by default.
  OFFICIAL. When nobody watches the
  receiver, set `from` and wait for receipts (`send.py --receipts`).
- **The token.** `CLAUDE_CODE_MESSAGING_TOKEN` lets its holder pass as the
  session's own child, which is delivered whatever the class. OFFICIAL. Treat it
  like any session secret: do not log it or send it to another session's socket.
- **Turning it off.** `crossSessionInbound: "refuse"` drops every message, and
  `hold` keeps each for your approval; managed settings can set it for an
  organization. A refusing session still binds its socket. OFFICIAL.

## 15. Error handling

This section is a lookup. It gathers every way a connection or a frame fails
and what the sender sees when it does, and it states nothing that another
section does not; each paragraph names the section that does. The Evidence
table at the end has one row per statement, in the order of the prose, with
the source of that statement.

The receiver never writes on the sender's connection ([transport](spec/transport.md#the-inbox)). Whatever a
sender learns about a failure comes in a receipt, sent to the sender's own
socket, and only when the frame named a reply address in `from` that the
receiver can reach and vet (section 7). A frame with no `from` gets nothing
back. The receiver's debug log records some failures, with the lines quoted
below.

A connection to a socket file that no process listens on is refused (section
2). A line of more than 1,048,576 characters with no newline makes the receiver
drop the connection and deliver nothing from it; the session is unharmed
([lines](spec/transport.md#lines)). A connection that completes no line within 30 seconds is closed
([lines](spec/transport.md#lines)). On native Windows a connection without a valid auth line delivers
nothing, and a first line that is not a valid auth line closes it; which first
lines count as invalid there is not established ([lines](spec/transport.md#lines) and [the auth frame](spec/frames.md#the-auth-frame)). On macOS
and Linux a wrong token closes nothing: the frames that follow are delivered
and the sender is treated as unverified ([the auth frame](spec/frames.md#the-auth-frame)). A Claude Code sender
refuses to send a frame past about a million characters and names the sizes,
and paces its own sends to one target (section 10).

The receiver skips these without a reply. A line that is blank or not valid
JSON is skipped and the rest of the connection is still read ([lines](spec/transport.md#lines)). A
frame that is not a JSON object with a string `type` is ignored, and the debug
log says `Ignoring message without valid type field`; a `type` the receiver
does not know is ignored with `Received unhandled message type` ([lines](spec/transport.md#lines)). A
`user` frame whose `message.content` is missing, empty or not a string is
ignored with `Ignoring user message with missing or non-string content`
([the message frame](spec/frames.md#the-message-frame)). A frame whose `session_id` differs from the receiver's is dropped
silently, with `session_id mismatch` in the debug log ([the message frame](spec/frames.md#the-message-frame)). A `msg_id`
that is not a UUID is treated as absent, and a `priority` other than `now`,
`next` or `later` reads as `next` ([the message frame](spec/frames.md#the-message-frame)). A control frame with an unknown
`action` is ignored, with `Unhandled control action` in the debug log, and a
`rename` sent to a `-p` session has no effect ([control frames](spec/frames.md#control-frames) and section 11).
`notify_when_idle` is refused when `from` is missing, is the receiver itself or
cannot be vetted, or when `msg_id` is not a UUID; past 32 subscriptions in all,
or the per-sender limit, the oldest is answered `unavailable` ([control frames](spec/frames.md#control-frames)).

The guard drops with a `dropped` receipt that carries a `drop_reason`. A body
identical to the previous one from the same sender within 30 seconds is
dropped as `duplicate`. A sender past its bucket of 30 messages, refilled at
0.5 per second, gets `rate-limited` (section 10). A session with 50 accepted
messages waiting drops the next as `queue-full` (section 8). The reasons
`hop-loop` and `hop-runaway` are not reachable from a local client (section
7). Drops to one sender within 5 seconds are coalesced into one receipt, with
the other ids in `dropped_msg_ids` (section 7). A receiver sends a receipt
only after checking, through the kernel, that the process listening at `from`
is the one that posted the frame (section 7). An accepted message produces no
receipt, so silence after a clean write means the frame was taken, not that
the model has read it (section 7).

Holds and refusals follow `crossSessionInbound` and the permission classes
(section 9). A receiver set to `refuse` drops every message and answers with
`expired` and `status_detail: "refused"`; it still binds its socket and looks
the same from outside. A receiver set to `hold` sends a `held` receipt and
delivers nothing until a later `accept` applies. A prompting receiver holds a
message whose sender asserts bypass (`holdCause: mode-mismatch`); a bypassing
receiver holds every message unless its sender asserts bypass too; and a
receiver that cannot tell its own permission mode holds. A held message in an
interactive terminal opens a dialog, and a `-p` session has none. Past
`dialogExpiry`, 5 minutes by default, the message is dropped and the receipt
is `expired`; at most 100 are held, and past that the oldest is dropped. The
`expired` receipt covers an elapsed deadline, a full hold buffer and a session
that exits with a message held. The user's answer to the dialog sends `denied`
or `delivered`.

### Evidence

| Claim | Source |
|---|---|
| The receiver never writes on the sender's connection | OBSERVED |
| A held message gets a `held` receipt only when the frame named a `from` the receiver can reach | OBSERVED |
| A receipt needs a `from` in the frame | OBSERVED |
| The receiver sends a receipt only to a `from` it can vet | UNVERIFIED (bundle) |
| A connection to a stale socket file is refused | OBSERVED |
| A line over 1,048,576 characters with no newline drops the connection, delivers nothing and leaves the session unharmed | OBSERVED |
| A connection with no complete line in 30 seconds is closed | OFFICIAL |
| The same, at 30.0 seconds | OBSERVED |
| On native Windows a connection without a valid auth line delivers nothing | OFFICIAL |
| On native Windows a first line that is not a valid auth line closes the connection | OFFICIAL |
| Which first lines count as invalid on Windows (blank, unparseable, not auth) | UNVERIFIED |
| A wrong token on macOS and Linux closes nothing, and the sender is treated as unverified | OBSERVED |
| A Claude Code sender refuses a frame past about a million characters and names the sizes | OFFICIAL |
| That limit is the 1,048,576 constant minus the auth line | UNVERIFIED (bundle) |
| A Claude Code sender paces its sends to one target | OFFICIAL |
| A blank or invalid JSON line is skipped and the rest of the connection is read | OBSERVED |
| A frame without a string `type` is ignored | OBSERVED |
| A `type` the receiver does not know is ignored | OBSERVED |
| A `user` frame with missing, empty or non-string `message.content` is ignored | OBSERVED |
| A `session_id` mismatch drops the frame silently | OBSERVED |
| A `msg_id` that is not a UUID is treated as absent | UNVERIFIED (bundle) |
| A `priority` other than `now`, `next` or `later` reads as `next` | UNVERIFIED (bundle) |
| An unknown control `action` is logged and ignored | OBSERVED |
| `rename` has no effect on a `-p` session | OBSERVED |
| `notify_when_idle` is refused for a missing, own or unvettable `from`, or a `msg_id` that is not a UUID | UNVERIFIED (bundle) |
| Past 32 subscriptions, or the per-sender limit, the oldest is answered `unavailable` | UNVERIFIED (bundle) |
| An identical body within 30 seconds is dropped as `duplicate` | OBSERVED (a repeat at 5 s dropped, the same body at 31 s admitted) |
| Past a bucket of 30 messages refilled at 0.5 per second, a message is `rate-limited` | OBSERVED |
| A session with 50 accepted messages waiting drops more | OFFICIAL |
| The receipt reason for that drop is `queue-full` | UNVERIFIED (bundle) |
| `hop-loop` and `hop-runaway` are not reachable from a local client | UNVERIFIED (bundle) |
| Drops to one sender are coalesced over 5 seconds, with ids in `dropped_msg_ids` | UNVERIFIED (bundle) |
| The receiver checks that the process listening at `from` is the one that posted | UNVERIFIED (bundle) |
| An accepted message produces no receipt | OBSERVED |
| An accepted message produces no receipt | THIRD-PARTY |
| `refuse` drops every message | OFFICIAL |
| `refuse` answers with `expired` and `status_detail: "refused"` | OBSERVED |
| A refusing session still binds its socket and looks the same from outside | OFFICIAL |
| `hold` delivers nothing until a later `accept` applies | OFFICIAL |
| `hold` sends a `held` receipt, and the message expires only when the session exits | OBSERVED |
| A prompting receiver holds a message whose sender asserts bypass | OFFICIAL |
| That hold is recorded as `holdCause: mode-mismatch` | OBSERVED |
| A bypassing receiver holds every message unless its sender asserts bypass too | OFFICIAL |
| The same, at a `-p` receiver in `bypassPermissions` mode | OBSERVED (R20, R20b) |
| A receiver that cannot tell its own permission mode holds | UNVERIFIED (bundle) |
| In an interactive terminal a held message opens a dialog | OBSERVED |
| A `-p` session has no dialog and drops a default-held message at the deadline, reporting `expired` | OFFICIAL |
| A held message is dropped past `dialogExpiry`, and at most 100 are held, the oldest dropped first | OFFICIAL |
| The `expired` receipt covers a deadline, a full hold buffer and an exit with a message held | OBSERVED (at session exit) |
| The user's answer to the dialog sends `denied` or `delivered` | OBSERVED |
