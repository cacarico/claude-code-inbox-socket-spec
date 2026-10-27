# Platforms

How `claude -p` and bare mode differ from an interactive session, what changes on each platform, and the steps of a minimal client.

```
claude -p --input-format stream-json --output-format stream-json
claude -p --input-format stream-json --settings '{"crossSessionInbound":"accept"}'
claude --bare
```

The first command starts a `-p` session that stays open and keeps receiving. The second takes messages unattended, because a `-p` session has no hold dialog. The third skips the default inbox.

## `claude -p`

A `claude -p` session binds an inbox like an interactive one and appears in the registry, with `entrypoint: sdk-cli` ([finding a session](transport.md#finding-a-session)). A plain `-p` run exits after its turn and takes the socket with it. With `--input-format stream-json` it stays open and keeps receiving, which is how `conformance/check.py` tests it.

A `-p` session cannot show the hold dialog ([inbound controls](delivery.md#inbound-controls)). To take messages unattended, start it with `--settings '{"crossSessionInbound":"accept"}'`. The `rename` control frame has no effect on a `-p` session.

## Bare mode

Bare mode is `--bare`, and `CLAUDE_CODE_SIMPLE=1`, which is treated the same. It skips the default inbox, so the session cannot receive and is not listed. An explicit `--messaging-socket-path` still binds an inbox under bare mode, as far as the bundle shows. Bare mode was not run here, since it needs an API-key login.

## Platform notes

| platform | what holds |
|---|---|
| Linux and WSL 2 | everything in these pages; run on Linux |
| macOS | the same socket and frames; auth is optional; the own-child check uses process evidence only while the child runs; Claude Code's own client delays its half-close by 150 ms |
| native Windows | a named pipe; the auth line is required, with the peer token read from the key file; no process evidence; nothing here was run on Windows |
| containers | a session inside and one on the host cannot reach each other; two inside the same container can; where Claude Code is pid 1 it has no process evidence and relies on the token |
| sandboxed Bash | whether a command can reach the socket from inside the sandbox is set by `sandbox.network.allowUnixSockets` and `allowAllUnixSockets` |

## A minimal client

1. Find the socket: `$CLAUDE_CODE_MESSAGING_SOCKET` inside the session, or the registry record of the session you want, after checking it connects.
2. Build the payload first: an optional auth line, then one `user` frame with `type` and `message.content`. Add `msg_id`, `from` and `session_id` if you want receipts.
3. Connect, write, half-close, read to end of file, close.
4. If you set `from`, listen there. No frame means accepted. `held`, `expired`, `denied` or `dropped` means the model did not get it ([receipts](delivery.md#receipts)).

`examples/send.py` is this in code, and `examples/send.sh` and `examples/send.mjs` post a message from a shell and from Node. `conformance/check.py` holds the checks behind the observed statements that a headless `-p` session can show. The rest, such as the interactive hold dialog, the fallback socket directories and the refill rate, were run by hand and are listed in [the changelog](../CHANGELOG.md).

## Evidence

| Claim | Source |
|---|---|
| A `-p` session binds an inbox and appears in the registry | OFFICIAL |
| The same, seen with `entrypoint: sdk-cli` | OBSERVED |
| A plain `-p` run exits after its turn; with `--input-format stream-json` it stays open and keeps receiving | OBSERVED |
| A `-p` session cannot show the hold dialog | OFFICIAL |
| `--settings '{"crossSessionInbound":"accept"}'` takes messages unattended | OFFICIAL |
| `rename` has no effect on a `-p` session | OBSERVED |
| Bare mode and `CLAUDE_CODE_SIMPLE=1` skip the default inbox, so the session cannot receive and is not listed | OFFICIAL |
| An explicit `--messaging-socket-path` binds an inbox under bare mode | UNVERIFIED (bundle) |
| Everything in these pages holds on Linux and WSL 2 | OBSERVED (Linux) |
| macOS: the same socket and frames, optional auth, own-child evidence only while the child runs | OFFICIAL |
| Claude Code's own client delays its half-close by 150 ms on macOS | UNVERIFIED (bundle) |
| Native Windows: a named pipe, a required auth line with the peer token from the key file, no process evidence | OFFICIAL |
| A session in a container and one on the host cannot reach each other; two in one container can | OFFICIAL |
| Where Claude Code is pid 1 it has no process evidence and relies on the token | OFFICIAL |
| Sandbox settings decide whether a sandboxed command can reach the socket | OFFICIAL |
