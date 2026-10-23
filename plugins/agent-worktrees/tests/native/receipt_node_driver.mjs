// The harness launches this process; the production observation may launch none.
import childProcess from "node:child_process";
import dns from "node:dns";
import { syncBuiltinESMExports } from "node:module";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

let helperCalls = 0;
for (const key of ["exec", "execFile", "spawn", "fork", "execSync", "execFileSync", "spawnSync"]) {
  childProcess[key] = () => { helperCalls++; throw new Error("helper forbidden"); };
}
dns.lookup = () => { throw new Error("DNS forbidden"); };
syncBuiltinESMExports();
const source = join(process.env.RECEIPT_CLIENT_ROOT, "extensions", "context-handoff", "handoff-core.mjs");
const { observeHandoffPromptReceived } = await import(pathToFileURL(source));
const options = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const pending = options.pending || {};
const started = performance.now();
const observe = (state) => observeHandoffPromptReceived({
  sessionDir: options.sessionDir, sid: options.sessionId, locator: options.locator, pending: state,
});
const result = options.concurrent
  ? { status: "batch", results: await Promise.all(Array.from({ length: options.concurrent }, () => observe({}))) }
  : await observe(pending);
console.log(JSON.stringify({
  status: result.status, reason: result.reason, replayed: result.replayed,
  results: result.results, elapsedMs: performance.now() - started, helperCalls, pending,
}));
