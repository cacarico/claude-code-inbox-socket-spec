# Claude Code inbox socket

Every Claude Code session listens on a private Unix socket (a named pipe on
Windows). Other sessions on the machine use it to message each other. Anything
that can write a line of JSON to a socket can use it too: a CI job, a file
watcher, a script that waits for a slow command and then wakes the session that
started it.

The socket is named after the session's process id, in a directory only your
user can enter. A client connects, writes one JSON object per line and closes
its write side. A `user` frame carries text. A busy session reads it between
tool calls; an idle one starts a new turn with it. The model sees the text
marked as coming from another Claude session, never as the user's own words.
Nothing comes back on that connection. Receipts for held, denied, refused,
dropped or expired messages, and for a held message later delivered, arrive on
a socket of the sender's own, if the sender named one.

Anthropic documents the environment variables, the auth line and the delivery
rules. The frame itself is not documented and not a public interface. It was
read out of the shipped binary and checked live. [SPEC.md](SPEC.md) writes it
down with every statement tagged by how it is known, and
[conformance/check.py](conformance/check.py) re-checks it against the
`claude --version` you have.

Unofficial. Not affiliated with or endorsed by Anthropic.

## Quick start

From a Bash tool call or a hook, post to the session you are in:

```sh
printf '%s\n' '{"type":"user","message":{"content":"hello from a script"}}' \
  | nc -N -U "$CLAUDE_CODE_MESSAGING_SOCKET"       # or: socat - UNIX-CONNECT:"$CLAUDE_CODE_MESSAGING_SOCKET"
```

From anywhere on the machine, standard library only:

```sh
python3 examples/send.py --list                      # live sessions and their sockets
python3 examples/send.py --name my-session "the build is green"
python3 examples/send.py --socket /run/user/1000/cc-socks/12345.sock --receipts 10 "hello"
```

`examples/send.sh` does the same with nc or socat. `examples/waiter.py` waits
for a command or a file, then nudges the session it was started from.
`python3 conformance/check.py` prints a pass or fail per rule for your Claude
Code; it starts throwaway `claude -p` sessions, which cost a few cents.

## License and credits

MIT, see [LICENSE](LICENSE). Sources: the official
[cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging)
page, and [claude-code-socket-transport](https://github.com/PeterSR/claude-code-socket-transport)
by PeterSR, the Go client that first wrote the frame down. Versions checked:
[CHANGELOG.md](CHANGELOG.md).
