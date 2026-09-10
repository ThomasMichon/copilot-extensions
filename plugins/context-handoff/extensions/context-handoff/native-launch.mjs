import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join, resolve } from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";

export function nativeLaunchArguments(record, phase, args = [], { receiverExists = false } = {}) {
  const goal = record.nativeGoal;
  if (!goal?.successorSessionId || !record.seed) {
    throw new Error("Native handoff launch requires a frozen successor identity and seed.");
  }
  if (goal.permissionMode !== "allow-all") {
    throw new Error(`Native handoff cannot preserve ${goal.permissionMode}; no CLI was launched.`);
  }
  const launchArgs = [];
  const profileOptions = new Set(["--model", "--effort", "--context", "--agent"]);
  for (let index = 0; index < args.length; index++) {
    if (args[index] === "--session-id") {
      if (args[++index] !== goal.successorSessionId) {
        throw new Error("Launcher session identity disagrees with the native handoff.");
      }
    } else if (goal.profile && profileOptions.has(args[index].split("=")[0])) {
      if (!args[index].includes("=")) index++;
    } else {
      launchArgs.push(args[index]);
    }
  }
  if (goal.profile) {
    const { model, agentId } = goal.profile;
    if (!model.modelId) throw new Error("Source model identity is unavailable; source preserved.");
    launchArgs.push("--model", model.modelId);
    if (model.reasoningEffort) launchArgs.push("--effort", model.reasoningEffort);
    if (model.contextTier) launchArgs.push("--context", model.contextTier);
    if (agentId) launchArgs.push("--agent", agentId);
  }
  if (phase === "prepare" && !receiverExists) {
    return [
      ...launchArgs, "--session-id", goal.successorSessionId,
      "--name", `Handoff ${record.handoffId}`.slice(0, 100),
      "--mode", "interactive",
    ];
  }
  return [
    ...launchArgs, "--resume", goal.successorSessionId,
    "--mode", "interactive",
  ];
}

export function runNativeSuccessor({ checkpoint, cli, args = [], spawn = spawnSync }) {
  const load = () => JSON.parse(readFileSync(checkpoint, "utf8"));
  let record = load();
  const run = phase => {
    // First-trust interruption can leave the named empty CLI saved before
    // bootstrap writes the objective. Continue that exact receiver, not a
    // second creation of its UUID.
    const home = record.nativeGoal.profile?.copilotHome
      || process.env.COPILOT_HOME || join(homedir(), ".copilot");
    const receiverExists = existsSync(join(
      home, "session-state", record.nativeGoal.successorSessionId, "events.jsonl",
    ));
    const result = spawn(cli, nativeLaunchArguments(record, phase, args, { receiverExists }), {
      cwd: record.nativeGoal.cwd,
      stdio: "inherit",
      env: {
        ...process.env,
        ...(record.nativeGoal.profile?.copilotHome
          ? { COPILOT_HOME: record.nativeGoal.profile.copilotHome } : {}),
        CONTEXT_HANDOFF_NATIVE_CHECKPOINT: checkpoint,
        CONTEXT_HANDOFF_NATIVE_PHASE: phase,
      },
    });
    if (result.error) throw result.error;
    if (result.status !== 0) {
      throw new Error(`Native handoff ${phase} CLI exited ${result.status ?? result.signal}; predecessor preserved.`);
    }
  };
  if (record.nativeGoal.phase === "frozen") {
    run("prepare");
    record = load();
  }
  if (!["prepared", "hydrated"].includes(record.nativeGoal.phase)
    || record.nativeGoal.admissionComplete) {
    throw new Error("Native successor is not awaiting cold resume; do not launch another receiver.");
  }
  run("resume");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [checkpointFlag, checkpoint, cliFlag, cli, separator, ...args] = process.argv.slice(2);
  if (checkpointFlag !== "--checkpoint" || cliFlag !== "--cli" || separator !== "--") {
    throw new Error("Usage: native-launch.mjs --checkpoint PATH --cli COPILOT -- [CLI options]");
  }
  runNativeSuccessor({ checkpoint, cli, args });
}
