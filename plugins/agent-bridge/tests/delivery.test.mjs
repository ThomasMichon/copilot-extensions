import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DELIVERY_SOURCE,
  buildDeliveredSendOptions,
  escAttr,
  renderDeliveredPrompt,
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
