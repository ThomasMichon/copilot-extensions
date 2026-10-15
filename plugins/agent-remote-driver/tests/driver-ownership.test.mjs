import { test } from "node:test";
import assert from "node:assert/strict";
import { createDriverOwnership, DriverControlError } from "../extensions/agent-remote-driver/driver-ownership.mjs";

function fixture(options = {}) {
  let identity = "session-1";
  let generation = 0;
  let credential = 0;
  const ownership = createDriverOwnership({
    getSessionId: () => identity,
    instanceId: "instance-1",
    newGeneration: () => `generation-${++generation}`,
    newCredential: () => `credential-${++credential}`,
    ...options,
  });
  ownership.markReady();
  return { ownership, setIdentity: (value) => { identity = value; } };
}

const expectCode = (code) => (error) => error instanceof DriverControlError && error.code === code;
const rehook = (ownership, ownerId = "driver-a") => ownership.grant(ownership.snapshot(), ownerId, { rehook: true });
const userInput = (id = "human-1", source = "user") => ({ id, type: "user.message", data: { source } });

test("fresh targets are human-held until an explicit re-hook grants a claim", () => {
  const ownership = createDriverOwnership({ getSessionId: () => "session-1" });
  assert.equal(ownership.snapshot().ready, false);
  assert.throws(() => rehook(ownership), expectCode("observation_unavailable"));
  ownership.markReady();
  assert.equal(ownership.snapshot().mode, "human");
  assert.throws(() => ownership.grant(ownership.snapshot(), "driver-a"), expectCode("human_controlled"));
  const before = ownership.snapshot().generation;
  const lease = rehook(ownership);
  assert.notEqual(lease.generation, before);
  assert.equal(ownership.snapshot().mode, "remote");
  assert.equal(ownership.snapshot().ownerId, "driver-a");
  assert.equal(JSON.stringify(ownership.snapshot()).includes(lease.token), false);
});

test("two contenders using one target generation cannot both win", () => {
  const { ownership } = fixture();
  const target = ownership.snapshot();
  ownership.grant(target, "driver-a", { rehook: true });
  assert.throws(() => ownership.grant(target, "driver-b", { rehook: true }), expectCode("generation_stale"));
  assert.throws(() => ownership.grant(ownership.snapshot(), "driver-b"), expectCode("driver_claim_conflict"));
  assert.equal(ownership.snapshot().ownerId, "driver-a");
});

test("target identity and credentials fence mutation independently of owner labels", () => {
  const { ownership } = fixture();
  const lease = rehook(ownership);
  assert.throws(() => ownership.begin({ ...lease, sessionId: "session-other" }, 1, "send"), expectCode("target_changed"));
  assert.throws(() => ownership.begin({ ...lease, instanceId: "instance-other" }, 1, "send"), expectCode("target_changed"));
  assert.throws(() => ownership.begin({ ...lease, token: "wrong", ownerId: "driver-a" }, 1, "send"), expectCode("driver_credential_invalid"));
  assert.equal(ownership.snapshot().lastSequence, 0);
  assert.equal(ownership.snapshot().inFlight, null);
});

test("sequences reject gaps, malformed values, and replay without repeating an operation", () => {
  const { ownership } = fixture();
  const lease = rehook(ownership);
  for (const value of [0, -1, 1.5, "1", Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => ownership.begin(lease, value, "send"), expectCode("sequence_invalid"));
  }
  assert.throws(() => ownership.begin(lease, 2, "send"), expectCode("operation_out_of_order"));
  const first = ownership.begin(lease, 1, "send");
  assert.throws(() => ownership.begin(lease, 2, "send"), expectCode("control_busy"));
  ownership.finish(first);
  assert.throws(() => ownership.begin(lease, 1, "send"), expectCode("operation_replayed"));
  const second = ownership.begin(lease, 2, "abort");
  ownership.finish(second);
  assert.equal(ownership.snapshot().lastSequence, 2);
});

test("an admitted remote source is recognized before its send promise settles", () => {
  const { ownership } = fixture();
  const lease = rehook(ownership);
  const operation = ownership.begin(lease, 1, "send");
  const source = ownership.sourceFor(operation);
  ownership.dispatched(operation, "send");
  assert.equal(ownership.observe(userInput("remote-1", source)), false);
  ownership.assertCurrent(operation);
  assert.equal(ownership.snapshot().pendingSources, 0);
  ownership.completed(operation, "send");
  ownership.finish(operation);
  assert.equal(ownership.snapshot().mode, "remote");
});

test("root human input revokes a pending steer and preserves honest partial effects", () => {
  const { ownership } = fixture();
  const lease = rehook(ownership);
  const operation = ownership.begin(lease, 1, "steer");
  ownership.dispatched(operation, "abort");
  ownership.completed(operation, "abort");
  assert.equal(ownership.observe(userInput()), true);
  assert.equal(ownership.snapshot().mode, "human");
  assert.throws(() => ownership.assertCurrent(operation), (error) => {
    assert.equal(error.code, "driver_revoked");
    assert.deepEqual(error.details.effects, { abort: "completed", send: "not_started" });
    return true;
  });
  assert.throws(() => rehook(ownership), expectCode("control_busy"));
  ownership.finish(operation);
  assert.throws(() => ownership.begin(lease, 2, "send"), expectCode("generation_stale"));
  assert.throws(() => ownership.grant(ownership.snapshot(), "driver-a"), expectCode("human_controlled"));
  const next = rehook(ownership);
  assert.notEqual(next.generation, lease.generation);
  assert.notEqual(next.token, lease.token);
  assert.throws(() => ownership.begin({ ...next, token: lease.token }, 1, "send"), expectCode("driver_credential_invalid"));
});

test("human input racing an administrative re-hook invalidates its expected generation", () => {
  const { ownership } = fixture();
  const target = ownership.snapshot();
  ownership.observe(userInput());
  assert.throws(() => ownership.grant(target, "driver-a", { rehook: true }), expectCode("generation_stale"));
  assert.equal(ownership.snapshot().mode, "human");
});

test("a replayed input cannot revoke a fresh re-hook, while new unknown root input does", () => {
  const { ownership } = fixture();
  rehook(ownership);
  ownership.observe(userInput("human-1"));
  const next = rehook(ownership);
  assert.equal(ownership.observe(userInput("human-1")), false);
  assert.equal(ownership.snapshot().generation, next.generation);
  assert.equal(ownership.observe({ type: "user.message", id: "unknown-1", data: {} }), true);
  assert.equal(ownership.snapshot().mode, "human");
});

test("sub-agent and non-user events do not claim root-human takeover", () => {
  const { ownership } = fixture();
  const lease = rehook(ownership);
  assert.equal(ownership.observe({ ...userInput(), agentId: "subagent-1" }), false);
  assert.equal(ownership.observe({ ...userInput(), data: { agentId: "legacy-subagent" } }), false);
  assert.equal(ownership.observe({ ...userInput(), data: { parentToolCallId: "legacy-parent" } }), false);
  assert.equal(ownership.observe({ type: "assistant.idle" }), false);
  assert.equal(ownership.snapshot().generation, lease.generation);
});

test("a late event from an earlier admitted remote send neither grants nor revokes ownership", () => {
  const { ownership } = fixture();
  const lease = rehook(ownership);
  const operation = ownership.begin(lease, 1, "send");
  const source = ownership.sourceFor(operation);
  ownership.dispatched(operation, "send");
  ownership.finish(operation);
  ownership.observe(userInput());
  const fresh = rehook(ownership);
  assert.equal(ownership.observe(userInput("late-remote", source)), false);
  assert.equal(ownership.snapshot().generation, fresh.generation);
});

test("release is compare-and-release; old credentials cannot release the next owner", () => {
  const { ownership } = fixture();
  const lease = rehook(ownership);
  ownership.release(lease, 1);
  assert.equal(ownership.snapshot().mode, "available");
  const next = ownership.grant(ownership.snapshot(), "driver-b");
  assert.throws(() => ownership.release(lease, 2), expectCode("generation_stale"));
  assert.equal(ownership.snapshot().generation, next.generation);
  assert.equal(ownership.snapshot().ownerId, "driver-b");
});

test("pending source tracking is bounded and refuses another send rather than forgetting authority", () => {
  const { ownership } = fixture({ maxPendingSources: 2 });
  const lease = rehook(ownership);
  const sources = [];
  for (let sequence = 1; sequence <= 2; sequence++) {
    const operation = ownership.begin(lease, sequence, "send");
    sources.push(ownership.sourceFor(operation));
    ownership.dispatched(operation, "send");
    ownership.finish(operation);
  }
  const refused = ownership.begin(lease, 3, "send");
  assert.throws(() => ownership.sourceFor(refused), expectCode("provenance_backlog_full"));
  ownership.finish(refused);
  assert.equal(ownership.snapshot().pendingSources, 2);
  ownership.observe(userInput("remote-consumed", sources[0]));
  const next = ownership.begin(lease, 4, "send");
  ownership.sourceFor(next);
  ownership.finish(next);
  assert.equal(ownership.snapshot().pendingSources, 1);
});

test("observation loss and a changed native session fail closed", () => {
  const { ownership, setIdentity } = fixture();
  const lease = rehook(ownership);
  ownership.suspend("native_connection_lost");
  assert.equal(ownership.snapshot().ready, false);
  assert.throws(() => ownership.begin(lease, 1, "send"), expectCode("observation_unavailable"));
  ownership.markReady();
  const fresh = rehook(ownership);
  setIdentity("session-2");
  assert.equal(ownership.snapshot().ready, false);
  assert.throws(() => ownership.begin({ ...fresh, sessionId: "session-2" }, 1, "send"), expectCode("observation_unavailable"));
});

test("a failed generation transition invalidates authority instead of retaining a live owner", () => {
  let fail = false;
  let sequence = 0;
  const { ownership } = fixture({ newGeneration: () => fail ? "generation-3" : `generation-${++sequence}` });
  rehook(ownership);
  fail = true;
  assert.throws(() => ownership.observe(userInput()), expectCode("generation_unavailable"));
  assert.equal(ownership.snapshot().mode, "human");
  assert.equal(ownership.snapshot().ready, false);
  assert.equal(ownership.snapshot().ownerId, null);
});
