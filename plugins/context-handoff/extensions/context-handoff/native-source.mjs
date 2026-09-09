import { randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  storeHandoff, buildSeedForStored, sessionStateHandoffPath,
  writeSessionStateHandoff, readSessionStateHandoff, writeJsonAtomic, runCli,
} from "./handoff-core.mjs";
import { readNativeGoal } from "./native-transport.mjs";
import { freezeNativeGoal } from "./native-goal.mjs";
import { isHerdrPane, resolveHerdrCwd, launchHerdrSuccessor } from "./herdr.mjs";

export const NATIVE_LAUNCHER = join(dirname(fileURLToPath(import.meta.url)), "native-launch.mjs");

export function recoverPendingHandoff(sessionId) {
  const found = readSessionStateHandoff(sessionId);
  if (!found || found.record.consumed) return null;
  const { record, path } = found;
  return {
    seed: record.seed, token: record.handoffId, promptText: record.promptText,
    nativeGoalCheckpoint: record.nativeGoal ? path : null,
    stored: {
      id: record.handoffId, storage: record.storage,
      taskId: record.taskId, path: record.handoffPath,
      metadata: {
        worktree: record.worktree, worktreeDir: record.worktreeDir,
        stateDir: record.stateDir, title: record.title, predecessor: record.predecessor,
      },
    },
  };
}

export async function requestPlainCutover(session, pending, cwd) {
  if (!pending?.stored || !pending.promptText) {
    throw new Error("Recover the saved handoff before requesting a live cutover.");
  }
  if ((await readNativeGoal(session.sessionId)).state) {
    throw new Error("A native goal appeared after save; save a native handoff before continuing.");
  }
  const permissionMode = (await session.rpc.permissions.getMode()).mode;
  if (isHerdrPane() && permissionMode !== "allow-all") {
    throw new Error(`Herdr cannot preserve ${permissionMode}; no pane was created.`);
  }
  const previous = readSessionStateHandoff(session.sessionId)?.record;
  if (previous?.seed === pending.seed && previous.launch) return { launch: previous.launch };
  if (previous?.seed === pending.seed && previous.launchRequested) {
    throw new Error("Existing receiver launch is unresolved; do not replay.");
  }
  await session.rpc.mode.set({ mode: "interactive" });
  const request = writeSessionStateHandoff({
    sid: session.sessionId, promptText: pending.promptText, stored: pending.stored, seed: pending.seed,
  });
  if (!request.ok) throw new Error(request.error);
  writeJsonAtomic(request.path, {
    ...request.record, nativeGoal: null, liveCutover: true,
    permissionMode, cwd: resolveHerdrCwd(cwd, runCli),
  });
  return { path: request.path, pending: true };
}

export async function saveNativeBaton(session, { promptText, title, cwd, preferTask = true }) {
  const sid = session.sessionId;
  const previous = readSessionStateHandoff(sid)?.record;
  if (previous?.nativeGoal?.launchRequested && !previous.consumed) {
    throw new Error("A native successor is already being launched; use the existing baton, not a second save.");
  }
  const resolvedCwd = resolveHerdrCwd(cwd, runCli);
  const native = (await readNativeGoal(sid)).state;
  const stored = storeHandoff({
    promptText, title, cwd: resolvedCwd, sid, preferTask,
    nativeGoalCheckpoint: sessionStateHandoffPath(sid),
    nativeGoalState: native ? "present" : "none",
  });
  if (!stored.storage) return stored;
  const result = writeSessionStateHandoff({
      sid, promptText, stored, seed: buildSeedForStored(stored),
      nativeGoal: {
        version: 1, phase: "pending", sourceSessionId: sid,
        successorSessionId: randomUUID(), sourceObjectiveId: native?.id ?? null, cwd: resolvedCwd,
      },
    });
  if (!result.ok) throw new Error(result.error);
  return stored;
}

export async function requestNativeCutover(session, seed) {
  const found = readSessionStateHandoff(session.sessionId);
  if (!found?.record?.nativeGoal || found.record.seed !== seed) {
    throw new Error("No matching native handoff was saved for this session.");
  }
  const { record, path } = found;
  const goal = record.nativeGoal;
  if (record.consumed || goal.admissionComplete) {
    throw new Error("This handoff is already admitted; do not resume the source.");
  }
  if (goal.launch) return { path, launch: goal.launch };
  if (goal.launchRequested) {
    if (["prepared", "hydrated"].includes(goal.phase)) {
      return { path, launch: {
        ok: true, new_session: goal.successorSessionId,
        startup_pending: !goal.admissionComplete, recovered: true,
      } };
    }
    throw new Error("Successor launch outcome is unresolved; inspect the existing receiver rather than launching again.");
  }
  if (goal.phase !== "pending" && goal.phase !== "frozen") {
    throw new Error("This native handoff has already advanced to successor preparation.");
  }
  const state = (await readNativeGoal(session.sessionId)).state;
  if ((state?.id ?? null) !== goal.sourceObjectiveId) {
    throw new Error("The source objective changed after save; preserve it and save a new handoff.");
  }
  const mode = await session.rpc.mode.get();
  const permissionMode = (await session.rpc.permissions.getMode()).mode;
  if (isHerdrPane() && permissionMode !== "allow-all") {
    throw new Error(`Herdr cannot preserve ${permissionMode} with the installed launch contract; no pane was created.`);
  }
  const profile = {
    model: await session.rpc.model.getCurrent(),
    agentId: (await session.rpc.agent.getCurrent()).agent?.id || null,
    copilotHome: process.env.COPILOT_HOME || null,
  };
  const pause = await session.rpc.mode.set({ mode: "interactive" });
  if (pause.confirmation || pause.deferImplementation) {
    throw new Error("Native handoff pause requires host confirmation; no successor was launched.");
  }
  writeJsonAtomic(path, {
    ...record,
    nativeGoal: {
      ...goal, permissionMode, profile, cutoverRequested: true,
      intent: goal.intent ?? (state?.status === "active" && mode === "autopilot" ? "running" : "stopped"),
    },
  });
  return { path, pending: true };
}

export async function freezeNativeSource(session, path) {
  const found = readSessionStateHandoff(session.sessionId);
  if (!found || found.path !== path) throw new Error("Native source checkpoint is unavailable.");
  let record = found.record;
  let goal = record.nativeGoal;
  if (!goal) return record;
  if (goal.phase === "pending") {
    const state = (await readNativeGoal(session.sessionId)).state;
    if ((state?.id ?? null) !== goal.sourceObjectiveId) {
      throw new Error("Source objective changed before freeze; no successor was launched.");
    }
    const { content } = await session.rpc.workspaces.readAutopilotObjective();
    goal = {
      ...freezeNativeGoal({
        sourceSessionId: session.sessionId, successorSessionId: goal.successorSessionId,
        cwd: goal.cwd, intent: goal.intent, state, content,
      }),
      permissionMode: goal.permissionMode,
      profile: goal.profile,
      cutoverRequested: goal.cutoverRequested,
      sourceObjectiveId: goal.sourceObjectiveId,
    };
    record = { ...record, nativeGoal: goal };
  }
  if (goal.phase !== "frozen") throw new Error("Native goal was not frozen; source is preserved.");
  writeJsonAtomic(path, record);
  return record;
}

export async function freezeAndLaunchNative(session, path) {
  const found = readSessionStateHandoff(session.sessionId);
  if (!found || found.path !== path) throw new Error("Source checkpoint is unavailable.");
  const receipt = found.record.nativeGoal || found.record;
  if (receipt.launch) return receipt.launch;
  if (receipt.launchRequested) throw new Error("Existing receiver launch is unresolved; do not replay.");
  let record = await freezeNativeSource(session, path);
  let goal = record.nativeGoal;
  if (!goal) {
    writeJsonAtomic(path, { ...record, launchRequested: true });
    const launch = isHerdrPane()
      ? launchHerdrSuccessor(record.cwd, record.seed, runCli, record.permissionMode)
      : JSON.parse(runCli("agent-worktrees", [
        "handoff-cutover", "--seed", record.seed, "--session-id", session.sessionId,
        "--handoff-token", record.handoffId,
        ...(record.worktree ? ["--worktree-id", record.worktree] : []),
      ], { cwd: record.cwd, timeout: 180000 }));
    if (!launch.ok) throw new Error(`Handoff launch failed: ${launch.error || launch.reason}`);
    const current = readSessionStateHandoff(session.sessionId).record;
    writeJsonAtomic(path, { ...current, launch });
    return launch;
  }
  goal = {
    ...goal, launchRequested: true,
    launchTransport: isHerdrPane() ? "herdr" : "mux",
  };
  record = { ...record, nativeGoal: goal };
  writeJsonAtomic(path, record);
  const launch = isHerdrPane()
    ? launchHerdrSuccessor(goal.cwd, record.seed, runCli, goal.permissionMode, {
      checkpoint: path, launcher: NATIVE_LAUNCHER,
    })
    : JSON.parse(runCli("agent-worktrees", [
      "handoff-cutover", "--seed", record.seed,
      "--session-id", session.sessionId, "--handoff-token", record.handoffId,
      "--native-handoff", path, "--native-launcher", NATIVE_LAUNCHER,
      ...(record.worktree ? ["--worktree-id", record.worktree] : []),
    ], { cwd: goal.cwd, timeout: 180000 }));
  if (!launch.ok && !launch.new_pane) {
    writeJsonAtomic(path, { ...record, nativeGoal: { ...goal, launchRequested: false } });
    throw new Error(`Native handoff launch failed: ${launch.error || launch.reason}`);
  }
  // The successor can hydrate while the launch command is returning; merge
  // into its current receipt instead of overwriting it with the frozen copy.
  const current = readSessionStateHandoff(session.sessionId).record;
  writeJsonAtomic(path, { ...current, nativeGoal: { ...current.nativeGoal, launch } });
  return launch;
}
