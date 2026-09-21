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
  case "wait-for-signal":
    // The parent test sends the signal via child.kill(); this process just
    // needs to stay alive long enough to receive it (the registered signal
    // listener itself keeps the event loop alive, so no extra keep-alive is
    // needed once installEmergencyDiagnostics() has run).
    break;
  default:
    process.stderr.write(`unknown scenario: ${scenario}\n`);
    process.exit(2);
}
