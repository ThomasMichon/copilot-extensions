import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { ensureInstalledService } from "../extensions/agent-remote-driver/service-startup.mjs";

function fixture(t, platform = process.platform) {
  const root = mkdtempSync(join(tmpdir(), "driver-startup-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const generation = "0.1.0-dev1+source.fixture";
  const slot = join(root, "versions", generation);
  const bindingPath = join(root, "binding.json");
  const binding = {
    schema: "copilot-driver.service.startup", version: 1, root, generation,
    argv: [
      join(root, platform === "win32" ? "pythonw.exe" : "python"),
      "-I", "-S", "-B", join(slot, ".runtime-bootstrap", "runtime_launcher.py"),
      ...(platform === "win32" ? ["--gui-runtime"] : []),
      "--runtime-identity", "a".repeat(32) + ":" + "b".repeat(64),
      "-m", "agent_remote_driver_app", "service", "ensure",
    ],
  };
  writeFileSync(join(root, "current-version"), generation);
  const save = () => writeFileSync(bindingPath, JSON.stringify(binding));
  save();
  return { root, binding, bindingPath, save };
}

for (const platform of ["win32", "linux"]) {
  test(`startup uses only its selected retained application (${platform})`, async t => {
    const { root, binding, bindingPath } = fixture(t, platform);
    const calls = [];
    const observed = await ensureInstalledService({
      bindingPath, platform,
      run: async (executable, argv, options) => {
        calls.push({ executable, argv, options });
        return { stdout: JSON.stringify({
          contract: "copilot-driver.service.commands", version: 1, state: "running",
          generation: binding.generation, instance_id: "original-instance", pid: 123,
        }) };
      },
    });
    assert.equal(observed.state, "ensured");
    assert.equal(calls.length, 1);
    assert.deepEqual([calls[0].executable, ...calls[0].argv], binding.argv);
    assert.equal(calls[0].options.cwd, root);
    assert.equal(calls[0].options.env.AGENT_REMOTE_DRIVER_HOME, root);
    assert.equal(calls[0].options.windowsHide, true);
    assert.equal(calls[0].options.timeout, 12000);
    assert.equal(calls[0].options.maxBuffer, 65536);
    assert.equal(calls[0].options.shell, undefined);
  });
}

for (const changed of ["selection", "traversal", "command", "arguments", "oversize", "missing"]) {
  test(`startup refuses ${changed} without a process or fallback`, async t => {
    const { root, binding, bindingPath, save } = fixture(t);
    if (changed === "selection") writeFileSync(join(root, "current-version"), "new-generation");
    if (changed === "traversal") binding.generation = "../outside";
    if (changed === "command") binding.argv[binding.argv.length - 1] = "install";
    if (changed === "arguments") binding.argv.push("--unexpected");
    save();
    if (changed === "oversize") writeFileSync(bindingPath, "x".repeat(16385));
    if (changed === "missing") rmSync(bindingPath);
    const observed = await ensureInstalledService({
      bindingPath, run: () => assert.fail("unadmitted process"),
    });
    assert.equal(observed.state, changed === "missing" ? "not-configured" : "unavailable");
  });
}

for (const failed of ["timeout", "wrong-generation", "stopped", "unstructured"]) {
  test(`startup keeps ${failed} unavailable and never replays`, async t => {
    const { binding, bindingPath } = fixture(t);
    let calls = 0;
    const observed = await ensureInstalledService({
      bindingPath,
      run: async () => {
        calls++;
        if (failed === "timeout") throw new Error("original child timeout");
        return { stdout: failed === "unstructured" ? "{}" : JSON.stringify({
          contract: "copilot-driver.service.commands", version: 1,
          state: failed === "stopped" ? "stopped" : "running",
          generation: failed === "wrong-generation" ? "other" : binding.generation,
          instance_id: "original-instance", pid: 123,
        }) };
      },
    });
    assert.equal(calls, 1);
    assert.equal(observed.state, "unavailable");
  });
}
