import { spawn } from "node:child_process";

export function gitStatusHasOutput(cwd, env, launch = spawn) {
  return new Promise((resolve, reject) => {
    let hasOutput = false;
    const child = launch(
      "git", ["status", "--porcelain", "--ignored", "--untracked-files=all"],
      { cwd, env, windowsHide: true, timeout: 5000, stdio: ["ignore", "pipe", "ignore"] },
    );
    child.stdout.on("data", (chunk) => {
      if (chunk.length > 0) hasOutput = true;
    });
    child.once("error", reject);
    child.once("close", (code, signal) => {
      if (code === 0) resolve(hasOutput);
      else if (child.killed) reject(new Error("Git status inspection timed out"));
      else reject(new Error(`Git status inspection failed (${signal ? `signal ${signal}` : `exit ${code}`})`));
    });
  });
}
