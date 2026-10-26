import assert from "node:assert/strict";

// Unit RPC-owner double, not a persisted native-host qualification.
export function nativeClientMetadata({ sessionId = "subject", remote = false, initial = {} } = {}) {
  const runtime = {
    bag: { ...initial }, calls: [], warnings: [],
    beforeUpdate: null, afterUpdate: null, beforeGet: null,
    snapshot: { sessionId, isRemote: remote, workspacePath: null },
    client() {
      const handlers = new Map();
      const session = {
        sessionId,
        on(name, callback) {
          const entries = handlers.get(name) || new Set();
          entries.add(callback);
          handlers.set(name, entries);
          return () => entries.delete(callback);
        },
        log(message) { runtime.warnings.push(message); },
        emit(name, event = {}) { for (const callback of handlers.get(name) || []) callback(event); },
        rpc: { metadata: {
          async snapshot() {
            runtime.calls.push({ method: "snapshot" });
            return { ...runtime.snapshot };
          },
          async getClientMetadata() {
            runtime.calls.push({ method: "get" });
            await runtime.beforeGet?.();
            return { ...runtime.bag };
          },
          async updateClientMetadata(patch) {
            assert.deepEqual(Object.keys(patch), ["set"]);
            runtime.calls.push({ method: "update", patch: structuredClone(patch) });
            await runtime.beforeUpdate?.(patch);
            const next = { ...runtime.bag, ...patch.set };
            const entries = Object.entries(next);
            if (entries.length > 128 || entries.some(([key, value]) =>
              !key || Buffer.byteLength(key) > 256 || typeof value !== "string"
              || Buffer.byteLength(value) > 16384)
                || Buffer.byteLength(JSON.stringify(next)) > 65536) {
              throw new Error("native metadata capacity");
            }
            runtime.bag = next;
            const committed = { ...next };
            await runtime.afterUpdate?.(patch);
            return committed;
          },
        } },
      };
      return session;
    },
  };
  return runtime;
}

export function pendingIdentity(sessionId = "subject", locator = "task:one") {
  return {
    installation: {
      installationId: "installation", sourceFingerprint: "source",
      marketplaceId: "marketplace", pluginId: "agent-worktrees",
    },
    runtimeRoot: process.cwd(),
    subject: {
      project: "project", worktreeId: "worktree", creationNonce: "incarnation",
      sessionId, locator,
    },
  };
}

export function context(milliseconds = 1500) {
  const controller = new AbortController();
  return { deadline: performance.now() + milliseconds, signal: controller.signal, controller };
}

export async function until(predicate, timeout = 3000) {
  const deadline = performance.now() + timeout;
  while (!predicate()) {
    assert.ok(performance.now() < deadline, "unit fixture did not settle");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}
