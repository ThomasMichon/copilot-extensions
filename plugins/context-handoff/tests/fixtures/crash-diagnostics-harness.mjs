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
const diagnostics = installEmergencyDiagnostics(emergencyLog);
process.stdout.write("READY\n");

switch (scenario) {
  case "clean-exit":
    process.exit(0);
    break;
  case "exit-1-directly":
    // Regression case: a process can terminate with a non-zero code
    // without ever raising an exception, rejection, or signal (e.g. a bare
    // process.exit(1) elsewhere in the real extension). onExit() must still
    // route this through the same log-or-stderr-fallback path as every
    // other failure case, not silently allow the file to go unwritten with
    // no fallback either.
    process.exit(1);
    break;
  case "uncaught-exception":
    setImmediate(() => {
      throw new Error("boom-uncaught");
    });
    break;
  case "uncaught-exception-after-ready":
    // Regression case for markReady(): once joinSession() (or, here, the
    // harness standing in for it) has resolved, a subsequent captured
    // failure's log entry must reflect ready=true -- proving the flag
    // actually threads through to installEmergencyDiagnostics()'s handlers
    // rather than only ever defaulting to false.
    diagnostics.markReady();
    setImmediate(() => {
      throw new Error("boom-after-ready");
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
  case "throw-non-error-string":
    // Regression case for describeFailure()'s OTHER branch: a thrown value
    // with no .stack property at all (string, number, plain object, ...)
    // falls through to String(value) instead of Error.stack -- this must
    // actually be exercised, not just the hostile-.stack-getter path above.
    setImmediate(() => {
      throw "boom-plain-string";
    });
    break;
  case "throw-hostile-tostring":
    // Regression case: an object with NO .stack property (so
    // describeFailure() falls through past the .stack branch) but a
    // hostile Symbol.toPrimitive/toString that itself throws when
    // String(value) tries to coerce it -- describeFailure()'s own outer
    // try/catch must still produce the fixed fallback string rather than
    // crashing the handler, distinct from the .stack-getter scenario above.
    setImmediate(() => {
      throw {
        [Symbol.toPrimitive]() {
          throw new Error("bad toPrimitive");
        },
        toString() {
          throw new Error("bad toString");
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
