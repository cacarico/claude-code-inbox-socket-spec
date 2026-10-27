#!/usr/bin/env python3
"""Block on something, then post a nudge to your own Claude Code session.

Run it from a Bash tool call or a hook inside the session you want to wake. It
reads CLAUDE_CODE_MESSAGING_SOCKET and CLAUDE_CODE_MESSAGING_TOKEN from the
environment, which Claude Code exports to its hooks and Bash commands.

    waiter.py -- make test                      # wait for a command, report its exit
    waiter.py --until-file build/done.flag      # wait for a file to appear
    waiter.py --sleep 300 --message "five minutes passed"
    waiter.py --detach -- ./deploy.sh           # return at once; a daemon waits and posts

Why it works: a message posted to an idle session starts a new turn; one posted to
a busy session is read between tool calls. A detached waiter is still verified as
the session's own child because it sends the token in the auth line, so the
session delivers it even where no crossSessionInbound value applies.

Not a public interface of Claude Code; see the pages under spec/.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time


def post(sock_path, token, text, priority="next", from_name=""):
    if from_name:
        # The envelope gives the message a sender name. An interactive session then
        # shows it as a one-line `Message from @<name>` preview instead of the full
        # wrapper text it shows for a bare frame.
        name = from_name.replace('"', "").replace("<", "").replace(">", "")
        text = '<cross-session-message from-name="%s">\n%s\n</cross-session-message>' % (name, text)
    frames = []
    if token:
        frames.append({"type": "auth", "token": token})
    frames.append({"type": "user", "message": {"role": "user", "content": text}, "priority": priority})
    payload = "".join(json.dumps(f, ensure_ascii=False) + "\n" for f in frames).encode("utf-8")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    s.connect(sock_path)
    s.sendall(payload)
    s.shutdown(socket.SHUT_WR)
    try:
        while s.recv(4096):
            pass
    except OSError:
        pass
    s.close()


def detach():
    """Double fork and setsid: the Bash tool call returns, the waiter lives on."""
    if os.fork() > 0:
        return False
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="*", help="command to wait for (after --)")
    ap.add_argument("--until-file", metavar="PATH")
    ap.add_argument("--sleep", type=float, metavar="SECONDS")
    ap.add_argument("--message", help="text to post (default: a line about what finished)")
    ap.add_argument("--priority", choices=["now", "next", "later"], default="next",
                    help="keep next; now also closes an open permission prompt as if the user said no")
    ap.add_argument("--from-name", default="waiter", help="sender name shown by the session (default: waiter); empty sends a bare frame")
    ap.add_argument("--detach", action="store_true")
    ap.add_argument("--socket", default=os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET"))
    ap.add_argument("--token", default=os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN"))
    ap.add_argument("--timeout", type=float, default=12 * 3600, help="with --until-file, give up after this many seconds")
    args = ap.parse_args()
    if not args.socket:
        sys.exit("waiter.py: CLAUDE_CODE_MESSAGING_SOCKET is not set; run this under a Claude Code session or pass --socket")
    if not (args.command or args.until_file or args.sleep is not None):
        ap.error("give a command after --, --until-file or --sleep")

    if args.detach:
        if not detach():
            print("waiter detached; it will post to %s" % args.socket)
            return 0

    start = time.time()
    if args.command:
        rc = subprocess.call(args.command)
        what = "`%s` exited %d after %ds" % (" ".join(args.command), rc, time.time() - start)
    elif args.until_file:
        while not os.path.exists(args.until_file):
            if time.time() - start > args.timeout:
                what = "waiter gave up: %s did not appear within %ds" % (args.until_file, args.timeout)
                break
            time.sleep(1)
        else:
            what = "%s appeared after %ds" % (args.until_file, time.time() - start)
    else:
        time.sleep(args.sleep)
        what = "%ds passed" % args.sleep

    text = args.message or (what if args.from_name else "waiter: " + what)
    if not os.path.exists(args.socket):
        # The session is gone; nothing to wake.
        return 1
    try:
        post(args.socket, args.token, text, args.priority, args.from_name)
    except OSError as e:
        print("waiter.py: cannot post to %s: %s" % (args.socket, e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
