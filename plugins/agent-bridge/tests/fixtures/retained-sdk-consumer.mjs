// Controlled SDK boundary for the real HTTP/SQLite process-loss tests.
// This is not native SDK qualification.
import { closeSync, existsSync, fsyncSync, openSync, readFileSync, writeSync } from "node:fs";
import { createInterface } from "node:readline";
import { createRetainedInbox } from "../../extensions/agent-bridge/retained-delivery.mjs";

const config = JSON.parse(process.argv[2]);
const replies = new Map();
const lines = createInterface({ input: process.stdin });
lines.on("line", (line) => {
  const reply = JSON.parse(line);
  const resolve = replies.get(reply.id);
  replies.delete(reply.id);
  resolve(reply);
});
let sequence = 0;
let clock = Date.now();
const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");
const identity = { pid: process.pid, process_started_at: Date.now() / 1000 - process.uptime() };
const durable = (path, value) => {
  const fd = openSync(path, "a");
  try {
    writeSync(fd, JSON.stringify(value) + "\n");
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
};
const request = async (method, path, body) => {
  const id = ++sequence;
  const reply = new Promise((resolve) => replies.set(id, resolve));
  emit({ id, method, path, body });
  const response = await reply;
  if (!response.ok) throw Object.assign(
    new Error(`synthetic transport HTTP ${response.status}`), { status: response.status },
  );
  const boundary = path.endsWith("/poll") ? "poll" : path.endsWith("/begin") ? "begin"
    : path.endsWith("/outcome") ? body.outcome : "";
  if (boundary && config.exitAt === boundary) process.exit(23);
  return response.body;
};
const session = {
  sessionId: config.sid,
  rpc: { metadata: { snapshot: async () => ({
    sessionId: config.sid, workingDirectory: config.cwd,
  }) } },
  getEvents: async () => existsSync(config.events)
    ? readFileSync(config.events, "utf8").split("\n").filter(Boolean).map(JSON.parse) : [],
  send: async (options) => {
    if (config.exitAt === "before-sdk") process.exit(23);
    durable(config.effects, options);
    if (config.exitAt === "sdk") process.exit(23);
    if (config.record) durable(config.events, {
      id: "native-event-a", type: "user.message", data: {
        source: options.source, content: options.prompt,
      },
    });
    return "sdk-message-a";
  },
  abort: async () => { throw new Error("unexpected abort"); },
};
if (!config.recoveryOnly) await request("POST", "/api/v1/live-sessions", {
  session_id: config.sid, machine: "synthetic-machine", ...identity,
});
const consumer = createRetainedInbox({
  session, request, processIdentity: identity, cwd: config.cwd,
  incarnation: () => config.incarnation, now: () => clock,
  log: (message) => emit({ log: message }),
});
await consumer.tick(config.sid, { allowSend: !config.recoveryOnly });
if (config.exitAt === "consumed" || config.retry) {
  clock += 30_000;
  await consumer.tick(config.sid, { allowSend: !!config.retry });
}
consumer.close();
emit({ done: true });
lines.close();
process.stdin.pause();
