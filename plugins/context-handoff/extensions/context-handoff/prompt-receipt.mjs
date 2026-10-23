import { basename } from "node:path";
import { extractRecoveryLocatorFromPrompt, recoveryLocatorFor } from "./cutover-seed.mjs";
import { observeHandoffPromptReceived } from "./handoff-core.mjs";
import { PROMPT_RECEIPT_RETRY_MS } from "./cli-timeouts.mjs";

// One slot, not a per-event queue. Failures retain the exact first locator;
// later lifecycle events may reconcile it without launching a polling loop.
export function registerPromptReceiptObservation(session, state, {
  observe = observeHandoffPromptReceived,
  env = process.env,
  now = Date.now,
  retryMs = PROMPT_RECEIPT_RETRY_MS,
} = {}) {
  if (state.promptReceiptObservation) return state.promptReceiptObservation;
  let pending = null;
  let running = null;
  let scheduled = null;
  let controller = null;
  let retryAt = 0;
  let disposed = false;
  let status = { status: "idle" };
  const unsubscribers = [];

  function reportUnavailable() {
    try {
      session.log(
        `[Context Handoff] prompt receipt unavailable (${status.reason || "observation-failed"}); retained for later reconciliation.`,
        { level: "warning" },
      );
    } catch { /* a disconnected logging sink must not reject background work */ }
  }

  function reconcile() {
    if (disposed || !pending || running || scheduled || now() < retryAt) return;
    status = { status: "pending" };
    scheduled = setImmediate(() => {
      scheduled = null;
      if (disposed) return;
      controller = new AbortController();
      running = Promise.resolve().then(() => observe({
        ...pending, env, signal: controller.signal,
      })).then((result) => {
        if (disposed) return;
        status = result;
        if (result.status === "logged") pending = null;
        else {
          retryAt = now() + retryMs;
          reportUnavailable();
        }
      }, () => {
        if (!disposed) {
          status = { status: "unavailable", reason: "observation-failed" };
          retryAt = now() + retryMs;
          reportUnavailable();
        }
      }).finally(() => { running = null; controller = null; });
    });
  }

  function onMessage(event) {
    if (disposed || event.agentId) return;
    state.turnCount++;
    if (!state.firstUserPrompt && event.data?.content) {
      state.firstUserPrompt = event.data.content;
    }
    if (state.turnCount === 1 && !state.pickupSignalChecked) {
      state.pickupSignalChecked = true;
      const locator = extractRecoveryLocatorFromPrompt(event.data?.content);
      if (locator) {
        const cwd = state.cwd || process.cwd();
        // Never trust legacy ambient WORKTREE_ID. A qualified launch reference
        // is usable only for this cwd or this exact session's bare resume.
        const match = /^([^/#]+)\/([^/#]+)\/([^/#]+)(?:#([^/#]+))?$/.exec(
          env.AGENT_WORKTREES_OWNER_REF || "",
        );
        const launchIdentity = match && (
          basename(cwd) === match[3]
          || (state.sessionId && state.sessionId === match[4])
        ) ? { worktreeId: match[3], project: match[2] } : null;
        pending = {
          cwd, sid: state.sessionId, launchIdentity,
          locator: recoveryLocatorFor(locator.kind, locator.id),
        };
      }
    }
    reconcile();
  }

  for (const [event, callback] of [
    ["user.message", onMessage],
    ["session.idle", reconcile],
    ["tool.execution_complete", reconcile],
  ]) {
    const unsubscribe = session.on(event, callback);
    if (typeof unsubscribe === "function") unsubscribers.push(unsubscribe);
  }
  const observation = {
    get status() { return { ...status }; },
    reconcile,
    async dispose() {
      disposed = true;
      if (scheduled) clearImmediate(scheduled);
      scheduled = null;
      controller?.abort();
      for (const unsubscribe of unsubscribers) unsubscribe();
      await running;
      pending = null;
      status = { status: "disposed" };
      delete state.promptReceiptObservation;
    },
  };
  state.promptReceiptObservation = observation;
  return observation;
}
