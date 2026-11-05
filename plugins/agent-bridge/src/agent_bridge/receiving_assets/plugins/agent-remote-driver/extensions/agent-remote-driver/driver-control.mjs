import { tokensMatch } from "./discovery.mjs";
import { createDriverOwnership, DriverControlError } from "./driver-ownership.mjs";

export function createDriverControl({ getSessionId, send, abort, controlToken, onChange = () => {} }) {
  if (typeof controlToken !== "string" || !controlToken || typeof send !== "function" || typeof abort !== "function") {
    throw new TypeError("Driver control requires an administrative credential and native send/abort callbacks");
  }
  const ownership = createDriverOwnership({ getSessionId });
  const publish = () => onChange(ownership.snapshot());

  function objectBody(body) {
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      throw new DriverControlError("body_invalid", "A JSON object is required", 400);
    }
  }

  function administrator(body) {
    objectBody(body);
    if (!tokensMatch(body.controlToken, controlToken)) {
      throw new DriverControlError("control_credential_invalid", "An administrative control credential is required", 403);
    }
  }

  function grant(body, rehook) {
    administrator(body);
    const lease = ownership.grant(body.target, body.ownerId, { rehook });
    publish();
    return lease;
  }

  function release(body) {
    objectBody(body);
    const state = ownership.release(body.lease, body.sequence);
    publish();
    return state;
  }

  async function invoke(action, body) {
    if (!["send", "steer", "abort"].includes(action)) {
      throw new DriverControlError("action_invalid", "Unknown control operation", 400);
    }
    objectBody(body);
    if (action !== "abort" && (typeof body.content !== "string" || !body.content)) {
      throw new DriverControlError("content_required", "content (string) is required", 400);
    }
    if (body.mode !== undefined && !["enqueue", "immediate"].includes(body.mode)) {
      throw new DriverControlError("mode_invalid", "mode must be enqueue or immediate", 400);
    }
    const operation = ownership.begin(body.lease, body.sequence, action);
    let result;
    let failure;
    try {
      if (action === "abort" || action === "steer") {
        ownership.dispatched(operation, "abort");
        result = await abort();
        ownership.completed(operation, "abort");
        ownership.assertCurrent(operation);
      }
      if (action === "send" || action === "steer") {
        const source = ownership.sourceFor(operation);
        ownership.dispatched(operation, "send");
        result = await send({ content: body.content, mode: action === "steer" ? "immediate" : body.mode, source });
        ownership.completed(operation, "send");
        ownership.assertCurrent(operation);
      }
    } catch (error) {
      failure = error;
      if (!(error instanceof DriverControlError)) ownership.suspend("native_control_outcome_unknown");
    } finally {
      ownership.finish(operation);
    }
    if (failure) {
      const details = { effects: ownership.effects(operation), control: ownership.snapshot() };
      if (failure instanceof DriverControlError) {
        throw new DriverControlError(failure.code, failure.message, failure.status, { ...failure.details, ...details });
      }
      throw new DriverControlError(
        "native_control_failed",
        `Native ${action} failed: ${failure?.message || "unknown error"}`,
        502,
        details,
      );
    }
    return result ?? null;
  }

  return {
    snapshot: ownership.snapshot,
    claim: (body) => grant(body, false),
    rehook: (body) => grant(body, true),
    release,
    invoke,
    observe(event) {
      if (event?.type === "session.shutdown") {
        ownership.suspend("native_session_shutdown");
        publish();
        return true;
      }
      const changed = ownership.observe(event);
      if (changed) publish();
      return changed;
    },
    markReady() {
      ownership.markReady();
      publish();
    },
    suspend(reason) {
      ownership.suspend(reason);
      publish();
    },
  };
}
