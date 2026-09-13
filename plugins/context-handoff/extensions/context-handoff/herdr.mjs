import { existsSync, mkdirSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { join, parse, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export function isHerdrPane() {
  return process.env.HERDR_ENV === "1" && Boolean(process.env.HERDR_PANE_ID);
}

export function workerLifecycle(record, checkpoint, action, sessionId, execute) {
  const goal = record.nativeGoal;
  const home = goal.workerLifecycle?.config_home || goal.profile?.copilotHome
    || process.env.COPILOT_HOME || join(homedir(), ".copilot");
  const reference = join(home, "session-state", goal.sourceSessionId, "files", "worker-lifecycle.json");
  if (!goal.workerLifecycle && !existsSync(reference)) return { managed: false };
  const result = JSON.parse(execute(join(homedir(), ".local", "bin", "copilot-pane"), [
    "lifecycle", "--config-home", home, action, "--checkpoint", checkpoint, "--session", sessionId,
  ], { cwd: goal.cwd, timeout: 30000 }));
  if (!result.managed) throw new Error("Managed handoff lost its lifecycle registry; source preserved.");
  return result;
}

export function advertiseWorkerLifecycle(sessionId, execute) {
  if (!isHerdrPane()) return;
  const home = process.env.COPILOT_HOME || join(homedir(), ".copilot");
  if (!existsSync(join(home, "worker-lifecycle", "installation.json"))) return;
  execute(join(homedir(), ".local", "bin", "copilot-pane"), [
    "lifecycle", "--config-home", home, "native-ready", "--session", sessionId,
    "--plugin-path", fileURLToPath(new URL("../..", import.meta.url)),
  ], { timeout: 30000 });
}

export function herdrStateDir(cwd) {
  const absolute = resolve(cwd);
  return join(process.env.COPILOT_HOME || join(homedir(), ".copilot"), "context-handoff", "checkouts",
    relative(parse(absolute).root, absolute) || "_root");
}

export function resolveHerdrCwd(cwd, execute) {
  if (!isHerdrPane()) return cwd;
  const response = JSON.parse(execute(join(homedir(), ".local", "bin", "herdr"),
    ["pane", "current", "--current"], { timeout: 5000 }));
  const paneCwd = response?.result?.pane?.cwd;
  if (typeof paneCwd !== "string" || !paneCwd) {
    throw new Error("Herdr did not report the current pane working directory.");
  }
  return paneCwd;
}

function agentIdentity(paneId, execute) {
  const response = JSON.parse(execute(join(homedir(), ".local", "bin", "herdr"),
    ["agent", "get", paneId], { timeout: 5000 }));
  const agent = response?.result?.agent;
  if (agent?.agent !== "copilot" || agent.pane_id !== paneId || !agent.terminal_id) {
    throw new Error(`Herdr pane ${paneId} does not report a Copilot terminal identity.`);
  }
  return {
    paneId, sessionId: agent.agent_session?.value || null,
    terminalId: agent.terminal_id, agentName: agent.name || null,
  };
}

export function captureHerdrPredecessor(sessionId, execute) {
  const identity = agentIdentity(process.env.HERDR_PANE_ID, execute);
  if (!sessionId || (identity.sessionId && identity.sessionId !== sessionId)) {
    throw new Error("Herdr predecessor session does not match the handoff owner.");
  }
  return { ...identity, sessionId, transport: "herdr" };
}

export function retireHerdrPredecessor(metadata, successorSessionId, execute, checkpoint = null) {
  const predecessor = metadata.predecessor;
  if (!isHerdrPane() || !successorSessionId
    || predecessor.paneId === process.env.HERDR_PANE_ID
    || predecessor.sessionId === successorSessionId) {
    throw new Error("Herdr successor identity is not distinct; predecessor preserved.");
  }
  if (metadata.nativeGoal?.workerLifecycle) {
    const state = workerLifecycle(metadata, checkpoint, "handoff-retire-check", successorSessionId, execute);
    if (state.source_exited) {
      return { host: "herdr", successorVerified: true, retired: true, pane: predecessor.paneId, alreadyGone: true };
    }
  }
  const successor = agentIdentity(process.env.HERDR_PANE_ID, execute);
  if (successor.sessionId && successor.sessionId !== successorSessionId) {
    throw new Error("Herdr successor session mismatch; predecessor preserved.");
  }
  const target = agentIdentity(predecessor.paneId, execute);
  if (target.terminalId !== predecessor.terminalId
    || (target.sessionId && target.sessionId !== predecessor.sessionId)
    || (target.agentName && predecessor.agentName && target.agentName !== predecessor.agentName)) {
    throw new Error("Herdr predecessor identity changed; no pane was stopped.");
  }
  execute(join(homedir(), ".local", "bin", "copilot-pane"),
    ["stop", "--pane", predecessor.paneId], { timeout: 30000 });
  return { host: "herdr", successorVerified: true, retired: true, pane: predecessor.paneId };
}

export function launchHerdrSuccessor(cwd, seed, execute, permissionMode, native = null) {
  if (!["manual", "assisted", "allow-all"].includes(permissionMode)) {
    throw new Error("Current predecessor permission mode is required; no pane was created.");
  }
  const launchCwd = resolveHerdrCwd(cwd, execute);
  const stateDir = herdrStateDir(launchCwd);
  mkdirSync(stateDir, { recursive: true });
  const taskDir = mkdtempSync(join(stateDir, "launch-"));
  try {
    const taskFile = join(taskDir, "task.txt");
    writeFileSync(taskFile, seed, { mode: 0o600 });
    const args = [
      "launch", "--role", "coordinator", "--cwd", launchCwd,
      "--host", "local", "--task-file", taskFile, "--permission-mode", permissionMode,
    ];
    if (native) args.push("--native-handoff", native.checkpoint, "--native-launcher", native.launcher);
    const output = execute(join(homedir(), ".local", "bin", "copilot-pane"), args,
      { cwd: launchCwd, timeout: 180000 });
    const values = Object.fromEntries(String(output).trim().split(/\r?\n/).map(line => {
      const at = line.indexOf("=");
      return [line.slice(0, at), line.slice(at + 1)];
    }));
    if (!values.pane_handle || !values.copilot_session_id) {
      throw new Error("copilot-pane did not report its receiver; do not launch another pane.");
    }
    return {
      ok: true, host: "herdr", new_pane: values.pane_handle,
      new_session: values.copilot_session_id, startup_pending: values.startup_pending === "true",
      ...(values.native_startup_error ? { native_startup_error: values.native_startup_error } : {}),
    };
  } finally {
    rmSync(taskDir, { recursive: true, force: true });
  }
}
