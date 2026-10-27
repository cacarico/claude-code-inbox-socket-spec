# Errors

Every way a connection or a frame fails, and what the sender sees when it does.

```
{"type":"auth","token":"ffffffffffffffffffffffffffffffff"}
this line is not JSON
{"type":"user","message":{"content":""}}
{"type":"user","session_id":"00000000-0000-0000-0000-000000000000","message":{"content":"hi"}}
{"type":"user","message":{"content":"the build is green"}}
```

On macOS and Linux this connection goes on past a wrong token. The line that is not JSON is skipped. The empty message is ignored, and the debug log says `Ignoring user message with missing or non-string content`. The frame with another `session_id` is dropped, and the debug log says `session_id mismatch`. Only the last frame is delivered. The sender saw nothing of this: the receiver never writes on the sender's connection ([transport](transport.md#the-inbox)).

## What the sender learns

Whatever a sender learns about a failure comes in a receipt, sent to the sender's own socket, and only when the frame named a reply address in `from` that the receiver can reach and vet ([receipts](delivery.md#receipts)). A frame with no `from` gets nothing back. A held message gets its `held` receipt only on the same condition. The debug log of the receiver records some failures, with the lines quoted on this page.

## The connection

| what goes wrong | what happens |
|---|---|
| the socket file has no process behind it | the connection is refused ([finding a session](transport.md#finding-a-session)) |
| a line of more than 1,048,576 characters with no newline | the receiver drops the connection and delivers nothing from it; the session is unharmed ([lines](transport.md#lines)) |
| no complete line within 30 seconds | the receiver closes the connection ([lines](transport.md#lines)) |
| native Windows: no valid auth line | nothing is delivered |
| native Windows: a first line that is not a valid auth line | the connection is closed; which first lines count as invalid there is unknown ([lines](transport.md#lines), [the auth frame](frames.md#the-auth-frame)) |
| macOS and Linux: a wrong token | nothing is closed; the frames that follow are delivered and the sender is treated as unverified ([the auth frame](frames.md#the-auth-frame)) |
| a Claude Code sender and a frame past about a million characters | it refuses to send and names the sizes; the bound is the 1,048,576 constant less the auth line ([limits](delivery.md#limits)) |
| a Claude Code sender and many sends to one target | it paces its own sends |

## Frames skipped without a reply

| what goes wrong | what happens |
|---|---|
| a line that is blank or not valid JSON | skipped; the rest of the connection is still read ([lines](transport.md#lines)) |
| a frame that is not a JSON object with a string `type` | ignored; the debug log says `Ignoring message without valid type field` |
| a `type` the receiver does not know | ignored; the debug log says `Received unhandled message type` |
| a `user` frame whose `message.content` is missing, empty or not a string | ignored; the debug log says `Ignoring user message with missing or non-string content` ([the message frame](frames.md#the-message-frame)) |
| a `session_id` that differs from the receiver's | dropped silently; the debug log says `session_id mismatch` |
| a `msg_id` that is not a UUID | treated as absent |
| a `priority` other than `now`, `next` or `later` | read as `next` |
| a control frame with an unknown `action` | ignored; the debug log says `Unhandled control action` ([control frames](frames.md#control-frames)) |
| a `rename` sent to a `-p` session | no effect ([platforms](platforms.md#claude--p)) |
| `notify_when_idle` with a missing, own or unvettable `from`, or a `msg_id` that is not a UUID | refused |
| `notify_when_idle` past 32 subscriptions in all, or the per-sender limit | the oldest is answered `unavailable` |

## Drops with a receipt

The guard drops with a `dropped` receipt that carries a `drop_reason`.

| `drop_reason` | when |
|---|---|
| `duplicate` | a body identical to the previous one from the same sender within 30 seconds |
| `rate-limited` | a sender past its bucket of 30 messages, refilled at 0.5 per second ([limits](delivery.md#limits)) |
| `queue-full` | a session with 50 accepted messages waiting ([delivery rules](delivery.md#delivery-rules)) |
| `hop-loop`, `hop-runaway` | not reachable from a local client |

Drops to one sender within 5 seconds are coalesced into one receipt, with the other ids in `dropped_msg_ids`. A receiver sends a receipt only after checking, through the kernel, that the process listening at `from` is the one that posted the frame. An accepted message produces no receipt, so silence after a clean write means the frame was taken, not that the model has read it.

## Holds and refusals

These follow `crossSessionInbound` and the permission classes ([inbound controls](delivery.md#inbound-controls)).

| setting or case | what happens |
|---|---|
| `refuse` | drops every message and answers with `expired` and `status_detail: "refused"`; the session still binds its socket and looks the same from outside |
| `hold` | sends a `held` receipt and delivers nothing until a later `accept` applies; the message expires only when the session exits |
| a prompting receiver and a sender that asserts bypass | held, with `holdCause: mode-mismatch` |
| a bypassing receiver and a sender that does not assert bypass | held |
| a receiver that cannot tell its own permission mode | held |
| a held message in an interactive terminal | opens a dialog |
| a held message in a `-p` session | no dialog; dropped at the deadline with an `expired` receipt |
| a held message past `dialogExpiry`, 5 minutes by default | dropped; at most 100 are held, and past that the oldest is dropped |
| the `expired` receipt | covers an elapsed deadline, a full hold buffer and a session that exits with a message held |
| the user's answer to the dialog | sends `denied` or `delivered` |

## Evidence

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
| A `msg_id` that is not a UUID is treated as absent | OBSERVED (R22) |
| A `priority` other than `now`, `next` or `later` reads as `next` | OBSERVED (R23) |
| An unknown control `action` is logged and ignored | OBSERVED |
| `rename` has no effect on a `-p` session | OBSERVED |
| `notify_when_idle` is refused for a missing, own or unvettable `from`, or a `msg_id` that is not a UUID | UNVERIFIED (bundle) |
| Past 32 subscriptions, or the per-sender limit, the oldest is answered `unavailable` | UNVERIFIED (bundle) |
| An identical body within 30 seconds is dropped as `duplicate` | OBSERVED (a repeat at 5 s dropped, the same body at 31 s admitted) |
| Past a bucket of 30 messages refilled at 0.5 per second, a message is `rate-limited` | OBSERVED |
| A session with 50 accepted messages waiting drops more | OFFICIAL |
| The receipt reason for that drop is `queue-full` | OBSERVED (R24) |
| `hop-loop` and `hop-runaway` are not reachable from a local client | UNVERIFIED (bundle) |
| Drops to one sender are coalesced over 5 seconds, with ids in `dropped_msg_ids` | UNVERIFIED (bundle) |
| A receipt goes only to the process that posted: another process listening at `from` gets none | OBSERVED (R25) |
| The check goes through the kernel | UNVERIFIED (bundle) |
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
