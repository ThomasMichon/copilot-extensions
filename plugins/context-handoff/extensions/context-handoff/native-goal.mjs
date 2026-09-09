// Native objective data is opaque on disk. Only the published getState
// projection is used for lifecycle decisions and budget accounting.
function creditCap(credits) {
  if (!Number.isFinite(credits) || credits <= 0) {
    throw new Error("Native objective has an invalid finite AI-credit cap.");
  }
  const [mantissa, exponent = "0"] = String(credits).toLowerCase().split("e");
  const [whole, fraction = ""] = mantissa.split(".");
  return { digits: BigInt(whole + fraction), decimals: fraction.length - Number(exponent) };
}

export function remainingNativeCredits(state) {
  const limit = state?.creditLimit;
  if (limit?.credits === undefined) return null;
  const cap = creditCap(limit.credits);
  if (!/^\d+$/.test(limit.creditsUsedNanoAiu)) {
    throw new Error("Native objective exact consumption is unavailable.");
  }
  const decimals = Math.max(9, cap.decimals);
  const unit = 10n ** BigInt(decimals);
  const remaining = cap.digits * 10n ** BigInt(decimals - cap.decimals)
    - BigInt(limit.creditsUsedNanoAiu) * 10n ** BigInt(decimals - 9);
  if (remaining <= 0n) return "0";
  const fraction = String(remaining % unit).padStart(decimals, "0").replace(/0+$/, "");
  return `${remaining / unit}${fraction ? `.${fraction}` : ""}`;
}

export function freezeNativeGoal({ sourceSessionId, successorSessionId, cwd, intent, state, content }) {
  if (!state) {
    if (content !== null) throw new Error("Native objective state and persistence disagree.");
    return {
      version: 1, sourceSessionId, successorSessionId, cwd, phase: "frozen",
      state: null, content: null, intent: "stopped", remainingCredits: null,
    };
  }
  if (typeof content !== "string" || !content) {
    throw new Error("Native objective persistence is unavailable; source is preserved.");
  }
  const remainingCredits = remainingNativeCredits(state);
  const terminal = state.status === "completed";
  const exhausted = remainingCredits === "0"
    || ["max_ai_credits", "legacy_cap_reached"].includes(state.pauseReason);
  const sourcePausedForHandoff = state.status === "active"
    || (state.status === "paused" && state.pauseReason === "autopilot_off");
  return {
    version: 1,
    sourceSessionId,
    successorSessionId,
    cwd,
    phase: "frozen",
    intent: intent === "running" && !terminal && !exhausted && sourcePausedForHandoff ? "running" : "stopped",
    remainingCredits,
    state,
    content,
  };
}

export function assertNativeRestored(goal, observed, sessionId) {
  if (goal.successorSessionId !== sessionId) {
    throw new Error("Native handoff belongs to a different successor; source is preserved.");
  }
  if (goal.state === null) {
    if (observed) throw new Error("Successor already has a different native objective.");
    return;
  }
  const expected = goal.state;
  if (!observed || observed.objective !== expected.objective) {
    throw new Error("Successor native objective does not match the frozen handoff.");
  }
  if (goal.successorObjectiveId !== undefined && observed.id !== goal.successorObjectiveId) {
    throw new Error("Successor native objective identity changed after hydration; source is preserved.");
  }
  if (observed.creditCountNanoAiu !== expected.creditCountNanoAiu
    || observed.turnCount !== expected.turnCount
    || observed.creditLimit?.credits !== expected.creditLimit?.credits
    || observed.creditLimit?.creditsUsedNanoAiu !== expected.creditLimit?.creditsUsedNanoAiu) {
    throw new Error("Successor native objective usage changed before handoff admission.");
  }
  const resumedActive = expected.status === "active"
    && observed.status === "paused" && observed.pauseReason === "resumed_session";
  if (!resumedActive && (observed.status !== expected.status
    || observed.pauseReason !== expected.pauseReason)) {
    throw new Error("Successor native objective lifecycle does not match the frozen handoff.");
  }
}

export function nativeConsumeError(metadata, sessionId) {
  const goal = metadata?.nativeGoal;
  if (!goal || goal.phase === "none") return null;
  if (goal.version !== 1) return "This native handoff version is unsupported; predecessor preserved.";
  if (goal.successorSessionId !== sessionId) {
    return "Native handoff belongs to a different successor; predecessor preserved.";
  }
  if (!goal.hydratedBySession || goal.hydratedBySession !== sessionId) {
    return "Native objective restoration is pending. Resume the prepared successor before consuming; predecessor preserved.";
  }
  return null;
}

export async function prepareNativeGoal({ goal, session, readState, save }) {
  if (goal.successorSessionId !== session.sessionId) {
    throw new Error("Native preparation session identity does not match the handoff.");
  }
  if (goal.phase === "none") return;
  if (goal.phase !== "frozen") {
    throw new Error("Native handoff is not awaiting preparation; do not overwrite its objective.");
  }
  const events = await session.getEvents();
  if (events.some(event => ["user.message", "assistant.turn_start", "tool.execution_start"].includes(event.type))) {
    throw new Error("Native preparation requires an unused successor session.");
  }
  if ((await readState()).state) {
    throw new Error("Native preparation cannot overwrite an existing objective.");
  }
  if (goal.state !== null) {
    await session.rpc.workspaces.writeAutopilotObjective({ content: goal.content });
  }
  // The active registry has intentionally not been changed. A native cold
  // resume, not this file write, will hydrate and acknowledge the objective.
  await save({ ...goal, phase: "prepared" });
  const result = await session.rpc.commands.enqueue({ command: "/exit" });
  if (!result.queued) throw new Error("Native preparation exit was not accepted; source is preserved.");
}

export async function acknowledgeNativeGoal({ goal, session, readState, save }) {
  const observed = (await readState()).state;
  assertNativeRestored(goal, observed, session.sessionId);
  const hydrated = {
    ...goal,
    phase: goal.phase === "none" ? "none" : "hydrated",
    hydratedBySession: session.sessionId,
    successorObjectiveId: observed?.id ?? null,
  };
  await save(hydrated);
  return hydrated;
}

async function assertNoSubmissionSince(session, watermark) {
  const events = await session.getEvents();
  const index = watermark === null ? -1 : events.findIndex(event => event.id === watermark);
  if (watermark === undefined || (watermark !== null && index < 0)) {
    throw new Error("Native activation event watermark is unavailable; submission cannot be reconciled.");
  }
  if (events.slice(index + 1).some(event =>
    event.type === "user.message" || event.type === "assistant.turn_start")) {
    throw new Error("A message or business turn exists after activation was requested; do not repeat submission.");
  }
}

export async function activateNativeGoal({ goal, session, readState, save }) {
  if (goal.intent !== "running" || goal.phase === "none") return null;
  if (!goal.consumedBySession || goal.consumedBySession !== session.sessionId) {
    throw new Error("Native continuation requires successful handoff consumption.");
  }
  if (goal.activation || goal.activationRequested) {
    const observed = (await readState()).state;
    const objectiveId = goal.activation?.objectiveId ?? goal.successorObjectiveId;
    const expected = goal.activation?.state ?? goal.state;
    if (!observed || observed.id !== objectiveId || observed.objective !== goal.state.objective
      || observed.creditCountNanoAiu !== expected.creditCountNanoAiu
      || observed.turnCount !== expected.turnCount
      || remainingNativeCredits(observed) !== goal.remainingCredits) {
      throw new Error("Native activation outcome is unresolved; do not replay or retire the source.");
    }
    await assertNoSubmissionSince(session, goal.activationEventWatermark);
    if (observed.status === "active") {
      // A lost command response is recoverable from the unchanged native
      // objective. No second command or business message is needed.
      const activation = goal.activation ?? {
        prompt: "Continue the active native objective.", objectiveId: observed.id, state: observed,
      };
      await save({ ...goal, activation });
      return activation;
    }
    if (observed.status !== "paused" || observed.pauseReason !== "resumed_session") {
      throw new Error("Native activation outcome is unresolved; do not replay or retire the source.");
    }
    // The receipt and event watermark identify this transport's unused
    // activation, not a user's new goal with matching text. Native unlimited
    // reactivation may replace its internal ID; it is still one logical handoff.
  } else {
    assertNativeRestored(goal, (await readState()).state, session.sessionId);
    goal = {
      ...goal, activationEventWatermark: (await session.getEvents()).at(-1)?.id ?? null,
    };
  }
  if (goal.remainingCredits === "0") {
    throw new Error("An exhausted native objective cannot be automatically resumed.");
  }
  await save({ ...goal, activationRequested: true });
  const command = goal.remainingCredits === null
    ? `-- ${goal.state.objective}`
    : `--max-ai-credits ${goal.remainingCredits}`;
  const result = await session.rpc.commands.invoke({ name: "autopilot", input: command });
  if (result.kind !== "agent-prompt" || result.mode !== "autopilot") {
    throw new Error("Native goal activation requires host confirmation or returned no continuation; source is preserved.");
  }
  const observed = (await readState()).state;
  if (!observed || observed.status !== "active"
    || observed.objective !== goal.state.objective
    || remainingNativeCredits(observed) !== goal.remainingCredits) {
    throw new Error("Native activation did not preserve the remaining budget; source is preserved.");
  }
  await assertNoSubmissionSince(session, goal.activationEventWatermark);
  const replacedObjectiveIds = goal.activation?.replacedObjectiveIds || [];
  const activation = {
    prompt: result.prompt, objectiveId: observed.id, state: observed,
    replacedObjectiveIds: goal.activation && goal.activation.objectiveId !== observed.id
      ? [...replacedObjectiveIds, goal.activation.objectiveId] : replacedObjectiveIds,
  };
  await save({ ...goal, activationRequested: true, activation });
  return activation;
}
