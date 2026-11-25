import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { loadExtensionSdk } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/sdk-compat.mjs";

test("the tool-free global Driver entrypoint requests no permission handler capability", async () => {
  const source = readFileSync(
    new URL("../extensions/agent-remote-driver/driver-extension.mjs", import.meta.url), "utf8",
  );
  const begin = source.indexOf("const session = await joinSession");
  const end = source.indexOf("\n  const token =", begin);
  assert.ok(begin >= 0 && end > begin);
  const calls = [];
  const session = {};
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const join = new AsyncFunction("joinSession", "approveAll", source.slice(begin, end) + "\nreturn session;");
  assert.equal(await join(async (...args) => { calls.push(args); return session; },
    () => ({ kind: "approved" })), session);
  assert.deepEqual(calls, [[]]);
});

function missing(name) {
  return Object.assign(new Error(`Cannot find package '${name}' imported from fixture.mjs`), {
    code: "ERR_MODULE_NOT_FOUND",
  });
}

function fixture(modern) {
  const imports = [];
  const joins = [];
  const approveAll = () => ({ kind: "approved" });
  const session = {};
  const namespace = modern ? "@microsoft/copilot-sdk" : "@github/copilot-sdk";
  const importModule = async (name) => {
    imports.push(name);
    if (name === namespace) return { approveAll };
    if (name === `${namespace}/extension`) {
      return { joinSession: async (config) => { joins.push(config); return session; } };
    }
    throw missing("@microsoft/copilot-sdk");
  };
  return { imports, joins, approveAll, session, importModule };
}

test("current SDK receives grouped options with the original callbacks and contributions", async () => {
  const f = fixture(true);
  const hook = async (input) => ({ additionalContext: input.toolName });
  const tool = { name: "fixture_tool", handler: async (args) => args };
  const command = { name: "fixture_command", handler: async () => "command-result" };
  const workflows = [{}];
  const config = {
    tools: [tool], hooks: { onPreToolUse: hook, directories: ["fixture-hooks"] },
    commands: [command], onPermissionRequest: f.approveAll,
    permissions: { mode: "default" }, interaction: { input: "fixture" },
    requestedEnvironmentVariables: ["FIXTURE_OPTIONAL_VALUE"], workflows,
    futureOption: { value: true },
  };
  const sdk = await loadExtensionSdk(f.importModule);
  assert.equal(await sdk.joinSession(config), f.session);
  assert.deepEqual(f.imports, ["@microsoft/copilot-sdk/extension", "@microsoft/copilot-sdk"]);
  const [joined] = f.joins;
  assert.equal(joined.tools.definitions, config.tools);
  assert.equal(joined.hooks.handlers.onPreToolUse, hook);
  assert.deepEqual(joined.hooks.directories, ["fixture-hooks"]);
  assert.equal(joined.permissions.onRequest, f.approveAll);
  assert.equal(joined.permissions.mode, "default");
  assert.equal(joined.interaction.commands, config.commands);
  assert.equal(joined.interaction.input, "fixture");
  assert.equal(joined.workflows, workflows);
  assert.equal(joined.requestedEnvironmentVariables, config.requestedEnvironmentVariables);
  assert.equal(joined.futureOption, config.futureOption);
  assert.deepEqual(await joined.hooks.handlers.onPreToolUse({ toolName: "read" }), {
    additionalContext: "read",
  });
  assert.deepEqual(await joined.tools.definitions[0].handler({ value: 1 }), { value: 1 });
  assert.equal(await joined.interaction.commands[0].handler(), "command-result");
  assert.deepEqual(joined.permissions.onRequest(), { kind: "approved" });
  assert.equal(config.hooks.onPreToolUse, hook);
  assert(!("handlers" in config.hooks));
  assert(!("onRequest" in config.permissions));
});

test("previous SDK receives the exact original options and joins only once", async () => {
  const f = fixture(false);
  const sdk = await loadExtensionSdk(f.importModule);
  const config = { tools: [], hooks: { onPreToolUse() {} }, onPermissionRequest: f.approveAll };
  assert.equal(await sdk.joinSession(config), f.session);
  assert.equal(f.joins.length, 1);
  assert.equal(f.joins[0], config);
  assert.equal(sdk.approveAll, f.approveAll);
  assert.deepEqual(f.imports, [
    "@microsoft/copilot-sdk/extension", "@github/copilot-sdk/extension", "@github/copilot-sdk",
  ]);
});

test("grouped current options pass through without adding permissions", async () => {
  const f = fixture(true);
  const sdk = await loadExtensionSdk(f.importModule);
  const config = {
    tools: { definitions: [] }, hooks: { handlers: {} },
    interaction: { commands: [] }, permissions: { mode: "default" },
  };
  await sdk.joinSession(config);
  assert.deepEqual(f.joins[0], config);
  assert.equal(f.joins[0].tools, config.tools);
  assert.equal(f.joins[0].hooks, config.hooks);
  await sdk.joinSession();
  assert.equal(f.joins[1], undefined);
  await sdk.joinSession({});
  assert.deepEqual(f.joins[2], {});
});

test("conflicting option shapes fail before joining", async () => {
  const f = fixture(true);
  const sdk = await loadExtensionSdk(f.importModule);
  for (const config of [
    { hooks: { handlers: {}, onPreToolUse() {} } },
    { onPermissionRequest() {}, permissions: { onRequest() {} } },
    { commands: [], interaction: { commands: [] } },
  ]) {
    assert.throws(() => sdk.joinSession(config), /Conflicting SDK/);
  }
  assert.equal(f.joins.length, 0);
});

test("missing transitive dependencies, exports and evaluation errors never select the old SDK", async () => {
  for (const error of [
    missing("transitive-dependency"),
    Object.assign(new Error("Cannot find package '@microsoft/copilot-sdk-extra' imported from fixture"), {
      code: "ERR_MODULE_NOT_FOUND",
    }),
    Object.assign(new Error("package subpath is unavailable"), { code: "ERR_PACKAGE_PATH_NOT_EXPORTED" }),
    new SyntaxError("fixture evaluation failed"),
  ]) {
    const calls = [];
    await assert.rejects(loadExtensionSdk(async (name) => {
      calls.push(name);
      throw error;
    }), (actual) => actual === error);
    assert.deepEqual(calls, ["@microsoft/copilot-sdk/extension"]);
  }
});

test("a missing selected root or a failed join never retries through another SDK", async () => {
  const rootError = missing("@microsoft/copilot-sdk");
  const imports = [];
  await assert.rejects(loadExtensionSdk(async (name) => {
    imports.push(name);
    if (name.endsWith("/extension")) return { joinSession() {} };
    throw rootError;
  }), (error) => error === rootError);
  assert.deepEqual(imports, ["@microsoft/copilot-sdk/extension", "@microsoft/copilot-sdk"]);
  for (const modern of [false, true]) {
    const f = fixture(modern);
    const joinError = new Error("fixture join refused");
    let joins = 0;
    const sdk = await loadExtensionSdk(async (name) => {
      const exports = await f.importModule(name);
      return exports.joinSession ? {
        joinSession: async () => { joins++; throw joinError; },
      } : exports;
    });
    const count = f.imports.length;
    await assert.rejects(sdk.joinSession({ tools: [] }), (error) => error === joinError);
    assert.equal(joins, 1);
    assert.equal(f.imports.length, count);
  }
});
