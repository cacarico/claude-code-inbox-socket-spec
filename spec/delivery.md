# Delivery

What the receiver does with an accepted frame: the receipts it sends, when the model reads a message, how the inbound controls hold or refuse one, and every limit.

```
{"type":"user","message":{"content":"<cross-session-message from=\"uds:/run/user/1000/cc-socks/23456.sock\" from-mode=\"bypass\">\nrotate the log files\n</cross-session-message>"},"from":"uds:/run/user/1000/cc-socks/23456.sock","msg_id":"4b7e0f3a-6f4b-4c1e-9f6d-2a1c7e5b9d10"}
{"type":"control","action":"peer_message_status","status":"held","reason":"Your message is held for the recipient user's approval before it reaches their Claude session (permission-mode parity).","from":"uds:/run/user/1000/cc-socks/12345.sock","orig_msg_id":"4b7e0f3a-6f4b-4c1e-9f6d-2a1c7e5b9d10","msgV":1,"msg_id":"c81f5a07-2d3e-4b96-a0f4-5e7d9b3c1a28"}
{"crossSessionInbound":"accept"}
```

The first line is a frame that says the sender bypasses permission prompts, posted to a receiver that prompts. The second is the receipt that comes back: the receiver holds the frame for its user. The third is the setting that makes a session take every message. Frames are described in [frames](frames.md), the connection in [transport](transport.md).

## Receipts

A receipt is a control frame the receiver sends, on a new connection, to the `from` address of the frame it is about. It is the only answer a sender ever gets. The `status` values:

| `status` | when | extra fields |
|---|---|---|
| `held` | the inbound controls hold the message for the user | |
| `denied` | the user declined a held message; `reason` is "The recipient user declined your message; it was not delivered to their Claude session." | |
| `expired` | a held message was dropped: the dialog deadline passed, the hold buffer was full, or the session shut down with it held | |
| `expired` with `status_detail: "refused"` | the session refuses peer messages | `status_detail` |
| `delivered` | a held message was approved and released; `reason` is "Your previously-held message was approved and released to the recipient's Claude session." | |
| `dropped` | the inbox guard dropped it | `drop_reason`: `duplicate`, `rate-limited`, `hop-loop`, `hop-runaway` or `queue-full`; `dropped_msg_ids`: other ids coalesced into this receipt |

An accepted message produces no receipt. Silence after a clean write means the receiver took the frame; it does not mean the model has read it yet. In a receipt, `reason` is a sentence for a person, `orig_msg_id` is the `msg_id` of the frame concerned, and `from` is the receiver's own address.

The receiver sends only to a `from` it can vet: a `uds:` address whose path ends in `.sock` in the same directory as its own socket, or a `<digits>.sock`, `<digits>-<8 hex>.sock` or `<hex>.sock` in one of the known `cc-socks` directories of the same user when the sender's pid was verified. A `<own pid>.sock` beside the target's socket works. Before it sends, the receiver checks through the kernel that the process listening at `from` is the process that posted the frame, with the same pid and start token. So the listener must be the sender itself, which is what `examples/send.py --receipts` does.

Drop receipts to one sender are coalesced over a trail of 5 seconds; further drops add ids to `dropped_msg_ids`. The reasons `hop-loop` and `hop-runaway` cannot come from a local client: the hop checks run only when the sender's pid is not verified by the kernel, and a local socket connection always is verified.

## Delivery rules

The receiving model reads a message between tool calls during a running turn. A running tool is never interrupted. When the session is idle, Claude Code starts a new turn with the message. Frames posted by a `SessionStart` hook were delivered before the first prompt from stdin in a `-p` session.

The three priorities, in a test of three frames posted within 100 ms to an idle session:

| `priority` | what it did |
|---|---|
| `later` | arrived first and started a turn |
| `now` | joined that turn |
| `next` | the default; came in the following turn |

In the bundle `now` is handled outside the arrival queue and can overtake frames that arrived earlier, `next` is the default and `later` goes after `next`; beyond that one test the order is not seen. `now` does not interrupt a running tool: posted during a 20-second Bash command, it was read after the command finished. It does close an open permission prompt as a refusal ([what the model sees](frames.md#what-the-model-sees)). The Go client says that `now` can overtake earlier frames from the same sender.

At most 50 accepted peer messages wait to be read in one session. Past that, new ones are dropped with `queue-full`.

## Inbound controls

The setting `crossSessionInbound` takes one of three values and applies to messages from the socket like any other peer message.

| value | what the receiver does |
|---|---|
| `accept` | delivers every frame |
| `hold` | sends a `held` receipt and delivers nothing until a later `accept` applies; such a message does not expire on its own |
| `refuse` | drops the message and sends an `expired` receipt with `status_detail: "refused"` |

A session that refuses still binds its socket and looks the same from outside.

When no value applies, the receiver decides per message, by permission class. A session is **bypassing** when it runs in `bypassPermissions`, or in plan mode in an interactive terminal where bypass is available. Every other session is **prompting**, `auto`, `acceptEdits` and `dontAsk` included.

| receiver | sender | result |
|---|---|---|
| prompting | asserts no class, or `prompting` | delivered |
| prompting | asserts `bypass` | held, with `holdCause: mode-mismatch` |
| bypassing | asserts `bypass` | delivered |
| bypassing | anything else | held |
| cannot tell its own mode | any | held |

The sender's class is the `from-mode` attribute of the envelope ([the envelope](frames.md#the-envelope)). A default `-p` session delivered frames that assert no class, a bypass assertion to a prompting session was held, and a `-p` session in `bypassPermissions` mode behaved as the table says (`conformance/check.py` R20 and R20b). A socket post the receiver cannot verify as its own child is treated like any other post that asserts no class. That the attribute is the only way to assert a class is read in the bundle only.

**Own-child exception.** A message the receiver can verify as coming from its own child process, such as a hook or a Bash command posting back to its session, is delivered whatever the class. On Linux the receiver verifies it by process ancestry, even after the child exited; on macOS only while the child runs. Where that evidence is missing, the child token in the auth line verifies it ([the auth frame](frames.md#the-auth-frame)). The exception was seen on Linux with and without the token.

A held message in an interactive terminal opens a dialog. Left unanswered past `dialogExpiry`, 5 minutes by default, it is dropped. A `-p` session has no dialog and drops a default-held message at the same deadline, reporting `expired`. At most 100 messages are held, and past that the oldest is dropped. The dialog was seen to open on a held bypass assertion, with two choices: deny, which drops the message and sends a `denied` receipt, or deliver, which releases it to the model and sends a `delivered` receipt.

## Limits

| limit | value |
|---|---|
| line length | 1,048,576 characters; the connection is dropped at that point |
| message size at the sender | about a million characters of serialised frame; Claude Code refuses to send past it and names the sizes; the bundle has the same 1,048,576 constant, less the auth line |
| first line deadline | 30 seconds from connect |
| queued accepted messages per session | 50 |
| held messages per session | 100 |
| identical repeats | a body identical to the previous one from the same sender within 30 seconds is dropped as `duplicate`; a repeat at 5 seconds was dropped and the same body after 31 seconds was admitted |
| rate per sender | a bucket of 30 messages refilled at 0.5 per second; past it, `rate-limited`; a burst of distinct bodies was admitted up to the 30th and the 31st was dropped, and from an emptied bucket steady probing was admitted at 0.5 per second |
| senders tracked for the above | 256, the least recently seen evicted |
| relay loops | a `hop-chain` that already holds the receiver 10 times, or is longer than 28 hops, is dropped as `hop-loop` or `hop-runaway` |
| bursts from a Claude sender | Claude Code paces its own sends to one target and refuses more "just now"; this does not apply to another sender, which meets the receiver's bucket above |

The sender key for the duplicate and rate rules is the frame's `from` address when present, else the connecting pid.

## Evidence

| Claim | Source |
|---|---|
| A held message gets a `held` receipt | OBSERVED (`crossSessionInbound: hold`) |
| A declined held message gets a `denied` receipt | OBSERVED (declined in the interactive hold dialog) |
| A held message dropped at a deadline, at a full buffer or at exit gets an `expired` receipt | OBSERVED (settled as `expired` at session exit) |
| A refusing session answers `expired` with `status_detail: "refused"` | OBSERVED |
| An approved held message gets a `delivered` receipt and then starts a turn | OBSERVED (approved in the interactive hold dialog) |
| A guard drop for `duplicate` or `rate-limited` gets a `dropped` receipt | OBSERVED |
| The `queue-full` drop reason, and that `hop-loop` and `hop-runaway` cannot come from a local client | UNVERIFIED (bundle) |
| An accepted message produces no receipt | OBSERVED (no frame within 6 s of an immediate delivery) |
| An accepted message produces no receipt | THIRD-PARTY |
| `reason` is a sentence, `orig_msg_id` is the `msg_id` concerned, `from` is the receiver's own address | OBSERVED |
| The receiver sends only to a `from` it can vet | UNVERIFIED (bundle) |
| A `<own pid>.sock` beside the target's socket works | OBSERVED |
| The receiver checks that the process listening at `from` is the one that posted | UNVERIFIED (bundle) |
| Drop receipts to one sender are coalesced over 5 seconds | UNVERIFIED (bundle) |
| A message is read between tool calls and a running tool is never interrupted | OFFICIAL |
| The same, seen in a `-p` session: a frame posted during a tool call reached the model later in the turn | OBSERVED |
| An idle session starts a new turn with the message | OFFICIAL |
| The same, in `-p` and interactive sessions | OBSERVED |
| Order of `later`, `now` and `next` in three frames within 100 ms | OBSERVED |
| `now` is handled outside the queue, `next` is the default, `later` goes after `next` | UNVERIFIED |
| `now` does not interrupt a running tool | OBSERVED (20-second Bash command) |
| `now` closes an open permission prompt as a refusal | OBSERVED |
| `now` can overtake earlier frames from the same sender | THIRD-PARTY |
| Frames from a `SessionStart` hook arrive before the first prompt from stdin | OBSERVED (`-p` session) |
| At most 50 accepted messages wait per session | OFFICIAL |
| The receipt reason for the 51st is `queue-full` | UNVERIFIED (bundle) |
| `crossSessionInbound` takes `accept`, `hold` or `refuse` and applies to socket messages | OFFICIAL |
| `accept` delivers every frame | OFFICIAL |
| `hold` sends a `held` receipt and delivers nothing until a later `accept`, with no expiry of its own | OFFICIAL |
| `hold` seen: `held`, then `expired` only at session exit | OBSERVED |
| `refuse` drops the message | OFFICIAL |
| `refuse` seen: the `expired` receipt with `status_detail: "refused"` | OBSERVED |
| A refusing session still binds its socket and looks the same from outside | OFFICIAL |
| With no value set, the receiver decides per message by permission class | OFFICIAL |
| A session is bypassing or prompting, and which modes fall in which | OFFICIAL |
| A prompting receiver delivers all but a message that asserts bypass, which it holds | OFFICIAL |
| A default `-p` session delivers frames that assert no class, and holds a bypass assertion (`holdCause: mode-mismatch`) | OBSERVED |
| A bypassing receiver holds all but a message whose sender asserts bypass | OFFICIAL |
| The same, at a `-p` receiver in `bypassPermissions` mode | OBSERVED (R20, R20b) |
| The sender's class is the `from-mode` attribute: `bypass` is held and `prompting` delivered at a prompting receiver | OBSERVED |
| A post the receiver cannot verify as its own child is treated as asserting no class | OFFICIAL |
| The `from-mode` attribute is the only way to assert a class | UNVERIFIED (bundle) |
| A frame without `from-mode` is delivered to a prompting receiver | OBSERVED |
| The own-child exception, by ancestry on Linux and macOS and by token elsewhere | OFFICIAL |
| The own-child exception, seen on Linux with and without the token | OBSERVED |
| The dialog, its `dialogExpiry`, the `-p` drop and the limit of 100 held | OFFICIAL |
| The dialog opened on a held bypass assertion, with the two choices and their receipts | OBSERVED |
| A receiver that cannot tell its own permission mode holds | UNVERIFIED (bundle) |
| The line length limit of 1,048,576 characters | OBSERVED |
| Claude Code refuses to send past about a million characters | OFFICIAL |
| That limit is the 1,048,576 constant less the auth line | UNVERIFIED (bundle) |
| The first line must arrive within 30 seconds | OFFICIAL |
| The same, at 30.0 seconds | OBSERVED |
| 50 queued messages per session | OFFICIAL |
| 100 held messages per session | OFFICIAL |
| An identical repeat within 30 seconds is dropped | OBSERVED (a repeat at 5 s dropped, the same body at 31 s admitted) |
| The bucket of 30 messages refilled at 0.5 per second | OBSERVED |
| 256 senders tracked, least recently seen evicted | UNVERIFIED (bundle) |
| The relay loop limits of 10 and 28 | UNVERIFIED (bundle) |
| A Claude sender paces its own sends to one target | OFFICIAL |
| The sender key is `from`, else the connecting pid | UNVERIFIED (bundle) |
