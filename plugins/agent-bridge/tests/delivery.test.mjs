import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DELIVERY_SOURCE,
  InFlightMessages,
  buildDeliveredSendOptions,
  consumedMessageId,
  deliveryPlan,
  escAttr,
  renderDeliveredPrompt,
  STATUS_CHECK_INLINE_GUIDANCE,
} from "../extensions/agent-bridge/delivery.mjs";

test("buildDeliveredSendOptions always tags an explicit non-user source", () => {
  const options = buildDeliveredSendOptions({
    id: 1,
    sender: "peer-agent",
    body: "hello",
  });
  assert.equal(options.source, "agent-bridge");
  assert.equal(options.source, DELIVERY_SOURCE);
  // Must satisfy the runtime's `agent-<agent-id>` provenance-tag shape
  // (SendRequest.source) so apply_public_send_admission never defaults this
  // delivery to source: "user" and collides with a real operator keystroke.
  assert.match(options.source, /^agent-.+/);
});

test("buildDeliveredSendOptions carries source even with minimal message fields", () => {
  // No sender, no kind, no reply_to -- the degenerate case must still tag source.
  const options = buildDeliveredSendOptions({ id: 2, body: "" });
  assert.equal(options.source, "agent-bridge");
  assert.equal(typeof options.prompt, "string");
  assert.equal(typeof options.displayPrompt, "string");
});

test("buildDeliveredSendOptions never omits source regardless of message shape", () => {
  const shapes = [
    { id: 3, sender: "a", body: "x" },
    { id: 4, sender: "a", body: "x", kind: "notify" },
    { id: 5, sender: "a", body: "x", kind: "status-check" },
    { id: 6, sender: "a", body: "x", reply_to: 2 },
    { id: 7, sender: null, body: "x" },
  ];
  for (const msg of shapes) {
    const options = buildDeliveredSendOptions(msg);
    assert.ok(
      Object.prototype.hasOwnProperty.call(options, "source"),
      `source missing for message shape: ${JSON.stringify(msg)}`,
    );
    assert.equal(options.source, "agent-bridge");
  }
});

test("deliveryPlan maps queue and unknown delivery to default enqueue send", () => {
  const queued = deliveryPlan({ id: 8, sender: "a", body: "x", delivery: "queue" });
  assert.equal(queued.abortFirst, false);
  assert.equal(queued.options.mode, undefined);
  assert.equal(queued.options.source, DELIVERY_SOURCE);

  const unknown = deliveryPlan({ id: 9, sender: "a", body: "x", delivery: "later" });
  assert.equal(unknown.abortFirst, false);
  assert.equal(unknown.options.mode, undefined);
  assert.equal(unknown.options.source, DELIVERY_SOURCE);
});

test("deliveryPlan maps steer to immediate send without abort", () => {
  const plan = deliveryPlan({ id: 10, sender: "a", body: "x", delivery: "steer" });
  assert.equal(plan.abortFirst, false);
  assert.equal(plan.options.mode, "immediate");
  assert.equal(plan.options.source, DELIVERY_SOURCE);
});

test("deliveryPlan maps interrupt to abort, then an immediate send", () => {
  const plan = deliveryPlan({ id: 11, sender: "a", body: "x", delivery: "interrupt" });
  assert.equal(plan.abortFirst, true);
  // Immediate, so the interrupting message is not queued behind earlier
  // enqueued messages the abort releases.
  assert.equal(plan.options.mode, "immediate");
  assert.equal(plan.options.source, DELIVERY_SOURCE);
});

test("renderDeliveredPrompt escapes attribute values", () => {
  const rendered = renderDeliveredPrompt({
    id: 1,
    sender: '"><script>',
    body: "hi",
  });
  assert.ok(!rendered.includes('sender="\"><script>"'));
  assert.match(rendered, /from="&quot;&gt;&lt;script&gt;"/);
});

test("escAttr escapes the reserved XML characters", () => {
  assert.equal(escAttr(`&"<>`), "&amp;&quot;&lt;&gt;");
  assert.equal(escAttr(null), "");
  assert.equal(escAttr(undefined), "");
});

test("status-check without reply-to asks for an inline answer", () => {
  const inline = renderDeliveredPrompt({ id: 8, sender: "bridge-ui", body: "status?", kind: "status-check" });
  assert.ok(inline.includes(STATUS_CHECK_INLINE_GUIDANCE));
  assert.ok(!inline.includes("send <reply-to>"));
  const routed = renderDeliveredPrompt({ id: 9, sender: "a", body: "status?", kind: "status-check", reply_to: "wt-a" });
  assert.ok(routed.includes("send <reply-to>"));
});

function recorded(msg) {
  return {
    type: "user.message",
    data: { source: DELIVERY_SOURCE, content: "Message from a (via agent-bridge)", transformedContent: renderDeliveredPrompt(msg) },
  };
}

test("consumedMessageId reads our envelope's msg-id and ignores other user turns", () => {
  assert.equal(consumedMessageId(recorded({ id: 41, sender: "a", body: "x" })), 41);
  assert.equal(consumedMessageId({ type: "user.message", data: { source: "user", content: "hi" } }), null);
  assert.equal(consumedMessageId({ type: "assistant.message", data: {} }), null);
});

test("an interrupt re-sends exactly the older messages the CLI has not recorded", () => {
  const inFlight = new InFlightMessages();
  const a = { id: 1, sender: "a", body: "seen" };
  const b = { id: 2, sender: "a", body: "queued" };
  const c = { id: 3, sender: "a", body: "steered" };
  for (const m of [a, b, c]) inFlight.sent(m);
  inFlight.observe(recorded(a)); // the CLI already took message 1
  assert.deepEqual(inFlight.takeBefore(4).map((m) => m.id), [2, 3]);
  assert.deepEqual(inFlight.takeBefore(4), []); // taken once
});
