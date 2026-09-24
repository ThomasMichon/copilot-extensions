// Async, non-blocking session-metadata resolution for the agent-bridge
// extension. Split out from extension.mjs (which calls joinSession() at
// import time, making it hard to unit test directly) so this logic is
// exercised hermetically, the same way delivery.mjs already is.
//
// NEVER use execSync/execFileSync here. Those suspend the ENTIRE process --
// no I/O of any kind, including the host's own readiness/liveness handshake
// with the extension -- for as long as the child takes to exit. Resolving
// session metadata used to run four external spawns (git + three
// `agent-worktrees get` calls) sequentially via execSync/execFileSync, which
// could block the whole process for their combined timeout (up to ~29s).
// Confirmed live as the cause of a ready-timeout crash-loop: under
// facility-wide session contention (many Copilot processes competing for the
// same CPU), those blocking spawns ran long enough that the extension never
// reported ready in time, and the host killed + respawned it in a loop that
// never resolved on its own.
//
// `execFile` (callback-based, wrapped in a Promise) keeps every child async:
// the event loop stays free to service everything else while a spawn is in
// flight, so a slow one degrades registration latency, never extension
// readiness. The four calls also run in parallel (`Promise.all`), bounded by
// the single slowest one instead of their sum.
import { exec, execFile } from "node:child_process";

// --- Async CLI runner (non-blocking; never freezes the event loop) ---
// Mirrors the platform split the old synchronous runCli used (Windows
// binstubs are .cmd -> need a shell; POSIX can exec the binary directly) --
// `exec` with a manually-quoted command line on Windows avoids Node's
// shell:true-with-argv deprecation warning (args would otherwise be
// concatenated unescaped), while POSIX uses `execFile` directly with no
// shell at all.
export function runCliAsync(bin, args, cwd) {
  return new Promise((resolve) => {
    try {
      if (process.platform === "win32") {
        const line = [bin, ...args.map((a) => `"${String(a).replace(/"/g, '""')}"`)].join(" ");
        exec(line, { cwd, timeout: 8000, encoding: "utf-8", windowsHide: true }, (err, stdout) => {
          resolve(err ? null : String(stdout).trim());
        });
      } else {
        execFile(bin, args, { cwd, timeout: 8000, encoding: "utf-8" }, (err, stdout) => {
          resolve(err ? null : String(stdout).trim());
        });
      }
    } catch {
      resolve(null);
    }
  });
}

// --- One-time session metadata ---
// Deliberately never awaited on the critical path to readiness by the caller
// (see extension.mjs's load-time init) -- state.meta starts unset and this
// fills it in whenever it resolves; register()'s payload spread tolerates an
// absent/partial value and self-corrects on the next heartbeat.
export async function resolveMetadataAsync({ cwd = process.cwd(), env = process.env } = {}) {
  const getAsync = (key) => runCliAsync("agent-worktrees", ["get", key], cwd); // marketplace-isolation: allow agent-worktrees-management
  const [branch, sessionScopeId, machine, repo] = await Promise.all([
    runCliAsync("git", ["rev-parse", "--abbrev-ref", "HEAD"], cwd),
    getAsync("session-scope-id"),
    getAsync("machine"),
    getAsync("project"),
  ]);
  return {
    machine,
    cwd,
    // A venue launcher may pin a venue-qualified identity (e.g.
    // `anchor-<repo>@<codespace>`) via AGENT_BRIDGE_SCOPE_ID so several
    // venues of the same repo stay distinguishable on the host bridge.
    worktree_id: env.AGENT_BRIDGE_SCOPE_ID || sessionScopeId || null,
    repo,
    branch: branch || null,
    // process.pid is the extension host process -- a liveness hint, not the
    // copilot PID. The durable key is session_id; liveness is heartbeat-based.
    pid: process.pid,
    role: null,
    // D4: who is steering this session, if an agent embodied it (set by
    // `agent-worktrees embody --driver`). Surfaces the "driven by <agent>"
    // banner so a human dropping in via Neuron Forge sees who's at the wheel.
    // Absent/null for an operator-launched session.
    driven_by: env.AGENT_BRIDGE_DRIVEN_BY || null,
  };
}
