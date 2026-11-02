// Exact rejected helper from fb6d1592e, retained only for the historical
// counterexample. This is not imported by the plugin or its supported tests.
import { execFile } from "node:child_process";
import { join } from "node:path";

export function runReceiptChild(bin, args, options) {
  return new Promise((resolve, reject) => {
    const { signal, timeout, ...childOptions } = options;
    signal?.throwIfAborted();
    let outcome;
    let stopping = false;
    let cleanup = Promise.resolve();
    const child = execFile(bin, args, {
      ...childOptions, timeout: 0, detached: process.platform !== "win32",
    }, (error, stdout, stderr) => {
      outcome = { error, stdout, stderr };
    });
    function stop() {
      if (stopping || !child.pid) return;
      stopping = true;
      if (process.platform === "win32") {
        cleanup = new Promise((done) => {
          execFile(join(process.env.SystemRoot || "C:\\Windows", "System32", "taskkill.exe"), [
            "/PID", String(child.pid), "/T", "/F",
          ], { windowsHide: true, timeout: 500, killSignal: "SIGKILL" }, () => {
            child.kill("SIGKILL");
            done();
          });
        });
      } else {
        try { process.kill(-child.pid, "SIGKILL"); }
        catch { child.kill("SIGKILL"); }
      }
    }
    const timer = setTimeout(stop, timeout);
    signal?.addEventListener("abort", stop, { once: true });
    child.once("close", async () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", stop);
      await cleanup;
      if (stopping) {
        const error = new Error("observation-child-stopped");
        error.code = signal?.aborted ? "ABORT_ERR" : "ETIMEDOUT";
        reject(error);
      }
      else if (outcome?.error) reject(outcome.error);
      else if (outcome) resolve({ stdout: outcome.stdout, stderr: outcome.stderr });
      else reject(new Error("observation-child-closed"));
    });
  });
}
