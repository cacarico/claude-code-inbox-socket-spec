#!/usr/bin/env python3
"""Post a message to a Claude Code session's inbox socket. Standard library only.

    send.py "text"                         # to the session this shell runs under
    send.py --name api-worker "text"       # to a session by name (registry lookup)
    send.py --pid 12345 "text"             # by process id
    send.py --socket /path/to/12345.sock "text"
    send.py --receipts 20 "text"           # also listen 20 s for receipt frames
    send.py --notify-idle --receipts 600   # ask for one notice when the session is next idle
    send.py --rename new-name              # rename the target (interactive sessions)
    send.py --list                         # the sessions the registry knows, live or not

Receipts need a reply socket. send.py binds one beside the target's socket, named
after its own pid, and puts its address in the frame's `from` field. An accepted
message produces no receipt (Claude Code 2.1.310): silence after a successful write
means the message was taken. A receipt arrives for held, refused, expired, denied
and dropped messages, and when a held message is later released (delivered).

See SPEC.md for the frames. This is not a public interface of Claude Code.
"""
import argparse
import json
import os
import pathlib
import socket
import sys
import time
import uuid

SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:_/.\\-")


def config_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or (pathlib.Path.home() / ".claude"))


def uds_address(path: str) -> str:
    """`uds:` + the path, percent-encoded the way Claude Code does it."""
    out = []
    for ch in path:
        if ch in SAFE:
            out.append(ch)
        else:
            out.extend("%%%02X" % b for b in ch.encode("utf-8"))
    return "uds:" + "".join(out)


def socket_alive(path: str) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def registry():
    """Session records: <config dir>/sessions/<pid>.json. Records can outlive their process."""
    out = []
    d = config_dir() / "sessions"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        sock = rec.get("messagingSocketPath")
        rec["_alive"] = bool(sock) and os.path.exists(sock) and socket_alive(sock)
        out.append(rec)
    return out


def find_target(args):
    if args.socket:
        return args.socket, None
    if not (args.pid or args.name or args.session_id):
        sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
        if not sock:
            sys.exit("send.py: no target: pass --socket, --pid, --name or --session-id, or run under a Claude Code session")
        return sock, os.environ.get("CLAUDE_CODE_SESSION_ID")
    matches = []
    for rec in registry():
        if args.pid and rec.get("pid") != args.pid:
            continue
        if args.name and rec.get("name") != args.name:
            continue
        if args.session_id and rec.get("sessionId") != args.session_id:
            continue
        if rec["_alive"]:
            matches.append(rec)
    if not matches:
        sys.exit("send.py: no live session matches")
    if len(matches) > 1:
        sys.exit("send.py: %d live sessions match; pick one with --pid: %s" % (len(matches), ", ".join(str(m["pid"]) for m in matches)))
    return matches[0]["messagingSocketPath"], matches[0].get("sessionId")


class ReplySocket:
    """A listener beside the target socket, so the receiver's receipts can reach us."""

    def __init__(self, target_sock: str):
        d = os.path.dirname(target_sock)
        self.path = os.path.join(d, "%d.sock" % os.getpid())
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        os.chmod(self.path, 0o600)
        self.srv.listen(8)
        self.address = uds_address(self.path)

    def frames(self, seconds: float):
        """Yield receipt frames (dicts) until the deadline."""
        end = time.time() + seconds
        while True:
            left = end - time.time()
            if left <= 0:
                return
            self.srv.settimeout(left)
            try:
                c, _ = self.srv.accept()
            except socket.timeout:
                return
            c.settimeout(5)
            data = b""
            try:
                while True:
                    chunk = c.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            except socket.timeout:
                pass
            c.close()
            for line in data.decode("utf-8", "replace").splitlines():
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        yield {"raw": line}

    def close(self):
        self.srv.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


def post(sock_path: str, frames) -> None:
    """One connection: every frame as a JSON line, then half-close. Build the payload
    before connecting: the receiver closes a connection that has not completed a line
    within 30 seconds."""
    payload = "".join(json.dumps(f, ensure_ascii=False) + "\n" for f in frames).encode("utf-8")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    s.connect(sock_path)
    s.sendall(payload)
    s.shutdown(socket.SHUT_WR)
    # The receiver writes nothing on this connection; wait for its EOF so the data is flushed.
    try:
        while s.recv(4096):
            pass
    except OSError:
        pass
    s.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("text", nargs="*", help="the message; '-' reads stdin")
    t = ap.add_argument_group("target")
    t.add_argument("--socket")
    t.add_argument("--pid", type=int)
    t.add_argument("--name")
    t.add_argument("--session-id")
    ap.add_argument("--list", action="store_true", help="list registry records and exit")
    ap.add_argument("--priority", choices=["now", "next", "later"], default="next")
    ap.add_argument("--from-name", help="wrap the text in a <cross-session-message> envelope carrying this sender name")
    ap.add_argument("--auth", metavar="TOKEN", help="auth line token (default: $CLAUDE_CODE_MESSAGING_TOKEN when posting to your own session)")
    ap.add_argument("--no-auth", action="store_true")
    ap.add_argument("--receipts", type=float, metavar="SECONDS", help="bind a reply socket, set `from`, and print receipts for this long")
    ap.add_argument("--notify-idle", action="store_true", help="send a notify_when_idle control instead of (or before) a message")
    ap.add_argument("--rename", metavar="NAME", help="send a rename control")
    args = ap.parse_args()

    if args.list:
        for rec in registry():
            print("%-7s pid=%-8s %-28s %s  %s" % ("live" if rec["_alive"] else "stale", rec.get("pid"), rec.get("name"), rec.get("messagingSocketPath"), rec.get("cwd", "")))
        return 0

    sock_path, session_id = find_target(args)
    text = None
    if args.text:
        text = sys.stdin.read() if args.text == ["-"] else " ".join(args.text)
    if text is None and not (args.notify_idle or args.rename):
        ap.error("no text given")

    reply = None
    if args.receipts is not None or args.notify_idle:
        reply = ReplySocket(sock_path)

    frames = []
    own = sock_path == os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    token = args.auth or (os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN") if own else None)
    if token and not args.no_auth:
        frames.append({"type": "auth", "token": token})

    def base(**extra):
        f = {"msgV": 1, "msg_id": str(uuid.uuid4())}
        if session_id:
            f["session_id"] = session_id
        if reply:
            f["from"] = reply.address
        f.update(extra)
        return f

    if args.rename:
        frames.append(base(type="control", action="rename", name=args.rename))
    if text is not None:
        body = text
        if args.from_name:
            attrs = ' from="%s"' % reply.address if reply else ""
            attrs += ' from-name="%s"' % args.from_name.replace('"', "").replace("<", "").replace(">", "")
            body = "<cross-session-message%s>\n%s\n</cross-session-message>" % (attrs, body)
        frames.append(base(type="user", message={"role": "user", "content": body}, priority=args.priority))
    notify_id = None
    if args.notify_idle:
        notify_id = str(uuid.uuid4())
        frames.append({"msgV": 1, "msg_id": notify_id, "type": "control", "action": "notify_when_idle", "from": reply.address, **({"session_id": session_id} if session_id else {})})

    post(sock_path, frames)
    sent = [f for f in frames if f.get("type") != "auth"]
    print("sent %d frame(s) to %s" % (len(sent), sock_path), file=sys.stderr)
    for f in sent:
        print("  %s %s msg_id=%s" % (f["type"], f.get("action", f.get("priority", "")), f["msg_id"]), file=sys.stderr)

    rc = 0
    if reply:
        wait = args.receipts if args.receipts is not None else 600
        for frame in reply.frames(wait):
            print(json.dumps(frame))
            if frame.get("action") == "peer_message_status" and frame.get("status") in ("held", "expired", "denied", "dropped", "refused"):
                rc = 3
            if frame.get("action") == "peer_idle_notice" and frame.get("orig_msg_id") == notify_id:
                break
        reply.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
