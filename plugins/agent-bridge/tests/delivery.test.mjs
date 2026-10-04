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
  PROMPT_REPLY_GUIDANCE,
  controlPlan,
  modeApplied,
  adoptSessionId,
  serializedRegister,
  REJECTED_RETRY_MS,
} from "../extensions/agent-bridge/delivery.mjs";

test("a same-process resume switches registration to the resumed id", () => {
  const state = { sessionId: null, registered: false };
  assert.equal(adoptSessionId(state, "placeholder"), true); // late id: register it
  state.registered = true;
  assert.equal(adoptSessionId(state, "placeholder"), false); // same id: nothing to do
  assert.equal(adoptSessionId(state, undefined), false); // an event without an id
  assert.equal(adoptSessionId(state, "resumed"), true); // the resume renamed it
  assert.deepEqual(state, { sessionId: "resumed", registered: false });
  assert.equal(adoptSessionId(state, "resumed"), false);
});

function deferredPoster() {
  const calls = [];
  const post = (id) => new Promise((resolve) => calls.push({ id, resolve }));
  return { calls, post };
}
const tick = () => new Promise((r) => setTimeout(r, 0));

test("a resumed id refused for a crashed predecessor's row keeps the placeholder serving", async () => {
  // The resumed conversation's id still has the crashed process's expired row:
  // the bridge refuses this process for it (incarnation mismatch).
  let clock = 1_000;
  const state = { sessionId: "placeholder", registered: false };
  const posts = [];
  const post = async (id) => { posts.push(id); return id === "resumed" ? "rejected" : true; };
  const register = serializedRegister(state, post, () => {}, { now: () => clock });
  await register();
  assert.equal(adoptSessionId(state, "resumed", clock), true);
  await register();
  assert.deepEqual(posts, ["placeholder", "resumed", "placeholder"]);
  assert.deepEqual([state.sessionId, state.registered], ["placeholder", true]);  // inbox/flush go on
  assert.equal(adoptSessionId(state, "resumed", clock + 1), false);  // not re-tried on every event
  await register();  // a heartbeat refreshes the placeholder
  assert.equal(posts.at(-1), "placeholder");
  clock += REJECTED_RETRY_MS;
  assert.equal(adoptSessionId(state, "resumed", clock), true);  // retried later (the row may be purged)
  assert.deepEqual(await register.close(), ["placeholder"]);
});

test("an accepted id-only registration later refused for the same id revokes delivery and cleanup", async () => {
  // The first registration went out before this process's identity was known
  // and was accepted onto another incarnation's row; the next heartbeat, with
  // pid and start time, is refused for that very id.
  const state = { sessionId: "resumed", registered: false };
  let refuse = false;
  const posts = [];
  const post = async (id) => { posts.push(id); return refuse ? "rejected" : true; };
  const register = serializedRegister(state, post);
  assert.equal(await register(), true);
  assert.equal(state.registered, true);
  refuse = true;
  assert.equal(await register(), false);
  assert.deepEqual([state.sessionId, state.registered], ["resumed", false]);  // inbox/flush stop
  assert.deepEqual(await register.close(), []);  // shutdown leaves the other process's row alone
  assert.deepEqual(posts, ["resumed", "resumed"]);
});

test("a revoked id is served again once the other incarnation's row is gone", async () => {
  const state = { sessionId: "resumed", registered: false };
  let result = true;
  const register = serializedRegister(state, async () => result);
  await register();
  result = "rejected";
  await register();
  assert.equal(state.registered, false);
  result = true;  // the other row was purged: the next heartbeat registers it
  assert.equal(await register(), true);
  assert.equal(state.registered, true);
  assert.deepEqual(await register.close(), ["resumed"]);
});

test("an old id refused after a rename is no longer deregistered at shutdown", async () => {
  // The placeholder was registered; its in-flight heartbeat comes back refused
  // (its row now belongs to another process) only after a resume renamed it.
  const state = { sessionId: "placeholder", registered: false };
  const { calls, post } = deferredPoster();
  const register = serializedRegister(state, post);
  const first = register();
  await tick();
  calls[0].resolve(true);
  await first;
  const beat = register();
  await tick();
  adoptSessionId(state, "resumed");
  calls[1].resolve("rejected");  // the placeholder's heartbeat
  await tick();
  calls[2].resolve(true);  // the resumed id registers
  await beat;
  assert.deepEqual([state.sessionId, state.registered], ["resumed", true]);
  assert.deepEqual(await register.close(), ["resumed"]);  // not the other process's placeholder row
});

test("registration waits for ready (the session metadata) and shutdown stops the wait", async () => {
  const state = { sessionId: "placeholder", registered: false };
  let release;
  const ready = new Promise((resolve) => { release = resolve; });
  const posts = [];
  const register = serializedRegister(state, async (id) => { posts.push(id); return true; },
    () => {}, { ready });
  const first = register();
  await tick();
  assert.deepEqual(posts, []);  // nothing registers before the metadata settles
  adoptSessionId(state, "resumed");  // a resume meanwhile: only the current id posts
  release();
  assert.equal(await first, true);
  assert.deepEqual(posts, ["resumed"]);

  const waiting = { sessionId: "s", registered: false };
  const never = serializedRegister(waiting, async () => true, () => {}, { ready: new Promise(() => {}) });
  never();
  assert.deepEqual(await never.close(), []);  // close() doesn't hang on a slow lookup
  assert.equal(waiting.registered, false);
});

test("shutdown drains a pending registration and returns every id to deregister", async () => {
  const state = { sessionId: "placeholder", registered: false };
  const { calls, post } = deferredPoster();
  const register = serializedRegister(state, post);
  register();
  await tick();
  adoptSessionId(state, "resumed");  // a resume while the placeholder's POST is pending
  const closing = register.close();
  register();  // a heartbeat after shutdown began: not admitted
  let done = false;
  closing.then(() => { done = true; });
  await tick();
  assert.equal(done, false);  // waits for the in-flight registration
  calls[0].resolve(true);
  const ids = await closing;
  await tick();
  assert.deepEqual(calls.map((c) => c.id), ["placeholder"]);  // the resumed id is never posted after it
  assert.deepEqual(ids, ["placeholder"]);
});

test("a rename while the old id's registration is in flight registers the new id before ready", async () => {
  const state = { sessionId: "placeholder", registered: false };
  const { calls, post } = deferredPoster();
  const register = serializedRegister(state, post);
  const first = register();
  await tick();
  adoptSessionId(state, "resumed");
  const second = register();  // the rename's own registration
  calls[0].resolve(true);     // the old id's request completes first
  await tick();
  assert.equal(state.registered, false);  // not ready: the current id isn't registered yet
  assert.deepEqual(calls.map((c) => c.id), ["placeholder", "resumed"]);
  calls[1].resolve(true);
  await first;
  await tick();
  assert.equal(state.registered, true);
  calls.slice(2).forEach((c) => c.resolve(true));
  await second;
  assert.ok(calls.slice(1).every((c) => c.id === "resumed"));
});

test("a heartbeat queued before a rename never registers the old id after the new one", async () => {
  const state = { sessionId: "placeholder", registered: true };
  const { calls, post } = deferredPoster();
  const register = serializedRegister(state, post);
  const inFlight = register();
  const heartbeat = register();  // queued behind it, before the rename
  await tick();
  adoptSessionId(state, "resumed");
  const renamed = register();
  for (let i = 0; i < 6 && calls.some((c) => !c.done); i++) {
    for (const c of calls) if (!c.done) { c.done = true; c.resolve(true); }
    await tick();
  }
  await Promise.all([inFlight, heartbeat, renamed]);
  const ids = calls.map((c) => c.id);
  assert.equal(ids[0], "placeholder");
  assert.ok(ids.slice(1).every((id) => id === "resumed"), ids.join(","));
  assert.equal(state.registered, true);
  // Shutdown deregisters both rows this process registered, newest first.
  assert.deepEqual(await register.close(), ["resumed", "placeholder"]);
});

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

test("an ordinary prompt with reply-to tells the receiver reply-to is the routable address", () => {
  const routed = renderDeliveredPrompt({ id: 12, sender: "wt-sender", body: "do the thing", reply_to: "wt-sender" });
  assert.ok(routed.includes(PROMPT_REPLY_GUIDANCE));
  assert.ok(routed.includes("send <reply-to>"));
});

test("an ordinary prompt with no reply-to carries no reply guidance (nothing to route to)", () => {
  const unrouted = renderDeliveredPrompt({ id: 13, sender: "bridge-ui", body: "do the thing" });
  assert.ok(!unrouted.includes(PROMPT_REPLY_GUIDANCE));
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

test("controlPlan applies only a mode change to a mode the CLI knows", () => {
  assert.deepEqual(controlPlan({ id: 1, kind: "control:set-mode", body: "autopilot" }),
    { action: "set-mode", mode: "autopilot" });
  assert.deepEqual(controlPlan({ id: 2, kind: "control:set-mode", body: " interactive " }),
    { action: "set-mode", mode: "interactive" });
  assert.equal(controlPlan({ id: 3, kind: "control:set-mode", body: "yolo" }).action, "skip");
  assert.equal(controlPlan({ id: 4, kind: "control:reboot", body: "now" }).action, "skip");
  assert.equal(controlPlan({ id: 5, kind: "prompt", body: "autopilot" }).action, "skip");
});

test("modeApplied is false only when the CLI says the mode wasn't applied", () => {
  assert.equal(modeApplied({ status: "applied", modelChanged: false }), true);
  assert.equal(modeApplied({ status: "applied", modelChanged: false, modeApplied: true }), true);
  assert.equal(modeApplied({ status: "rejected", modelChanged: false, modeApplied: false }), false);
  assert.equal(modeApplied(undefined), false);
});
