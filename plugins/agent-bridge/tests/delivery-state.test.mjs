import { test } from "node:test";
import assert from "node:assert/strict";
import { DeliveryAttention, publishDeliveryAttention } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/delivery-state.mjs";

const event = (type, requestId, extra = {}) => ({ type, data: { requestId }, ...extra });
const identity = { pid: 123, process_started_at: 100 };

test("mid-turn absence is unknown; exact root pending input clears independently", () => {
  const attention = new DeliveryAttention();
  attention.observe(event("tool.execution_start", "tool"), "s1");
  assert.equal(attention.snapshot(identity, false).complete, false);
  attention.observe(event("elicitation.requested", "q1"), "s1");
  attention.observe(event("permission.requested", "p1"), "s1");
  attention.observe(event("user_input.requested", "child", { agentId: "child" }), "s1");
  assert.deepEqual(attention.snapshot(identity, false).pending, ["permission", "question"]);
  attention.observe(event("elicitation.completed", "q1"), "s1");
  assert.deepEqual(attention.snapshot(identity, false).pending, ["permission"]);
  attention.observe(event("assistant.turn_end"), "s1");
  assert.deepEqual(attention.snapshot(identity, false).pending, ["permission"]);
  attention.observe(event("permission.completed", "p1"), "s1");
  assert.deepEqual(attention.snapshot(identity, false).pending, []);
  assert.equal(attention.complete, false);
  attention.observe(event("assistant.idle"), "s1");
  assert.equal(attention.complete, true);
  attention.observe(event("permission.requested", "new"), "s2");
  assert.equal(attention.complete, false);
  assert.deepEqual(attention.snapshot(identity, false).pending, ["permission"]);
});

test("publisher gates new syntax on daemon support and retains mute state", async () => {
  const attention = new DeliveryAttention();
  attention.observe(event("assistant.idle"), "s1");
  const calls = [];
  let supported = false;
  const request = async (method, path, body) => {
    calls.push({ method, path, body });
    return path === "/health" ? { capabilities: { live_delivery_observation: supported ? 1 : 0 } } : {};
  };
  const options = { sid: "s1", attention, identity, muted: true, request };
  await publishDeliveryAttention(options);
  assert.deepEqual(calls.map(c => c.method), ["GET"]);
  supported = true;
  attention.capability = null;
  await publishDeliveryAttention(options);
  assert.deepEqual(calls.at(-1), {
    method: "POST", path: "/api/v1/live-sessions/s1/delivery-state",
    body: attention.snapshot(identity, true),
  });
  await publishDeliveryAttention(options);
  assert.equal(calls.filter(c => c.method === "POST").length, 1);
  await publishDeliveryAttention({ ...options, muted: false });
  assert.equal(calls.filter(c => c.method === "POST").length, 2);
  assert.equal(calls.filter(c => c.method === "GET").length, 2);
  const before = calls.length;
  await publishDeliveryAttention({ ...options, isCurrent: () => false });
  assert.equal(calls.length, before);
});
