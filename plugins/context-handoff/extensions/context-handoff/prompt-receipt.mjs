import { extractRecoveryLocatorFromPrompt, recoveryLocatorFor } from "./cutover-seed.mjs";
import { observeHandoffPromptReceived } from "./handoff-core.mjs";
import { PROMPT_RECEIPT_RETRY_MS } from "./cli-timeouts.mjs";
import { OBSERVATION_MS, SETTLEMENT_RESERVE_MS } from "./resident-receipt-client.mjs";
import { createNativeReceiptPendingSlot, PendingSlotError, pendingSlotReason, waitForReceiptWork } from "./prompt-receipt-state.mjs";

function same(a, b) {
  return a && b && Object.keys(a).length === Object.keys(b).length
    && Object.keys(a).every((key) => a[key] === b[key]);
}
function stable(value) {
  return value && Object.fromEntries(Object.entries(value)
    .filter(([key]) => key !== "installGeneration" && key !== "activationGeneration"));
}

export function registerNativePromptReceiptObservation(session, state, options = {}) {
  return registerPromptReceiptObservation(session, state, {
    ...options, pendingSlot: createNativeReceiptPendingSlot(session),
  });
}

// One slot, not a per-event queue. Failures retain the exact first locator;
// later lifecycle events may reconcile it without launching a polling loop.
export function registerPromptReceiptObservation(session, state, {
  observe = observeHandoffPromptReceived,
  sessionDir = null,
  now = Date.now,
  retryMs = PROMPT_RECEIPT_RETRY_MS,
  pendingSlot = createNativeReceiptPendingSlot(session),
} = {}) {
  if (state.promptReceiptObservation) return state.promptReceiptObservation;
  let pending = state.receiptPending || null;
  let running = null;
  let scheduled = null;
  let controller = null;
  let retryAt = 0;
  let disposed = false;
  let restored = false;
  let captureEstablishedHere = false;
  let terminal = false;
  let terminalStatus = "logged";
  let status = { status: "idle" };
  const unsubscribers = [];

  function reportUnavailable() {
    try {
      session.log(
        `[Context Handoff] prompt receipt unavailable (${/^[a-z][a-z0-9-]{0,79}$/u.test(status.reason || "") ? status.reason : "observation-failed"}); retained for later reconciliation.`,
        { level: "warning" },
      );
    } catch { /* a disconnected logging sink must not reject background work */ }
  }

  function adopt(snapshot) {
    if (!snapshot.capture) return;
    if (pending && (pending.sid && pending.sid !== snapshot.sessionId
        || pending.locator !== snapshot.capture.locator)) throw new PendingSlotError("pending-identity-conflict");
    if (!pending || pending.locator !== snapshot.capture.locator) {
      pending = {
        cwd: state.cwd || process.cwd(), sid: snapshot.sessionId, sessionDir,
        locator: snapshot.capture.locator, observationState: {},
      };
    }
    pending.sid = pending.sid || snapshot.sessionId;
    const observationState = pending.observationState;
    if (snapshot.identity) {
      const identity = snapshot.identity;
      if (observationState.subject && !same(observationState.subject, identity.subject)
          || observationState.installation && !same(stable(observationState.installation), identity.installation)
          || observationState.runtimeRoot && observationState.runtimeRoot !== identity.runtimeRoot) {
        throw new PendingSlotError("pending-identity-conflict");
      }
      const hadIdentity = Boolean(observationState.subject && observationState.installation && observationState.runtimeRoot);
      observationState.subject ??= identity.subject;
      observationState.installation ??= identity.installation;
      observationState.runtimeRoot ??= identity.runtimeRoot;
      if (!restored || !hadIdentity) observationState.uncertain = true;
    }
    if (snapshot.status === "confirmed" || snapshot.status === "checked") {
      terminal = true;
      terminalStatus = snapshot.status === "confirmed" ? "logged" : "idle";
      pending = null;
      delete state.receiptPending;
      state.pickupSignalChecked = true;
      return;
    }
    state.receiptPending = pending;
    state.pickupSignalChecked = true;
  }

  async function attempt(context) {
    const check = () => {
      if (disposed || context.signal.aborted || performance.now() >= context.deadline) {
        throw new PendingSlotError("pending-observation-cancelled");
      }
    };
    let snapshot = await pendingSlot.restore(context);
    check();
    adopt(snapshot);
    if (terminal) return { status: terminalStatus, reason: terminalStatus === "logged"
      ? "metadata-confirmed" : "metadata-first-prompt-checked" };
    if (!pending) return { status: "idle" };
    if (pending.sid && pending.sid !== snapshot.sessionId) throw new PendingSlotError("native-metadata-session-mismatch");
    pending.sid ||= snapshot.sessionId;
    if (snapshot.status === "empty") {
      if (pending.observationState.subject || pending.observationState.installation
          || pending.observationState.runtimeRoot || pending.observationState.uncertain) {
        throw new PendingSlotError("pending-record-invalid");
      }
      snapshot = await pendingSlot.capture({
        sessionId: snapshot.sessionId, locator: pending.locator,
      }, context);
      check();
      adopt(snapshot);
      captureEstablishedHere = true;
      if (terminal) return { status: terminalStatus, reason: terminalStatus === "logged"
        ? "metadata-confirmed" : "metadata-first-prompt-checked" };
    }
    if (!snapshot.identity && !captureEstablishedHere
        && !(pending.observationState.subject && pending.observationState.installation
          && pending.observationState.runtimeRoot)) {
      throw new PendingSlotError("pending-qualified-identity-unavailable");
    }
    restored = true;
    const result = await waitForReceiptWork(Promise.resolve().then(() => observe({
      ...pending, pending: pending.observationState, signal: context.signal, deadline: context.deadline,
      beforeRecord: async (observationState) => {
        check();
        observationState.uncertain = true;
        const committed = await pendingSlot.qualify(observationState, context);
        check();
        if (committed.status === "confirmed") return { confirmed: true };
        if (committed.status !== "uncertain") throw new PendingSlotError("pending-commit-unverified");
        return { verified: true };
      },
    })), context);
    check();
    if (result.status === "logged") {
      try {
        const committed = await pendingSlot.confirm(pending.observationState, context);
        check();
        if (committed.status !== "confirmed") throw new PendingSlotError("pending-commit-unverified");
      } catch (error) {
        pending.observationState.uncertain = true;
        throw error;
      }
      terminal = true;
      pending = null;
      delete state.receiptPending;
    }
    return result;
  }

  function reconcile() {
    if (disposed || terminal || running || scheduled || now() < retryAt) return;
    status = { status: "pending" };
    scheduled = setImmediate(() => {
      scheduled = null;
      if (disposed) return;
      controller = new AbortController();
      const deadline = performance.now() + OBSERVATION_MS - SETTLEMENT_RESERVE_MS;
      const timer = setTimeout(() => controller?.abort(), Math.max(0, deadline - performance.now()));
      running = Promise.resolve().then(() => attempt({ signal: controller.signal, deadline })).then((result) => {
        if (disposed) return;
        status = result;
        if (!["logged", "idle"].includes(result.status)) {
          retryAt = now() + retryMs;
          if (pending?.locator) reportUnavailable();
        }
      }, (error) => {
        if (!disposed) {
          const reason = pendingSlotReason(error);
          status = { status: pending?.observationState?.uncertain || reason === "pending-identity-conflict" ? "unknown" : "unavailable",
            reason };
          retryAt = now() + retryMs;
          if (pending?.locator) reportUnavailable();
        }
      }).finally(() => {
        clearTimeout(timer);
        controller?.abort();
        running = null;
        controller = null;
      });
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
      const cwd = state.cwd || process.cwd();
      pending = {
        cwd, sid: session.sessionId || null, sessionDir, observationState: {},
        locator: locator ? recoveryLocatorFor(locator.kind, locator.id) : null,
      };
      state.receiptPending = pending;
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
      status = { status: "disposed" };
      delete state.promptReceiptObservation;
    },
  };
  state.promptReceiptObservation = observation;
  reconcile();
  return observation;
}
