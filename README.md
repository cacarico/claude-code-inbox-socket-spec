# Claude Code inbox socket protocol

Every Claude Code session (2.1.224 and later on macOS and Linux, 2.1.234 on
Windows) listens on a private socket where other sessions on the same machine
drop messages. Claude Code uses it for its own cross-session messaging, but
anything that can write a line of JSON to a Unix socket can post into a running
session: a CI job, a file watcher, a deploy script, a hook that waits for a slow
command and then wakes the session that started it.

The socket is a Unix domain socket named after the session's process id, in a
directory only your user can enter. A client connects, writes one JSON object per
line and closes its write side. A `user` frame carries text; the session reads it
between tool calls when it is busy and starts a new turn with it when it is idle.
The text reaches the model marked as coming from another Claude session, never
as the user's own words. Nothing comes back on that connection. Receipts for
held, refused or dropped messages, and the one-time idle notice, arrive as
frames on a socket of the sender's own, if the sender named one.

Anthropic documents the environment variables, the auth line and the delivery
rules. The frame itself is not documented and not a public interface: it was
read out of the shipped binary and checked live. [SPEC.md](SPEC.md) writes it
down, with every statement tagged by how it is known, and
[conformance/check.py](conformance/check.py) re-checks it against whatever
`claude --version` you have installed.

This is an unofficial, independent document. It is not affiliated with or
endorsed by Anthropic.

## Quick start

From a Bash tool call or a hook inside a session, post to that same session:

```sh
printf '%s\n' '{"type":"user","message":{"role":"user","content":"hello from a script"}}' \
  | nc -N -U "$CLAUDE_CODE_MESSAGING_SOCKET"          # OpenBSD nc; or:
  | socat - UNIX-CONNECT:"$CLAUDE_CODE_MESSAGING_SOCKET"
```

From anywhere on the machine, with the standard library only:

```sh
python3 examples/send.py --list                        # live sessions and their sockets
python3 examples/send.py --name my-session "the build is green"
python3 examples/send.py --socket /run/user/1000/cc-socks/12345.sock --receipts 10 "hello"
```

`examples/send.sh` does the same with nc or socat, `examples/waiter.py` blocks on
a command or a file and then nudges the session it was started from, and
`python3 conformance/check.py` prints a pass or fail per rule for the installed
Claude Code (it starts throwaway `claude -p` sessions, which cost a few cents).

## Versions

See [CHANGELOG.md](CHANGELOG.md) for the Claude Code versions this was verified
against and what changed between them.

## Credits and license

MIT, see [LICENSE](LICENSE). The official page is
[Message your other Claude Code sessions](https://code.claude.com/docs/en/cross-session-messaging).
The frame was first written down by
[claude-code-socket-transport](https://github.com/PeterSR/claude-code-socket-transport),
a Go client by PeterSR, against Claude Code 2.1.233.
