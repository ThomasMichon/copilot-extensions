import { execFile } from "node:child_process";
import { realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { delimiter, isAbsolute, join } from "node:path";

function stamp(path, expected) {
  try {
    const info = statSync(path, { bigint: true });
    if (info.isDirectory()) return expected?.[0] === "listing"
      ? ["listing", canonical(path), String(info.mtimeNs)] : ["directory", canonical(path)];
    return ["file", canonical(path), String(info.size), String(info.mtimeNs)];
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
        JSON.stringify(stamp(path, value)) === JSON.stringify(value));
  }
  const check = async input => {
    const identity = key(input);
    if (identity === null) return false;
    if (current(input)) return cached.result.admitted === true;
    if (pending?.key === identity) return pending.promise;
    const promise = Promise.resolve().then(async () => {
      let selected = interpreter(python, environment);
      const systemRoot = environment.SystemRoot || environment.SYSTEMROOT;
      if (selected === null && !python) {
        selected = await new Promise(resolve => {
          let shell, args;
          if (process.platform === "win32" && systemRoot) {
            shell = join(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
            const helper = join(scriptsRoot, "managed-session.ps1").replaceAll("'", "''");
            args = ["-NoProfile", "-NonInteractive", "-Command",
              `try { . '${helper}'; Get-ManagedWorktreesPython } catch { }`];
          } else if (process.platform !== "win32") {
            shell = "/bin/sh";
            args = ["-c", '. "$1/managed-session.sh"; managed_worktrees_python "$1"',
              "managed-session", scriptsRoot];
          } else {
            resolve(null);
            return;
          }
          execFile(shell, args, {
            windowsHide: true, cwd: homedir(), env: environment,
            encoding: "utf8", timeout: 2000, maxBuffer: 65536,
          }, (error, stdout) => resolve(error ? null : interpreter(stdout.trim(), environment)));
        });
      }
      if (selected === null) return false;
      return new Promise(resolve => {
        let timer;
        const child = execFile(selected,
          ["-I", "-S", "-B", "-X", "utf8", join(scriptsRoot, "managed_session.py"), "--base64-input"], {
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
    });
    pending = { key: identity, promise };
    try { return await promise; }
    catch { return false; }
    finally { if (pending?.promise === promise) pending = null; }
  };
  check.current = input => current(input) && cached.result.admitted === true;
  return check;
}
