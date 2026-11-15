// Read-only public SDK observation. It never installs, reloads, or sends a task.
import { isAbsolute, resolve } from "node:path";
import { realpathSync } from "node:fs";

const identity = /^[A-Za-z0-9][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*$/;
const canonical = (path) => realpathSync(resolve(path));
const samePath = (a, b) => canonical(a) === canonical(b);

export function readinessContextMatches(snapshot, request) {
  return snapshot?.sessionId === request.expected_session_id
    && typeof snapshot.workingDirectory === "string"
    && isAbsolute(snapshot.workingDirectory)
    && samePath(snapshot.workingDirectory, request.cwd);
}

export async function observePluginReadiness(
  session, request, { isCurrent = () => true, now = () => performance.now(),
    pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), signal, waitForReady = true } = {},
) {
  const result = (status, reason, missing = [], cwd = null) => ({
    status, reason, missing, cwd, session_id: request?.expected_session_id || "",
    selection_digest: request?.selection_digest || "",
  });
  if (request?.owner !== "bridge" || !request.expected_session_id
      || typeof request.cwd !== "string" || !isAbsolute(request.cwd)
      || !Array.isArray(request.plugins) || request.plugins.length > 512
      || typeof request.selection_digest !== "string"
      || !Number.isFinite(request.timeout_seconds) || request.timeout_seconds <= 0
      || request.timeout_seconds > 120) {
    return result("failed", "invalid-readiness-request");
  }
  const expected = new Set();
  for (const plugin of request.plugins) {
    if (!plugin || !identity.test(plugin.projected_source)
        || expected.has(plugin.projected_source)
        || (plugin.version != null && typeof plugin.version !== "string")
        || (plugin.live_marketplace_root != null
            && (typeof plugin.live_marketplace_root !== "string" || !isAbsolute(plugin.live_marketplace_root)))) {
      return result("failed", "invalid-expected-plugin");
    }
    expected.add(plugin.projected_source);
  }
  let rpc, list, snapshot;
  try {
    rpc = session?.rpc;
    list = rpc?.plugins?.list;
    snapshot = rpc?.metadata?.snapshot;
  } catch {
    return result("failed", "native-observation-error");
  }
  if (typeof list !== "function" || typeof snapshot !== "function") {
    return result("unsupported", "public-session-plugin-observation-unavailable");
  }
  const deadline = now() + request.timeout_seconds * 1000;
  const current = () => !signal?.aborted && isCurrent()
    && typeof session.rpc?.plugins?.list === "function"
    && typeof session.rpc?.metadata?.snapshot === "function";
  let timer;
  let onAbort;
  let missing = [];
  const expired = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("deadline")), request.timeout_seconds * 1000);
    if (signal) {
      onAbort = () => reject(new Error("cancelled"));
      signal.addEventListener("abort", onAbort, { once: true });
    }
  });
  try {
    while (now() < deadline) {
      if (!current()) return result("failed", signal?.aborted ? "cancelled" : "session-changed");
      const before = await Promise.race([snapshot.call(rpc.metadata), expired]);
      if (!readinessContextMatches(before, request)) return result("failed", "session-cwd-or-id-changed");
      const response = await Promise.race([list.call(rpc.plugins), expired]);
      const latest = session.rpc;
      const after = await Promise.race([latest.metadata.snapshot(), expired]);
      if (!current()) return result("failed", signal?.aborted ? "cancelled" : "session-changed");
      if (!readinessContextMatches(after, request)) {
        return result("failed", "session-cwd-or-id-changed");
      }
      if (!response || !Array.isArray(response.plugins)) return result("failed", "malformed-plugin-list");
      const rows = new Map();
      for (const plugin of response.plugins) {
        if (!plugin || typeof plugin.name !== "string" || typeof plugin.marketplace !== "string"
            || typeof plugin.enabled !== "boolean"
            || (plugin.installed !== undefined && typeof plugin.installed !== "boolean")
            || (plugin.version !== undefined && typeof plugin.version !== "string")
            || (plugin.installedFrom !== undefined
                && (typeof plugin.installedFrom !== "string" || !isAbsolute(plugin.installedFrom)))) {
          return result("failed", "malformed-plugin-row");
        }
        const key = `${plugin.name}@${plugin.marketplace}`;
        if (!rows.has(key)) rows.set(key, []);
        rows.get(key).push(plugin);
      }
      missing = [];
      for (const plugin of request.plugins) {
        const candidates = rows.get(plugin.projected_source) || [];
        const matches = !candidates.some((row) => row.installed === false) && candidates.some((row) =>
          row.enabled && row.installed !== false
            && !(plugin.version && row.version && plugin.version !== row.version)
            && !(row.installedFrom && plugin.live_marketplace_root
              && !samePath(row.installedFrom, plugin.live_marketplace_root)));
        if (!matches) {
          missing.push(plugin.projected_source);
        }
      }
      if (!missing.length) return result("ready", "selected-loadout-registered", [], after.workingDirectory);
      if (!waitForReady) return result("failed", "selected-loadout-pending", missing, after.workingDirectory);
      await Promise.race([pause(Math.min(250, Math.max(0, deadline - now()))), expired]);
    }
    return result("failed", "readiness-timeout", missing);
  } catch {
    return result("failed", signal?.aborted ? "cancelled"
      : now() >= deadline ? "readiness-timeout" : "native-observation-error", missing);
  } finally {
    clearTimeout(timer);
    if (onAbort) signal.removeEventListener("abort", onAbort);
  }
}
