import { createHash } from "node:crypto";
import { isAbsolute } from "node:path";

export const RECEIPT_METADATA_PREFIX = "context-handoff/prompt-receipt/v1/";
export const RECEIPT_METADATA_VALUE_BYTES = 8192;
const slots = new WeakMap();
const STABLE = ["installationId", "sourceFingerprint", "marketplaceId", "pluginId"];
const SUBJECT = ["project", "worktreeId", "creationNonce", "sessionId", "locator"];
const bytes = (value) => Buffer.byteLength(value, "utf8");
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value, limit) => typeof value === "string" && value.length > 0
  && bytes(value) <= limit && !/[\u0000-\u001f\u007f]/u.test(value);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export class PendingSlotError extends Error {
  constructor(reason) {
    const bounded = /^[a-z][a-z0-9-]{0,79}$/u.test(reason) ? reason : "native-metadata-unavailable";
    super(bounded);
    this.reason = bounded;
  }
}
function refuse(reason) { throw new PendingSlotError(reason); }
function fields(value, names) {
  return object(value) && Object.keys(value).length === names.length
    && names.every((key) => Object.hasOwn(value, key));
}
function capture(value, sid) {
  if (!fields(value, ["sessionId", "locator"]) || value.sessionId !== sid
      || !text(value.sessionId, 256) || (value.locator !== null && (
        !text(value.locator, 512) || !/^(task|file):[!-~]+$/u.test(value.locator)))) {
    refuse("pending-capture-invalid");
  }
  return { sessionId: value.sessionId, locator: value.locator };
}
function qualified(value, sid) {
  if (!fields(value, ["capture", "installation", "runtimeRoot", "subject"])) {
    refuse("pending-identity-invalid");
  }
  const captured = capture(value.capture, sid);
  if (captured.locator === null) refuse("pending-identity-invalid");
  if (!fields(value.installation, STABLE) || !fields(value.subject, SUBJECT)
      || !text(value.runtimeRoot, 4096) || !isAbsolute(value.runtimeRoot)) refuse("pending-identity-invalid");
  const installation = Object.fromEntries(STABLE.map((key) => [key, value.installation[key]]));
  if (STABLE.some((key) => !text(installation[key], key === "marketplaceId" ? 256 : 4096))
      || installation.pluginId !== "agent-worktrees") refuse("pending-identity-invalid");
  const subject = Object.fromEntries(SUBJECT.map((key) => [key, value.subject[key]]));
  if (SUBJECT.some((key) => !text(subject[key], key === "locator" ? 512 : 256))
      || subject.sessionId !== sid || subject.locator !== captured.locator
      || ["project", "worktreeId"].some((key) => !/^[A-Za-z0-9_@][A-Za-z0-9_.@-]{0,255}$/u.test(subject[key]))) {
    refuse("pending-identity-invalid");
  }
  return { capture: captured, installation, runtimeRoot: value.runtimeRoot, subject };
}
function identityFor(pending, sid) {
  if (!object(pending) || !object(pending.installation) || !object(pending.subject)
      || Object.keys(pending.installation).some((key) => ![...STABLE, "installGeneration", "activationGeneration"].includes(key))) {
    refuse("pending-identity-invalid");
  }
  return qualified({
    capture: { sessionId: pending.subject.sessionId, locator: pending.subject.locator },
    installation: Object.fromEntries(STABLE.map((key) => [key, pending.installation[key]])),
    runtimeRoot: pending.runtimeRoot, subject: pending.subject,
  }, sid);
}
function record(phase, identity) {
  const canonical = JSON.stringify(identity);
  const digest = createHash("sha256").update(canonical).digest("hex");
  const key = `${RECEIPT_METADATA_PREFIX}${phase}/${digest}`;
  const value = JSON.stringify({ version: 1, phase, identity });
  if (bytes(key) > 256 || bytes(value) > RECEIPT_METADATA_VALUE_BYTES) refuse("pending-capacity");
  return { key, value };
}
function validateBag(bag) {
  if (!object(bag)) refuse("pending-bag-invalid");
  const entries = Object.entries(bag);
  if (entries.length > 128 || entries.some(([key, value]) =>
    !key || bytes(key) > 256 || typeof value !== "string" || bytes(value) > 16384)
      || bytes(JSON.stringify(bag)) > 65536) refuse("pending-capacity");
  return entries;
}
function readSlot(bag, sid) {
  const entries = validateBag(bag).filter(([key]) => key.startsWith(RECEIPT_METADATA_PREFIX));
  if (entries.length > 3) refuse("pending-identity-conflict");
  let captured = null;
  let identity = null;
  let possiblySent = false;
  let confirmed = false;
  for (const [key, value] of entries) {
    if (bytes(value) > RECEIPT_METADATA_VALUE_BYTES) refuse("pending-capacity");
    let parsed;
    try { parsed = JSON.parse(value); } catch { refuse("pending-record-invalid"); }
    if (!fields(parsed, ["version", "phase", "identity"]) || parsed.version !== 1
        || !["capture", "qualified", "confirmed"].includes(parsed.phase)) refuse("pending-record-invalid");
    const current = parsed.phase === "capture" ? capture(parsed.identity, sid) : qualified(parsed.identity, sid);
    const encoded = record(parsed.phase, current);
    if (encoded.key !== key || encoded.value !== value) refuse("pending-record-invalid");
    const currentCapture = parsed.phase === "capture" ? current : current.capture;
    if (captured && !same(captured, currentCapture)) refuse("pending-identity-conflict");
    captured = currentCapture;
    if (parsed.phase !== "capture") {
      if (identity && !same(identity, current)) refuse("pending-identity-conflict");
      identity = current;
      possiblySent ||= parsed.phase === "qualified";
      confirmed ||= parsed.phase === "confirmed";
    }
  }
  const hasCapture = entries.some(([key]) => key.startsWith(`${RECEIPT_METADATA_PREFIX}capture/`));
  if ((identity && !hasCapture) || (confirmed && !possiblySent)) refuse("pending-record-invalid");
  return {
    status: confirmed ? "confirmed" : possiblySent ? "uncertain"
      : captured ? captured.locator === null ? "checked" : "captured" : "empty",
    sessionId: sid, capture: captured, identity,
  };
}
function check(context) {
  if (context?.signal?.aborted) refuse("pending-observation-cancelled");
  if (!Number.isFinite(context?.deadline) || performance.now() >= context.deadline) {
    refuse("pending-observation-expired");
  }
}
export function waitForReceiptWork(work, context) {
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = (error, value) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      context.signal?.removeEventListener("abort", abort);
      if (error) reject(error); else resolve(value);
    };
    const abort = () => finish(new PendingSlotError("pending-observation-cancelled"));
    const timer = setTimeout(() => finish(new PendingSlotError("pending-observation-expired")),
      Math.max(0, context.deadline - performance.now()));
    context.signal?.addEventListener("abort", abort, { once: true });
    work.then((value) => finish(null, value), (error) => finish(error));
    if (context.signal?.aborted) abort();
  });
}

export function pendingSlotReason(error) {
  return error instanceof PendingSlotError ? error.reason : "native-metadata-unavailable";
}

export function createNativeReceiptPendingSlot(session) {
  if (object(session) && slots.has(session)) return slots.get(session);
  let owned = null;
  async function run(context, operation) {
    check(context);
    if (owned) refuse("pending-rpc-inflight");
    const work = (async () => {
      let metadata;
      try { metadata = session?.rpc?.metadata; } catch { refuse("native-metadata-unsupported"); }
      if (!metadata || ["snapshot", "getClientMetadata", "updateClientMetadata"]
        .some((key) => typeof metadata[key] !== "function")) refuse("native-metadata-unsupported");
      async function call(method, ...args) {
        check(context);
        let result;
        try { result = await metadata[method](...args); }
        catch (error) {
          if (error?.code === -32601) refuse("native-metadata-unsupported");
          refuse("native-metadata-unavailable");
        }
        check(context);
        return result;
      }
      const snapshot = await call("snapshot");
      if (!object(snapshot) || !text(snapshot.sessionId, 256)
          || snapshot.sessionId !== session.sessionId || typeof snapshot.isRemote !== "boolean"
          || !(snapshot.workspacePath === null || typeof snapshot.workspacePath === "string")) {
        refuse("native-metadata-session-mismatch");
      }
      if (snapshot.isRemote) refuse("native-metadata-remote");
      const sid = snapshot.sessionId;
      let bag = await call("getClientMetadata");
      let state = readSlot(bag, sid);
      async function publish(phase, value) {
        const encoded = record(phase, value);
        if (Object.hasOwn(bag, encoded.key)) {
          if (bag[encoded.key] !== encoded.value) refuse("pending-identity-conflict");
        }
        const predicted = { ...bag, [encoded.key]: encoded.value };
        readSlot(predicted, sid);
        bag = await call("updateClientMetadata", { set: { [encoded.key]: encoded.value } });
        state = readSlot(bag, sid);
        if (bag[encoded.key] !== encoded.value) refuse("pending-commit-unverified");
        const committedOwn = Object.entries(bag)
          .filter(([key]) => key.startsWith(RECEIPT_METADATA_PREFIX));
        bag = await call("getClientMetadata");
        state = readSlot(bag, sid);
        if (committedOwn.some(([key, value]) => bag[key] !== value)) refuse("pending-commit-unverified");
        return state;
      }
      const result = await operation({ sid, state, publish });
      check(context);
      return result;
    })();
    owned = work;
    work.then(() => { if (owned === work) owned = null; },
      () => { if (owned === work) owned = null; });
    return waitForReceiptWork(work, context);
  }
  const slot = {
    get busy() { return owned !== null; },
    restore(context) { return run(context, ({ state }) => state); },
    capture(value, context) {
      return run(context, ({ sid, state, publish }) => {
        const captured = capture(value, sid);
        if (state.capture && !same(state.capture, captured)) refuse("pending-identity-conflict");
        return state.status === "empty" ? publish("capture", captured) : state;
      });
    },
    qualify(pending, context) {
      return run(context, ({ sid, state, publish }) => {
        const identity = identityFor(pending, sid);
        if (!state.capture || !same(state.capture, identity.capture)
            || (state.identity && !same(state.identity, identity))) refuse("pending-identity-conflict");
        if (state.status === "confirmed") return state;
        return publish("qualified", identity);
      });
    },
    confirm(pending, context) {
      return run(context, ({ sid, state, publish }) => {
        const identity = identityFor(pending, sid);
        if (!state.identity || !same(state.identity, identity)) refuse("pending-identity-conflict");
        return state.status === "confirmed" ? state : publish("confirmed", identity);
      });
    },
  };
  if (object(session)) slots.set(session, slot);
  return slot;
}
