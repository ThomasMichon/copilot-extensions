import { randomUUID } from "node:crypto";
import { generateToken, tokensMatch } from "./discovery.mjs";

export const CONTROL_PROTOCOL = 2;

export class DriverControlError extends Error {
  constructor(code, message, status = 409, details = {}) {
    super(message);
    this.name = "DriverControlError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

const shortString = (value) => typeof value === "string" && value.length > 0 && value.length <= 256;

export function createDriverOwnership({
  getSessionId,
  instanceId = randomUUID(),
  newGeneration = randomUUID,
  newCredential = generateToken,
  maxPendingSources = 128,
  maxObservedInputs = 512,
} = {}) {
  if (typeof getSessionId !== "function" || !shortString(instanceId)) {
    throw new TypeError("Driver ownership requires a session identity and instance ID");
  }
  for (const bound of [maxPendingSources, maxObservedInputs]) {
    if (!Number.isSafeInteger(bound) || bound < 1) throw new TypeError("Ownership bounds must be positive integers");
  }
  let generation = newGeneration();
  let ready = false;
  let observedSessionId = null;
  let mode = "human";
  let reason = "initial_human_control";
  let owner = null;
  let inFlight = null;
  const sources = new Map();
  const observedInputs = new Set();

  function rotate() {
    const next = newGeneration();
    if (!shortString(next) || next === generation) {
      ready = false;
      mode = "human";
      owner = null;
      reason = "generation_unavailable";
      throw new DriverControlError("generation_unavailable", "Cannot establish a fresh ownership generation", 503);
    }
    generation = next;
  }

  function snapshot() {
    const sameSession = observedSessionId === getSessionId();
    return {
      controlProtocol: CONTROL_PROTOCOL,
      instanceId,
      sessionId: getSessionId(),
      generation,
      ready: ready && sameSession,
      mode,
      reason: ready && !sameSession ? "session_identity_changed" : reason,
      ownerId: owner?.id ?? null,
      lastSequence: owner?.sequence ?? 0,
      pendingSources: sources.size,
      inFlight: inFlight ? { kind: inFlight.kind, sequence: inFlight.sequence } : null,
    };
  }

  function assertReady() {
    if (!ready || observedSessionId !== getSessionId()) {
      throw new DriverControlError("observation_unavailable", "Native input observation is not ready", 503);
    }
  }

  function assertTarget(target) {
    assertReady();
    if (!target || typeof target !== "object") {
      throw new DriverControlError("target_required", "A target identity is required", 400);
    }
    if (target.sessionId !== observedSessionId || target.instanceId !== instanceId) {
      throw new DriverControlError("target_changed", "The target session or extension instance changed");
    }
    if (target.generation !== generation) {
      throw new DriverControlError("generation_stale", "The ownership generation changed");
    }
  }

  function assertLease(lease) {
    if (!lease || typeof lease !== "object") {
      throw new DriverControlError("driver_claim_required", "A current driver claim is required");
    }
    assertTarget(lease);
    if (mode !== "remote" || !owner) {
      throw new DriverControlError("driver_claim_required", "No remote driver owns this target");
    }
    if (!tokensMatch(lease.token, owner.credential)) {
      throw new DriverControlError("driver_credential_invalid", "The driver credential is invalid", 403);
    }
  }

  function assertNotBusy() {
    if (inFlight) throw new DriverControlError("control_busy", "An earlier native control operation is still settling");
  }

  function grant(target, ownerId, { rehook = false } = {}) {
    assertTarget(target);
    assertNotBusy();
    if (!shortString(ownerId) || ownerId.length > 128 || /[\r\n]/.test(ownerId)) {
      throw new DriverControlError("owner_required", "ownerId must be a nonempty label of at most 128 characters", 400);
    }
    if (!rehook && mode !== "available") {
      throw new DriverControlError(
        mode === "human" ? "human_controlled" : "driver_claim_conflict",
        mode === "human" ? "Explicit re-hook is required after human takeover" : "Another driver owns this target",
      );
    }
    const credential = newCredential();
    if (!shortString(credential)) throw new DriverControlError("credential_unavailable", "Cannot establish a driver credential", 503);
    rotate();
    mode = "remote";
    reason = rehook ? "explicit_rehook" : "driver_claimed";
    owner = { id: ownerId, credential, sequence: 0 };
    return { sessionId: observedSessionId, instanceId, generation, ownerId, token: credential };
  }

  function begin(lease, sequence, kind) {
    assertLease(lease);
    assertNotBusy();
    if (!Number.isSafeInteger(sequence) || sequence < 1) {
      throw new DriverControlError("sequence_invalid", "sequence must be a positive safe integer", 400);
    }
    if (sequence <= owner.sequence) throw new DriverControlError("operation_replayed", "This operation sequence was already admitted");
    if (sequence !== owner.sequence + 1) throw new DriverControlError("operation_out_of_order", "The next operation sequence is required");
    owner.sequence = sequence;
    inFlight = {
      generation, sequence, kind, source: null,
      abortDispatched: false, abortCompleted: false,
      sendDispatched: false, sendCompleted: false,
    };
    return inFlight;
  }

  function effects(operation) {
    const stage = (name) => operation[`${name}Completed`] ? "completed" : operation[`${name}Dispatched`] ? "dispatched" : "not_started";
    return { abort: stage("abort"), send: stage("send") };
  }

  function assertCurrent(operation) {
    assertReady();
    if (operation !== inFlight || mode !== "remote" || operation.generation !== generation || !owner) {
      throw new DriverControlError("driver_revoked", "Driver ownership changed during the operation", 409, { effects: effects(operation) });
    }
  }

  function sourceFor(operation) {
    assertCurrent(operation);
    if (operation.source) return operation.source;
    if (sources.size >= maxPendingSources) {
      throw new DriverControlError("provenance_backlog_full", "Unreconciled native sends reached the tracking bound", 503);
    }
    operation.source = `agent-remote-driver-${randomUUID()}`;
    sources.set(operation.source, operation.generation);
    return operation.source;
  }

  function dispatched(operation, stage) {
    assertCurrent(operation);
    operation[`${stage}Dispatched`] = true;
  }

  function completed(operation, stage) {
    operation[`${stage}Completed`] = true;
  }

  function finish(operation) {
    if (operation !== inFlight) return;
    if (operation.source && !operation.sendDispatched) sources.delete(operation.source);
    inFlight = null;
  }

  function release(lease, sequence) {
    const operation = begin(lease, sequence, "release");
    try {
      owner = null;
      mode = "available";
      reason = "driver_released";
      rotate();
      return snapshot();
    } finally {
      finish(operation);
    }
  }

  function observe(event) {
    if (event?.type !== "user.message" ||
        [event.agentId, event.data?.agentId, event.data?.parentToolCallId]
          .some((value) => typeof value === "string" && value.length > 0)) return false;
    if (shortString(event.id)) {
      if (observedInputs.has(event.id)) return false;
      observedInputs.add(event.id);
      if (observedInputs.size > maxObservedInputs) observedInputs.delete(observedInputs.values().next().value);
    }
    const source = event.data?.source;
    if (shortString(source) && sources.delete(source)) return false;
    owner = null;
    mode = "human";
    reason = "unowned_root_input";
    rotate();
    return true;
  }

  function markReady() {
    const sessionId = getSessionId();
    if (!shortString(sessionId)) throw new DriverControlError("session_unknown", "The native session identity is unavailable", 503);
    observedSessionId = sessionId;
    owner = null;
    mode = "human";
    reason = "awaiting_explicit_rehook";
    rotate();
    ready = true;
  }

  function suspend(observationReason) {
    // Disabling admission must not depend on fresh entropy; markReady rotates before recovery.
    ready = false;
    owner = null;
    mode = "human";
    reason = observationReason;
  }

  return {
    snapshot, markReady, suspend, grant, begin, assertCurrent,
    sourceFor, dispatched, completed, effects, finish, release, observe,
  };
}
