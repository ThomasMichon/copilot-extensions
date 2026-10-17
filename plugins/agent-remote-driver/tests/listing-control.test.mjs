import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

test("the actual aggregated reporting CLI omits both observation and re-hook credentials", () => {
  const root = mkdtempSync(join(tmpdir(), "driver-list-redaction-"));
  try {
    mkdirSync(join(root, "sessions"));
    writeFileSync(join(root, "sessions", "s.json"), JSON.stringify({
      version: 1, sessionId: "s", pid: process.pid, updatedAt: new Date().toISOString(),
      host: "127.0.0.1", port: 1, token: "sensitive-observation-value", controlToken: "sensitive-admin-value",
      controlProtocol: 2, instanceId: "i",
    }));
    const result = spawnSync(process.execPath, [
      fileURLToPath(new URL("../bin/list-sessions.mjs", import.meta.url)), "--json", "--no-probe",
    ], { env: { ...process.env, AGENT_REMOTE_DRIVER_CONFIG_DIR: root }, encoding: "utf8", windowsHide: true, timeout: 5_000 });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.includes("sensitive-"), false);
    const rows = JSON.parse(result.stdout);
    assert.equal(rows[0].controlProtocol, 2);
    assert.equal(Object.hasOwn(rows[0], "token"), false);
    assert.equal(Object.hasOwn(rows[0], "controlToken"), false);
  } finally {
    rmSync(root, { recursive: true });
  }
});
