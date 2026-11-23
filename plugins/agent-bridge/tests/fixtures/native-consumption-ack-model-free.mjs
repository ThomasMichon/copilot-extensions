import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createInterface } from "node:readline";

const config = JSON.parse(process.argv[2]);
const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");
const replies = new Map();
const lines = createInterface({ input: process.stdin });
lines.on("line", (line) => {
  const reply = JSON.parse(line);
  const resolve = replies.get(reply.id);
  replies.delete(reply.id);
  resolve(reply);
});
let sequence = 0;
globalThis.fetch = async (location, options) => {
  if (!options.headers.Authorization.endsWith(config.token)) {
    throw Error("The actual endpoint header lost the fixture value");
  }
  const url = new URL(location);
  const id = ++sequence;
  const response = new Promise((resolve) => replies.set(id, resolve));
  emit({
    id, method: options.method, path: url.pathname + url.search,
    body: options.body ? JSON.parse(options.body) : undefined,
  });
  const reply = await response;
  return new Response(JSON.stringify(reply.body), { status: reply.status });
};
let observer;
globalThis.__nativeAckFixtureSession = {
  rpc: { metadata: { snapshot: async () => ({ sessionId: config.sid }) } },
  on: (callback) => { observer = callback; return () => {}; },
  send: async (options) => {
    queueMicrotask(() => observer({
      id: "model-free-native-event", type: "user.message", timestamp: "2030-01-02T03:04:05.000Z",
      data: { content: options.prompt, source: options.source },
    }));
    return "sdk-accepted";
  },
};
await import(config.module);
const deadline = setTimeout(() => {
  emit({ log: "Model-free native fixture protocol exceeded its deadline" });
  process.exitCode = 1;
  clearInterval(timer);
  lines.close();
  process.stdin.pause();
}, 10_000);
let terminalAt;
const timer = setInterval(() => {
  const paths = readdirSync(config.directory).filter((name) => /^status-\d+\.json$/.test(name)).sort();
  if (!paths.length) return;
  const state = JSON.parse(readFileSync(join(config.directory, paths.at(-1)), "utf8"));
  if ((!state.error && !state.finished) || replies.size) return;
  terminalAt ??= Date.now();
  if (!state.error && Date.now() - terminalAt < 250) return;
  clearTimeout(deadline);
  clearInterval(timer);
  emit({ log: state });
  emit({ done: true });
  if (state.error) process.exitCode = 1;
  lines.close();
  process.stdin.pause();
}, 10);
