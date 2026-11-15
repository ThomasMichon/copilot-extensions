import test from "node:test";
import assert from "node:assert/strict";
import { PendingQuestions, pollQuestions } from "../../../libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/questions.mjs";

function fixture(family = "elicitation", extra = {}) {
  const questions = new PendingQuestions();
  questions.observe({
    type: "tool.execution_start", data: { toolCallId: "tool-1", toolName: "ask_user" },
  }, "native-1");
  questions.observe({
    type: `${family}.requested`,
    data: { requestId: "question-1", toolCallId: "tool-1", message: "Choose",
      question: "Choose", ...extra },
  }, "native-1");
  const calls = [];
  const session = {
    rpc: { ui: {
      handlePendingElicitation: async (body) => { calls.push(body); return { success: true }; },
      handlePendingUserInput: async (body) => { calls.push(body); return { success: true }; },
    } },
    log: async () => {},
  };
  const control = {
    id: 1, sender: "delegated-controller",
    body: {
      session_id: "native-1", generation: questions.generation, request_id: "question-1",
      family, action: "accept", content: family === "user_input" ? { answer: "blue" } : { color: "blue" },
      expires_at: Date.now() / 1000 + 30,
    },
  };
  return { questions, session, control, calls };
}

for (const family of ["elicitation", "user_input"]) {
  test(`answers ${family} through its exact ordinary pending RPC once`, async () => {
    const f = fixture(family);
    const outcomes = await Promise.all([
      f.questions.apply(f.control, f.session), f.questions.apply(f.control, f.session),
    ]);
    assert.deepEqual(outcomes.map((item) => item.state), ["accepted", "rejected"]);
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0].requestId, "question-1");
    if (family === "elicitation") {
      assert.deepEqual(f.calls[0].result, { action: "accept", content: { color: "blue" } });
    } else {
      assert.deepEqual(f.calls[0].response, { answer: "blue", wasFreeform: true });
    }
    assert.equal(f.questions.snapshot(f.session, {}).requests.length, 0);
  });
}

test("native terminal wins without turning a false SDK result into success", async () => {
  const f = fixture();
  f.session.rpc.ui.handlePendingElicitation = async () => ({ success: false });
  assert.equal((await f.questions.apply(f.control, f.session)).state, "rejected");
});

test("a timeline-log failure does not hide an already accepted native answer", async () => {
  const f = fixture();
  f.session.log = () => { throw new Error("timeline unavailable"); };
  assert.equal((await f.questions.apply(f.control, f.session)).state, "accepted");
  assert.equal(f.calls.length, 1);
});

test("completed, superseded and redelivered questions do not reopen", async () => {
  const f = fixture();
  f.questions.observe({ type: "elicitation.completed", data: { requestId: "question-1" } }, "native-1");
  f.questions.observe({
    type: "elicitation.requested",
    data: { requestId: "question-1", toolCallId: "tool-1", message: "Choose" },
  }, "native-1");
  assert.equal((await f.questions.apply(f.control, f.session)).state, "rejected");
  f.questions.observe({
    type: "elicitation.requested",
    data: { requestId: "question-2", toolCallId: "tool-1", message: "Choose" },
  }, "native-1");
  assert.deepEqual([...f.questions.pending.keys()], ["question-2"]);
  assert.equal((await f.questions.apply(f.control, f.session)).state, "rejected");
  assert.equal(f.calls.length, 0);
});

test("session, generation, request, deadline and current-owner mismatches refuse before RPC", async () => {
  for (const mismatch of [
    { session_id: "native-2" }, { generation: "old-generation" },
    { request_id: "question-2" }, { expires_at: 0 }, { family: "permission" },
  ]) {
    const f = fixture();
    Object.assign(f.control.body, mismatch);
    assert.equal((await f.questions.apply(f.control, f.session)).state, "rejected");
    assert.equal(f.calls.length, 0);
  }
  const f = fixture();
  assert.equal((await f.questions.apply(f.control, f.session, { isCurrent: () => false })).state, "rejected");
  f.questions.observe({ type: "session.start", data: {} }, "native-2");
  assert.equal((await f.questions.apply(f.control, f.session)).state, "rejected");
  assert.equal(f.calls.length, 0);
});

test("permission, sourced MCP, URL and nested questions are never answerable", async () => {
  for (const extra of [{ elicitationSource: "example-server" }, { mode: "url" }]) {
    const f = fixture("elicitation", extra);
    assert.equal(f.questions.snapshot(f.session, {}).requests[0].answerable, false);
    assert.equal((await f.questions.apply(f.control, f.session)).state, "rejected");
    assert.equal(f.calls.length, 0);
  }
  const f = fixture();
  f.questions.reset("native-1");
  for (const type of ["permission.requested", "exit_plan_mode.requested", "auto_mode_switch.requested"]) {
    f.questions.observe({ type, data: { requestId: "question-1" } }, "native-1");
  }
  f.questions.observe({
    type: "elicitation.requested", agentId: "child",
    data: { requestId: "question-1", toolCallId: "tool-1" },
  }, "native-1");
  assert.equal(f.questions.pending.size, 0);
});

test("unsupported SDK and malformed user input refuse; unknown RPC effects never retry", async () => {
  const f = fixture("user_input", { choices: ["red"], allowFreeform: false });
  assert.equal((await f.questions.apply(f.control, f.session)).state, "rejected");
  delete f.session.rpc.ui.handlePendingUserInput;
  assert.equal(f.questions.snapshot(f.session, {}).requests[0].answerable, false);
  const other = fixture();
  other.session.rpc.ui.handlePendingElicitation = async () => { throw new Error("transport lost"); };
  assert.equal((await other.questions.apply(other.control, other.session)).state, "unknown");
  assert.equal((await other.questions.apply(other.control, other.session)).state, "rejected");
});

test("question poll gates the running provider and retries acknowledgments, not answers", async () => {
  const f = fixture();
  let permitted = false;
  let failAck = true;
  let polls = 0;
  let time = Date.now();
  const requests = [];
  const request = async (method, path, body) => {
    requests.push({ method, path, body });
    if (path === "/health") return { capabilities: permitted ? { live_question_answers: 1 } : {} };
    if (path.endsWith("/poll")) {
      polls++;
      return { controls: polls === 1 ? [f.control] : [] };
    }
    if (failAck) { failAck = false; throw new Error("ACK transport lost"); }
    return { acked: 1 };
  };
  const options = { ...f, identity: { pid: 123, process_started_at: 1 }, request, now: () => time };
  await pollQuestions("native-1", options);
  assert.equal(polls, 0);
  permitted = true;
  time += 30_000;
  await assert.rejects(pollQuestions("native-1", options), /ACK transport lost/);
  assert.equal(f.calls.length, 1);
  await pollQuestions("native-1", options);
  assert.equal(f.calls.length, 1);
  assert.equal(f.questions.acks.size, 0);
  assert.equal(requests.filter((r) => r.path.endsWith("/ack")).length, 2);
});

test("rollover retries a known native outcome under its original audit scope", async () => {
  const f = fixture();
  const originalAck = { generation: f.questions.generation, request_id: "question-1", state: "accepted" };
  f.questions.acks.set(1, { sid: "native-1", ack: originalAck });
  f.questions.reset("native-2");
  const calls = [];
  await pollQuestions("native-2", {
    ...f, identity: { pid: 123, process_started_at: 1 },
    request: async (method, path, body) => {
      calls.push({ method, path, body });
      return path === "/health" ? { capabilities: { live_question_answers: 1 } } : { controls: [] };
    },
  });
  assert.equal(f.questions.acks.size, 0);
  assert.deepEqual(calls.find((item) => item.path.endsWith("/ack")), {
    method: "POST", path: "/api/v1/live-sessions/native-1/questions/answers/1/ack",
    body: originalAck,
  });
  assert.equal(f.calls.length, 0);
});

test("missing original audit evidence is visible and cannot block a new snapshot or replay an answer", async () => {
  for (const status of [404, 409]) {
    const f = fixture();
    f.questions.acks.set(1, { sid: "native-old", ack: {
      generation: "old-extension", request_id: "old-request", state: "accepted",
    } });
    const calls = [], logs = [];
    await pollQuestions("native-1", {
      ...f, identity: { pid: 123, process_started_at: 1 }, log: (value) => logs.push(value),
      request: async (_method, path) => {
        calls.push(path);
        if (path === "/health") return { capabilities: { live_question_answers: 1 } };
        if (path.endsWith("/ack")) throw Object.assign(new Error("record changed"), { status });
        return { controls: [] };
      },
    });
    assert.equal(f.questions.acks.size, 0);
    assert.ok(calls.some((path) => path.endsWith("/native-1/questions/poll")));
    assert.match(logs[0], /original audit record unavailable/);
    assert.equal(f.calls.length, 0);
  }
});
