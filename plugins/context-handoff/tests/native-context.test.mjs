import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  NATIVE_CONTEXT_TOOLS, parseSettings, readNativeSetting, offeredNativeTools,
  selectNativeContext, createNativeContextObserver, nativePressurePrompt,
} from "../extensions/context-handoff/native-context.mjs";

const full = { tools: NATIVE_CONTEXT_TOOLS.map((name) => ({ name })) };

test("selection matrix separates opt-in, unavailable, and unknown", () => {
  for (const setting of [true, false, null]) {
    for (const availability of ["available", "unavailable", "unknown"]) {
      assert.equal(selectNativeContext(setting, availability).native,
        setting !== false && availability !== "unavailable" &&
        (setting === true || availability === "available"));
    }
  }
  assert.equal(offeredNativeTools(full), "available");
  assert.equal(offeredNativeTools({ tools: full.tools.slice(1) }), "unavailable");
  assert.equal(offeredNativeTools({ tools: null }), "unknown");
  assert.equal(offeredNativeTools({}), "unknown");
  assert.equal(offeredNativeTools({ tools: full.tools.map((t) =>
    ({ ...t, mcpServerName: "external" })) }), "unavailable");
});

test("JSONC preserves credential-shaped strings and rejects malformed input", () => {
  assert.deepEqual(parseSettings('\uFEFF{/*x*/"url":"https://x/*z*/",// line\n' +
    '"quoted":"\\\"//", "contextManagementTools":true,}'),
  { url: "https://x/*z*/", quoted: '"//', contextManagementTools: true });
  assert.throws(() => parseSettings("{/*"));
  assert.throws(() => parseSettings("[]"));
  assert.throws(() => parseSettings('{"contextManagementTools":tru}'));
  assert.throws(() => parseSettings('{"contextManagementTools":true,"x":[,]}'));
  assert.equal(parseSettings('{//comment\r"contextManagementTools":true\r}').contextManagementTools, true);
});

test("settings layer precedence, explicit false and errors", async () => {
  const root = await mkdtemp(join(tmpdir(), "native-settings-"));
  try {
    await mkdir(join(root, ".git"));
    await mkdir(join(root, ".github", "copilot"), { recursive: true });
    const home = join(root, "home");
    await mkdir(home);
    await writeFile(join(home, "settings.json"), '{"contextManagementTools":true}');
    await writeFile(join(root, ".github", "copilot", "settings.local.json"),
      '{"contextManagementTools":false,}');
    let setting = await readNativeSetting(root, { configHome: home });
    assert.equal(setting.enabled, false);
    assert.deepEqual(setting.warnings, []);
    await writeFile(join(root, ".github", "copilot", "settings.local.json"),
      '{"contextManagementTools":"true"}');
    setting = await readNativeSetting(root, { configHome: home });
    assert.equal(setting.enabled, null);
    assert.match(setting.warnings[0], /invalid-settings/);
    assert.doesNotMatch(setting.warnings[0], /"true"/);
    await rm(join(root, ".github", "copilot", "settings.local.json"));
    await writeFile(join(home, "settings.local.json"), '{"contextManagementTools":false}');
    assert.equal((await readNativeSetting(root, { configHome: home })).enabled, false);
    await rm(join(home, "settings.local.json"));
    await writeFile(join(home, "config.json"), '{"contextManagementTools":false}');
    assert.equal((await readNativeSetting(root, { configHome: home })).enabled, false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("user settings can follow a legitimate symlink", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "native-symlink-"));
  try {
    const target = join(root, "target.json");
    await writeFile(target, '{"contextManagementTools":true}');
    try { await symlink(target, join(root, "settings.json"), "file"); }
    catch (error) {
      if (error.code === "EPERM" || error.code === "EACCES") {
        t.skip("host cannot create file symlinks");
        return;
      }
      throw error;
    }
    assert.equal((await readNativeSetting(root, { configHome: root })).enabled, true);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("observer rechecks changes, bounds failures and retains flag on unknown", async () => {
  let calls = 0;
  let metadata = full;
  const observer = createNativeContextObserver({
    readSetting: async () => ({ enabled: true, warnings: [] }),
    getMetadata: async () => { calls++; return metadata; },
  });
  assert.equal((await observer.observe("/repo")).native, true);
  metadata = { tools: [] };
  assert.equal((await observer.observe("/repo")).native, true);
  observer.invalidate();
  const unavailable = await observer.observe("/repo");
  assert.equal(unavailable.native, false);
  assert.match(unavailable.warnings[0], /enabled in settings.*unavailable/);
  assert.equal(calls, 2);
  const timeout = createNativeContextObserver({
    readSetting: async () => ({ enabled: true, warnings: [] }),
    getMetadata: () => new Promise(() => {}),
    timeoutMs: 5,
  });
  const observation = await timeout.observe("/repo");
  assert.equal(observation.native, true);
  assert.equal(observation.availability, "unknown");
  assert.equal(observation.warnings.length, 1);
  assert.match(nativePressurePrompt({ utilization: "80%", tokens: "800/1000" },
    "hard", observation), /exactly one next action/);
  const slowSettings = createNativeContextObserver({
    readSetting: () => new Promise(() => {}), getMetadata: async () => full,
    timeoutMs: 5,
  });
  const recovered = await slowSettings.observe("/repo");
  assert.equal(recovered.native, true);
  assert.match(recovered.warnings[0], /settings observation failed/);
  const disabled = createNativeContextObserver({
    readSetting: async () => ({ enabled: false, warnings: [] }),
    getMetadata: async () => full,
  });
  assert.match((await disabled.observe("/repo")).warnings[0], /explicit settings opt-out/);
});

test("native pressure defers checkpoint persistence and ordering to the host", () => {
  for (const level of ["soft", "hard"]) {
    const prompt = nativePressurePrompt(
      { utilization: "70%", tokens: "700/1000" }, level,
      { reason: "setting-enabled", availability: "unknown" });
    assert.match(prompt, new RegExp(`${level} checkpoint boundary`));
    assert.match(prompt, /built-in checkpoint instructions/);
    assert.match(prompt, /complete-read budget in UTF-8 bytes, not an assumed storage limit/);
    assert.match(prompt, /never overwrite from a truncated projection as if complete/);
    assert.match(prompt, /Confirm persistence from the mutation result/);
    assert.match(prompt, /single-mutation ordering when rollover is imminent/);
    assert.match(prompt, /new_context binds the private snapshot itself/);
    assert.doesNotMatch(prompt, /Verify the checkpoint and host-required snapshot binding/);
    assert.match(prompt, /original approval evidence\/limits/);
    assert.match(prompt, /exactly one next action/);
  }
});

async function runExtensionFixture(fixtureName, configText) {
  const root = await mkdtemp(join(tmpdir(), "native-extension-"));
  try {
    await mkdir(join(root, ".git"));
    await mkdir(join(root, ".context-handoff"));
    await writeFile(join(root, ".context-handoff", "config.yaml"), configText);
    await writeFile(join(root, "settings.json"), '{"contextManagementTools":true}');
    const fixture = fileURLToPath(new URL(`./fixtures/${fixtureName}`, import.meta.url));
    const loader = new URL("./fixtures/native-sdk-loader.mjs", import.meta.url).href;
    const extension = fileURLToPath(new URL("../extensions/context-handoff/extension.mjs", import.meta.url));
    const result = spawnSync(process.execPath,
      ["--experimental-loader", loader, fixture, extension], {
        cwd: root, encoding: "utf8", windowsHide: true, timeout: 15000,
        env: { ...process.env, COPILOT_HOME: root, HOME: root, USERPROFILE: root,
          COPILOT_AGENT_SESSION_ID: "" },
      });

    assert.equal(result.status, 0, result.stderr || result.stdout);
  } finally { await rm(root, { recursive: true, force: true }); }
}

test("real extension event path suppresses force and resets only root windows", async () => {
  await runExtensionFixture("native-extension.mjs", "mode: auto\nnative_pressure: nudge\n");
});

test("production extension registers durable capture, reset replay and the complete paged reader", async () => {
  await runExtensionFixture("intent-extension.mjs", "mode: manual-only\n");
});

test("bound config overrides launch config, reloads live, and emergency store skips Goal discovery", async () => {
  const root = await mkdtemp(join(tmpdir(), "intent-bound-"));
  try {
    const bound = join(root, "bound");
    await mkdir(join(root, ".git"));
    await mkdir(join(root, ".context-handoff"));
    await writeFile(join(root, ".context-handoff", "config.yaml"), "mode: auto\n");
    await mkdir(join(bound, ".git"), { recursive: true });
    await mkdir(join(bound, ".context-handoff"));
    await writeFile(join(bound, ".context-handoff", "config.yaml"), "mode: off\n");
    await writeFile(join(bound, "effort.md"), "# Work\n\n## Goal\n\nBound Goal.\n");
    await writeFile(join(root, "settings.json"), '{"contextManagementTools":true}');
    const fixture = fileURLToPath(new URL("./fixtures/intent-bound-extension.mjs", import.meta.url));
    const loader = new URL("./fixtures/intent-bound-loader.mjs", import.meta.url).href;
    const extension = fileURLToPath(new URL("../extensions/context-handoff/extension.mjs", import.meta.url));
    for (const variant of ["immediate", "delayed", "file", "task", "checkpoint"]) {
      await writeFile(join(bound, ".context-handoff", "config.yaml"), "mode: off\n");
      await writeFile(join(root, "settings.json"), '{"contextManagementTools":true}');
      const result = spawnSync(process.execPath, ["--experimental-loader", loader, fixture, extension], {
        cwd: root, encoding: "utf8", windowsHide: true, timeout: 15000,
        env: { ...process.env, COPILOT_HOME: root, HOME: root, USERPROFILE: root,
          FIXTURE_BOUND_CWD: bound, FIXTURE_DELAYED_ID: variant === "immediate" ? "" : "true",
          FIXTURE_CONSUME: ["file", "task", "checkpoint"].includes(variant) ? variant : "",
          COPILOT_AGENT_SESSION_ID: "" },
      });
      assert.equal(result.status, 0, result.stderr || result.stdout);
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("native-selected sessions defer automatic pressure nudges to the host by default", async () => {
  await runExtensionFixture("native-defer-extension.mjs", "mode: auto\n");
});

test("live config reload re-arms tiers, rejects live-window misordering, and reports status", async () => {
  await runExtensionFixture("live-control-extension.mjs", "mode: auto\nnative_pressure: nudge\n");
});
