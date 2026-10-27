#!/usr/bin/env node
// Post a message to a Claude Code session's inbox socket. Node 18.3 or later,
// built-in modules only.
//
//   send.mjs "text"                         to the session this shell runs under
//   send.mjs --name api-worker "text"       to a session by name (registry lookup)
//   send.mjs --pid 12345 "text"             by process id
//   send.mjs --socket /path/to/12345.sock "text"
//   send.mjs --receipts 20 "text"           also listen 20 s for receipt frames
//   send.mjs --list                         the sessions the registry knows, live or not
//
// It does what send.py does for a message; send.py also has --notify-idle and
// --rename. Receipts need a reply socket: send.mjs binds one beside the target's
// socket, named after its own pid, and puts its address in the frame's `from`
// field. An accepted message gets no receipt, so silence after a successful write
// means the message was taken.
//
// Exit status: 0 when the frames were written and no receipt said otherwise; 1
// when the socket could not be reached; 2 for a usage error; 3 when a receipt said
// the message was held, expired, denied or dropped and no later `delivered`
// receipt released it.
//
// See SPEC.md for the frames. This is not a public interface of Claude Code.

import { randomUUID } from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { parseArgs } from "node:util";

const SAFE = /[A-Za-z0-9:_/.\\-]/;

function fail(code, message) {
  console.error(`send.mjs: ${message}`);
  process.exit(code);
}

function configDir() {
  return process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude");
}

// `uds:` and the path, with every other character written as %XX per UTF-8 byte,
// the way Claude Code writes it.
function udsAddress(p) {
  let out = "uds:";
  for (const ch of p) {
    out += SAFE.test(ch) ? ch : [...Buffer.from(ch)].map((b) => "%" + b.toString(16).toUpperCase().padStart(2, "0")).join("");
  }
  return out;
}

function socketAlive(p) {
  return new Promise((resolve) => {
    const s = net.createConnection(p);
    const done = (alive) => {
      s.destroy();
      resolve(alive);
    };
    s.setTimeout(500, () => done(false));
    s.once("connect", () => done(true));
    s.once("error", () => done(false));
  });
}

// Session records: <config dir>/sessions/<pid>.json. A record can outlive its process,
// so each one is marked alive only when its socket answers.
async function registry() {
  const dir = path.join(configDir(), "sessions");
  let names;
  try {
    names = fs.readdirSync(dir).filter((n) => n.endsWith(".json")).sort();
  } catch {
    return [];
  }
  const out = [];
  for (const name of names) {
    let rec;
    try {
      rec = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
    } catch {
      continue;
    }
    const sock = rec.messagingSocketPath;
    rec.alive = Boolean(sock) && fs.existsSync(sock) && (await socketAlive(sock));
    out.push(rec);
  }
  return out;
}

async function findTarget(opts) {
  if (opts.socket) return [opts.socket, undefined];
  if (!(opts.pid || opts.name || opts["session-id"])) {
    const sock = process.env.CLAUDE_CODE_MESSAGING_SOCKET;
    if (!sock) fail(1, "no target: pass --socket, --pid, --name or --session-id, or run under a Claude Code session");
    return [sock, process.env.CLAUDE_CODE_SESSION_ID];
  }
  const matches = (await registry()).filter(
    (rec) =>
      rec.alive &&
      (!opts.pid || String(rec.pid) === opts.pid) &&
      (!opts.name || rec.name === opts.name) &&
      (!opts["session-id"] || rec.sessionId === opts["session-id"]),
  );
  if (matches.length === 0) fail(1, "no live session matches");
  if (matches.length > 1) fail(1, `${matches.length} live sessions match; pick one with --pid: ${matches.map((m) => m.pid).join(", ")}`);
  return [matches[0].messagingSocketPath, matches[0].sessionId];
}

// A listener beside the target socket, so the receiver's receipts can reach us.
// Each connection carries one or more JSON lines and is closed by the receiver.
function replySocket(targetSock) {
  const file = path.join(path.dirname(targetSock), `${process.pid}.sock`);
  fs.rmSync(file, { force: true });
  const frames = [];
  const server = net.createServer((c) => {
    let data = "";
    c.setEncoding("utf8");
    c.on("data", (chunk) => (data += chunk));
    c.on("end", () => {
      for (const line of data.split("\n")) {
        if (!line.trim()) continue;
        let frame;
        try {
          frame = JSON.parse(line);
        } catch {
          frame = { raw: line };
        }
        frames.push(frame);
        console.log(JSON.stringify(frame));
      }
    });
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(file, () => {
      fs.chmodSync(file, 0o600);
      resolve({
        address: udsAddress(file),
        frames,
        close: () => {
          server.close();
          fs.rmSync(file, { force: true });
        },
      });
    });
  });
}

// One connection: every frame as a JSON line, then half-close. The payload is built
// before connecting, since the receiver closes a connection that has not completed a
// line within 30 seconds. The receiver writes nothing back; waiting for its end of the
// connection makes sure the data was read.
function post(sockPath, frames) {
  const payload = frames.map((f) => JSON.stringify(f) + "\n").join("");
  return new Promise((resolve, reject) => {
    const s = net.createConnection(sockPath);
    s.setTimeout(10000, () => s.destroy(new Error("timed out")));
    s.once("error", reject);
    s.once("connect", () => s.end(payload));
    s.on("data", () => {});
    s.once("close", (hadError) => hadError || resolve());
  });
}

async function main() {
  let opts, positionals;
  try {
    ({ values: opts, positionals } = parseArgs({
      allowPositionals: true,
      options: {
        socket: { type: "string" },
        pid: { type: "string" },
        name: { type: "string" },
        "session-id": { type: "string" },
        list: { type: "boolean" },
        priority: { type: "string", default: "next" },
        "from-name": { type: "string" },
        auth: { type: "string" },
        "no-auth": { type: "boolean" },
        receipts: { type: "string" },
      },
    }));
  } catch (e) {
    fail(2, e.message);
  }
  if (!["now", "next", "later"].includes(opts.priority)) fail(2, "--priority is now, next or later");

  if (opts.list) {
    for (const rec of await registry()) {
      const pid = `pid=${rec.pid}`;
      console.log(`${(rec.alive ? "live" : "stale").padEnd(7)} ${pid.padEnd(12)} ${(rec.name || "-").padEnd(28)} ${rec.messagingSocketPath}  ${rec.cwd || ""}`);
    }
    return 0;
  }

  const [sockPath, sessionId] = await findTarget(opts);
  if (positionals.length === 0) fail(2, "no text given");
  const text = positionals.length === 1 && positionals[0] === "-" ? fs.readFileSync(0, "utf8") : positionals.join(" ");

  let wait;
  if (opts.receipts !== undefined) {
    wait = Number(opts.receipts);
    if (!(wait >= 0)) fail(2, "--receipts takes a number of seconds");
  }
  const reply = wait === undefined ? null : await replySocket(sockPath);

  const frames = [];
  // The child token proves the post comes from the session's own hook or command,
  // so it goes only to the session this shell runs under, never to another one.
  const own = sockPath === process.env.CLAUDE_CODE_MESSAGING_SOCKET;
  const token = opts.auth || (own ? process.env.CLAUDE_CODE_MESSAGING_TOKEN : undefined);
  if (token && !opts["no-auth"]) frames.push({ type: "auth", token });

  let body = text;
  if (opts["from-name"] !== undefined) {
    let attrs = reply ? ` from="${reply.address}"` : "";
    attrs += ` from-name="${opts["from-name"].replace(/["<>]/g, "")}"`;
    body = `<cross-session-message${attrs}>\n${body}\n</cross-session-message>`;
  }
  const msg = { msgV: 1, msg_id: randomUUID() };
  if (sessionId) msg.session_id = sessionId;
  if (reply) msg.from = reply.address;
  Object.assign(msg, { type: "user", message: { role: "user", content: body }, priority: opts.priority });
  frames.push(msg);

  try {
    await post(sockPath, frames);
  } catch (e) {
    reply?.close();
    console.error(`send.mjs: cannot post to ${sockPath}: ${e.message}`);
    return 1;
  }
  console.error(`sent 1 frame(s) to ${sockPath}`);
  console.error(`  user ${msg.priority} msg_id=${msg.msg_id}`);

  if (!reply) return 0;
  await new Promise((resolve) => setTimeout(resolve, wait * 1000));
  reply.close();
  let rc = 0;
  for (const f of reply.frames) {
    if (f.action !== "peer_message_status") continue;
    if (["held", "expired", "denied", "dropped"].includes(f.status)) rc = 3;
    else if (f.status === "delivered") rc = 0; // a held message was approved after all
  }
  return rc;
}

process.exitCode = await main();
