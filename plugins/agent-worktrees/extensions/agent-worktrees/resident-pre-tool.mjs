import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { lstatSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";

const GUARDS = [
  "statelessness_guard.py", "cross_repo_guard.py",
  "anchor_write_guard.py", "pr_supersede_guard.py",
];
const decoder = new TextDecoder("utf-8", { fatal: true });
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);

function boundedFile(path, limit) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const before = lstatSync(path, { bigint: true });
    if (!before.isFile() || before.isSymbolicLink() || before.size > BigInt(limit)) {
      throw new Error("unsafe-policy-file");
    }
    const bytes = readFileSync(path);
    const after = lstatSync(path, { bigint: true });
    if (bytes.length <= limit && stamp(before) === stamp(after)) {
      return decoder.decode(bytes);
    }
  }
  throw new Error("policy-file-changed");
}

function stamp(info) {
  return [info.dev, info.ino, info.size, info.mtimeNs, info.ctimeNs].join(":");
}

function policyGeneration(scriptsRoot) {
  return ["hook-read-policy.json", ...GUARDS].map(name => {
    const info = lstatSync(join(scriptsRoot, name), { bigint: true });
    if (!info.isFile() || info.isSymbolicLink()) throw new Error("unsafe-policy-file");
    return stamp(info);
  }).join("|");
}

function matchesReadPolicy(scriptsRoot) {
  const policy = JSON.parse(boundedFile(join(scriptsRoot, "hook-read-policy.json"), 4096));
  if (!object(policy) || policy.schema !== "agent-worktrees.native-read-policy" ||
      policy.version !== 1 || !object(policy.guards) ||
      Object.keys(policy.guards).length !== GUARDS.length) return false;
  return GUARDS.every(name => {
    const expected = policy.guards[name];
    if (typeof expected !== "string" || !/^[0-9a-f]{64}$/.test(expected)) return false;
    const source = boundedFile(join(scriptsRoot, name), 131072).replaceAll("\r\n", "\n");
    return createHash("sha256").update(source, "utf8").digest("hex") === expected;
  });
}

function hotStop(environment) {
  if (environment.COPILOT_EXTENSIONS_CONTEXT?.trim()) return null;
  const override = environment.PRETOOL_KILLSWITCH_STATE_FILE;
  if (override && !isAbsolute(override)) return null;
  const path = override || join(homedir(), ".copilot-extensions", "killSwitches", "pretool-killswitch.json");
  try {
    const value = JSON.parse(boundedFile(path, 16384));
    if (!object(value) || typeof value.active !== "boolean") return null;
    if ("contract" in value && (!object(value.contract) ||
        value.contract.id !== "copilot-extensions.pretool-killswitch" ||
        value.contract.version !== 1)) return null;
    return value.active;
  } catch (error) {
    return error.code === "ENOENT" ? false : null;
  }
}

function validResult(value) {
  return object(value) &&
    (value.permissionDecision === undefined || ["allow", "ask", "deny"].includes(value.permissionDecision)) &&
    (value.permissionDecisionReason === undefined || typeof value.permissionDecisionReason === "string") &&
    (value.additionalContext === undefined || typeof value.additionalContext === "string");
}

export function createPythonPolicy({ scriptsRoot, python, environment = process.env }) {
  if (!isAbsolute(python) || !statSync(python).isFile()) {
    throw new Error("resident pre-tool policy requires an absolute existing interpreter");
  }
  return payload => new Promise((resolve, reject) => {
    for (const name of [...GUARDS, "hook_client.py", "hook_runtime.py", "pretool_killswitch.py", "hook_pre_relevance.py"]) {
      boundedFile(join(scriptsRoot, name), 131072);
    }
    const child = execFile(python, ["-X", "utf8", "-B", join(scriptsRoot, "hook_client.py"), "preToolUseStrict"], {
      windowsHide: true, cwd: homedir(), env: environment,
      encoding: "utf8", timeout: 28000, maxBuffer: 65536,
    }, (error, stdout, stderr) => {
      if (stderr) process.stderr.write(stderr);
      if (error) return reject(new Error("policy-process-failed"));
      if (!stdout.trim() && hotStop(environment) !== true) {
        return reject(new Error("policy-response-empty"));
      }
      try {
        resolve(stdout.trim() ? JSON.parse(stdout) : {});
      }
      catch { reject(new Error("policy-response-invalid")); }
    });
    child.stdin.on("error", () => { /* execFile reports child failure through its callback. */ });
    child.stdin.end(JSON.stringify(payload), "utf8");
  });
}

export function createResidentPreToolHook({
  scriptsRoot, invokePolicy, environment = process.env,
  report = reason => console.error(`agent-worktrees resident preToolUse: ${reason}`),
}) {
  let generation = null;
  let readPolicyMatches = false;
  const reported = new Set();
  return async input => {
    try {
      if (!object(input) || typeof input.toolName !== "string" ||
          typeof input.workingDirectory !== "string" || !isAbsolute(input.workingDirectory) ||
          typeof input.sessionId !== "string" || !input.sessionId) {
        throw new Error("invalid-sdk-input");
      }
      const stop = hotStop(environment);
      if (stop === true) return {};
      if (stop === false && ["view", "rg", "glob"].includes(input.toolName)) {
        try {
          for (let attempt = 0; attempt < 2; attempt++) {
            const current = policyGeneration(scriptsRoot);
            if (current === generation) break;
            readPolicyMatches = matchesReadPolicy(scriptsRoot);
            // Never cache a result from a concurrently replaced guard generation.
            generation = current === policyGeneration(scriptsRoot) ? current : null;
            if (generation !== null) break;
          }
          if (generation !== null && readPolicyMatches) return {};
        } catch {
          generation = null;
          readPolicyMatches = false;
        }
      }
      // SDK uses workingDirectory; the existing command guard contract uses cwd.
      const result = await invokePolicy({ ...input, cwd: input.workingDirectory });
      if (!validResult(result)) throw new Error("policy-response-invalid");
      return result;
    } catch (error) {
      const diagnostic = error?.message;
      let reason = ["invalid-sdk-input", "policy-process-failed", "policy-response-empty",
        "policy-response-invalid", "unsafe-policy-file", "policy-file-changed"].includes(diagnostic) ?
        diagnostic : "policy-unavailable";
      if (!reported.has(reason)) {
        reported.add(reason);
        try { report(reason); }
        catch { reason += "-diagnostic-failed"; }
      }
      return {
        permissionDecision: "deny",
        permissionDecisionReason: `agent-worktrees resident pre-tool ${reason}; restore the command hook or repair its owning installation`,
      };
    }
  };
}
