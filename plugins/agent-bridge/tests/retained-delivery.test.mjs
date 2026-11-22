import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  createRetainedInbox, retainedDeliverySelected, retainedConsumption, retainedConsumptionReason, RESUME_NUDGE,
} from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/retained-delivery.mjs";
import { isRemoteDriverSource } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/remote-driver.mjs";
import { MainTurn, PendingInputNotice } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/delivery.mjs";

const contract = "retained-inbox-v1";
const sid = "native-a";
const prefix = `/api/v1/live-sessions/${sid}/retained-inbox`;
const binding = { installation: "synthetic-installation", epoch: 1 };
const identity = { pid: 102, process_started_at: 1 };
const clone = (value) => structuredClone(value);
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("buffered native headers do not retain their large message bodies", () => {
  const moduleUrl = new URL(
    "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/retained-delivery.mjs",
    import.meta.url,
  ).href;
  const child = spawnSync(process.execPath, ["--expose-gc", "--input-type=module", "--eval", `
    import assert from "node:assert/strict";
    import { createRetainedInbox } from ${JSON.stringify(moduleUrl)};
    const inbox = createRetainedInbox({ session: { sessionId: "native-memory" } });
    const observe = (id) => inbox.observe("native-memory", {
      type: "user.message",
      data: { source: "agent-bridge", turnId: "turn-" + id, content:
        '<agent-message retained-session="native-memory" retained-consumer="original" '
        + 'retained-handoff="handoff-' + id + '" msg-id="' + id + '">'
        + "body".repeat(1024 * 1024) + id },
    });
    global.gc();
    const before = process.memoryUsage().heapUsed;
    for (let id = 1; id <= 12; id++) observe(id);
    global.gc();
    const retained = process.memoryUsage().heapUsed - before;
    assert.ok(retained < 8 * 1024 * 1024, "buffer retained " + retained + " bytes");
    inbox.close();
  `], { encoding: "utf8", windowsHide: true, timeout: 15_000 });
  assert.ifError(child.error);
  assert.equal(child.status, 0, child.stderr || child.stdout);
});

function fixture() {
  const f = {
    calls: [], sends: [], logs: [], events: [], rows: [], current: null,
    version: 50, capabilities: {}, clock: 1_000, snapshotSid: sid, nativeSid: sid, closed: false,
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
    if (path === "/health") result = { protocol_version: f.version, capabilities: f.capabilities };
    else if (path.startsWith(`${prefix}/recovery?`)) {
      const query = new URL(path, "http://localhost").searchParams;
      const pending = f.rows.filter((r) => r.id > Number(query.get("after"))
        && r.handoff_id && !r.disposition && !r.control && r.handoff_stage !== "consumed");
      const page = pending.slice(0, Number(query.get("limit")));
      result = { messages: page.map(f.receipt),
        next_after: pending.length > page.length ? page.at(-1).id : null };
    } else if (path.startsWith(`${prefix}/recovery/`)) {
      const row = f.rows.find((row) => row.id === Number(path.split("/").at(-1)));
      if (!row) throw Object.assign(new Error("original unavailable"), { status: 404 });
      result = f.receipt(row);
    } else if (path === `${prefix}/negotiate`) {
      f.frontendReady = true;
      result = { contract, binding, expected_incarnation: f.current?.incarnation ?? null };
      if (f.messageBase) result.message_base = 1;
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
      const reattempt = (r) => (body.reattempt ?? []).includes(r.id)
        && ["sending", "unknown"].includes(r.handoff_stage);
      const row = f.rows.find((r) => !r.disposition && (body.receiving_api === "native-sdk-v1"
        ? !r.handoff_stage || r.handoff_stage === "offered" || reattempt(r)
        : r.handoff_stage !== "consumed"));
      if (row && reattempt(row)) row.attempt ??= 1;
      if (row && !row.handoff_id && !row.control) {
        row.handoff_id = `handoff-${row.id}`;
        row.consumer = clone(body.consumer);
        row.handoff_stage = "offered";
      }
      if (row && f.messageBase) {
        row.handoff_stage ??= "offered";
        row.message_contract = {
          base_version: 1, type: row.kind, type_version: 1, requires: [],
          message_id: row.id, session_id: sid, receiver_incarnation: body.consumer.incarnation,
          sender: row.sender, ...f.contractChanges,
        };
      }
      if (row && body.receiving_api !== "native-sdk-v1") assert.deepEqual(row.consumer, body.consumer);
      result = { contract, binding, consumer: body.consumer, messages: row ? [clone(row)] : [] };
    } else if (path === `${prefix}/begin`) {
      if (f.closed) throw new Error("epoch closed");
      const row = f.rows.find((r) => r.id === body.message_id);
      assert.equal(body.handoff_id, row.handoff_id);
      if (body.attempt !== undefined) {
        // Re-grant for the current consumer; bounded by the attempt ledger.
        assert.deepEqual(body.consumer, f.current);
        const count = row.attempt ?? 1;
        let begin = false;
        if (body.attempt === count && ["sending", "unknown"].includes(row.handoff_stage)) {
          if (count >= 3) row.disposition = { state: "failed", code: "delivery_attempts_exhausted" };
          else Object.assign(row, { attempt: count + 1, handoff_stage: "sending" });
          begin = !row.disposition;
        }
        f.regrants = (f.regrants ?? 0) + 1;
        result = { begin };
      } else {
        assert.deepEqual(body.consumer, row.consumer);
        const begin = row.handoff_stage === "offered";
        if (begin) row.handoff_stage = "sending";
        result = { begin };
      }
    } else if (path === `${prefix}/disposition`) {
      const row = f.rows.find((r) => r.id === body.message_id);
      assert.deepEqual(body.consumer, f.current);
      const begin = body.state === "started" && !row.disposition;
      row.disposition = clone(body);
      result = { ...row.disposition, begin };
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
    data: { source: "agent-bridge", content: f.sends[index].prompt, turnId: `native-turn-${index}` },
    ...changes,
  });
  return f;
}

test("runtime datetime prefixes reconcile original SDK input without resending", async () => {
  const f = fixture();
  f.add();
  const inbox = f.consumer();
  await inbox.tick(sid);
  const receipt = f.receipt(f.rows[0]);
  const good = f.event();
  for (const prefix of [
    "<current_datetime>2026-01-02T03:04:05.678-08:00</current_datetime>\n\n",
    "<current_datetime>2026-01-02T03:04:05Z</current_datetime>\r\n\r\n",
  ]) {
    const event = { ...good, data: { ...good.data, content: "display only",
      transformedContent: prefix + good.data.content } };
    assert.equal(retainedConsumptionReason(event, receipt), "matched");
    assert.equal(retainedConsumption(event, receipt), true);
    f.events = [event];
  }
  for (const prefix of [
    "quoted input:\n",
    "> ",
    "<system_reminder>context</system_reminder>\n",
    "<current_datetime>arbitrary text</current_datetime>\n\n",
    "<current_datetime>2026-01-02T03:04:05Z</current_datetime>\n",
    "<current_datetime>2026-01-02T03:04:05Z</current_datetime>\n\n"
      + "<current_datetime>2026-01-02T03:04:05Z</current_datetime>\n\n",
  ]) {
    const event = { ...good, data: { ...good.data, transformedContent: prefix + good.data.content } };
    assert.equal(retainedConsumptionReason(event, receipt), "header_mismatch");
    assert.equal(retainedConsumption(event, receipt), false);
  }
  assert.equal(retainedConsumptionReason(good, null), "invalid_receipt");
  assert.equal(retainedConsumption(good, null), false);
  f.clock += 30_001;
  await inbox.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.equal(f.sends.length, 1);
  assert.equal(f.calls.filter((call) => call.path.endsWith("/begin")).length, 1);
});

test("match diagnostics contain correlation and fixed reason counts, never input or replay", async () => {
  const f = fixture();
  f.add();
  const observations = [];
  const inbox = f.consumer("consumer-a", { onObservation: (event) => observations.push(event) });
  await inbox.tick(sid);
  const event = f.event();
  event.data.content = event.data.content.replace("handoff-1", "other-handoff");
  f.events = [event, { type: "assistant.message", data: { content: "private-output-sentinel" } }];
  f.clock += 30_001;
  await inbox.tick(sid);
  assert.deepEqual(observations, [{
    stage: "match_failed", source: "retained-sdk-recovery", session_id: sid,
    message_id: 1, handoff_id: "handoff-1", consumer_incarnation: "consumer-a",
    event_count: 2, failures: { header_mismatch: 1, not_root_input: 1 },
  }]);
  assert.equal(JSON.stringify(observations).includes("private-output-sentinel"), false);
  assert.equal(JSON.stringify(observations).includes("body-1"), false);
  f.clock += 30_001;
  await f.consumer("consumer-b", { onObservation: () => { throw new Error("sink failed"); } })
    .tick(sid, { allowSend: false });
  assert.equal(f.sends.length, 1);
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
});

test("accepted retained recovery clears submission attention without consuming or resending", async () => {
  const f = fixture();
  f.add();
  let notice = new PendingInputNotice({ now: () => f.clock, reminderMs: 0 });
  const options = {
    onPending: (target, messages) => notice.offered(target, messages),
    onSubmitted: (target, id) => notice.confirmed(target, id),
  };
  const inbox = f.consumer("consumer-a", options);
  await inbox.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  assert.equal(notice.pending.size, 0);
  // A new consumer has no in-memory confirmation for the original receipt.
  notice = new PendingInputNotice({ now: () => f.clock, reminderMs: 0 });
  notice.offered(sid, [{ id: 1 }]);
  assert.equal(notice.pending.size, 1);
  await f.consumer("consumer-b", options).tick(sid, { allowSend: false });
  assert.equal(notice.pending.size, 0);
  assert.equal(f.sends.length, 1);
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  assert.equal(f.calls.some((call) => call.body?.outcome === "consumed"), false);
});

test("late SDK acceptance clears attention even while the original receipt remains uncertain", async () => {
  const f = fixture();
  f.add();
  let release;
  f.session.send = (options) => {
    f.sends.push(options);
    return new Promise((resolve) => { release = resolve; });
  };
  const notice = new PendingInputNotice();
  const inbox = f.consumer("consumer-a", {
    nativeTimeoutMs: 5,
    onPending: (target, messages) => notice.offered(target, messages),
    onSubmitted: (target, id) => notice.confirmed(target, id),
  });
  await inbox.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "unknown");
  assert.equal(notice.pending.size, 1);
  release("sdk-late");
  await tick();
  assert.equal(notice.pending.size, 0);
  f.clock += 30_001;
  await inbox.tick(sid);
  assert.equal(notice.pending.size, 0);
  assert.equal(f.sends.length, 1);
  assert.equal(f.rows[0].handoff_stage, "unknown");
  assert.equal(f.calls.some((call) => call.body?.outcome === "consumed"), false);
});

test("versioned retained controls reconstruct bounded history without SDK dispatch", async () => {
  const f = fixture();
  f.messageBase = true;
  f.capabilities.compatible_native_inbox = 1;
  for (let id = 1; id <= 24; id++) {
    Object.assign(f.add(id), { kind: "control:replay-history", body: "", control: true });
  }
  const replays = [];
  const inbox = f.consumer("current", {
    receivingApi: "native-sdk-v1", replayHistory: async (target) => { replays.push(target); return true; },
  });
  for (let id = 1; id <= 25; id++) await inbox.tick(sid);
  assert.equal(replays.length, 24);
  assert.ok(replays.every((target) => target === sid));
  assert.equal(f.sends.length, 0);
  assert.ok(f.rows.every((row) => row.disposition.state === "completed"));
  assert.equal(f.calls.filter((call) => call.path.endsWith("/begin")).length, 0);
});

for (const changes of [
  { type: "future:message" }, { type_version: 9 }, { base_version: 9 },
  { requires: ["future-required"] }, { requires: null }, { contract_error: "invalid_contract" },
]) {
  test(`versioned rejection precedes SDK dispatch: ${JSON.stringify(changes)}`, async () => {
    const f = fixture();
    f.messageBase = true;
    f.capabilities.compatible_native_inbox = 1;
    f.contractChanges = changes;
    const row = f.add();
    await f.consumer("current", { receivingApi: "native-sdk-v1" }).tick(sid);
    assert.equal(f.sends.length, 0);
    assert.equal(row.disposition.state, "rejected");
    assert.ok(row.disposition.code && row.disposition.reason);
    assert.equal(f.calls.filter((call) => call.path.endsWith("/begin")).length, 0);
  });
}

test("a lost history start reply never authorizes replay or false rejection", async () => {
  const f = fixture();
  f.messageBase = true;
  f.capabilities.compatible_native_inbox = 1;
  const row = Object.assign(f.add(), { kind: "control:replay-history", body: "", control: true });
  f.after = async (_method, route, body) => {
    if (route.endsWith("/disposition") && body.state === "started") throw new Error("reply lost");
  };
  let replays = 0;
  const inbox = f.consumer("current", {
    receivingApi: "native-sdk-v1", replayHistory: async () => { replays++; return true; },
  });
  await inbox.tick(sid);
  f.clock += 30_001;
  await inbox.tick(sid);
  assert.equal(replays, 0);
  assert.equal(f.sends.length, 0);
  assert.equal(row.disposition.state, "started");
});

test("unknown additive optional envelope properties preserve supported delivery", async () => {
  const f = fixture();
  f.messageBase = true;
  f.capabilities.compatible_native_inbox = 1;
  f.contractChanges = { future_optional: { ignored: true } };
  f.add();
  await f.consumer("current", { receivingApi: "native-sdk-v1" }).tick(sid);
  assert.equal(f.sends.length, 1);
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
});

test("retained mode is explicit and never changes legacy delivery defaults", () => {
  for (const value of [undefined, "", "0", "false", "off", "no", "retained", "maybe"]) {
    assert.equal(retainedDeliverySelected(value), false);
  }
  for (const value of ["1", "true", "on", "yes", "TRUE"]) {
    assert.equal(retainedDeliverySelected(value), true);
  }
});

test("native SDK receiving recovers the original offered consumer after reload without process admission", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  const row = f.add();
  row.handoff_id = "original-handoff";
  row.handoff_stage = "offered";
  row.consumer = { session_id: sid, incarnation: "previous", pid: null, process_started_at: null };
  const inbox = f.consumer("reloaded", {
    receivingApi: "native-sdk-v1", processIdentity: undefined,
    request: (method, path, body) => {
      const wire = clone(body);
      if (wire?.consumer) {
        wire.consumer.pid ??= null;
        wire.consumer.process_started_at ??= null;
      }
      return f.request(method, path, wire);
    },
  });
  await inbox.tick(sid);
  assert.equal(f.sends.length, 1);
  assert.match(f.sends[0].prompt, /retained-consumer="previous"/);
  assert.equal(row.handoff_stage, "sdk_accepted");
  assert.deepEqual(f.calls.find((call) => call.path.endsWith("/begin")).body.consumer, row.consumer);
  await inbox.tick(sid);
  assert.equal(f.sends.length, 1);
  f.events.push(f.event());
  f.clock += 30_001;
  await inbox.tick(sid);
  assert.equal(row.handoff_stage, "consumed");
});

test("compatible receiving observes a begun original but never sends it again", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  const row = f.add();
  row.handoff_id = "original-handoff";
  row.handoff_stage = "unknown";
  row.consumer = { session_id: sid, incarnation: "previous", ...identity };
  f.add();
  await f.consumer("reloaded", { receivingApi: "native-sdk-v1" }).tick(sid);
  assert.equal(f.sends.length, 1);
  assert.match(f.sends[0].prompt, /msg-id="2"/);
  assert.equal(f.calls.filter((call) => call.path.endsWith("/begin")).length, 1);
  assert.equal(row.handoff_stage, "unknown");
});

test("native receiving does not turn unavailable process diagnostics into admission", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  f.add();
  await f.consumer("native", {
    receivingApi: "native-sdk-v1",
    processIdentity: { pid: "unavailable", process_started_at: NaN },
  }).tick(sid);
  assert.equal(f.sends.length, 1);
  const enrollment = f.calls.find((call) => call.path.endsWith("/enroll")).body;
  assert.equal("pid" in enrollment.consumer, false);
  assert.equal("process_started_at" in enrollment.consumer, false);
});

test("native receiving recovers registration loss without replaying an accepted original", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  f.add();
  let next = 0;
  const inbox = f.consumer("native", {
    receivingApi: "native-sdk-v1", incarnation: () => `consumer-${++next}`,
  });
  await inbox.tick(sid);
  const original = clone(f.rows[0]);
  f.current = null;
  f.frontendReady = false;
  f.add();
  await inbox.tick(sid);
  f.clock += 30_001;
  await inbox.tick(sid);
  assert.equal(f.current.incarnation, "consumer-2");
  assert.equal(f.sends.length, 2);
  assert.match(f.sends[0].prompt, /msg-id="1"/);
  assert.match(f.sends[1].prompt, /msg-id="2"/);
  assert.deepEqual(f.rows[0], original);
});

for (const boundary of ["before", "after"]) {
  test(`portable loadout validation remains separate from private control (${boundary} begin)`, async () => {
    const f = fixture();
    f.capabilities.compatible_native_inbox = 1;
    f.add();
    let checks = 0;
    await f.consumer("native", {
      receivingApi: "native-sdk-v1",
      validateSendContext: async () => {
        if (++checks === (boundary === "before" ? 1 : 2)) {
          throw new Error("selected extensions are duplicated");
        }
      },
    }).tick(sid);
    assert.equal(f.sends.length, 0);
    assert.equal(f.calls.filter((call) => call.path.endsWith("/begin")).length,
      boundary === "before" ? 0 : 1);
    assert.equal(f.rows[0].handoff_stage, boundary === "before" ? "offered" : "unknown");
    assert.ok(f.logs.some((line) => line.includes("duplicated")));
  });
}

test("driver consumption requires explicit source selection and still binds the original receipt", async () => {
  const f = fixture();
  f.add();
  const pending = [], consumed = [];
  const inbox = f.consumer("consumer-a", {
    sourceAllowed: isRemoteDriverSource,
    onPending: (sessionId, messages) => {
      assert.equal(sessionId, sid);
      pending.push(messages.map((msg) => msg.id));
    },
    onConsumed: (sessionId, id) => {
      assert.equal(sessionId, sid);
      consumed.push(id);
    },
  });
  await inbox.tick(sid);
  assert.deepEqual(pending.at(-1), [1]);
  assert.deepEqual(consumed, []);
  const receipt = f.receipt(f.rows[0]);
  const event = f.event();
  event.data.source = "agent-remote-driver-11111111-2222-4333-8444-555555555555";
  assert.equal(retainedConsumption(event, receipt), false);
  assert.equal(retainedConsumption(event, receipt, isRemoteDriverSource), true);
  assert.equal(retainedConsumption(event, { ...receipt, handoff_id: "different" }, isRemoteDriverSource), false);
  f.events.push(event);
  f.clock += 30_001;
  await inbox.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.deepEqual(consumed, [1]);
  assert.equal(f.sends.length, 1);
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

test("portable consumer declares its attempt only after explicit provider qualification", async () => {
  const portableLaunch = "11111111-1111-4111-8111-111111111111";
  const blocked = fixture();
  await blocked.consumer("consumer", { portableLaunch }).tick(sid);
  assert.deepEqual(blocked.calls.map((call) => call.path), ["/health"]);
  const f = fixture();
  f.capabilities = { portable_inbox: 1, readonly_plugin_controls: 1 };
  await f.consumer("consumer", { portableLaunch }).tick(sid);
  assert.equal(f.current.portable_launch, portableLaunch);
});

for (const changed of ["none", "missing", "after-grant"]) {
  test(`retained startup rechecks its original plugin context (${changed})`, async () => {
    const f = fixture();
    const row = f.add(1, "queue");
    row.kind = "startup:prompt:7";
    row.plugin_readiness = {
      owner: "bridge", expected_session_id: sid, cwd: process.cwd(), selection_digest: "original",
      timeout_seconds: 1,
      plugins: [{ projected_source: "helper@fixture", logical_source: "helper@fixture" }],
    };
    let enabled = changed !== "missing";
    f.session.rpc.plugins = { list: async () => ({
      plugins: [{ name: "helper", marketplace: "fixture", enabled }],
    }) };
    f.after = async (_method, path) => {
      if (changed === "after-grant" && path.endsWith("/begin")) enabled = false;
    };
    const inbox = f.consumer();
    await inbox.tick(sid);
    assert.equal(f.sends.length, changed === "none" ? 1 : 0, f.logs.join("\n"));
    if (changed === "none") assert.doesNotMatch(f.sends[0].prompt, /startup:/);
    if (changed === "missing") assert.equal(f.calls.some((call) => call.path.endsWith("/begin")), false);
    if (changed === "after-grant") {
      assert.equal(row.handoff_stage, "unknown");
      f.clock += 30_001;
      await inbox.tick(sid);
      assert.equal(f.sends.length, 0);
    }
  });
}

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
  assert.equal(retainedConsumption({
    ...good, data: { ...good.data, turnId: undefined },
  }, receipt), true, "historical witnesses keep their supported older event shape");
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
  const notice = new PendingInputNotice();
  const consumer = f.consumer("consumer-a", {
    onPending: (target, messages) => notice.offered(target, messages),
    onSubmitted: (target, id) => notice.confirmed(target, id),
  });
  f.after = async (_method, path, body) => {
    if (path.endsWith("/outcome") && body.outcome === "sdk_accepted") throw new Error("lost acceptance report");
  };
  await consumer.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  assert.equal(notice.pending.size, 0);
  f.clock += 30_000;
  await consumer.tick(sid);
  assert.equal(f.sends.length, 1);
  assert.equal(f.calls.filter((call) => call.path.endsWith("/outcome")).length, 1);
  assert.ok(f.calls.some((call) => call.path.endsWith("/recovery/1")));
  assert.equal(notice.pending.size, 0);
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

for (const failure of ["rejected", "pending"]) {
  test(`unavailable history cannot starve compatible fresh delivery (${failure})`, async () => {
    const f = fixture();
    f.capabilities.compatible_native_inbox = 1;
    f.add();
    const consumer = f.consumer("native", {
      receivingApi: "native-sdk-v1", nativeTimeoutMs: 5,
    });
    await consumer.tick(sid);
    const original = clone(f.rows[0]);
    const originalEvent = f.event();
    let release;
    const history = new Promise((resolve) => { release = resolve; });
    f.session.getEvents = () => {
      f.historyReads++;
      return failure === "pending" ? history : Promise.reject(new Error("history unavailable"));
    };
    try {
      f.add();
      f.clock += 30_001;
      await consumer.tick(sid);
      assert.equal(f.sends.length, 2, "history observation must not gate a fresh granted send");
      assert.deepEqual(f.rows[0], original, "unobserved original consumption stays unresolved");
      f.add();
      f.clock += 30_001;
      await consumer.tick(sid);
      assert.equal(f.sends.length, 3);
      assert.equal(f.historyReads, failure === "pending" ? 1 : 2);

      f.add();
      f.snapshotSid = "other";
      f.clock += 30_001;
      await consumer.tick(sid);
      assert.equal(f.sends.length, 3, "fresh native-context checks still gate delivery");
      assert.equal(f.rows[3].handoff_id, undefined);
      f.snapshotSid = sid;
      release([originalEvent]);
      await tick();
      f.session.getEvents = async () => [originalEvent];
      f.clock += 30_001;
      await consumer.tick(sid, { allowSend: false });
      assert.equal(f.rows[0].handoff_stage, "consumed");
      assert.equal(f.sends.length, 3, "late history observes the original without resending");
    } finally {
      release([]);
      consumer.close();
    }
  });
}

for (const failure of ["rejected", "pending"]) {
  test(`live root input settles its original receipt with unavailable history (${failure})`, async () => {
    const f = fixture();
    f.capabilities.compatible_native_inbox = 1;
    f.add();
    const consumer = f.consumer("native", {
      receivingApi: "native-sdk-v1", nativeTimeoutMs: 5,
    });
    await consumer.tick(sid);
    let release;
    const history = new Promise((resolve) => { release = resolve; });
    f.session.getEvents = () => {
      f.historyReads++;
      return failure === "pending" ? history : Promise.reject(new Error("history unavailable"));
    };
    try {
      f.clock += 30_001;
      await consumer.tick(sid);
      assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
      const calls = f.calls.length;
      consumer.observe?.(sid, f.event());
      assert.equal(f.calls.length, calls, "the native callback must do no I/O");
      await consumer.tick(sid, { allowSend: false });
      assert.equal(f.rows[0].handoff_stage, "consumed");
      assert.equal(f.historyReads, 1, "live evidence must not await another history read");
      assert.equal(f.sends.length, 1);
      assert.equal(f.calls.filter((call) => call.body?.outcome === "consumed").length, 1);
    } finally {
      release([]);
      consumer.close();
    }
  });
}

test("a stale offered recovery reply preserves input observed after the original begin", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  Object.assign(f.add(), {
    handoff_id: "handoff-1", handoff_stage: "offered",
    consumer: { session_id: sid, incarnation: "original-consumer" },
  });
  const owner = f.consumer("original-consumer", { receivingApi: "native-sdk-v1" });
  const observer = f.consumer("observer", { receivingApi: "native-sdk-v1" });
  f.session.getEvents = async () => { throw new Error("history unavailable"); };
  f.after = async (method, path) => {
    if (method === "GET" && path.includes("/recovery?")) {
      f.after = null;
      await owner.tick(sid);
      observer.observe(sid, f.event());
    }
  };
  await observer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  await observer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.equal(f.sends.length, 1);
  assert.equal(f.historyReads, 0);
  owner.close();
  observer.close();
});

test("live input beyond an older history page settles without skipping historical recovery", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  f.add();
  const consumer = f.consumer("native", { receivingApi: "native-sdk-v1" });
  await consumer.tick(sid);
  for (let id = 2; id <= 100; id++) {
    Object.assign(f.add(id), {
      handoff_id: `handoff-${id}`, handoff_stage: "sdk_accepted", consumer: clone(f.current),
    });
  }
  f.add(101);
  f.session.getEvents = async () => { throw new Error("history unavailable"); };
  f.clock += 30_001;
  await consumer.tick(sid);
  consumer.observe?.(sid, f.event(1));
  f.clock += 30_001;
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.rows[100].handoff_stage, "consumed");
  assert.ok(f.rows.slice(0, 100).every((row) => row.handoff_stage === "sdk_accepted"));
  assert.equal(f.sends.length, 2);
  assert.ok(f.calls.some((call) => call.path.endsWith("/recovery/101")));
  const pages = f.calls.filter((call) => call.path.includes("/recovery?"));
  assert.ok(pages.every((call) => new URL(call.path, "http://localhost").searchParams.get("after") === "0"));
  consumer.close();
});

test("live receipt evidence preserves exact matching and fresh native-context admission", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  f.add();
  const consumer = f.consumer("native", { receivingApi: "native-sdk-v1" });
  await consumer.tick(sid);
  f.session.getEvents = async () => { throw new Error("history unavailable"); };
  const good = f.event();
  for (const bad of [
    { ...good, agentId: "nested" },
    { ...good, data: { ...good.data, parentToolCallId: "nested" } },
    { ...good, sessionId: "other-native" },
    { ...good, data: { ...good.data, source: "user" } },
    { ...good, data: { ...good.data, turnId: undefined, clientTurnId: "caller-chosen-not-a-witness" } },
    { ...good, data: { ...good.data, content: good.data.content.replace("handoff-1", "other") } },
    { ...good, data: { ...good.data, content: "quoted input:\n" + good.data.content } },
    { ...good, data: { ...good.data,
      content: good.data.content.replace('msg-id="1"', `padding="${"x".repeat(5000)}" msg-id="1"`) } },
  ]) consumer.observe?.(sid, bad);
  f.clock += 30_001;
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  consumer.observe?.(sid, { ...good, data: {
    ...good.data, content: "display only",
    transformedContent: "<current_datetime>2026-01-02T03:04:05Z</current_datetime>\n\n" + good.data.content,
  } });
  f.snapshotSid = "different-native";
  f.clock += 30_001;
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  f.snapshotSid = sid;
  f.clock += 30_001;
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.equal(f.sends.length, 1);
  consumer.close();
});

test("live input after receiver reload settles the original consumer and explicitly allowed source", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  f.add();
  const first = f.consumer("original-consumer", { receivingApi: "native-sdk-v1" });
  await first.tick(sid);
  const event = f.event();
  event.data.source = "agent-remote-driver-11111111-2222-4333-8444-555555555555";
  first.close();
  f.session.getEvents = async () => { throw new Error("history unavailable"); };
  const next = f.consumer("successor-consumer", {
    receivingApi: "native-sdk-v1", sourceAllowed: isRemoteDriverSource,
  });
  next.observe?.(sid, event);
  await next.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "consumed");
  const report = f.calls.find((call) => call.body?.outcome === "consumed");
  assert.equal(report.body.consumer.incarnation, "original-consumer");
  assert.equal(f.sends.length, 1);
  next.close();
});

test("live input observed before a late SDK reply retains its exact original evidence", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  f.add();
  let release;
  const accepted = new Promise((resolve) => { release = resolve; });
  let consumer;
  f.session.send = (options) => {
    f.sends.push(options);
    consumer.observe?.(sid, f.event());
    return accepted;
  };
  consumer = f.consumer("native", { receivingApi: "native-sdk-v1", nativeTimeoutMs: 5 });
  try {
    await consumer.tick(sid);
    assert.equal(f.rows[0].handoff_stage, "unknown");
    release("late-sdk-acceptance");
    await tick();
    f.session.getEvents = async () => { throw new Error("history unavailable"); };
    f.clock += 30_001;
    await consumer.tick(sid, { allowSend: false });
    assert.equal(f.rows[0].handoff_stage, "consumed");
    assert.equal(f.sends.length, 1);
    assert.equal(f.historyReads, 0);
  } finally {
    release("late-sdk-acceptance");
    consumer.close();
  }
});

test("a lost live-consumption reply recovers the original outcome without another send", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  f.add();
  const consumed = [];
  const consumer = f.consumer("native", {
    receivingApi: "native-sdk-v1", onConsumed: (_sid, id) => consumed.push(id),
  });
  await consumer.tick(sid);
  f.session.getEvents = async () => { throw new Error("history unavailable"); };
  f.after = async (_method, path, body) => {
    if (path.endsWith("/outcome") && body.outcome === "consumed") {
      f.after = null;
      throw new Error("consumption reply lost");
    }
  };
  consumer.observe?.(sid, f.event());
  f.clock += 30_001;
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.deepEqual(consumed, [1]);
  f.clock += 30_001;
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.calls.filter((call) => call.path.endsWith("/recovery/1")).length, 1);
  assert.equal(f.calls.filter((call) => call.body?.outcome === "consumed").length, 1);
  assert.equal(f.sends.length, 1);
  assert.deepEqual(consumed, [1]);
  consumer.close();
});

test("bounded live evidence loss retains uncertainty rather than inventing consumption", async () => {
  const f = fixture();
  f.capabilities.compatible_native_inbox = 1;
  f.add();
  const consumer = f.consumer("native", { receivingApi: "native-sdk-v1" });
  await consumer.tick(sid);
  f.session.getEvents = async () => { throw new Error("history unavailable"); };
  const original = f.event();
  const calls = f.calls.length;
  const logs = f.logs.length;
  consumer.observe?.(sid, original);
  for (let id = 2; id <= 1001; id++) {
    consumer.observe?.(sid, { ...original, data: {
      ...original.data, content: original.data.content.replace('msg-id="1"', `msg-id="${id}"`),
    } });
  }
  assert.equal(f.calls.length, calls);
  assert.equal(f.logs.length, logs, "overflow diagnostics must also stay off the native callback");
  f.clock += 30_001;
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "sdk_accepted");
  assert.ok(f.logs.some((line) => line.includes("native input observation overflow")));
  consumer.observe?.(sid, original);
  await consumer.tick(sid, { allowSend: false });
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.equal(f.sends.length, 1);
  consumer.close();
});

test("unavailable history keeps legacy original-recovery admission held", async () => {
  const f = fixture();
  f.add();
  const consumer = f.consumer();
  await consumer.tick(sid);
  const originalEvent = f.event();
  f.add();
  f.session.getEvents = async () => { throw new Error("history unavailable"); };
  f.clock += 30_001;
  await consumer.tick(sid);
  assert.equal(f.sends.length, 1);
  assert.equal(f.rows[1].handoff_id, undefined);
  f.session.getEvents = async () => [originalEvent];
  f.clock += 30_001;
  await consumer.tick(sid);
  assert.equal(f.rows[0].handoff_stage, "consumed");
  assert.equal(f.sends.length, 2);
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

// Managed (idle-aware) receiving: nothing queued and unsubmitted stays ambiguous.
function managedFixture() {
  const f = fixture();
  f.messageBase = true;
  f.capabilities = { compatible_native_inbox: 1, retained_delivery_reattempt: 1 };
  f.turn = new MainTurn();
  f.notice = new PendingInputNotice({ now: () => f.clock });
  f.managed = (incarnation = "consumer-new", options = {}) => f.consumer(incarnation, {
    receivingApi: "native-sdk-v1", mainTurn: f.turn,
    onPending: (target, messages) => f.notice.offered(target, messages),
    onSubmitted: (target, id) => f.notice.confirmed(target, id),
    onConsumed: (target, id) => f.notice.confirmed(target, id),
    ...options,
  });
  // A begun original from an earlier consumer incarnation of this session.
  f.started = (stage = "unknown") => {
    const row = f.add();
    return Object.assign(row, {
      handoff_id: `handoff-${row.id}`, handoff_stage: stage,
      consumer: { session_id: sid, incarnation: "consumer-old" },
    });
  };
  f.history = (row) => ({
    id: `history-${row.id}`, type: "user.message", timestamp: new Date(3_000).toISOString(),
    data: { source: "agent-bridge", turnId: `turn-${row.id}`,
      content: `<agent-message retained-session="${sid}" retained-consumer="consumer-old" `
        + `retained-handoff="${row.handoff_id}" msg-id="${row.id}" from="peer">body-${row.id}</agent-message>` },
  });
  f.run = () => f.turn.observe({ type: "assistant.turn_start" });
  f.idle = () => { f.run(); f.turn.observe({ type: "session.idle" }); };
  f.nudges = () => f.sends.filter((send) => send.prompt === RESUME_NUDGE).length;
  return f;
}

test("an unsubmitted original from a prior incarnation is submitted once the session goes idle", async () => {
  const f = managedFixture();
  const first = f.started("unknown");
  const second = f.started("sending");
  const inbox = f.managed();
  f.run();
  await inbox.tick(sid);
  assert.equal(f.sends.length, 0);
  assert.equal(f.historyReads, 0);
  assert.deepEqual([...f.notice.pending], [1, 2]);
  f.idle();
  await inbox.tick(sid);
  await inbox.tick(sid);
  await inbox.tick(sid);
  assert.equal(f.historyReads, 1, "one bounded history check per idle episode");
  assert.equal(f.sends.length, 2);
  assert.match(f.sends[0].prompt, /^<agent-message retained-session="native-a" retained-consumer="consumer-old" retained-handoff="handoff-1" /);
  assert.match(f.sends[0].prompt, /msg-id="1"/);
  assert.match(f.sends[1].prompt, /msg-id="2"/);
  assert.equal(f.sends[0].mode, "enqueue");
  assert.equal(first.handoff_stage, "sdk_accepted");
  assert.equal(second.handoff_stage, "sdk_accepted");
  assert.equal(first.attempt, 2);
  const begin = f.calls.find((call) => call.path.endsWith("/begin")).body;
  assert.equal(begin.attempt, 1);
  assert.deepEqual(begin.consumer, f.current);
  assert.equal(f.notice.pending.size, 0);
  assert.equal(f.nudges(), 0);
});

test("an original already in session history is marked consumed and never resent", async () => {
  const f = managedFixture();
  const row = f.started("unknown");
  f.events = [f.history(row)];
  const inbox = f.managed();
  f.run();
  await inbox.tick(sid);
  f.idle();
  await inbox.tick(sid);
  assert.equal(row.handoff_stage, "consumed");
  assert.equal(f.calls.filter((call) => call.path.endsWith("/begin")).length, 0);
  assert.deepEqual(f.sends.map((send) => send.prompt), [RESUME_NUDGE]);
  assert.equal(f.sends[0].mode, "enqueue");
  assert.equal(f.notice.pending.size, 0);
  await inbox.tick(sid);
  f.clock += 30_001;
  await inbox.tick(sid);
  assert.equal(f.nudges(), 1, "one nudge per idle episode");
  f.idle();
  await inbox.tick(sid);
  assert.equal(f.nudges(), 1, "a later idle with an empty queue is never nudged");
});

test("an idle session with an empty queue gets no prompt and no history read", async () => {
  const f = managedFixture();
  const inbox = f.managed();
  f.run();
  await inbox.tick(sid);
  f.idle();
  await inbox.tick(sid);
  await inbox.tick(sid);
  assert.equal(f.sends.length, 0);
  assert.equal(f.historyReads, 0);
});

test("mixed redundant and new originals submit only the new one and never nudge", async () => {
  const f = managedFixture();
  const redundant = f.started("unknown");
  f.events = [f.history(redundant)];
  const inbox = f.managed();
  f.run();
  await inbox.tick(sid);
  assert.equal(f.sends.length, 0);
  f.add();
  f.idle();
  await inbox.tick(sid);
  await inbox.tick(sid);
  await inbox.tick(sid);
  assert.equal(redundant.handoff_stage, "consumed");
  assert.equal(f.sends.length, 1);
  assert.match(f.sends[0].prompt, /msg-id="2"/);
  assert.equal(f.nudges(), 0);
});

test("unavailable history defers submission to a later idle check, then submits", async () => {
  const f = managedFixture();
  const row = f.started("unknown");
  let fail = true;
  const getEvents = f.session.getEvents;
  f.session.getEvents = async () => {
    if (fail) { f.historyReads++; throw new Error("history unavailable"); }
    return getEvents();
  };
  const inbox = f.managed();
  f.idle();
  await inbox.tick(sid);
  await inbox.tick(sid);
  assert.equal(f.sends.length, 0);
  assert.equal(row.handoff_stage, "unknown");
  assert.equal(f.historyReads, 1, "cooldown bounds history retries");
  assert.equal(f.nudges(), 0);
  fail = false;
  f.clock += 30_001;
  await inbox.tick(sid);
  assert.equal(f.sends.length, 1);
  assert.equal(row.handoff_stage, "sdk_accepted");
  assert.equal(f.notice.pending.size, 0);
});

test("bounded attempts end in an explicit terminal failure, not a lingering record", async () => {
  const f = managedFixture();
  const row = f.started("unknown");
  let attempts = 0;
  f.session.send = async () => { attempts++; throw new Error("SDK reply lost"); };
  const inbox = f.managed();
  f.idle();
  for (let i = 0; i < 6; i++) {
    await inbox.tick(sid);
    f.clock += 30_001;
  }
  assert.equal(attempts, 2);
  assert.deepEqual(row.disposition, { state: "failed", code: "delivery_attempts_exhausted" });
  assert.equal(f.historyReads, 1, "a full history read happens at most once per original");
  assert.equal(f.notice.pending.size, 0);
  const regrants = f.regrants;
  await inbox.tick(sid);
  assert.equal(f.regrants, regrants);
});

test("live-observed and offered originals settle without any history read", async () => {
  const f = managedFixture();
  const observed = f.started("unknown");
  const inbox = f.managed();
  inbox.observe(sid, f.history(observed));
  f.add();
  f.idle();
  await inbox.tick(sid);
  // Both pre-submit decisions were made without reading history.
  assert.equal(f.historyReads, 0);
  await inbox.tick(sid);
  assert.equal(observed.handoff_stage, "consumed");
  assert.equal(f.rows[1].handoff_stage, "sdk_accepted");
  // Only the submitted original then seeks consumption, once, as before.
  assert.equal(f.historyReads, 1);
  await inbox.tick(sid);
  assert.equal(f.historyReads, 1);
  assert.equal(f.sends.length, 1);
  assert.match(f.sends[0].prompt, /msg-id="2"/);
});

test("a bridge without re-attempt support never resends and clears the pending notice", async () => {
  const f = managedFixture();
  f.capabilities = { compatible_native_inbox: 1 };
  const row = f.started("unknown");
  const inbox = f.managed();
  f.idle();
  await inbox.tick(sid);
  await inbox.tick(sid);
  assert.equal(f.sends.length, 0);
  assert.equal(row.handoff_stage, "unknown");
  assert.equal(f.notice.pending.size, 0);
  assert.equal(f.calls.some((call) => call.body?.reattempt), false);
});

test("bounded recent-first history is preferred over a full event read", async () => {
  const f = managedFixture();
  const row = f.started("unknown");
  const reads = [];
  f.session.rpc.eventLog = { read: async (options) => {
    reads.push(options);
    return { events: [f.history(row)], cursor: null, hasMore: false, cursorStatus: "ok" };
  } };
  const inbox = f.managed();
  f.idle();
  await inbox.tick(sid);
  assert.equal(f.historyReads, 0);
  assert.equal(reads.length, 1);
  assert.equal(reads[0].direction, "backward");
  assert.deepEqual(reads[0].types, ["user.message"]);
  assert.equal(row.handoff_stage, "consumed");
  assert.equal(f.calls.filter((call) => call.path.endsWith("/begin")).length, 0);
});