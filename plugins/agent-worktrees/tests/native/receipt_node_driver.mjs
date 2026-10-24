// The harness launches this process; the production observation may launch none.
import childProcess from "node:child_process";
import dns from "node:dns";
import net from "node:net";
import { syncBuiltinESMExports } from "node:module";
import { readFileSync, watch } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { nativeClientMetadata } from "../../../context-handoff/tests/fixtures/native-client-metadata.mjs";

let helperCalls = 0;
let socketCalls = 0;
const connect = net.createConnection;
net.createConnection = (...args) => { socketCalls++; return connect(...args); };
for (const key of ["exec", "execFile", "spawn", "fork", "execSync", "execFileSync", "spawnSync"]) {
  childProcess[key] = () => { helperCalls++; throw new Error("helper forbidden"); };
}
dns.lookup = () => { throw new Error("DNS forbidden"); };
syncBuiltinESMExports();
const source = join(process.env.RECEIPT_CLIENT_ROOT, "extensions", "context-handoff", "handoff-core.mjs");
const { observeHandoffPromptReceived } = await import(pathToFileURL(source));
const { registerNativePromptReceiptObservation } = await import(pathToFileURL(
  join(process.env.RECEIPT_CLIENT_ROOT, "extensions", "context-handoff", "prompt-receipt.mjs"),
));
const options = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const pending = options.pending || {};
const started = performance.now();
const controller = new AbortController();
if (options.cancelBefore) controller.abort();
const watcher = options.cancelFile ? watch(options.cancelFile, () => controller.abort()) : null;
const observe = (state, locator = options.locator) => observeHandoffPromptReceived({
  sessionDir: options.sessionDir, sid: options.sessionId, locator, pending: state,
  signal: controller.signal,
  // Isolated transport/backend lane, not native SDK persistence qualification.
  beforeRecord: async () => ({ verified: true }),
});
let result;
let callbackMs;
let callbackBeforeIO;
if (options.callback) {
  const metadata = nativeClientMetadata({ sessionId: options.sessionId });
  const session = metadata.client();
  const state = { turnCount: 0, sessionId: options.sessionId, cwd: process.cwd() };
  const observation = registerNativePromptReceiptObservation(session, state, { sessionDir: options.sessionDir });
  const callbackStarted = performance.now();
  session.emit("user.message", { data: { content: `Recovery: context-handoff ${options.locator}` } });
  callbackMs = performance.now() - callbackStarted;
  callbackBeforeIO = socketCalls === 0;
  try {
    while (["idle", "pending"].includes(observation.status.status)) {
      if (performance.now() - started > 3000) throw new Error("callback harness did not settle");
      await delay(5);
    }
    result = observation.status;
  } finally { await observation.dispose(); }
} else {
  result = options.concurrent
    ? { status: "batch", results: await Promise.all(Array.from({ length: options.concurrent },
      (_, index) => observe({}, options.distinct ? `${options.locator}-${index}` : options.locator))) }
    : await observe(pending);
}
watcher?.close();
console.log(JSON.stringify({
  status: result.status, reason: result.reason, replayed: result.replayed,
  results: result.results, elapsedMs: performance.now() - started,
  helperCalls, socketCalls, callbackMs, callbackBeforeIO, pending,
  metadataMode: options.callback ? "sdk-shaped-rpc-owner-double" : "explicit-transport-fixture-gate",
}));
