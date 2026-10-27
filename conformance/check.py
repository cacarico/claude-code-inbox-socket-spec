#!/usr/bin/env python3
"""Conformance check for Claude Code's session inbox socket (see the pages under spec/).

Starts throwaway `claude -p` sessions in a scratch directory, posts frames to their
inbox sockets and checks what the receiver does. Prints `claude --version`, then one
PASS, FAIL or SKIP line per rule, and exits 1 when any rule failed. Run it after a
Claude Code upgrade to see which statements in the pages under spec/ still hold.

    python3 conformance/check.py                 # everything (about 7 minutes, a few cents of model use)
    python3 conformance/check.py --quick         # skip the rules that wait 30 s (R13, R18, R27) and the extra sessions
    python3 conformance/check.py --model sonnet  # another model for the throwaway sessions
    python3 conformance/check.py --log           # also write conformance/runs/<version>-<os>.txt
    python3 conformance/check.py --bypass        # also run R20 against a session in bypassPermissions mode

The sessions run under your normal Claude Code login, in a fresh temporary directory
under this folder's .tmp/, with no tools allowed. Do not point it at a session you
care about: it renames nothing, but it does post prompts.

Why some rules read the session's output: an accepted message produces no receipt,
so delivery is checked through the session itself, in its stream-json output and in
its transcript file. The receipt path is checked with the cases that do produce one
(duplicate and rate-limited drops, hold, refuse) and with the idle notice.

The rules that load the session (the rate burst, the duplicate window, the envelope
hold) run last, each on a reply socket of its own: the `from` address is the sender
key for the rate bucket and the duplicate window, so a fresh socket is a fresh bucket.
"""
import argparse
import calendar
import hashlib
import json
import os
import pathlib
import platform
import re
import select
import shlex
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
    print(result_line(rule, status, detail), flush=True)


def result_line(rule, status, detail):
    return "%-4s %s%s" % (status, rule, ("  -- " + detail) if detail else "")


def log_name(version, system):
    """The log's file name: `2.1.310-linux.txt` for `claude --version` 2.1.310 on Linux."""
    number = version.split()[0] if version.split() else "unknown"
    return "%s-%s.txt" % (number, {"Darwin": "macos"}.get(system, system.lower()))


def write_log(path, results, header):
    """Write the run's header and one line per rule to `path`.

    Logs are committed under conformance/runs/, so paths in the details are written
    without the machine's layout: this checkout as `.`, the home directory as `~`."""
    home = str(pathlib.Path.home())
    lines = ["%-9s %s" % (key + ":", value) for key, value in header.items()]
    lines.append("")
    lines += [result_line(rule, status, detail.replace(str(ROOT), ".").replace(home, "~")) for rule, status, detail in results]
    lines.append("")
    lines.append("%d rules: %d pass, %d fail, %d skipped" % (len(results), sum(r[1] == "PASS" for r in results),
                                                           sum(r[1] == "FAIL" for r in results), sum(r[1] == "SKIP" for r in results)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def uds_address(path):
    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:_/.\\-")
    return "uds:" + "".join(ch if ch in safe else "".join("%%%02X" % b for b in ch.encode()) for ch in path)


def envelope(attrs, text):
    """A cross-session envelope around `text`, with the attributes [(name, value), ...] in the order given."""
    return "<cross-session-message %s>\n%s\n</cross-session-message>" % (" ".join('%s="%s"' % a for a in attrs), text)


def clean_env():
    return {k: v for k, v in os.environ.items() if not (k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE")}


class Reply:
    """Our reply socket, beside the target's, for receipts and notices.

    `suffix` names a second socket of this process, `<pid>-<8 hex>.sock`, one of the
    forms the receiver sends receipts to; its address is a distinct sender key. `name`
    gives the file name outright."""

    def __init__(self, sock_dir, suffix="", name=None):
        self.path = os.path.join(sock_dir, name or "%d%s.sock" % (os.getpid(), suffix))
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        os.chmod(self.path, 0o600)
        self.srv.listen(16)
        self.address = uds_address(self.path)
        self.frames = []
        self.arrived = []  # when each frame came, by index
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
                self.arrived.append(time.time())
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

    def __init__(self, cwd, model, extra=(), label="S", debug_file=None):
        self.label = label
        self.debug_file = debug_file
        args = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--model", model, "--max-turns", "3", "--disallowedTools", "*"] + list(extra)
        if debug_file:
            args += ["--debug-file", debug_file]
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

    def debug_count(self, needle):
        """How many lines of the session's debug log contain `needle`."""
        try:
            return sum(needle in line for line in pathlib.Path(self.debug_file).read_text(errors="replace").splitlines())
        except OSError:
            return 0

    def debug_times(self, needle):
        """The times, in seconds since the epoch, of the debug log lines that contain `needle`."""
        out = []
        try:
            lines = pathlib.Path(self.debug_file).read_text(errors="replace").splitlines()
        except OSError:
            return out
        for line in lines:
            if needle in line:
                try:
                    out.append(calendar.timegm(time.strptime(line[:19], "%Y-%m-%dT%H:%M:%S")) + float("0" + line[19:23]))
                except ValueError:
                    pass
        return out

    def debug_wait(self, needle, count, timeout):
        """Wait until the debug log has `count` lines with `needle`; return how many it has."""
        end = time.time() + timeout
        while self.debug_count(needle) < count and time.time() < end:
            time.sleep(0.3)
        return self.debug_count(needle)

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

    def transcript_order(self, markers, timeout):
        """The markers in the order their peer user lines appear in the transcript, once all of them are
        there, or None. Messages read together may share a line; then their place in the line decides."""
        end = time.time() + timeout
        while time.time() < end:
            seen = {}
            for f in (CONFIG / "projects").glob("*/%s.jsonl" % self.sid):
                try:
                    lines = f.read_text().splitlines()
                except OSError:
                    continue
                for n, line in enumerate(lines):
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get("type") != "user" or (o.get("origin") or {}).get("kind") != "peer":
                        continue
                    for m in markers:
                        if m not in seen and m in line:
                            seen[m] = (n, line.index(m))
            if len(seen) == len(markers):
                return sorted(markers, key=seen.get)
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


def connects(path):
    """Whether a socket at `path` accepts a connection. A path too long for a client's
    socket address (Claude Code can bind one) is reached through its directory's fd."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    fd = None
    try:
        if len(path.encode()) > 100 and os.path.isdir("/proc/self/fd"):
            fd = os.open(os.path.dirname(path), os.O_RDONLY)
            path = "/proc/self/fd/%d/%s" % (fd, os.path.basename(path))
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()
        if fd is not None:
            os.close(fd)


def probe(cwd, model, env=None, extra=(), setup=None, timeout=25):
    """Start `claude -p` with no prompt and report the inbox it bound, with no model turn.

    The SDK's initialize request starts the session, and a SessionStart hook writes the
    socket path it was given, or `unset`, and whether the token variable is set (never
    its value). The process first waits for a go file, so `setup(pid)` can prepare what
    depends on the pid; the environment it returns is exported before Claude Code starts.
    `env` values of None unset a variable. Returns a dict: socket, token, exit, stderr."""
    tag = uuid.uuid4().hex[:8]
    out, go = os.path.join(cwd, "probe-%s.txt" % tag), os.path.join(cwd, "go-%s" % tag)
    hook = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command":
            "printf '%%s\\n%%s\\n' \"${CLAUDE_CODE_MESSAGING_SOCKET-unset}\" \"${CLAUDE_CODE_MESSAGING_TOKEN:+set}\" > %s" % shlex.quote(out)}]}]}}
    args = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--model", model, "--disallowedTools", "*", "--settings", json.dumps(hook)] + list(extra)
    e = clean_env()
    for k, v in (env or {}).items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    p = subprocess.Popen(["sh", "-c", 'while [ ! -e "$0" ]; do sleep 0.05; done; . "$0"; exec "$@"', go] + args,
                         cwd=cwd, env=e, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        exports = setup(p.pid) if setup else {}
        pathlib.Path(go).write_text("".join("export %s=%s\n" % (k, shlex.quote(v)) for k, v in exports.items()))
        p.stdin.write(json.dumps({"type": "control_request", "request_id": "probe", "request": {"subtype": "initialize"}}) + "\n")
        p.stdin.flush()
        end = time.time() + timeout
        lines = []
        while time.time() < end and p.poll() is None:
            try:
                lines = pathlib.Path(out).read_text().split("\n")
            except OSError:
                lines = []
            if len(lines) >= 3:
                break
            time.sleep(0.2)
        res = {"pid": p.pid, "socket": None if len(lines) < 3 or lines[0] == "unset" else lines[0],
               "hook": len(lines) >= 3, "token": len(lines) >= 3 and lines[1] == "set", "exit": p.poll()}
        if res["socket"]:
            res["live"] = connects(res["socket"])
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        if p.poll() is None:
            p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    res["stderr"] = p.stderr.read().strip()
    return res


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--quick", action="store_true", help="skip the rules that wait 30 s (R13, R18, R27) and the extra sessions")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--bypass", action="store_true",
                    help="run R20: start a throwaway session with --permission-mode bypassPermissions (no tools, empty directory)")
    ap.add_argument("--log", nargs="?", const="", metavar="FILE",
                    help="write the results to FILE, by default conformance/runs/<version>-<os>.txt")
    args = ap.parse_args()
    VERBOSE = args.verbose

    version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    header = {"claude": version or "<not found>", "platform": platform.platform(), "date": time.strftime("%Y-%m-%d"),
              "model": args.model, "mode": "quick" if args.quick else "full"}
    for key, value in header.items():
        print("%-9s" % (key + ":"), value)
    print("config:   ", CONFIG)
    if platform.system() == "Windows":
        sys.exit("check.py speaks Unix sockets only; the Windows named pipe is not covered")

    code = run(args, version)
    if args.log is not None:
        path = pathlib.Path(args.log) if args.log else ROOT / "conformance" / "runs" / log_name(version, platform.system())
        write_log(path, RESULTS, header)
        print("log:      ", path)
    return code


def run(args, version):
    tmp_root = ROOT / ".tmp"
    tmp_root.mkdir(exist_ok=True)
    cwd = tempfile.mkdtemp(prefix="conformance-", dir=tmp_root)
    # A second scratch directory with a short name, for sockets of our own and socket paths
    # that must stay short.
    for _ in range(256):
        short = str(tmp_root / ("s%02x" % (uuid.uuid4().int & 0xFF)))
        try:
            os.mkdir(short, 0o700)
            break
        except FileExistsError:
            continue
    sessions = []
    replies = []
    try:
        try:
            S = Session(cwd, args.model, label="main", debug_file=os.path.join(cwd, "debug-main.log"))
        except RuntimeError as e:
            report("R00 a throwaway `claude -p` session starts", "FAIL", str(e))
            return 1
        sessions.append(S)
        sock_dir = os.path.dirname(S.sock)
        reply = Reply(sock_dir)
        replies.append(reply)

        def fresh_reply():
            r = Reply(os.path.dirname(S.sock), "-%08x" % (uuid.uuid4().int & 0xFFFFFFFF))
            replies.append(r)
            return r

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

        # R28 message.role is not checked: a frame whose role is not "user" is still a user turn.
        S.send([{"type": "user", "message": {"role": "assistant", "content": "C28 Reply exactly: ACK C28"}}])
        shown = S.transcript_has("C28 Reply exactly", 45)
        report("R28 message.role is not checked: a frame with role \"assistant\" is delivered as a peer user turn", "PASS" if shown and S.wait_text("ACK C28", 30) else "FAIL")

        # R29 msgV: other values are not rejected.
        S.send([S.msg("C29A Reply exactly: ACK C29A", msgV=2)])
        two = S.wait_text("ACK C29A", 45)
        S.send([S.msg("C29B Reply exactly: ACK C29B", msgV="one")])
        word = S.wait_text("ACK C29B", 45)
        report("R29 msgV other than 1 is not rejected: frames with msgV 2 and msgV \"one\" are delivered", "PASS" if two and word else "FAIL",
               "msgV 2: %s; msgV \"one\": %s" % ("delivered" if two else "not delivered", "delivered" if word else "not delivered"))

        # R30 the frame's uuid becomes the id of the queued input, the transcript line's uuid.
        given = str(uuid.uuid4())
        S.send([S.msg("C30 Reply exactly: ACK C30", uuid=given)])
        line = S.transcript_has("C30 Reply exactly", 45)
        report("R30 a frame's uuid is the id the receiver gives the input: the transcript line carries it", "PASS" if line and line.get("uuid") == given else "FAIL",
               "transcript uuid %s" % ("is the frame's" if line and line.get("uuid") == given else (line or {}).get("uuid", "missing")))

        # R35 notify_when_idle refusals, on a session that is idle and holds nothing (a held message
        # delays idle notices). A refused subscription gets no notice; the debug log names the reason.
        # A valid one, sent last, shows that the session answers.
        rp35 = fresh_reply()
        outside = Reply(short, name="n%d.sock" % os.getpid())
        replies.append(outside)
        def notify(**kw):
            f = {"msgV": 1, "msg_id": str(uuid.uuid4()), "type": "control", "action": "notify_when_idle", "session_id": S.sid}
            f.update(kw)
            return f
        refusals = [("no from", notify(), "notify_when_idle dropped: malformed frame"),
                    ("its own address", notify(**{"from": uds_address(S.sock)}), "notify_when_idle dropped: reply target is this session"),
                    ("an address outside its socket directories", notify(**{"from": outside.address}), "notify_when_idle dropped: reply address unshaped or outside our socket namespace"),
                    ("a msg_id that is not a UUID", notify(**{"from": rp35.address, "msg_id": "abc"}), "notify_when_idle from %s: invalid" % rp35.address)]
        before = {needle: S.debug_count(needle) for _, _, needle in refusals}
        valid = notify(**{"from": rp35.address})
        S.send([f for _, f, _ in refusals] + [valid])
        answered = rp35.wait(lambda f: f.get("action") == "peer_idle_notice" and f.get("orig_msg_id") == valid["msg_id"], 20)
        time.sleep(2)
        stray = [f for f in list(rp35.frames) + list(outside.frames) if f.get("action") == "peer_idle_notice" and f.get("orig_msg_id") != valid["msg_id"]]
        logged = [what for what, _, needle in refusals if S.debug_count(needle) > before[needle]]
        report("R35 notify_when_idle is refused with no from, with the receiver's own address, with an address outside its socket directories, and with a msg_id that is not a UUID",
               "PASS" if answered and not stray and len(logged) == len(refusals) else "FAIL",
               "refusals logged: %d of %d; notices to refused subscriptions: %d; the valid one answered: %s" % (len(logged), len(refusals), len(stray), bool(answered)))

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

        # The rules below load the session (R19 leaves a held message behind, R17 queues 30 prompts), so
        # they come last. Each runs on a fresh reply socket (fresh_reply above): its `from` address is a
        # sender key the rules above never used, so the rate bucket is full and the duplicate window empty.
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

        # R19 the envelope's from-mode against a prompting receiver (a default -p session is one)
        rp19 = fresh_reply()
        def envelope19(mode, text):
            return envelope([("from", rp19.address), ("from-name", "conformance"), ("from-mode", mode)], text)
        m = S.msg(envelope19("prompting", "C19 Reply exactly: ACK C19"), rp19)
        S.send([m])
        got = S.wait_text("ACK C19", 45)
        r = rp19.wait(lambda f: f.get("orig_msg_id") == m["msg_id"], 4)
        report("R19 an envelope asserting from-mode=prompting is delivered to a prompting receiver, no receipt", "PASS" if got and r is None else "FAIL", json.dumps(r) if r else "")
        m = S.msg(envelope19("bypass", "C19b Reply exactly: ACK C19B"), rp19)
        S.send([m])
        r = rp19.wait(lambda f: f.get("orig_msg_id") == m["msg_id"], 20)
        delivered = S.wait_text("ACK C19B", 6)
        report("R19b an envelope asserting from-mode=bypass is held by a prompting receiver: `held` receipt naming permission-mode parity",
               "PASS" if r and r.get("status") == "held" and "parity" in (r.get("reason") or "") and not delivered else "FAIL",
               json.dumps(r) if r else "no receipt")

        def outcome(sess, rp, m, marker, timeout=40, before=()):
            """Post `m`, after the frames `before` on the same connection, and say what became of it: `held`
            (a held receipt on `rp`), `delivered` (the model answered with `ACK <marker>`) or `neither`."""
            sess.send(list(before) + [m])
            end = time.time() + timeout
            while time.time() < end:
                if any(f.get("orig_msg_id") == m["msg_id"] and f.get("status") == "held" for f in list(rp.frames)):
                    return "held"
                if any("ACK " + marker in t for t in sess.texts):
                    return "delivered"
                time.sleep(0.2)
            return "neither"

        def asking(marker):
            return "%s Reply exactly: ACK %s" % (marker, marker)

        # R21 the receiver reads an envelope only when it re-serialises to the same bytes. A prompting
        # receiver holds an envelope that asserts from-mode=bypass (R19b), so `held` says the envelope was
        # read, and `delivered` says it was not and the frame counted as asserting no permission class.
        rp21 = fresh_reply()
        def env21(attrs, marker):
            return outcome(S, rp21, S.msg(envelope(attrs, asking(marker)), rp21), marker)
        reordered = env21([("from", rp21.address), ("from-mode", "bypass"), ("from-name", "conformance")], "C21A")
        report("R21 an envelope with its attributes out of order is not read: its from-mode=bypass is ignored and the frame delivered",
               "PASS" if reordered == "delivered" else "FAIL", reordered)
        name64 = env21([("from", rp21.address), ("from-name", "n" * 64), ("from-mode", "bypass")], "C21B")
        name65 = env21([("from", rp21.address), ("from-name", "n" * 65), ("from-mode", "bypass")], "C21C")
        report("R21b an envelope with a 64-character from-name is read; with a 65-character one it is not",
               "PASS" if name64 == "held" and name65 == "delivered" else "FAIL", "64: %s, 65: %s" % (name64, name65))

        # R22 a msg_id that is not a UUID counts as absent: a frame with msg_id "abc" fares like one with no
        # msg_id at all. Both assert bypass, so both are held; whether a receipt comes, and that it names no
        # orig_msg_id, must be the same for the two.
        rp22 = fresh_reply()
        def held22(marker, msg_id):
            m = S.msg(envelope([("from", rp22.address), ("from-mode", "bypass")], asking(marker)), rp22)
            if msg_id is None:
                del m["msg_id"]
            else:
                m["msg_id"] = msg_id
            old = {id(f) for f in rp22.frames}
            S.send([m])
            r = rp22.wait(lambda f: f.get("action") == "peer_message_status" and id(f) not in old, 15)
            return r, bool(S.wait_text("ACK " + marker, 1 if r else 10))
        def said(r, shown):
            return "delivered" if shown else (json.dumps(r) if r else "held, no receipt")
        (absent, shown_a), (abc, shown_b) = held22("C22A", None), held22("C22B", "abc")
        same = not shown_a and not shown_b and (absent is None) == (abc is None) and \
            all(r.get("status") == "held" and r.get("orig_msg_id") is None for r in (absent, abc) if r)
        report("R22 a msg_id that is not a UUID is treated as absent: the frame fares like one with no msg_id, and no receipt names it",
               "PASS" if same else "FAIL", "no msg_id: %s; msg_id=abc: %s" % (said(absent, shown_a), said(abc, shown_b)))

        # R25 a receipt goes only to the process that posted. A child of this script posts a frame that will
        # be held, naming this script's reply socket as `from`: the listener there is not the poster, so no
        # receipt may come. The child stays alive meanwhile, so its pid is still there to be checked.
        rp25 = fresh_reply()
        mine = S.msg(envelope([("from", rp25.address), ("from-mode", "bypass")], asking("C25A")), rp25)
        own = outcome(S, rp25, mine, "C25A", 20)
        posed = S.msg(envelope([("from", rp25.address), ("from-mode", "bypass")], asking("C25B")), rp25)
        poster = ("import socket,sys,time;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);"
                  "s.sendall(sys.argv[2].encode());s.shutdown(socket.SHUT_WR);time.sleep(30)")
        child = subprocess.Popen([sys.executable, "-c", poster, S.sock, json.dumps(posed) + "\n"])
        try:
            other = rp25.wait(lambda f: f.get("orig_msg_id") == posed["msg_id"], 20)
            shown = S.wait_text("ACK C25B", 1)
        finally:
            child.kill()
            child.wait()
        report("R25 a receipt goes only to the process that posted: another process naming our socket as `from` gets none",
               "PASS" if own == "held" and other is None and not shown else "FAIL",
               "own post: %s; other process: %s" % (own, json.dumps(other) if other else ("delivered" if shown else "no receipt")))

        # R31 `from` is percent-encoded. A reply socket whose name has a space is reachable only through
        # its encoded address: a held frame naming it encoded gets its receipt, and one naming the raw
        # path gets none. The envelope, which carries the encoded form in both, is what makes it held.
        rp31 = Reply(os.path.dirname(S.sock), name="%d-%08x c31.sock" % (os.getpid(), uuid.uuid4().int & 0xFFFFFFFF))
        replies.append(rp31)
        def held31(address, marker):
            m = S.msg(envelope([("from", rp31.address), ("from-mode", "bypass")], asking(marker)), **{"from": address})
            S.send([m])
            return rp31.wait(lambda f: f.get("orig_msg_id") == m["msg_id"], 15)
        encoded, raw = held31(rp31.address, "C31A"), held31("uds:" + rp31.path, "C31B")
        report("R31 `from` is percent-encoded: a socket name with a space gets its receipt at the encoded address and none at the raw one",
               "PASS" if encoded and encoded.get("status") == "held" and raw is None else "FAIL",
               "encoded: %s; raw: %s" % (encoded.get("status") if encoded else "no receipt", raw.get("status") if raw else "no receipt"))

        # R32 the envelope's attributes in the documented order, all six, are read: the bypass
        # assertion among them makes a prompting receiver hold the frame.
        rp32 = fresh_reply()
        full = [("from", rp32.address), ("from-session", "conformance-32"), ("hop-chain", "0123456789abcdef01234567"),
                ("from-name", "conformance"), ("from-mode", "bypass"), ("from-plugin", "conformance")]
        seen32 = outcome(S, rp32, S.msg(envelope(full, asking("C32")), rp32), "C32")
        report("R32 an envelope with all six attributes in the documented order is read: its from-mode=bypass is held",
               "PASS" if seen32 == "held" else "FAIL", seen32)

        # R33 drop receipts to one sender are coalesced. After a first copy, four identical ones are
        # dropped as duplicates: the first drop is answered at once, and the next three together in one
        # receipt about 5 s later, their ids in dropped_msg_ids.
        rp33 = fresh_reply()
        body = "C33 coalesce %s. Reply exactly: ACK C33" % uuid.uuid4().hex[:6]
        S.send([S.msg(body, rp33)])
        S.wait_text("ACK C33", 45)
        dups = [S.msg(body, rp33) for _ in range(4)]
        for m in dups:
            S.send([m])
            time.sleep(0.2)
        def status33():
            return [(f, at) for f, at in zip(list(rp33.frames), list(rp33.arrived)) if f.get("action") == "peer_message_status"]
        end = time.time() + 15
        while len(status33()) < 2 and time.time() < end:
            time.sleep(0.2)
        time.sleep(2)
        got = status33()
        ids = [m["msg_id"] for m in dups]
        first = next((f for f, _ in got if f.get("orig_msg_id") == ids[0]), None)
        rest = [(f, at) for f, at in got if f is not first]
        named = set((rest[0][0].get("dropped_msg_ids") or []) + [rest[0][0].get("orig_msg_id")]) if len(rest) == 1 else set()
        gap = rest[0][1] - got[0][1] if len(rest) == 1 and got else 0.0
        late = len(rest) == 1 and gap >= 4
        report("R33 drop receipts to one sender are coalesced: the first at once, the next three in one receipt at least 4 s later",
               "PASS" if first and not first.get("dropped_msg_ids") and len(rest) == 1 and set(ids[1:]) <= named and late else "FAIL",
               "%d receipts, %.1f s apart; the later one names %d of the other 3" % (len(got), gap, len(set(ids[1:]) & named)))

        # R34 the sender key for the duplicate rule is `from` when present, else the connecting pid.
        # Without `from`, a repeat from this process is a duplicate and the same body from another
        # process is not; with `from`, two addresses are two senders even from one process.
        tag34 = uuid.uuid4().hex[:6]
        unknown = "drop duplicate from unknown"
        def routed(marker, n):
            return S.debug_wait("Routed user message to queue (priority=next): %s" % marker, n, 45)
        nofrom = S.debug_count(unknown)
        body = "C34A same body %s. Reply exactly: OK" % tag34
        S.send([S.msg(body)])
        routed("C34A", 1)
        post = ("import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);"
                "s.sendall(sys.argv[2].encode());s.shutdown(socket.SHUT_WR);s.recv(1);s.close()")
        subprocess.run([sys.executable, "-c", post, S.sock, json.dumps(S.msg(body)) + "\n"], timeout=20)
        routed("C34A", 2)
        other_pid = S.debug_count(unknown) - nofrom
        S.send([S.msg(body)])
        routed("C34A", 3)
        same_pid = S.debug_count(unknown) - nofrom - other_pid
        f1, f2 = (uds_address(os.path.join(os.path.dirname(S.sock), "%d-c34%s.sock" % (os.getpid(), x))) for x in "ab")
        body = "C34B same body %s. Reply exactly: OK" % tag34
        for address in (f1, f2, f1):
            S.send([S.msg(body, **{"from": address})])
        routed("C34B", 3)
        d1, d2 = S.debug_count("drop duplicate from %s" % f1), S.debug_count("drop duplicate from %s" % f2)
        report("R34 the duplicate rule keys on `from`, else on the connecting pid: repeats from one pid or one address are dropped, from another pid or address admitted",
               "PASS" if other_pid == 0 and same_pid == 1 and d1 == 1 and d2 == 0 else "FAIL",
               "no from: other pid dropped %d, same pid dropped %d; from: same address dropped %d, other address dropped %d" % (other_pid, same_pid, d1, d2))

        # R27 the 30 s deadline is for the first line: a connection whose first line is complete, a frame or a
        # blank line, is still open past it, and a frame written later on the blank one is delivered.
        r27 = ("R27 a complete first line clears the 30 s deadline: the connection is still open at 40 s",
               "R27b a blank first line counts as that line: the connection stays open, and a frame sent on it at 40 s is delivered")
        if args.quick:
            for rule in r27:
                report(rule, "SKIP", "--quick")
        else:
            framed = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            framed.connect(S.sock)
            framed.sendall((json.dumps(S.msg(asking("C27A"))) + "\n").encode())
            blank = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            blank.connect(S.sock)
            blank.sendall(b"\n")
            time.sleep(40)

            def still_open(c):
                # The receiver never writes on a connection, so a read that would block means it is open.
                c.setblocking(False)
                try:
                    return c.recv(1) != b""
                except BlockingIOError:
                    return True
                except OSError:
                    return False
                finally:
                    c.setblocking(True)

            open_framed, open_blank = still_open(framed), still_open(blank)
            try:
                blank.sendall((json.dumps(S.msg(asking("C27B"))) + "\n").encode())
                blank.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            late = S.wait_text("ACK C27B", 45)
            framed.close()
            blank.close()
            report(r27[0], "PASS" if open_framed else "FAIL", "open at 40 s: %s" % open_framed)
            report(r27[1], "PASS" if open_blank and late else "FAIL", "open at 40 s: %s; later frame delivered: %s" % (open_blank, bool(late)))

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
        # may be coalesced into one receipt over a 5 s trail, hence the wait after the first. The bucket refills
        # at 0.5 per second, so a burst that a busy receiver takes more than 2 s to admit lets one more in per
        # 2 s; the debug log times the admissions.
        rp17 = fresh_reply()
        rl_ids = []
        tag17 = uuid.uuid4().hex[:4]
        for i in range(34):
            m = S.msg("C17 rate %s %02d. Reply exactly: OK" % (tag17, i), rp17)
            rl_ids.append(m["msg_id"])
            S.send([m])
        end = time.time() + 10
        while time.time() < end and "rate-limited" not in drops(rp17).values():
            time.sleep(0.2)
        time.sleep(6)
        d = drops(rp17)
        first_rl = next((i for i, mid in enumerate(rl_ids) if d.get(mid) == "rate-limited"), None)
        early = sum(mid in d for mid in rl_ids[:30])
        began = S.debug_times("Routed user message to queue (priority=next): C17 rate %s 00" % tag17)
        limited = S.debug_times("drop rate-limited from %s" % rp17.address)
        took = limited[0] - began[0] if began and limited else 0.0
        report("R17 a burst from one sender is admitted up to a bucket of 30; the 31st is dropped with drop_reason=rate-limited",
               "PASS" if first_rl is not None and 30 <= first_rl <= 30 + int(took * 0.5) and early == 0 else "FAIL",
               "first rate-limited at index %s, %.1f s into the burst; dropped among the first 30: %d" % (first_rl, took, early))

        S.stop()

        R23 = "R23 waiting messages are read now first, then next in arrival order, then later; an unknown priority reads as next"
        R24 = "R24 at most 50 accepted messages wait; the 51st is dropped with drop_reason=queue-full"
        R26 = "R26 only the first auth line of a connection counts: the child token after a wrong one does not make the post the session's own"
        if args.quick:
            for rule in ("R14 crossSessionInbound=hold: `held` receipt, nothing delivered",
                         "R15 crossSessionInbound=refuse: `expired` receipt with status_detail=refused",
                         "R16 own child (SessionStart hook) posting with the token is delivered",
                         "R16b own child posting without the token is delivered (process evidence)",
                         R26, R23, R24, "R36 idle subscription limits", "R37 the key file", "R38 senders tracked by the duplicate rule",
                         "R39-R45 where the inbox is bound"):
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
                      "s.sendall((json.dumps({'type':'user','message':{'content':'C16b Reply exactly: ACK C16B'}})+'\\n').encode());s.shutdown(1);s.close()")
            hook = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "%s -c \"%s\"" % (py, poster.replace('"', '\\"'))}]}]}}
            O = Session(cwd, args.model, ["--settings", json.dumps(hook)], "ownchild")
            sessions.append(O)
            report("R16 own child (SessionStart hook) posting with the token is delivered", "PASS" if O.wait_text("ACK C16", 60) else "FAIL")
            report("R16b own child posting without the token is delivered (process evidence)", "PASS" if O.wait_text("ACK C16B", 60) else "FAIL")
            O.stop()

            # R26 only the first auth line of a connection counts. A SessionStart hook leaves the child token in
            # the scratch directory, so this script, which is not the session's child, can send it. A frame
            # that asserts bypass is held at a prompting receiver unless its connection counts as the
            # session's own child: with the token first it should be delivered, and with a wrong token
            # first and the right one second, held. The token file goes with the scratch directory.
            tokfile = os.path.join(cwd, "child-token")
            hook = {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
                    "command": "printf %%s \"$CLAUDE_CODE_MESSAGING_TOKEN\" > '%s'" % tokfile}]}]}}
            T = Session(cwd, args.model, ["--settings", json.dumps(hook)], "token")
            sessions.append(T)
            rp26 = Reply(os.path.dirname(T.sock), "-%08x" % (uuid.uuid4().int & 0xFFFFFFFF))
            replies.append(rp26)
            try:
                token = pathlib.Path(tokfile).read_text().strip()
            except OSError:
                token = ""
            def auth26(tokens, marker):
                m = T.msg(envelope([("from", rp26.address), ("from-mode", "bypass")], asking(marker)), rp26)
                return outcome(T, rp26, m, marker, before=[{"type": "auth", "token": t} for t in tokens])
            if len(token) != 32:
                report(R26, "SKIP", "the hook left no child token")
            else:
                first = auth26([token], "C26A")
                second = auth26(["0" * 32, token], "C26B")
                if first != "delivered":
                    report(R26, "SKIP", "the child token from a process that is not the session's child changed nothing (%s), so the order of auth lines cannot be seen" % first)
                else:
                    report(R26, "PASS" if second == "held" else "FAIL", "token first: %s; wrong token first, token second: %s" % (first, second))

            # R37 the key file beside the registry record. The values stay in this process: only their
            # shape, and whether they match, is reported.
            keys = list(SESS_DIR.glob("%d.*.key" % T.p.pid))
            problems = []
            if len(keys) != 1:
                problems.append("%d key files" % len(keys))
            else:
                try:
                    data = json.loads(keys[0].read_text())
                except (OSError, ValueError):
                    data = {}
                peer = data.get("peerToken")
                if sorted(data) != ["peerToken", "pidDomain", "procStart"]:
                    problems.append("fields %s" % ", ".join(sorted(data)))
                if not (isinstance(peer, str) and re.fullmatch(r"[0-9a-f]{32}", peer)):
                    problems.append("peerToken is not 32 hex")
                if peer == token:
                    problems.append("peerToken is the child token")
                if len(token) != 32:
                    problems.append("no child token to compare")
                if (keys[0].stat().st_mode & 0o777) != 0o600:
                    problems.append("mode %o" % (keys[0].stat().st_mode & 0o777))
                hashes = {hashlib.sha256(x.encode()).hexdigest() for x in (T.sock, os.path.realpath(T.sock))}
                if keys[0].name.split(".")[1] not in hashes:
                    problems.append("name is not <pid>.<sha256 of the socket path>.key")
                if data.get("procStart") != T.record().get("procStart"):
                    problems.append("procStart differs from the registry record")
            report("R37 the key file holds peerToken (32 hex, not the child token), procStart and pidDomain, mode 0600, named <pid>.<sha256 of the socket path>.key",
                   "PASS" if not problems else "FAIL", "; ".join(problems) if problems else "fields peerToken, pidDomain, procStart")
            T.stop()

            # R23 and R24 need messages waiting. A session with no tools reads peer messages only between
            # turns, so while it writes a long answer every post waits in its queue.
            Q = Session(cwd, args.model, label="queue")
            sessions.append(Q)
            rpq = Reply(os.path.dirname(Q.sock), "-%08x" % (uuid.uuid4().int & 0xFFFFFFFF))
            replies.append(rpq)
            # Long enough to keep the session busy: asked for 1 to 600, haiku once answered in one line in 2 s.
            busy = "Write the whole numbers from 1 to 3000, one per line, and nothing else."
            tag = uuid.uuid4().hex[:4]

            def idle(timeout=120):
                nid = str(uuid.uuid4())
                Q.send([{"msgV": 1, "msg_id": nid, "type": "control", "action": "notify_when_idle", "from": rpq.address, "session_id": Q.sid}])
                return rpq.wait(lambda f: f.get("action") == "peer_idle_notice" and f.get("orig_msg_id") == nid, timeout)

            def start_busy():
                """Prompt the long answer and make sure the session is in it: an idle session answers a
                notify_when_idle at once, a busy one only when its turn ends. Tries twice."""
                for _ in range(2):
                    Q.prompt(busy)
                    time.sleep(3)
                    if idle(2) is None:
                        return True
                    idle()
                return False

            busy_ok = start_busy()
            order = [("later", "C23L"), ("urgent", "C23X"), ("next", "C23N"), ("now", "C23W")]
            for prio, marker in order:
                Q.send([Q.msg("%s %s. Reply exactly: OK" % (marker, tag), priority=prio)])
                time.sleep(0.1)
            seen = Q.transcript_order([m for _, m in order], 120)
            if not busy_ok:
                report(R23, "SKIP", "the session was not busy when the messages came")
            else:
                report(R23, "PASS" if seen == ["C23W", "C23X", "C23N", "C23L"] else "FAIL",
                       "read order: %s" % (", ".join(seen) if seen else "not all read"))

            idle()
            time.sleep(5)
            idle()
            rpa, rpb = fresh_reply(), fresh_reply()
            busy_ok = start_busy()
            ids = []
            for i in range(51):
                m = Q.msg("C24 %s %02d. Reply exactly: OK" % (tag, i), rpa if i < 30 else rpb)
                ids.append(m["msg_id"])
                Q.send([m])
            time.sleep(8)  # drops to one sender may be coalesced over a 5 s trail
            d = drops(rpa)
            d.update(drops(rpb))
            first_full = next((i for i, mid in enumerate(ids) if d.get(mid) == "queue-full"), None)
            early = sum(mid in d for mid in ids[:50])
            if not busy_ok:
                report(R24, "SKIP", "the session was not busy when the messages came")
            else:
                report(R24, "PASS" if first_full == 50 and early == 0 else "FAIL",
                       "first queue-full at index %s; dropped among the first 50: %d; reasons: %s" % (first_full, early, sorted(set(d.values()))))

            # R36 idle subscriptions while the session is busy: at most 4 per verified pid, the oldest
            # answered `unavailable` when a fifth comes, and 32 in all, a subscription past that answered
            # `unavailable` at once. Each subscription has its own reply socket; the other pids are child
            # processes that subscribe from their own sockets and print what they receive. The busy check
            # leaves one subscription of this pid waiting, so this pid has six: the check's and five more.
            # The fifth pushes out the check's, the sixth the first of the five.
            idle()
            time.sleep(5)
            idle()
            busy_ok = start_busy()
            sub_ids = []
            ours = [fresh_reply() for _ in range(5)]
            for rp in ours:
                nid = str(uuid.uuid4())
                sub_ids.append(nid)
                Q.send([{"msgV": 1, "msg_id": nid, "type": "control", "action": "notify_when_idle", "from": rp.address, "session_id": Q.sid}])
                time.sleep(0.2)
            child = ("import json,os,select,socket,sys,time,uuid\n"
                     "d,n,sid,target,wait=sys.argv[1],int(sys.argv[2]),sys.argv[3],sys.argv[4],float(sys.argv[5])\n"
                     "srv=[]\n"
                     "for i in range(n):\n"
                     "    p=os.path.join(d,'%d-%08x.sock'%(os.getpid(),uuid.uuid4().int&0xFFFFFFFF));s=socket.socket(socket.AF_UNIX);s.bind(p);os.chmod(p,0o600);s.listen(4);srv.append((s,p))\n"
                     "    c=socket.socket(socket.AF_UNIX);c.connect(target)\n"
                     "    c.sendall((json.dumps({'msgV':1,'msg_id':str(uuid.uuid4()),'type':'control','action':'notify_when_idle','from':'uds:'+p,'session_id':sid})+'\\n').encode());c.shutdown(socket.SHUT_WR);c.recv(1);c.close()\n"
                     "print('subscribed',flush=True)\n"
                     "got={};end=time.time()+wait\n"
                     "while len(got)<n and time.time()<end:\n"
                     "    r,_,_=select.select([s for s,_ in srv],[],[],0.5)\n"
                     "    for s in r:\n"
                     "        c,_=s.accept();c.settimeout(5);data=b''\n"
                     "        try:\n"
                     "            while True:\n"
                     "                b=c.recv(65536)\n"
                     "                if not b: break\n"
                     "                data+=b\n"
                     "        except socket.timeout: pass\n"
                     "        c.close()\n"
                     "        for line in data.decode().splitlines():\n"
                     "            f=json.loads(line)\n"
                     "            if f.get('action')=='peer_idle_notice': got.setdefault(s.fileno(),f.get('state'))\n"
                     "for s,p in srv:\n"
                     "    s.close();os.unlink(p)\n"
                     "print(json.dumps(sorted(got.values())),flush=True)\n")
            qdir = os.path.dirname(Q.sock)
            kids = []
            for k in range(8):
                kid = subprocess.Popen([sys.executable, "-c", child, qdir, "4" if k < 7 else "1", Q.sid, Q.sock, "150"],
                                       stdout=subprocess.PIPE, text=True)
                kids.append(kid)
                kid.stdout.readline()
            evicted = ours[0].wait(lambda f: f.get("action") == "peer_idle_notice", 15)
            states = []
            for kid in kids:
                try:
                    out, _ = kid.communicate(timeout=180)
                except subprocess.TimeoutExpired:
                    kid.kill()
                    out, _ = kid.communicate()
                try:
                    states.append(json.loads(out.strip().splitlines()[-1]))
                except (ValueError, IndexError):
                    states.append([])
            mine = [rp.wait(lambda f: f.get("action") == "peer_idle_notice", 10) for rp in ours[1:]]
            fourth = all(f and f.get("state") == "idle" for f in mine)
            kept = all(s == ["idle"] * 4 for s in states[:7])
            refused = states[7] == ["unavailable"]
            R36 = "R36 idle subscriptions: 4 per verified pid, the oldest answered `unavailable` by a fifth; 32 in all, the 33rd answered `unavailable`"
            if not busy_ok:
                report(R36, "SKIP", "the session was not busy when the subscriptions came")
            else:
                report(R36, "PASS" if evicted and evicted.get("state") == "unavailable" and fourth and kept and refused else "FAIL",
                       "this pid's first of 5: %s; its other 4: %s; 7 other pids x 4: %s; the 33rd: %s" %
                       (evicted.get("state") if evicted else "no notice", "idle" if fourth else [f.get("state") if f else None for f in mine],
                        "all idle" if kept else states[:7], states[7]))
            Q.stop()

            # R38 the duplicate and rate rules track 256 senders and forget the least recently seen. In a
            # fresh session, sender A posts, then B, then 254 others: 256 tracked. B's repeat is a duplicate,
            # which also makes B recent; one more sender, the 257th, evicts A, so A's repeat is not.
            G = Session(cwd, args.model, label="senders", debug_file=os.path.join(cwd, "debug-senders.log"))
            sessions.append(G)
            gdir = os.path.dirname(G.sock)
            tag38 = uuid.uuid4().hex[:6]
            def sender(name):
                return uds_address(os.path.join(gdir, "%d-c38%s.sock" % (os.getpid(), name)))
            def post38(name, text):
                G.send([G.msg("C38 %s %s. Reply exactly: OK" % (text, tag38), **{"from": sender(name)})])
            post38("a", "first")
            post38("b", "second")
            for n in range(254):
                post38("o%03d" % n, "other %03d" % n)
            post38("b", "second")
            post38("c", "last")
            post38("a", "first")
            G.debug_wait("Routed user message to queue (priority=next): C38", 259, 60)
            dup_b = G.debug_count("drop duplicate from %s" % sender("b"))
            dup_a = G.debug_count("drop duplicate from %s" % sender("a"))
            routed_a = G.debug_count("Routed user message to queue (priority=next): C38 first")
            report("R38 the duplicate rule tracks 256 senders: with 256 tracked a repeat is still caught, and the 257th sender evicts the least recently seen",
                   "PASS" if dup_b == 1 and dup_a == 0 and routed_a == 2 else "FAIL",
                   "repeat at 256 tracked dropped: %d; repeat of the evicted sender dropped: %d" % (dup_b, dup_a))
            G.stop()

            # R39 to R45: where the inbox is bound. Each starts a session with no prompt and reads the
            # socket path its SessionStart hook was given, so they cost no model use. They work in the
            # short directory, since socket paths are limited to about 104 bytes.
            def private(*parts, mode=0o700):
                d = os.path.join(short, *parts)
                os.makedirs(d, exist_ok=True)
                os.chmod(d, mode)
                return d
            fallback = re.compile(r"^/tmp/cc-socks-\d+/(\d+)\.sock$")
            def at(res, path):
                return res["socket"] == path and res.get("live")
            def where(res):
                if res["exit"] not in (None, 0) and not res["hook"]:
                    said = res["stderr"].splitlines()[-1] if res["stderr"] else ""
                    return "exited %s: %s" % (res["exit"], said.split(";")[0].split(": /")[0])
                return res["socket"] or "no inbox"

            # R39 CLAUDE_CODE_TMPDIR is the base when XDG_RUNTIME_DIR is unset.
            base = private("t")
            res = probe(cwd, args.model, env={"XDG_RUNTIME_DIR": None, "CLAUDE_CODE_TMPDIR": base})
            report("R39 with XDG_RUNTIME_DIR unset, the socket is <CLAUDE_CODE_TMPDIR>/cc-socks/<pid>.sock",
                   "PASS" if at(res, os.path.join(base, "cc-socks", "%d.sock" % res["pid"])) else "FAIL", where(res))

            # R40 the default path may be 103 bytes; at 104 the per-user fallback is used. The pid is known
            # before Claude Code starts, so XDG_RUNTIME_DIR is padded to make the path exactly that long.
            def sized(n):
                def setup(pid):
                    pad = n - len("/cc-socks/%d.sock" % pid) - len(short) - 1
                    if pad < 1:
                        raise ValueError("this checkout's path is too long for a %d-byte socket path" % n)
                    return {"XDG_RUNTIME_DIR": private("x" * pad)}
                return setup
            try:
                fits, over = probe(cwd, args.model, setup=sized(103)), probe(cwd, args.model, setup=sized(104))
                ok = fits["socket"] and len(fits["socket"].encode()) == 103 and fits["socket"].startswith(short) and fits.get("live") \
                    and over["socket"] and fallback.match(over["socket"]) and over.get("live")
                report("R40 a default socket path of 103 bytes is used; one of 104 falls back to /tmp/cc-socks-<uid>/<pid>.sock",
                       "PASS" if ok else "FAIL", "103: %s; 104: %s" % (where(fits), where(over)))
            except ValueError as e:
                report("R40 a default socket path of 103 bytes is used; one of 104 falls back to /tmp/cc-socks-<uid>/<pid>.sock", "SKIP", str(e))

            # R41 a refused directory falls back: one writable by others without the sticky bit, or a
            # sockets directory that is a symlink. With the sticky bit the same directory is used.
            res = probe(cwd, args.model, env={"XDG_RUNTIME_DIR": private("w", mode=0o777)})
            report("R41 a base directory writable by others without the sticky bit is refused: the socket falls back to /tmp/cc-socks-<uid>/",
                   "PASS" if res["socket"] and fallback.match(res["socket"]) and res.get("live") else "FAIL", where(res))
            sticky = private("k", mode=0o1777)
            res = probe(cwd, args.model, env={"XDG_RUNTIME_DIR": sticky})
            report("R41b the same directory with the sticky bit is used",
                   "PASS" if at(res, os.path.join(sticky, "cc-socks", "%d.sock" % res["pid"])) else "FAIL", where(res))
            linked = private("l")
            os.symlink(private("r"), os.path.join(linked, "cc-socks"))
            res = probe(cwd, args.model, env={"XDG_RUNTIME_DIR": linked})
            report("R41c a sockets directory that is a symlink is refused: the socket falls back to /tmp/cc-socks-<uid>/",
                   "PASS" if res["socket"] and fallback.match(res["socket"]) and res.get("live") else "FAIL", where(res))

            # R42 under Termux (TERMUX_VERSION set) the per-user fallback is under $PREFIX/tmp.
            prefix = private("u")
            res = probe(cwd, args.model, env={"XDG_RUNTIME_DIR": private("w", mode=0o777), "TERMUX_VERSION": "0", "PREFIX": prefix})
            m42 = re.match(r"^%s/tmp/cc-socks-\d+/(\d+)\.sock$" % re.escape(prefix), res["socket"] or "")
            report("R42 with TERMUX_VERSION set, the per-user fallback is $PREFIX/tmp/cc-socks-<uid>/<pid>.sock",
                   "PASS" if m42 and res.get("live") else "FAIL", where(res))

            # R43 with no acceptable directory the session runs with no inbox, and neither variable is set.
            prefix = private("v")
            private("v", "tmp", mode=0o777)
            res = probe(cwd, args.model, env={"XDG_RUNTIME_DIR": private("w", mode=0o777), "TERMUX_VERSION": "0", "PREFIX": prefix})
            report("R43 with no acceptable directory there is no inbox: CLAUDE_CODE_MESSAGING_SOCKET and CLAUDE_CODE_MESSAGING_TOKEN are both unset",
                   "PASS" if res["hook"] and res["socket"] is None and not res["token"] else "FAIL",
                   "hook ran: %s; socket: %s; token set: %s" % (res["hook"], res["socket"] or "unset", res["token"]))

            # R44 a default path that another live process listens on: the session binds <pid>-<8 hex>.sock
            # beside it. This listens at the pid's path in the default socket directory, the one the
            # main session used, before Claude Code starts in that pid.
            taken = []
            def occupy(pid):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.bind(os.path.join(sock_dir, "%d.sock" % pid))
                s.listen(1)
                taken.append(s)
                return {}
            try:
                res = probe(cwd, args.model, setup=occupy)
            finally:
                for s in taken:
                    os.unlink(s.getsockname())
                    s.close()
            moved = re.match(r"^%s/%d-[0-9a-f]{8}\.sock$" % (re.escape(sock_dir), res["pid"]), res["socket"] or "")
            report("R44 when another live process holds the default path, the socket is <pid>-<8 hex>.sock beside it",
                   "PASS" if moved and res.get("live") else "FAIL", where(res))

            # R45 --messaging-socket-path binds where it says, in a private directory; it refuses a parent
            # that others can read, and a path over 103 bytes.
            chosen = os.path.join(private("m"), "chosen.sock")
            res = probe(cwd, args.model, extra=["--messaging-socket-path", chosen])
            report("R45 --messaging-socket-path binds the inbox at the given path", "PASS" if at(res, chosen) else "FAIL", where(res))
            res = probe(cwd, args.model, extra=["--messaging-socket-path", os.path.join(private("p", mode=0o755), "chosen.sock")])
            report("R45b --messaging-socket-path refuses a directory that is not mode 0700",
                   "PASS" if not res["hook"] and "not private" in res["stderr"] else "FAIL", where(res))
            d = private("q")
            name = lambda n: os.path.join(d, "c" * (n - len(d) - 1 - len(".sock")) + ".sock")
            fits, over = probe(cwd, args.model, extra=["--messaging-socket-path", name(103)]), probe(cwd, args.model, extra=["--messaging-socket-path", name(104)])
            report("R45c --messaging-socket-path takes a path of 103 bytes and refuses one of 104",
                   "PASS" if at(fits, name(103)) and not over["hook"] and "too long" in over["stderr"] else "FAIL",
                   "103: %s; 104: %s" % (where(fits), where(over)))

        # R20 a bypassing receiver: R19 and R19b again, against a session in bypassPermissions mode. It
        # runs only on request, because it starts a session that skips permission prompts; the session
        # still has no tools and works in the same empty directory as the others.
        r20 = ("R20 a bypassing receiver delivers an envelope asserting from-mode=bypass, no receipt",
               "R20b a bypassing receiver holds a frame that asserts no permission class: `held` receipt, nothing delivered")
        if not args.bypass:
            for rule in r20:
                report(rule, "SKIP", "needs --bypass")
        else:
            B = Session(cwd, args.model, ["--permission-mode", "bypassPermissions"], "bypass")
            sessions.append(B)
            rp20 = Reply(os.path.dirname(B.sock), "-%08x" % (uuid.uuid4().int & 0xFFFFFFFF))
            replies.append(rp20)
            text = "C20 Reply exactly: ACK C20"
            m = B.msg('<cross-session-message from="%s" from-name="conformance" from-mode="bypass">\n%s\n</cross-session-message>'
                      % (rp20.address, text), rp20)
            B.send([m])
            got = B.wait_text("ACK C20", 45)
            r = rp20.wait(lambda f: f.get("orig_msg_id") == m["msg_id"], 4)
            report(r20[0], "PASS" if got and r is None else "FAIL", json.dumps(r) if r else ("" if got else "not delivered"))
            m = B.msg("C20b Reply exactly: ACK C20B", rp20)
            B.send([m])
            r = rp20.wait(lambda f: f.get("orig_msg_id") == m["msg_id"], 20)
            delivered = B.wait_text("ACK C20B", 6)
            report(r20[1], "PASS" if r and r.get("status") == "held" and not delivered else "FAIL",
                   json.dumps(r) if r else ("delivered" if delivered else "no receipt"))
            B.stop()
    finally:
        for s in sessions:
            try:
                s.stop()
            except Exception:
                pass
        for r in replies:
            r.close()
        shutil.rmtree(cwd, ignore_errors=True)
        shutil.rmtree(short, ignore_errors=True)

    fails = [r for r in RESULTS if r[1] == "FAIL"]
    print("\n%d rules: %d pass, %d fail, %d skipped  (claude %s)" % (len(RESULTS), sum(r[1] == "PASS" for r in RESULTS), len(fails), sum(r[1] == "SKIP" for r in RESULTS), version))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
