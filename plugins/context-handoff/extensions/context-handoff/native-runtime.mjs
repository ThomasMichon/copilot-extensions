import { readFileSync, watch } from "node:fs";
import { dirname } from "node:path";
import {
  writeJsonAtomic, runCli, consumeFileHandoff, consumeDispatchHandoffTask,
} from "./handoff-core.mjs";
import { prepareNativeGoal, acknowledgeNativeGoal, activateNativeGoal } from "./native-goal.mjs";
import { readNativeGoal } from "./native-transport.mjs";
import { retireHerdrPredecessor } from "./herdr.mjs";

export function nativeCheckpoint(path, sessionId) {
  const record = JSON.parse(readFileSync(path, "utf8"));
  if (record.kind !== "context-handoff-session-request"
    || record.nativeGoal?.successorSessionId !== sessionId) {
    throw new Error("Native handoff checkpoint does not belong to this successor.");
  }
  return record;
}

function saveNativeCheckpoint(path, sessionId, goal) {
  const record = nativeCheckpoint(path, sessionId);
  writeJsonAtomic(path, { ...record, nativeGoal: goal });
}

export async function assertNativeProfile(session, goal) {
  if (!goal.profile) return;
  const model = await session.rpc.model.getCurrent();
  const agentId = (await session.rpc.agent.getCurrent()).agent?.id || null;
  const permissionMode = (await session.rpc.permissions.getMode()).mode;
  if (["modelId", "reasoningEffort", "contextTier"].some(
    key => model[key] !== goal.profile.model[key],
  ) || agentId !== goal.profile.agentId || permissionMode !== goal.permissionMode) {
    throw new Error("Successor model, agent, or permissions differ from the source; admission stopped.");
  }
}

export function bindNativeSuccessor(record, sessionId, execute = runCli) {
  if (record.nativeGoal.launchTransport !== "mux") return;
  const bound = JSON.parse(execute("agent-worktrees", [
    "bind-session", "--session-id", sessionId,
    "--worktree-id", record.worktree, "--handoff-token", record.handoffId,
  ], { cwd: record.worktreeDir, timeout: 30000 }));
  if (!bound.bound || bound.head_session !== sessionId || bound.handoff_token !== record.handoffId) {
    throw new Error("Native successor is not the acknowledged worktree head; predecessor preserved.");
  }
}

async function waitForNativeLaunch(path, sessionId) {
  const { nativeGoal: goal } = nativeCheckpoint(path, sessionId);
  if (!goal.launchTransport || !goal.launchRequested || goal.launch) return;
  // The source owns the checkpoint until it publishes the host launch result.
  // Starting preparation sooner lets that last source write overwrite the
  // receiver's prepared/hydrated receipt. The CLI can finish startup while
  // this event-driven bootstrap waits; no model turn is involved.
  await new Promise((resolve, reject) => {
    const changed = () => {
      try {
        const current = nativeCheckpoint(path, sessionId).nativeGoal;
        if (current.launch) {
          watcher.close();
          resolve();
        } else if (!current.launchRequested) {
          watcher.close();
          reject(new Error("Source launch was not accepted; predecessor preserved."));
        }
      } catch (error) {
        watcher.close();
        reject(error);
      }
    };
    const watcher = watch(dirname(path), changed);
    watcher.on("error", error => { watcher.close(); reject(error); });
    changed();
  });
}

export async function bootstrapNativeHandoff(session, env = process.env) {
  const path = env.CONTEXT_HANDOFF_NATIVE_CHECKPOINT;
  if (!path) return null;
  const candidate = JSON.parse(readFileSync(path, "utf8"));
  // Native --resume briefly opens an unused startup session before attaching
  // the requested identity. It is not a handoff consumer.
  if (candidate.nativeGoal?.successorSessionId !== session.sessionId) return null;
  await waitForNativeLaunch(path, session.sessionId);
  const record = nativeCheckpoint(path, session.sessionId);
  const goal = record.nativeGoal;
  if (goal.admissionComplete) {
    retireNativePredecessor(path, session.sessionId);
    return { preparing: false, path };
  }
  await assertNativeProfile(session, goal);
  const adapter = {
    goal, session,
    readState: () => readNativeGoal(session.sessionId),
    save: updated => saveNativeCheckpoint(path, session.sessionId, updated),
  };
  if (env.CONTEXT_HANDOFF_NATIVE_PHASE === "prepare") {
    if (goal.phase === "prepared") {
      const result = await session.rpc.commands.enqueue({ command: "/exit" });
      if (!result.queued) throw new Error("Prepared successor exit was not accepted; source preserved.");
      return { preparing: true, path };
    }
    await prepareNativeGoal(adapter);
    return { preparing: true, path };
  }
  if (env.CONTEXT_HANDOFF_NATIVE_PHASE !== "resume") {
    throw new Error("Unknown native handoff bootstrap phase; source is preserved.");
  }
  if (!goal.hydratedBySession) await acknowledgeNativeGoal(adapter);
  // Admission is deterministic. A model asked to "only consume" can still
  // execute the brief before ownership or budget admission has completed.
  await session.rpc.workspaces.createFile({
    path: "context-handoff.md", content: record.promptText,
  });
  const consumed = record.storage === "agent-dispatch"
    ? consumeDispatchHandoffTask(goal.cwd, record.taskId || record.handoffId, session.sessionId, true)
    : consumeFileHandoff(goal.cwd, session.sessionId, record.handoffId, record.handoffPath);
  if (!consumed.ok) throw new Error(consumed.message);
  await continueNativeAfterAdmission(session, path);
  return { preparing: false, path };
}

export async function continueNativeAfterAdmission(session, path, {
  readState = () => readNativeGoal(session.sessionId), execute = runCli,
} = {}) {
  const record = nativeCheckpoint(path, session.sessionId);
  let goal = record.nativeGoal;
  if (goal.admissionComplete) {
    retireNativePredecessor(path, session.sessionId);
    return;
  }
  if (goal.consumedBySession !== session.sessionId) {
    throw new Error("Native continuation requires the assigned consumer; predecessor preserved.");
  }
  await assertNativeProfile(session, goal);
  bindNativeSuccessor(record, session.sessionId, execute);
  if (goal.intent !== "running") {
    retireNativePredecessor(path, session.sessionId);
    return;
  }
  if (goal.continuationRequested) {
    const delivered = (await session.getEvents()).find(event =>
      event.type === "user.message" && event.data?.content === goal.continuationPrompt
      && (!goal.continuationMessageId
        || [event.id, event.data.messageId].includes(goal.continuationMessageId)));
    if (!delivered) {
      // send() can acknowledge the startup queue before a user.message is
      // persisted. Let bootstrap finish; user.message/idle will reconcile it.
      // The known send is never repeated and the source remains available.
      if (goal.continuationMessageId) return;
      throw new Error("Native continuation admission is unresolved; do not replay or retire the source.");
    }
    writeJsonAtomic(path, {
      ...record, consumed: true, consumedBySession: session.sessionId,
      nativeGoal: {
        ...goal, continuationMessageId: delivered.data.messageId || delivered.id,
        continuationObserved: delivered.id,
      },
    });
    retireNativePredecessor(path, session.sessionId);
    return;
  }
  const activation = await activateNativeGoal({
    goal, session,
    readState,
    save: updated => {
      goal = updated;
      saveNativeCheckpoint(path, session.sessionId, updated);
    },
  });
  if (!activation) return;
  goal = {
    ...goal, continuationRequested: true,
    continuationPrompt: `${activation.prompt}\n\nHandoff continuation ${record.handoffId}:\n\n${record.promptText}`,
  };
  saveNativeCheckpoint(path, session.sessionId, goal);
  const messageId = await session.send({
    prompt: goal.continuationPrompt,
  });
  const current = nativeCheckpoint(path, session.sessionId);
  writeJsonAtomic(path, {
    ...current,
    nativeGoal: { ...goal, continuationMessageId: messageId },
  });
  await continueNativeAfterAdmission(session, path, { readState, execute });
}

export function retireNativePredecessor(path, sessionId) {
  const record = nativeCheckpoint(path, sessionId);
  const goal = record.nativeGoal;
  if (goal.retired) return;
  if (!record.consumed || goal.hydratedBySession !== sessionId
    || (goal.intent === "running" && !goal.continuationObserved)) {
    throw new Error("Native handoff admission is incomplete; predecessor preserved.");
  }
  writeJsonAtomic(path, { ...record, nativeGoal: { ...goal, admissionComplete: true } });
  if (record.predecessor?.transport === "herdr") {
    const retired = retireHerdrPredecessor(record, sessionId, runCli);
    writeJsonAtomic(path, { ...record, nativeGoal: { ...goal, admissionComplete: true, retired } });
  }
}
