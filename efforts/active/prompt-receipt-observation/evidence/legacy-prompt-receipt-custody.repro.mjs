// Required negative-path qualification for #7942; uses Python descendants so
// Node's own child lifetime machinery cannot mask missing adapter custody.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { runReceiptChild } from "./legacy-receipt-child.mjs";

for (const mode of ["missing-taskkill", "output-overflow"]) {
  test(`receipt custody: ${mode} must not leave a Python descendant`, {
    skip: mode === "missing-taskkill" && process.platform !== "win32",
  }, async (t) => {
    const root = mkdtempSync(join(process.cwd(), ".receipt-custody-"));
    const pidFile = join(root, "children.json");
    const originalRoot = process.env.SystemRoot;
    const env = { ...process.env };
    const python = env.RECEIPT_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");
    let children;
    let operation;
    let settled = false;
    let descendantAlive = false;
    try {
      if (mode === "missing-taskkill") process.env.SystemRoot = root;
      const script = [
        "import subprocess,sys,os,json,pathlib,time",
        "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))",
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'root':os.getpid(),'descendant':c.pid}))",
        mode === "output-overflow" ? "sys.stdout.write('x'*100000);sys.stdout.flush()" : "pass",
        "time.sleep(60)",
      ].join(";");
      operation = runReceiptChild(python, ["-c", script, pidFile], {
        env, encoding: "utf-8", windowsHide: true,
        timeout: 1200, maxBuffer: 65536, killSignal: "SIGKILL",
      }).then(() => { settled = true; }, () => { settled = true; });
      await delay(2800);
      children = JSON.parse(readFileSync(pidFile, "utf-8"));
      try { process.kill(children.descendant, 0); descendantAlive = true; } catch {}
      t.diagnostic(JSON.stringify({ mode, settled, descendantAlive, observationMs: 2800 }));
    } finally {
      if (originalRoot === undefined) delete process.env.SystemRoot;
      else process.env.SystemRoot = originalRoot;
      for (const pid of Object.values(children || {})) {
        try { process.kill(pid, "SIGKILL"); } catch {}
      }
      await operation;
      rmSync(root, { recursive: true, force: true });
    }
    assert.equal(settled, true, "operation did not settle within the bounded observation");
    assert.equal(descendantAlive, false, "operation settled without retiring its owned descendant");
  });
}
