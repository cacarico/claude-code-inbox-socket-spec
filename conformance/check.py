#!/usr/bin/env python3
"""Conformance check for Claude Code's session inbox socket (see SPEC.md).

Starts throwaway `claude -p` sessions in a scratch directory, posts frames to their
inbox sockets and checks what the receiver does. Prints `claude --version`, then one
PASS, FAIL or SKIP line per rule, and exits 1 when any rule failed. Run it after a
Claude Code upgrade to see which statements in SPEC.md still hold.

    python3 conformance/check.py                 # everything (about 3 minutes, a few cents of model use)
    python3 conformance/check.py --quick         # skip the 30 s silent-connection rule and the extra sessions
    python3 conformance/check.py --model sonnet  # another model for the throwaway sessions

The sessions run under your normal Claude Code login, in a fresh temporary directory
under this folder's .tmp/, with no tools allowed. Do not point it at a session you
care about: it renames nothing, but it does post prompts.

Why some rules read the session's output: an accepted message produces no receipt,
so delivery is checked through the session itself, in its stream-json output and in
its transcript file. The receipt path is checked with the cases that do produce one
(duplicate and rate-limited drops, hold, refuse) and with the idle notice.

The rules that load the session (the rate burst, the duplicate window) run last,
each on a reply socket of its own: the `from` address is the sender key for the
rate bucket and the duplicate window, so a fresh socket is a fresh bucket.
"""
import argparse
import json
import os
import pathlib
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or (pathlib.Path.home() / ".claude"))
SESS_DIR = CONFIG / "sessions"
RESULTS = []
VERBOSE = False


def log(*a):
    if VERBOSE:
        print("   ", *a, file=sys.stderr, flush=True)


def report(rule, status, detail=""):
    RESULTS.append((rule, status, detail))
    print("%-4s %s%s" % (status, rule, ("  -- " + detail) if detail else ""), flush=True)


def uds_address(path):
    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:_/.\\-")
    return "uds:" + "".join(ch if ch in safe else "".join("%%%02X" % b for b in ch.encode()) for ch in path)


def clean_env():
    return {k: v for k, v in os.environ.items() if not (k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE")}


class Reply:
    """Our reply socket, beside the target's, for receipts and notices.

    `suffix` names a second socket of this process, `<pid>-<8 hex>.sock`, one of the
    forms the receiver sends receipts to; its address is a distinct sender key."""

    def __init__(self, sock_dir, suffix=""):
        self.path = os.path.join(sock_dir, "%d%s.sock" % (os.getpid(), suffix))
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        os.chmod(self.path, 0o600)
        self.srv.listen(16)
        self.address = uds_address(self.path)
        self.frames = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
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
                try:
                    f = json.loads(line)
                except ValueError:
                    f = {"raw": line}
                log("receipt:", json.dumps(f)[:300])
                self.frames.append(f)

    def wait(self, pred, timeout):
        end = time.time() + timeout
        while time.time() < end:
            for f in self.frames:
                if pred(f):
                    return f
            time.sleep(0.2)
        return None

    def close(self):
        self.srv.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


class Session:
    """A throwaway `claude -p` session kept open on stream-json stdin."""

    def __init__(self, cwd, model, extra=(), label="S"):
        self.label = label
        args = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--model", model, "--max-turns", "3", "--disallowedTools", "*"] + list(extra)
        self.p = subprocess.Popen(args, cwd=cwd, env=clean_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1)
        self.texts = []
        self.err = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()
        self.prompt("Reply with exactly the word READY and nothing else.")
        self.rec = None
        end = time.time() + 90
        while time.time() < end and self.rec is None:
            f = SESS_DIR / ("%d.json" % self.p.pid)
            if f.exists():
                try:
                    self.rec = json.loads(f.read_text())
                except (OSError, ValueError):
                    pass
            time.sleep(0.3)
        if not self.rec:
            raise RuntimeError("%s: no registry record appeared for pid %d; stderr: %s" % (label, self.p.pid, " ".join(self.err[-3:])))
        self.sock = self.rec.get("messagingSocketPath")
        self.sid = self.rec.get("sessionId")
        if self.wait_text("READY", 90) is None:
            raise RuntimeError("%s: the session did not answer its first prompt; stderr: %s" % (label, " ".join(self.err[-3:])))

    def prompt(self, text):
        self.p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
        self.p.stdin.flush()

    def _pump(self):
        for line in self.p.stdout:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "assistant":
                for b in o.get("message", {}).get("content", []):
                    if b.get("type") == "text":
                        self.texts.append(b["text"])
                        log(self.label, "assistant:", b["text"][:120])

    def _pump_err(self):
        for line in self.p.stderr:
            self.err.append(line.rstrip())

    def record(self):
        return json.loads((SESS_DIR / ("%d.json" % self.p.pid)).read_text())

    def wait_text(self, marker, timeout):
        end = time.time() + timeout
        while time.time() < end:
            for t in self.texts:
                if marker in t:
                    return t
            time.sleep(0.2)
        return None

    def raw(self, payload, read=True):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock)
        err = None
        try:
            s.sendall(payload)
        except OSError as e:
            err = e
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        back = b""
        if read:
            s.settimeout(3)
            try:
                while True:
                    ch = s.recv(65536)
                    if not ch:
                        break
                    back += ch
            except socket.timeout:
                back += b"<timeout>"
            except OSError as e:
                err = err or e
        s.close()
        return back, err

    def send(self, frames):
        return self.raw("".join(json.dumps(f) + "\n" for f in frames).encode())

    def msg(self, text, reply=None, **kw):
        f = {"msgV": 1, "msg_id": str(uuid.uuid4()), "type": "user",
             "message": {"role": "user", "content": text}, "priority": "next", "session_id": self.sid}
        if reply:
            f["from"] = reply.address
        f.update(kw)
        return f

    def transcript_has(self, marker, timeout=20, msg_id=None):
        """The session's transcript holds a user line with the marker, with a peer origin.
        With `msg_id`, the line must be the one for that frame (origin.msg_id)."""
        end = time.time() + timeout
        while time.time() < end:
            for f in (CONFIG / "projects").glob("*/%s.jsonl" % self.sid):
                try:
                    for line in f.read_text().splitlines():
                        if marker not in line:
                            continue
                        o = json.loads(line)
                        origin = o.get("origin") or {}
                        if o.get("type") == "user" and origin.get("kind") == "peer" and (msg_id is None or origin.get("msg_id") == msg_id):
                            return o
                except (OSError, ValueError):
                    pass
            time.sleep(0.5)
        return None

    def stop(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        time.sleep(0.5)
        self.p.terminate()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--quick", action="store_true", help="skip the 30 s silent-connection rule and the hold/refuse/own-child sessions")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    VERBOSE = args.verbose

    version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    print("claude:   ", version or "<not found>")
    print("platform: ", platform.platform())
    print("date:     ", time.strftime("%Y-%m-%d"))
    print("config:   ", CONFIG)
    if platform.system() == "Windows":
        sys.exit("check.py speaks Unix sockets only; the Windows named pipe is not covered")

    tmp_root = ROOT / ".tmp"
    tmp_root.mkdir(exist_ok=True)
    cwd = tempfile.mkdtemp(prefix="conformance-", dir=tmp_root)
    sessions = []
    replies = []
    try:
        try:
            S = Session(cwd, args.model, label="main")
        except RuntimeError as e:
            report("R00 a throwaway `claude -p` session starts", "FAIL", str(e))
            return 1
        sessions.append(S)
        reply = Reply(os.path.dirname(S.sock))
        replies.append(reply)

        # R01 discovery
        ok = bool(S.sock) and os.path.exists(S.sock) and (SESS_DIR / ("%d.json" % S.p.pid)).exists()
        keys = list(SESS_DIR.glob("%d.*.key" % S.p.pid))
        report("R01 registry record <config>/sessions/<pid>.json names a live socket; key file beside it", "PASS" if ok and keys else "FAIL",
               "socket=%s key=%s" % (S.sock, keys[0].name if keys else "none"))
        st = os.stat(S.sock)
        report("R01b socket mode 0600 in a 0700 directory", "PASS" if (st.st_mode & 0o777) == 0o600 and (os.stat(os.path.dirname(S.sock)).st_mode & 0o777) == 0o700 else "FAIL",
               "%o / %o" % (st.st_mode & 0o777, os.stat(os.path.dirname(S.sock)).st_mode & 0o777))

        # R02 minimal frame, delivered, shown
        S.send([{"type": "user", "message": {"content": "C02 Reply exactly: ACK C02"}}])
        got = S.wait_text("ACK C02", 45)
        shown = S.transcript_has("C02 Reply exactly") if got else None
        report("R02 minimal frame {type:user, message.content} starts a turn in an idle session", "PASS" if got else "FAIL")
        report("R02b the session's transcript records it as a user line with origin.kind=peer", "PASS" if shown else "FAIL",
               ("from=%s" % (shown.get("origin") or {}).get("from")) if shown else "")

        # R03 accepted message: no receipt
        m = S.msg("C03 Reply exactly: ACK C03", reply)
        S.send([m])
        got = S.wait_text("ACK C03", 45)
        r = reply.wait(lambda f: f.get("orig_msg_id") == m["msg_id"], 6)
        report("R03 a frame with `from` that is accepted gets no receipt", "PASS" if got and r is None else "FAIL", json.dumps(r) if r else "")

        # R04 session_id mismatch
        S.send([S.msg("C04 Reply exactly: ACK C04", reply, session_id=str(uuid.uuid4()))])
        report("R04 session_id that is not the receiver's: dropped silently", "PASS" if S.wait_text("ACK C04", 10) is None else "FAIL")

        # R05 bad content
        S.send([{"type": "user", "message": {"content": ""}}, {"type": "user", "message": {"content": ["C05 Reply exactly: ACK C05"]}}, {"type": "user"}])
        report("R05 empty, non-string or missing message.content: ignored", "PASS" if S.wait_text("ACK C05", 8) is None else "FAIL")

        # R06 junk lines then a valid one
        S.raw(b'not json\n\n{"no":"type"}\n{"type":7}\n[1]\n' + json.dumps({"type": "user", "message": {"content": "C06 Reply exactly: ACK C06"}}).encode())
        report("R06 unparseable or typeless lines skipped; a later valid line on the same connection delivered, even without a final newline", "PASS" if S.wait_text("ACK C06", 45) else "FAIL")

        # R07 duplicate -> dropped receipt
        m1 = S.msg("C07 Reply exactly: ACK C07", reply)
        m2 = S.msg("C07 Reply exactly: ACK C07", reply)
        S.send([m1])
        S.wait_text("ACK C07", 45)
        time.sleep(1)
        S.send([m2])
        r = reply.wait(lambda f: f.get("action") == "peer_message_status" and f.get("status") == "dropped", 20)
        report("R07 identical repeat from the same sender within the window: `dropped` receipt, drop_reason=duplicate", "PASS" if r and r.get("drop_reason") == "duplicate" else "FAIL", json.dumps(r) if r else "no receipt")
        report("R07b receipt orig_msg_id names the dropped frame", "PASS" if r and r.get("orig_msg_id") == m2["msg_id"] else "FAIL")
        time.sleep(2)
        report("R07c the first copy was delivered once", "PASS" if sum("ACK C07" in t for t in S.texts) == 1 else "FAIL", "%d" % sum("ACK C07" in t for t in S.texts))

        # R08 wrong auth token tolerated (not Windows)
        S.send([{"type": "auth", "token": "0" * 32}, {"type": "user", "message": {"content": "C08 Reply exactly: ACK C08"}}])
        report("R08 a wrong auth token does not close the connection on macOS/Linux; the message is still delivered", "PASS" if S.wait_text("ACK C08", 45) else "FAIL")

        # R09 line cap
        back, err = S.raw(b"a" * (1024 * 1024 + 16))
        dropped = (back == b"") or err is not None
        S.send([S.msg("C09 Reply exactly: ACK C09", reply)])
        alive = S.wait_text("ACK C09", 45) is not None
        report("R09 more than 1 MiB without a newline: connection dropped, session unharmed", "PASS" if dropped and alive else "FAIL", "closed=%s alive=%s" % (dropped, alive))

        # R10 notify_when_idle
        nid = str(uuid.uuid4())
        S.send([{"msgV": 1, "msg_id": nid, "type": "control", "action": "notify_when_idle", "from": reply.address, "session_id": S.sid}])
        r = reply.wait(lambda f: f.get("action") == "peer_idle_notice" and f.get("orig_msg_id") == nid, 20)
        report("R10 notify_when_idle on an idle session: peer_idle_notice with state=idle", "PASS" if r and r.get("state") == "idle" else "FAIL", json.dumps(r) if r else "no notice")

        # R11 unknown control action and type are ignored, session still works
        S.send([{"type": "control", "action": "no_such_action", "session_id": S.sid}, {"type": "no_such_type"}, S.msg("C11 Reply exactly: ACK C11", reply)])
        report("R11 unknown control action or frame type: ignored", "PASS" if S.wait_text("ACK C11", 45) else "FAIL")

        # R12 rename has no effect on a -p session (interactive sessions do rename)
        S.send([{"msgV": 1, "msg_id": str(uuid.uuid4()), "type": "control", "action": "rename", "name": "conformance-renamed", "session_id": S.sid}])
        time.sleep(4)
        name = S.record().get("name")
        report("R12 rename control on a -p session leaves the registry name alone (interactive sessions rename)", "PASS" if name != "conformance-renamed" else "FAIL", "name=%s" % name)

        # R13 silent connection deadline
        if args.quick:
            report("R13 a connection with no complete line is closed after 30 s", "SKIP", "--quick")
        else:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(S.sock)
            s.settimeout(45)
            t0 = time.time()
            try:
                closed = s.recv(1) == b""
            except socket.timeout:
                closed = False
            dt = time.time() - t0
            s.close()
            report("R13 a connection with no complete line is closed after 30 s", "PASS" if closed and 25 <= dt <= 40 else "FAIL", "%.1f s" % dt)

        # The rules below load the session (R17 queues 30 prompts), so they come last. Each runs on a
        # fresh reply socket: its `from` address is a sender key the rules above never used, so the rate bucket is full and the duplicate window empty when it starts.
        def fresh_reply():
            r = Reply(os.path.dirname(S.sock), "-%08x" % (uuid.uuid4().int & 0xFFFFFFFF))
            replies.append(r)
            return r

        def drops(rp):
            """msg_id -> drop_reason over every `dropped` receipt on a reply socket, coalesced ids included."""
            out = {}
            for f in list(rp.frames):
                if f.get("action") == "peer_message_status" and f.get("status") == "dropped":
                    for mid in ([f.get("orig_msg_id")] if f.get("orig_msg_id") else []) + (f.get("dropped_msg_ids") or []):
                        out[mid] = f.get("drop_reason")
            return out

        def dropped_as(rp, mid, timeout):
            end = time.time() + timeout
            while True:
                d = drops(rp)
                if mid in d or time.time() >= end:
                    return d.get(mid)
                time.sleep(0.2)

        # R18 duplicate window: an identical body is dropped inside the window and admitted again past 30 s.
        if args.quick:
            report("R18 an identical repeat within 30 s is dropped; the same body after the window is admitted", "SKIP", "--quick")
        else:
            rp18 = fresh_reply()
            body = "C18 dup window %s. Reply exactly: ACK C18" % uuid.uuid4().hex[:6]
            a = S.msg(body, rp18); S.send([a]); t_first = time.time()
            time.sleep(5)
            b = S.msg(body, rp18); S.send([b])
            inside = dropped_as(rp18, b["msg_id"], 12)
            first_ok = a["msg_id"] not in drops(rp18)
            while time.time() - t_first < 31:
                time.sleep(0.5)
            c = S.msg(body, rp18); S.send([c])
            after = dropped_as(rp18, c["msg_id"], 8)
            shown = S.transcript_has(body, 60, msg_id=c["msg_id"]) if after is None else None
            report("R18 an identical repeat within 30 s is dropped; the same body after the window is admitted",
                   "PASS" if first_ok and inside == "duplicate" and after is None and shown else "FAIL",
                   "first=%s within=%s after_window=%s" % ("admitted" if first_ok else "dropped", inside, "delivered" if shown else (after or "no transcript line")))

        # R17 rate bucket: a burst of distinct bodies from one sender is admitted up to 30, the 31st is rate-limited.
        # The guard runs at admission, so the receipts come back before the model reads the queue; later drops
        # may be coalesced into one receipt over a 5 s trail, hence the wait after the first.
        rp17 = fresh_reply()
        rl_ids = []
        for i in range(34):
            m = S.msg("C17 rate %s %02d. Reply exactly: OK" % (uuid.uuid4().hex[:4], i), rp17)
            rl_ids.append(m["msg_id"])
            S.send([m])
        end = time.time() + 10
        while time.time() < end and "rate-limited" not in drops(rp17).values():
            time.sleep(0.2)
        time.sleep(6)
        d = drops(rp17)
        first_rl = next((i for i, mid in enumerate(rl_ids) if d.get(mid) == "rate-limited"), None)
        early = sum(mid in d for mid in rl_ids[:30])
        report("R17 a burst from one sender is admitted up to a bucket of 30; the 31st is dropped with drop_reason=rate-limited",
               "PASS" if first_rl == 30 and early == 0 else "FAIL", "first rate-limited at index %s; dropped among the first 30: %d" % (first_rl, early))

        S.stop()

        if args.quick:
            for rule in ("R14 crossSessionInbound=hold: `held` receipt, nothing delivered",
                         "R15 crossSessionInbound=refuse: `expired` receipt with status_detail=refused",
                         "R16 own child (SessionStart hook) posting with the token is delivered",
                         "R16b own child posting without the token is delivered (process evidence)"):
                report(rule, "SKIP", "--quick")
        else:
            H = Session(cwd, args.model, ["--settings", json.dumps({"crossSessionInbound": "hold"})], "hold")
            sessions.append(H)
            m = H.msg("C14 Reply exactly: ACK C14", reply)
            H.send([m])
            r = reply.wait(lambda f: f.get("orig_msg_id") == m["msg_id"], 20)
            delivered = H.wait_text("ACK C14", 8)
            report("R14 crossSessionInbound=hold: `held` receipt, nothing delivered", "PASS" if r and r.get("status") == "held" and not delivered else "FAIL", json.dumps(r) if r else "no receipt")
            H.stop()
            r2 = reply.wait(lambda f: f.get("orig_msg_id") == m["msg_id"] and f.get("status") == "expired", 15)
            report("R14b a held message is settled as `expired` when the session ends", "PASS" if r2 else "FAIL")

            R = Session(cwd, args.model, ["--settings", json.dumps({"crossSessionInbound": "refuse"})], "refuse")
            sessions.append(R)
            m = R.msg("C15 Reply exactly: ACK C15", reply)
            R.send([m])
            r = reply.wait(lambda f: f.get("orig_msg_id") == m["msg_id"], 20)
            report("R15 crossSessionInbound=refuse: `expired` receipt with status_detail=refused", "PASS" if r and r.get("status") == "expired" and r.get("status_detail") == "refused" else "FAIL", json.dumps(r) if r else "no receipt")
            R.stop()

            py = shutil.which("python3") or sys.executable
            poster = ("import os,socket,json;s=socket.socket(socket.AF_UNIX);s.connect(os.environ['CLAUDE_CODE_MESSAGING_SOCKET']);"
                      "s.sendall((json.dumps({'type':'auth','token':os.environ['CLAUDE_CODE_MESSAGING_TOKEN']})+'\\n'+json.dumps({'type':'user','message':{'content':'C16 Reply exactly: ACK C16'}})+'\\n').encode());s.shutdown(1);s.close();"
                      "s=socket.socket(socket.AF_UNIX);s.connect(os.environ['CLAUDE_CODE_MESSAGING_SOCKET']);"
                      "s.sendall((json.dumps({'type':'user','message':{'content':'C17 Reply exactly: ACK C17'}})+'\\n').encode());s.shutdown(1);s.close()")
            hook = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "%s -c \"%s\"" % (py, poster.replace('"', '\\"'))}]}]}}
            O = Session(cwd, args.model, ["--settings", json.dumps(hook)], "ownchild")
            sessions.append(O)
            report("R16 own child (SessionStart hook) posting with the token is delivered", "PASS" if O.wait_text("ACK C16", 60) else "FAIL")
            report("R16b own child posting without the token is delivered (process evidence)", "PASS" if O.wait_text("ACK C17", 60) else "FAIL")
            O.stop()
    finally:
        for s in sessions:
            try:
                s.stop()
            except Exception:
                pass
        for r in replies:
            r.close()
        shutil.rmtree(cwd, ignore_errors=True)

    fails = [r for r in RESULTS if r[1] == "FAIL"]
    print("\n%d rules: %d pass, %d fail, %d skipped  (claude %s)" % (len(RESULTS), sum(r[1] == "PASS" for r in RESULTS), len(fails), sum(r[1] == "SKIP" for r in RESULTS), version))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
