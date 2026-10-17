import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

test("production extension establishes ownership observation before publishing a writable endpoint", () => {
  const result = spawnSync(process.execPath, [
    "--loader", new URL("./fixtures/extension-sdk-loader.mjs", import.meta.url).href,
    fileURLToPath(new URL("./fixtures/extension-control-probe.mjs", import.meta.url)),
  ], { encoding: "utf8", windowsHide: true, timeout: 10_000 });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /PASS: production extension wiring/);
});

test("production extension refuses mutation when input observation cannot be installed", () => {
  const result = spawnSync(process.execPath, [
    "--loader", new URL("./fixtures/extension-sdk-loader.mjs", import.meta.url).href,
    fileURLToPath(new URL("./fixtures/extension-control-probe.mjs", import.meta.url)),
  ], {
    encoding: "utf8", windowsHide: true, timeout: 10_000,
    env: { ...process.env, PROBE_OBSERVER_UNAVAILABLE: "1" },
  });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /PASS: unavailable production observer disables mutation/);
});
