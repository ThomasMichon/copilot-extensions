import { test } from "node:test";
import assert from "node:assert/strict";
import {
  existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  consumeDispatchHandoffTask,
  consumeFileHandoff,
  decodeHandoffPayload,
  encodeHandoffPayload,
  makeHandoffMetadata,
  readSessionStateHandoff,
  writeJsonAtomic,
  writeSessionStateHandoff,
} from "../extensions/context-handoff/handoff-core.mjs";
import { captureHandoffIdentity } from "../extensions/context-handoff/handoff-identity.mjs";

function fixture(run) {
  const home = mkdtempSync(join(tmpdir(), "handoff-identity-"));
  const previous = { HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE };
  process.env.HOME = home;
  process.env.USERPROFILE = home;
  const stateDir = join(home, "state");
  const worktreeDir = join(home, "checkout", "wt-example");
  const activation = {
    version: 1, ordinal: 3, sourceIdentity: "native:example",
    startedAt: "2026-11-01T10:00:00Z", startRecordedAt: "2026-11-01T10:00:01Z",
  };
  const binding = (sid, changes = {}) => {
    const dir = join(home, ".copilot", "session-state", sid);
    mkdirSync(dir, { recursive: true });
    writeJsonAtomic(join(dir, "worktree-binding.json"), {
      sessionId: sid, worktreeId: "wt-example", worktreeDir,
      project: "example", creationNonce: "original-incarnation",
      sessionActivation: activation, ...changes,
    });
  };
  const metadata = () => makeHandoffMetadata({
    sid: "predecessor", cwd: worktreeDir, storage: "file", title: "Continue",
  }, (key) => key === "worktree-dir" ? worktreeDir : stateDir);
  const request = (meta, id = meta.id, storage = "file") => writeSessionStateHandoff({
    sid: "predecessor", promptText: "brief", seed: "seed",
    stored: { storage, id, metadata: meta },
  });
  try {
    run({ home, stateDir, worktreeDir, activation, binding, metadata, request });
  } finally {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    rmSync(home, { recursive: true, force: true });
  }
}

test("metadata and predecessor request retain the original binding, not a later incarnation", () => {
  fixture(({ binding, metadata, request, activation }) => {
    binding("predecessor");
    const meta = metadata();
    binding("predecessor", { creationNonce: "later-incarnation" });
    const saved = request(meta).record;
    assert.deepEqual(meta.authorIdentity, {
      version: 1, project: "example", worktreeId: "wt-example",
      creationNonce: "original-incarnation", sessionId: "predecessor", activation,
    });
    assert.deepEqual(saved.authorIdentity, meta.authorIdentity);
    assert.deepEqual(decodeHandoffPayload(encodeHandoffPayload("brief", meta))
      .metadata.authorIdentity, meta.authorIdentity);
  });
});

for (const invalid of ["absent", "legacy", "wrong-session", "wrong-worktree", "oversized", "invalid"]) {
  test(`optional ${invalid} binding does not qualify or block ordinary consumption`, () => {
    fixture(({ home, binding, metadata, stateDir }) => {
      if (invalid !== "absent") {
        binding("predecessor", {
          ...(invalid === "legacy" ? { project: null, creationNonce: null } : {}),
          ...(invalid === "wrong-session" ? { sessionId: "other" } : {}),
          ...(invalid === "wrong-worktree" ? { worktreeId: "other" } : {}),
        });
        const path = join(home, ".copilot", "session-state", "predecessor", "worktree-binding.json");
        if (invalid === "oversized") writeFileSync(path, " ".repeat(16385));
        if (invalid === "invalid") writeFileSync(path, "{broken");
      }
      const meta = metadata();
      assert.equal(meta.authorIdentity, null);
      mkdirSync(stateDir, { recursive: true });
      const path = join(stateDir, "baton.json");
      writeJsonAtomic(path, { ...meta, consumed: false, promptText: "brief" });
      const result = consumeFileHandoff("/source", "consumer", meta.id, path, {
        promoteHead: () => { throw new Error("unavailable"); },
      });
      assert.equal(result.ok, true);
      assert.equal(result.metadata.consumerIdentity, null);
    });
  });
}

test("legacy or unsupported activation metadata never becomes an exact incarnation", () => {
  fixture(({ binding, metadata, activation }) => {
    for (const sessionActivation of [undefined, {}, { ...activation, version: 2 },
      { ...activation, ordinal: 0 }, { ...activation, sourceIdentity: "not an identity" }]) {
      binding("predecessor", { sessionActivation });
      const identity = metadata().authorIdentity;
      assert.equal(identity.creationNonce, "original-incarnation");
      assert.equal(identity.activation, null);
    }
  });
});

test("identity observation refuses path aliases and links without creating missing state", () => {
  fixture(({ home, binding, metadata }) => {
    assert.equal(captureHandoffIdentity("../predecessor", "wt-example"), null);
    assert.equal(captureHandoffIdentity("..", "wt-example"), null);
    assert.equal(captureHandoffIdentity("missing", "wt-example"), null);
    assert.equal(existsSync(join(home, ".copilot")), false);
    binding("actual", { sessionId: "predecessor" });
    const root = join(home, ".copilot", "session-state");
    symlinkSync(join(root, "actual"), join(root, "predecessor"), "junction");
    assert.equal(metadata().authorIdentity, null);
  });
});

test("file consume persists exact consumer before failed promotion and never refreshes it on replay", () => {
  fixture(({ binding, metadata, request, stateDir }) => {
    binding("predecessor");
    binding("consumer");
    const meta = metadata();
    request(meta);
    mkdirSync(stateDir, { recursive: true });
    const path = join(stateDir, "baton.json");
    writeJsonAtomic(path, { ...meta, consumed: false, promptText: "brief" });
    const options = { promoteHead: () => { throw new Error("unavailable"); } };
    const first = consumeFileHandoff("/source", "consumer", meta.id, path, options);
    const identity = first.metadata.consumerIdentity;
    assert.equal(first.ok, true);
    assert.equal(identity.sessionId, "consumer");
    assert.equal(identity.activation.ordinal, 3);
    assert.deepEqual(JSON.parse(readFileSync(path)).consumerIdentity, identity);
    assert.deepEqual(readSessionStateHandoff("predecessor").record.consumerIdentity, identity);

    binding("consumer", { sessionActivation: {
      ...identity.activation, ordinal: 4, sourceIdentity: "native:resumed",
    } });
    const replay = consumeFileHandoff("/source", "consumer", meta.id, path, options);
    assert.equal(replay.ok, true);
    assert.deepEqual(replay.metadata.consumerIdentity, identity);
    assert.deepEqual(readSessionStateHandoff("predecessor").record.consumerIdentity, identity);
    assert.equal(consumeFileHandoff("/source", "other", meta.id, path, options).ok, false);
  });
});

test("successful legacy file replay never backfills current identity", () => {
  fixture(({ binding, metadata, stateDir }) => {
    const meta = metadata();
    mkdirSync(stateDir, { recursive: true });
    const path = join(stateDir, "baton.json");
    writeJsonAtomic(path, {
      ...meta, consumed: true, consumedBySession: "consumer", consumedAt: "earlier",
    });
    binding("consumer");
    const result = consumeFileHandoff("/source", "consumer", meta.id, path, {
      promoteHead: () => {},
    });
    assert.equal(result.ok, true);
    assert.equal(Object.hasOwn(result.metadata, "consumerIdentity"), false);
  });
});

test("task preparation and unknown consumption never record a consumer incarnation", () => {
  fixture(({ binding, metadata, request, stateDir }) => {
    binding("predecessor");
    binding("consumer");
    const meta = metadata();
    request(meta, "task-example", "agent-dispatch");
    const payload = encodeHandoffPayload("brief", meta);
    const result = consumeDispatchHandoffTask("/source", "task-example", "consumer", true, {
      readPayload: () => payload,
      consumeTask: () => { throw new Error("outcome unknown"); },
      promoteHead: () => { assert.fail("must not promote"); },
    });
    assert.equal(result.ok, false);
    const saved = JSON.parse(readFileSync(join(stateDir, "handoff", "delivery-task-example.json")));
    assert.equal(saved.steps.consumeAttempted, true);
    assert.equal(saved.steps.taskConsumed, false);
    assert.equal(Object.hasOwn(saved, "consumerIdentity"), false);
    assert.equal(readSessionStateHandoff("predecessor").record.consumed, false);
  });
});

test("actual task consumption retains both identities and retry cannot relabel the activation", () => {
  fixture(({ binding, metadata, request, stateDir }) => {
    binding("predecessor");
    binding("consumer");
    const meta = metadata();
    request(meta, "task-example", "agent-dispatch");
    const payload = encodeHandoffPayload("brief", meta);
    let calls = 0;
    const options = {
      readPayload: () => payload,
      stateDirResolver: () => stateDir,
      consumeTask: () => {
        calls++;
        const prepared = JSON.parse(readFileSync(join(stateDir, "handoff", "delivery-task-example.json")));
        assert.equal(Object.hasOwn(prepared, "consumerIdentity"), false);
        return payload;
      },
      promoteHead: () => { throw new Error("unavailable"); },
    };
    const first = consumeDispatchHandoffTask("/source", "task-example", "consumer", true, options);
    assert.equal(first.ok, true);
    const saved = JSON.parse(readFileSync(first.checkpoint));
    assert.deepEqual(saved.metadata.authorIdentity, meta.authorIdentity);
    assert.equal(saved.consumerIdentity.sessionId, "consumer");
    assert.equal(saved.steps.taskConsumed, true);
    binding("consumer", { sessionActivation: {
      ...saved.consumerIdentity.activation, ordinal: 4, sourceIdentity: "native:resumed",
    } });
    const retry = consumeDispatchHandoffTask("/source", "task-example", "consumer", true, options);
    assert.equal(retry.ok, true);
    assert.equal(calls, 1);
    assert.deepEqual(retry.checkpointState.consumerIdentity, saved.consumerIdentity);
    assert.deepEqual(readSessionStateHandoff("predecessor").record.consumerIdentity, saved.consumerIdentity);
  });
});

test("resumed task retry after an uncertain consume never attributes the later activation", () => {
  fixture(({ binding, metadata, request, activation }) => {
    binding("predecessor");
    binding("consumer");
    const meta = metadata();
    request(meta, "task-example", "agent-dispatch");
    const payload = encodeHandoffPayload("brief", meta);
    const options = { readPayload: () => payload, promoteHead: () => {} };
    const first = consumeDispatchHandoffTask("/source", "task-example", "consumer", true, {
      ...options, consumeTask: () => { throw new Error("possibly consumed before failure"); },
    });
    assert.equal(first.ok, false);
    binding("consumer", { sessionActivation: {
      ...activation, ordinal: 4, sourceIdentity: "native:resumed",
    } });
    const retry = consumeDispatchHandoffTask("/source", "task-example", "consumer", true, {
      ...options, consumeTask: () => payload,
    });
    assert.equal(retry.ok, true);
    assert.equal(retry.checkpointState.steps.taskConsumed, true);
    assert.equal(retry.checkpointState.consumerIdentity, null);
    assert.equal(JSON.parse(readFileSync(retry.checkpoint)).consumerIdentity, null);
    assert.equal(readSessionStateHandoff("predecessor").record.consumerIdentity, null);
  });
});

test("a legacy completed task checkpoint remains unqualified on same-session replay", () => {
  fixture(({ binding, metadata, stateDir }) => {
    const meta = metadata();
    const path = join(stateDir, "handoff", "delivery-task-example.json");
    mkdirSync(join(stateDir, "handoff"), { recursive: true });
    writeJsonAtomic(path, {
      kind: "context-handoff-delivery", version: 1, path,
      handoffToken: "task-example", consumerSession: "consumer",
      metadata: meta, payload: "brief", steps: { taskConsumed: true },
    });
    binding("consumer");
    const before = readFileSync(path, "utf-8");
    const result = consumeDispatchHandoffTask("/source", "task-example", "consumer", true, {
      readPayload: () => "",
      stateDirResolver: () => stateDir,
      consumeTask: () => { assert.fail("must not consume twice"); },
      promoteHead: () => {},
    });
    assert.equal(result.ok, true);
    assert.equal(Object.hasOwn(result.checkpointState, "consumerIdentity"), false);
    assert.equal(readFileSync(path, "utf-8"), before);
  });
});
