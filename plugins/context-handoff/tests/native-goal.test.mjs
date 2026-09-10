import assert from "node:assert/strict";
import { test } from "node:test";
import {
  remainingNativeCredits, freezeNativeGoal, assertNativeRestored,
  nativeConsumeError, prepareNativeGoal, acknowledgeNativeGoal, activateNativeGoal,
} from "../extensions/context-handoff/native-goal.mjs";

const state = {
  id: 1, objective: 'A goal\n"quoted" --max-ai-credits 42', status: "paused",
  pauseReason: "autopilot_off", turnCount: 3, creditCountNanoAiu: "125000001",
  creditLimit: { credits: 0.375, creditsUsed: 0.125000001, creditsUsedNanoAiu: "125000001" },
};
const freeze = (overrides = {}) => freezeNativeGoal({
  sourceSessionId: "source", successorSessionId: "target", intent: "running",
  state, content: "opaque-native-content", ...overrides,
});

test("confirmed absence uses the same receiver without creating or activating a goal", async () => {
  let goal = freeze({ state: null, content: null });
  const session = {
    sessionId: "target", getEvents: async () => [],
    rpc: {
      workspaces: { writeAutopilotObjective: async () => assert.fail("No objective may be manufactured") },
      commands: {
        enqueue: async () => ({ queued: true }),
        invoke: async () => assert.fail("No automatic goal activation"),
      },
    },
  };
  const save = async value => { goal = value; };
  const readState = async () => ({ state: null });
  await prepareNativeGoal({ goal, session, readState, save });
  assert.equal(goal.phase, "prepared");
  await acknowledgeNativeGoal({ goal, session, readState, save });
  assert.equal(goal.hydratedBySession, "target");
  assert.equal(goal.successorObjectiveId, null);
  assert.equal(await activateNativeGoal({ goal, session, readState, save }), null);
  assert.throws(() => assertNativeRestored(goal, state, "target"), /different native objective/);
});

test("remaining budget uses exact nano-AIU and never rounds upward", () => {
  assert.equal(remainingNativeCredits(state), "0.249999999");
  assert.equal(remainingNativeCredits({ creditLimit: { credits: 1e-9, creditsUsedNanoAiu: "0" } }), "0.000000001");
  assert.equal(remainingNativeCredits({ creditLimit: { credits: 5e-10, creditsUsedNanoAiu: "0" } }), "0.0000000005");
  assert.equal(remainingNativeCredits({ ...state, creditLimit: { ...state.creditLimit, credits: 0.01 } }), "0");
  assert.equal(remainingNativeCredits({}), null);
  assert.throws(() => remainingNativeCredits({ creditLimit: { credits: 0 } }), /invalid/);
  assert.throws(() => remainingNativeCredits({ creditLimit: { credits: 1 } }), /consumption/);
});

test("freezing preserves opaque state and independent intent, including exhausted and completed", () => {
  assert.equal(freeze().intent, "running");
  assert.equal(freeze({ intent: "stopped" }).intent, "stopped");
  assert.equal(freeze({ state: { ...state, status: "completed" } }).intent, "stopped");
  assert.equal(freeze({ state: { ...state, creditLimit: { ...state.creditLimit, credits: 0.01 } } }).intent, "stopped");
  assert.equal(freeze().content, "opaque-native-content");
  const absent = freeze({ state: null, content: null });
  assert.equal(absent.phase, "frozen");
  assert.equal(absent.state, null);
  assert.equal(absent.intent, "stopped");
  assert.throws(() => freeze({ content: null }), /persistence/);
});

test("restoration checks native identity, exact usage, lifecycle and successor ownership", () => {
  assertNativeRestored(freeze(), state, "target");
  const pending = freeze({ state: { ...state, status: "active", pauseReason: undefined } });
  assertNativeRestored(pending, { ...state, pauseReason: "resumed_session" }, "target");
  assert.throws(() => assertNativeRestored(freeze(), state, "other"), /different successor/);
  assert.throws(() => assertNativeRestored(freeze(), null, "target"), /does not match/);
  assert.throws(() => assertNativeRestored(freeze(), { ...state, turnCount: 4 }, "target"), /usage/);
  assert.throws(() => assertNativeRestored(freeze(), { ...state, status: "active" }, "target"), /lifecycle/);
  assert.throws(() => assertNativeRestored(
    { ...freeze(), successorObjectiveId: state.id }, { ...state, id: state.id + 1 }, "target",
  ), /identity changed/);
});

test("all SDK-free consumers must wait for native hydration in the assigned session", () => {
  assert.equal(nativeConsumeError({}, "target"), null);
  assert.match(nativeConsumeError({ nativeGoal: freeze() }, "target"), /restoration is pending/);
  assert.match(nativeConsumeError({ nativeGoal: freeze() }, "other"), /different successor/);
  assert.equal(nativeConsumeError({ nativeGoal: { ...freeze(), hydratedBySession: "target" } }, "target"), null);
});

test("preparation writes only opaque state, records receipt, then requests native exit", async () => {
  const calls = [];
  const session = {
    sessionId: "target", getEvents: async () => [],
    rpc: {
      workspaces: { writeAutopilotObjective: async value => calls.push(["write", value]) },
      commands: { enqueue: async value => { calls.push(["exit", value]); return { queued: true }; } },
    },
  };
  await prepareNativeGoal({
    goal: freeze(), session, readState: async () => ({ state: null }),
    save: async goal => calls.push(["save", goal.phase]),
  });
  assert.deepEqual(calls, [
    ["write", { content: "opaque-native-content" }], ["save", "prepared"], ["exit", { command: "/exit" }],
  ]);
  await assert.rejects(prepareNativeGoal({
    goal: freeze(), session: { ...session, getEvents: async () => [{ type: "user.message" }] },
    readState: async () => ({ state: null }), save: async () => {},
  }), /unused successor/);
});

test("hydration receipt depends on native provider, not the workspace file", async () => {
  let saved;
  await acknowledgeNativeGoal({
    goal: freeze(), session: { sessionId: "target" }, readState: async () => ({ state }),
    save: async goal => { saved = goal; },
  });
  assert.equal(saved.hydratedBySession, "target");
  await assert.rejects(acknowledgeNativeGoal({
    goal: freeze(), session: { sessionId: "target" }, readState: async () => ({ state: null }),
    save: async () => assert.fail("invalid restoration must not be acknowledged"),
  }), /does not match/);
});

test("activation uses remaining cap once and returns, but never sends, native continuation", async () => {
  let observed = state;
  const commands = [];
  const goal = { ...freeze(), consumedBySession: "target" };
  const saved = [];
  const activation = await activateNativeGoal({
    goal,
    session: {
      sessionId: "target",
      getEvents: async () => [],
      rpc: { commands: { invoke: async command => {
        commands.push(command);
        observed = {
          ...state, status: "active", pauseReason: undefined,
          creditLimit: { credits: 0.249999999, creditsUsed: 0, creditsUsedNanoAiu: "0" },
        };
        return { kind: "agent-prompt", mode: "autopilot", prompt: "Native continuation" };
      } } },
    },
    readState: async () => ({ state: observed }),
    save: async record => saved.push(record),
  });
  assert.deepEqual(commands, [{ name: "autopilot", input: "--max-ai-credits 0.249999999" }]);
  assert.equal(activation.prompt, "Native continuation");
  assert.equal(saved[0].activationRequested, true);
  assert.deepEqual(saved[1].activation, activation);
  await assert.rejects(activateNativeGoal({
    goal: { ...goal, activationRequested: true }, session: { sessionId: "target" },
    readState: async () => ({ state }),
  }), /unresolved/);
});

test("lost activation receipt reconciles native state without invoking twice", async () => {
  const observed = {
    ...state, status: "active",
    creditLimit: { credits: 0.249999999, creditsUsedNanoAiu: "0" },
  };
  let saved;
  const activation = await activateNativeGoal({
    goal: {
      ...freeze(), consumedBySession: "target", successorObjectiveId: state.id,
      activationRequested: true, activationEventWatermark: null,
    },
    session: { sessionId: "target", getEvents: async () => [] },
    readState: async () => ({ state: observed }),
    save: async value => { saved = value; },
  });
  assert.equal(activation.objectiveId, observed.id);
  assert.equal(saved.activation, activation);
});

test("cold-resumed activation re-arms only the same unused objective", async () => {
  let observed = {
    ...state, pauseReason: "resumed_session",
    creditLimit: { credits: 0.249999999, creditsUsedNanoAiu: "0" },
  };
  const goal = {
    ...freeze(), consumedBySession: "target", successorObjectiveId: state.id,
    activationRequested: true, activationEventWatermark: null,
    activation: { objectiveId: state.id, state: { ...observed }, prompt: "Continue" },
  };
  const commands = [];
  const session = {
    sessionId: "target",
    getEvents: async () => [],
    rpc: { commands: { invoke: async command => {
      commands.push(command);
      observed = { ...observed, status: "active", pauseReason: undefined };
      return { kind: "agent-prompt", mode: "autopilot", prompt: "Continue" };
    } } },
  };
  await activateNativeGoal({
    goal, session, readState: async () => ({ state: observed }), save: async () => {},
  });
  assert.deepEqual(commands, [{ name: "autopilot", input: "--max-ai-credits 0.249999999" }]);
  observed = { ...observed, creditCountNanoAiu: "125000002" };
  await assert.rejects(activateNativeGoal({
    goal, session, readState: async () => ({ state: observed }),
  }), /unresolved/);
  assert.equal(commands.length, 1);
});

test("unlimited activation resumes inherited objective without parsing its text as options", async () => {
  let observed = { ...state, creditLimit: undefined };
  const goal = {
    ...freeze({ state: observed }), consumedBySession: "target",
  };
  await activateNativeGoal({
    goal,
    session: { sessionId: "target", getEvents: async () => [], rpc: { commands: { invoke: async command => {
      assert.deepEqual(command, { name: "autopilot", input: `-- ${state.objective}` });
      observed = { ...observed, status: "active" };
      return { kind: "agent-prompt", mode: "autopilot", prompt: "Continue" };
    } } } },
    readState: async () => ({ state: observed }), save: async () => {},
  });
});

test("unlimited cold recovery replaces only the token's unused activation, using its own counters", async () => {
  const activated = {
    ...state, id: 42, status: "active", pauseReason: undefined,
    creditLimit: undefined, creditCountNanoAiu: "0", turnCount: 0,
  };
  let observed = { ...activated, status: "paused", pauseReason: "resumed_session" };
  const goal = {
    ...freeze({ state: { ...state, creditLimit: undefined } }),
    consumedBySession: "target", successorObjectiveId: state.id,
    activationEventWatermark: "before-activation",
    activation: { objectiveId: activated.id, state: activated, prompt: "Continue" },
  };
  const events = [{ id: "before-activation", type: "session.start" }];
  const commands = [];
  const saved = [];
  const session = {
    sessionId: "target", getEvents: async () => events,
    rpc: { commands: { invoke: async command => {
      commands.push(command);
      observed = { ...activated, id: 43 };
      return { kind: "agent-prompt", mode: "autopilot", prompt: "Continue" };
    } } },
  };
  const result = await activateNativeGoal({
    goal, session, readState: async () => ({ state: observed }),
    save: async value => saved.push(value),
  });
  assert.equal(result.objectiveId, 43);
  assert.deepEqual(result.replacedObjectiveIds, [42]);
  assert.equal(result.state.creditCountNanoAiu, "0");
  assert.equal(saved.at(-1).activationEventWatermark, "before-activation");
  assert.equal(commands.length, 1);

  // Identical text is not ownership: a user-created replacement is a conflict.
  observed = { ...activated, id: 99, status: "paused", pauseReason: "resumed_session" };
  await assert.rejects(activateNativeGoal({
    goal, session, readState: async () => ({ state: observed }),
  }), /unresolved/);
  assert.equal(commands.length, 1);

  observed = { ...activated, status: "paused", pauseReason: "resumed_session" };
  events.push({ id: "submitted", type: "user.message", data: { content: "Already submitted" } });
  await assert.rejects(activateNativeGoal({
    goal, session, readState: async () => ({ state: observed }),
  }), /message|submission/);
  assert.equal(commands.length, 1);
});
