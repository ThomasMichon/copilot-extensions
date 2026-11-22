import { createInterface } from "node:readline";

const config = JSON.parse(process.argv[2]);
const { drainInbox, InFlightMessages } = await import(config.module);
const replies = new Map();
const lines = createInterface({ input: process.stdin });
lines.on("line", (line) => {
  const reply = JSON.parse(line);
  const resolve = replies.get(reply.id);
  replies.delete(reply.id);
  resolve(reply);
});
let sequence = 0;
const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");
const request = async (method, path, body) => {
  const id = ++sequence;
  const result = new Promise((resolve) => replies.set(id, resolve));
  emit({ id, method, path, body });
  const reply = await result;
  if (!reply.ok) throw new Error(`Fixture HTTP ${reply.status}`);
  return reply.body;
};
const inFlight = new InFlightMessages();
const sends = [];
const dependencies = {
  inFlight,
  getJson: (path) => request("GET", path),
  post: async (method, path, body) => {
    await request(method, path, body);
    return true;
  },
  session: { send: async (options) => {
    sends.push(options);
    return "sdk-accepted";
  } },
};
await drainInbox(config.sid, dependencies);
await request("GET", "/health");
await drainInbox(config.sid, dependencies);
if (sends.length !== 1) throw new Error("Unconsumed input was sent more than once");
const consumed = {
  id: "original-native-input",
  type: "user.message", data: { source: sends[0].source, content: sends[0].prompt },
};
inFlight.observe(consumed);
if (config.flushConsumption) {
  await request("POST", `/api/v1/live-sessions/${encodeURIComponent(config.sid)}/events`, {
    events: [consumed],
  });
}
await drainInbox(config.sid, dependencies);
emit({ log: {
  sends: sends.length, nativeConsumed: true, unacknowledged: inFlight.unacknowledgedConsumed.size,
} });
emit({ done: true });
lines.close();
process.stdin.pause();
