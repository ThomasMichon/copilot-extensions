"""Exercise the actual event subscriptions with a bounded SDK event-source fixture."""

import os
from pathlib import Path
import shutil
import subprocess

from agent_procutil import no_window_kwargs


def test_root_messages_persist_without_intent_and_ignore_usage_or_delegate_activity(tmp_path):
    node = shutil.which("node")
    assert node, "message extension fixture requires Node"
    session = tmp_path / ".copilot" / "session-state" / "fixture-session"
    session.mkdir(parents=True)
    extension = Path(__file__).parents[1] / "extensions" / "agent-worktrees" / "extension.mjs"
    script = r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import vm from "node:vm";
const handlers = new Map();
const prompts = [];
const admission = Object.assign(async () => true, {current: () => true});
const synthetic = values => new vm.SyntheticModule(Object.keys(values), function() {
  for (const [key, value] of Object.entries(values)) this.setExport(key, value);
});
const module = new vm.SourceTextModule(readFileSync(process.argv[1], "utf8"), {
  initializeImportMeta: meta => {meta.url = pathToFileURL(process.argv[1]).href;},
});
await module.link(async name => {
  if (name === "@github/copilot-sdk") return synthetic({approveAll: () => true});
  if (name === "@github/copilot-sdk/extension") return synthetic({
    joinSession: async () => ({sessionId: "fixture-session", on: (key, fn) => handlers.set(key, fn),
      send: async message => {prompts.push(message);}}),
  });
  if (name.endsWith("managed-session.mjs")) return synthetic({
    createManagedSessionAdmission: () => admission,
  });
  if (name.endsWith("resident-pre-tool.mjs")) return synthetic({
    createPythonPolicy: () => {}, createResidentPreToolHook: () => {},
    selectResidentPreToolHooks: () => null,
  });
  if (name.endsWith("worktree-changed.mjs")) {
    name = new URL(name, pathToFileURL(process.argv[1])).href;
  }
  return synthetic(await import(name));
});
await module.evaluate();
await handlers.get("user.message")({timestamp: "2026-11-07T01:00:00Z", data: {content: "user"}});
await handlers.get("assistant.message")({
  timestamp: "2026-11-07T01:01:00Z", data: {content: "assistant"},
});
await handlers.get("assistant.message")({
  agentId: "delegate", timestamp: "2026-11-08T01:00:00Z", data: {content: "child"},
});
await new Promise(resolve => setTimeout(resolve, 2700));
const path = join(process.env.USERPROFILE || process.env.HOME, ".copilot",
  "session-state", "fixture-session", "substatus.json");
const before = JSON.parse(readFileSync(path, "utf8"));
assert.equal(before.messageActivityVersion, 1);
assert.equal(before.lastMessageAt, "2026-11-07T01:01:00.000Z");
assert.equal(before.intent, null);
await handlers.get("session.idle")({});
const after = JSON.parse(readFileSync(path, "utf8"));
assert.equal(after.lastMessageAt, before.lastMessageAt);
assert.equal(after.rest, "idle");
for (let i = 0; i < 3; i++) await handlers.get("tool.execution_complete")({});
for (let i = 0; i < 10; i++) {
  await handlers.get("assistant.turn_start")({});
  await handlers.get("assistant.turn_end")({});
}
assert.equal(prompts.length, 1);
assert.equal(prompts[0].source, "system");
assert.match(prompts[0].prompt, /<system_reminder>/);
"""
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--input-type=module", "-e", script, str(extension)],
        env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
             "AGENT_WORKTREES_STATUS_NUDGE": "1", "AGENT_BRIDGE_DRIVEN_BY": ""},
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
