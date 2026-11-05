import { execFile } from "node:child_process";
import { realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { delimiter, isAbsolute, join } from "node:path";

function stamp(path) {
  try {
    const info = statSync(path, { bigint: true });
    if (info.isDirectory()) return ["directory", canonical(path)];
    return [String(info.size), String(info.mtimeNs)];
  } catch { return null; }
}

function canonical(path) {
  const resolved = realpathSync(path);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function interpreter(selected, environment) {
  const usable = path => {
    try {
      return isAbsolute(path) &&
        !(process.platform === "win32" && /[\\/]WindowsApps[\\/]/i.test(path)) &&
        statSync(path).isFile();
    } catch { return false; }
  };
  if (selected) return usable(selected) ? selected : null;
  for (const directory of (environment.PATH || environment.Path || "").split(delimiter)) {
    if (!isAbsolute(directory)) continue;
    const candidate = join(directory, process.platform === "win32" ? "python.exe" : "python3");
    if (usable(candidate)) return candidate;
  }
  return null;
}

export function createManagedSessionAdmission({ scriptsRoot, python, environment = process.env }) {
  let cached = null;
  let pending = null;
  function key(input) {
    const cwd = input?.workingDirectory ?? input?.cwd;
    if (typeof input?.sessionId !== "string" || !input.sessionId ||
        typeof cwd !== "string" || !isAbsolute(cwd)) return null;
    try {
      return JSON.stringify([input.sessionId, canonical(cwd),
        ...["COPILOT_EXTENSIONS_CONTEXT", "AGENT_HOME", "HOME", "USERPROFILE",
          "AGENT_WORKTREES_PAYLOAD_ROOT", "COPILOT_PLUGIN_ROOT"].map(name => environment[name] || "")]);
    } catch { return null; }
  }
  function current(input) {
    return cached?.key === key(input) &&
      Object.entries(cached.result.inputs).every(([path, value]) =>
        JSON.stringify(stamp(path)) === JSON.stringify(value));
  }
  const check = async input => {
    const identity = key(input);
    if (identity === null) return false;
    if (current(input)) return cached.result.admitted === true;
    if (pending?.key === identity) return pending.promise;
    const selected = interpreter(python, environment);
    if (selected === null) return false;
    const promise = new Promise(resolve => {
      let timer;
      const child = execFile(selected,
        ["-I", "-B", "-X", "utf8", join(scriptsRoot, "managed_session.py"), "--base64-input"], {
          windowsHide: true, cwd: homedir(), env: environment,
          encoding: "utf8", timeout: 2000, maxBuffer: 65536,
        }, (error, stdout) => {
          clearTimeout(timer);
          child.stdin?.destroy();
          let result = { admitted: false, inputs: {} };
          if (!error) {
            try {
              const value = JSON.parse(stdout);
              if (typeof value.admitted === "boolean" && value.inputs &&
                  typeof value.inputs === "object" && !Array.isArray(value.inputs) &&
                  (!value.admitted || (value.workspace === canonical(input.workingDirectory ?? input.cwd) &&
                    Object.keys(value.inputs).length > 0))) result = value;
            } catch { /* Missing admission remains inert. */ }
          }
          cached = error || Object.keys(result.inputs).length === 0 ? null : { key: identity, result };
          resolve(result.admitted);
        });
      child.stdin?.on("error", () => {});
      const frame = JSON.stringify({ sessionId: input.sessionId, cwd: input.workingDirectory ?? input.cwd });
      child.stdin?.write(Buffer.from(frame, "utf8").toString("base64") + "\n");
      timer = setTimeout(() => {
        child.stdin?.destroy();
        child.kill();
      }, 2000);
      timer.unref();
    });
    pending = { key: identity, promise };
    try { return await promise; }
    catch { return false; }
    finally { if (pending?.promise === promise) pending = null; }
  };
  check.current = input => current(input) && cached.result.admitted === true;
  return check;
}
