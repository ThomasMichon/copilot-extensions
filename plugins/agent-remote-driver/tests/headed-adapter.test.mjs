import assert from "node:assert/strict";
import { setImmediate as tick } from "node:timers/promises";
import test from "node:test";
import { createHeadedAdapter } from "../../../libs/copilot-driver-core/src/agent_remote_driver/headed/acp-adapter.mjs";

function fixture(options = {}) {
  let observer;
  const sent = [];
  const emitted = [];
  const session = {
    sessionId: "native-fixture",
    on(callback) { observer = callback; return () => { observer = null; }; },
    async send(options) { sent.push(options); },
    async abort() {},
  };
  const adapter = createHeadedAdapter(session, {
    emit: (message) => emitted.push(message), cwd: "workspace", ...options,
  });
  return {
    adapter, session, sent, emitted,
    event: (type, data = {}, extra = {}) => observer({ type, data, ...extra }),
    prompt: (id = 1, prompt = 'literal\n"quote" & $value') => adapter.handle({
      jsonrpc: "2.0", id, method: "session/prompt",
      params: { sessionId: session.sessionId, prompt: [{ type: "text", text: prompt }] },
    }),
    cancel: () => adapter.handle({
      jsonrpc: "2.0", method: "session/cancel", params: { sessionId: session.sessionId },
    }),
  };
}

test("auth wait freezes sends without laundering native control or uncertain operations", async () => {
  const states = [];
  const f = fixture({ observeState: (state) => states.push(state) });
  try {
    const operation = f.prompt();
    const frozen = f.adapter.authWait();
    assert.equal(frozen.pending, true);
    assert.equal(frozen.waiting, true);
    assert.match((await f.prompt(2)).error.message, /unavailable/);
    f.event("user.message", { content: "native input while stopping" });
    assert.match((await operation).error.message, /Native input took control/);
    assert.equal(f.adapter.authWait().controlled, false);
    assert.equal(f.sent.length, 1);
    assert.ok(states.some((state) => state.pending && state.waiting));
  } finally { f.adapter.close(); }
  assert.equal(states.at(-1).closed, true);
});

test("idle auth wait is reversible only through a new receiving incarnation", async () => {
  const f = fixture();
  try {
    assert.deepEqual(f.adapter.authWait(), {
      controlled: true, closed: false, pending: false,
      sending: false, outstanding: false, waiting: true,
    });
    assert.match((await f.prompt()).error.message, /unavailable/);
    assert.equal(f.sent.length, 0);
    assert.equal(typeof f.adapter.authRenew, "undefined");
  } finally { f.adapter.close(); }
});

test("an unobserved launch seed remains outstanding at auth stop", () => {
  const f = fixture({ startupPrompt: "original seed" });
  try {
    assert.equal(f.adapter.authWait().outstanding, true);
    f.event("user.message", { content: "original seed" });
    assert.equal(f.adapter.authWait().outstanding, true);
    f.event("session.idle");
    assert.equal(f.adapter.authWait().outstanding, false);
  } finally { f.adapter.close(); }
});

test("literal text goes to one native session and stale idle cannot complete it", async () => {
  const f = fixture();
  try {
    f.event("session.idle");
    const operation = f.prompt();
    let completed = false;
    operation.then(() => { completed = true; });
    await tick();
    assert.equal(completed, false);
    assert.equal(f.sent.length, 1);
    assert.equal(f.sent[0].prompt, 'literal\n"quote" & $value');
    f.event("user.message", { source: f.sent[0].source }, { id: "owned-input" });
    f.event("assistant.message_delta", { deltaContent: "native answer" });
    f.event("session.idle");
    assert.deepEqual(await operation, { jsonrpc: "2.0", id: 1, result: { stopReason: "end_turn" } });
    assert.equal(f.emitted[0].params.sessionId, "native-fixture");
    assert.equal(f.emitted[0].params.update.content.text, "native answer");
  } finally { f.adapter.close(); }
});

test("native idle before send acknowledgement cannot disguise a failed send", async () => {
  const f = fixture();
  f.session.send = async (options) => {
    f.sent.push(options);
    f.event("user.message", { source: options.source });
    f.event("session.idle");
    await tick();
    throw new Error("transport outcome unknown");
  };
  try {
    assert.match((await f.prompt()).error.message, /unknown.*not be replayed/);
    assert.match((await f.prompt(2)).error.message, /unavailable/);
    assert.equal(f.sent.length, 1);
  } finally { f.adapter.close(); }
});

test("cancellation remains pending until abort settles, including a failed abort", async () => {
  const f = fixture();
  f.session.abort = async () => {
    f.event("session.idle");
    await tick();
    throw new Error("abort outcome unknown");
  };
  try {
    const prompt = f.prompt();
    f.event("user.message", { source: f.sent[0].source });
    await f.cancel();
    assert.match((await prompt).error.message, /cancellation outcome is unknown/);
    assert.match((await f.prompt(2)).error.message, /unavailable/);
  } finally { f.adapter.close(); }
});

test("human root input revokes remote control rather than aborting or sending again", async () => {
  const f = fixture();
  let aborts = 0;
  f.session.abort = async () => { aborts++; };
  try {
    const prompt = f.prompt();
    f.event("user.message", { source: f.sent[0].source });
    f.event("user.message", { content: "human input" }, { id: "human-input" });
    assert.match((await prompt).error.message, /Native input took control/);
    assert.match((await f.prompt(2)).error.message, /unavailable/);
    await f.cancel();
    assert.equal(aborts, 0);
    assert.equal(f.sent.length, 1);
  } finally { f.adapter.close(); }
});

test("unsupported creation, restoration and non-text input fail before native effects", async () => {
  const f = fixture();
  try {
    for (const [method, params] of [
      ["session/new", { cwd: "workspace", mcpServers: [] }],
      ["session/load", { sessionId: "native-fixture", cwd: "workspace", mcpServers: [] }],
      ["session/prompt", { sessionId: "other-native", prompt: [{ type: "text", text: "wrong" }] }],
      ["session/prompt", { sessionId: "native-fixture", prompt: [{ type: "image", data: "unsupported" }] }],
    ]) {
      assert.ok((await f.adapter.handle({ jsonrpc: "2.0", id: 1, method, params })).error);
    }
    assert.equal(await f.adapter.handle({
      jsonrpc: "2.0", method: "session/prompt",
      params: { sessionId: "native-fixture", prompt: [{ type: "text", text: "no request id" }] },
    }), null);
    assert.equal(f.sent.length, 0);
    const initialized = await f.adapter.handle({
      jsonrpc: "2.0", id: 2, method: "initialize", params: { protocolVersion: 1 },
    });
    assert.equal(initialized.result.agentCapabilities.loadSession, false);
    assert.equal(initialized.result._meta.nativeSessionId, "native-fixture");
  } finally { f.adapter.close(); }
});

test("peer loss settles unknown prompt without abort or replay", async () => {
  const f = fixture();
  f.session.abort = async () => assert.fail("peer loss is not native shutdown");
  const prompt = f.prompt();
  f.adapter.close();
  assert.match((await prompt).error.message, /no operation will be replayed/);
  assert.equal(f.sent.length, 1);
});

test("the original native startup prompt is observed once, never resent or treated as a standing grant", async () => {
  const f = fixture({ startupPrompt: "original native seed" });
  try {
  f.event("user.message", { content: "original native seed" }, { id: "startup" });
  f.event("session.idle");
  assert.equal(f.sent.length, 0);
  const prompt = f.prompt();
  f.event("user.message", { source: f.sent[0].source });
  f.event("session.idle");
  assert.equal((await prompt).result.stopReason, "end_turn");
  f.event("user.message", { content: "original native seed" }, { id: "later-human" });
  assert.match((await f.prompt(2)).error.message, /unavailable/);
  assert.equal(f.sent.length, 1);
  } finally { f.adapter.close(); }
});

test("native send preserves explicit urgency and returns the actual acceptance id without waiting for idle", async () => {
  const f = fixture();
  f.session.send = async (options) => {
    f.sent.push(options);
    f.event("user.message", { source: options.source, content: options.prompt });
    return `native-message-${f.sent.length}`;
  };
  try {
    for (const mode of ["enqueue", "immediate"]) {
      const reply = await f.adapter.handle({
        jsonrpc: "2.0", id: mode, method: "copilot-driver/native-send",
        params: {
          sessionId: f.session.sessionId, prompt: "literal\npeer message",
          displayPrompt: "attributed peer", mode,
        },
      });
      assert.equal(reply.result.messageId, `native-message-${f.sent.length}`);
      assert.equal(f.sent.at(-1).mode, mode);
      assert.equal(f.sent.at(-1).displayPrompt, "attributed peer");
      assert.match(f.sent.at(-1).source, /^agent-remote-driver-[0-9a-f-]{36}$/);
    }
    const initialized = await f.adapter.handle({
      jsonrpc: "2.0", id: "support", method: "initialize", params: { protocolVersion: 1 },
    });
    assert.deepEqual(initialized.result._meta.nativeSend, {
      version: 1, modes: ["enqueue", "immediate"], receipt: "native-message-id",
    });
  } finally { f.adapter.close(); }
});

test("native send refuses mixed options and concurrent effects before touching the SDK", async () => {
  const f = fixture();
  const params = { sessionId: f.session.sessionId, prompt: "one", mode: "enqueue" };
  const send = (id, value) => f.adapter.handle({
    jsonrpc: "2.0", id, method: "copilot-driver/native-send", params: value,
  });
  try {
    for (const value of [
      { ...params, sessionId: "other" }, { ...params, mode: "interrupt" },
      { ...params, source: "user" }, { ...params, prompt: "" }, { ...params, displayPrompt: {} },
    ]) assert.ok((await send("invalid", value)).error);
    assert.equal(f.sent.length, 0);
    let settle;
    f.session.send = (options) => {
      f.sent.push(options);
      return new Promise((resolve) => { settle = resolve; });
    };
    const first = send("first", params);
    assert.match((await send("duplicate", params)).error.message, /already in flight/);
    assert.match((await f.prompt()).error.message, /already in flight/);
    settle("actual-native-id");
    assert.equal((await first).result.messageId, "actual-native-id");
    assert.equal(f.sent.length, 1);
  } finally { f.adapter.close(); }
});

test("unknown native send or intervening human input permanently holds delivery without replay", async () => {
  for (const outcome of ["lost", "missing-id", "human", "closed"]) {
    const f = fixture();
    f.session.send = async (options) => {
      f.sent.push(options);
      if (outcome === "lost") throw new Error("reply lost");
      if (outcome === "human") f.event("user.message", { content: "operator input" });
      if (outcome === "closed") f.adapter.close();
      return outcome === "missing-id" ? undefined : "actual-native-id";
    };
    const send = () => f.adapter.handle({
      jsonrpc: "2.0", id: 1, method: "copilot-driver/native-send",
      params: { sessionId: f.session.sessionId, prompt: "one", mode: "immediate" },
    });
    try {
      assert.match((await send()).error.message, /unknown.*not be replayed/);
      assert.match((await send()).error.message, /unavailable/);
      assert.equal(f.sent.length, 1);
    } finally { f.adapter.close(); }
  }
});

test("a missed startup observation cannot exempt later identical human input after controlled admission", async () => {
  for (const method of ["session/prompt", "copilot-driver/native-send"]) {
    const f = fixture({ startupPrompt: "original seed" });
    f.session.send = async (options) => {
      f.sent.push(options);
      f.event("user.message", { source: options.source, content: options.prompt });
      return "actual-native-id";
    };
    try {
      const request = f.adapter.handle({
        jsonrpc: "2.0", id: 1, method,
        params: {
          sessionId: f.session.sessionId,
          ...(method === "session/prompt"
            ? { prompt: [{ type: "text", text: "remote work" }] }
            : { prompt: "remote work", mode: "enqueue" }),
        },
      });
      if (method === "copilot-driver/native-send") assert.ok((await request).result);
      f.event("user.message", { source: "user", content: "original seed" });
      if (method === "session/prompt") assert.match((await request).error.message, /Native input took control/);
      assert.match((await f.prompt(2)).error.message, /unavailable/);
      assert.equal(f.sent.length, 1);
    } finally { f.adapter.close(); }
  }
});
