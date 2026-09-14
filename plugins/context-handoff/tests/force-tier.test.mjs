import { test } from "node:test";
import assert from "node:assert/strict";

import {
  FORCE_TIER_DENY_FEEDBACK,
  isReadOnlyPermissionRequest,
} from "../extensions/context-handoff/force-tier.mjs";

test("read and url requests are always read-only", () => {
  assert.equal(isReadOnlyPermissionRequest({ kind: "read", path: "/x" }), true);
  assert.equal(isReadOnlyPermissionRequest({ kind: "url", url: "https://x" }), true);
});

test("write requests are never read-only", () => {
  assert.equal(
    isReadOnlyPermissionRequest({ kind: "write", fileName: "/x", diff: "" }),
    false,
  );
});

test("shell requests are read-only only when every parsed command is", () => {
  const allReadOnly = {
    kind: "shell",
    fullCommandText: "cat foo.txt | grep bar",
    commands: [
      { identifier: "cat", readOnly: true },
      { identifier: "grep", readOnly: true },
    ],
  };
  const mixed = {
    kind: "shell",
    fullCommandText: "cat foo.txt && rm foo.txt",
    commands: [
      { identifier: "cat", readOnly: true },
      { identifier: "rm", readOnly: false },
    ],
  };
  const noParsedCommands = { kind: "shell", fullCommandText: "", commands: [] };

  assert.equal(isReadOnlyPermissionRequest(allReadOnly), true);
  assert.equal(isReadOnlyPermissionRequest(mixed), false);
  // Fail closed (treat as mutating) when the parser found nothing to vouch for.
  assert.equal(isReadOnlyPermissionRequest(noParsedCommands), false);
});

test("mcp requests defer to the SDK's own readOnly flag", () => {
  assert.equal(
    isReadOnlyPermissionRequest({ kind: "mcp", toolName: "search", readOnly: true }),
    true,
  );
  assert.equal(
    isReadOnlyPermissionRequest({ kind: "mcp", toolName: "write_file", readOnly: false }),
    false,
  );
});

test("custom-tool, memory, hook, and extension-management requests are mutating by default", () => {
  for (const kind of [
    "custom-tool", "memory", "hook", "extension-management", "extension-permission-access",
  ]) {
    assert.equal(isReadOnlyPermissionRequest({ kind }), false, kind);
  }
});

test("missing or unrecognized kind fails closed to mutating", () => {
  assert.equal(isReadOnlyPermissionRequest({}), false);
  assert.equal(isReadOnlyPermissionRequest(null), false);
  assert.equal(isReadOnlyPermissionRequest(undefined), false);
});

test("deny feedback names the force threshold and the read-only exception", () => {
  assert.match(FORCE_TIER_DENY_FEEDBACK, /force threshold/i);
  assert.match(FORCE_TIER_DENY_FEEDBACK, /read-only/i);
});
