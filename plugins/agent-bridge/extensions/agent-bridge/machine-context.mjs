// Observe the reporting process, not the daemon or a worktree's saved name.
import { readFile, stat } from "node:fs/promises";
import { hostname, release } from "node:os";

export async function executionObservation({
  platform = process.platform, host = hostname(), kernel = release(),
  read = (path) => readFile(path, "utf8"),
  isFile = async (path) => stat(path).then((info) => info.isFile()).catch(() => false),
} = {}) {
  let environment = "linux";
  if (platform === "win32") environment = "windows";
  else if (platform === "linux") {
    const markers = await Promise.all(["/.dockerenv", "/run/.containerenv"].map(isFile));
    const cgroup = await read("/proc/1/cgroup").catch(() => "");
    if (!markers.some(Boolean) && !["/docker", "/kubepods", "containerd", "/lxc", "libpod"]
      .some((tag) => cgroup.includes(tag))) {
      const version = await read("/proc/version").catch(() => "");
      if (/microsoft/i.test(`${kernel}\n${version}`)) environment = "wsl";
    }
  } else return null;
  return { hostname: host, environment };
}

export function machineLookup(request, { observe = executionObservation } = {}) {
  return async () => {
    try {
      const observation = await observe();
      if (!observation) return null;
      const query = new URLSearchParams(observation);
      const response = await request("GET", `/api/v1/execution-context?${query}`);
      // Only an older provider's absent route permits the legacy CLI adapter.
      if (response.status === 404) return undefined;
      if (!response.ok) return null;
      const body = await response.json();
      const context = body?.context;
      if (body?.schema !== "agent-bridge.execution-context/v1"
          || context?.hostname !== observation.hostname
          || context?.environment !== observation.environment
          || typeof context?.execution_machine !== "string"
          || !/^[^\s/\\#\0]+$/.test(context.execution_machine)) return null;
      return context.execution_machine;
    } catch {
      return null;
    }
  };
}
