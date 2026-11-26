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

test("selected loadout failure holds headed input without a native send or fallback", async () => {
  let checks = 0;
  const f = fixture({ validateSendContext: async () => { checks++; throw new Error("another root"); } });
  try {
    assert.match((await f.prompt()).error.message, /Selected native execution context/);
    assert.match((await f.prompt(2)).error.message, /control is unavailable/);
    assert.equal(checks, 1);
    assert.equal(f.sent.length, 0);
  } finally { f.adapter.close(); }
});

test("selected context diagnostics do not forward arbitrary errors or reason fields", async () => {
  for (const fields of [
    {},
    { selectedContextReason: "private-reason", selectedContextPlugin: "private-plugin" },
    { selectedContextReason: "plugin-missing", selectedContextPlugin: "private-plugin" },
  ]) {
    const f = fixture({ validateSendContext: async () => {
      throw Object.assign(new Error("private path and observer payload"), fields);
    } });
    try {
      const { error } = await f.prompt();
      assert.equal(error.message, "Selected native execution context is unavailable; no input was dispatched " +
        `[${fields.selectedContextReason === "plugin-missing" ? "plugin-missing" : "context-validation-failed"}]`);
      assert.equal(f.sent.length, 0);
    } finally { f.adapter.close(); }
  }
});

test("owner comparisons require the exact bounded schema and identity-refusal context", async () => {
  const identity = { id: "match", name: "directory", source: "plugin", status: "running", pid: "match" };
  for (const fields of [
    { selectedContextReason: "plugin-missing", selectedContextPlugin: "connector",
      selectedContextIdentity: identity },
    { selectedContextReason: "extension-identity-mismatch", selectedContextPlugin: "driver",
      selectedContextIdentity: identity },
    ...[
      { ...identity, source: "private-source" }, { ...identity, pid: "private-process" },
      { ...identity, extra: "private-payload" }, { id: "match" }, ["private-payload"],
      { ...identity, get source() { throw new Error("private observer failure"); } },
    ].map((selectedContextIdentity) => ({
      selectedContextReason: "extension-identity-mismatch", selectedContextPlugin: "connector",
      selectedContextIdentity,
    })),
  ]) {
    const f = fixture({ validateSendContext: async () => {
      throw Object.assign(new Error("private observer payload"), fields);
    } });
    try {
      const { error } = await f.prompt();
      assert.equal(error.message, "Selected native execution context is unavailable; no input was dispatched " +
        `[${fields.selectedContextReason}:${fields.selectedContextPlugin}]`);
      assert.equal(f.sent.length, 0);
    } finally { f.adapter.close(); }
  }
});

test("owner comparisons serialize the same values that passed validation", async () => {
  let reads = 0;
  const identity = {
    id: "match", name: "directory", status: "running", pid: "match",
    get source() { return reads++ === 0 ? "plugin" : "private-observer-payload"; },
  };
  const f = fixture({ validateSendContext: async () => {
    throw Object.assign(new Error("not forwarded"), {
      selectedContextReason: "extension-identity-mismatch", selectedContextPlugin: "connector",
      selectedContextIdentity: identity,
    });
  } });
  try {
    const { error } = await f.prompt();
    assert.equal(error.message, "Selected native execution context is unavailable; no input was dispatched " +
      "[extension-identity-mismatch:connector] " +
      "[owner-checks id=match name=directory source=plugin status=running pid=match]");
    assert.equal(reads, 1);
    assert.equal(f.sent.length, 0);
  } finally { f.adapter.close(); }
});

test("input takeover during asynchronous loadout validation prevents late native dispatch", async () => {
  let release;
  const validation = new Promise((resolve) => { release = resolve; });
  const f = fixture({ validateSendContext: () => validation });
  try {
    const operation = f.prompt();
    assert.match((await f.prompt(2)).error.message, /already in flight/);
    f.event("user.message", { source: "unowned-input" });
    release();
    assert.match((await operation).error.message, /Selected native execution context/);
    assert.equal(f.sent.length, 0);
  } finally { f.adapter.close(); }
});

test("the selected send reservation survives validation's completion microtask", async () => {
  let release;
  const validation = new Promise((resolve) => { release = resolve; });
  const f = fixture({ validateSendContext: () => validation });
  try {
    const operation = f.prompt();
    release();
    const overlapping = await Promise.resolve().then(() => f.prompt(2));
    assert.match(overlapping.error.message, /already in flight/);
    assert.equal(f.sent.length, 1);
    f.event("user.message", { source: f.sent[0].source });
    f.event("session.idle");
    assert.equal((await operation).result.stopReason, "end_turn");
  } finally { f.adapter.close(); }
});

test("takeover after selected validation still prevents dispatch in the caller's continuation", async () => {
  let release;
  const validation = new Promise((resolve) => { release = resolve; });
  const f = fixture({ validateSendContext: () => validation });
  try {
    const operation = f.prompt();
    release();
    queueMicrotask(() => f.event("user.message", { source: "unowned-input" }));
    assert.match((await operation).error.message, /control is unavailable/);
    assert.equal(f.sent.length, 0);
  } finally { f.adapter.close(); }
});

for (const method of ["session/prompt", "copilot-driver/native-send"]) {
  for (const timing of ["during validation", "after validation"]) {
    test(`cancellation ${timing} prevents ${method} dispatch without aborting native work`, async () => {
      let release;
      const validation = new Promise((resolve) => { release = resolve; });
      const f = fixture({ validateSendContext: () => validation });
      let aborts = 0;
      f.session.abort = async () => { aborts++; };
      f.session.send = async (options) => { f.sent.push(options); return "native-message-id"; };
      try {
        const request = () => f.adapter.handle({
          jsonrpc: "2.0", id: 1, method,
          params: {
            sessionId: f.session.sessionId,
            ...(method === "session/prompt"
              ? { prompt: [{ type: "text", text: "cancelled input" }] }
              : { prompt: "cancelled input", mode: "enqueue" }),
          },
        });
        const operation = request();
        if (timing === "during validation") await f.cancel();
        release();
        if (timing === "after validation") queueMicrotask(() => { void f.cancel(); });
        await tick();
        if (f.sent.length) {
          f.event("user.message", { source: f.sent[0].source });
          f.event("session.idle");
        }
        const response = await operation;
        assert.equal(f.sent.length, 0, "cancelled validation must never reach SDK send");
        assert.equal(aborts, 0, "a pre-dispatch cancellation must not abort existing native work");
        assert.match(response.error?.message ?? "", /cancelled.*no input was dispatched/i);
        const next = request();
        await tick();
        assert.equal(f.sent.length, 1, "cancellation releases the reservation without revoking control");
        f.event("user.message", { source: f.sent[0].source });
        f.event("session.idle");
        assert.ok((await next).result);
      } finally { f.adapter.close(); }
    });
  }
}

test("headed interruption holds the input slot until abort, ACP acceptance and idle all settle", async () => {
  const f = fixture();
  let acceptSend, acceptAbort;
  f.session.send = (options) => {
    f.sent.push(options);
    f.event("user.message", { source: options.source });
    return new Promise((resolve) => { acceptSend = resolve; });
  };
  let aborts = 0;
  f.session.abort = () => {
    aborts++;
    return new Promise((resolve) => { acceptAbort = resolve; });
  };
  try {
    const prompt = f.prompt();
    const interruption = f.adapter.interrupt();
    let interrupted = false;
    interruption.then(() => { interrupted = true; });
    await f.cancel();
    assert.equal(aborts, 1, "overlapping cancellation must not dispatch another abort");
    assert.match((await f.prompt(2)).error.message, /already in flight/);
    acceptAbort();
    await tick();
    assert.equal(interrupted, false);
    f.event("session.idle");
    await tick();
    assert.equal(interrupted, false, "idle cannot disguise unconfirmed send acceptance");
    assert.match((await f.prompt(3)).error.message, /already in flight/);
    acceptSend("native-message-id");
    assert.equal((await prompt).result.stopReason, "cancelled");
    await interruption;
    assert.equal(f.sent.length, 1);
    const status = await f.adapter.handle({
      jsonrpc: "2.0", id: "status", method: "copilot-driver/input-status",
      params: { sessionId: f.session.sessionId },
    });
    assert.equal(status.result.nativeSend, "ready");
  } finally { f.adapter.close(); }
});

for (const [cancel, idleTiming] of [
  ["ACP cancellation", "during abort"], ["retained interruption", "during abort"],
  ["ACP cancellation", "after abort"], ["retained interruption", "after abort"],
]) {
  test(`${cancel} settles discarded queued input with idle ${idleTiming}`, async () => {
    const f = fixture();
    f.session.abort = async () => {
      if (idleTiming === "during abort") f.event("session.idle");
    };
    const prompt = f.prompt();
    let interruption;
    try {
      await tick();
      f.event("session.idle");
      await tick();
      let completed = false;
      prompt.then(() => { completed = true; });
      assert.equal(completed, false, "stale idle cannot complete unconsumed input");
      interruption = cancel === "ACP cancellation" ? f.cancel() : f.adapter.interrupt();
      interruption.catch(() => {});
      await tick();
      if (idleTiming === "after abort") {
        assert.equal(completed, false, "abort acceptance alone must not release the input slot");
        assert.match((await f.prompt(2)).error.message, /already in flight/);
        f.event("session.idle");
      }
      await tick();
      assert.equal(completed, true, "successful abort and idle must retire discarded input");
      await interruption;
      assert.equal((await prompt).result.stopReason, "cancelled");
      const next = f.prompt(2);
      f.event("user.message", { source: f.sent[1].source });
      f.event("session.idle");
      assert.equal((await next).result.stopReason, "end_turn");
      assert.equal(f.adapter.authWait().outstanding, false, "discarded source cannot hold retirement");
    } finally {
      f.adapter.close();
      await interruption?.catch(() => {});
      await prompt;
    }
  });
}

test("discarded queued input still requires successful native send acceptance", async () => {
  const f = fixture();
  let rejectSend;
  f.session.send = (options) => {
    f.sent.push(options);
    return new Promise((_resolve, reject) => { rejectSend = reject; });
  };
  f.session.abort = async () => { f.event("session.idle"); };
  try {
    const prompt = f.prompt();
    let interrupted = false;
    const interruption = f.adapter.interrupt();
    interruption.then(() => { interrupted = true; }, () => {});
    await tick();
    assert.equal(interrupted, false);
    assert.match((await f.prompt(2)).error.message, /already in flight/);
    rejectSend(new Error("native acceptance reply lost"));
    assert.match((await prompt).error.message, /unknown.*not be replayed/);
    await assert.rejects(interruption, /cancellation outcome is unknown/);
    assert.match((await f.prompt(3)).error.message, /unavailable/);
    assert.equal(f.sent.length, 1);
  } finally { f.adapter.close(); }
});

for (const origin of ["native-send", "startup"]) {
  test(`${origin} interruption reserves input through deferred root idle`, async () => {
    const f = fixture(origin === "startup" ? { startupPrompt: "original seed" } : {});
    f.session.send = async (options) => { f.sent.push(options); return "native-message-id"; };
    f.session.abort = async () => {};
    let interruption;
    try {
      if (origin === "startup") f.event("user.message", { content: "original seed" });
      else {
        await f.adapter.handle({
          jsonrpc: "2.0", id: 1, method: "copilot-driver/native-send",
          params: { sessionId: f.session.sessionId, prompt: "active native work", mode: "enqueue" },
        });
        f.event("user.message", { source: f.sent[0].source });
      }
      let interrupted = false;
      interruption = f.adapter.interrupt();
      interruption.then(() => { interrupted = true; }, () => {});
      await tick();
      assert.equal(interrupted, false, "abort acceptance is not root idle");
      const overlap = await f.adapter.handle({
        jsonrpc: "2.0", id: 2, method: "copilot-driver/native-send",
        params: { sessionId: f.session.sessionId, prompt: "overlap", mode: "enqueue" },
      });
      assert.match(overlap.error.message, /already in flight/);
      f.event("session.idle");
      await interruption;
      const next = f.prompt(3);
      let completed = false;
      next.then(() => { completed = true; });
      f.event("user.message", { source: f.sent.at(-1).source });
      await tick();
      assert.equal(completed, false, "the interrupted turn's idle cannot complete its successor");
      f.event("session.idle");
      assert.equal((await next).result.stopReason, "end_turn");
    } finally { f.adapter.close(); await interruption?.catch(() => {}); }
  });
}

for (const invalidated of ["close", "error", "human", "auth"]) {
  test(`native interruption's deferred idle wait settles on ${invalidated}`, async () => {
    const f = fixture({ startupPrompt: "original seed" });
    f.session.abort = async () => {};
    let interruption;
    try {
      f.event("user.message", { content: "original seed" });
      let state = "pending";
      interruption = f.adapter.interrupt();
      interruption.then(() => { state = "resolved"; }, () => { state = "rejected"; });
      await tick();
      assert.equal(state, "pending");
      if (invalidated === "close") f.adapter.close();
      if (invalidated === "error") f.event("session.error");
      if (invalidated === "human") f.event("user.message", { content: "operator input" });
      if (invalidated === "auth") f.adapter.authWait();
      await tick();
      assert.equal(state, "rejected", "lost control must settle rather than strand the idle waiter");
      await assert.rejects(interruption, /cancellation outcome is unknown/);
      assert.equal(f.sent.length, 0);
    } finally { f.adapter.close(); await interruption?.catch(() => {}); }
  });
}

test("connection loss settles interruption even before native abort acknowledges", async () => {
  const f = fixture({ startupPrompt: "original seed" });
  f.session.abort = () => new Promise(() => {});
  f.event("user.message", { content: "original seed" });
  const interruption = f.adapter.interrupt();
  interruption.catch(() => {});
  await tick();
  f.adapter.close();
  await assert.rejects(interruption, /cancellation outcome is unknown/);
  assert.equal(f.sent.length, 0);
});

for (const changed of ["loadout", "session binding"]) {
  test(`active ACP cancellation validates a changed ${changed} before native abort`, async () => {
    let invalid = false;
    const f = fixture({ validateSendContext: async () => {
      if (invalid && changed === "loadout") throw new Error("selected root changed");
    } });
    let aborts = 0;
    f.session.abort = async () => { aborts++; };
    const prompt = f.prompt();
    try {
      await tick();
      f.event("user.message", { source: f.sent[0].source });
      invalid = true;
      if (changed === "session binding") f.session.sessionId = "another-session";
      await f.adapter.handle({
        jsonrpc: "2.0", method: "session/cancel", params: { sessionId: "native-fixture" },
      });
      assert.equal(aborts, 0, "active cancellation must not mutate an invalidated context");
      assert.match((await prompt).error.message, /execution context is unavailable/);
      assert.equal(f.adapter.authWait().controlled, false);
      assert.equal(f.sent.length, 1);
    } finally { f.adapter.close(); await prompt; }
  });
}

for (const outcome of ["loadout", "human", "auth", "closed", "binding", "abort-unknown"]) {
  test(`headed interruption preserves the ${outcome} fence without replay or late input`, async () => {
    let release;
    const validation = new Promise((resolve) => { release = resolve; });
    const f = fixture({
      startupPrompt: "original seed",
      validateSendContext: async () => {
        await validation;
        if (outcome === "loadout") throw new Error("another selected root");
      },
    });
    let aborts = 0;
    f.session.abort = async () => { aborts++; throw new Error("unconfirmed abort"); };
    try {
      f.event("user.message", { content: "original seed" });
      const interruption = f.adapter.interrupt();
      assert.match((await f.prompt(2)).error.message, /startup.*not ready/i);
      if (outcome === "human") f.event("user.message", { content: "operator took control" });
      if (outcome === "auth") f.adapter.authWait();
      if (outcome === "closed") f.adapter.close();
      if (outcome === "binding") f.session.sessionId = "another-session";
      release();
      await assert.rejects(interruption, outcome === "abort-unknown" ? /cancellation outcome is unknown/
        : /execution context is unavailable/);
      await assert.rejects(f.adapter.interrupt(), /unavailable/);
      assert.equal(aborts, outcome === "abort-unknown" ? 1 : 0);
      assert.equal(f.sent.length, 0);
    } finally { f.adapter.close(); }
  });
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

test("a complete native assistant message reaches ACP when no deltas were observed", async () => {
  const f = fixture();
  try {
    const operation = f.prompt();
    f.event("user.message", { source: f.sent[0].source });
    f.event("assistant.message", { messageId: "native-answer", content: "complete native answer" });
    f.event("session.idle");
    assert.equal((await operation).result.stopReason, "end_turn");
    assert.deepEqual(f.emitted.map((message) => message.params.update.content.text), [
      "complete native answer",
    ]);
  } finally { f.adapter.close(); }
});

test("streamed and complete native messages do not duplicate or hide another message", async () => {
  const f = fixture();
  try {
    const operation = f.prompt();
    f.event("user.message", { source: f.sent[0].source });
    f.event("assistant.message_delta", { messageId: "streamed", deltaContent: "first " });
    f.event("assistant.message", { messageId: "streamed", content: "first answer" });
    f.event("assistant.message_delta", { messageId: "streamed", deltaContent: "answer" });
    f.event("assistant.message", { messageId: "complete", content: " plus complete" });
    f.event("assistant.message", { messageId: "complete", content: " plus complete" });
    f.event("session.idle");
    await operation;
    assert.equal(f.emitted.map((message) => message.params.update.content.text).join(""),
      "first answer plus complete");
  } finally { f.adapter.close(); }
});

test("empty native deltas cannot suppress the complete-message fallback", async () => {
  const f = fixture();
  try {
    const operation = f.prompt();
    f.event("user.message", { source: f.sent[0].source });
    f.event("assistant.message_delta", { messageId: "native-answer", deltaContent: "" });
    f.event("assistant.message", { messageId: "native-answer", content: "actual answer" });
    f.event("session.idle");
    await operation;
    assert.deepEqual(f.emitted.map((message) => message.params.update.content.text), ["actual answer"]);
  } finally { f.adapter.close(); }
});

test("legacy unkeyed deltas retain streaming without a duplicate complete message", async () => {
  const f = fixture();
  try {
    const operation = f.prompt();
    f.event("user.message", { source: f.sent[0].source });
    f.event("assistant.message_delta", { deltaContent: "legacy answer" });
    f.event("assistant.message", { messageId: "native-answer", content: "legacy answer" });
    f.event("session.idle");
    await operation;
    assert.deepEqual(f.emitted.map((message) => message.params.update.content.text), ["legacy answer"]);
  } finally { f.adapter.close(); }
});

test("a full-message fallback is not repeated by later deltas for that native message", async () => {
  const f = fixture();
  try {
    const operation = f.prompt();
    f.event("user.message", { source: f.sent[0].source });
    f.event("assistant.message", { messageId: "native-answer", content: "actual answer" });
    f.event("assistant.message_delta", { messageId: "native-answer", deltaContent: "actual answer" });
    f.event("session.idle");
    await operation;
    assert.deepEqual(f.emitted.map((message) => message.params.update.content.text), ["actual answer"]);
  } finally { f.adapter.close(); }
});

test("an unidentifiable full message cannot duplicate an already-forwarded native stream", async () => {
  const f = fixture();
  try {
    const operation = f.prompt();
    f.event("user.message", { source: f.sent[0].source });
    f.event("assistant.message_delta", { messageId: "native-answer", deltaContent: "actual answer" });
    f.event("assistant.message", { content: "actual answer" });
    f.event("session.idle");
    await operation;
    assert.deepEqual(f.emitted.map((message) => message.params.update.content.text), ["actual answer"]);
  } finally { f.adapter.close(); }
});

for (const marker of ["event-agent", "data-agent", "parent-tool"]) {
  test(`child messages and idle cannot render or finish the root prompt (${marker})`, async () => {
    const f = fixture();
    const extra = marker === "event-agent" ? { agentId: "child" } : {};
    const data = marker === "data-agent" ? { agentId: "child" }
      : marker === "parent-tool" ? { parentToolCallId: "child-tool" } : {};
    try {
      const operation = f.prompt();
      let completed = false;
      operation.then(() => { completed = true; });
      f.event("user.message", { source: f.sent[0].source });
      f.event("assistant.message_delta", { ...data, deltaContent: "child stream" }, extra);
      f.event("assistant.message", {
        ...data, messageId: "shared-message", content: "child completion",
      }, extra);
      f.event("session.idle", data, extra);
      await tick();
      assert.equal(completed, false);
      f.event("assistant.message", { messageId: "shared-message", content: "root completion" });
      f.event("session.idle");
      assert.equal((await operation).result.stopReason, "end_turn");
      assert.deepEqual(f.emitted.map((message) => message.params.update.content.text), [
        "root completion",
      ]);
    } finally { f.adapter.close(); }
  });
}

test("child idle does not announce completion of the original root launch seed", async () => {
  const f = fixture({ startupPrompt: "original seed" });
  const status = () => f.adapter.handle({
    jsonrpc: "2.0", id: "status", method: "copilot-driver/input-status",
    params: { sessionId: f.session.sessionId },
  });
  try {
    f.event("user.message", { content: "original seed" });
    for (const [data, extra] of [
      [{}, { agentId: "child" }],
      [{ agentId: "child" }, {}],
      [{ parentToolCallId: "child-tool" }, {}],
    ]) {
      f.event("session.idle", data, extra);
      assert.equal((await status()).result.prompt, "pending");
    }
    f.event("session.idle");
    assert.equal((await status()).result.prompt, "ready");
  } finally { f.adapter.close(); }
});

test("standard ACP extension wire names preserve readiness and native-send semantics", async () => {
  const f = fixture();
  f.session.send = async (options) => { f.sent.push(options); return "native-message-id"; };
  try {
    for (const prefix of ["", "_"]) {
      const status = await f.adapter.handle({
        jsonrpc: "2.0", id: "status", method: prefix + "copilot-driver/input-status",
        params: { sessionId: f.session.sessionId },
      });
      assert.equal(status.result?.prompt, "ready");
      const sent = await f.adapter.handle({
        jsonrpc: "2.0", id: "send", method: prefix + "copilot-driver/native-send",
        params: { sessionId: f.session.sessionId, prompt: "literal input", mode: "enqueue" },
      });
      assert.equal(sent.result?.messageId, "native-message-id");
    }
    assert.equal((await f.adapter.handle({
      jsonrpc: "2.0", id: "unknown", method: "_copilot-driver/unknown", params: {},
    })).error.code, -32601);
    assert.equal(f.sent.length, 2);
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

test("startup observation gates dispatch without revoking control or exempting later human input", async () => {
  for (const method of ["session/prompt", "copilot-driver/native-send"]) {
    const f = fixture({ startupPrompt: "original seed" });
    f.session.send = async (options) => {
      f.sent.push(options);
      f.event("user.message", { source: options.source, content: options.prompt });
      return "actual-native-id";
    };
    try {
      const request = () => f.adapter.handle({
        jsonrpc: "2.0", id: 1, method,
        params: {
          sessionId: f.session.sessionId,
          ...(method === "session/prompt"
            ? { prompt: [{ type: "text", text: "remote work" }] }
            : { prompt: "remote work", mode: "enqueue" }),
        },
      });
      const early = await Promise.race([request(), tick().then(() => null)]);
      assert.ok(early?.error, "Startup must refuse before dispatch, not leave a native prompt pending");
      assert.match(early.error.message, /startup.*not ready/i);
      assert.equal(f.sent.length, 0);
      f.event("session.idle");
      assert.match((await request()).error.message, /startup.*not ready/i);
      assert.equal(f.sent.length, 0);
      f.event("user.message", { source: "user", content: "original seed" }, { id: "original" });
      f.event("session.idle");
      const admitted = request();
      if (method === "copilot-driver/native-send") assert.ok((await admitted).result);
      f.event("user.message", { source: "user", content: "original seed" });
      if (method === "session/prompt") assert.match((await admitted).error.message, /Native input took control/);
      assert.match((await f.prompt(2)).error.message, /unavailable/);
      assert.equal(f.sent.length, 1);
    } finally { f.adapter.close(); }
  }
});

test("seed acceptance permits native enqueue but not an ACP turn mixed with the original turn", async () => {
  const f = fixture({ startupPrompt: "original seed" });
  const status = () => f.adapter.handle({
    jsonrpc: "2.0", id: "status", method: "copilot-driver/input-status",
    params: { sessionId: f.session.sessionId },
  });
  f.session.send = async (options) => {
    f.sent.push(options);
    f.event("user.message", { source: options.source, content: options.prompt });
    return "native-message-id";
  };
  try {
    const initialized = await f.adapter.handle({
      jsonrpc: "2.0", id: "init", method: "initialize", params: { protocolVersion: 1 },
    });
    assert.equal(initialized.result._meta.inputReadiness.version, 1);
    assert.equal((await status()).result.startupSeed, "pending");
    f.event("user.message", { content: "original seed" }, { id: "startup" });
    assert.deepEqual((await status()).result, {
      sessionId: f.session.sessionId, startupSeed: "observed", startupTurn: "active",
      nativeSend: "ready", prompt: "pending",
    });
    assert.match((await f.prompt()).error.message, /startup.*not ready/i);
    assert.equal(f.sent.length, 0);
    assert.equal((await f.adapter.handle({
      jsonrpc: "2.0", id: "enqueue", method: "copilot-driver/native-send",
      params: { sessionId: f.session.sessionId, prompt: "queued work", mode: "enqueue" },
    })).result.messageId, "native-message-id");
    assert.equal((await status()).result.startupTurn, "active");
    f.event("session.idle");
    assert.equal((await status()).result.startupTurn, "idle-observed");
    const prompt = f.prompt();
    f.event("session.idle");
    assert.equal((await prompt).result.stopReason, "end_turn");
    assert.equal(f.sent.length, 2);
  } finally { f.adapter.close(); }
});

test("no seed is not a restoration or idle claim, and status cannot thaw an auth freeze", async () => {
  const f = fixture();
  const status = (sessionId = f.session.sessionId) => f.adapter.handle({
    jsonrpc: "2.0", id: "status", method: "copilot-driver/input-status", params: { sessionId },
  });
  try {
    assert.deepEqual((await status()).result, {
      sessionId: f.session.sessionId, startupSeed: "not-requested", startupTurn: "not-requested",
      nativeSend: "ready", prompt: "ready",
    });
    assert.ok((await status("different-native")).error);
    f.adapter.authWait();
    assert.equal((await status()).result.nativeSend, "unavailable");
    assert.equal((await status()).result.prompt, "unavailable");
    assert.match((await f.prompt()).error.message, /unavailable/);
    assert.equal(f.sent.length, 0);
  } finally { f.adapter.close(); }
  assert.equal((await status()).result.nativeSend, "unavailable");
});
