#!/usr/bin/env node
// Test-only harness: installs crash-diagnostics against an injected log
// path, then deliberately triggers one failure scenario. Spawned as a real
// subprocess by tests/crash-diagnostics.test.mjs -- uncaughtException/
// unhandledRejection/signal handling and actual process exit codes must be
// observed from outside the process; letting these handlers run inside the
// shared `node --test` runner process would call process.exit() on the
// test runner itself.
import { createEmergencyLog, installEmergencyDiagnostics } from "../../extensions/context-handoff/crash-diagnostics.mjs";

const [, , scenario, logPath] = process.argv;
const emergencyLog = createEmergencyLog(logPath);
installEmergencyDiagnostics(emergencyLog);
process.stdout.write("READY\n");

switch (scenario) {
  case "clean-exit":
    process.exit(0);
    break;
  case "uncaught-exception":
    setImmediate(() => {
      throw new Error("boom-uncaught");
    });
    break;
  case "unhandled-rejection":
    setImmediate(() => {
      Promise.reject(new Error("boom-rejected"));
    });
    break;
  case "throw-hostile-getter":
    // Regression case: a thrown value is not required to be an Error, and
    // reading a hostile/buggy .stack getter (or a Symbol.toPrimitive/
    // toString that throws) must not itself crash the crash handler and
    // lose the diagnostic -- see describeFailure() in crash-diagnostics.mjs.
    setImmediate(() => {
      throw {
        get stack() {
          throw new Error("bad getter");
        },
      };
    });
    break;
  case "wait-for-signal":
    // The parent test sends the signal via child.kill(). A bare
    // process.on(signal, ...) registration does NOT by itself keep the
    // Node.js event loop alive (empirically confirmed: without this, the
    // process exits(0) via natural event-loop drain almost immediately
    // after printing READY, racing the parent's signal delivery and
    // failing intermittently/always depending on scheduling -- the real
    // extension.mjs never has this problem since its live joinSession() IPC
    // connection is its own independent keep-alive handle). This interval
    // is a harmless, real keep-alive for the test harness only; the signal
    // handler re-raises the signal after logging, so the process still
    // dies from that re-raised signal once no listener remains to catch
    // it -- this interval never prevents that.
    setInterval(() => {}, 60_000);
    break;
  default:
    process.stderr.write(`unknown scenario: ${scenario}\n`);
    process.exit(2);
}
