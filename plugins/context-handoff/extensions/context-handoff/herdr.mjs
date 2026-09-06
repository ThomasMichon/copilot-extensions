import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { join, parse, relative, resolve } from "node:path";

export function isHerdrPane() {
  return process.env.HERDR_ENV === "1" && Boolean(process.env.HERDR_PANE_ID);
}

export function herdrStateDir(cwd) {
  const absolute = resolve(cwd);
  return join(
    homedir(), ".copilot", "context-handoff", "checkouts",
    relative(parse(absolute).root, absolute) || "_root",
  );
}

export function resolveHerdrCwd(cwd, execute) {
  if (!isHerdrPane()) return cwd;
  const response = JSON.parse(execute(
    join(homedir(), ".local", "bin", "herdr"),
    ["pane", "current", "--current"], { timeout: 5000 },
  ));
  // The foreground process can be an extension in a replaced plugin payload.
  const paneCwd = response?.result?.pane?.cwd;
  if (typeof paneCwd !== "string" || !paneCwd) {
    throw new Error("Herdr did not report the current pane working directory.");
  }
  return paneCwd;
}

function agentIdentity(paneId, execute) {
  const response = JSON.parse(execute(
    join(homedir(), ".local", "bin", "herdr"),
    ["agent", "get", paneId], { timeout: 5000 },
  ));
  const agent = response?.result?.agent;
  if (agent?.agent !== "copilot" || agent.pane_id !== paneId || !agent.terminal_id) {
    throw new Error(`Herdr pane ${paneId} does not report a Copilot terminal identity.`);
  }
  return {
    paneId,
    sessionId: agent.agent_session?.value || null,
    terminalId: agent.terminal_id,
    agentName: agent.name || null,
  };
}

export function captureHerdrPredecessor(sessionId, execute) {
  const identity = agentIdentity(process.env.HERDR_PANE_ID, execute);
  if (!sessionId || (identity.sessionId && identity.sessionId !== sessionId)) {
    throw new Error("Herdr predecessor session does not match the handoff owner.");
  }
  return { ...identity, sessionId, transport: "herdr" };
}

export function retireHerdrPredecessor(metadata, successorSessionId, execute) {
  const predecessor = metadata.predecessor;
  const result = { retired: false, host: "herdr", successorVerified: false };
  if (
    !isHerdrPane() || !successorSessionId
    || predecessor.paneId === process.env.HERDR_PANE_ID
    || predecessor.sessionId === successorSessionId
  ) {
    return { ...result, manualCleanup: "Herdr successor identity is not distinct; predecessor preserved." };
  }
  try {
    const successor = agentIdentity(process.env.HERDR_PANE_ID, execute);
    if (successor.sessionId && successor.sessionId !== successorSessionId) {
      return { ...result, manualCleanup: "Herdr successor session mismatch; predecessor preserved." };
    }
    result.successorVerified = true;
    const target = agentIdentity(predecessor.paneId, execute);
    if (
      target.terminalId !== predecessor.terminalId
      || (target.sessionId && target.sessionId !== predecessor.sessionId)
      || (target.agentName && predecessor.agentName && target.agentName !== predecessor.agentName)
    ) {
      return { ...result, manualCleanup: "Herdr predecessor identity changed; no pane was stopped." };
    }
    execute(
      join(homedir(), ".local", "bin", "copilot-pane"),
      ["stop", "--pane", predecessor.paneId], { timeout: 30000 },
    );
    return { ...result, retired: true, pane: predecessor.paneId };
  } catch (error) {
    return {
      ...result,
      manualCleanup: `Herdr retirement failed; predecessor preserved: ${error.message}`,
    };
  }
}

export function launchHerdrSuccessor(cwd, seed, execute, permissionMode) {
  if (!["manual", "assisted", "allow-all"].includes(permissionMode)) {
    return {
      ok: false, host: "herdr", reason: "error",
      error: "The predecessor's current permission mode is required; no pane was created.",
    };
  }
  let taskDir;
  try {
    const launchCwd = resolveHerdrCwd(cwd, execute);
    const stateDir = herdrStateDir(launchCwd);
    mkdirSync(stateDir, { recursive: true });
    taskDir = mkdtempSync(join(stateDir, "launch-"));
    const taskFile = join(taskDir, "task.txt");
    writeFileSync(taskFile, seed, { mode: 0o600 });
    const output = execute(
      join(homedir(), ".local", "bin", "copilot-pane"),
      [
        "launch", "--role", "coordinator", "--cwd", launchCwd,
        "--host", "local", "--task-file", taskFile,
        "--permission-mode", permissionMode,
      ],
      { cwd: launchCwd, timeout: 180000 },
    );
    const values = Object.fromEntries(String(output).trim().split(/\r?\n/).map(line => {
      const at = line.indexOf("=");
      return [line.slice(0, at), line.slice(at + 1)];
    }));
    if (!values.pane_handle || !values.copilot_session_id) {
      throw new Error("copilot-pane did not report its seeded successor; do not launch another pane.");
    }
    return {
      ok: true, host: "herdr", old_pane: process.env.HERDR_PANE_ID,
      new_pane: values.pane_handle, new_session: values.copilot_session_id,
      startup_pending: values.startup_pending === "true",
    };
  } catch (error) {
    return { ok: false, host: "herdr", reason: "error", error: error.message };
  } finally {
    if (taskDir) rmSync(taskDir, { recursive: true, force: true });
  }
}
