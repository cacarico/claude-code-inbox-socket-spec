# About this document

What the document covers, the versions it names, the conventions it uses, and a list of terms.

## Status

This document is descriptive. It records what Claude Code 2.1.310 does on Linux, as seen from outside and as read in the shipped bundle, and it binds nobody. The source of truth is the running Claude Code: where the two differ, the binary is right and the document has a bug. Anthropic documents the environment variables, the auth line and the delivery rules; the frame itself is an internal interface.

## Versions

Four things carry a version. The frame has `msgV`, which is 1. Each registry record has `peerProtocol`, which is 1, and `peerFeatures`, a list of optional features. The document was checked against Claude Code 2.1.310; Anthropic's documentation gives the floors 2.1.224 (macOS and Linux), 2.1.234 (native Windows), 2.1.236 (the idle notice) and 2.1.248 (third-party providers), and [the changelog](../CHANGELOG.md) lists them with the run. The document's own revisions are in the "Changes to this document" section of the changelog.

When `claude --version` reports something newer than 2.1.310, run `make conformance` and read the changelog before trusting a page.

## Conventions

The receiver is the Claude Code session that owns the socket. The sender, or client, is the process that connects to it. Plain indicative sentences describe the receiver. "Must" and "should" appear only in instructions to a client, and mean what they mean in ordinary English; the document is descriptive and uses no capitalised requirement keywords. Numbers are decimal unless written `0x`. A path shown with `<pid>` is a pattern.

## Terminology

| term | meaning | where |
|---|---|---|
| session | one running Claude Code process, interactive or `-p` | [the inbox](transport.md#the-inbox) |
| receiver | the session that owns the socket and reads from it | Conventions, above |
| sender | the process that connects and writes; also called the client | Conventions, above |
| inbox | the socket a session binds to take messages from other processes | [the inbox](transport.md#the-inbox) |
| socket path | the file system path of the inbox | [socket paths](transport.md#socket-paths) |
| registry record | the file `<config dir>/sessions/<pid>.json` that describes a session | [finding a session](transport.md#finding-a-session) |
| frame | one JSON object sent to the receiver | [lines](transport.md#lines) |
| line | one frame followed by `\n` | [lines](transport.md#lines) |
| auth line | the optional first line of a connection, a frame of type `auth` | [the auth frame](frames.md#the-auth-frame) |
| child token | the secret exported to a session's own hooks and commands | [the auth frame](frames.md#the-auth-frame) |
| peer token | the secret in the registry key file, for other sessions | [the auth frame](frames.md#the-auth-frame) |
| message frame | a frame of type `user`, which carries text for the model | [the message frame](frames.md#the-message-frame) |
| control frame | a frame of type `control`, which carries an action | [control frames](frames.md#control-frames) |
| receipt | a control frame the receiver sends about a message it did not deliver | [receipt frames](../SPEC.md#7-receipt-frames) |
| notice | the `peer_idle_notice` control frame, sent when a watched session is idle | [control frames](frames.md#control-frames) |
| envelope | the `<cross-session-message>` wrapper inside `content` that names the sender | [the envelope](frames.md#the-envelope) |
| reply address | the `from` field: `uds:` and the path of a socket the sender listens on | [the message frame](frames.md#the-message-frame) |
| own child | a process the receiver can verify descends from itself | [inbound controls](../SPEC.md#9-inbound-controls) |
| permission class | bypassing or prompting, the receiver's and the sender's | [inbound controls](../SPEC.md#9-inbound-controls) |
| inbound control | the `crossSessionInbound` setting: `accept`, `hold` or `refuse` | [inbound controls](../SPEC.md#9-inbound-controls) |
| hold | keeping a message undelivered until the user approves it or a deadline passes | [inbound controls](../SPEC.md#9-inbound-controls) |
| guard | the receiver's drop rules for repeats, rate, queue size and relay loops | [limits](../SPEC.md#10-limits) |
| queue | where accepted message frames wait to be read | [delivery rules](../SPEC.md#8-delivery-rules) |
| priority | the `priority` field of a message frame: `now`, `next` or `later` | [the message frame](frames.md#the-message-frame) |
| turn | one pass in which the model reads its input and answers | [delivery rules](../SPEC.md#8-delivery-rules) |
