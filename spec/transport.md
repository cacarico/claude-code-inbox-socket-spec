# Transport

The socket, its path and permissions, how to find a session, and how lines are framed on it.

```
/run/user/1000/cc-socks/12345.sock                          the inbox of session 12345
~/.claude/sessions/12345.json                               its registry record
Peer address: uds:/run/user/1000/cc-socks/12345.sock        what /status shows
[uds-messaging] Listening: /run/user/1000/cc-socks/12345.sock   the debug log (claude --debug)
```

## The inbox

Each session with messaging on binds one inbox. On macOS and Linux (Linux inside WSL 2 included) it is a Unix domain stream socket; on native Windows it is a named pipe. The inbox is up within about two seconds of process start, before the first model turn. It goes down when the session exits, and the socket file is removed. The debug log records both events.

The socket has mode `0600` and sits in a directory of mode `0700` owned by the user, so another user's processes cannot connect.

A connection is one-way. The receiver never writes on the client's connection; it reads until the client half-closes, then closes. Receipts and notices travel on new connections that the receiver opens to the sender's own socket ([receipt frames](delivery.md#receipts)).

### Socket paths

| case | path |
|---|---|
| default on Linux | `$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`, where `<pid>` is the session's process id |
| `XDG_RUNTIME_DIR` unset | `<tmp>/cc-socks/<pid>.sock`, for example `/tmp/cc-socks/<pid>.sock` |
| `<tmp>` | the system temp directory, or `$CLAUDE_CODE_TMPDIR` when set |
| path too long, or directory refused | `/tmp/cc-socks-<uid>/<pid>.sock`, a private directory per user |
| default path in use by another live process (a sibling pid namespace, say) | `<pid>-<8 hex>.sock` |
| chosen by the user | the hidden flag `--messaging-socket-path <abs path>`; the parent must be a real directory, mode `0700`, owned by the user; the flag is not in `--help` |
| Termux base | `$PREFIX/tmp` |
| native Windows | `\\.\pipe\cc-msg-<32 hex>` |

The path limit is 103 bytes, and a directory is refused when it is not owned by the user or root, is writable by group or others without the sticky bit, is a symlink, and in similar cases; the full list is unknown. When no directory is acceptable the session runs with no inbox, and `/status` says `unavailable`. Nothing on native Windows was run for this document.

## Finding a session

There are three ways to find a session's socket.

### Environment

Claude Code exports `CLAUDE_CODE_MESSAGING_SOCKET` (the path) and `CLAUDE_CODE_MESSAGING_TOKEN` (a per-session secret, see [the auth frame](frames.md#the-auth-frame)) to its hooks and Bash tool commands. It does so before any hook runs, `SessionStart` included. Both variables are unset in a session without an inbox.

### `/status`

An interactive session shows a `Peer address` row with `uds:<path>`, or `unavailable` and the reason.

### The session registry

Each session writes `<config dir>/sessions/<pid>.json`, where `<config dir>` is `$CLAUDE_CONFIG_DIR` if set, else `~/.claude`. The Go client calls the same file `<config>/sessions/<pid>.json`. The fields seen in 2.1.310:

| field | example | meaning |
|---|---|---|
| `pid` | `12345` | the session's process id |
| `sessionId` | `2a191324-a7b9-431f-85b4-9a1773eeea24` | the conversation id; changes on `/clear` |
| `messagingSocketPath` | `/run/user/1000/cc-socks/12345.sock` | the inbox |
| `name`, `nameSource`, `nameSince` | `api-worker`, `user`, `derived`, `peer` | the display name and where it came from; the other values of `nameSource` are unknown |
| `cwd`, `startedAt`, `updatedAt` | | working directory and timestamps (ms since epoch) |
| `procStart`, `pidDomain` | | process start token and pid-namespace id, used to tell a live record from a recycled pid |
| `kind`, `entrypoint` | `interactive`, `cli` or `sdk-cli` | `-p` sessions report `kind: interactive`, `entrypoint: sdk-cli` |
| `status`, `statusUpdatedAt` | `idle`, `busy` | kept current by interactive sessions |
| `peerProtocol`, `peerFeatures` | `1`, `["notify_idle", "reply_across_default_dirs", "artifact_yield"]` | protocol version and optional features |
| `version` | `2.1.310` | Claude Code version |

A record can outlive its process, so check that the socket connects before trusting a record. Connecting is a reliable liveness test: a stale socket file refuses the connection, and any Unix-socket client such as `nc` or `socat` can probe it.

Beside each record sits `<pid>.<sha256 of the canonical socket path>.key`, mode `0600`. It is a JSON file with `peerToken` (32 hex), `procStart` and `pidDomain`, and it is how another session authenticates to this one where auth is required. The name and the fields were seen (`conformance/check.py` R37).

## Lines

A connection carries lines. This one holds an auth line and two frames:

```
{"type":"auth","token":"0123456789abcdef0123456789abcdef"}
{"type":"user","message":{"content":"the build is green"}}
{"type":"user","message":{"content":"and the deploy is next"}}
```

The client writes UTF-8 JSON, one object per line, each ended by `\n`, and half-closes (shuts down its write side) when it is done. Several frames may share one connection and are processed in order. A final fragment with no trailing newline is parsed when the client closes.

| the receiver gets | it does |
|---|---|
| a blank line, or a line that is not valid JSON | skips it and keeps reading the rest of the connection |
| a JSON value that is not an object with a string `type` | ignores it; the debug log says `Ignoring message without valid type field` |
| an object with a `type` it does not know | ignores it; the debug log says `Received unhandled message type` |
| a line over 1,048,576 characters with no newline | drops the connection and delivers nothing from it; the session is unharmed |
| no complete line within 30 seconds of connecting | closes the connection |
| on native Windows, a first line that is not a valid auth line | closes the connection; which first lines count (blank, unparseable, not auth) is unknown |

The 30-second timer is cleared by the first complete line. The line cap is 1 MiB for the Go client too. So build the whole payload first, then connect.

`user` frames are processed in arrival order on one queue. `control` frames other than `notify_when_idle`, and `user` frames with `priority: now` and no attachments, are handled at once, outside that order.

## Evidence

| Claim | Source |
|---|---|
| One inbox per session: a Unix domain stream socket on macOS and Linux (WSL 2 included), a named pipe on native Windows | OFFICIAL |
| The default path on Linux is `$XDG_RUNTIME_DIR/cc-socks/<pid>.sock` | OBSERVED (for example `/run/user/1000/cc-socks/12345.sock`) |
| With `XDG_RUNTIME_DIR` unset the path is `<tmp>/cc-socks/<pid>.sock` | OBSERVED |
| `$CLAUDE_CODE_TMPDIR` replaces the system temp directory as the base | OBSERVED (R39) |
| A long path falls back to a per-user directory | OBSERVED (174-byte path, 2.1.310 on Linux) |
| A refused directory falls back | OFFICIAL |
| The 103-byte limit | OBSERVED (R40, R45c) |
| A base writable by others without the sticky bit is refused, and used with it; a sockets directory that is a symlink is refused | OBSERVED (R41, R41b, R41c) |
| The other refusal conditions | UNVERIFIED (bundle) |
| Termux base is `$PREFIX/tmp` | OBSERVED (R42, `TERMUX_VERSION` set on Linux) |
| With no acceptable directory the session has no inbox and `/status` says `unavailable` | OFFICIAL |
| The socket is mode `0600` in a `0700` directory | OBSERVED (mode) |
| Another user's processes cannot connect | OFFICIAL (restricted to the OS user) |
| `--messaging-socket-path` binds the inbox at a chosen path, in a directory of mode `0700` | OBSERVED (R45, R45b; not in `--help`) |
| `<pid>-<8 hex>.sock` when the default path is in use | OBSERVED (R44) |
| Windows pipe names have the form `\\.\pipe\cc-msg-<32 hex>` | UNVERIFIED (bundle) |
| The inbox is up within about two seconds and goes down with the session | OBSERVED |
| The debug log records the listening path and the shutdown | OBSERVED |
| A connection is one-way; the receiver only reads | OBSERVED |
| The environment variables are exported to hooks and Bash commands, `SessionStart` included | OFFICIAL |
| The same, seen in a hook and in Bash tool calls | OBSERVED (`SessionStart` hook and Bash tool calls) |
| Both variables are unset without an inbox | OBSERVED (R43) |
| `/status` shows the `Peer address` row | OFFICIAL |
| The same, seen | OBSERVED (`Peer address: uds:/run/user/1000/cc-socks/<pid>.sock`) |
| The registry file is `<config dir>/sessions/<pid>.json` | OBSERVED |
| The Go client names the same file | THIRD-PARTY |
| The registry fields in the table | OBSERVED (2.1.310) |
| `sessionId` changes on `/clear` | THIRD-PARTY |
| A record can outlive its process | THIRD-PARTY |
| Connecting is a reliable liveness test | OBSERVED |
| The key file name | OBSERVED (file name) |
| The key file contents | OBSERVED (R37) |
| A stale socket file refuses the connection | OBSERVED |
| The client writes UTF-8 JSON lines and half-closes | OBSERVED |
| Several frames on one connection are processed in order | OBSERVED |
| A final fragment with no newline is parsed when the client closes | OBSERVED |
| Blank and invalid lines are skipped | OBSERVED |
| On Windows a first line that is not a valid auth line closes the connection | OFFICIAL |
| Which first lines count there | UNVERIFIED |
| A line over 1,048,576 characters drops the connection | OBSERVED |
| The Go client gives the same cap as 1 MiB | THIRD-PARTY |
| No complete line within 30 seconds closes the connection | OFFICIAL |
| The same, at 30.0 seconds | OBSERVED (30.0 s) |
| The first complete line clears the timer | OBSERVED (R27; R27b with a blank line) |
| Build the whole payload first, then connect | OFFICIAL |
| A frame that is not an object with a string `type` is ignored | OBSERVED |
| An unknown `type` is ignored | OBSERVED |
| `user` frames are read in arrival order, and a `now` frame ahead of them | OBSERVED (R23) |
| Which other frames are handled outside that order | UNVERIFIED (bundle) |
