import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createRetainedInbox, retainedDeliverySelected, retainedConsumption,
} from "../extensions/agent-bridge/retained-delivery.mjs";

const contract = "retained-inbox-v1";
const sid = "native-a";
const prefix = `/api/v1/live-sessions/${sid}/retained-inbox`;
const binding = { installation: "synthetic-installation", epoch: 1 };
const identity = { pid: 102, process_started_at: 1 };
const clone = (value) => structuredClone(value);
const tick = () => new Promise((resolve) => setImmediate(resolve));

function fixture() {
  const f = {
    calls: [], sends: [], logs: [], events: [], rows: [], current: null,
    version: 50, clock: 1_000, snapshotSid: sid, nativeSid: sid, closed: false,
    historyReads: 0, after: null, frontendReady: true,
  };
  f.receipt = (row) => ({
    session_id: sid, message_id: row.id, handoff_id: row.handoff_id,
    consumer: clone(row.consumer), stage: row.handoff_stage,
    admitted_at: 1, handoff_started_at: row.handoff_stage === "offered" ? null : 2,
    sdk_accepted_at: row.handoff_stage === "sdk_accepted" ? 3 : null,
    consumed_at: row.handoff_stage === "consumed" ? 4 : null,
  });
  f.request = async (method, path, body) => {
    f.calls.push({ method, path, body: clone(body) });
    if (!f.frontendReady && ["/enroll", "/poll", "/begin"].some((suffix) => path.endsWith(suffix))) {
      throw Object.assign(new Error("frontend requires explicit negotiation"), { status: 409 });
    }
    let result;
    if (path === "/health") result = { protocol_version: f.version };
    else if (path.startsWith(`${prefix}/recovery?`)) {
      const query = new URL(path, "http://localhost").searchParams;
      const pending = f.rows.filter((r) => r.id > Number(query.get("after"))
        && r.handoff_id && r.handoff_stage !== "consumed");
      const page = pending.slice(0, Number(query.get("limit")));
      result = { messages: page.map(f.receipt),
        next_after: pending.length > page.length ? page.at(-1).id : null };
    } else if (path.startsWith(`${prefix}/recovery/`)) {
      result = f.receipt(f.rows.find((row) => row.id === Number(path.split("/").at(-1))));
    } else if (path === `${prefix}/negotiate`) {
      f.frontendReady = true;
      result = { contract, binding, expected_incarnation: f.current?.incarnation ?? null };
    } else if (path === `${prefix}/enroll`) {
      if (f.current?.incarnation === body.consumer.incarnation) {
        assert.deepEqual(body, f.enrolledRequest);
      } else {
        assert.equal(body.expected_incarnation, f.current?.incarnation ?? null);
        f.current = clone(body.consumer);
        f.enrolledRequest = clone(body);
      }
      result = { contract, binding, expected_incarnation: body.expected_incarnation };
    } else if (path === `${prefix}/poll`) {
      assert.equal(body.limit, 1);
      assert.deepEqual(body.consumer, f.current);
      assert.deepEqual(body.binding, binding);
      const row = f.rows.find((r) => r.handoff_stage !== "consumed");
      if (row && !row.handoff_id) {
        row.handoff_id = `handoff-${row.id}`;
        row.consumer = clone(body.consumer);
        row.handoff_stage = "offered";
      }
      if (row) assert.deepEqual(row.consumer, body.consumer);
      result = { contract, binding, consumer: body.consumer, messages: row ? [clone(row)] : [] };
    } else if (path === `${prefix}/begin`) {
      if (f.closed) throw new Error("epoch closed");
      const row = f.rows.find((r) => r.id === body.message_id);
      assert.deepEqual(body.consumer, row.consumer);
      assert.equal(body.handoff_id, row.handoff_id);
      const begin = row.handoff_stage === "offered";
      if (begin) row.handoff_stage = "sending";
      result = { begin };
    } else if (path === `${prefix}/outcome`) {
      const row = f.rows.find((r) => r.id === body.message_id);
      assert.deepEqual(body.consumer, row.consumer);
      assert.equal(body.handoff_id, row.handoff_id);
      assert.equal("binding" in body, false);
      if (row.handoff_stage !== "consumed") row.handoff_stage = body.outcome;
      result = { stage: row.handoff_stage };
    } else throw new Error(`unexpected ${method} ${path}`);
    if (f.after) await f.after(method, path, body, result);
    return clone(result);
  };
  f.session = {
    get sessionId() { return f.nativeSid; },
    rpc: { metadata: { snapshot: async () => ({
      sessionId: f.snapshotSid, workingDirectory: process.cwd(),
    }) } },
    getEvents: async () => { f.historyReads++; return clone(f.events); },
    send: async (options) => { f.sends.push(options); return `sdk-${f.sends.length}`; },
    abort: async () => { throw new Error("retained delivery must not abort"); },
  };
  f.add = (id = f.rows.length + 1, delivery = "steer") => {
    const row = { id, body: `body-${id}`, sender: "peer", kind: "prompt", delivery };
    f.rows.push(row);
    return row;
  };
  f.consumer = (incarnation = "consumer-a", options = {}) => createRetainedInbox({
    session: f.session, request: f.request, processIdentity: identity,
    cwd: process.cwd(), incarnation: () => incarnation,
    now: () => f.clock, log: (line) => f.logs.push(line),
    ...options,
  });
  f.event = (index = 0, changes = {}) => ({
    id: `native-event-${index}`, type: "user.message",
    data: { source: "agent-bridge", content: f.sends[index].prompt },
    ...changes,
  });
  return f;
}

test("retained mode is explicit and never changes legacy delivery defaults", () => {
  for (const value of [undefined, "", "0", "false", "off", "no", "retained", "maybe"]) {
    assert.equal(retainedDeliverySelected(value), false);
  }
  for (const value of ["1", "true", "on", "yes", "TRUE"]) {
    assert.equal(retainedDeliverySelected(value), true);
  }
});

test("selected retained mode refuses old or malformed protocol without a legacy request", async () => {
  for (const version of [undefined, 49, "50", null]) {
    const f = fixture();
    f.version = version;
    f.add();
    await f.consumer().tick(sid);
    assert.deepEqual(f.calls.map((c) => c.path), ["/health"]);
    assert.equal(f.sends.length, 0);
    assert.match(f.logs.join("\n"), /protocol/);
  }
});

test("actual SDK target, metadata and required APIs gate enrollment", async () => {
  for (const problem of ["handle", "metadata", "getEvents", "snapshot", "send"]) {
    const f = fixture();
    f.add();
    if (problem === "handle") f.nativeSid = "other";
    if (problem === "metadata") f.snapshotSid = "other";
    if (problem === "getEvents") delete f.session.getEvents;
    if (problem === "snapshot") delete f.session.rpc.metadata.snapshot;
    if (problem === "send") delete f.session.send;
    await f.consumer().tick(sid);
    assert.equal(f.sends.length, 0);
    assert.equal(f.calls.some((c) => c.path.endsWith("/enroll")), false);
    assert.ok(f.logs.length);
  }
});

test("one poll, one final grant, one attributed SDK send and no legacy ACK", async () => {
  const f = fixture();
  f.add(); f.add();
  await f.consumer().tick(sid);
  assert.equal(f.sends.length, 1);
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  assert.equal(f.rows[1].handoff_id, undefined);
  assert.equal(f.sends[0].source, "agent-bridge");
  assert.equal(f.sends[0].mode, "immediate");
  assert.match(f.sends[0].prompt, /retained-handoff="handoff-1"/);
  assert.match(f.sends[0].prompt, /retained-session="native-a"/);
  assert.equal(f.calls.some((c) => /\/messages|\/controls/.test(c.path)), false);
});

test("queue delivery stays queued and interrupts never invoke SDK or abort", async () => {
  const f = fixture();
  f.add(1, "queue");
  await f.consumer().tick(sid);
  assert.equal(f.sends[0].mode, undefined);
  const blocked = fixture();
  blocked.add(1, "interrupt");
  await blocked.consumer().tick(sid);
  assert.equal(blocked.sends.length, 0);
  assert.equal(blocked.calls.some((c) => c.path.endsWith("/begin")), false);
  assert.ok(blocked.logs.length);
});

test("lost poll reply reuses the offered handoff, not a replacement", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  let lose = true;
  f.after = async (_method, path) => {
    if (lose && path.endsWith("/poll")) { lose = false; throw new Error("lost poll"); }
  };
  await consumer.tick(sid);
  const original = clone(f.rows[0]);
  f.clock += 30_000;
  await consumer.tick(sid);
  assert.equal(f.sends.length, 1);
  assert.equal(f.rows[0].handoff_id, original.handoff_id);
  assert.deepEqual(f.rows[0].consumer, original.consumer);
});

test("lost true final-admission reply never produces a send on retry", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  f.after = async (_method, path) => {
    if (path.endsWith("/begin")) throw new Error("lost true begin");
  };
  await consumer.tick(sid);
  f.clock += 30_000;
  await consumer.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "sending");
  assert.equal(f.sends.length, 0);
  assert.equal(f.calls.filter((c) => c.path.endsWith("/begin")).length, 1);
});

test("lost enrollment reply retries the original CAS without renegotiating its predecessor", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  let lose = true;
  f.after = async (_method, path) => {
    if (lose && path.endsWith("/enroll")) { lose = false; throw new Error("lost enrollment"); }
  };
  await consumer.tick(sid);
  assert.equal(f.sends.length, 0);
  f.clock += 30_000;
  await consumer.tick(sid);
  const enrollments = f.calls.filter((call) => call.path.endsWith("/enroll"));
  assert.equal(enrollments.length, 2);
  assert.deepEqual(enrollments[1].body, enrollments[0].body);
  assert.equal(f.calls.filter((call) => call.path.endsWith("/negotiate")).length, 1);
  assert.equal(f.sends.length, 1);
});

test("SDK ambiguity is retained, never resent, and does not settle consumption", async () => {
  const f = fixture();
  f.add();
  f.session.send = async (options) => {
    f.sends.push(options);
    throw new Error("reply lost after possible acceptance");
  };
  const consumer = f.consumer();
  await consumer.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "unknown");
  f.clock += 30_000;
  await consumer.tick(sid);
  assert.equal(f.sends.length, 1);
  assert.equal(f.rows[0].handoff_stage, "unknown");
});

test("native session change at final boundary leaves the reserved window outstanding", async () => {
  const f = fixture();
  f.add();
  f.after = async (_method, path) => {
    if (path.endsWith("/begin")) f.snapshotSid = "other";
  };
  await f.consumer().tick(sid);
  assert.equal(f.sends.length, 0);
  assert.ok(["sending", "unknown"].includes(f.rows[0].handoff_stage));
});

test("consumption requires original root attribution and all durable handoff fields", async () => {
  const f = fixture();
  f.add();
  await f.consumer().tick(sid);
  const receipt = f.receipt(f.rows[0]);
  const good = f.event();
  assert.equal(retainedConsumption(good, receipt), true);
  for (const bad of [
    { ...good, agentId: "child" },
    { ...good, sessionId: "other" },
    { ...good, type: "assistant.message" },
    { ...good, data: { ...good.data, source: "user" } },
    { ...good, data: { ...good.data, source: undefined } },
    { ...good, data: { ...good.data, parentToolCallId: "child" } },
    { ...good, data: { ...good.data, content: "quoted:\n" + good.data.content } },
    { ...good, data: { ...good.data, content: good.data.content.replace("handoff-1", "other") } },
    { ...good, data: { ...good.data, content: good.data.content.replace("consumer-a", "other") } },
    { ...good, data: { ...good.data, content: good.data.content.replace('msg-id="1"', 'msg-id="2"') } },
  ]) assert.equal(retainedConsumption(bad, receipt), false);
});

test("fresh helper recovers original consumption after closure without reenrollment or send", async () => {
  const f = fixture();
  f.add();
  await f.consumer().tick(sid);
  const original = clone(f.rows[0].consumer);
  f.events = [f.event()];
  f.closed = true;
  await f.consumer("consumer-reloaded").tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.deepEqual(f.rows[0].consumer, original);
  assert.equal(f.sends.length, 1);
  assert.equal(f.calls.filter((c) => c.path.endsWith("/enroll")).length, 1);
  assert.deepEqual(f.calls.at(-1).body.consumer, original);
});

test("lost consumed reply is recovered without another SDK call", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  await consumer.tick(sid);
  f.events = [f.event()];
  f.after = async (_method, path, body) => {
    if (path.endsWith("/outcome") && body.outcome === "consumed") throw new Error("lost report");
  };
  f.clock += 30_000;
  await consumer.tick(sid, { allowSend: false });
  await f.consumer("reloaded").tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.equal(f.sends.length, 1);
});

test("retired offered handoffs remain blocked; reload cannot begin or resend them", async () => {
  const f = fixture();
  f.add();
  f.after = async (_method, path) => {
    if (path.endsWith("/poll")) throw new Error("exit before final admission");
  };
  await f.consumer().tick(sid);
  f.after = null;
  await f.consumer("reloaded").tick(sid);
  assert.equal(f.rows[0].consumer.incarnation, "consumer-a");
  assert.equal(f.rows[0].handoff_stage, "offered");
  assert.equal(f.sends.length, 0);
  assert.ok(f.logs.some((line) => /original|retired/.test(line)));
});

test("lost acceptance report reply is read back through its original receipt", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  f.after = async (_method, path, body) => {
    if (path.endsWith("/outcome") && body.outcome === "sdk_accepted") throw new Error("lost acceptance report");
  };
  await consumer.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  f.clock += 30_000;
  await consumer.tick(sid);
  assert.equal(f.sends.length, 1);
  assert.equal(f.calls.filter((call) => call.path.endsWith("/outcome")).length, 1);
  assert.ok(f.calls.some((call) => call.path.endsWith("/recovery/1")));
});

test("recovery never attributes another native context's history after a concurrent rename", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  await consumer.tick(sid);
  f.session.getEvents = async () => {
    f.snapshotSid = "other";
    return [f.event()];
  };
  f.clock += 30_000;
  await consumer.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  assert.equal(f.sends.length, 1);
});

test("one durable history read reconciles many original receipts, never one RPC per row", async () => {
  const f = fixture();
  f.add();
  await f.consumer().tick(sid);
  const second = clone(f.rows[0]);
  second.id = 2;
  second.handoff_id = "handoff-2";
  second.handoff_stage = "unknown";
  f.rows.push(second);
  f.sends.push({ ...f.sends[0], prompt: f.sends[0].prompt
    .replace("handoff-1", "handoff-2").replace('msg-id="1"', 'msg-id="2"') });
  f.events = [f.event(), f.event(1)];
  await f.consumer("reloaded").tick(sid, { allowSend: false });
  assert.equal(f.historyReads, 1);
  assert.deepEqual(f.rows.map((row) => row.handoff_stage), ["consumed", "consumed"]);
});

test("history is lazy and shared across pending receipts with bounded retry cadence", async () => {
  const f = fixture();
  const consumer = f.consumer();
  await consumer.tick(sid);
  assert.equal(f.historyReads, 0);
  f.add();
  await consumer.tick(sid);
  await consumer.tick(sid);
  const reads = f.historyReads;
  for (let i = 0; i < 20; i++) await consumer.tick(sid);
  assert.ok(reads <= 1);
  assert.equal(f.historyReads, reads);
  f.clock += 30_000;
  await consumer.tick(sid);
  assert.equal(f.historyReads, reads + 1);
  assert.equal(f.sends.length, 1);
});

test("overlapping ticks serialize delivery and muted ticks never acquire a new handoff", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_id, undefined);
  let release;
  f.session.send = async (options) => {
    f.sends.push(options);
    await new Promise((resolve) => { release = resolve; });
    return "sdk-one";
  };
  const first = consumer.tick(sid);
  while (!release) await tick();
  await consumer.tick(sid);
  assert.equal(f.sends.length, 1);
  release();
  await first;
});

test("timed-out SDK invocation stays fenced until it settles, without starting another call", async () => {
  const f = fixture();
  f.add();
  let release;
  f.session.send = (options) => {
    f.sends.push(options);
    return new Promise((resolve) => { release = resolve; });
  };
  const consumer = f.consumer("consumer-a", { nativeTimeoutMs: 5 });
  await consumer.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "unknown");
  assert.match(f.logs.join("\n"), /timed out/);
  f.clock += 30_000;
  await consumer.tick(sid);
  assert.equal(f.sends.length, 1);
  assert.match(f.logs.join("\n"), /still outstanding/);
  release("late-sdk-reply");
  await tick();
  f.events = [f.event()];
  f.clock += 30_000;
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.equal(f.sends.length, 1);
});

test("frontend restart reestablishes negotiation without changing the original enrollment CAS", async () => {
  const f = fixture();
  const consumer = f.consumer();
  await consumer.tick(sid);
  const original = clone(f.enrolledRequest);
  f.frontendReady = false;
  f.add();
  await consumer.tick(sid);
  assert.equal(f.sends.length, 0);
  f.clock += 30_000;
  await consumer.tick(sid);
  assert.equal(f.sends.length, 1);
  assert.deepEqual(f.enrolledRequest, original);
  const enrollments = f.calls.filter((call) => call.path.endsWith("/enroll"));
  assert.equal(enrollments.length, 2);
  assert.deepEqual(enrollments[1].body, enrollments[0].body);
  assert.equal(f.calls.filter((call) => call.path.endsWith("/negotiate")).length, 2);
});

test("recovery pagination reaches later receipts despite older unknowns and the history cooldown", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  await consumer.tick(sid);
  const first = clone(f.rows[0]);
  for (let id = 2; id <= 201; id++) f.rows.push({
    ...clone(first), id, handoff_id: `handoff-${id}`, handoff_stage: "unknown",
  });
  f.events = f.rows.slice(100).map((row) => ({
    id: `native-event-${row.id}`, type: "user.message", data: {
      source: "agent-bridge",
      content: f.sends[0].prompt.replace("handoff-1", row.handoff_id)
        .replace('msg-id="1"', `msg-id="${row.id}"`),
    },
  }));
  for (let count = 0; count < 45; count++) {
    await consumer.tick(sid, { allowSend: false });
    f.clock += 2_000;
  }
  assert.equal(f.rows.slice(100).filter((row) => row.handoff_stage !== "consumed").length, 0);
  assert.equal(f.rows.slice(0, 100).some((row) => row.handoff_stage === "consumed"), false);
  assert.equal(f.historyReads, 3);
  assert.equal(f.sends.length, 1);
});

test("frontend restart after a lost enrollment reply still recovers the original CAS", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  let lose = true;
  f.after = async (_method, path) => {
    if (lose && path.endsWith("/enroll")) {
      lose = false;
      f.frontendReady = false;
      throw new Error("lost enrollment then frontend exit");
    }
  };
  await consumer.tick(sid);
  const original = clone(f.enrolledRequest);
  for (let count = 0; count < 2; count++) {
    f.clock += 30_000;
    await consumer.tick(sid);
  }
  assert.equal(f.sends.length, 1);
  assert.deepEqual(f.enrolledRequest, original);
  assert.equal(f.calls.filter((call) => call.path.endsWith("/begin")).length, 1);
});

test("frontend recovery refuses a changed binding or another current consumer", async () => {
  for (const problem of ["binding", "consumer"]) {
    const f = fixture();
    const consumer = f.consumer();
    await consumer.tick(sid);
    f.frontendReady = false;
    f.add();
    await consumer.tick(sid);
    f.after = async (_method, path, _body, result) => {
      if (path.endsWith("/negotiate")) {
        if (problem === "binding") result.binding = { ...binding, epoch: 2 };
        else result.expected_incarnation = "foreign-consumer";
      }
    };
    f.clock += 30_000;
    await consumer.tick(sid);
    assert.equal(f.sends.length, 0);
    assert.equal(f.calls.filter((call) => call.path.endsWith("/enroll")).length, 1);
    assert.match(f.logs.join("\n"), /original binding or consumer/);
  }
});
