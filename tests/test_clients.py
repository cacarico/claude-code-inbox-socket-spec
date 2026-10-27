#!/usr/bin/env python3
"""Run the example clients against a fake receiver. No Claude Code, no model.

The fake receiver is a Unix socket that records what each connection wrote and
whether the client half-closed. These tests check the client side of SPEC.md:
one JSON object per line, the half-close, the auth line only on the session's
own socket, `session_id` and `from`, the envelope, and send.py's handling of
receipts. send.mjs runs the same cases as send.py when node is on PATH. What a
real session does with the frames is conformance/check.py's job.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TOKEN = "0123456789abcdef0123456789abcdef"


def scratch_dir():
    """A private directory for the sockets. AF_UNIX paths are limited to 103 bytes
    on macOS; the longest one here is the directory plus 24, so the repo's .tmp/ is
    used when that fits and the system temp directory otherwise."""
    base = ROOT / ".tmp"
    if len(str(base)) <= 79:
        base.mkdir(exist_ok=True)
        d = tempfile.mkdtemp(prefix="t-", dir=base)
    else:
        d = tempfile.mkdtemp(prefix="cc-inbox-test-")
    os.chmod(d, 0o700)
    return d


class FakeReceiver:
    """Accepts connections, reads each to EOF, keeps the bytes. Never writes back,
    like the real receiver. A connection that does not half-close within 5 s is
    recorded with half_closed False."""

    def __init__(self, path, receipt=None):
        self.path = path
        self.receipt = receipt  # a function frame -> list of receipt frames to send to its `from`
        self.conns = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            c.settimeout(5)
            data, half_closed = b"", False
            try:
                while True:
                    chunk = c.recv(65536)
                    if not chunk:
                        half_closed = True
                        break
                    data += chunk
            except socket.timeout:
                pass
            c.close()
            self.conns.append({"data": data, "half_closed": half_closed})
            if self.receipt:
                for line in data.decode().splitlines():
                    frame = json.loads(line)
                    for r in self.receipt(frame):
                        self._send_receipt(frame["from"], r)

    @staticmethod
    def _send_receipt(address, frame):
        assert address.startswith("uds:")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(address[4:])
        s.sendall((json.dumps(frame) + "\n").encode())
        s.close()

    def wait(self, n=1, timeout=10):
        end = time.time() + timeout
        while len(self.conns) < n and time.time() < end:
            time.sleep(0.05)
        return self.conns

    def frames(self, i=0):
        data = self.conns[i]["data"]
        assert data.endswith(b"\n"), data
        return [json.loads(line) for line in data.decode().split("\n")[:-1]]

    def close(self):
        # shutdown wakes the accept() in the other thread; close alone leaves the
        # socket listening until it returns.
        try:
            self.srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.srv.close()


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
    env.update(extra)
    return env


class ReceiverCase(unittest.TestCase):
    """A fake receiver at <scratch>/4242.sock for each test."""

    def setUp(self):
        self.dir = scratch_dir()
        self.sock = os.path.join(self.dir, "4242.sock")
        self.rx = FakeReceiver(self.sock)

    def tearDown(self):
        self.rx.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_py(self, script, *args, env=None, timeout=20):
        return subprocess.run([sys.executable, str(EXAMPLES / script), *args], env=env or clean_env(),
                              capture_output=True, text=True, timeout=timeout)

    def own_env(self, **extra):
        return clean_env(CLAUDE_CODE_MESSAGING_SOCKET=self.sock, CLAUDE_CODE_MESSAGING_TOKEN=TOKEN, **extra)


class SendPyTests(ReceiverCase):
    """send.py against the fake receiver. SendMjsTests runs the same cases on send.mjs."""

    def send(self, *args, env=None):
        return self.run_py("send.py", *args, env=env)

    def test_frame(self):
        r = self.send("--socket", self.sock, "--priority", "later", "hello", "there")
        self.assertEqual(r.returncode, 0, r.stderr)
        conn = self.rx.wait()[0]
        self.assertTrue(conn["half_closed"], "the client did not half-close")
        [f] = self.rx.frames()
        self.assertEqual(f["type"], "user")
        self.assertEqual(f["message"]["content"], "hello there")
        self.assertEqual(f["priority"], "later")
        self.assertEqual(f["msgV"], 1)
        uuid.UUID(f["msg_id"])
        self.assertNotIn("from", f)

    def test_own_session_sends_auth_and_session_id(self):
        r = self.send("hi", env=self.own_env(CLAUDE_CODE_SESSION_ID="sid-1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.rx.wait()
        auth, msg = self.rx.frames()
        self.assertEqual(auth, {"type": "auth", "token": TOKEN})
        self.assertEqual(msg["session_id"], "sid-1")

    def test_keeps_token_off_other_sockets(self):
        other = os.path.join(self.dir, "99.sock")
        r = self.send("--socket", self.sock, "hi",
                      env=clean_env(CLAUDE_CODE_MESSAGING_SOCKET=other, CLAUDE_CODE_MESSAGING_TOKEN=TOKEN))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.rx.wait()
        self.assertEqual([f["type"] for f in self.rx.frames()], ["user"])

    def test_envelope(self):
        r = self.send("--socket", self.sock, "--from-name", 'c"i<>', "green")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.rx.wait()
        [f] = self.rx.frames()
        self.assertEqual(f["message"]["content"], '<cross-session-message from-name="ci">\ngreen\n</cross-session-message>')

    def test_registry_lookup(self):
        cfg = os.path.join(self.dir, "cfg")
        os.makedirs(os.path.join(cfg, "sessions"))
        rec = {"pid": 4242, "sessionId": "sid-2", "name": "api-worker", "messagingSocketPath": self.sock}
        pathlib.Path(cfg, "sessions", "4242.json").write_text(json.dumps(rec))
        stale = dict(rec, pid=4343, name="gone", messagingSocketPath=os.path.join(self.dir, "4343.sock"))
        pathlib.Path(cfg, "sessions", "4343.json").write_text(json.dumps(stale))
        env = clean_env(CLAUDE_CONFIG_DIR=cfg)
        listing = self.send("--list", env=env).stdout
        self.assertRegex(listing, r"live\s+pid=4242\s+api-worker")
        self.assertRegex(listing, r"stale\s+pid=4343")
        r = self.send("--name", "api-worker", "hi", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        n = len(self.rx.wait(2))  # --list probed the socket once
        self.assertEqual(json.loads(self.rx.conns[n - 1]["data"])["session_id"], "sid-2")
        r = self.send("--name", "gone", "hi", env=env)
        self.assertNotEqual(r.returncode, 0)

    def test_receipts(self):
        """A held receipt on the reply socket gives exit 3; held then delivered gives 0."""
        for receipts, rc in (([{"status": "held"}], 3), ([{"status": "held"}, {"status": "delivered"}], 0)):
            with self.subTest(receipts=receipts):
                self.rx.close()
                os.unlink(self.sock)

                def answer(frame, receipts=receipts):
                    if frame.get("type") != "user":
                        return []
                    return [{"type": "control", "action": "peer_message_status", "orig_msg_id": frame["msg_id"], **r}
                            for r in receipts]

                self.rx = FakeReceiver(self.sock, receipt=answer)
                r = self.send("--socket", self.sock, "--receipts", "2", "hi")
                self.assertEqual(r.returncode, rc, r.stderr + r.stdout)
                [f] = self.rx.frames()
                self.assertTrue(f["from"].startswith("uds:" + self.dir + "/"), f["from"])
                self.assertFalse(os.path.exists(f["from"][4:]), "the client left its reply socket behind")
                self.assertEqual([json.loads(l)["status"] for l in r.stdout.splitlines()], [x["status"] for x in receipts])

    def test_dead_socket(self):
        self.rx.close()
        r = self.send("--socket", self.sock, "hi")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot post", r.stderr)


@unittest.skipUnless(shutil.which("node"), "send.mjs needs node")
class SendMjsTests(SendPyTests):
    def send(self, *args, env=None):
        return subprocess.run(["node", str(EXAMPLES / "send.mjs"), *args], env=env or clean_env(),
                              capture_output=True, text=True, timeout=20)


class ClientTests(ReceiverCase):
    # waiter.py

    def test_waiter_posts_after_command(self):
        r = self.run_py("waiter.py", "--", sys.executable, "-c", "raise SystemExit(4)", env=self.own_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.rx.wait()[0]["half_closed"])
        auth, msg = self.rx.frames()
        self.assertEqual(auth["token"], TOKEN)
        content = msg["message"]["content"]
        self.assertTrue(content.startswith('<cross-session-message from-name="waiter">\n'), content)
        self.assertIn("exited 4 after", content)

    def test_waiter_bare_frame(self):
        r = self.run_py("waiter.py", "--sleep", "0", "--from-name", "", "--message", "done", env=self.own_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.rx.wait()
        self.assertEqual(self.rx.frames()[1]["message"]["content"], "done")

    # send.sh

    def test_send_sh(self):
        has_nc = shutil.which("nc") and "-U" in subprocess.run(["nc", "-h"], capture_output=True, text=True).stderr
        if not (has_nc or shutil.which("socat")):
            self.skipTest("send.sh needs OpenBSD nc or socat")
        env = self.own_env()
        r = subprocess.run(["sh", str(EXAMPLES / "send.sh"), "-p", "now", 'a "q"\tb\\c\r\nline 2'],
                           env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.rx.wait()[0]["half_closed"])
        auth, msg = self.rx.frames()
        self.assertEqual(auth["token"], TOKEN)
        self.assertEqual(msg["message"]["content"], 'a "q"\tb\\c\nline 2')
        self.assertEqual(msg["priority"], "now")
        # -s to another socket: no token
        other = os.path.join(self.dir, "77.sock")
        rx2 = FakeReceiver(other)
        try:
            r = subprocess.run(["sh", str(EXAMPLES / "send.sh"), "-s", other, "hi"], env=env,
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stderr)
            rx2.wait()
            self.assertEqual([f["type"] for f in rx2.frames()], ["user"])
        finally:
            rx2.close()


if __name__ == "__main__":
    unittest.main()
