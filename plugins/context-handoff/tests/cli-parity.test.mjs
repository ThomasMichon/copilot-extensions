import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const plugin = join(dirname(fileURLToPath(import.meta.url)), "..");
const cli = join(
  plugin, "extensions", "context-handoff", "handoff-cli.mjs",
);

test("payload-local CLI exposes the extension fallback flow", () => {
  const result = spawnSync(process.execPath, [cli, "help"], {
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  for (const command of ["facts", "save", "cutover", "continue", "retry", "consume"]) {
    assert.match(result.stdout, new RegExp(`\\b${command}\\b`));
  }
  assert.match(result.stdout, /--locator/);
  assert.match(result.stdout, /--task-id/);
  assert.match(result.stdout, /--handoff-token/);
});

test("extension and CLI delegate lifecycle behavior to the same core", () => {
  const source = readFileSync(cli, "utf8");
  for (const shared of [
    "storeHandoff",
    "buildSeedForStored",
    "runHandoffCutover",
    "consumeFileHandoff",
    "consumeDispatchHandoffTask",
    "retryStoredHandoffCutover",
    "formatConsumeResult",
  ]) {
    assert.match(source, new RegExp(`\\b${shared}\\b`));
  }
  assert.match(
    source,
    /parsed\.kind === "task"[\s\S]*deferComplete = true/,
  );
  assert.doesNotMatch(source, /function (?:completeHandoffLifecycle|makeHandoffMetadata)/);
});

test("fallback remains payload-only with no installed runtime", () => {
  const manifest = JSON.parse(
    readFileSync(join(plugin, "plugin.json"), "utf8"),
  );
  assert.equal(manifest.runtimeScope, "none");
  assert.equal(manifest.extensions, undefined);
  assert.equal(existsSync(join(plugin, "pyproject.toml")), false);
  assert.equal(existsSync(join(plugin, "scripts", "install.sh")), false);
  assert.equal(existsSync(join(plugin, "scripts", "install.ps1")), false);
  assert.equal(
    readFileSync(join(plugin, "README.md"), "utf8").includes(
      "There is **no** installed runtime, venv, binstub",
    ),
    true,
  );
});
