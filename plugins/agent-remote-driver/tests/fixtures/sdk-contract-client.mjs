import assert from "node:assert/strict";
import { register } from "node:module";
import { pathToFileURL } from "node:url";

register(pathToFileURL(process.argv[2]).href);
const { loadExtensionSdk } = await import(pathToFileURL(process.argv[3]).href);
const { joinSession } = await loadExtensionSdk();
const calls = { tool: 0, hook: 0, permission: 0, command: 0 };
const session = await joinSession({
  tools: [{
    name: "fixture_tool", description: "Synthetic SDK contract control",
    parameters: { type: "object", properties: { value: { type: "string" } } },
    handler: async (args, invocation) => {
      assert.equal(invocation.sessionId, "sdk-contract-fixture");
      calls.tool++;
      return args.value;
    },
  }],
  hooks: { onPreToolUse: async () => {
    calls.hook++;
    return { additionalContext: "fixture-hook" };
  } },
  onPermissionRequest: async () => {
    calls.permission++;
    return { kind: "denied-interactively-by-user" };
  },
  commands: [{ name: "fixture-command", description: "Synthetic command", handler: async (input) => {
    assert.equal(input.args, "fixture-args");
    calls.command++;
  } }],
});
assert.equal(session.sessionId, "sdk-contract-fixture");
assert.equal((await session.rpc.metadata.snapshot()).sessionId, session.sessionId);
assert.deepEqual(calls, { tool: 1, hook: 1, permission: 1, command: 1 });
process.exit(0);
