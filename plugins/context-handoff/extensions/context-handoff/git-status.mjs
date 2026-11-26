import { spawn } from "node:child_process";

export function gitStatusHasOutput(cwd, env, launch = spawn, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    let hasOutput = false;
    let stderr = "";
    let timedOut = false;
    const child = launch(
      "git", ["status", "--porcelain", "--ignored", "--untracked-files=all"],
      {
        cwd, env: { ...env, GIT_OPTIONAL_LOCKS: "0" }, windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    const disposePipes = () => {
      child.stdout.destroy();
      child.stderr.destroy();
    };
    const deadline = setTimeout(() => {
      timedOut = true;
      // close can wait on inherited pipes even after Git exits.
      disposePipes();
      child.kill("SIGKILL");
      reject(new Error("Git status inspection timed out"));
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      if (chunk.length > 0) hasOutput = true;
    });
    child.stderr.setEncoding("utf-8");
    child.stderr.on("data", chunk => {
      stderr += chunk.slice(0, Math.max(0, 4096 - stderr.length));
    });
    child.once("error", error => {
      clearTimeout(deadline);
      disposePipes();
      reject(error);
    });
    child.once("close", (code, signal) => {
      clearTimeout(deadline);
      if (timedOut) reject(new Error("Git status inspection timed out"));
      else if (code === 0) resolve(hasOutput);
      else reject(new Error(
        `Git status inspection failed (${signal ? `signal ${signal}` : `exit ${code}`})`
          + (stderr.trim() ? `: ${stderr.trim()}` : ""),
      ));
    });
  });
}
